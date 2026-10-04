"""
Integration Tests: Checkpoint acceptance, cross-process resume, negative
replay, human-wait and terminal checkpoints (Phase 7 §8, §18, §19, §24, §25).

Process A runs to a checkpoint and stops (all objects dropped). Process B
uses fresh engines, stores, and providers on the same database file and
must reach the same terminal outcome. No live providers, no network.
"""

import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine

from casefile.agents.context import AgentContext
from casefile.agents.deterministic import DeterministicProvider
from casefile.agents.extractor import ExtractorAgent
from casefile.agents.investigator import InvestigatorAgent
from casefile.agents.reviewer import ReviewerAgent
from casefile.checkpoint.ledger import IdempotencyLedger
from casefile.checkpoint.model import (
    Checkpoint,
    RecordedAgentOutput,
    TransitionPathEntry,
)
from casefile.checkpoint.replay import (
    ReplayAgentProvider,
    ReplayMissingArtifactError,
    ReplayMode,
    run_replay,
)
from casefile.checkpoint.repository import SqlCheckpointRepository
from casefile.checkpoint.resume import resume_from_checkpoint
from casefile.models.contracts import (
    ClaimInput,
    ExtractionRequest,
    InvestigationRequest,
    ReviewRequest,
    WorkflowState,
)
from casefile.models.domain import AgentType
from casefile.models.persistence import get_engine, init_db
from casefile.workflow.context import FixedClock, RunContext, WorkflowSnapshot
from casefile.workflow.engine import WorkflowEngine
from casefile.workflow.store import SqliteWorkflowStore
from casefile.workflow.supervisor import SupervisorRouter
from casefile.workflow.transitions import TerminalStateError, TransitionEvent, TransitionRecord
from casefile.workflow.triggers import Trigger


def _fixed_db(tmp_path: Path, name: str = "replay.db") -> Engine:
    engine = get_engine(f"sqlite:///{tmp_path / name}")
    init_db(engine)
    return engine


def _claim() -> ClaimInput:
    return ClaimInput(
        policy_id="POL-REPLAY",
        claimant_name="Replay Tester",
        incident_date="2026-09-20",
        claim_amount=Decimal("1500.00"),
        description="Replay acceptance claim",
        documents=["SYN-DOC-N1"],
    )


def _extraction_json(run_id: UUID) -> dict[str, object]:
    return {
        "workflow_id": str(run_id),
        "claimant_name": "Replay Tester",
        "policy_id": "POL-REPLAY",
        "incident_date": "2026-09-20",
        "incident_location": "Main St",
        "incident_description": "Replay acceptance claim",
        "claim_amount": "1500.00",
        "requested_coverage_type": "COLLISION",
        "extraction_confidence": 0.95,
    }


def _investigation_json(run_id: UUID) -> dict[str, object]:
    return {
        "workflow_id": str(run_id),
        "policy_details": {"status": "active"},
        "claim_history": {"prior": 0},
        "repair_cost_validation": {"within_range": True},
        "fraud_signals": {"risk": "low"},
        "supporting_documents": ["SYN-DOC-N1"],
        "findings_summary": "Policy active; costs reasonable",
        "evidence_strength": 0.85,
        "tool_calls_made": ["policy_lookup"],
        "investigation_duration_seconds": 3,
        "tools_succeeded": 1,
        "tools_failed": 0,
    }


def _review_json(run_id: UUID, decision: str = "APPROVE") -> dict[str, object]:
    return {
        "workflow_id": str(run_id),
        "decision": decision,
        "reasoning": "Evidence complete and consistent",
        "confidence_score": 0.93,
        "evidence_completeness": 1.0,
        "identified_gaps": [],
        "rework_feedback": None,
        "fraud_risk_level": "LOW",
        "fraud_signals_detected": False,
    }


def _agent_ctx(claim_id: UUID, run_id: UUID, agent: AgentType) -> AgentContext:
    return AgentContext(claim_id=claim_id, workflow_run_id=run_id, agent=agent)


def _drive(
    engine: WorkflowEngine,
    snapshot: WorkflowSnapshot,
    events: list[tuple[Trigger, AgentType]],
    ctx: RunContext,
) -> tuple[WorkflowSnapshot, list[TransitionRecord]]:
    records: list[TransitionRecord] = []
    for trigger, actor in events:
        result = engine.apply(
            snapshot,
            TransitionEvent(
                claim_id=snapshot.claim_id,
                workflow_run_id=snapshot.workflow_run_id,
                trigger=trigger,
                actor=actor,
            ),
            ctx,
        )
        assert result.record is not None
        records.append(result.record)
        snapshot = result.snapshot
    return snapshot, records


def _checkpoint_for(
    snapshot: WorkflowSnapshot,
    records: list[TransitionRecord],
    recordings: list[RecordedAgentOutput],
    claim_id: UUID,
    run_id: UUID,
    parent: UUID | None = None,
) -> Checkpoint:
    path = tuple(
        TransitionPathEntry(
            sequence_no=record.sequence_no,
            trigger=record.trigger,
            actor=record.actor,
            reason=record.reason,
        )
        for record in records
    )
    return Checkpoint(
        claim_id=claim_id,
        workflow_run_id=run_id,
        state=snapshot.current_state,
        snapshot=snapshot,
        step_count=snapshot.step_count,
        rework_count=snapshot.rework_count,
        recorded_agents=tuple(recordings),
        path=path,
        parent_checkpoint_id=parent,
    )


@pytest.mark.integration
class TestReplayAcceptance:
    def test_live_path_equals_replay_path(self, tmp_path: Path) -> None:
        engine_db = _fixed_db(tmp_path)
        claim = _claim()
        run_id, claim_id = uuid4(), claim.claim_id
        clock = FixedClock(datetime.now(UTC))

        # ---- PROCESS A: live run to INVESTIGATION, then checkpoint + stop
        live_engine = WorkflowEngine()
        live_ctx = RunContext(claim_id=claim_id, workflow_run_id=run_id, clock=clock)
        live_store = SqliteWorkflowStore(engine_db)
        live_checkpoints = SqlCheckpointRepository(engine_db)
        live_ledger = IdempotencyLedger(engine_db)

        extractor = ExtractorAgent(_scripted([_extraction_json(run_id)]))
        extraction, _ = extractor.run(
            ExtractionRequest(workflow_id=run_id, claim_input=claim),
            _agent_ctx(claim_id, run_id, AgentType.EXTRACTOR),
        )
        investigator = InvestigatorAgent(_scripted([_investigation_json(run_id)]))
        investigation, _ = investigator.run(
            InvestigationRequest(workflow_id=run_id, extraction_result=extraction),
            _agent_ctx(claim_id, run_id, AgentType.INVESTIGATOR),
        )
        snap = WorkflowSnapshot(
            workflow_run_id=run_id, claim_id=claim_id, current_state=WorkflowState.RECEIVED
        )
        snap, records_a = _drive(
            live_engine,
            snap,
            [
                (Trigger.CLAIM_VALIDATED, AgentType.SUPERVISOR),
                (Trigger.EXTRACTION_SUCCEEDED, AgentType.SUPERVISOR),
                (Trigger.INVESTIGATION_SUCCEEDED, AgentType.SUPERVISOR),
            ],
            live_ctx,
        )
        for record in records_a:
            live_ledger.claim(
                idempotency_key=record.idempotency_key,
                workflow_run_id=run_id,
                execution_id=record.execution_id,
                sequence_no=record.sequence_no,
                result_kind="transition",
                result_ref=str(record.transition_id),
            )
            live_store.record_transition(record)
        live_store.save_snapshot(snap)
        assert snap.current_state == WorkflowState.REVIEW
        reviewer_live = ReviewerAgent(_scripted([_review_json(run_id, "APPROVE")]))
        review_live, _ = reviewer_live.run(
            ReviewRequest(
                workflow_id=run_id,
                extraction_result=extraction,
                investigation_result=investigation,
            ),
            _agent_ctx(claim_id, run_id, AgentType.REVIEWER),
        )
        assert review_live.decision == "APPROVE"
        recordings = [
            RecordedAgentOutput(
                agent=AgentType.EXTRACTOR,
                contract_type="extraction_result",
                output_json=extraction.model_dump_json(),
                execution_id=uuid4(),
            ),
            RecordedAgentOutput(
                agent=AgentType.INVESTIGATOR,
                contract_type="investigation_result",
                output_json=investigation.model_dump_json(),
                execution_id=uuid4(),
            ),
            RecordedAgentOutput(
                agent=AgentType.REVIEWER,
                contract_type="review_result",
                output_json=review_live.model_dump_json(),
                execution_id=uuid4(),
            ),
        ]
        checkpoint = live_checkpoints.create(
            _checkpoint_for(snap, records_a, recordings, claim_id, run_id)
        )
        checkpoint_id = checkpoint.checkpoint_id
        assert checkpoint.sequence_no == 1
        # PROCESS A exits: drop every live object (simulates process death)
        del live_engine, live_ctx, live_store, live_checkpoints, live_ledger
        del extractor, investigator, reviewer_live, snap, records_a, checkpoint
        del extraction, investigation, review_live

        # ---- PROCESS B: fresh objects on the same file, resume + replay
        fresh_engine = WorkflowEngine()
        fresh_store = SqliteWorkflowStore(engine_db)
        fresh_checkpoints = SqlCheckpointRepository(engine_db)
        resumed = resume_from_checkpoint(checkpoint_id, checkpoints=fresh_checkpoints)
        assert resumed.snapshot.current_state == WorkflowState.REVIEW
        # Recordings come from the reloaded checkpoint, not process memory
        reloaded = fresh_checkpoints.get(checkpoint_id)
        by_contract = {rec.contract_type: rec.output_json for rec in reloaded.recorded_agents}
        from casefile.models.contracts import ExtractionResult, InvestigationResult

        live_extraction = ExtractionResult.model_validate_json(by_contract["extraction_result"])
        live_investigation = InvestigationResult.model_validate_json(
            by_contract["investigation_result"]
        )
        live_review_json = by_contract["review_result"]
        replay_provider = ReplayAgentProvider(resumed.checkpoint)
        reviewer = ReviewerAgent(replay_provider)
        review, _ = reviewer.run(
            ReviewRequest(
                workflow_id=run_id,
                extraction_result=live_extraction,
                investigation_result=live_investigation,
            ),
            _agent_ctx(claim_id, run_id, AgentType.REVIEWER),
        )
        assert review.decision == "APPROVE"
        from casefile.models.contracts import ReviewResult

        live_review = ReviewResult.model_validate_json(live_review_json)
        assert review == live_review
        fresh_ctx = resumed.context
        report = run_replay(
            resumed.checkpoint,
            [(Trigger.REVIEW_APPROVED, AgentType.SUPERVISOR)],
            engine=fresh_engine,
            ctx=fresh_ctx,
        )
        assert report.mode == ReplayMode.REPLAY
        assert report.states == [WorkflowState.REVIEW, WorkflowState.HUMAN_APPROVAL]
        # Human-wait checkpoint: stays waiting, never auto-approves
        wait_snapshot = WorkflowSnapshot(
            workflow_run_id=run_id,
            claim_id=claim_id,
            current_state=WorkflowState.HUMAN_APPROVAL,
            step_count=4,
        )
        decision = SupervisorRouter().route(wait_snapshot)
        assert decision.node.value == "human_approval"
        # Human grant continues to terminal; terminal checkpoint is immutable
        grant = TransitionEvent(
            claim_id=claim_id,
            workflow_run_id=run_id,
            trigger=Trigger.APPROVAL_GRANTED,
            actor=AgentType.HUMAN,
        )
        terminal = fresh_engine.apply(wait_snapshot, grant, fresh_ctx)
        assert terminal.snapshot.current_state == WorkflowState.APPROVED
        assert terminal.record is not None
        fresh_store.save_snapshot_and_audit(terminal.snapshot, terminal.record)
        with pytest.raises(TerminalStateError):
            fresh_engine.apply(
                terminal.snapshot,
                TransitionEvent(
                    claim_id=claim_id, workflow_run_id=run_id, trigger=Trigger.CLAIM_VALIDATED
                ),
                fresh_ctx,
            )
        # Live full path for comparison: same triggers ⇒ same terminal
        compare_engine = WorkflowEngine()
        compare_ctx = RunContext(claim_id=claim_id, workflow_run_id=uuid4(), clock=clock)
        compare_snap = WorkflowSnapshot(
            workflow_run_id=compare_ctx.workflow_run_id,
            claim_id=claim_id,
            current_state=WorkflowState.RECEIVED,
        )
        live_path = [WorkflowState.RECEIVED]
        for trigger, actor in [
            (Trigger.CLAIM_VALIDATED, AgentType.SUPERVISOR),
            (Trigger.EXTRACTION_SUCCEEDED, AgentType.SUPERVISOR),
            (Trigger.INVESTIGATION_SUCCEEDED, AgentType.SUPERVISOR),
            (Trigger.REVIEW_APPROVED, AgentType.SUPERVISOR),
            (Trigger.APPROVAL_GRANTED, AgentType.HUMAN),
        ]:
            compare_snap = compare_engine.apply(
                compare_snap,
                TransitionEvent(
                    claim_id=claim_id,
                    workflow_run_id=compare_ctx.workflow_run_id,
                    trigger=trigger,
                    actor=actor,
                ),
                compare_ctx,
            ).snapshot
            live_path.append(compare_snap.current_state)
        assert live_path[-1] == WorkflowState.APPROVED
        assert report.states + [WorkflowState.APPROVED] == [
            WorkflowState.REVIEW,
            WorkflowState.HUMAN_APPROVAL,
            WorkflowState.APPROVED,
        ]
        assert live_path[-3:] == report.states + [WorkflowState.APPROVED]
        assert "langchain_openai" not in sys.modules
        assert "langchain_anthropic" not in sys.modules
        engine_db.dispose()

    def test_negative_replay_missing_artifact(self, tmp_path: Path) -> None:

        engine_db = _fixed_db(tmp_path)
        repo = SqlCheckpointRepository(engine_db)
        stored = repo.create(
            _checkpoint_for(
                WorkflowSnapshot(
                    workflow_run_id=uuid4(),
                    claim_id=uuid4(),
                    current_state=WorkflowState.INVESTIGATION,
                ),
                [],
                [],
                uuid4(),
                uuid4(),
            )
        )
        provider = ReplayAgentProvider(stored)
        from casefile.agents.providers import LLMRequest

        with pytest.raises(ReplayMissingArtifactError):
            provider.complete(LLMRequest(model="m", messages=()))
        engine_db.dispose()

    def test_cross_restart_idempotency(self, tmp_path: Path) -> None:
        db_path = tmp_path / "idem.db"
        engine = get_engine(f"sqlite:///{db_path}")
        init_db(engine)
        run_id, claim_id = uuid4(), uuid4()
        ledger_a = IdempotencyLedger(engine)
        flow = WorkflowEngine()
        ctx = RunContext(
            claim_id=claim_id,
            workflow_run_id=run_id,
            clock=FixedClock(datetime.now(UTC)),
        )
        snap = WorkflowSnapshot(
            workflow_run_id=run_id, claim_id=claim_id, current_state=WorkflowState.RECEIVED
        )
        event = TransitionEvent(
            claim_id=claim_id, workflow_run_id=run_id, trigger=Trigger.CLAIM_VALIDATED
        )
        first = flow.apply(snap, event, ctx)
        assert first.record is not None
        key = first.record.idempotency_key
        assert (
            ledger_a.claim(
                idempotency_key=key,
                workflow_run_id=run_id,
                execution_id=event.execution_id,
                sequence_no=1,
                result_kind="transition",
                result_ref=str(first.record.transition_id),
            )
            is True
        )
        engine.dispose()
        # PROCESS B retries the SAME event after restart
        engine_b = get_engine(f"sqlite:///{db_path}")
        ledger_b = IdempotencyLedger(engine_b)
        assert ledger_b.is_claimed(key) is True
        assert (
            ledger_b.claim(
                idempotency_key=key,
                workflow_run_id=run_id,
                execution_id=event.execution_id,
                sequence_no=1,
                result_kind="transition",
                result_ref="retry",
            )
            is False
        )
        info = ledger_b.lookup(key)
        assert info is not None
        assert info["result_ref"] == str(first.record.transition_id)
        engine_b.dispose()


def _scripted(payloads: list[dict[str, object]]):  # type: ignore[no-untyped-def]

    provider = DeterministicProvider()
    for payload in payloads:
        provider.push_json(payload)
    return provider

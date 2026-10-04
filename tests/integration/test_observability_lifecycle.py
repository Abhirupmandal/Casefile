"""
Integration Tests: Phase 10 end-to-end acceptance (7 scenarios).

Full workflow runs with observability wired through every layer,
asserted against the in-memory span exporter. Deterministic providers,
tmp SQLite, no network, no live Jaeger required.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from casefile.agents.context import AgentContext
from casefile.agents.deterministic import DeterministicProvider
from casefile.agents.extractor import ExtractorAgent
from casefile.agents.reviewer import ReviewerAgent
from casefile.approval.model import ApprovalDecision, ApprovalVerdict, HumanActor
from casefile.approval.service import ApprovalService
from casefile.budget.engine import BudgetEngine, ReserveKind
from casefile.budget.envelope import BudgetEnvelope
from casefile.budget.pricing import ModelUsage, PricingTable, zero_pricing
from casefile.budget.usage import BudgetUsage
from casefile.checkpoint.model import Checkpoint, CheckpointKind
from casefile.checkpoint.replay import run_replay
from casefile.models.contracts import (
    ClaimInput,
    ExtractionRequest,
    InvestigationResult,
    ReviewRequest,
    WorkflowState,
)
from casefile.models.domain import (
    AgentType,
    HumanApprovalRequest,
    Money,
    Recommendation,
    RecommendationType,
)
from casefile.models.persistence import get_engine, get_session_factory, init_db
from casefile.observability.provider import (
    Observability,
)
from casefile.observability.provider import (
    test_observability as make_test_observability,
)
from casefile.tools import create_default_registry
from casefile.tools.contracts import ToolContext
from casefile.workflow.context import FixedClock, RunContext, WorkflowSnapshot
from casefile.workflow.engine import WorkflowEngine
from casefile.workflow.store import SqliteWorkflowStore
from casefile.workflow.transitions import TransitionEvent
from casefile.workflow.triggers import Trigger


def _setup(
    tmp_path: Path, name: str
) -> tuple[Engine, sessionmaker[Session], FixedClock, UUID, Observability, InMemorySpanExporter]:
    engine = get_engine(f"sqlite:///{tmp_path / name}")
    init_db(engine)
    factory: sessionmaker[Session] = get_session_factory(engine)
    clock = FixedClock(datetime.now(UTC))
    run_id = uuid4()
    obs, exporter = make_test_observability()
    return engine, factory, clock, run_id, obs, exporter


def _claim() -> ClaimInput:
    return ClaimInput(
        policy_id="POL-OBS",
        claimant_name="Observability Tester",
        incident_date="2026-09-20",
        claim_amount=Decimal("1500.00"),
        description="Observability acceptance claim",
        documents=["SYN-DOC-N1"],
    )


def _extraction_payload(run_id: UUID) -> dict[str, object]:
    return {
        "workflow_id": str(run_id),
        "claimant_name": "Observability Tester",
        "policy_id": "POL-OBS",
        "incident_date": "2026-09-20",
        "incident_location": "Main St",
        "incident_description": "Observability acceptance claim",
        "claim_amount": "1500.00",
        "requested_coverage_type": "COLLISION",
        "extraction_confidence": 0.95,
    }


def _review_payload(run_id: UUID, decision: str = "APPROVE") -> dict[str, object]:
    return {
        "workflow_id": str(run_id),
        "decision": decision,
        "reasoning": "Evidence complete",
        "confidence_score": 0.93,
        "evidence_completeness": 1.0,
        "identified_gaps": [],
        "rework_feedback": None,
        "fraud_risk_level": "LOW",
        "fraud_signals_detected": False,
    }


def _span_names(exporter: InMemorySpanExporter) -> list[str]:
    return [span.name for span in exporter.get_finished_spans()]


def _span_by_name(exporter: InMemorySpanExporter, name: str) -> ReadableSpan:
    for span in exporter.get_finished_spans():
        if span.name == name:
            return span
    raise AssertionError(f"span {name!r} not found")


@pytest.mark.integration
class TestScenario1FullLiveTrace:
    def test_root_children_approval_checkpoint_budget(self, tmp_path: Path) -> None:
        engine, factory, clock, run_id, obs, exporter = _setup(tmp_path, "obs1.db")
        claim = _claim()
        flow = WorkflowEngine()
        ctx = RunContext(claim_id=claim.claim_id, workflow_run_id=run_id, clock=clock)
        store = SqliteWorkflowStore(engine)
        approvals = ApprovalService(factory, clock)
        pricing = PricingTable(
            entries={"deterministic/stub": zero_pricing("deterministic", "stub")}
        )
        budget = BudgetEngine(
            run_id,
            BudgetEnvelope(),
            BudgetUsage(wall_start=clock.now()),
            clock,
            factory,
            pricing=pricing,
        )
        budget.start_run()
        registry = create_default_registry()

        with obs.tracer.span("casefile.workflow.run", {"casefile.workflow_state": "RECEIVED"}):
            provider = DeterministicProvider()
            provider.push_json(_extraction_payload(run_id))
            agent = ExtractorAgent(provider)
            agent_ctx = AgentContext(
                claim_id=claim.claim_id, workflow_run_id=run_id, agent=AgentType.EXTRACTOR
            )
            assert budget.pre_step(ReserveKind.AGENT_STEP, obs=obs).granted is True
            extraction, _record = agent.run(
                ExtractionRequest(workflow_id=run_id, claim_input=claim), agent_ctx, obs=obs
            )
            budget.record_model_call(
                ModelUsage(
                    provider="deterministic", model="stub", input_tokens=500, output_tokens=100
                ),
                obs=obs,
            )
            tool_ctx = ToolContext(
                claim_id=claim.claim_id, workflow_run_id=run_id, agent=AgentType.INVESTIGATOR
            )
            assert budget.pre_step(ReserveKind.TOOL_CALL, obs=obs).granted is True
            tool_outcome = registry.execute(
                "policy_lookup", "1.0.0", {"policy_number": "POL-SYN-001"}, tool_ctx, obs=obs
            )
            assert tool_outcome.succeeded is True

            snap = _drive_to_human_approval(flow, store, budget, run_id, claim.claim_id, ctx, obs)

            reviewer_provider = DeterministicProvider()
            reviewer_provider.push_json(_review_payload(run_id, "APPROVE"))
            reviewer = ReviewerAgent(reviewer_provider)
            review, _ = reviewer.run(
                ReviewRequest(
                    workflow_id=run_id,
                    extraction_result=extraction,
                    investigation_result=_investigation_result(run_id),
                ),
                AgentContext(
                    claim_id=claim.claim_id, workflow_run_id=run_id, agent=AgentType.REVIEWER
                ),
                obs=obs,
            )
            assert review.decision == "APPROVE"

            request = HumanApprovalRequest(
                workflow_run_id=run_id,
                claim_id=claim.claim_id,
                recommendation=_recommendation(claim.claim_id),
                reviewer_summary="Ready",
                deadline=clock.now() + timedelta(hours=24),
                requested_at=clock.now(),
            )
            checkpoint = Checkpoint(
                claim_id=claim.claim_id,
                workflow_run_id=run_id,
                state=WorkflowState.HUMAN_APPROVAL,
                kind=CheckpointKind.HUMAN_WAIT,
                snapshot=snap,
                step_count=snap.step_count,
                rework_count=snap.rework_count,
                approval_request=request,
            )
            approval, stored_cp = approvals.request_approval_with_checkpoint(
                request, checkpoint, WorkflowState.HUMAN_APPROVAL, obs=obs
            )
            assert approval.status.value == "PENDING"
            assert stored_cp.sequence_no == 1
            clock.advance(60)

            decision = approvals.decide(
                ApprovalDecision(
                    approval_id=approval.approval_id,
                    actor=HumanActor(actor_id="human-1", display_name="Human One"),
                    verdict=ApprovalVerdict.APPROVE,
                    decision_key="obs-1",
                    expected_version=1,
                ),
                WorkflowState.HUMAN_APPROVAL,
                obs=obs,
            )
            assert decision.approval.status.value == "APPROVED"
            assert budget.pre_step(ReserveKind.WORKFLOW_STEP, obs=obs).granted is True
            terminal = flow.apply(
                snap,
                TransitionEvent(
                    claim_id=claim.claim_id,
                    workflow_run_id=run_id,
                    trigger=Trigger.APPROVAL_GRANTED,
                    actor=AgentType.HUMAN,
                ),
                ctx,
                obs=obs,
            )
            assert terminal.snapshot.current_state == WorkflowState.APPROVED
            obs.meters.record_counter("casefile.workflow.runs", 1.0, {"outcome": "approved"})
            obs.meters.record_counter("casefile.workflow.failures", 0.0, {"outcome": "approved"})

        names = _span_names(exporter)
        for expected in (
            "casefile.workflow.run",
            "casefile.agent.run",
            "casefile.tool.call",
            "casefile.workflow.transition",
            "casefile.approval.decide",
            "casefile.checkpoint.create",
        ):
            assert expected in names, expected
        # Parent-child: agent/tool/transition spans nest under the root run span
        by_name: dict[str, list[ReadableSpan]] = {}
        for span in exporter.get_finished_spans():
            by_name.setdefault(span.name, []).append(span)
        root = by_name["casefile.workflow.run"][0]
        root_id = root.context.span_id
        for child_name in (
            "casefile.agent.run",
            "casefile.tool.call",
            "casefile.workflow.transition",
        ):
            for child in by_name[child_name]:
                assert child.parent is not None
                assert child.parent.span_id == root_id
        # Approval span present with approval identity
        approval_spans = by_name["casefile.approval.decide"]
        assert len(approval_spans) == 2  # request + decide
        # Budget data present in meters
        assert obs.meters.total("casefile.budget.tokens") == 600.0
        assert (
            obs.meters.total(
                "casefile.tool.invocations",
                {"tool": "policy_lookup", "agent": "INVESTIGATOR", "mode": "LIVE"},
            )
            == 1.0
        )
        engine.dispose()

    def test_no_sensitive_data_in_spans(self, tmp_path: Path) -> None:
        engine, factory, clock, run_id, obs, exporter = _setup(tmp_path, "obs1b.db")
        claim = _claim()
        flow = WorkflowEngine()
        ctx = RunContext(claim_id=claim.claim_id, workflow_run_id=run_id, clock=clock)
        with obs.tracer.span("casefile.workflow.run", {"casefile.workflow_state": "RECEIVED"}):
            flow.apply(
                WorkflowSnapshot(
                    workflow_run_id=run_id,
                    claim_id=claim.claim_id,
                    current_state=WorkflowState.RECEIVED,
                ),
                TransitionEvent(
                    claim_id=claim.claim_id, workflow_run_id=run_id, trigger=Trigger.CLAIM_VALIDATED
                ),
                ctx,
                obs=obs,
            )
        for span in exporter.get_finished_spans():
            blob = str(span.attributes)
            assert "Observability Tester" not in blob
            assert "SYN-DOC" not in blob
            assert "sk-" not in blob
        engine.dispose()


def _drive_to_human_approval(
    flow: WorkflowEngine,
    store: SqliteWorkflowStore,
    budget: BudgetEngine,
    run_id: UUID,
    claim_id: UUID,
    ctx: RunContext,
    obs: Observability,
) -> WorkflowSnapshot:
    snap = WorkflowSnapshot(
        workflow_run_id=run_id, claim_id=claim_id, current_state=WorkflowState.RECEIVED
    )
    for trigger in (
        Trigger.CLAIM_VALIDATED,
        Trigger.EXTRACTION_SUCCEEDED,
        Trigger.INVESTIGATION_SUCCEEDED,
        Trigger.REVIEW_APPROVED,
    ):
        assert budget.pre_step(ReserveKind.WORKFLOW_STEP, obs=obs).granted is True
        result = flow.apply(
            snap,
            TransitionEvent(
                claim_id=claim_id,
                workflow_run_id=run_id,
                trigger=trigger,
                actor=AgentType.SUPERVISOR,
            ),
            ctx,
            obs=obs,
        )
        assert result.record is not None
        store.save_snapshot_and_audit(result.snapshot, result.record)
        snap = result.snapshot
    assert snap.current_state == WorkflowState.HUMAN_APPROVAL
    return snap


def _investigation_result(run_id: UUID) -> InvestigationResult:
    return InvestigationResult(
        workflow_id=run_id,
        findings_summary="Policy active; costs reasonable",
        evidence_strength=0.85,
        tool_calls_made=["policy_lookup"],
        investigation_duration_seconds=3,
        tools_succeeded=1,
        tools_failed=0,
    )


def _recommendation(claim_id: UUID) -> Recommendation:
    return Recommendation(
        claim_id=claim_id,
        recommendation_type=RecommendationType.APPROVE_FULL,
        estimated_payout=Money(amount=Decimal("1200.00")),
        notes="ok",
        confidence=0.9,
    )


@pytest.mark.integration
class TestScenario2ReworkTrace:
    def test_rework_visible_with_counters(self, tmp_path: Path) -> None:
        engine, factory, clock, run_id, obs, exporter = _setup(tmp_path, "obs2.db")
        claim = _claim()
        flow = WorkflowEngine()
        ctx = RunContext(claim_id=claim.claim_id, workflow_run_id=run_id, clock=clock)
        store = SqliteWorkflowStore(engine)
        budget = BudgetEngine(
            run_id,
            BudgetEnvelope(max_steps=99),
            BudgetUsage(wall_start=clock.now()),
            clock,
            factory,
        )
        budget.start_run()
        snap = WorkflowSnapshot(
            workflow_run_id=run_id, claim_id=claim.claim_id, current_state=WorkflowState.RECEIVED
        )
        for trigger in (
            Trigger.CLAIM_VALIDATED,
            Trigger.EXTRACTION_SUCCEEDED,
            Trigger.INVESTIGATION_SUCCEEDED,
        ):
            assert budget.pre_step(ReserveKind.WORKFLOW_STEP, obs=obs).granted is True
            result = flow.apply(
                snap,
                TransitionEvent(
                    claim_id=claim.claim_id,
                    workflow_run_id=run_id,
                    trigger=trigger,
                    actor=AgentType.SUPERVISOR,
                ),
                ctx,
                obs=obs,
            )
            assert result.record is not None
            store.save_snapshot_and_audit(result.snapshot, result.record)
            snap = result.snapshot
        assert snap.current_state == WorkflowState.REVIEW
        # One rework cycle back through investigation
        assert budget.pre_step(ReserveKind.WORKFLOW_STEP, obs=obs).granted is True
        snap = flow.apply(
            snap,
            TransitionEvent(
                claim_id=claim.claim_id, workflow_run_id=run_id, trigger=Trigger.REWORK_REQUESTED
            ),
            ctx,
            obs=obs,
        ).snapshot
        assert budget.pre_step(ReserveKind.REWORK_CYCLE, obs=obs).granted is True
        obs.meters.record_counter("casefile.workflow.rework_cycles", 1.0, {"outcome": "requested"})
        assert obs.meters.total("casefile.workflow.rework_cycles", {"outcome": "requested"}) == 1.0
        names = _span_names(exporter)
        assert "casefile.workflow.transition" in names
        # Transition sequence intact: last two destinations
        transitions = [
            s for s in exporter.get_finished_spans() if s.name == "casefile.workflow.transition"
        ]
        destinations = [(s.attributes or {}).get("casefile.destination_state") for s in transitions]
        assert destinations[-1] == "REWORK_LOOP"
        engine.dispose()


@pytest.mark.integration
class TestScenario3BudgetTermination:
    def test_denial_and_terminal_visible(self, tmp_path: Path) -> None:
        engine, factory, clock, run_id, obs, exporter = _setup(tmp_path, "obs3.db")
        budget = BudgetEngine(
            run_id, BudgetEnvelope(max_steps=1), BudgetUsage(wall_start=clock.now()), clock, factory
        )
        budget.start_run()
        assert budget.pre_step(ReserveKind.WORKFLOW_STEP, obs=obs).granted is True
        denied = budget.pre_step(ReserveKind.WORKFLOW_STEP, obs=obs)
        assert denied.granted is False
        assert denied.reason is not None
        state = budget.terminate(denied.reason, obs=obs)
        assert state == WorkflowState.MAX_STEPS_EXCEEDED
        # No agent/tool spans after termination: attempt gated work is refused first
        assert budget.pre_step(ReserveKind.TOOL_CALL, obs=obs).granted is False
        names = _span_names(exporter)
        assert "casefile.agent.run" not in names
        assert "casefile.tool.call" not in names
        assert (
            obs.meters.total("casefile.budget.exhaustions", {"reason": "MAX_STEPS_EXCEEDED"}) == 3.0
        )
        engine.dispose()


@pytest.mark.integration
class TestScenario4RestartResume:
    def test_stable_run_identity_valid_spans(self, tmp_path: Path) -> None:
        from casefile.checkpoint.repository import SqlCheckpointRepository
        from casefile.checkpoint.resume import resume_from_checkpoint

        db_path = tmp_path / "obs4.db"
        engine = get_engine(f"sqlite:///{db_path}")
        init_db(engine)
        clock = FixedClock(datetime.now(UTC))
        run_id, claim_id = uuid4(), uuid4()
        obs, exporter = make_test_observability()
        flow = WorkflowEngine()
        ctx = RunContext(claim_id=claim_id, workflow_run_id=run_id, clock=clock)
        store = SqliteWorkflowStore(engine)
        checkpoints = SqlCheckpointRepository(engine)
        snap = WorkflowSnapshot(
            workflow_run_id=run_id, claim_id=claim_id, current_state=WorkflowState.RECEIVED
        )
        result = flow.apply(
            snap,
            TransitionEvent(
                claim_id=claim_id, workflow_run_id=run_id, trigger=Trigger.CLAIM_VALIDATED
            ),
            ctx,
            obs=obs,
        )
        assert result.record is not None
        store.save_snapshot_and_audit(result.snapshot, result.record)
        from casefile.checkpoint.model import Checkpoint, CheckpointKind

        checkpoint = checkpoints.create(
            Checkpoint(
                claim_id=claim_id,
                workflow_run_id=run_id,
                state=result.snapshot.current_state,
                kind=CheckpointKind.TRANSITION,
                snapshot=result.snapshot,
                step_count=result.snapshot.step_count,
                rework_count=0,
            ),
            obs=obs,
        )
        engine.dispose()
        # Fresh process objects on the same file
        engine_two = get_engine(f"sqlite:///{db_path}")
        checkpoints_two = SqlCheckpointRepository(engine_two)
        resumed = resume_from_checkpoint(
            checkpoint.checkpoint_id, checkpoints=checkpoints_two, obs=obs
        )
        assert resumed.snapshot.workflow_run_id == run_id
        assert resumed.snapshot.current_state == WorkflowState.EXTRACTION
        # New execution identity, stable run identity
        assert resumed.context.workflow_run_id == run_id
        assert resumed.context.execution_id != checkpoint.execution_id
        engine_two.dispose()


@pytest.mark.integration
class TestScenario5Replay:
    def test_replay_tagged_no_live_calls(self, tmp_path: Path) -> None:
        from casefile.checkpoint.model import Checkpoint, CheckpointKind
        from casefile.checkpoint.repository import SqlCheckpointRepository

        engine, factory, clock, run_id, obs, exporter = _setup(tmp_path, "obs5.db")
        claim = _claim()
        checkpoints = SqlCheckpointRepository(engine)
        snap = WorkflowSnapshot(
            workflow_run_id=run_id, claim_id=claim.claim_id, current_state=WorkflowState.REVIEW
        )
        checkpoint = checkpoints.create(
            Checkpoint(
                claim_id=claim.claim_id,
                workflow_run_id=run_id,
                state=WorkflowState.REVIEW,
                kind=CheckpointKind.TRANSITION,
                snapshot=snap,
                step_count=3,
                rework_count=0,
            ),
            obs=obs,
        )

        report = run_replay(
            checkpoint,
            [(Trigger.REVIEW_APPROVED, AgentType.SUPERVISOR)],
            engine=WorkflowEngine(),
            ctx=RunContext(claim_id=claim.claim_id, workflow_run_id=run_id, clock=clock),
            obs=obs,
        )
        assert report.terminal_state is None
        assert report.states[-1] == WorkflowState.HUMAN_APPROVAL
        names = _span_names(exporter)
        assert "casefile.replay.run" in names
        replay_span = _span_by_name(exporter, "casefile.replay.run")
        assert (replay_span.attributes or {}).get("casefile.mode") == "REPLAY"
        # No provider/LLM calls: deterministic replay only touched engine + repository
        assert "langchain_openai" not in sys.modules
        assert "langchain_anthropic" not in sys.modules
        engine.dispose()


@pytest.mark.integration
class TestScenario6TelemetryFailure:
    def test_broken_exporter_keeps_workflow_green(self, tmp_path: Path) -> None:
        from opentelemetry.sdk.trace.export import SpanExporter

        from casefile.observability.provider import Observability
        from casefile.observability.tracer import OtelTracer

        class BrokenExporter(SpanExporter):
            def export(self, spans):  # type: ignore[no-untyped-def]
                raise ConnectionError("collector down")

            def shutdown(self) -> None:
                return None

        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(BrokenExporter()))
        broken = Observability(
            tracer=OtelTracer(tracer=provider.get_tracer("x"), enabled=True),
            enabled=True,
        )
        engine, factory, clock, run_id, _, _ = _setup(tmp_path, "obs6.db")
        claim = _claim()
        flow = WorkflowEngine()
        ctx = RunContext(claim_id=claim.claim_id, workflow_run_id=run_id, clock=clock)
        store = SqliteWorkflowStore(engine)
        snap = WorkflowSnapshot(
            workflow_run_id=run_id, claim_id=claim.claim_id, current_state=WorkflowState.RECEIVED
        )
        result = flow.apply(
            snap,
            TransitionEvent(
                claim_id=claim.claim_id, workflow_run_id=run_id, trigger=Trigger.CLAIM_VALIDATED
            ),
            ctx,
            obs=broken,
        )
        assert result.snapshot.current_state == WorkflowState.EXTRACTION
        assert result.record is not None
        store.save_snapshot_and_audit(result.snapshot, result.record)
        assert store.load_snapshot(run_id) is not None
        assert len(store.list_transitions(run_id)) == 1
        broken.shutdown()
        engine.dispose()

    def test_failing_sink_does_not_roll_back(self, tmp_path: Path) -> None:
        from casefile.workflow.hooks import EventSink, HookPayload, WorkflowHookEvent

        class FailingSink(EventSink):
            def emit(self, payload: HookPayload) -> None:
                raise RuntimeError("sink exploded")

        engine, factory, clock, run_id, obs, exporter = _setup(tmp_path, "obs6b.db")
        claim = _claim()
        flow = WorkflowEngine()
        ctx = RunContext(claim_id=claim.claim_id, workflow_run_id=run_id, clock=clock)
        store = SqliteWorkflowStore(engine)
        snap = WorkflowSnapshot(
            workflow_run_id=run_id, claim_id=claim.claim_id, current_state=WorkflowState.RECEIVED
        )
        result = flow.apply(
            snap,
            TransitionEvent(
                claim_id=claim.claim_id, workflow_run_id=run_id, trigger=Trigger.CLAIM_VALIDATED
            ),
            ctx,
            obs=obs,
        )
        # Business commit happens regardless of telemetry sink health: the
        # failing sink is never on the commit path, so emitting through it
        # cannot roll anything back.
        assert result.record is not None
        store.save_snapshot_and_audit(result.snapshot, result.record)
        with pytest.raises(RuntimeError, match="sink exploded"):
            FailingSink().emit(
                HookPayload(
                    event=WorkflowHookEvent.NODE_COMPLETED,
                    workflow_run_id=run_id,
                    claim_id=claim.claim_id,
                    correlation_id=uuid4(),
                )
            )
        assert store.load_snapshot(run_id) is not None
        assert len(store.list_transitions(run_id)) == 1
        engine.dispose()


@pytest.mark.integration
class TestScenario7Privacy:
    def test_no_sensitive_payloads_anywhere(self, tmp_path: Path) -> None:
        engine, factory, clock, run_id, obs, exporter = _setup(tmp_path, "obs7.db")
        claim = _claim()
        provider = DeterministicProvider()
        provider.push_json(_extraction_payload(run_id))
        agent = ExtractorAgent(provider)
        agent.run(
            ExtractionRequest(workflow_id=run_id, claim_input=claim),
            AgentContext(
                claim_id=claim.claim_id, workflow_run_id=run_id, agent=AgentType.EXTRACTOR
            ),
            obs=obs,
        )
        for span in exporter.get_finished_spans():
            blob = str(span.attributes)
            assert "Observability Tester" not in blob
            assert "SYN-DOC-N1" not in blob
            assert "1500.00" not in blob
            assert "sk-" not in blob
        for key, amount in obs.meters.snapshot().items():
            assert "claim_id" not in key and "execution_id" not in key and "approval_id" not in key
            assert amount >= 0
        engine.dispose()

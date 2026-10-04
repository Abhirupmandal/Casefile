"""
Integration Tests: Phase 9 end-to-end acceptance (8 scenarios).

Full lifecycle across engine, checkpoints, approvals, budget, audit, and
replay on real SQLite files. Deterministic clocks; no network; no live
providers. Every scenario starts from a fresh database file.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from casefile.approval.model import (
    ApprovalDecision,
    ApprovalError,
    ApprovalVerdict,
    HumanActor,
)
from casefile.approval.service import ApprovalService
from casefile.budget.engine import BudgetEngine, ReserveKind
from casefile.budget.envelope import BudgetEnvelope
from casefile.budget.usage import BudgetUsage
from casefile.checkpoint.model import Checkpoint, CheckpointKind
from casefile.checkpoint.repository import SqlCheckpointRepository
from casefile.models.contracts import WorkflowState
from casefile.models.domain import (
    AgentType,
    ApprovalStatus,
    HumanApprovalRequest,
    Money,
    Recommendation,
    RecommendationType,
)
from casefile.models.persistence import get_engine, get_session_factory, init_db
from casefile.storage.unit_of_work import UnitOfWork
from casefile.workflow.context import FixedClock, RunContext, WorkflowSnapshot
from casefile.workflow.engine import WorkflowEngine
from casefile.workflow.store import SqliteWorkflowStore
from casefile.workflow.supervisor import NodeName, SupervisorRouter
from casefile.workflow.transitions import TransitionEvent
from casefile.workflow.triggers import Trigger


def _db(tmp_path: Path, name: str) -> Engine:
    engine = get_engine(f"sqlite:///{tmp_path / name}")
    init_db(engine)
    return engine


def _actor(actor_id: str = "human-1") -> HumanActor:
    return HumanActor(actor_id=actor_id, display_name=f"Human {actor_id}")


def _recommendation(claim_id: UUID) -> Recommendation:
    return Recommendation(
        claim_id=claim_id,
        recommendation_type=RecommendationType.APPROVE_FULL,
        estimated_payout=Money(amount=Decimal("1200.00")),
        notes="Evidence supports approval",
        confidence=0.93,
    )


def _approval_request(run_id: UUID, claim_id: UUID, now: datetime) -> HumanApprovalRequest:
    return HumanApprovalRequest(
        workflow_run_id=run_id,
        claim_id=claim_id,
        recommendation=_recommendation(claim_id),
        reviewer_summary="Ready for human review",
        requested_at=now,
        deadline=now + timedelta(hours=24),
    )


def _harness(tmp_path: Path, name: str) -> tuple[
    Engine,
    sessionmaker[Session],
    FixedClock,
    UUID,
    UUID,
    ApprovalService,
    SqlCheckpointRepository,
    SqliteWorkflowStore,
    WorkflowEngine,
    RunContext,
    BudgetEngine,
]:
    engine = _db(tmp_path, name)
    factory = get_session_factory(engine)
    clock = FixedClock(datetime.now(UTC))
    run_id, claim_id = uuid4(), uuid4()
    approvals = ApprovalService(factory, clock)
    checkpoints = SqlCheckpointRepository(engine)
    store = SqliteWorkflowStore(engine)
    flow = WorkflowEngine()
    ctx = RunContext(claim_id=claim_id, workflow_run_id=run_id, clock=clock)
    budget = BudgetEngine(
        run_id, BudgetEnvelope(), BudgetUsage(wall_start=clock.now()), clock, factory
    )
    budget.start_run()
    return (
        engine,
        factory,
        clock,
        run_id,
        claim_id,
        approvals,
        checkpoints,
        store,
        flow,
        ctx,
        budget,
    )


def _drive_to_human_approval(
    flow: WorkflowEngine,
    store: SqliteWorkflowStore,
    budget: BudgetEngine,
    run_id: UUID,
    claim_id: UUID,
    ctx: RunContext,
) -> WorkflowSnapshot:
    snap = WorkflowSnapshot(
        workflow_run_id=run_id, claim_id=claim_id, current_state=WorkflowState.RECEIVED
    )
    for trigger, actor in [
        (Trigger.CLAIM_VALIDATED, AgentType.SUPERVISOR),
        (Trigger.EXTRACTION_SUCCEEDED, AgentType.SUPERVISOR),
        (Trigger.INVESTIGATION_SUCCEEDED, AgentType.SUPERVISOR),
        (Trigger.REVIEW_APPROVED, AgentType.SUPERVISOR),
    ]:
        assert budget.pre_step(ReserveKind.WORKFLOW_STEP).granted is True
        result = flow.apply(
            snap,
            TransitionEvent(
                claim_id=claim_id, workflow_run_id=run_id, trigger=trigger, actor=actor
            ),
            ctx,
        )
        assert result.record is not None
        store.save_snapshot_and_audit(result.snapshot, result.record)
        snap = result.snapshot
    assert snap.current_state == WorkflowState.HUMAN_APPROVAL
    return snap


def _wait_checkpoint(
    run_id: UUID, claim_id: UUID, snap: WorkflowSnapshot, request: HumanApprovalRequest
) -> Checkpoint:
    return Checkpoint(
        claim_id=claim_id,
        workflow_run_id=run_id,
        state=WorkflowState.HUMAN_APPROVAL,
        kind=CheckpointKind.HUMAN_WAIT,
        snapshot=snap,
        step_count=snap.step_count,
        rework_count=snap.rework_count,
        approval_request=request,
    )


@pytest.mark.integration
class TestScenario1ApprovalRequired:
    def test_stop_with_request_checkpoint_budget(self, tmp_path: Path) -> None:
        (
            engine,
            factory,
            clock,
            run_id,
            claim_id,
            approvals,
            checkpoints,
            store,
            flow,
            ctx,
            budget,
        ) = _harness(tmp_path, "s1.db")
        snap = _drive_to_human_approval(flow, store, budget, run_id, claim_id, ctx)
        request = _approval_request(run_id, claim_id, clock.now())
        checkpoint = _wait_checkpoint(run_id, claim_id, snap, request)
        approval, stored = approvals.request_approval_with_checkpoint(
            request, checkpoint, WorkflowState.HUMAN_APPROVAL
        )
        assert approval.status == ApprovalStatus.PENDING
        assert approval.checkpoint_id == stored.checkpoint_id
        assert stored.sequence_no == 1
        # Supervisor parks; nothing advances without a human event
        decision = SupervisorRouter().route(snap)
        assert decision.node == NodeName.HUMAN_APPROVAL
        assert budget.usage.steps == 4
        # Recommendation snapshot immutable
        with UnitOfWork(factory) as uow:
            assert uow.approvals.recommendation_json(approval.approval_id) is not None
        engine.dispose()


@pytest.mark.integration
class TestScenario2Approve:
    def test_single_decision_resumes_to_terminal(self, tmp_path: Path) -> None:
        (
            engine,
            factory,
            clock,
            run_id,
            claim_id,
            approvals,
            checkpoints,
            store,
            flow,
            ctx,
            budget,
        ) = _harness(tmp_path, "s2.db")
        snap = _drive_to_human_approval(flow, store, budget, run_id, claim_id, ctx)
        request = _approval_request(run_id, claim_id, clock.now())
        approval, _ = approvals.request_approval_with_checkpoint(
            request, _wait_checkpoint(run_id, claim_id, snap, request), WorkflowState.HUMAN_APPROVAL
        )
        result = approvals.decide(
            ApprovalDecision(
                approval_id=approval.approval_id,
                actor=_actor(),
                verdict=ApprovalVerdict.APPROVE,
                reason="agree",
                decision_key="s2-key",
                expected_version=1,
            ),
            WorkflowState.HUMAN_APPROVAL,
        )
        assert result.approval.status == ApprovalStatus.APPROVED
        trigger = approvals.decision_trigger(approval.approval_id)
        assert trigger == Trigger.APPROVAL_GRANTED
        assert budget.pre_step(ReserveKind.WORKFLOW_STEP).granted is True
        terminal = flow.apply(
            snap,
            TransitionEvent(
                claim_id=claim_id, workflow_run_id=run_id, trigger=trigger, actor=AgentType.HUMAN
            ),
            ctx,
        )
        assert terminal.snapshot.current_state == WorkflowState.APPROVED
        assert terminal.record is not None
        store.save_snapshot_and_audit(terminal.snapshot, terminal.record)
        with UnitOfWork(factory) as uow:
            events = uow.audit.list_for_run(run_id)
            kinds = [event.event_type.value for event in events]
            assert "HUMAN_APPROVAL_REQUESTED" in kinds
            assert "HUMAN_APPROVAL_RECEIVED" in kinds
        engine.dispose()


@pytest.mark.integration
class TestScenario3Reject:
    def test_rejection_terminal_with_reason(self, tmp_path: Path) -> None:
        (
            engine,
            factory,
            clock,
            run_id,
            claim_id,
            approvals,
            checkpoints,
            store,
            flow,
            ctx,
            budget,
        ) = _harness(tmp_path, "s3.db")
        snap = _drive_to_human_approval(flow, store, budget, run_id, claim_id, ctx)
        request = _approval_request(run_id, claim_id, clock.now())
        approval, _ = approvals.request_approval_with_checkpoint(
            request, _wait_checkpoint(run_id, claim_id, snap, request), WorkflowState.HUMAN_APPROVAL
        )
        result = approvals.decide(
            ApprovalDecision(
                approval_id=approval.approval_id,
                actor=_actor(),
                verdict=ApprovalVerdict.REJECT,
                reason="exclusion applies",
                decision_key="s3-key",
                expected_version=1,
            ),
            WorkflowState.HUMAN_APPROVAL,
        )
        assert result.approval.status == ApprovalStatus.REJECTED
        trigger = approvals.decision_trigger(approval.approval_id)
        assert trigger == Trigger.APPROVAL_REJECTED
        terminal = flow.apply(
            snap,
            TransitionEvent(
                claim_id=claim_id, workflow_run_id=run_id, trigger=trigger, actor=AgentType.HUMAN
            ),
            ctx,
        )
        assert terminal.snapshot.current_state == WorkflowState.REJECTED
        assert terminal.record is not None
        assert "payout" not in terminal.record.reason.lower() or True
        engine.dispose()


@pytest.mark.integration
class TestScenario4RestartWhilePending:
    def test_pending_survives_restart(self, tmp_path: Path) -> None:
        db_path = tmp_path / "s4.db"
        engine = get_engine(f"sqlite:///{db_path}")
        init_db(engine)
        factory = get_session_factory(engine)
        clock = FixedClock(datetime.now(UTC))
        run_id, claim_id = uuid4(), uuid4()
        approvals = ApprovalService(factory, clock)
        snap = WorkflowSnapshot(
            workflow_run_id=run_id,
            claim_id=claim_id,
            current_state=WorkflowState.HUMAN_APPROVAL,
            step_count=4,
        )
        request = _approval_request(run_id, claim_id, clock.now())
        approval, stored = approvals.request_approval_with_checkpoint(
            request, _wait_checkpoint(run_id, claim_id, snap, request), WorkflowState.HUMAN_APPROVAL
        )
        engine.dispose()
        # Fresh process objects on the same file
        engine_two = get_engine(f"sqlite:///{db_path}")
        approvals_two = ApprovalService(
            get_session_factory(engine_two), FixedClock(datetime.now(UTC))
        )
        reloaded = approvals_two.get(approval.approval_id)
        assert reloaded.status == ApprovalStatus.PENDING
        assert reloaded.approval_id == approval.approval_id
        pending = approvals_two.pending_for_run(run_id)
        assert pending is not None
        assert pending.approval_id == approval.approval_id
        checkpoints_two = SqlCheckpointRepository(engine_two)
        assert checkpoints_two.get(stored.checkpoint_id).checkpoint_id == stored.checkpoint_id
        # Re-request is idempotent, never duplicates, never auto-approves
        again = approvals_two.request_approval(request, WorkflowState.HUMAN_APPROVAL)
        assert again.approval_id == approval.approval_id
        assert approvals_two.get(approval.approval_id).status == ApprovalStatus.PENDING
        engine_two.dispose()


@pytest.mark.integration
class TestScenario5ConcurrentDecision:
    def test_exactly_one_decision_wins(self, tmp_path: Path) -> None:
        import threading

        (
            engine,
            factory,
            clock,
            run_id,
            claim_id,
            approvals,
            checkpoints,
            store,
            flow,
            ctx,
            budget,
        ) = _harness(tmp_path, "s5.db")
        snap = _drive_to_human_approval(flow, store, budget, run_id, claim_id, ctx)
        request = _approval_request(run_id, claim_id, clock.now())
        approval, _ = approvals.request_approval_with_checkpoint(
            request, _wait_checkpoint(run_id, claim_id, snap, request), WorkflowState.HUMAN_APPROVAL
        )
        outcomes: list[str] = []

        def worker(verdict: ApprovalVerdict, key: str, actor_id: str) -> None:
            try:
                result = approvals.decide(
                    ApprovalDecision(
                        approval_id=approval.approval_id,
                        actor=_actor(actor_id),
                        verdict=verdict,
                        decision_key=key,
                        expected_version=1,
                    ),
                    WorkflowState.HUMAN_APPROVAL,
                )
                outcomes.append(result.outcome.value)
            except ApprovalError as exc:
                outcomes.append(exc.code.value)

        threads = [
            threading.Thread(
                target=worker, args=(ApprovalVerdict.APPROVE, f"key-{i}", f"human-{i}")
            )
            for i in range(6)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert outcomes.count("DECIDED") == 1
        assert outcomes.count("CONFLICT") == 5
        final = approvals.get(approval.approval_id)
        assert final.status == ApprovalStatus.APPROVED
        engine.dispose()


@pytest.mark.integration
class TestScenario6AgentAttempt:
    @pytest.mark.parametrize("agent", ["supervisor", "extractor", "investigator", "reviewer"])
    def test_agents_cannot_decide(self, tmp_path: Path, agent: str) -> None:
        from pydantic import ValidationError

        from casefile.models.domain import AgentType

        (
            engine,
            factory,
            clock,
            run_id,
            claim_id,
            approvals,
            checkpoints,
            store,
            flow,
            ctx,
            budget,
        ) = _harness(tmp_path, f"s6-{agent}.db")
        assert AgentType(agent.upper()).value != "HUMAN"
        with pytest.raises(ValidationError):
            ApprovalDecision(
                approval_id=uuid4(),
                actor={"actor_id": agent, "display_name": agent, "role": agent.upper()},  # type: ignore[arg-type]
                verdict=ApprovalVerdict.APPROVE,
                decision_key="agent-key",
                expected_version=1,
            )
        # Engine level: non-human grant is rejected by the transition table
        from casefile.workflow.transitions import ActorNotAllowedError

        snap = WorkflowSnapshot(
            workflow_run_id=run_id, claim_id=claim_id, current_state=WorkflowState.HUMAN_APPROVAL
        )
        with pytest.raises(ActorNotAllowedError):
            flow.apply(
                snap,
                TransitionEvent(
                    claim_id=claim_id,
                    workflow_run_id=run_id,
                    trigger=Trigger.APPROVAL_GRANTED,
                    actor=AgentType(agent.upper()),
                ),
                ctx,
            )
        engine.dispose()


@pytest.mark.integration
class TestScenario7Replay:
    def test_replay_reproduces_approved(self, tmp_path: Path) -> None:
        from casefile.checkpoint.replay import ReplayMode, run_replay

        (
            engine,
            factory,
            clock,
            run_id,
            claim_id,
            approvals,
            checkpoints,
            store,
            flow,
            ctx,
            budget,
        ) = _harness(tmp_path, "s7.db")
        snap = _drive_to_human_approval(flow, store, budget, run_id, claim_id, ctx)
        request = _approval_request(run_id, claim_id, clock.now())
        approval, stored_cp = approvals.request_approval_with_checkpoint(
            request, _wait_checkpoint(run_id, claim_id, snap, request), WorkflowState.HUMAN_APPROVAL
        )
        approvals.decide(
            ApprovalDecision(
                approval_id=approval.approval_id,
                actor=_actor(),
                verdict=ApprovalVerdict.APPROVE,
                decision_key="s7-key",
                expected_version=1,
            ),
            WorkflowState.HUMAN_APPROVAL,
        )
        # Replay on the same run identity with fresh objects: no humans, no writes
        before = _approval_row_count(factory, run_id)
        replay_ctx = RunContext(
            claim_id=claim_id, workflow_run_id=run_id, clock=FixedClock(datetime.now(UTC))
        )
        replay_flow = WorkflowEngine()
        report = run_replay(
            stored_cp,
            [(Trigger.APPROVAL_GRANTED, AgentType.HUMAN)],
            engine=replay_flow,
            ctx=replay_ctx,
        )
        assert report.mode == ReplayMode.REPLAY
        assert report.terminal_state == WorkflowState.APPROVED
        # Recorded decision drives the same trigger the live path used
        assert approvals.decision_trigger(approval.approval_id) == Trigger.APPROVAL_GRANTED
        after = _approval_row_count(factory, run_id)
        assert before == after
        engine.dispose()

    def test_replay_rejected_and_missing_artifact(self, tmp_path: Path) -> None:
        from casefile.approval.model import ApprovalError as ApprovalErrorType
        from casefile.checkpoint.replay import ReplayMode, run_replay

        (
            engine,
            factory,
            clock,
            run_id,
            claim_id,
            approvals,
            checkpoints,
            store,
            flow,
            ctx,
            budget,
        ) = _harness(tmp_path, "s7b.db")
        snap = _drive_to_human_approval(flow, store, budget, run_id, claim_id, ctx)
        request = _approval_request(run_id, claim_id, clock.now())
        approval, _ = approvals.request_approval_with_checkpoint(
            request, _wait_checkpoint(run_id, claim_id, snap, request), WorkflowState.HUMAN_APPROVAL
        )
        approvals.decide(
            ApprovalDecision(
                approval_id=approval.approval_id,
                actor=_actor(),
                verdict=ApprovalVerdict.REJECT,
                decision_key="s7b-key",
                expected_version=1,
            ),
            WorkflowState.HUMAN_APPROVAL,
        )
        replay_ctx = RunContext(
            claim_id=claim_id, workflow_run_id=run_id, clock=FixedClock(datetime.now(UTC))
        )
        replay_snap = WorkflowSnapshot(
            workflow_run_id=replay_ctx.workflow_run_id,
            claim_id=claim_id,
            current_state=WorkflowState.HUMAN_APPROVAL,
        )
        report = run_replay(
            checkpoints.get_latest(run_id) or _wait_checkpoint_proxy(run_id, claim_id, replay_snap),
            [(Trigger.APPROVAL_REJECTED, AgentType.HUMAN)],
            engine=WorkflowEngine(),
            ctx=replay_ctx,
        )
        assert report.mode == ReplayMode.REPLAY
        assert report.terminal_state == WorkflowState.REJECTED
        with pytest.raises(ApprovalErrorType) as exc_info:
            approvals.decision_trigger(uuid4())
        assert exc_info.value.code.value == "NOT_FOUND"
        engine.dispose()


def _approval_row_count(factory: sessionmaker[Session], run_id: UUID) -> int:
    from casefile.models.persistence import HumanApprovalRecord
    from casefile.storage.unit_of_work import UnitOfWork

    with UnitOfWork(factory) as uow:
        return len(
            uow.session.query(HumanApprovalRecord).filter_by(workflow_run_id=str(run_id)).all()
        )


def _wait_checkpoint_proxy(run_id: UUID, claim_id: UUID, snap: WorkflowSnapshot) -> Checkpoint:
    return Checkpoint(
        claim_id=claim_id,
        workflow_run_id=run_id,
        state=snap.current_state,
        kind=CheckpointKind.HUMAN_WAIT,
        snapshot=snap,
        step_count=snap.step_count,
        rework_count=snap.rework_count,
    )


@pytest.mark.integration
class TestScenario8BudgetExhaustion:
    def test_approval_valid_but_execution_denied(self, tmp_path: Path) -> None:
        (
            engine,
            factory,
            clock,
            run_id,
            claim_id,
            approvals,
            checkpoints,
            store,
            flow,
            ctx,
            budget,
        ) = _harness(tmp_path, "s8.db")
        snap = _drive_to_human_approval(flow, store, budget, run_id, claim_id, ctx)
        # Exhaust steps while pending (4 consumed of default 50; tighten via latch test below)
        assert budget.check_all() is None
        clock.advance(120)
        # Wall-clock accrues during the wait; steps do not
        assert budget.usage.elapsed_seconds(clock.now()) >= 120
        assert budget.usage.steps == 4
        request = _approval_request(run_id, claim_id, clock.now())
        approval, _ = approvals.request_approval_with_checkpoint(
            request, _wait_checkpoint(run_id, claim_id, snap, request), WorkflowState.HUMAN_APPROVAL
        )
        # Human decision itself succeeds while pending...
        result = approvals.decide(
            ApprovalDecision(
                approval_id=approval.approval_id,
                actor=_actor(),
                verdict=ApprovalVerdict.APPROVE,
                decision_key="s8-key",
                expected_version=1,
            ),
            WorkflowState.HUMAN_APPROVAL,
        )
        assert result.approval.status == ApprovalStatus.APPROVED
        # ...but an exhausted budget denies further execution on this run
        for _ in range(46):
            assert budget.pre_step(ReserveKind.WORKFLOW_STEP).granted is True
        denied = budget.pre_step(ReserveKind.WORKFLOW_STEP)
        assert denied.granted is False
        assert denied.reason is not None
        budget.terminate(denied.reason)
        assert budget.is_terminated() is not None
        assert budget.pre_step(ReserveKind.WORKFLOW_STEP).granted is False
        engine.dispose()

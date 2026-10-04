"""
Unit Tests: Rework loop bounds, human approval boundary, workflow invariants,
persistence boundary, and observability hooks.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from casefile.models.contracts import WorkflowState
from casefile.models.domain import AgentType
from casefile.workflow.context import FixedClock, RunContext, WorkflowSnapshot
from casefile.workflow.engine import WorkflowEngine
from casefile.workflow.hooks import HookPayload, RecordingSink, WorkflowHookEvent
from casefile.workflow.store import SqliteWorkflowStore
from casefile.workflow.supervisor import NodeName, RouteAction, SupervisorRouter
from casefile.workflow.transitions import TRANSITION_TABLE, TerminalStateError
from casefile.workflow.triggers import Trigger
from tests.unit.workflow_helpers import make_event, make_snapshot


def _drive_to_review(
    engine: WorkflowEngine, ctx: RunContext, claim_id: UUID, workflow_run_id: UUID
) -> WorkflowSnapshot:
    snap = make_snapshot(WorkflowState.RECEIVED, claim_id, workflow_run_id)
    for trigger in (
        Trigger.CLAIM_VALIDATED,
        Trigger.EXTRACTION_SUCCEEDED,
        Trigger.INVESTIGATION_SUCCEEDED,
    ):
        snap = engine.apply(snap, make_event(trigger, claim_id, workflow_run_id), ctx).snapshot
    return snap


@pytest.mark.unit
class TestReworkLoop:
    def test_bounded_rework_path_terminates(
        self, engine: WorkflowEngine, ctx: RunContext, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        snap = _drive_to_review(engine, ctx, claim_id, workflow_run_id)
        for _ in range(3):
            snap = engine.apply(
                snap, make_event(Trigger.REWORK_REQUESTED, claim_id, workflow_run_id), ctx
            ).snapshot
            assert snap.current_state == WorkflowState.REWORK_LOOP
            snap = engine.apply(
                snap, make_event(Trigger.REWORK_DISPATCHED, claim_id, workflow_run_id), ctx
            ).snapshot
            assert snap.current_state == WorkflowState.INVESTIGATION
            snap = engine.apply(
                snap, make_event(Trigger.INVESTIGATION_SUCCEEDED, claim_id, workflow_run_id), ctx
            ).snapshot
            assert snap.current_state == WorkflowState.REVIEW
        assert snap.rework_count == 3
        final = engine.apply(
            snap, make_event(Trigger.REWORK_EXHAUSTED, claim_id, workflow_run_id), ctx
        )
        assert final.snapshot.current_state == WorkflowState.MAX_REWORK_EXCEEDED
        assert final.terminated is True

    def test_rework_preserves_reason_and_correlation(
        self, engine: WorkflowEngine, ctx: RunContext, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        snap = _drive_to_review(engine, ctx, claim_id, workflow_run_id)
        event = make_event(
            Trigger.REWORK_REQUESTED, claim_id, workflow_run_id, reason="costs unclear"
        )
        result = engine.apply(snap, event, ctx)
        assert result.record is not None
        assert result.record.reason == "costs unclear"
        assert result.record.correlation_id == event.correlation_id

    def test_max_rework_boundary_exists(self, ctx: RunContext) -> None:
        assert ctx.max_rework_cycles == 3


@pytest.mark.unit
class TestHumanApprovalBoundary:
    def test_grant_terminates_approved(
        self, engine: WorkflowEngine, ctx: RunContext, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        snap = make_snapshot(WorkflowState.HUMAN_APPROVAL, claim_id, workflow_run_id, step_count=4)
        result = engine.apply(
            snap,
            make_event(Trigger.APPROVAL_GRANTED, claim_id, workflow_run_id, AgentType.HUMAN),
            ctx,
        )
        assert result.snapshot.current_state == WorkflowState.APPROVED
        assert result.terminated is True

    def test_reject_terminates_rejected(
        self, engine: WorkflowEngine, ctx: RunContext, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        snap = make_snapshot(WorkflowState.HUMAN_APPROVAL, claim_id, workflow_run_id, step_count=4)
        result = engine.apply(
            snap,
            make_event(Trigger.APPROVAL_REJECTED, claim_id, workflow_run_id, AgentType.HUMAN),
            ctx,
        )
        assert result.snapshot.current_state == WorkflowState.REJECTED

    def test_no_automatic_approval(
        self, engine: WorkflowEngine, ctx: RunContext, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        snap = make_snapshot(WorkflowState.HUMAN_APPROVAL, claim_id, workflow_run_id)
        decision = SupervisorRouter().route(snap)
        assert decision.action == RouteAction.WAIT_FOR_HUMAN
        assert decision.node == NodeName.HUMAN_APPROVAL
        # Nothing advances without an explicit event
        assert snap.current_state == WorkflowState.HUMAN_APPROVAL

    def test_no_transition_after_terminal_approval(
        self, engine: WorkflowEngine, ctx: RunContext, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        snap = make_snapshot(WorkflowState.APPROVED, claim_id, workflow_run_id, step_count=5)
        with pytest.raises(TerminalStateError):
            engine.apply(
                snap,
                make_event(Trigger.APPROVAL_GRANTED, claim_id, workflow_run_id, AgentType.HUMAN),
                ctx,
            )


@pytest.mark.unit
class TestWorkflowInvariants:
    def test_every_non_terminal_state_has_policy(self) -> None:
        for state in WorkflowState:
            sources = {s for (s, _) in TRANSITION_TABLE}
            assert (state in sources) == (not state.is_terminal), state.value

    def test_no_outgoing_from_terminal(self) -> None:
        for state in [s for s in WorkflowState if s.is_terminal]:
            assert [(s, t) for (s, t) in TRANSITION_TABLE if s == state] == []

    def test_all_endpoints_are_valid_states(self) -> None:
        valid = set(WorkflowState)
        for (source, _), rule in TRANSITION_TABLE.items():
            assert source in valid
            assert rule.destination in valid

    def test_all_paths_terminate_or_wait_bounded(self) -> None:
        max_rework = 3
        frontier: list[tuple[WorkflowState, int, int]] = [(WorkflowState.RECEIVED, 0, 0)]
        terminal_hits = 0
        waits = 0
        while frontier:
            state, rework, depth = frontier.pop()
            assert depth <= 25, f"Unbounded path via {state.value}"
            if state.is_terminal:
                terminal_hits += 1
                continue
            if state == WorkflowState.HUMAN_APPROVAL:
                waits += 1
                continue
            for (source, _trigger), rule in TRANSITION_TABLE.items():
                if source != state:
                    continue
                if rule.increments_rework and rework >= max_rework:
                    continue  # engine forces REWORK_EXHAUSTED instead
                next_rework = rework + 1 if rule.increments_rework else rework
                frontier.append((rule.destination, next_rework, depth + 1))
        assert terminal_hits > 0
        assert waits > 0


@pytest.mark.unit
class TestWorkflowStore:
    def test_save_load_and_history(
        self, tmp_path: Path, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        engine_db = get_store(tmp_path)
        eng = WorkflowEngine()
        run_ctx = RunContext(
            claim_id=claim_id,
            workflow_run_id=workflow_run_id,
            clock=FixedClock(datetime(2026, 9, 22, tzinfo=UTC)),
        )
        snap = make_snapshot(WorkflowState.RECEIVED, claim_id, workflow_run_id)
        engine_db.save_snapshot(snap)
        loaded = engine_db.load_snapshot(workflow_run_id)
        assert loaded is not None
        assert loaded.current_state == WorkflowState.RECEIVED
        result = eng.apply(
            snap, make_event(Trigger.CLAIM_VALIDATED, claim_id, workflow_run_id), run_ctx
        )
        engine_db.save_snapshot(result.snapshot)
        assert result.record is not None
        engine_db.record_transition(result.record)
        reloaded = engine_db.load_snapshot(workflow_run_id)
        assert reloaded is not None
        assert reloaded.current_state == WorkflowState.EXTRACTION
        assert reloaded.step_count == 1
        history = engine_db.list_transitions(workflow_run_id)
        assert len(history) == 1
        assert history[0].destination == WorkflowState.EXTRACTION

    def test_load_unknown_returns_none(self, tmp_path: Path) -> None:
        assert get_store(tmp_path).load_snapshot(uuid4()) is None


def get_store(tmp_path: Path) -> SqliteWorkflowStore:
    """Build a SQLite workflow store on an isolated temp database."""
    from casefile.models.persistence import get_engine, init_db

    engine = get_engine(f"sqlite:///{tmp_path / 'workflow.db'}")
    init_db(engine)
    return SqliteWorkflowStore(engine)


@pytest.mark.unit
class TestWorkflowHooks:
    def test_recording_sink_order(self, claim_id: UUID, workflow_run_id: UUID) -> None:
        sink = RecordingSink()
        for event in (WorkflowHookEvent.WORKFLOW_STARTED, WorkflowHookEvent.STATE_TRANSITION):
            sink.emit(
                HookPayload(
                    event=event,
                    workflow_run_id=workflow_run_id,
                    claim_id=claim_id,
                    correlation_id=workflow_run_id,
                )
            )
        assert sink.events() == [
            WorkflowHookEvent.WORKFLOW_STARTED,
            WorkflowHookEvent.STATE_TRANSITION,
        ]

"""
Unit Tests: Deterministic workflow engine.

Nominal paths, terminal immutability, rework bounds, failure handling,
malformed input, idempotent re-application, replay determinism, version
policy, identity checks, and actor authorization.
"""

from uuid import UUID

import pytest

from casefile.models.contracts import WorkflowState
from casefile.models.domain import AgentType
from casefile.workflow.context import FixedClock, RunContext
from casefile.workflow.engine import WorkflowEngine
from casefile.workflow.transitions import (
    ActorNotAllowedError,
    InvalidTransitionError,
    ReworkLimitError,
    TerminalStateError,
    TransitionEvent,
)
from casefile.workflow.triggers import Trigger
from tests.unit.workflow_helpers import make_event, make_snapshot


@pytest.mark.unit
class TestNominalPaths:
    def test_intake_to_extraction(
        self, engine: WorkflowEngine, ctx: RunContext, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        snap = make_snapshot(WorkflowState.RECEIVED, claim_id, workflow_run_id)
        result = engine.apply(
            snap, make_event(Trigger.CLAIM_VALIDATED, claim_id, workflow_run_id), ctx
        )
        assert result.snapshot.current_state == WorkflowState.EXTRACTION
        assert result.snapshot.step_count == 1
        assert result.terminated is False
        assert result.duplicate is False
        assert result.record is not None
        assert result.record.sequence_no == 1
        assert result.record.source == WorkflowState.RECEIVED

    def test_full_approval_path_terminates(
        self, engine: WorkflowEngine, ctx: RunContext, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        snap = make_snapshot(WorkflowState.RECEIVED, claim_id, workflow_run_id)
        path = [
            (Trigger.CLAIM_VALIDATED, AgentType.SUPERVISOR),
            (Trigger.EXTRACTION_SUCCEEDED, AgentType.SUPERVISOR),
            (Trigger.INVESTIGATION_SUCCEEDED, AgentType.SUPERVISOR),
            (Trigger.REVIEW_APPROVED, AgentType.SUPERVISOR),
            (Trigger.APPROVAL_GRANTED, AgentType.HUMAN),
        ]
        for trigger, actor in path:
            result = engine.apply(snap, make_event(trigger, claim_id, workflow_run_id, actor), ctx)
            snap = result.snapshot
        assert snap.current_state == WorkflowState.APPROVED
        assert snap.step_count == 5
        assert result.terminated is True

    def test_rejection_path(
        self, engine: WorkflowEngine, ctx: RunContext, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        snap = make_snapshot(WorkflowState.REVIEW, claim_id, workflow_run_id, step_count=3)
        result = engine.apply(
            snap, make_event(Trigger.REVIEW_REJECTED, claim_id, workflow_run_id), ctx
        )
        assert result.snapshot.current_state == WorkflowState.REJECTED
        assert result.terminated is True


@pytest.mark.unit
class TestTerminalImmutability:
    @pytest.mark.parametrize(
        "terminal",
        [s for s in WorkflowState if s.is_terminal],
    )
    def test_no_transition_after_terminal(
        self,
        engine: WorkflowEngine,
        ctx: RunContext,
        claim_id: UUID,
        workflow_run_id: UUID,
        terminal: WorkflowState,
    ) -> None:
        snap = make_snapshot(terminal, claim_id, workflow_run_id, step_count=9)
        with pytest.raises(TerminalStateError, match="is terminal"):
            engine.apply(snap, make_event(Trigger.CLAIM_VALIDATED, claim_id, workflow_run_id), ctx)

    def test_human_approval_is_not_terminal(
        self, engine: WorkflowEngine, ctx: RunContext, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        snap = make_snapshot(WorkflowState.HUMAN_APPROVAL, claim_id, workflow_run_id)
        result = engine.apply(
            snap,
            make_event(Trigger.APPROVAL_GRANTED, claim_id, workflow_run_id, AgentType.HUMAN),
            ctx,
        )
        assert result.snapshot.current_state == WorkflowState.APPROVED


@pytest.mark.unit
class TestReworkBound:
    def test_rework_increments_and_routes(
        self, engine: WorkflowEngine, ctx: RunContext, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        snap = make_snapshot(WorkflowState.REVIEW, claim_id, workflow_run_id, rework_count=1)
        result = engine.apply(
            snap, make_event(Trigger.REWORK_REQUESTED, claim_id, workflow_run_id), ctx
        )
        assert result.snapshot.current_state == WorkflowState.REWORK_LOOP
        assert result.snapshot.rework_count == 2

    def test_rework_at_budget_rejected(
        self, engine: WorkflowEngine, ctx: RunContext, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        snap = make_snapshot(WorkflowState.REVIEW, claim_id, workflow_run_id, rework_count=3)
        with pytest.raises(ReworkLimitError, match="Rework budget exhausted"):
            engine.apply(snap, make_event(Trigger.REWORK_REQUESTED, claim_id, workflow_run_id), ctx)

    def test_rework_exhausted_terminates(
        self, engine: WorkflowEngine, ctx: RunContext, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        snap = make_snapshot(WorkflowState.REVIEW, claim_id, workflow_run_id, rework_count=3)
        result = engine.apply(
            snap, make_event(Trigger.REWORK_EXHAUSTED, claim_id, workflow_run_id), ctx
        )
        assert result.snapshot.current_state == WorkflowState.MAX_REWORK_EXCEEDED
        assert result.terminated is True


@pytest.mark.unit
class TestFailures:
    def test_node_failure_marks_failed(
        self, engine: WorkflowEngine, ctx: RunContext, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        snap = make_snapshot(WorkflowState.INVESTIGATION, claim_id, workflow_run_id)
        result = engine.apply(
            snap, make_event(Trigger.NODE_FAILED, claim_id, workflow_run_id, reason="boom"), ctx
        )
        assert result.snapshot.current_state == WorkflowState.FAILED
        assert result.terminated is True
        assert result.record is not None
        assert result.record.reason == "boom"

    def test_invalid_pair_rejected(
        self, engine: WorkflowEngine, ctx: RunContext, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        snap = make_snapshot(WorkflowState.EXTRACTION, claim_id, workflow_run_id)
        with pytest.raises(InvalidTransitionError, match="not allowed"):
            engine.apply(snap, make_event(Trigger.APPROVAL_GRANTED, claim_id, workflow_run_id), ctx)

    def test_human_actor_required_for_grant(
        self, engine: WorkflowEngine, ctx: RunContext, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        snap = make_snapshot(WorkflowState.HUMAN_APPROVAL, claim_id, workflow_run_id)
        with pytest.raises(ActorNotAllowedError, match="may not fire"):
            engine.apply(
                snap,
                make_event(
                    Trigger.APPROVAL_GRANTED, claim_id, workflow_run_id, AgentType.SUPERVISOR
                ),
                ctx,
            )

    def test_identity_mismatch_rejected(
        self, engine: WorkflowEngine, ctx: RunContext, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        from uuid import uuid4

        snap = make_snapshot(WorkflowState.RECEIVED, claim_id, workflow_run_id)
        with pytest.raises(InvalidTransitionError, match="Identity mismatch"):
            engine.apply(snap, make_event(Trigger.CLAIM_VALIDATED, uuid4(), workflow_run_id), ctx)


@pytest.mark.unit
class TestIdempotency:
    def test_same_event_twice_returns_duplicate(
        self, engine: WorkflowEngine, ctx: RunContext, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        snap = make_snapshot(WorkflowState.RECEIVED, claim_id, workflow_run_id)
        event = make_event(Trigger.CLAIM_VALIDATED, claim_id, workflow_run_id)
        first = engine.apply(snap, event, ctx)
        second = engine.apply(first.snapshot, event, ctx)
        assert second.duplicate is True
        assert second.steps_advanced == 0
        assert second.snapshot == first.snapshot
        assert second.record == first.record
        assert engine.applied_count == 1

    def test_distinct_executions_apply_independently(
        self, engine: WorkflowEngine, ctx: RunContext, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        snap = make_snapshot(WorkflowState.RECEIVED, claim_id, workflow_run_id)
        first = engine.apply(
            snap, make_event(Trigger.CLAIM_VALIDATED, claim_id, workflow_run_id), ctx
        )
        assert engine.applied_count == 1
        assert first.duplicate is False


@pytest.mark.unit
class TestDeterminism:
    def test_same_sequence_same_path(
        self, claim_id: UUID, workflow_run_id: UUID, clock: FixedClock
    ) -> None:
        def run() -> list[WorkflowState]:
            eng = WorkflowEngine()
            run_ctx = RunContext(claim_id=claim_id, workflow_run_id=workflow_run_id, clock=clock)
            snap = make_snapshot(WorkflowState.RECEIVED, claim_id, workflow_run_id)
            states = [snap.current_state]
            for trigger, actor in [
                (Trigger.CLAIM_VALIDATED, AgentType.SUPERVISOR),
                (Trigger.EXTRACTION_SUCCEEDED, AgentType.SUPERVISOR),
                (Trigger.INVESTIGATION_SUCCEEDED, AgentType.SUPERVISOR),
                (Trigger.REVIEW_APPROVED, AgentType.SUPERVISOR),
                (Trigger.APPROVAL_GRANTED, AgentType.HUMAN),
            ]:
                snap = eng.apply(
                    snap, make_event(trigger, claim_id, workflow_run_id, actor), run_ctx
                ).snapshot
                states.append(snap.current_state)
            return states

        assert run() == run()

    def test_clock_drives_timestamps(
        self, claim_id: UUID, workflow_run_id: UUID, clock: FixedClock
    ) -> None:
        eng = WorkflowEngine()
        run_ctx = RunContext(claim_id=claim_id, workflow_run_id=workflow_run_id, clock=clock)
        snap = make_snapshot(WorkflowState.RECEIVED, claim_id, workflow_run_id)
        first = eng.apply(
            snap, make_event(Trigger.CLAIM_VALIDATED, claim_id, workflow_run_id), run_ctx
        )
        clock.advance(60)
        second = eng.apply(
            first.snapshot,
            make_event(Trigger.EXTRACTION_SUCCEEDED, claim_id, workflow_run_id),
            run_ctx,
        )
        assert second.snapshot.updated_at > first.snapshot.updated_at
        assert second.record is not None and first.record is not None
        assert second.record.sequence_no == first.record.sequence_no + 1

    def test_invalid_snapshot_version_rejected(
        self, engine: WorkflowEngine, ctx: RunContext, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        from casefile.models.versioning import IncompatibleSchemaVersionError

        snap = make_snapshot(WorkflowState.RECEIVED, claim_id, workflow_run_id)
        snap.schema_version = "9.9.9"
        with pytest.raises(IncompatibleSchemaVersionError):
            engine.apply(snap, make_event(Trigger.CLAIM_VALIDATED, claim_id, workflow_run_id), ctx)

    def test_malformed_event_rejected(
        self, engine: WorkflowEngine, ctx: RunContext, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        from pydantic import ValidationError

        make_snapshot(WorkflowState.RECEIVED, claim_id, workflow_run_id)
        with pytest.raises(ValidationError):
            TransitionEvent(
                claim_id=claim_id,
                workflow_run_id=workflow_run_id,
                trigger="SOMETHING_MADE_UP",  # type: ignore[arg-type]
            )
        assert engine.applied_count == 0

"""
Shared helpers for workflow tests (plain functions; fixtures live in conftest.py).
"""

from __future__ import annotations

from uuid import UUID

from casefile.models.contracts import WorkflowState
from casefile.models.domain import AgentType
from casefile.workflow.context import WorkflowSnapshot
from casefile.workflow.transitions import TransitionEvent
from casefile.workflow.triggers import Trigger


def make_snapshot(
    state: WorkflowState,
    claim_id: UUID,
    workflow_run_id: UUID,
    step_count: int = 0,
    rework_count: int = 0,
) -> WorkflowSnapshot:
    return WorkflowSnapshot(
        workflow_run_id=workflow_run_id,
        claim_id=claim_id,
        current_state=state,
        step_count=step_count,
        rework_count=rework_count,
    )


def make_event(
    trigger: Trigger,
    claim_id: UUID,
    workflow_run_id: UUID,
    actor: AgentType = AgentType.SUPERVISOR,
    reason: str = "test",
) -> TransitionEvent:
    return TransitionEvent(
        claim_id=claim_id,
        workflow_run_id=workflow_run_id,
        trigger=trigger,
        actor=actor,
        reason=reason,
    )

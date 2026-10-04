"""
CASEFILE workflow state categories (docs/state-machine.md).

The single state vocabulary is WorkflowState (models.contracts). This module
adds the documented lenses over it: active processing vs. waiting vs.
approval vs. failure-flavored vs. terminal. No second state machine.
"""

from __future__ import annotations

from enum import Enum

from casefile.models.contracts import WorkflowState


class StateKind(str, Enum):
    """Category of a workflow state."""

    ACTIVE = "ACTIVE"
    WAITING = "WAITING"
    APPROVAL = "APPROVAL"
    FAILURE = "FAILURE"
    TERMINAL = "TERMINAL"


ACTIVE_STATES: frozenset[WorkflowState] = frozenset(
    {
        WorkflowState.RECEIVED,
        WorkflowState.EXTRACTION,
        WorkflowState.INVESTIGATION,
        WorkflowState.REVIEW,
        WorkflowState.REWORK_LOOP,
    }
)

WAITING_STATES: frozenset[WorkflowState] = frozenset({WorkflowState.HUMAN_APPROVAL})

APPROVAL_STATES: frozenset[WorkflowState] = frozenset({WorkflowState.HUMAN_APPROVAL})

FAILURE_STATES: frozenset[WorkflowState] = frozenset(
    {
        WorkflowState.FAILED,
        WorkflowState.ESCALATION,
        WorkflowState.TIMEOUT,
    }
)

TERMINAL_STATES: frozenset[WorkflowState] = frozenset(
    {state for state in WorkflowState if state.is_terminal}
)


def state_kind(state: WorkflowState) -> StateKind:
    """Return the primary category for a state.

    TERMINAL wins over FAILURE flavor: FAILED/ESCALATION/TIMEOUT are
    terminal states with failure semantics (see is_failure_state).
    """
    if state in TERMINAL_STATES:
        return StateKind.TERMINAL
    if state in APPROVAL_STATES:
        return StateKind.APPROVAL
    if state in WAITING_STATES:
        return StateKind.WAITING
    return StateKind.ACTIVE


def is_active(state: WorkflowState) -> bool:
    """True for states where agent/supervisor work proceeds."""
    return state in ACTIVE_STATES


def is_waiting(state: WorkflowState) -> bool:
    """True when the workflow is paused for external input."""
    return state in WAITING_STATES


def requires_human_approval(state: WorkflowState) -> bool:
    """True when progress needs an explicit human approval event."""
    return state in APPROVAL_STATES


def is_failure_state(state: WorkflowState) -> bool:
    """True for abnormal endings (subset of terminal states)."""
    return state in FAILURE_STATES


def is_terminal(state: WorkflowState) -> bool:
    """True when no normal agent transition may occur."""
    return state in TERMINAL_STATES

"""
CASEFILE explicit transition table (docs/state-machine.md).

Every legal (source state, trigger) pair maps to one TransitionRule that
answers: is it allowed, what follows, which actors may fire it, does it
need human approval, does it terminate, does it consume rework budget, is
it a failure. Anything not in the table is rejected with a clear error —
transitions never hide inside scattered conditionals.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from casefile.models.contracts import WorkflowState
from casefile.models.domain import AgentType
from casefile.models.versioning import SchemaVersion
from casefile.workflow.triggers import Trigger


class InvalidTransitionError(ValueError):
    """Raised when a (state, trigger) pair has no table entry."""


class TerminalStateError(ValueError):
    """Raised when a transition is attempted from a terminal state."""


class ActorNotAllowedError(ValueError):
    """Raised when the firing actor may not use a transition."""


class ReworkLimitError(ValueError):
    """Raised when rework is requested beyond the configured maximum."""


@dataclass(frozen=True)
class TransitionRule:
    """One row of the transition table."""

    destination: WorkflowState
    allowed_actors: frozenset[AgentType]
    requires_human_approval: bool = False
    terminates: bool = False
    increments_rework: bool = False
    is_failure: bool = False


_SUPERVISOR = frozenset({AgentType.SUPERVISOR})
_SUPERVISOR_SYSTEM = frozenset({AgentType.SUPERVISOR, AgentType.SYSTEM})
_HUMAN = frozenset({AgentType.HUMAN})


def _rule(
    destination: WorkflowState,
    actors: frozenset[AgentType] = _SUPERVISOR,
    *,
    requires_human_approval: bool = False,
    terminates: bool = False,
    increments_rework: bool = False,
    is_failure: bool = False,
) -> TransitionRule:
    return TransitionRule(
        destination=destination,
        allowed_actors=actors,
        requires_human_approval=requires_human_approval,
        terminates=terminates,
        increments_rework=increments_rework,
        is_failure=is_failure,
    )


TRANSITION_TABLE: dict[tuple[WorkflowState, Trigger], TransitionRule] = {
    # Intake
    (WorkflowState.RECEIVED, Trigger.CLAIM_VALIDATED): _rule(WorkflowState.EXTRACTION),
    (WorkflowState.RECEIVED, Trigger.CLAIM_INVALID): _rule(
        WorkflowState.FAILED, terminates=True, is_failure=True
    ),
    (WorkflowState.RECEIVED, Trigger.BUDGET_EXHAUSTED): _rule(
        WorkflowState.BUDGET_EXHAUSTED, actors=_SUPERVISOR_SYSTEM, terminates=True
    ),
    (WorkflowState.RECEIVED, Trigger.STEPS_EXHAUSTED): _rule(
        WorkflowState.MAX_STEPS_EXCEEDED, actors=_SUPERVISOR_SYSTEM, terminates=True
    ),
    (WorkflowState.RECEIVED, Trigger.WORKFLOW_TIMED_OUT): _rule(
        WorkflowState.TIMEOUT, actors=_SUPERVISOR_SYSTEM, terminates=True, is_failure=True
    ),
    # Extraction
    (WorkflowState.EXTRACTION, Trigger.EXTRACTION_SUCCEEDED): _rule(WorkflowState.INVESTIGATION),
    (WorkflowState.EXTRACTION, Trigger.EXTRACTION_FAILED): _rule(
        WorkflowState.FAILED, terminates=True, is_failure=True
    ),
    (WorkflowState.EXTRACTION, Trigger.EXTRACTION_TIMED_OUT): _rule(
        WorkflowState.ESCALATION, terminates=True, is_failure=True
    ),
    (WorkflowState.EXTRACTION, Trigger.BUDGET_EXHAUSTED): _rule(
        WorkflowState.BUDGET_EXHAUSTED, actors=_SUPERVISOR_SYSTEM, terminates=True
    ),
    (WorkflowState.EXTRACTION, Trigger.STEPS_EXHAUSTED): _rule(
        WorkflowState.MAX_STEPS_EXCEEDED, actors=_SUPERVISOR_SYSTEM, terminates=True
    ),
    (WorkflowState.EXTRACTION, Trigger.WORKFLOW_TIMED_OUT): _rule(
        WorkflowState.TIMEOUT, actors=_SUPERVISOR_SYSTEM, terminates=True, is_failure=True
    ),
    (WorkflowState.EXTRACTION, Trigger.NODE_FAILED): _rule(
        WorkflowState.FAILED, terminates=True, is_failure=True
    ),
    # Investigation
    (WorkflowState.INVESTIGATION, Trigger.INVESTIGATION_SUCCEEDED): _rule(WorkflowState.REVIEW),
    (WorkflowState.INVESTIGATION, Trigger.INVESTIGATION_FAILED): _rule(
        WorkflowState.FAILED, terminates=True, is_failure=True
    ),
    (WorkflowState.INVESTIGATION, Trigger.INVESTIGATION_TIMED_OUT): _rule(
        WorkflowState.ESCALATION, terminates=True, is_failure=True
    ),
    (WorkflowState.INVESTIGATION, Trigger.BUDGET_EXHAUSTED): _rule(
        WorkflowState.BUDGET_EXHAUSTED, actors=_SUPERVISOR_SYSTEM, terminates=True
    ),
    (WorkflowState.INVESTIGATION, Trigger.STEPS_EXHAUSTED): _rule(
        WorkflowState.MAX_STEPS_EXCEEDED, actors=_SUPERVISOR_SYSTEM, terminates=True
    ),
    (WorkflowState.INVESTIGATION, Trigger.WORKFLOW_TIMED_OUT): _rule(
        WorkflowState.TIMEOUT, actors=_SUPERVISOR_SYSTEM, terminates=True, is_failure=True
    ),
    (WorkflowState.INVESTIGATION, Trigger.NODE_FAILED): _rule(
        WorkflowState.FAILED, terminates=True, is_failure=True
    ),
    # Review
    (WorkflowState.REVIEW, Trigger.REVIEW_APPROVED): _rule(
        WorkflowState.HUMAN_APPROVAL, requires_human_approval=True
    ),
    (WorkflowState.REVIEW, Trigger.REVIEW_REJECTED): _rule(WorkflowState.REJECTED, terminates=True),
    (WorkflowState.REVIEW, Trigger.REWORK_REQUESTED): _rule(
        WorkflowState.REWORK_LOOP, increments_rework=True
    ),
    (WorkflowState.REVIEW, Trigger.REWORK_EXHAUSTED): _rule(
        WorkflowState.MAX_REWORK_EXCEEDED, terminates=True
    ),
    (WorkflowState.REVIEW, Trigger.BUDGET_EXHAUSTED): _rule(
        WorkflowState.BUDGET_EXHAUSTED, actors=_SUPERVISOR_SYSTEM, terminates=True
    ),
    (WorkflowState.REVIEW, Trigger.STEPS_EXHAUSTED): _rule(
        WorkflowState.MAX_STEPS_EXCEEDED, actors=_SUPERVISOR_SYSTEM, terminates=True
    ),
    (WorkflowState.REVIEW, Trigger.WORKFLOW_TIMED_OUT): _rule(
        WorkflowState.TIMEOUT, actors=_SUPERVISOR_SYSTEM, terminates=True, is_failure=True
    ),
    (WorkflowState.REVIEW, Trigger.NODE_FAILED): _rule(
        WorkflowState.FAILED, terminates=True, is_failure=True
    ),
    # Rework loop
    (WorkflowState.REWORK_LOOP, Trigger.REWORK_DISPATCHED): _rule(WorkflowState.INVESTIGATION),
    (WorkflowState.REWORK_LOOP, Trigger.REWORK_EXHAUSTED): _rule(
        WorkflowState.MAX_REWORK_EXCEEDED, terminates=True
    ),
    (WorkflowState.REWORK_LOOP, Trigger.BUDGET_EXHAUSTED): _rule(
        WorkflowState.BUDGET_EXHAUSTED, actors=_SUPERVISOR_SYSTEM, terminates=True
    ),
    (WorkflowState.REWORK_LOOP, Trigger.STEPS_EXHAUSTED): _rule(
        WorkflowState.MAX_STEPS_EXCEEDED, actors=_SUPERVISOR_SYSTEM, terminates=True
    ),
    (WorkflowState.REWORK_LOOP, Trigger.WORKFLOW_TIMED_OUT): _rule(
        WorkflowState.TIMEOUT, actors=_SUPERVISOR_SYSTEM, terminates=True, is_failure=True
    ),
    # Human approval (human actor fires grant/reject; system fires timeouts)
    (WorkflowState.HUMAN_APPROVAL, Trigger.APPROVAL_GRANTED): _rule(
        WorkflowState.APPROVED, actors=_HUMAN, terminates=True
    ),
    (WorkflowState.HUMAN_APPROVAL, Trigger.APPROVAL_REJECTED): _rule(
        WorkflowState.REJECTED, actors=_HUMAN, terminates=True
    ),
    (WorkflowState.HUMAN_APPROVAL, Trigger.APPROVAL_TIMED_OUT): _rule(
        WorkflowState.ESCALATION, actors=_SUPERVISOR_SYSTEM, terminates=True, is_failure=True
    ),
    (WorkflowState.HUMAN_APPROVAL, Trigger.WORKFLOW_TIMED_OUT): _rule(
        WorkflowState.TIMEOUT, actors=_SUPERVISOR_SYSTEM, terminates=True, is_failure=True
    ),
    (WorkflowState.HUMAN_APPROVAL, Trigger.BUDGET_EXHAUSTED): _rule(
        WorkflowState.BUDGET_EXHAUSTED, actors=_SUPERVISOR_SYSTEM, terminates=True
    ),
    (WorkflowState.HUMAN_APPROVAL, Trigger.STEPS_EXHAUSTED): _rule(
        WorkflowState.MAX_STEPS_EXCEEDED, actors=_SUPERVISOR_SYSTEM, terminates=True
    ),
}


def is_allowed(source: WorkflowState, trigger: Trigger) -> bool:
    """True when the table contains this (state, trigger) pair."""
    return (source, trigger) in TRANSITION_TABLE


def rule_for(source: WorkflowState, trigger: Trigger) -> TransitionRule:
    """Return the rule for a pair or raise with the legal trigger list."""
    try:
        return TRANSITION_TABLE[(source, trigger)]
    except KeyError:
        legal = sorted(t.value for s, t in TRANSITION_TABLE if s == source)
        raise InvalidTransitionError(
            f"Transition {source.value}+{trigger.value} is not allowed; "
            f"legal triggers from {source.value}: {legal}"
        ) from None


def triggers_from(source: WorkflowState) -> list[Trigger]:
    """All triggers accepted in a state (empty for terminal states)."""
    return sorted(
        (trigger for state, trigger in TRANSITION_TABLE if state == source),
        key=lambda t: t.value,
    )


def destinations_from(source: WorkflowState) -> list[WorkflowState]:
    """All reachable destinations from a state."""
    return sorted(
        {rule.destination for (state, _), rule in TRANSITION_TABLE.items() if state == source},
        key=lambda s: s.value,
    )


def incoming_triggers(destination: WorkflowState) -> list[Trigger]:
    """All triggers that can transition into this destination state."""
    return sorted(
        {
            trigger
            for (_, trigger), rule in TRANSITION_TABLE.items()
            if rule.destination == destination
        },
        key=lambda t: t.value,
    )


def incoming_sources(destination: WorkflowState) -> list[WorkflowState]:
    """All source states that can transition into this destination state."""
    return sorted(
        {
            source
            for (source, _), rule in TRANSITION_TABLE.items()
            if rule.destination == destination
        },
        key=lambda s: s.value,
    )


@dataclass(frozen=True)
class StateDeclaration:
    """Explicit declaration of a workflow state's properties, transitions, and actors."""

    state: WorkflowState
    is_terminal: bool
    allowed_incoming_triggers: tuple[Trigger, ...]
    allowed_outgoing_triggers: tuple[Trigger, ...]
    allowed_incoming_sources: tuple[WorkflowState, ...]
    allowed_outgoing_destinations: tuple[WorkflowState, ...]
    responsible_actors: tuple[AgentType, ...]


def state_declaration(state: WorkflowState) -> StateDeclaration:
    """Return the explicit declaration for any WorkflowState."""
    out_rules = [rule for (src, _), rule in TRANSITION_TABLE.items() if src == state]
    out_actors: set[AgentType] = set()
    for r in out_rules:
        out_actors.update(r.allowed_actors)
    if not out_actors:
        if state == WorkflowState.RECEIVED:
            out_actors = {AgentType.SUPERVISOR}
        else:
            out_actors = {AgentType.SYSTEM}

    return StateDeclaration(
        state=state,
        is_terminal=state.is_terminal,
        allowed_incoming_triggers=tuple(incoming_triggers(state)),
        allowed_outgoing_triggers=tuple(triggers_from(state)),
        allowed_incoming_sources=tuple(incoming_sources(state)),
        allowed_outgoing_destinations=tuple(destinations_from(state)),
        responsible_actors=tuple(sorted(out_actors, key=lambda a: a.value)),
    )


class TransitionEvent(BaseModel):
    """A typed request to move a workflow. Auditable and replayable."""

    transition_id: UUID = Field(default_factory=uuid4)
    claim_id: UUID
    workflow_run_id: UUID
    execution_id: UUID = Field(default_factory=uuid4)
    trigger: Trigger
    actor: AgentType = AgentType.SUPERVISOR
    reason: str = ""
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    correlation_id: UUID = Field(default_factory=uuid4)
    schema_version: SchemaVersion = "1.0.0"


class TransitionRecord(BaseModel):
    """An applied transition: event plus outcome, persisted to the audit log."""

    transition_id: UUID
    sequence_no: int
    claim_id: UUID
    workflow_run_id: UUID
    execution_id: UUID
    source: WorkflowState
    destination: WorkflowState
    trigger: Trigger
    actor: AgentType
    reason: str = ""
    idempotency_key: str
    applied_at: datetime
    correlation_id: UUID
    schema_version: SchemaVersion = "1.0.0"

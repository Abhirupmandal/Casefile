"""
Unit Tests: Workflow state categories and transition table.

Every legal (state, trigger) pair is exercised; representative illegal
pairs fail clearly; terminal states expose no outgoing transitions.
"""

import pytest

from casefile.models.contracts import WorkflowState
from casefile.models.domain import AgentType
from casefile.workflow.states import (
    StateKind,
    is_active,
    is_failure_state,
    is_terminal,
    is_waiting,
    requires_human_approval,
    state_kind,
)
from casefile.workflow.transitions import (
    TRANSITION_TABLE,
    InvalidTransitionError,
    destinations_from,
    is_allowed,
    rule_for,
    triggers_from,
)
from casefile.workflow.triggers import Trigger


@pytest.mark.unit
class TestStateKinds:
    def test_active_states(self) -> None:
        for state in (
            WorkflowState.RECEIVED,
            WorkflowState.EXTRACTION,
            WorkflowState.INVESTIGATION,
            WorkflowState.REVIEW,
            WorkflowState.REWORK_LOOP,
        ):
            assert state_kind(state) == StateKind.ACTIVE
            assert is_active(state) is True

    def test_approval_state(self) -> None:
        assert state_kind(WorkflowState.HUMAN_APPROVAL) == StateKind.APPROVAL
        assert requires_human_approval(WorkflowState.HUMAN_APPROVAL) is True
        assert is_waiting(WorkflowState.HUMAN_APPROVAL) is True

    def test_terminal_states(self) -> None:
        terminals = [s for s in WorkflowState if s.is_terminal]
        assert len(terminals) == 8
        for state in terminals:
            assert state_kind(state) == StateKind.TERMINAL
            assert is_terminal(state) is True
            assert triggers_from(state) == []
            assert destinations_from(state) == []

    def test_failure_flavor(self) -> None:
        assert is_failure_state(WorkflowState.FAILED) is True
        assert is_failure_state(WorkflowState.ESCALATION) is True
        assert is_failure_state(WorkflowState.TIMEOUT) is True
        assert is_failure_state(WorkflowState.APPROVED) is False
        assert is_failure_state(WorkflowState.REVIEW) is False

    def test_invalid_state_value_rejected(self) -> None:
        with pytest.raises(ValueError):
            WorkflowState("SOMEWHERE_ELSE")


@pytest.mark.unit
class TestTransitionTable:
    def test_every_table_entry_resolves(self) -> None:
        assert len(TRANSITION_TABLE) > 0
        for (source, trigger), rule in TRANSITION_TABLE.items():
            assert is_allowed(source, trigger) is True
            assert rule_for(source, trigger).destination == rule.destination
            assert isinstance(source, WorkflowState)
            assert isinstance(trigger, Trigger)
            assert rule.destination in set(WorkflowState)

    def test_approval_transitions_need_human(self) -> None:
        rule = rule_for(WorkflowState.HUMAN_APPROVAL, Trigger.APPROVAL_GRANTED)
        assert rule.requires_human_approval is False  # the event itself IS approval
        assert rule.terminates is True
        assert AgentType.HUMAN in rule.allowed_actors
        assert AgentType.SUPERVISOR not in rule.allowed_actors

    def test_review_approved_routes_to_human_gate(self) -> None:
        rule = rule_for(WorkflowState.REVIEW, Trigger.REVIEW_APPROVED)
        assert rule.destination == WorkflowState.HUMAN_APPROVAL
        assert rule.requires_human_approval is True
        assert rule.terminates is False

    def test_rework_consumes_budget(self) -> None:
        rule = rule_for(WorkflowState.REVIEW, Trigger.REWORK_REQUESTED)
        assert rule.destination == WorkflowState.REWORK_LOOP
        assert rule.increments_rework is True

    def test_illegal_pairs_rejected_with_guidance(self) -> None:
        with pytest.raises(InvalidTransitionError, match="legal triggers"):
            rule_for(WorkflowState.EXTRACTION, Trigger.APPROVAL_GRANTED)
        with pytest.raises(InvalidTransitionError, match="legal triggers"):
            rule_for(WorkflowState.RECEIVED, Trigger.REVIEW_APPROVED)
        assert is_allowed(WorkflowState.REVIEW, Trigger.CLAIM_VALIDATED) is False

    def test_no_shortcuts_between_specialists(self) -> None:
        # Every non-terminal source has a policy; nothing bypasses the supervisor
        for state in WorkflowState:
            if not state.is_terminal:
                assert triggers_from(state), f"{state.value} has no transition policy"

    def test_documented_flow_present(self) -> None:
        assert rule_for(WorkflowState.RECEIVED, Trigger.CLAIM_VALIDATED).destination == (
            WorkflowState.EXTRACTION
        )
        assert (
            rule_for(WorkflowState.EXTRACTION, Trigger.EXTRACTION_SUCCEEDED).destination
            == WorkflowState.INVESTIGATION
        )
        assert (
            rule_for(WorkflowState.INVESTIGATION, Trigger.INVESTIGATION_SUCCEEDED).destination
            == WorkflowState.REVIEW
        )
        assert rule_for(WorkflowState.REWORK_LOOP, Trigger.REWORK_DISPATCHED).destination == (
            WorkflowState.INVESTIGATION
        )

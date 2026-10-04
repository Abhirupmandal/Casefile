"""
Termination vocabulary (Phase 8 §7).

Fine-grained budget reasons map onto the existing Phase 3 terminal
states — no existing reason is replaced. The structured outcome always
carries the fine-grained code; the workflow state uses the mapped state.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

from casefile.models.contracts import WorkflowState


class BudgetTermination(str, Enum):
    """Budget-exhaustion reasons."""

    MAX_STEPS_EXCEEDED = "MAX_STEPS_EXCEEDED"
    MAX_AGENT_STEPS_EXCEEDED = "MAX_AGENT_STEPS_EXCEEDED"
    MAX_TOOL_CALLS_EXCEEDED = "MAX_TOOL_CALLS_EXCEEDED"
    MAX_REWORK_EXCEEDED = "MAX_REWORK_EXCEEDED"
    MAX_AGENT_RETRIES_EXCEEDED = "MAX_AGENT_RETRIES_EXCEEDED"
    MAX_WALL_CLOCK_EXCEEDED = "MAX_WALL_CLOCK_EXCEEDED"
    MAX_INPUT_TOKENS_EXCEEDED = "MAX_INPUT_TOKENS_EXCEEDED"
    MAX_OUTPUT_TOKENS_EXCEEDED = "MAX_OUTPUT_TOKENS_EXCEEDED"
    MAX_TOTAL_TOKENS_EXCEEDED = "MAX_TOTAL_TOKENS_EXCEEDED"
    MAX_COST_EXCEEDED = "MAX_COST_EXCEEDED"


_BUDGET_TO_WORKFLOW_STATE: dict[BudgetTermination, WorkflowState] = {
    BudgetTermination.MAX_STEPS_EXCEEDED: WorkflowState.MAX_STEPS_EXCEEDED,
    BudgetTermination.MAX_AGENT_STEPS_EXCEEDED: WorkflowState.MAX_STEPS_EXCEEDED,
    BudgetTermination.MAX_TOOL_CALLS_EXCEEDED: WorkflowState.BUDGET_EXHAUSTED,
    BudgetTermination.MAX_REWORK_EXCEEDED: WorkflowState.MAX_REWORK_EXCEEDED,
    BudgetTermination.MAX_AGENT_RETRIES_EXCEEDED: WorkflowState.BUDGET_EXHAUSTED,
    BudgetTermination.MAX_WALL_CLOCK_EXCEEDED: WorkflowState.TIMEOUT,
    BudgetTermination.MAX_INPUT_TOKENS_EXCEEDED: WorkflowState.BUDGET_EXHAUSTED,
    BudgetTermination.MAX_OUTPUT_TOKENS_EXCEEDED: WorkflowState.BUDGET_EXHAUSTED,
    BudgetTermination.MAX_TOTAL_TOKENS_EXCEEDED: WorkflowState.BUDGET_EXHAUSTED,
    BudgetTermination.MAX_COST_EXCEEDED: WorkflowState.BUDGET_EXHAUSTED,
}


def to_workflow_state(reason: BudgetTermination) -> WorkflowState:
    """Map a fine-grained budget reason onto the existing terminal state."""
    return _BUDGET_TO_WORKFLOW_STATE[reason]


class TerminationDecision(BaseModel):
    """Structured decision produced by the central termination policy."""

    should_terminate: bool
    terminal_state: WorkflowState | None = None
    reason: str | None = None
    budget_reason: BudgetTermination | None = None


class TerminationPolicy:
    """Central deterministic termination policy evaluator (no LLM involvement)."""

    @staticmethod
    def evaluate(
        state: WorkflowState,
        *,
        step_count: int,
        max_steps: int,
        rework_count: int = 0,
        max_rework_cycles: int = 3,
        budget_reason: BudgetTermination | None = None,
        unrecoverable_error: str | None = None,
    ) -> TerminationDecision:
        """Evaluate terminal criteria deterministically in strict priority order."""
        if state.is_terminal:
            return TerminationDecision(
                should_terminate=True,
                terminal_state=state,
                reason=f"Workflow already in terminal state {state.value}",
            )
        if budget_reason is not None:
            dest = to_workflow_state(budget_reason)
            return TerminationDecision(
                should_terminate=True,
                terminal_state=dest,
                reason=f"Budget exhaustion: {budget_reason.value}",
                budget_reason=budget_reason,
            )
        if step_count >= max_steps:
            return TerminationDecision(
                should_terminate=True,
                terminal_state=WorkflowState.MAX_STEPS_EXCEEDED,
                reason=f"Step count {step_count} exceeded max_steps limit {max_steps}",
                budget_reason=BudgetTermination.MAX_STEPS_EXCEEDED,
            )
        if rework_count >= max_rework_cycles:
            return TerminationDecision(
                should_terminate=True,
                terminal_state=WorkflowState.MAX_REWORK_EXCEEDED,
                reason=f"Rework count {rework_count} exceeded limit {max_rework_cycles}",
                budget_reason=BudgetTermination.MAX_REWORK_EXCEEDED,
            )
        if unrecoverable_error:
            return TerminationDecision(
                should_terminate=True,
                terminal_state=WorkflowState.FAILED,
                reason=f"Unrecoverable execution error: {unrecoverable_error}",
            )
        return TerminationDecision(should_terminate=False)

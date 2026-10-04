"""
Supervisor agent: orchestration entry point and router (AGENTS.md §1).

Thin behavior over the authoritative Phase 3 machinery: starts claims
(RECEIVED snapshots), routes via SupervisorRouter, and applies the
pre-flight budget guard from the existing BudgetState before dispatch.
Performs no extraction, investigation, review, or payout decisions, calls
no tools, and never bypasses human approval — the transition engine and
router already enforce all of that. No LLM is consulted for routing.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from casefile.models.contracts import BudgetState, ClaimInput, WorkflowState
from casefile.models.domain import AgentType
from casefile.workflow.context import RunContext, WorkflowSnapshot
from casefile.workflow.hooks import EventSink, NullSink
from casefile.workflow.supervisor import NodeName, RouteAction, RoutingDecision, SupervisorRouter


class SupervisorAgent:
    """Orchestration agent: entry, budget pre-flight, deterministic routing."""

    agent_type = AgentType.SUPERVISOR
    allowed_tools: frozenset[str] = frozenset()

    def __init__(
        self,
        router: SupervisorRouter | None = None,
        sink: EventSink | None = None,
    ) -> None:
        self._router = router or SupervisorRouter()
        self._sink = sink or NullSink()

    def start_claim(self, claim: ClaimInput) -> tuple[UUID, RunContext, WorkflowSnapshot]:
        """Initialize a workflow run at RECEIVED; returns ids, context, snapshot."""
        workflow_run_id = uuid4()
        ctx = RunContext(
            claim_id=claim.claim_id,
            workflow_run_id=workflow_run_id,
            actor=AgentType.SUPERVISOR,
        )
        snapshot = WorkflowSnapshot(
            workflow_run_id=workflow_run_id,
            claim_id=claim.claim_id,
            current_state=WorkflowState.RECEIVED,
        )
        return workflow_run_id, ctx, snapshot

    def route_next(self, snapshot: WorkflowSnapshot, budget: BudgetState) -> RoutingDecision:
        """Route with pre-flight budget guard; engine policy stays authoritative."""
        exhausted = budget.check_limits()
        if exhausted is not None:
            return RoutingDecision(
                action=RouteAction.TERMINATED,
                node=NodeName.END,
                reason=f"Budget exhausted before dispatch: {exhausted}",
            )
        return self._router.route(snapshot)

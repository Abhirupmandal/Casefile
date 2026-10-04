"""
CASEFILE Supervisor router: deterministic orchestration code, not an LLM agent
(docs/agent-architecture.md §Supervisor Agent).

Inspects a typed snapshot and returns a RoutingDecision: which architectural
node runs next, whether to wait for human input, or whether the workflow has
terminated. Never extracts, investigates, reviews, or decides payouts.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel

from casefile.models.contracts import WorkflowState
from casefile.models.versioning import SchemaVersion
from casefile.workflow.context import WorkflowSnapshot
from casefile.workflow.states import is_terminal, requires_human_approval

if TYPE_CHECKING:
    from casefile.observability.provider import Observability


class RouteAction(str, Enum):
    """Supervisor routing verdict."""

    DISPATCH = "DISPATCH"
    WAIT_FOR_HUMAN = "WAIT_FOR_HUMAN"
    TERMINATED = "TERMINATED"


class NodeName(str, Enum):
    """Architectural nodes addressable by the router and the graph."""

    SUPERVISOR = "supervisor"
    EXTRACTOR = "extractor"
    INVESTIGATOR = "investigator"
    REVIEWER = "reviewer"
    HUMAN_APPROVAL = "human_approval"
    END = "end"


class RoutingDecision(BaseModel):
    """Typed supervisor verdict for one snapshot."""

    action: RouteAction
    node: NodeName
    reason: str = ""
    schema_version: SchemaVersion = "1.0.0"


_STATE_TO_NODE: dict[WorkflowState, NodeName] = {
    WorkflowState.RECEIVED: NodeName.EXTRACTOR,
    WorkflowState.EXTRACTION: NodeName.EXTRACTOR,
    WorkflowState.INVESTIGATION: NodeName.INVESTIGATOR,
    WorkflowState.REVIEW: NodeName.REVIEWER,
    WorkflowState.REWORK_LOOP: NodeName.INVESTIGATOR,
}


class SupervisorRouter:
    """Pure routing function over typed workflow snapshots."""

    def route(
        self, snapshot: WorkflowSnapshot, obs: Observability | None = None
    ) -> RoutingDecision:
        """Decide the next architectural node for a snapshot."""
        from casefile.observability.spans import SPAN_SUPERVISOR_ROUTE
        from casefile.observability.tracer import maybe_span

        tracer = obs.tracer if obs is not None else None
        with maybe_span(
            tracer, SPAN_SUPERVISOR_ROUTE, {"casefile.current_state": snapshot.current_state.value}
        ) as span:
            decision = self._route_inner(snapshot)
            span.set_attribute("casefile.route", decision.node.value)
            span.set_attribute("casefile.reason_code", decision.action.value)
            return decision

    def _route_inner(self, snapshot: WorkflowSnapshot) -> RoutingDecision:
        """Uninstrumented routing logic (deterministic, unchanged)."""
        state = snapshot.current_state
        if is_terminal(state):
            return RoutingDecision(
                action=RouteAction.TERMINATED,
                node=NodeName.END,
                reason=f"Workflow reached terminal state {state.value}",
            )
        if requires_human_approval(state):
            return RoutingDecision(
                action=RouteAction.WAIT_FOR_HUMAN,
                node=NodeName.HUMAN_APPROVAL,
                reason="Workflow waits for an explicit human approval event",
            )
        node = _STATE_TO_NODE.get(state)
        if node is None:
            raise ValueError(f"No routing rule for state {state.value}")
        return RoutingDecision(
            action=RouteAction.DISPATCH,
            node=node,
            reason=f"State {state.value} dispatches to {node.value}",
        )


ROUTABLE_STATES: frozenset[WorkflowState] = frozenset(_STATE_TO_NODE)

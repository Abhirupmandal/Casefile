"""
CASEFILE agent node boundaries (Phase 3 §8, §21).

Every node has the same typed shape: typed graph state in, typed
NodeOutcome out. Nodes never mutate global state, never call LLMs, and
never fabricate agent intelligence. Specialist nodes assert their required
typed input is present and DEFER to the Phase 4 agents; the supervisor node
consults the deterministic router; the human-approval node parks the
workflow in WAITING until an explicit approval event arrives.
"""

from __future__ import annotations

from enum import Enum
from typing import TypedDict
from uuid import UUID

from pydantic import BaseModel

from casefile.models.contracts import (
    ClaimInput,
    ExtractionResult,
    InvestigationResult,
    ReviewResult,
    WorkflowState,
)
from casefile.models.versioning import SchemaVersion
from casefile.workflow.context import WorkflowSnapshot
from casefile.workflow.supervisor import NodeName, SupervisorRouter


class NodeStatus(str, Enum):
    """Lifecycle of a placeholder node execution."""

    DEFERRED = "DEFERRED"
    WAITING = "WAITING"
    DONE = "DONE"


class GraphState(TypedDict, total=False):
    """Strongly typed LangGraph state. Explicit fields only — no Any payloads."""

    workflow_run_id: UUID
    claim_id: UUID
    current_state: WorkflowState
    step_count: int
    rework_count: int
    last_node: NodeName
    last_outcome: NodeStatus
    last_detail: str
    correlation_id: UUID
    terminal: bool
    next_node: NodeName
    claim_input: ClaimInput
    extraction_result: ExtractionResult
    investigation_result: InvestigationResult
    review_result: ReviewResult


class NodeOutcome(BaseModel):
    """Typed boundary result of one node execution."""

    node: NodeName
    status: NodeStatus
    detail: str = ""
    schema_version: SchemaVersion = "1.0.0"


def _require(state: GraphState, field: str) -> None:
    if field not in state:
        raise ValueError(f"Node input missing required field {field!r}")


def supervisor_node(state: GraphState, router: SupervisorRouter | None = None) -> dict[str, object]:
    """Route via the deterministic supervisor; never synthesizes results."""
    _require(state, "current_state")
    active_router = router or SupervisorRouter()
    decision = active_router.route(
        WorkflowSnapshot(
            workflow_run_id=state["workflow_run_id"],
            claim_id=state["claim_id"],
            current_state=state["current_state"],
            step_count=state.get("step_count", 0),
            rework_count=state.get("rework_count", 0),
        )
    )
    outcome = NodeOutcome(node=NodeName.SUPERVISOR, status=NodeStatus.DONE, detail=decision.reason)
    return {
        "last_node": NodeName.SUPERVISOR,
        "last_outcome": outcome.status,
        "last_detail": outcome.detail,
        "terminal": decision.node == NodeName.END,
        "next_node": decision.node,
    }


def _deferred_node(state: GraphState, node: NodeName, needs: str) -> dict[str, object]:
    _require(state, "current_state")
    outcome = NodeOutcome(
        node=node,
        status=NodeStatus.DEFERRED,
        detail=f"{node.value} deferred to Phase 4 agent; required input present: {needs}",
    )
    return {
        "last_node": node,
        "last_outcome": outcome.status,
        "last_detail": outcome.detail,
    }


def extractor_node(state: GraphState) -> dict[str, object]:
    """Boundary stub: validates input presence, defers intelligence to Phase 4."""
    return _deferred_node(state, NodeName.EXTRACTOR, "claim documents")


def investigator_node(state: GraphState) -> dict[str, object]:
    """Boundary stub: validates input presence, defers intelligence to Phase 4."""
    return _deferred_node(state, NodeName.INVESTIGATOR, "extraction result")


def reviewer_node(state: GraphState) -> dict[str, object]:
    """Boundary stub: validates input presence, defers intelligence to Phase 4."""
    return _deferred_node(state, NodeName.REVIEWER, "investigation result")


def human_approval_node(state: GraphState) -> dict[str, object]:
    """Parks the workflow: waits for an explicit human approval event.

    Never infers approval, never triggers payout.
    """
    _require(state, "current_state")
    outcome = NodeOutcome(
        node=NodeName.HUMAN_APPROVAL,
        status=NodeStatus.WAITING,
        detail="Waiting for explicit human approval event; no automatic continuation",
    )
    return {
        "last_node": NodeName.HUMAN_APPROVAL,
        "last_outcome": outcome.status,
        "last_detail": outcome.detail,
        "terminal": False,
    }

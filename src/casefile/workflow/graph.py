"""
CASEFILE LangGraph orchestration graph (Phase 3 §5).

Topology mirrors the architecture: every specialist node returns to the
supervisor, which routes deterministically. Human approval parks the run
(no automatic continuation). Placeholder nodes carry no intelligence —
Phase 4 agents plug into the same node boundaries.
"""

from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from casefile.workflow.nodes import (
    GraphState,
    extractor_node,
    human_approval_node,
    investigator_node,
    reviewer_node,
    supervisor_node,
)
from casefile.workflow.supervisor import NodeName, SupervisorRouter

NODE_TO_TARGET: dict[str, str] = {
    NodeName.EXTRACTOR.value: NodeName.EXTRACTOR.value,
    NodeName.INVESTIGATOR.value: NodeName.INVESTIGATOR.value,
    NodeName.REVIEWER.value: NodeName.REVIEWER.value,
    NodeName.HUMAN_APPROVAL.value: NodeName.HUMAN_APPROVAL.value,
    NodeName.END.value: END,
}


def route_from_supervisor(state: GraphState) -> str:
    """Conditional edge: follow the supervisor's typed routing decision."""
    next_node = state.get("next_node", NodeName.END)
    value = next_node.value if isinstance(next_node, NodeName) else str(next_node)
    return NODE_TO_TARGET.get(value, END)


def build_graph() -> Any:
    """Build and compile the CASEFILE orchestration graph."""
    builder: StateGraph = StateGraph(GraphState)
    builder.add_node(NodeName.SUPERVISOR.value, supervisor_node)
    builder.add_node(NodeName.EXTRACTOR.value, extractor_node)
    builder.add_node(NodeName.INVESTIGATOR.value, investigator_node)
    builder.add_node(NodeName.REVIEWER.value, reviewer_node)
    builder.add_node(NodeName.HUMAN_APPROVAL.value, human_approval_node)
    builder.set_entry_point(NodeName.SUPERVISOR.value)
    builder.add_conditional_edges(NodeName.SUPERVISOR.value, route_from_supervisor)
    for node in (
        NodeName.EXTRACTOR.value,
        NodeName.INVESTIGATOR.value,
        NodeName.REVIEWER.value,
    ):
        builder.add_edge(node, NodeName.SUPERVISOR.value)
    builder.add_edge(NodeName.HUMAN_APPROVAL.value, END)
    return builder.compile()


GRAPH_NODES: tuple[str, ...] = (
    NodeName.SUPERVISOR.value,
    NodeName.EXTRACTOR.value,
    NodeName.INVESTIGATOR.value,
    NodeName.REVIEWER.value,
    NodeName.HUMAN_APPROVAL.value,
)


def build_agent_graph(
    extractor: Any,
    investigator: Any,
    reviewer: Any,
    *,
    engine: Any = None,
    router: SupervisorRouter | None = None,
    max_rework_cycles: int = 3,
) -> Any:
    """Build and compile an agent-backed CASEFILE orchestration graph.

    Executes the multi-agent workflow deterministically via LangGraph.
    """
    from casefile.agents.base import AgentFailedError
    from casefile.agents.context import AgentContext
    from casefile.models.contracts import (
        ExtractionRequest,
        ExtractionResult,
        InvestigationRequest,
        InvestigationResult,
        ReviewRequest,
        ReviewResult,
        WorkflowState,
    )
    from casefile.models.domain import AgentType
    from casefile.models.envelope import ContractEnvelope
    from casefile.workflow.context import RunContext, WorkflowSnapshot
    from casefile.workflow.engine import WorkflowEngine
    from casefile.workflow.transitions import TransitionEvent
    from casefile.workflow.triggers import Trigger

    active_engine: WorkflowEngine = engine or WorkflowEngine()
    active_router: SupervisorRouter = router or SupervisorRouter()

    def _agent_supervisor_node(state: GraphState) -> dict[str, object]:
        snap = WorkflowSnapshot(
            workflow_run_id=state["workflow_run_id"],
            claim_id=state["claim_id"],
            current_state=state["current_state"],
            step_count=state.get("step_count", 0),
            rework_count=state.get("rework_count", 0),
        )
        decision = active_router.route(snap)
        return {
            "last_node": NodeName.SUPERVISOR,
            "terminal": decision.node == NodeName.END,
            "next_node": decision.node,
        }

    def _agent_extractor_node(state: GraphState) -> dict[str, object]:
        claim = state.get("claim_input")
        if not claim:
            raise ValueError("extractor_node requires claim_input in state")
        req = ExtractionRequest(workflow_id=state["workflow_run_id"], claim_input=claim)
        env = ContractEnvelope.wrap(
            "extraction_request",
            req,
            claim_id=state["claim_id"],
            workflow_run_id=state["workflow_run_id"],
        )
        unwrapped = env.unwrap(ExtractionRequest)
        ctx = AgentContext(
            workflow_run_id=state["workflow_run_id"],
            claim_id=state["claim_id"],
            correlation_id=state["correlation_id"],
            agent=AgentType.EXTRACTOR,
        )

        snap = WorkflowSnapshot(
            workflow_run_id=state["workflow_run_id"],
            claim_id=state["claim_id"],
            current_state=state["current_state"],
            step_count=state.get("step_count", 0),
            rework_count=state.get("rework_count", 0),
        )
        run_ctx = RunContext(
            claim_id=state["claim_id"],
            workflow_run_id=state["workflow_run_id"],
            correlation_id=state["correlation_id"],
        )

        if snap.current_state == WorkflowState.RECEIVED:
            init_event = TransitionEvent(
                claim_id=state["claim_id"],
                workflow_run_id=state["workflow_run_id"],
                trigger=Trigger.CLAIM_VALIDATED,
                actor=AgentType.SUPERVISOR,
                correlation_id=state["correlation_id"],
                reason="Claim intake validated",
            )
            apply_init = active_engine.apply(snap, init_event, run_ctx)
            snap = apply_init.snapshot

        try:
            result, _ = extractor.run(unwrapped, ctx)
            result_env = ContractEnvelope.wrap(
                "extraction_result",
                result,
                claim_id=state["claim_id"],
                workflow_run_id=state["workflow_run_id"],
            )
            validated = result_env.unwrap(ExtractionResult)

            event = TransitionEvent(
                claim_id=state["claim_id"],
                workflow_run_id=state["workflow_run_id"],
                trigger=Trigger.EXTRACTION_SUCCEEDED,
                correlation_id=state["correlation_id"],
            )
            apply_res = active_engine.apply(snap, event, run_ctx)
            return {
                "last_node": NodeName.EXTRACTOR,
                "current_state": apply_res.snapshot.current_state,
                "step_count": apply_res.snapshot.step_count,
                "extraction_result": validated,
                "terminal": False,
            }
        except AgentFailedError:
            event = TransitionEvent(
                claim_id=state["claim_id"],
                workflow_run_id=state["workflow_run_id"],
                trigger=Trigger.EXTRACTION_FAILED,
                correlation_id=state["correlation_id"],
            )
            apply_res = active_engine.apply(snap, event, run_ctx)
            return {
                "last_node": NodeName.EXTRACTOR,
                "current_state": apply_res.snapshot.current_state,
                "step_count": apply_res.snapshot.step_count,
                "terminal": True,
            }

    def _agent_investigator_node(state: GraphState) -> dict[str, object]:
        extraction = state.get("extraction_result")
        if not extraction:
            raise ValueError("investigator_node requires extraction_result in state")
        rework_feedback = None
        rev = state.get("review_result")
        if rev and getattr(rev, "decision", None) == "REWORK":
            rework_feedback = getattr(rev, "rework_feedback", None)

        req = InvestigationRequest(
            workflow_id=state["workflow_run_id"],
            extraction_result=extraction,
            rework_feedback=rework_feedback,
        )
        env = ContractEnvelope.wrap(
            "investigation_request",
            req,
            claim_id=state["claim_id"],
            workflow_run_id=state["workflow_run_id"],
        )
        unwrapped = env.unwrap(InvestigationRequest)
        ctx = AgentContext(
            workflow_run_id=state["workflow_run_id"],
            claim_id=state["claim_id"],
            correlation_id=state["correlation_id"],
            agent=AgentType.INVESTIGATOR,
        )

        snap = WorkflowSnapshot(
            workflow_run_id=state["workflow_run_id"],
            claim_id=state["claim_id"],
            current_state=state["current_state"],
            step_count=state.get("step_count", 0),
            rework_count=state.get("rework_count", 0),
        )
        run_ctx = RunContext(
            claim_id=state["claim_id"],
            workflow_run_id=state["workflow_run_id"],
            correlation_id=state["correlation_id"],
        )

        try:
            result, _ = investigator.run(unwrapped, ctx)
            result_env = ContractEnvelope.wrap(
                "investigation_result",
                result,
                claim_id=state["claim_id"],
                workflow_run_id=state["workflow_run_id"],
            )
            validated = result_env.unwrap(InvestigationResult)

            event = TransitionEvent(
                claim_id=state["claim_id"],
                workflow_run_id=state["workflow_run_id"],
                trigger=Trigger.INVESTIGATION_SUCCEEDED,
                correlation_id=state["correlation_id"],
            )
            apply_res = active_engine.apply(snap, event, run_ctx)
            return {
                "last_node": NodeName.INVESTIGATOR,
                "current_state": apply_res.snapshot.current_state,
                "step_count": apply_res.snapshot.step_count,
                "investigation_result": validated,
                "terminal": False,
            }
        except AgentFailedError:
            event = TransitionEvent(
                claim_id=state["claim_id"],
                workflow_run_id=state["workflow_run_id"],
                trigger=Trigger.INVESTIGATION_FAILED,
                correlation_id=state["correlation_id"],
            )
            apply_res = active_engine.apply(snap, event, run_ctx)
            return {
                "last_node": NodeName.INVESTIGATOR,
                "current_state": apply_res.snapshot.current_state,
                "step_count": apply_res.snapshot.step_count,
                "terminal": True,
            }

    def _agent_reviewer_node(state: GraphState) -> dict[str, object]:
        extraction = state.get("extraction_result")
        investigation = state.get("investigation_result")
        if not extraction or not investigation:
            raise ValueError(
                "reviewer_node requires extraction_result and investigation_result in state"
            )
        req = ReviewRequest(
            workflow_id=state["workflow_run_id"],
            extraction_result=extraction,
            investigation_result=investigation,
            rework_count=state.get("rework_count", 0),
        )
        env = ContractEnvelope.wrap(
            "review_request",
            req,
            claim_id=state["claim_id"],
            workflow_run_id=state["workflow_run_id"],
        )
        unwrapped = env.unwrap(ReviewRequest)
        ctx = AgentContext(
            workflow_run_id=state["workflow_run_id"],
            claim_id=state["claim_id"],
            correlation_id=state["correlation_id"],
            agent=AgentType.REVIEWER,
        )

        snap = WorkflowSnapshot(
            workflow_run_id=state["workflow_run_id"],
            claim_id=state["claim_id"],
            current_state=state["current_state"],
            step_count=state.get("step_count", 0),
            rework_count=state.get("rework_count", 0),
        )
        run_ctx = RunContext(
            claim_id=state["claim_id"],
            workflow_run_id=state["workflow_run_id"],
            correlation_id=state["correlation_id"],
            max_rework_cycles=max_rework_cycles,
        )

        try:
            result, _ = reviewer.run(unwrapped, ctx)
            result_env = ContractEnvelope.wrap(
                "review_result",
                result,
                claim_id=state["claim_id"],
                workflow_run_id=state["workflow_run_id"],
            )
            validated = result_env.unwrap(ReviewResult)

            if result.decision == "APPROVE":
                event = TransitionEvent(
                    claim_id=state["claim_id"],
                    workflow_run_id=state["workflow_run_id"],
                    trigger=Trigger.REVIEW_APPROVED,
                    correlation_id=state["correlation_id"],
                )
                apply_res = active_engine.apply(snap, event, run_ctx)
                return {
                    "last_node": NodeName.REVIEWER,
                    "current_state": apply_res.snapshot.current_state,
                    "step_count": apply_res.snapshot.step_count,
                    "review_result": validated,
                    "terminal": False,
                }
            elif result.decision == "REWORK":
                if snap.rework_count >= max_rework_cycles:
                    event = TransitionEvent(
                        claim_id=state["claim_id"],
                        workflow_run_id=state["workflow_run_id"],
                        trigger=Trigger.REWORK_EXHAUSTED,
                        correlation_id=state["correlation_id"],
                    )
                    apply_res = active_engine.apply(snap, event, run_ctx)
                    return {
                        "last_node": NodeName.REVIEWER,
                        "current_state": apply_res.snapshot.current_state,
                        "step_count": apply_res.snapshot.step_count,
                        "review_result": validated,
                        "terminal": True,
                    }
                event_rw = TransitionEvent(
                    claim_id=state["claim_id"],
                    workflow_run_id=state["workflow_run_id"],
                    trigger=Trigger.REWORK_REQUESTED,
                    correlation_id=state["correlation_id"],
                )
                apply_rw = active_engine.apply(snap, event_rw, run_ctx)
                event_disp = TransitionEvent(
                    claim_id=state["claim_id"],
                    workflow_run_id=state["workflow_run_id"],
                    trigger=Trigger.REWORK_DISPATCHED,
                    correlation_id=state["correlation_id"],
                )
                apply_disp = active_engine.apply(apply_rw.snapshot, event_disp, run_ctx)
                return {
                    "last_node": NodeName.REVIEWER,
                    "current_state": apply_disp.snapshot.current_state,
                    "step_count": apply_disp.snapshot.step_count,
                    "rework_count": apply_disp.snapshot.rework_count,
                    "review_result": validated,
                    "terminal": False,
                }
            elif result.decision == "REJECT":
                event = TransitionEvent(
                    claim_id=state["claim_id"],
                    workflow_run_id=state["workflow_run_id"],
                    trigger=Trigger.REVIEW_REJECTED,
                    correlation_id=state["correlation_id"],
                )
                apply_res = active_engine.apply(snap, event, run_ctx)
                return {
                    "last_node": NodeName.REVIEWER,
                    "current_state": apply_res.snapshot.current_state,
                    "step_count": apply_res.snapshot.step_count,
                    "review_result": validated,
                    "terminal": True,
                }
            else:
                raise ValueError(f"Unknown reviewer decision {result.decision}")
        except AgentFailedError:
            event = TransitionEvent(
                claim_id=state["claim_id"],
                workflow_run_id=state["workflow_run_id"],
                trigger=Trigger.NODE_FAILED,
                correlation_id=state["correlation_id"],
            )
            apply_res = active_engine.apply(snap, event, run_ctx)
            return {
                "last_node": NodeName.REVIEWER,
                "current_state": apply_res.snapshot.current_state,
                "step_count": apply_res.snapshot.step_count,
                "terminal": True,
            }

    builder: StateGraph = StateGraph(GraphState)
    builder.add_node(NodeName.SUPERVISOR.value, _agent_supervisor_node)
    builder.add_node(NodeName.EXTRACTOR.value, _agent_extractor_node)
    builder.add_node(NodeName.INVESTIGATOR.value, _agent_investigator_node)
    builder.add_node(NodeName.REVIEWER.value, _agent_reviewer_node)
    builder.add_node(NodeName.HUMAN_APPROVAL.value, human_approval_node)
    builder.set_entry_point(NodeName.SUPERVISOR.value)
    builder.add_conditional_edges(NodeName.SUPERVISOR.value, route_from_supervisor)
    for node in (
        NodeName.EXTRACTOR.value,
        NodeName.INVESTIGATOR.value,
        NodeName.REVIEWER.value,
    ):
        builder.add_edge(node, NodeName.SUPERVISOR.value)
    builder.add_edge(NodeName.HUMAN_APPROVAL.value, END)
    return builder.compile()

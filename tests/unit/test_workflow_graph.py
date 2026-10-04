"""
Unit Tests: Supervisor router, placeholder nodes, and LangGraph construction.

The router is pure and deterministic; nodes defer intelligence (no fake AI);
the compiled graph mirrors the architecture topology.
"""

from uuid import UUID

import pytest

from casefile.models.contracts import WorkflowState
from casefile.workflow.graph import GRAPH_NODES, build_graph, route_from_supervisor
from casefile.workflow.nodes import (
    GraphState,
    NodeStatus,
    extractor_node,
    human_approval_node,
    investigator_node,
    reviewer_node,
    supervisor_node,
)
from casefile.workflow.supervisor import (
    NodeName,
    RouteAction,
    SupervisorRouter,
)
from tests.unit.workflow_helpers import make_snapshot


@pytest.mark.unit
class TestSupervisorRouting:
    def test_dispatch_map(self, claim_id: UUID, workflow_run_id: UUID) -> None:
        router = SupervisorRouter()
        expectations = {
            WorkflowState.RECEIVED: NodeName.EXTRACTOR,
            WorkflowState.EXTRACTION: NodeName.EXTRACTOR,
            WorkflowState.INVESTIGATION: NodeName.INVESTIGATOR,
            WorkflowState.REVIEW: NodeName.REVIEWER,
            WorkflowState.REWORK_LOOP: NodeName.INVESTIGATOR,
        }
        for state, node in expectations.items():
            decision = router.route(make_snapshot(state, claim_id, workflow_run_id))
            assert decision.action == RouteAction.DISPATCH
            assert decision.node == node

    def test_wait_for_human(self, claim_id: UUID, workflow_run_id: UUID) -> None:
        decision = SupervisorRouter().route(
            make_snapshot(WorkflowState.HUMAN_APPROVAL, claim_id, workflow_run_id)
        )
        assert decision.action == RouteAction.WAIT_FOR_HUMAN
        assert decision.node == NodeName.HUMAN_APPROVAL

    def test_terminal_routes_to_end(self, claim_id: UUID, workflow_run_id: UUID) -> None:
        for state in [s for s in WorkflowState if s.is_terminal]:
            decision = SupervisorRouter().route(make_snapshot(state, claim_id, workflow_run_id))
            assert decision.action == RouteAction.TERMINATED
            assert decision.node == NodeName.END


@pytest.mark.unit
class TestPlaceholderNodes:
    def _state(self, claim_id: UUID, workflow_run_id: UUID) -> GraphState:
        return {
            "workflow_run_id": workflow_run_id,
            "claim_id": claim_id,
            "current_state": WorkflowState.EXTRACTION,
            "step_count": 1,
            "rework_count": 0,
        }

    def test_specialists_defer_without_fake_results(
        self, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        state = self._state(claim_id, workflow_run_id)
        for node_fn, _name in [
            (extractor_node, "extractor"),
            (investigator_node, "investigator"),
            (reviewer_node, "reviewer"),
        ]:
            outcome = node_fn(state)
            assert outcome["last_outcome"] == NodeStatus.DEFERRED
            detail = str(outcome["last_detail"])
            assert "deferred" in detail.lower()
            assert "Phase 4" in detail
            for banned in ("analyzed successfully", "AI ", "intelligence", "confidence"):
                assert banned not in detail

    def test_human_approval_parks(self, claim_id: UUID, workflow_run_id: UUID) -> None:
        outcome = human_approval_node(self._state(claim_id, workflow_run_id))
        assert outcome["last_outcome"] == NodeStatus.WAITING
        assert outcome["terminal"] is False
        assert "explicit human approval" in str(outcome["last_detail"])

    def test_nodes_reject_missing_input(self, claim_id: UUID, workflow_run_id: UUID) -> None:
        with pytest.raises(ValueError, match="required field"):
            extractor_node({"claim_id": claim_id, "workflow_run_id": workflow_run_id})

    def test_supervisor_node_routes(self, claim_id: UUID, workflow_run_id: UUID) -> None:
        outcome = supervisor_node(self._state(claim_id, workflow_run_id))
        assert outcome["last_node"] == NodeName.SUPERVISOR
        assert outcome["next_node"] == NodeName.EXTRACTOR
        assert outcome["terminal"] is False


@pytest.mark.unit
class TestGraphConstruction:
    def test_all_architectural_nodes_present(self) -> None:
        assert set(GRAPH_NODES) == {
            "supervisor",
            "extractor",
            "investigator",
            "reviewer",
            "human_approval",
        }
        graph = build_graph()
        assert set(graph.nodes) >= set(GRAPH_NODES)

    def test_route_mapping(self, claim_id: UUID, workflow_run_id: UUID) -> None:
        from langgraph.graph import END

        assert route_from_supervisor({"next_node": NodeName.EXTRACTOR}) == "extractor"
        assert route_from_supervisor({"next_node": NodeName.END}) == END
        assert route_from_supervisor({}) == END

    def test_compiled_graph_terminates_on_terminal_state(
        self, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        # Cyclic supervisor↔agent edges are bounded by the engine in production;
        # the graph itself terminates immediately once the snapshot is terminal.
        graph = build_graph()
        result = graph.invoke(
            {
                "workflow_run_id": workflow_run_id,
                "claim_id": claim_id,
                "current_state": WorkflowState.APPROVED,
                "step_count": 5,
                "rework_count": 0,
            }
        )
        assert result["terminal"] is True
        assert result["next_node"] == NodeName.END

    def test_compiled_graph_parks_at_human_approval(
        self, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        graph = build_graph()
        result = graph.invoke(
            {
                "workflow_run_id": workflow_run_id,
                "claim_id": claim_id,
                "current_state": WorkflowState.HUMAN_APPROVAL,
                "step_count": 4,
                "rework_count": 0,
            }
        )
        assert result["last_node"] == NodeName.HUMAN_APPROVAL
        assert result["last_outcome"] == NodeStatus.WAITING

    def test_no_llm_imports_in_workflow_package(self) -> None:
        from pathlib import Path

        package = Path(__file__).resolve().parents[2] / "src" / "casefile" / "workflow"
        hits = []
        for path in sorted(package.glob("*.py")):
            text = path.read_text(encoding="utf-8").lower()
            for token in ("openai", "anthropic", "gemini", "langchain", "llm."):
                if token in text:
                    hits.append(f"{path.name}:{token}")
        assert hits == []

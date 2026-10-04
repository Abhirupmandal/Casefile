"""
Unit Tests: Tool registry — registration, versioning, authorization,
typed execution, idempotency, timeouts, hooks, and usage tracking.
"""

from uuid import uuid4

import pytest

from casefile.models.domain import AgentType
from casefile.tools import create_default_registry
from casefile.tools.contracts import ToolContext, ToolErrorCode, ToolStatus
from casefile.tools.document import DocumentRetrievalTool
from casefile.tools.registry import AUTHORIZED_TOOLS, ToolRegistry
from casefile.workflow.hooks import RecordingSink, WorkflowHookEvent


def _ctx(agent: AgentType = AgentType.INVESTIGATOR) -> ToolContext:
    return ToolContext(claim_id=uuid4(), workflow_run_id=uuid4(), agent=agent)


@pytest.mark.unit
class TestRegistryBasics:
    def test_default_registry_lists_six_tools(self) -> None:
        registry = create_default_registry()
        assert registry.list_tools() == [
            "claim_history_lookup",
            "document_retrieval",
            "evidence_lookup",
            "fraud_signal_lookup",
            "policy_lookup",
            "repair_cost_lookup",
        ]

    def test_duplicate_registration_rejected(self) -> None:
        registry = create_default_registry()
        with pytest.raises(ValueError, match="already registered"):
            registry.register(DocumentRetrievalTool())

    def test_metadata_exposes_version(self) -> None:
        registry = create_default_registry()
        meta = registry.metadata("policy_lookup")
        assert meta["version"] == "1.0.0"
        assert meta["name"] == "policy_lookup"

    def test_version_mismatch_rejected(self) -> None:
        registry = create_default_registry()
        outcome = registry.execute(
            "policy_lookup", "9.9.9", {"policy_number": "POL-SYN-001"}, _ctx()
        )
        assert outcome.status == ToolStatus.FAILURE
        assert outcome.error is not None
        assert outcome.error.code == ToolErrorCode.VERSION_MISMATCH

    def test_unknown_tool_rejected(self) -> None:
        registry = create_default_registry()
        outcome = registry.execute("telepathy", "1.0.0", {}, _ctx())
        assert outcome.status == ToolStatus.FAILURE
        assert outcome.error is not None
        assert outcome.error.code == ToolErrorCode.UNKNOWN_TOOL
        assert outcome.output is None


@pytest.mark.unit
class TestAuthorization:
    def test_matrix_matches_architecture(self) -> None:
        assert AUTHORIZED_TOOLS[AgentType.SUPERVISOR] == frozenset()
        assert AUTHORIZED_TOOLS[AgentType.EXTRACTOR] == frozenset({"document_retrieval"})
        assert AUTHORIZED_TOOLS[AgentType.REVIEWER] == frozenset(
            {"document_retrieval", "evidence_lookup", "policy_lookup", "repair_cost_lookup"}
        )
        assert "fraud_signal_lookup" not in AUTHORIZED_TOOLS[AgentType.REVIEWER]
        assert len(AUTHORIZED_TOOLS[AgentType.INVESTIGATOR]) == 6
        assert AUTHORIZED_TOOLS[AgentType.HUMAN] == frozenset()

    def test_unauthorized_denied_before_execution(self) -> None:
        sink = RecordingSink()
        registry = create_default_registry(sink=sink)
        outcome = registry.execute(
            "policy_lookup",
            "1.0.0",
            {"policy_number": "POL-SYN-001"},
            _ctx(AgentType.EXTRACTOR),
        )
        assert outcome.status == ToolStatus.DENIED
        assert outcome.error is not None
        assert outcome.error.code == ToolErrorCode.UNAUTHORIZED_TOOL
        events = sink.events()
        assert WorkflowHookEvent.TOOL_AUTHORIZATION_DENIED in events
        assert WorkflowHookEvent.TOOL_STARTED not in events

    def test_unknown_agent_gets_nothing(self) -> None:
        registry = create_default_registry()
        assert registry.tools_for(AgentType.SUPERVISOR) == []
        assert registry.is_authorized(AgentType.SUPERVISOR, "policy_lookup") is False

    def test_tools_for_agent(self) -> None:
        registry = create_default_registry()
        assert registry.tools_for(AgentType.EXTRACTOR) == ["document_retrieval"]
        assert "fraud_signal_lookup" in registry.tools_for(AgentType.INVESTIGATOR)


@pytest.mark.unit
class TestExecutionSemantics:
    def test_success_outcome_shape(self) -> None:
        registry = create_default_registry()
        ctx = _ctx()
        outcome = registry.execute("policy_lookup", "1.0.0", {"policy_number": "POL-SYN-001"}, ctx)
        assert outcome.status == ToolStatus.SUCCESS
        assert outcome.succeeded is True
        assert outcome.output is not None
        assert outcome.call is not None
        assert outcome.duration_ms >= 0
        assert outcome.idempotency_key != ""
        assert ctx.usage.calls_made == 1

    def test_invalid_input_is_typed_error(self) -> None:
        registry = create_default_registry()
        outcome = registry.execute("policy_lookup", "1.0.0", {"policy_number": "   "}, _ctx())
        assert outcome.status == ToolStatus.FAILURE
        assert outcome.error is not None
        assert outcome.error.code == ToolErrorCode.INVALID_INPUT
        assert outcome.output is None

    def test_same_request_same_idempotency_key(self) -> None:
        registry = create_default_registry()
        ctx = _ctx()
        first = registry.execute("policy_lookup", "1.0.0", {"policy_number": "POL-SYN-001"}, ctx)
        second = registry.execute("policy_lookup", "1.0.0", {"policy_number": "POL-SYN-001"}, ctx)
        assert first.idempotency_key == second.idempotency_key
        assert first.output == second.output

    def test_hook_sequence_on_success(self) -> None:
        sink = RecordingSink()
        registry = create_default_registry(sink=sink)
        registry.execute("policy_lookup", "1.0.0", {"policy_number": "POL-SYN-001"}, _ctx())
        assert sink.events() == [
            WorkflowHookEvent.TOOL_REQUESTED,
            WorkflowHookEvent.TOOL_STARTED,
            WorkflowHookEvent.TOOL_COMPLETED,
        ]

    def test_timeout_enforced(self) -> None:
        import time

        from casefile.models.domain import AgentType as AgentT
        from casefile.tools.policy import PolicyLookupInput, PolicyLookupOutput
        from casefile.tools.registry import Tool

        class SlowTool(Tool[PolicyLookupInput, PolicyLookupOutput]):
            name = "slow_probe"
            version = "1.0.0"
            description = "test-only slow tool"
            timeout_seconds = 5.0
            input_model = PolicyLookupInput
            output_model = PolicyLookupOutput

            def run(self, tool_input: PolicyLookupInput, ctx: ToolContext) -> PolicyLookupOutput:
                time.sleep(0.3)
                return PolicyLookupOutput(found=False)

        class OpenRegistry(ToolRegistry):
            """White-box: bypass matrix to exercise the timeout path itself."""

            def is_authorized(self, agent: AgentT, tool_name: str) -> bool:
                return True

        registry = OpenRegistry()
        registry.register(SlowTool())
        ctx = _ctx()
        ctx.timeout_seconds = 0.05
        outcome = registry.execute("slow_probe", "1.0.0", {"policy_number": "X"}, ctx)
        assert outcome.status == ToolStatus.TIMEOUT
        assert outcome.error is not None
        assert outcome.error.code == ToolErrorCode.TIMEOUT
        assert outcome.output is None

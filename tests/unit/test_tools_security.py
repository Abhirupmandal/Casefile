"""
Unit Tests: Tool-layer security boundaries (Phase 5 §21 A–L).

Unknown/unauthorized tools, malformed input, scope violations, path
traversal, arbitrary SQL, oversized retrieval, result confinement
(no state/budget/approval/permission/tool-chain effects), and untrusted
content handling.
"""

import pytest

from casefile.models.domain import AgentType
from casefile.tools.contracts import ToolErrorCode, ToolStatus
from tests.unit.test_tools_contracts import _run


@pytest.mark.unit
class TestToolSecurity:
    def test_a_unknown_tool_rejected(self) -> None:
        outcome = _run("nope_tool", {})
        assert outcome.error is not None
        assert outcome.error.code == ToolErrorCode.UNKNOWN_TOOL

    def test_b_unauthorized_agent_rejected(self) -> None:
        outcome = _run("policy_lookup", {"policy_number": "POL-SYN-001"}, AgentType.SUPERVISOR)
        assert outcome.status == ToolStatus.DENIED
        assert outcome.error is not None
        assert outcome.error.code == ToolErrorCode.UNAUTHORIZED_TOOL

    def test_c_malformed_input_rejected(self) -> None:
        outcome = _run("claim_history_lookup", {"customer_id": "", "limit": 9999})
        assert outcome.status == ToolStatus.FAILURE
        assert outcome.error is not None
        assert outcome.error.code == ToolErrorCode.INVALID_INPUT

    def test_d_claim_scope_violation(self) -> None:
        outcome = _run(
            "document_retrieval",
            {"document_ref": "SYN-DOC-N1", "claim_ref": "WRONG-CLAIM"},
            AgentType.EXTRACTOR,
        )
        assert outcome.error is not None
        assert outcome.error.code == ToolErrorCode.SCOPE_VIOLATION

    def test_e_path_traversal_rejected(self) -> None:
        for evil in ("../secret", "..\\secret", "/etc/passwd", "a/b", "", "x" * 65):
            outcome = _run(
                "document_retrieval",
                {"document_ref": evil, "claim_ref": "SYN-NORMAL-001"},
                AgentType.EXTRACTOR,
            )
            assert outcome.status == ToolStatus.FAILURE, evil
            assert outcome.error is not None
            assert outcome.error.code == ToolErrorCode.INVALID_INPUT

    def test_f_no_arbitrary_sql_surface(self) -> None:
        import inspect

        from casefile.tools import fixtures as fixtures_module
        from casefile.tools import registry as registry_module

        for module in (registry_module, fixtures_module):
            source = inspect.getsource(module)
            for token in ("sqlite3", "SELECT ", "INSERT ", "UPDATE ", "DELETE ", "sessionmaker"):
                assert token not in source, f"{module.__name__}:{token}"

    def test_g_oversized_retrieval_rejected(self) -> None:
        outcome = _run("claim_history_lookup", {"customer_id": "CUST-SYN-HIST", "limit": 51})
        assert outcome.status == ToolStatus.FAILURE
        assert outcome.error is not None
        assert outcome.error.code == ToolErrorCode.INVALID_INPUT

    def test_h_result_cannot_modify_workflow_state(self) -> None:
        outcome = _run(
            "document_retrieval",
            {"document_ref": "SYN-DOC-N1", "claim_ref": "SYN-NORMAL-001"},
            AgentType.EXTRACTOR,
        )
        assert outcome.succeeded is True
        dumped = outcome.output.model_dump() if outcome.output else {}
        assert "current_state" not in dumped
        assert "terminal_state" not in dumped

    def test_i_result_cannot_grant_approval(self) -> None:
        outcome = _run("policy_lookup", {"policy_number": "POL-SYN-001"})
        dumped = outcome.output.model_dump() if outcome.output else {}
        for key in dumped:
            assert "approv" not in key.lower()
            assert "decision" not in key.lower()
            assert "payout" not in key.lower()

    def test_j_result_cannot_modify_permissions(self) -> None:
        from casefile.tools.registry import AUTHORIZED_TOOLS

        before = {agent: set(tools) for agent, tools in AUTHORIZED_TOOLS.items()}
        _run("evidence_lookup", {"claim_ref": "SYN-NORMAL-001"})
        after = {agent: set(tools) for agent, tools in AUTHORIZED_TOOLS.items()}
        assert before == after

    def test_k_result_cannot_execute_another_tool(self) -> None:
        outcome = _run("evidence_lookup", {"claim_ref": "SYN-NORMAL-001"})
        assert outcome.succeeded is True
        dumped = outcome.output.model_dump_json() if outcome.output else ""
        assert "policy_lookup(" not in dumped
        assert "registry.execute" not in dumped

    def test_l_untrusted_instructions_stay_untrusted(self) -> None:
        outcome = _run(
            "document_retrieval",
            {"document_ref": "SYN-DOC-F1", "claim_ref": "SYN-FRAUD-001"},
            AgentType.EXTRACTOR,
        )
        assert outcome.succeeded is True
        assert outcome.output is not None
        content = outcome.output.content_untrusted  # type: ignore[attr-defined]
        assert "Ignore previous instructions" in content
        assert outcome.output.document is not None  # type: ignore[attr-defined]
        assert outcome.output.document.trusted_source is False  # type: ignore[attr-defined]

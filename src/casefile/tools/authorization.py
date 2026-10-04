"""
Tool authorization matrix and permission verification (Phase 3).

Enforces the principle of least privilege:
- Supervisor: No domain tools (orchestration only)
- Extractor: document_retrieval only
- Investigator: full evidence gathering (policy, history, damage, fraud, documents, evidence)
- Reviewer: retrieval and verification (policy, repair cost, documents, evidence); fraud tools excluded
- Human / System: no direct internal tool invocation
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from casefile.models.domain import AgentType
from casefile.tools.errors import ToolAuthorizationError

if TYPE_CHECKING:
    pass

AUTHORIZED_TOOLS: dict[AgentType, frozenset[str]] = {
    AgentType.SUPERVISOR: frozenset(),
    AgentType.EXTRACTOR: frozenset({"document_retrieval"}),
    AgentType.INVESTIGATOR: frozenset(
        {
            "document_retrieval",
            "evidence_lookup",
            "claim_history_lookup",
            "policy_lookup",
            "repair_cost_lookup",
            "fraud_signal_lookup",
        }
    ),
    AgentType.REVIEWER: frozenset(
        {
            "document_retrieval",
            "evidence_lookup",
            "policy_lookup",
            "repair_cost_lookup",
        }
    ),
    AgentType.HUMAN: frozenset(),
    AgentType.SYSTEM: frozenset(),
}


def is_authorized(agent: AgentType, tool_name: str) -> bool:
    """Check whether the agent is authorized to invoke the specified tool."""
    return tool_name in AUTHORIZED_TOOLS.get(agent, frozenset())


def check_authorization(agent: AgentType, tool_name: str) -> None:
    """Validate that the agent is permitted to execute the tool; raise on denial."""
    if not is_authorized(agent, tool_name):
        raise ToolAuthorizationError(
            f"Agent {agent.value!r} is not authorized to invoke tool {tool_name!r}"
        )

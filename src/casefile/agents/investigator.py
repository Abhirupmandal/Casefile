"""
Investigator agent: extracted facts in, typed InvestigationResult out.

Gathers evidence per the AGENTS.md authorization matrix (declared as names;
Phase 5 implements the tools) and synthesizes findings. Never approves,
denies, decides payout, bypasses Reviewer, or mutates workflow state.
"""

from __future__ import annotations

from collections.abc import Callable

from casefile.agents.base import AgentFailedError, BaseAgent, lookup_tool
from casefile.agents.context import AgentContext
from casefile.agents.prompts import (
    PromptTemplate,
    RenderedPrompt,
    get_prompt,
    render,
)
from casefile.agents.providers import LLMProvider
from casefile.agents.results import AgentError, AgentErrorCategory
from casefile.models.contracts import InvestigationRequest, InvestigationResult
from casefile.models.domain import AgentType
from casefile.tools.contracts import ToolOutcome
from casefile.tools.registry import ToolRegistry
from casefile.workflow.hooks import EventSink

ALLOWED_INVESTIGATOR_TOOLS: frozenset[str] = frozenset(
    {
        "policy_lookup",
        "claim_history_lookup",
        "repair_cost_lookup",
        "fraud_signal_lookup",
        "document_retrieval",
    }
)


class InvestigatorAgent(BaseAgent[InvestigationRequest, InvestigationResult]):
    """Specialized evidence-gathering component behind the typed boundary."""

    agent_type = AgentType.INVESTIGATOR
    output_contract_type = "investigation_result"
    allowed_tools: frozenset[str] = ALLOWED_INVESTIGATOR_TOOLS

    def __init__(
        self,
        provider: LLMProvider,
        *,
        model: str = "gpt-4o",
        temperature: float = 0.3,
        sink: EventSink | None = None,
        prompt: PromptTemplate | None = None,
        retry_guard: Callable[[int], bool] | None = None,
    ) -> None:
        super().__init__(
            provider,
            prompt or get_prompt("investigator"),
            model=model,
            temperature=temperature,
            sink=sink,
            retry_guard=retry_guard,
        )

    def validate_input(self, payload: InvestigationRequest, ctx: AgentContext) -> None:
        """Require an extraction result to investigate; typed fields validated."""
        if not payload.extraction_result.incident_description.strip():
            raise AgentFailedError(
                AgentError(
                    category=AgentErrorCategory.INVALID_INPUT,
                    code="EMPTY_EXTRACTION",
                    message="InvestigationRequest carries no incident description",
                    retryable=False,
                )
            )

    def build_render(
        self, payload: InvestigationRequest, ctx: AgentContext
    ) -> tuple[RenderedPrompt, dict[str, object]]:
        """Extraction facts are workflow data (trusted channel); rework notes
        ride the trusted channel as reviewer-authored workflow context."""
        rework = (
            f"Rework feedback:\n{payload.rework_feedback}\n\n" if payload.rework_feedback else ""
        )
        rendered = render(
            self._prompt,
            variables={
                "extraction": payload.extraction_result.model_dump_json(indent=1),
                "evidence": "(tool evidence arrives via Phase 5 tool calls)",
                "rework": rework,
            },
        )
        return rendered, {}

    def lookup(
        self,
        tool_name: str,
        raw_input: dict[str, object],
        *,
        agent_ctx: AgentContext,
        registry: ToolRegistry,
    ) -> ToolOutcome:
        """Request one authorized tool through the registry (typed outcome)."""
        allowed = self.allowed_tools | (
            frozenset({"evidence_lookup"})
            if registry.is_authorized(self.agent_type, "evidence_lookup")
            else frozenset()
        )
        return lookup_tool(registry, allowed, tool_name, raw_input, agent_ctx, self.agent_type)

    def parse_output(
        self,
        raw: str,
        payload: InvestigationRequest,
        ctx: AgentContext,
        render_meta: dict[str, object],
    ) -> InvestigationResult:
        """Parse, validate, and consistency-check the investigation contract."""
        data = self.parse_json_object(raw, contract="InvestigationResult")
        result = self.validate_contract(InvestigationResult, data, contract="InvestigationResult")
        if result.workflow_id != payload.workflow_id:
            raise AgentFailedError(
                AgentError(
                    category=AgentErrorCategory.CONTRACT_VALIDATION,
                    code="WORKFLOW_MISMATCH",
                    message="InvestigationResult workflow_id does not match the request",
                    retryable=False,
                )
            )
        if not result.findings_summary.strip():
            raise AgentFailedError(
                AgentError(
                    category=AgentErrorCategory.CONTRACT_VALIDATION,
                    code="EMPTY_FINDINGS",
                    message="InvestigationResult requires a findings summary",
                    retryable=False,
                )
            )
        if result.tools_succeeded + result.tools_failed != len(result.tool_calls_made):
            raise AgentFailedError(
                AgentError(
                    category=AgentErrorCategory.CONTRACT_VALIDATION,
                    code="TOOL_COUNT_MISMATCH",
                    message="tools_succeeded + tools_failed must equal tool_calls_made entries",
                    retryable=False,
                )
            )
        return result

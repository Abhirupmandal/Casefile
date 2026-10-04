"""
Reviewer agent: extraction + investigation findings in, typed ReviewResult out.

Evaluates evidence completeness and quality, identifies gaps, and
recommends APPROVE, REJECT, or REWORK with structured reasoning. A
recommendation is never a payout action; human approval stays mandatory.
Structural consistency rules (REWORK needs feedback; APPROVE contradicts
detected fraud or known-incomplete extraction) are explicit domain logic,
separate from provider reasoning.
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
from casefile.models.contracts import ReviewRequest, ReviewResult
from casefile.models.domain import AgentType
from casefile.tools.contracts import ToolOutcome
from casefile.tools.registry import ToolRegistry
from casefile.workflow.hooks import EventSink

ALLOWED_REVIEWER_TOOLS: frozenset[str] = frozenset(
    {"document_retrieval", "evidence_lookup", "policy_lookup", "repair_cost_lookup"}
)


class ReviewerAgent(BaseAgent[ReviewRequest, ReviewResult]):
    """Specialized review component behind the typed boundary."""

    agent_type = AgentType.REVIEWER
    output_contract_type = "review_result"
    allowed_tools: frozenset[str] = ALLOWED_REVIEWER_TOOLS

    def __init__(
        self,
        provider: LLMProvider,
        *,
        model: str = "claude-3-5-sonnet-20241022",
        temperature: float = 0.0,
        sink: EventSink | None = None,
        prompt: PromptTemplate | None = None,
        retry_guard: Callable[[int], bool] | None = None,
    ) -> None:
        super().__init__(
            provider,
            prompt or get_prompt("reviewer"),
            model=model,
            temperature=temperature,
            sink=sink,
            retry_guard=retry_guard,
        )

    def validate_input(self, payload: ReviewRequest, ctx: AgentContext) -> None:
        """Require both upstream findings; typed fields already validated."""
        if not payload.investigation_result.findings_summary.strip():
            raise AgentFailedError(
                AgentError(
                    category=AgentErrorCategory.INVALID_INPUT,
                    code="EMPTY_INVESTIGATION",
                    message="ReviewRequest carries no investigation findings",
                    retryable=False,
                )
            )

    def build_render(
        self, payload: ReviewRequest, ctx: AgentContext
    ) -> tuple[RenderedPrompt, dict[str, object]]:
        """Upstream typed findings travel the trusted channel."""
        rendered = render(
            self._prompt,
            variables={
                "extraction": payload.extraction_result.model_dump_json(indent=1),
                "investigation": payload.investigation_result.model_dump_json(indent=1),
                "rework_count": str(payload.rework_count),
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
        return lookup_tool(
            registry, self.allowed_tools, tool_name, raw_input, agent_ctx, self.agent_type
        )

    def parse_output(
        self, raw: str, payload: ReviewRequest, ctx: AgentContext, render_meta: dict[str, object]
    ) -> ReviewResult:
        """Parse, validate, and consistency-check the review contract."""
        data = self.parse_json_object(raw, contract="ReviewResult")
        result = self.validate_contract(ReviewResult, data, contract="ReviewResult")
        if result.workflow_id != payload.workflow_id:
            raise AgentFailedError(
                AgentError(
                    category=AgentErrorCategory.CONTRACT_VALIDATION,
                    code="WORKFLOW_MISMATCH",
                    message="ReviewResult workflow_id does not match the request",
                    retryable=False,
                )
            )
        if result.decision == "REWORK" and not (result.rework_feedback or "").strip():
            raise AgentFailedError(
                AgentError(
                    category=AgentErrorCategory.CONTRACT_VALIDATION,
                    code="REWORK_WITHOUT_FEEDBACK",
                    message="REWORK decisions require rework_feedback for the investigator",
                    retryable=False,
                )
            )
        if result.decision == "APPROVE" and result.fraud_signals_detected:
            raise AgentFailedError(
                AgentError(
                    category=AgentErrorCategory.POLICY_VIOLATION,
                    code="APPROVE_WITH_FRAUD",
                    message="APPROVE contradicts detected fraud signals",
                    retryable=False,
                )
            )
        if result.decision == "APPROVE" and not payload.extraction_result.is_complete:
            raise AgentFailedError(
                AgentError(
                    category=AgentErrorCategory.POLICY_VIOLATION,
                    code="APPROVE_WITH_GAPS",
                    message="APPROVE contradicts incomplete extraction",
                    retryable=False,
                )
            )
        return result

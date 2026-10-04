"""
Extractor agent: claim-document references in, typed ExtractionResult out.

Consumes document references as UNTRUSTED content (quarantined in the user
section). Never approves, denies, authorizes payout, or investigates.
Allowed tools: none.
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
from casefile.models.contracts import ExtractionRequest, ExtractionResult
from casefile.models.domain import AgentType
from casefile.tools.document import DocumentRetrievalOutput
from casefile.tools.registry import ToolRegistry
from casefile.workflow.hooks import EventSink


class ExtractorAgent(BaseAgent[ExtractionRequest, ExtractionResult]):
    """Specialized extraction component behind the typed boundary."""

    agent_type = AgentType.EXTRACTOR
    output_contract_type = "extraction_result"
    allowed_tools: frozenset[str] = frozenset({"document_retrieval"})

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
            prompt or get_prompt("extractor"),
            model=model,
            temperature=temperature,
            sink=sink,
            retry_guard=retry_guard,
        )

    def validate_input(self, payload: ExtractionRequest, ctx: AgentContext) -> None:
        """Reject empty document lists; typed fields already validated."""
        if not payload.claim_input.documents:
            raise AgentFailedError(
                AgentError(
                    category=AgentErrorCategory.INVALID_INPUT,
                    code="NO_DOCUMENTS",
                    message="ExtractionRequest carries no document references",
                    retryable=False,
                )
            )

    def build_render(
        self, payload: ExtractionRequest, ctx: AgentContext
    ) -> tuple[RenderedPrompt, dict[str, object]]:
        """Trusted pointer in the task; document refs quarantined as untrusted."""
        documents = {f"document-{i}": ref for i, ref in enumerate(payload.claim_input.documents)}
        rendered = render(
            self._prompt,
            variables={"documents": "(see UNTRUSTED section below)"},
            untrusted=documents,
        )
        return rendered, {}

    def fetch_documents(
        self,
        document_refs: list[str],
        claim_ref: str,
        *,
        agent_ctx: AgentContext,
        registry: ToolRegistry,
    ) -> list[DocumentRetrievalOutput]:
        """Retrieve documents through the registry (typed outputs only)."""
        outputs: list[DocumentRetrievalOutput] = []
        for ref in document_refs:
            outcome = lookup_tool(
                registry,
                self.allowed_tools,
                "document_retrieval",
                {"document_ref": ref, "claim_ref": claim_ref},
                agent_ctx,
                self.agent_type,
            )
            if not outcome.succeeded or not isinstance(outcome.output, DocumentRetrievalOutput):
                raise AgentFailedError(
                    AgentError(
                        category=AgentErrorCategory.UNEXPECTED,
                        code="DOCUMENT_FETCH_FAILED",
                        message=f"Document {ref!r} could not be retrieved",
                        retryable=False,
                    )
                )
            outputs.append(outcome.output)
        return outputs

    def parse_output(
        self,
        raw: str,
        payload: ExtractionRequest,
        ctx: AgentContext,
        render_meta: dict[str, object],
    ) -> ExtractionResult:
        """Parse, validate, and consistency-check the extraction contract."""
        data = self.parse_json_object(raw, contract="ExtractionResult")
        result = self.validate_contract(ExtractionResult, data, contract="ExtractionResult")
        if result.workflow_id != payload.workflow_id:
            raise AgentFailedError(
                AgentError(
                    category=AgentErrorCategory.CONTRACT_VALIDATION,
                    code="WORKFLOW_MISMATCH",
                    message="ExtractionResult workflow_id does not match the request",
                    retryable=False,
                )
            )
        if result.is_complete and result.missing_fields:
            raise AgentFailedError(
                AgentError(
                    category=AgentErrorCategory.CONTRACT_VALIDATION,
                    code="COMPLETENESS_CONTRADICTION",
                    message="is_complete with non-empty missing_fields is contradictory",
                    retryable=False,
                )
            )
        if not result.is_complete and not result.missing_fields:
            raise AgentFailedError(
                AgentError(
                    category=AgentErrorCategory.CONTRACT_VALIDATION,
                    code="COMPLETENESS_CONTRADICTION",
                    message="incomplete extraction must list missing_fields",
                    retryable=False,
                )
            )
        return result

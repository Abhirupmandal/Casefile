"""
Extractor agent: claim-document references in, typed ExtractionResult out.

Consumes document references as UNTRUSTED content (quarantined in the user
section). Never approves, denies, authorizes payout, or investigates.
Allowed tools: none.

Phase 12.2: Multi-Language Document Intake, Translation & Dual-Hash Evidence Provenance.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

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
from casefile.models.contracts import (
    SUPPORTED_LANGUAGE_CODES,
    ExtractionRequest,
    ExtractionResult,
)
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
        translation_client: Any = None,
    ) -> None:
        super().__init__(
            provider,
            prompt or get_prompt("extractor"),
            model=model,
            temperature=temperature,
            sink=sink,
            retry_guard=retry_guard,
        )
        self._translation_client = translation_client

    def validate_input(self, payload: ExtractionRequest, ctx: AgentContext) -> None:
        """Reject empty document lists and unsupported languages; typed fields already validated."""
        if not payload.claim_input.documents:
            raise AgentFailedError(
                AgentError(
                    category=AgentErrorCategory.INVALID_INPUT,
                    code="NO_DOCUMENTS",
                    message="ExtractionRequest carries no document references",
                    retryable=False,
                )
            )

        lang = getattr(payload.claim_input, "language", "en") or "en"
        base_lang = lang.strip().lower().split("-")[0].split("_")[0]
        if base_lang not in SUPPORTED_LANGUAGE_CODES:
            raise AgentFailedError(
                AgentError(
                    category=AgentErrorCategory.INVALID_INPUT,
                    code="UNSUPPORTED_LANGUAGE",
                    message=f"Unsupported claim language {lang!r}. Supported: {sorted(SUPPORTED_LANGUAGE_CODES)}",
                    retryable=False,
                )
            )

    def build_render(
        self, payload: ExtractionRequest, ctx: AgentContext
    ) -> tuple[RenderedPrompt, dict[str, object]]:
        """Trusted pointer in the task; document refs quarantined as untrusted."""
        claim_input = payload.claim_input
        lang = getattr(claim_input, "language", "en") or "en"
        base_lang = lang.strip().lower().split("-")[0].split("_")[0]

        was_translated = False
        source_lang = base_lang
        trans_provenance: dict[str, Any] = {}

        raw_documents = {f"document-{i}": ref for i, ref in enumerate(claim_input.documents)}
        quarantined_docs = dict(raw_documents)

        if self._translation_client is not None:
            from casefile.translation.contracts import TranslationRequest, TranslationStatus

            translated_docs = {}
            for doc_key, doc_text in raw_documents.items():
                det = self._translation_client.detect_language(doc_text)
                if det.is_ambiguous:
                    raise AgentFailedError(
                        AgentError(
                            category=AgentErrorCategory.INVALID_INPUT,
                            code="AMBIGUOUS_LANGUAGE",
                            message=f"Ambiguous language detected in {doc_key!r}",
                            retryable=False,
                        )
                    )
                if not det.is_supported:
                    raise AgentFailedError(
                        AgentError(
                            category=AgentErrorCategory.INVALID_INPUT,
                            code="UNSUPPORTED_LANGUAGE",
                            message=f"Unsupported language {det.detected_language!r} in {doc_key!r}",
                            retryable=False,
                        )
                    )

                effective_src = (
                    det.detected_language if det.detected_language != "en" else base_lang
                )
                if effective_src != "en":
                    res = self._translation_client.translate(
                        TranslationRequest(
                            text=doc_text,
                            source_language=effective_src,
                            target_language="en",
                        )
                    )
                    if (
                        res.status != TranslationStatus.SUCCESS
                        and res.status != TranslationStatus.SKIPPED
                    ):
                        raise AgentFailedError(
                            AgentError(
                                category=AgentErrorCategory.INVALID_INPUT,
                                code="TRANSLATION_FAILED",
                                message=f"Translation failed for {doc_key}: {res.error_message or res.failure_reason}",
                                retryable=False,
                            )
                        )
                    translated_docs[doc_key] = res.translated_text
                    was_translated = True
                    source_lang = effective_src
                    if res.metadata:
                        trans_provenance[doc_key] = res.metadata.model_dump()
                else:
                    translated_docs[doc_key] = doc_text
            quarantined_docs = translated_docs
        elif base_lang != "en":
            raise AgentFailedError(
                AgentError(
                    category=AgentErrorCategory.INVALID_INPUT,
                    code="TRANSLATION_UNAVAILABLE",
                    message=f"No translation client configured for non-English language {base_lang!r}",
                    retryable=False,
                )
            )

        render_meta = {
            "was_translated": was_translated,
            "source_language": source_lang,
            "translation_provenance": trans_provenance,
        }
        rendered = render(
            self._prompt,
            variables={"documents": "(see UNTRUSTED section below)"},
            untrusted=quarantined_docs,
        )
        return rendered, render_meta

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

        was_trans = bool(render_meta.get("was_translated", False))
        src_lang = str(render_meta.get("source_language", "en"))
        prov = render_meta.get("translation_provenance", {})

        if was_trans or src_lang != "en":
            result = result.model_copy(
                update={
                    "was_translated": was_trans,
                    "source_language": src_lang,
                    "translation_provenance": prov if isinstance(prov, dict) else {},
                }
            )

        return result

"""
Document retrieval tool: typed, claim-scoped, read-only.

Security: reference allowlist pattern, claim-ownership check, bounded
content size, no filesystem paths, no URLs, no path traversal, no
cross-claim leakage. Content returns as UNTRUSTED text for the prompt quarantine.

Phase 12.2: Multi-Language Document Intake, Translation & Dual-Hash Evidence Integrity.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from casefile.models.domain import ClaimDocumentReference, DocumentContentType, DocumentType
from casefile.models.versioning import SchemaVersion
from casefile.tools.contracts import ToolContext, ToolError, ToolErrorCode, ToolFailureError
from casefile.tools.fixtures import DEFAULT_STORE, FixtureStore
from casefile.tools.registry import Tool

DOCUMENT_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
MAX_CONTENT_CHARS = 32768


class DocumentRetrievalInput(BaseModel):
    """Typed document request. References only — never paths or URLs."""

    model_config = {"frozen": True}

    document_ref: str
    claim_ref: str
    target_language: str = "en"
    translate: bool = False


class DocumentRetrievalOutput(BaseModel):
    """Structured metadata plus content marked UNTRUSTED."""

    model_config = {"frozen": True}

    found: bool
    document: ClaimDocumentReference | None = None
    content_untrusted: str = ""
    original_content_untrusted: str = ""
    original_language: str = "en"
    detected_language: str = "en"
    was_translated: bool = False
    original_content_hash: str | None = None
    translated_content_hash: str | None = None
    translation_metadata: dict[str, Any] = Field(default_factory=dict)
    truncated: bool = False
    schema_version: SchemaVersion = "1.0.0"


class DocumentRetrievalTool(Tool[DocumentRetrievalInput, DocumentRetrievalOutput]):
    """Fetch one claim document by reference within claim scope."""

    name = "document_retrieval"
    version = "1.0.0"
    description = "Retrieve a claim document's metadata and content by reference"
    timeout_seconds = 5.0
    cache_ttl = 7200

    input_model = DocumentRetrievalInput
    output_model = DocumentRetrievalOutput

    def __init__(
        self,
        store: FixtureStore | None = None,
        client: Any = None,
        translation_client: Any = None,
    ) -> None:
        self._store = store or DEFAULT_STORE
        self._client = client
        self._translation_client = translation_client

    def run(self, tool_input: DocumentRetrievalInput, ctx: ToolContext) -> DocumentRetrievalOutput:
        """Validate reference, enforce claim scope, normalize and return bounded content."""
        ref = tool_input.document_ref.strip()
        if not DOCUMENT_REF_PATTERN.match(ref):
            raise ToolFailureError(
                ToolError(
                    code=ToolErrorCode.INVALID_INPUT,
                    message=f"Malformed document reference {tool_input.document_ref!r}",
                )
            )

        if self._client is not None:
            from casefile.tools.adapters.errors import ExternalServiceError
            from casefile.tools.adapters.mapping import map_external_error_to_tool_failure

            try:
                data = self._client.get_document(ref)
                if data is None:
                    return DocumentRetrievalOutput(found=False)

                ext_claim_ref = data.get("claim_ref", "").strip()
                if ext_claim_ref and ext_claim_ref != tool_input.claim_ref.strip():
                    raise ToolFailureError(
                        ToolError(
                            code=ToolErrorCode.SCOPE_VIOLATION,
                            message=f"Document {ref!r} is not in claim scope {tool_input.claim_ref!r}",
                        )
                    )

                content = str(data.get("content", ""))
                doc_type_val = data.get("document_type", "INCIDENT_REPORT")
                doc_type = (
                    DocumentType(doc_type_val) if isinstance(doc_type_val, str) else doc_type_val
                )
                content_type_val = data.get("content_type", "TEXT_PLAIN")
                content_type = (
                    DocumentContentType(content_type_val)
                    if isinstance(content_type_val, str)
                    else content_type_val
                )
                c_hash = data.get("content_hash")

                return self._process_content_and_translation(
                    ref=ref,
                    claim_ref=tool_input.claim_ref,
                    content=content,
                    doc_type=doc_type,
                    content_type=content_type,
                    filename=str(data.get("filename", "")),
                    source=str(data.get("source", "external_storage")),
                    trusted_source=bool(data.get("trusted_source", False)),
                    orig_hash_hint=c_hash,
                    target_lang=tool_input.target_language,
                    translate_requested=tool_input.translate,
                )
            except ToolFailureError:
                raise
            except ExternalServiceError as exc:
                raise map_external_error_to_tool_failure(exc, self.name) from exc
            except Exception as exc:
                raise ToolFailureError(
                    ToolError(
                        code=ToolErrorCode.VALIDATION_FAILURE,
                        message=f"Failed to parse document data: {exc}",
                        retryable=False,
                    )
                ) from exc

        stored = self._store.get_document(ref)
        if stored is None:
            return DocumentRetrievalOutput(found=False)
        if stored.claim_ref != tool_input.claim_ref.strip():
            raise ToolFailureError(
                ToolError(
                    code=ToolErrorCode.SCOPE_VIOLATION,
                    message=f"Document {ref!r} is not in claim scope {tool_input.claim_ref!r}",
                )
            )

        return self._process_content_and_translation(
            ref=ref,
            claim_ref=tool_input.claim_ref,
            content=stored.content,
            doc_type=stored.document_type,
            content_type=stored.content_type,
            filename=stored.filename,
            source=stored.source,
            trusted_source=False,
            orig_hash_hint=stored.content_hash,
            target_lang=tool_input.target_language,
            translate_requested=tool_input.translate,
        )

    def _process_content_and_translation(
        self,
        *,
        ref: str,
        claim_ref: str,
        content: str,
        doc_type: DocumentType,
        content_type: DocumentContentType,
        filename: str | None,
        source: str,
        trusted_source: bool,
        orig_hash_hint: str | None,
        target_lang: str = "en",
        translate_requested: bool = False,
    ) -> DocumentRetrievalOutput:
        """Handle hash computation, language detection, translation, and metadata."""
        raw_content = content
        orig_hash = orig_hash_hint or hashlib.sha256(raw_content.encode("utf-8")).hexdigest()

        if self._translation_client is not None:
            from casefile.translation.contracts import (
                TranslationFailureReason,
                TranslationRequest,
                TranslationStatus,
            )

            # Language detection
            det = self._translation_client.detect_language(raw_content)
            if det.is_ambiguous:
                raise ToolFailureError(
                    ToolError(
                        code=ToolErrorCode.VALIDATION_FAILURE,
                        message=f"Ambiguous language detected for document {ref!r} (confidence: {det.confidence})",
                    )
                )
            if not det.is_supported:
                raise ToolFailureError(
                    ToolError(
                        code=ToolErrorCode.VALIDATION_FAILURE,
                        message=f"Unsupported document language {det.detected_language!r} for document {ref!r}",
                    )
                )

            # Perform translation if non-English or translation requested
            if det.detected_language != target_lang or translate_requested:
                req = TranslationRequest(
                    text=raw_content,
                    source_language=det.detected_language,
                    target_language=target_lang,
                    document_ref=ref,
                )
                res = self._translation_client.translate(req)

                if res.status == TranslationStatus.FAILED:
                    err_code = (
                        ToolErrorCode.TIMEOUT
                        if res.failure_reason == TranslationFailureReason.TIMEOUT
                        else (
                            ToolErrorCode.UNAVAILABLE
                            if res.failure_reason == TranslationFailureReason.PROVIDER_UNAVAILABLE
                            else ToolErrorCode.VALIDATION_FAILURE
                        )
                    )
                    raise ToolFailureError(
                        ToolError(
                            code=err_code,
                            message=f"Translation failed for document {ref!r}: {res.error_message or res.failure_reason}",
                        )
                    )
                elif res.status == TranslationStatus.AMBIGUOUS:
                    raise ToolFailureError(
                        ToolError(
                            code=ToolErrorCode.VALIDATION_FAILURE,
                            message=f"Ambiguous translation for document {ref!r}: {res.error_message}",
                        )
                    )

                if res.status == TranslationStatus.SUCCESS:
                    trans_text = res.translated_text
                    trans_hash = (
                        res.metadata.translated_content_hash
                        if res.metadata
                        else hashlib.sha256(trans_text.encode("utf-8")).hexdigest()
                    )
                    trans_meta = res.metadata.model_dump() if res.metadata else {}

                    doc_ref = ClaimDocumentReference(
                        document_id=_stable_uuid(ref),
                        document_type=doc_type,
                        filename=filename,
                        content_type=content_type,
                        content_hash=orig_hash,
                        source=source,
                        trusted_source=trusted_source,
                        original_language=det.detected_language,
                        translated_content_hash=trans_hash,
                        translation_provider=getattr(
                            res.metadata, "provider", "translation_service"
                        ),
                        was_translated=True,
                    )

                    truncated = (
                        len(raw_content) > MAX_CONTENT_CHARS or len(trans_text) > MAX_CONTENT_CHARS
                    )
                    return DocumentRetrievalOutput(
                        found=True,
                        document=doc_ref,
                        content_untrusted=trans_text[:MAX_CONTENT_CHARS],
                        original_content_untrusted=raw_content[:MAX_CONTENT_CHARS],
                        original_language=det.detected_language,
                        detected_language=det.detected_language,
                        was_translated=True,
                        original_content_hash=orig_hash,
                        translated_content_hash=trans_hash,
                        translation_metadata=trans_meta,
                        truncated=truncated,
                    )

        # Fallback: English / Untranslated
        truncated = len(raw_content) > MAX_CONTENT_CHARS
        content_bounded = raw_content[:MAX_CONTENT_CHARS] if truncated else raw_content
        return DocumentRetrievalOutput(
            found=True,
            document=ClaimDocumentReference(
                document_id=_stable_uuid(ref),
                document_type=doc_type,
                filename=filename,
                content_type=content_type,
                content_hash=orig_hash,
                source=source,
                trusted_source=trusted_source,
                original_language="en",
                was_translated=False,
            ),
            content_untrusted=content_bounded,
            original_content_untrusted=content_bounded,
            original_language="en",
            detected_language="en",
            was_translated=False,
            original_content_hash=orig_hash,
            translated_content_hash=None,
            truncated=truncated,
        )


def _stable_uuid(ref: str) -> UUID:
    try:
        return UUID(ref)
    except ValueError:
        pass
    digest = hashlib.sha256(f"casefile-doc:{ref}".encode()).hexdigest()[:32]
    return UUID(hex=digest)

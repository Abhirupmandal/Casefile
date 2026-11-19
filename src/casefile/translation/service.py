"""
Phase 12.2: Translation Coordinator Service.

Manages language detection, text translation, dual-hash integrity,
telemetry instrumentation, and strict isolation between untrusted raw evidence
and normalized translated content.
"""

from __future__ import annotations

import time
from typing import Any

from casefile.translation.contracts import (
    LanguageDetectionResult,
    TranslatedDocument,
    TranslationFailureReason,
    TranslationRequest,
    TranslationResponse,
    TranslationStatus,
    compute_sha256,
)
from casefile.translation.errors import (
    AmbiguousLanguageError,
    EmptyContentError,
    MalformedTranslationResponseError,
    TranslationError,
    TranslationTimeoutError,
    TranslationUnavailableError,
    UnsupportedLanguageError,
)
from casefile.translation.protocols import TranslationServiceClientProtocol


class TranslationService:
    """
    Coordinates translation operations and enforces evidence integrity.
    """

    def __init__(
        self,
        client: TranslationServiceClientProtocol,
        *,
        cache: dict[str, TranslatedDocument] | None = None,
    ) -> None:
        self._client = client
        self._cache = cache if cache is not None else {}

    @property
    def client(self) -> TranslationServiceClientProtocol:
        return self._client

    def detect_language(self, text: str) -> LanguageDetectionResult:
        """Detect language of content; validate support."""
        if not text or not text.strip():
            return LanguageDetectionResult(
                detected_language="en",
                confidence=1.0,
                is_supported=True,
                is_ambiguous=False,
            )
        return self._client.detect_language(text)

    def translate_document(
        self,
        *,
        document_ref: str,
        content: str,
        source_language: str | None = None,
        target_language: str = "en",
        claim_id: Any = None,
    ) -> TranslatedDocument:
        """
        Normalize document content into target language.

        Computes independent SHA-256 digests for raw untrusted source and
        translated target. Ensures original content is never mutated or overwritten.
        """
        if not content or not content.strip():
            raise EmptyContentError(f"Document {document_ref!r} content is empty")

        orig_hash = compute_sha256(content)
        cache_key = f"{document_ref}:{source_language or 'auto'}:{target_language}:{orig_hash}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        start_time = time.perf_counter()
        request = TranslationRequest(
            text=content,
            source_language=source_language,
            target_language=target_language,
            document_ref=document_ref,
            claim_id=claim_id,
        )

        response: TranslationResponse = self._client.translate(request)
        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        if response.status == TranslationStatus.FAILED:
            self._handle_failure(response, source_language or "unknown")
        elif response.status == TranslationStatus.AMBIGUOUS:
            raise AmbiguousLanguageError(
                response.error_message or f"Ambiguous language for document {document_ref!r}"
            )

        if response.status == TranslationStatus.SKIPPED or (
            response.metadata and response.metadata.source_language == target_language
        ):
            # Source was already English (or target language)
            doc = TranslatedDocument(
                document_ref=document_ref,
                original_content=content,
                original_content_hash=orig_hash,
                original_language=target_language,
                translated_content=content,
                translated_content_hash=orig_hash,
                target_language=target_language,
                was_translated=False,
                metadata={
                    "status": "SKIPPED",
                    "reason": "already_target_language",
                    "latency_ms": elapsed_ms,
                },
            )
            self._cache[cache_key] = doc
            return doc

        # Translation succeeded
        translated_text = response.translated_text
        trans_hash = compute_sha256(translated_text)

        src_lang = (
            response.metadata.source_language
            if response.metadata
            else (source_language or "unknown")
        )

        meta: dict[str, Any] = {
            "status": response.status.value,
            "latency_ms": elapsed_ms,
            "provider": response.metadata.provider if response.metadata else "unknown",
            "model": response.metadata.model if response.metadata else "unknown",
            "character_count": len(content),
            "token_count": response.metadata.token_count if response.metadata else 0,
            "cost_usd": str(response.metadata.cost_usd) if response.metadata else "0.00",
            "confidence": response.metadata.confidence if response.metadata else 1.0,
        }

        doc = TranslatedDocument(
            document_ref=document_ref,
            original_content=content,
            original_content_hash=orig_hash,
            original_language=src_lang,
            translated_content=translated_text,
            translated_content_hash=trans_hash,
            target_language=target_language,
            was_translated=True,
            metadata=meta,
        )

        self._cache[cache_key] = doc
        return doc

    def _handle_failure(self, response: TranslationResponse, lang_hint: str) -> None:
        """Map failure taxonomy to typed domain exceptions."""
        reason = response.failure_reason
        msg = response.error_message or "Translation failed"

        if reason == TranslationFailureReason.UNSUPPORTED_LANGUAGE:
            raise UnsupportedLanguageError(language=lang_hint, message=msg)
        elif reason == TranslationFailureReason.AMBIGUOUS_LANGUAGE:
            raise AmbiguousLanguageError(message=msg)
        elif reason == TranslationFailureReason.TIMEOUT:
            raise TranslationTimeoutError(message=msg)
        elif reason == TranslationFailureReason.PROVIDER_UNAVAILABLE:
            raise TranslationUnavailableError(message=msg)
        elif reason == TranslationFailureReason.MALFORMED_RESPONSE:
            raise MalformedTranslationResponseError(message=msg)
        elif reason == TranslationFailureReason.EMPTY_CONTENT:
            raise EmptyContentError(message=msg)
        else:
            raise TranslationError(
                message=msg, reason=reason or TranslationFailureReason.INTERNAL_ERROR
            )

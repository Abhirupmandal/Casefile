"""
Phase 12.2: Multi-Language Document Intake, Translation & Normalization Contracts.

Strictly typed contracts for language detection, translation requests,
responses, provenance metadata, and dual-hash evidence verification.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


def _utcnow() -> datetime:
    return datetime.now(UTC)


def compute_sha256(content: str) -> str:
    """Compute deterministic SHA-256 hex digest for string content."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


class TranslationStatus(str, Enum):
    """Lifecycle status of a translation request."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    AMBIGUOUS = "AMBIGUOUS"


class TranslationFailureReason(str, Enum):
    """Categorized failure taxonomy for translation operations."""

    UNSUPPORTED_LANGUAGE = "UNSUPPORTED_LANGUAGE"
    AMBIGUOUS_LANGUAGE = "AMBIGUOUS_LANGUAGE"
    TIMEOUT = "TIMEOUT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    EMPTY_CONTENT = "EMPTY_CONTENT"
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class LanguageDetectionResult(BaseModel):
    """Typed result from a language identification component."""

    model_config = {"frozen": True}

    detected_language: str = Field(..., description="ISO 639-1 two-letter code")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    is_supported: bool = Field(default=True)
    is_ambiguous: bool = Field(default=False)
    model_version: str = Field(default="v1.0")


class TranslationRequest(BaseModel):
    """Request envelope sent to a translation provider."""

    model_config = {"frozen": True}

    text: str = Field(..., description="Source text to translate")
    source_language: str | None = Field(
        default=None,
        description="ISO 639-1 source code; if None or 'auto', detection is performed",
    )
    target_language: str = Field(default="en", description="Target ISO 639-1 code (default 'en')")
    document_ref: str | None = Field(default=None, description="Optional document reference")
    claim_id: UUID | None = Field(default=None, description="Optional claim identifier")


class TranslationResultMetadata(BaseModel):
    """Immutable provenance and audit metadata for a completed translation."""

    model_config = {"frozen": True}

    source_language: str
    target_language: str = "en"
    provider: str
    model: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    original_content_hash: str
    translated_content_hash: str
    character_count: int = Field(ge=0)
    token_count: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0.0)
    cost_usd: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))
    detected_language: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)


class TranslationResponse(BaseModel):
    """Response returned by a translation service client."""

    status: TranslationStatus
    translated_text: str = ""
    original_text: str = ""
    metadata: TranslationResultMetadata | None = None
    failure_reason: TranslationFailureReason | None = None
    error_message: str | None = None

    @property
    def is_success(self) -> bool:
        return self.status == TranslationStatus.SUCCESS


class TranslatedDocument(BaseModel):
    """
    Evidence record binding original untrusted content, translated content,
    and their independent SHA-256 integrity digests.

    Guarantees translated content cannot overwrite or mutate original evidence.
    """

    model_config = {"frozen": True}

    document_ref: str
    original_content: str
    original_content_hash: str
    original_language: str
    translated_content: str
    translated_content_hash: str
    target_language: str = "en"
    was_translated: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("original_content_hash")
    @classmethod
    def _validate_orig_hash(cls, v: str) -> str:
        if len(v) != 64:
            raise ValueError("original_content_hash must be a 64-char SHA-256 hex digest")
        return v

    @field_validator("translated_content_hash")
    @classmethod
    def _validate_trans_hash(cls, v: str) -> str:
        if len(v) != 64:
            raise ValueError("translated_content_hash must be a 64-char SHA-256 hex digest")
        return v

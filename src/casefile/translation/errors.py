"""
Phase 12.2: Translation Exception Hierarchy.
"""

from __future__ import annotations

from casefile.translation.contracts import TranslationFailureReason


class TranslationError(Exception):
    """Base exception for translation domain errors."""

    def __init__(
        self,
        message: str,
        reason: TranslationFailureReason = TranslationFailureReason.INTERNAL_ERROR,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.retryable = retryable


class UnsupportedLanguageError(TranslationError):
    """Raised when an unsupported language is provided or detected."""

    def __init__(self, language: str, message: str | None = None) -> None:
        msg = (
            message
            or f"Language {language!r} is not supported. Supported: ['en', 'es', 'fr', 'de']"
        )
        super().__init__(msg, reason=TranslationFailureReason.UNSUPPORTED_LANGUAGE, retryable=False)
        self.language = language


class AmbiguousLanguageError(TranslationError):
    """Raised when language detection confidence is below threshold or ambiguous."""

    def __init__(self, message: str = "Language detection ambiguous or low confidence") -> None:
        super().__init__(
            message, reason=TranslationFailureReason.AMBIGUOUS_LANGUAGE, retryable=False
        )


class TranslationTimeoutError(TranslationError):
    """Raised when a translation provider call times out."""

    def __init__(self, message: str = "Translation service call timed out") -> None:
        super().__init__(message, reason=TranslationFailureReason.TIMEOUT, retryable=True)


class TranslationUnavailableError(TranslationError):
    """Raised when the translation service is down or 503 unavailable."""

    def __init__(self, message: str = "Translation service unavailable") -> None:
        super().__init__(
            message, reason=TranslationFailureReason.PROVIDER_UNAVAILABLE, retryable=True
        )


class MalformedTranslationResponseError(TranslationError):
    """Raised when the translation provider returns a malformed or corrupted payload."""

    def __init__(self, message: str = "Malformed translation response from provider") -> None:
        super().__init__(
            message, reason=TranslationFailureReason.MALFORMED_RESPONSE, retryable=False
        )


class EmptyContentError(TranslationError):
    """Raised when source content is empty or contains only whitespace."""

    def __init__(self, message: str = "Cannot translate empty content") -> None:
        super().__init__(message, reason=TranslationFailureReason.EMPTY_CONTENT, retryable=False)

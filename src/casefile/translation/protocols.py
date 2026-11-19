"""
Phase 12.2: Translation Service Protocol.
"""

from __future__ import annotations

from typing import Protocol

from casefile.translation.contracts import (
    LanguageDetectionResult,
    TranslationRequest,
    TranslationResponse,
)


class TranslationServiceClientProtocol(Protocol):
    """Protocol for translation service clients (Phase 12.2)."""

    def detect_language(self, text: str) -> LanguageDetectionResult:
        """Detect language of given text."""
        ...

    def translate(self, request: TranslationRequest) -> TranslationResponse:
        """Translate text according to request envelope."""
        ...

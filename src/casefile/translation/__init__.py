"""
Phase 12.2: Multi-Language Document Intake, Translation & Normalization Package.
"""

from casefile.translation.contracts import (
    LanguageDetectionResult,
    TranslatedDocument,
    TranslationFailureReason,
    TranslationRequest,
    TranslationResponse,
    TranslationResultMetadata,
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
from casefile.translation.fake import (
    FRENCH_TO_ENGLISH,
    GERMAN_TO_ENGLISH,
    SPANISH_TO_ENGLISH,
    FakeTranslationServiceClient,
)
from casefile.translation.protocols import TranslationServiceClientProtocol
from casefile.translation.service import TranslationService

__all__ = [
    "LanguageDetectionResult",
    "TranslatedDocument",
    "TranslationFailureReason",
    "TranslationRequest",
    "TranslationResponse",
    "TranslationResultMetadata",
    "TranslationStatus",
    "compute_sha256",
    "TranslationError",
    "AmbiguousLanguageError",
    "EmptyContentError",
    "MalformedTranslationResponseError",
    "TranslationTimeoutError",
    "TranslationUnavailableError",
    "UnsupportedLanguageError",
    "TranslationServiceClientProtocol",
    "FakeTranslationServiceClient",
    "TranslationService",
    "SPANISH_TO_ENGLISH",
    "FRENCH_TO_ENGLISH",
    "GERMAN_TO_ENGLISH",
]

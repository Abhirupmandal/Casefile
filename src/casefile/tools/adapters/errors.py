"""
Typed exceptions for external service integrations (Phase 9 §B).
"""

from __future__ import annotations


class ExternalServiceError(Exception):
    """Base exception for external service adapter failures."""

    def __init__(self, message: str, service_name: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.service_name = service_name
        self.status_code = status_code


class ExternalServiceTimeoutError(ExternalServiceError):
    """Raised when an external service request times out."""


class ExternalServiceUnavailableError(ExternalServiceError):
    """Raised when an external service is unavailable (HTTP 502/503/504 or connection failure)."""


class ExternalServiceAuthError(ExternalServiceError):
    """Raised when authentication/authorization fails against an external service (HTTP 401/403)."""


class ExternalServiceResponseError(ExternalServiceError):
    """Raised when an external service returns an unexpected or malformed payload."""

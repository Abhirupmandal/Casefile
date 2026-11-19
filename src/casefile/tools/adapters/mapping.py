"""
Mapping from external service exceptions to ToolFailureError (Phase 9 §B).
"""

from __future__ import annotations

from casefile.tools.adapters.errors import (
    ExternalServiceAuthError,
    ExternalServiceError,
    ExternalServiceResponseError,
    ExternalServiceTimeoutError,
    ExternalServiceUnavailableError,
)
from casefile.tools.contracts import ToolError, ToolErrorCode, ToolFailureError


def map_external_error_to_tool_failure(exc: Exception, tool_name: str) -> ToolFailureError:
    """Map external client exceptions to typed ToolFailureError."""
    if isinstance(exc, ExternalServiceTimeoutError):
        return ToolFailureError(
            ToolError(
                code=ToolErrorCode.TIMEOUT,
                message=f"External service for {tool_name!r} timed out: {exc}",
                retryable=True,
            )
        )
    if isinstance(exc, ExternalServiceUnavailableError):
        return ToolFailureError(
            ToolError(
                code=ToolErrorCode.UNAVAILABLE,
                message=f"External service for {tool_name!r} unavailable: {exc}",
                retryable=True,
            )
        )
    if isinstance(exc, ExternalServiceAuthError):
        return ToolFailureError(
            ToolError(
                code=ToolErrorCode.UNAUTHORIZED_TOOL,
                message=f"External service authentication failed for {tool_name!r}: {exc}",
                retryable=False,
            )
        )
    if isinstance(exc, ExternalServiceResponseError):
        return ToolFailureError(
            ToolError(
                code=ToolErrorCode.VALIDATION_FAILURE,
                message=f"Invalid response from {tool_name!r}: {exc}",
                retryable=False,
            )
        )
    if isinstance(exc, ExternalServiceError):
        return ToolFailureError(
            ToolError(
                code=ToolErrorCode.INTERNAL_FAILURE,
                message=f"External service error in {tool_name!r}: {exc}",
                retryable=False,
            )
        )
    return ToolFailureError(
        ToolError(
            code=ToolErrorCode.INTERNAL_FAILURE,
            message=f"Unexpected error calling {tool_name!r}: {exc}",
            retryable=False,
        )
    )

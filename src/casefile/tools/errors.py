"""
Typed tool exceptions and error taxonomy (Phase 3).

Distinguishes authorization failures, validation errors, timeouts,
contract violations, and execution failures without leaking secrets
or raw document text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from casefile.tools.contracts import ToolError as ToolErrorDetail


class ToolError(Exception):
    """Base exception for all tool operations."""

    def __init__(self, message: str, *, error: ToolErrorDetail | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.error = error


class ToolNotFoundError(ToolError):
    """Raised when an unregistered tool is requested."""


class ToolAuthorizationError(ToolError):
    """Raised when an agent attempts to invoke an unauthorized tool."""


class ToolValidationError(ToolError):
    """Raised when tool input fails schema or range validation."""


class ToolExecutionError(ToolError):
    """Raised when a tool encounters an internal runtime failure."""


class ToolTimeoutError(ToolError):
    """Raised when tool execution exceeds its configured timeout deadline."""


class ToolUnavailableError(ToolError):
    """Raised when an external/underlying data source is unreachable."""


class ToolContractError(ToolError):
    """Raised when a tool output fails schema or contract validation."""

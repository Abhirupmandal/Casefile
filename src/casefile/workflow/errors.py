"""
CASEFILE workflow error taxonomy (Phase 2 §20).

Typed, structured errors representing all workflow and agent failure modes:
- Invalid transition
- Terminal state violation
- Actor not allowed
- Rework limit exhausted
- Retry exhausted
- Malformed agent output
- Schema / contract validation failure
- Provider failure
- Provider timeout
- Contract payload mismatch
- Unknown contract type
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from casefile.agents.providers import ProviderError, ProviderTimeoutError
from casefile.models.envelope import ContractPayloadMismatchError, UnknownContractTypeError
from casefile.workflow.transitions import (
    ActorNotAllowedError,
    InvalidTransitionError,
    ReworkLimitError,
    TerminalStateError,
)


class WorkflowError(Exception):
    """Base class for all workflow engine errors."""

    def __init__(
        self, message: str, *, workflow_run_id: UUID | None = None, **details: Any
    ) -> None:
        super().__init__(message)
        self.message = message
        self.workflow_run_id = workflow_run_id
        self.details = details


class RetryExhaustedError(WorkflowError):
    """Raised when an agent's retry budget is exhausted."""


class MalformedOutputError(WorkflowError):
    """Raised when an agent returns non-JSON or malformed output."""


class ContractValidationError(WorkflowError):
    """Raised when an agent's output fails schema or domain validation."""


class WorkflowExecutionError(WorkflowError):
    """Raised when a workflow run encounters a terminal execution failure."""


__all__ = [
    "WorkflowError",
    "InvalidTransitionError",
    "TerminalStateError",
    "ActorNotAllowedError",
    "ReworkLimitError",
    "RetryExhaustedError",
    "MalformedOutputError",
    "ContractValidationError",
    "WorkflowExecutionError",
    "ProviderError",
    "ProviderTimeoutError",
    "ContractPayloadMismatchError",
    "UnknownContractTypeError",
]

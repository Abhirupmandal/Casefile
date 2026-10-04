"""
Typed tool invocation contracts (docs/tool-architecture.md, Phase 5).

Every invocation carries identities, versions, typed I/O, status, timing,
and error detail. Tools raise ToolFailureError (typed); the registry maps it
into a ToolOutcome — errors are data, never silent empty successes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from casefile.models.domain import AgentType
from casefile.models.versioning import SchemaVersion
from casefile.tools.context import ToolContext, ToolUsage


class ToolStatus(str, Enum):
    """Lifecycle of one tool invocation."""

    REQUESTED = "REQUESTED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    TIMEOUT = "TIMEOUT"
    DENIED = "DENIED"


class ToolErrorCode(str, Enum):
    """Typed tool failure categories (Phase 5 §15)."""

    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    UNAUTHORIZED_TOOL = "UNAUTHORIZED_TOOL"
    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    SCOPE_VIOLATION = "SCOPE_VIOLATION"
    VALIDATION_FAILURE = "VALIDATION_FAILURE"
    TIMEOUT = "TIMEOUT"
    UNAVAILABLE = "UNAVAILABLE"
    INTERNAL_FAILURE = "INTERNAL_FAILURE"
    VERSION_MISMATCH = "VERSION_MISMATCH"
    REPLAY_ARTIFACT_MISSING = "REPLAY_ARTIFACT_MISSING"


class ToolError(BaseModel):
    """Structured tool failure detail."""

    model_config = {"frozen": True}

    code: ToolErrorCode
    message: str
    retryable: bool = False
    schema_version: SchemaVersion = "1.0.0"


class ToolFailureError(Exception):
    """Raised by tools/registry; always carries a typed ToolError."""

    def __init__(self, error: ToolError) -> None:
        super().__init__(f"{error.code.value}: {error.message}")
        self.error = error


class ToolResultMetadata(BaseModel):
    """Structured provenance metadata for an executed tool invocation."""

    model_config = {"frozen": True}

    source: str
    source_version: str = "1.0.0"
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    fixture_id: str | None = None
    tool_name: str
    tool_version: str = "1.0.0"
    correlation_id: UUID
    schema_version: SchemaVersion = "1.0.0"


class ToolCall(BaseModel):
    """Durable record of one tool invocation for audit and replay."""

    invocation_id: UUID = Field(default_factory=uuid4)
    tool_name: str
    tool_version: str = "1.0.0"
    claim_id: UUID
    workflow_run_id: UUID
    execution_id: UUID
    correlation_id: UUID
    requesting_agent: AgentType
    input_json: str = ""
    output_json: str = ""
    status: ToolStatus = ToolStatus.REQUESTED
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    duration_ms: int = Field(default=0, ge=0)
    error: ToolError | None = None
    idempotency_key: str = ""
    schema_version: SchemaVersion = "1.0.0"


class ToolOutcome(BaseModel):
    """Registry result: typed output or typed error, with invocation metadata."""

    model_config = {"arbitrary_types_allowed": True}

    invocation_id: UUID
    tool_name: str
    tool_version: str = "1.0.0"
    status: ToolStatus
    output: BaseModel | None = None
    error: ToolError | None = None
    duration_ms: int = Field(default=0, ge=0)
    cost_units: int = Field(default=0, ge=0)
    idempotency_key: str = ""
    call: ToolCall | None = None
    provenance: ToolResultMetadata | None = None
    schema_version: SchemaVersion = "1.0.0"

    @property
    def succeeded(self) -> bool:
        """True only for successful outcomes with a typed output."""
        return self.status == ToolStatus.SUCCESS and self.output is not None


__all__ = [
    "ToolCall",
    "ToolContext",
    "ToolError",
    "ToolErrorCode",
    "ToolFailureError",
    "ToolOutcome",
    "ToolResultMetadata",
    "ToolStatus",
    "ToolUsage",
]

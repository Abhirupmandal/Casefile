"""
Agent failure categories and execution records (Phase 4 §13, §14, §20).

Failures are typed structures, never fake successes. Every execution
produces a record with the structured data Phase 11 evaluation needs:
success/failure, latency, token usage, output validity, retries.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from casefile.models.domain import AgentType
from casefile.models.versioning import SchemaVersion


class AgentErrorCategory(str, Enum):
    """Explicit agent failure categories."""

    INVALID_INPUT = "INVALID_INPUT"
    CONTRACT_VALIDATION = "CONTRACT_VALIDATION"
    PROVIDER = "PROVIDER"
    TIMEOUT = "TIMEOUT"
    MALFORMED_OUTPUT = "MALFORMED_OUTPUT"
    POLICY_VIOLATION = "POLICY_VIOLATION"
    UNEXPECTED = "UNEXPECTED"


class AgentError(BaseModel):
    """Typed agent failure. Retryable only for transient causes."""

    model_config = {"frozen": True}

    category: AgentErrorCategory
    code: str
    message: str
    retryable: bool = False
    attempt: int = Field(default=1, ge=1)


class AgentExecutionRecord(BaseModel):
    """Structured metadata for one agent execution (evaluation-ready)."""

    model_config = {"frozen": True}

    execution_id: UUID
    workflow_run_id: UUID | None = None
    agent: AgentType
    started_at: datetime
    finished_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    status: str = Field(default="SUCCESS", pattern=r"^(SUCCESS|FAILURE)$")
    input_correlation_id: UUID
    input_contract_version: str = "1.0.0"
    output_contract_type: str = ""
    contract_version: str = "1.0.0"
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    provider_name: str = ""
    model_name: str = ""
    attempts: int = Field(default=1, ge=1)
    retry_reasons: tuple[str, ...] = ()
    output_valid: bool = True
    error: AgentError | None = None
    schema_version: SchemaVersion = "1.0.0"

"""
Tool invocation context and usage accounting (Phase 3).

Carries execution identity and operational constraints. Tools see
identities and limits — never database sessions, network connections,
or filesystems.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from casefile.models.domain import AgentType
from casefile.models.versioning import SchemaVersion


class ToolUsage(BaseModel):
    """Call counter + latency/cost metadata for budget and resource accounting."""

    calls_made: int = Field(default=0, ge=0)
    total_latency_ms: int = Field(default=0, ge=0)
    cost_units: int = Field(default=0, ge=0)
    max_calls: int = Field(default=100, ge=1)


class ToolContext(BaseModel):
    """Per-invocation context carrying execution identity and operational boundaries."""

    model_config = {"arbitrary_types_allowed": True}

    claim_id: UUID
    workflow_run_id: UUID
    execution_id: UUID = Field(default_factory=uuid4)
    correlation_id: UUID = Field(default_factory=uuid4)
    agent: AgentType
    timeout_seconds: float = Field(default=10.0, gt=0.0)
    max_results: int = Field(default=50, ge=1, le=100)
    idempotency_key: str = ""
    usage: ToolUsage = Field(default_factory=ToolUsage)
    schema_version: SchemaVersion = "1.0.0"

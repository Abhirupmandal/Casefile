"""
Typed agent execution context (Phase 4 §9).

Every agent receives exactly this: identities, its own type, the schema
version, the tools it may use (names only — Phase 5 implements them),
bounded execution metadata, and an idempotency key. No globals, no
unrestricted application state.
"""

from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from casefile.models.domain import AgentType
from casefile.models.versioning import SchemaVersion


class AgentContext(BaseModel):
    """Per-execution context handed to one agent invocation."""

    model_config = {"frozen": True}

    claim_id: UUID
    workflow_run_id: UUID
    execution_id: UUID = Field(default_factory=uuid4)
    correlation_id: UUID = Field(default_factory=uuid4)
    agent: AgentType
    schema_version: SchemaVersion = "1.0.0"
    allowed_tools: frozenset[str] = Field(default_factory=frozenset)
    max_attempts: int = Field(default=2, ge=1, le=5)
    timeout_seconds: int = Field(default=300, ge=1)
    idempotency_key: str = ""

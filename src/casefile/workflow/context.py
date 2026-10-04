"""
CASEFILE workflow run context (Phase 3 §15).

Typed execution context carried alongside every transition: identities,
actor, timestamps, and a clock abstraction so tests control time while
production uses wall-clock time. No hidden globals.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    pass

from casefile.models.contracts import (
    ClaimInput,
    ExtractionResult,
    InvestigationResult,
    ReviewResult,
    WorkflowState,
)
from casefile.models.domain import (
    AgentType,
    Claim,
    ClaimDocumentReference,
    DamageEstimate,
    Policy,
    PriorClaim,
    Recommendation,
)
from casefile.models.versioning import SchemaVersion


@runtime_checkable
class Clock(Protocol):
    """Time source abstraction for deterministic workflows."""

    def now(self) -> datetime:
        """Return the current timestamp."""
        ...


class SystemClock:
    """Production clock: timezone-aware UTC wall time."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Test clock: frozen, manually advanced time."""

    def __init__(self, start: datetime) -> None:
        self._current = start

    def now(self) -> datetime:
        return self._current

    def advance(self, seconds: int) -> datetime:
        """Move the clock forward; returns the new time."""
        from datetime import timedelta

        self._current = self._current + timedelta(seconds=seconds)
        return self._current


class RunContext(BaseModel):
    """Typed context for one workflow execution step."""

    model_config = {"arbitrary_types_allowed": True}

    claim_id: UUID
    workflow_run_id: UUID
    execution_id: UUID = Field(default_factory=uuid4)
    correlation_id: UUID = Field(default_factory=uuid4)
    actor: AgentType = AgentType.SUPERVISOR
    clock: Clock = Field(default_factory=SystemClock)
    max_rework_cycles: int = Field(default=3, ge=0)
    schema_version: SchemaVersion = "1.0.0"


class WorkflowSnapshot(BaseModel):
    """Serializable point-in-time workflow position for engine input/output."""

    workflow_run_id: UUID
    claim_id: UUID
    current_state: WorkflowState
    step_count: int = Field(default=0, ge=0)
    rework_count: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    schema_version: SchemaVersion = "1.0.0"


class WorkflowContext(BaseModel):
    """
    Strongly typed workflow execution context carrying trusted control-plane state.

    Carries trusted references to:
    - claim
    - documents
    - policy information
    - prior claims
    - damage estimates
    - investigation findings
    - review findings
    - recommendation
    - workflow metadata
    - execution metadata

    Maintains strict separation between trusted control-plane state and untrusted
    claim/document content.
    """

    model_config = {"arbitrary_types_allowed": True}

    workflow_run_id: UUID = Field(default_factory=uuid4)
    claim_id: UUID = Field(default_factory=uuid4)
    correlation_id: UUID = Field(default_factory=uuid4)
    schema_version: SchemaVersion = "1.0.0"

    # Trusted domain references
    claim: ClaimInput | Claim | None = None
    documents: list[ClaimDocumentReference | str] = Field(default_factory=list)
    policy_information: Policy | None = None
    prior_claims: list[PriorClaim] = Field(default_factory=list)
    damage_estimates: list[DamageEstimate] = Field(default_factory=list)

    # Specialist agent findings (typed contracts only, never raw LLM output)
    extraction_result: ExtractionResult | None = None
    investigation_result: InvestigationResult | None = None
    review_result: ReviewResult | None = None
    recommendation: Recommendation | None = None

    # Metadata
    workflow_metadata: dict[str, str] = Field(default_factory=dict)
    execution_history: list[Any] = Field(default_factory=list)
    tool_calls: list[Any] = Field(default_factory=list)

    # Current snapshot
    current_snapshot: WorkflowSnapshot | None = None

    def record_execution(self, record: Any) -> None:
        """Record an agent execution in the execution history."""
        self.execution_history.append(record)

    def record_tool_call(self, call: Any) -> None:
        """Record a tool invocation in the workflow context."""
        self.tool_calls.append(call)

    def set_extraction_result(self, result: ExtractionResult) -> None:
        """Store trusted extraction result."""
        self.extraction_result = result

    def set_investigation_result(self, result: InvestigationResult) -> None:
        """Store trusted investigation result."""
        self.investigation_result = result

    def set_review_result(self, result: ReviewResult) -> None:
        """Store trusted review result."""
        self.review_result = result

    @property
    def untrusted_documents(self) -> list[str]:
        """Explicitly quarantined untrusted document references."""
        refs: list[str] = []
        for doc in self.documents:
            if isinstance(doc, str):
                refs.append(doc)
            elif hasattr(doc, "document_id"):
                refs.append(str(doc.document_id))
        return refs

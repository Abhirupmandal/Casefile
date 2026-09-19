"""
CASEFILE Agent & Workflow Contracts

Strictly typed Pydantic v2 models for all inter-agent communication,
state machine representation, budget tracking, and checkpointing.

Per ADR-002, no free-form dictionaries or untyped structures are permitted
in inter-agent communication.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class WorkflowState(str, Enum):
    """All 14 states in the CASEFILE state machine."""

    # Processing & Non-Terminal States
    RECEIVED = "RECEIVED"
    EXTRACTION = "EXTRACTION"
    INVESTIGATION = "INVESTIGATION"
    REVIEW = "REVIEW"
    REWORK_LOOP = "REWORK_LOOP"
    HUMAN_APPROVAL = "HUMAN_APPROVAL"

    # Terminal States (8 Total)
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"
    ESCALATION = "ESCALATION"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    TIMEOUT = "TIMEOUT"
    MAX_STEPS_EXCEEDED = "MAX_STEPS_EXCEEDED"
    MAX_REWORK_EXCEEDED = "MAX_REWORK_EXCEEDED"

    @property
    def is_terminal(self) -> bool:
        """Return True if this is a terminal state."""
        return self in {
            WorkflowState.APPROVED,
            WorkflowState.REJECTED,
            WorkflowState.FAILED,
            WorkflowState.ESCALATION,
            WorkflowState.BUDGET_EXHAUSTED,
            WorkflowState.TIMEOUT,
            WorkflowState.MAX_STEPS_EXCEEDED,
            WorkflowState.MAX_REWORK_EXCEEDED,
        }


class ClaimInput(BaseModel):
    """
    Initial claim submission contract.
    Entry point for workflow processing.
    """

    claim_id: UUID = Field(default_factory=uuid4)
    policy_id: str = Field(..., description="Policy identifier")
    claimant_name: str = Field(..., description="Name of the claimant")
    incident_date: str = Field(..., description="ISO 8601 date string of incident")
    claim_amount: Decimal = Field(..., gt=Decimal("0.00"), description="Claim amount in USD")
    description: str = Field(..., description="Incident description")
    documents: list[str] = Field(default_factory=list, description="Document IDs or references")
    schema_version: str = Field(default="1.0.0", description="Contract schema version")

    @field_validator("policy_id")
    @classmethod
    def validate_policy_id(cls, v: str) -> str:
        stripped = v.strip().upper()
        if not stripped:
            raise ValueError("policy_id cannot be empty")
        return stripped

    @field_validator("claimant_name")
    @classmethod
    def validate_claimant_name(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("claimant_name cannot be empty")
        return stripped


class BudgetLimits(BaseModel):
    """Pre-flight budget thresholds for workflow execution."""

    max_input_tokens: int = Field(default=100_000, ge=1)
    max_output_tokens: int = Field(default=20_000, ge=1)
    max_total_tokens: int = Field(default=150_000, ge=1)
    max_cost_usd: Decimal = Field(default=Decimal("5.00"), gt=Decimal("0.00"))
    max_steps: int = Field(default=50, ge=1)
    max_execution_time_seconds: int = Field(default=1800, ge=1)
    max_tool_calls: int = Field(default=100, ge=1)
    max_rework_cycles: int = Field(default=3, ge=1)


class TokenUsage(BaseModel):
    """Token usage metrics from an LLM call."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def calculate_total(self) -> TokenUsage:
        if self.total_tokens == 0 and (self.input_tokens > 0 or self.output_tokens > 0):
            self.total_tokens = self.input_tokens + self.output_tokens
        return self


class BudgetState(BaseModel):
    """Tracks resource consumption across all 7 budget dimensions."""

    input_tokens_used: int = Field(default=0, ge=0)
    output_tokens_used: int = Field(default=0, ge=0)
    total_tokens_used: int = Field(default=0, ge=0)
    estimated_cost_usd: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))
    steps_completed: int = Field(default=0, ge=0)
    elapsed_time_seconds: int = Field(default=0, ge=0)
    tool_calls_made: int = Field(default=0, ge=0)
    rework_count: int = Field(default=0, ge=0)
    limits: BudgetLimits = Field(default_factory=BudgetLimits)
    is_exhausted: bool = Field(default=False)
    exhaustion_reason: str | None = Field(default=None)

    @model_validator(mode="after")
    def update_totals(self) -> BudgetState:
        self.total_tokens_used = self.input_tokens_used + self.output_tokens_used
        return self

    def check_limits(self) -> str | None:
        """Check all 7 budget dimensions pre-flight."""
        if self.total_tokens_used >= self.limits.max_total_tokens:
            self.is_exhausted = True
            self.exhaustion_reason = "MAX_TOKENS_EXCEEDED"
            return self.exhaustion_reason

        if self.estimated_cost_usd >= self.limits.max_cost_usd:
            self.is_exhausted = True
            self.exhaustion_reason = "MAX_COST_EXCEEDED"
            return self.exhaustion_reason

        if self.steps_completed >= self.limits.max_steps:
            self.is_exhausted = True
            self.exhaustion_reason = "MAX_STEPS_EXCEEDED"
            return self.exhaustion_reason

        if self.elapsed_time_seconds >= self.limits.max_execution_time_seconds:
            self.is_exhausted = True
            self.exhaustion_reason = "MAX_TIME_EXCEEDED"
            return self.exhaustion_reason

        if self.rework_count >= self.limits.max_rework_cycles:
            self.is_exhausted = True
            self.exhaustion_reason = "MAX_REWORK_EXCEEDED"
            return self.exhaustion_reason

        return None

    def add_usage(self, tokens: TokenUsage, cost: Decimal) -> None:
        """Accumulate usage from an LLM call and check limits."""
        self.input_tokens_used += tokens.input_tokens
        self.output_tokens_used += tokens.output_tokens
        self.total_tokens_used = self.input_tokens_used + self.output_tokens_used
        self.estimated_cost_usd += cost
        self.check_limits()


class ExtractionRequest(BaseModel):
    """Supervisor -> Extractor agent contract."""

    workflow_id: UUID = Field(default_factory=uuid4)
    claim_input: ClaimInput


class ExtractionResult(BaseModel):
    """Extractor -> Supervisor agent contract."""

    workflow_id: UUID
    claimant_name: str
    policy_id: str
    incident_date: str
    incident_location: str
    incident_description: str
    claim_amount: Decimal
    requested_coverage_type: str
    documents_processed: list[str] = Field(default_factory=list)
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    missing_fields: list[str] = Field(default_factory=list)
    is_complete: bool = True
    schema_version: str = "1.0.0"


class InvestigationRequest(BaseModel):
    """Supervisor -> Investigator agent contract."""

    workflow_id: UUID
    extraction_result: ExtractionResult
    rework_feedback: str | None = None


class InvestigationResult(BaseModel):
    """Investigator -> Supervisor agent contract."""

    workflow_id: UUID
    policy_details: dict[str, Any] = Field(default_factory=dict)
    claim_history: dict[str, Any] = Field(default_factory=dict)
    repair_cost_validation: dict[str, Any] = Field(default_factory=dict)
    fraud_signals: dict[str, Any] = Field(default_factory=dict)
    supporting_documents: list[str] = Field(default_factory=list)
    findings_summary: str
    evidence_strength: float = Field(ge=0.0, le=1.0)
    tool_calls_made: list[str] = Field(default_factory=list)
    investigation_duration_seconds: int = 0
    tools_succeeded: int = 0
    tools_failed: int = 0
    schema_version: str = "1.0.0"


class ReviewRequest(BaseModel):
    """Supervisor -> Reviewer agent contract."""

    workflow_id: UUID
    extraction_result: ExtractionResult
    investigation_result: InvestigationResult
    rework_count: int = Field(default=0, ge=0)


class ReviewResult(BaseModel):
    """Reviewer -> Supervisor agent contract."""

    workflow_id: UUID
    decision: Literal["APPROVE", "REJECT", "REWORK"]
    reasoning: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    evidence_completeness: float = Field(default=1.0, ge=0.0, le=1.0)
    identified_gaps: list[str] = Field(default_factory=list)
    rework_feedback: str | None = None
    fraud_risk_level: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
    fraud_signals_detected: bool = False
    schema_version: str = "1.0.0"


class WorkflowResult(BaseModel):
    """Final workflow termination contract."""

    workflow_id: UUID
    terminal_state: WorkflowState
    terminal_reason: str
    budget_state: BudgetState
    duration_seconds: int = Field(default=0, ge=0)


class Checkpoint(BaseModel):
    """Serialized execution snapshot for resume and deterministic replay."""

    checkpoint_id: UUID = Field(default_factory=uuid4)
    workflow_run_id: UUID
    claim_id: UUID
    state: WorkflowState
    state_data: dict[str, Any] = Field(default_factory=dict)
    step_count: int = Field(default=0, ge=0)
    rework_count: int = Field(default=0, ge=0)
    budget_state: BudgetState
    recorded_tool_outputs: dict[str, dict[str, Any]] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    schema_version: str = "1.0.0"
    application_version: str = "0.1.0"
    checkpoint_type: str = "AFTER_NODE"
    parent_checkpoint_id: UUID | None = None
    checksum: str | None = None

    def compute_checksum(self) -> str:
        """Compute SHA256 checksum across deterministic fields."""
        dump_data = self.model_dump(exclude={"checksum"}, mode="json")
        serialized = json.dumps(dump_data, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def verify_integrity(self) -> bool:
        """Verify snapshot integrity against computed checksum."""
        if not self.checksum:
            return True
        return self.compute_checksum() == self.checksum

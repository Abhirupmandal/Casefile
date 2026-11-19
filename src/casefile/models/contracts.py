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
from datetime import UTC, datetime
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


class SupportedLanguage(str, Enum):
    """Supported claim document languages (Phase 12 §1)."""

    EN = "en"
    ES = "es"
    FR = "fr"
    DE = "de"


SUPPORTED_LANGUAGE_CODES: frozenset[str] = frozenset(lang.value for lang in SupportedLanguage)


def validate_language_code(value: str | SupportedLanguage | None) -> str:
    """Validate and normalize a language code. Default to 'en'."""
    if value is None:
        return SupportedLanguage.EN.value
    raw = value.value if isinstance(value, SupportedLanguage) else str(value)
    code = raw.strip().lower()
    base_code = code.split("-")[0].split("_")[0]
    if base_code not in SUPPORTED_LANGUAGE_CODES:
        raise ValueError(
            f"Unsupported language {value!r}. Supported languages: {sorted(SUPPORTED_LANGUAGE_CODES)}"
        )
    return base_code


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
    language: str = Field(default="en", description="Claim language code: en (default), es, fr, de")
    batch_id: UUID | None = Field(
        default=None, description="Optional batch ID for grouped/disaster claims"
    )
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

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        return validate_language_code(v)


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
    source_language: str = "en"
    was_translated: bool = False
    translation_provenance: dict[str, Any] = Field(default_factory=dict)
    schema_version: str = "1.0.0"


class InvestigationRequest(BaseModel):
    """Supervisor -> Investigator agent contract."""

    workflow_id: UUID
    extraction_result: ExtractionResult
    rework_feedback: str | None = None
    fraud_assessment: dict[str, Any] | None = None
    disaster_context: dict[str, Any] | None = None


class InvestigationResult(BaseModel):
    """Investigator -> Supervisor agent contract."""

    workflow_id: UUID
    policy_details: dict[str, Any] = Field(default_factory=dict)
    claim_history: dict[str, Any] = Field(default_factory=dict)
    repair_cost_validation: dict[str, Any] = Field(default_factory=dict)
    fraud_signals: dict[str, Any] = Field(default_factory=dict)
    fraud_assessment: dict[str, Any] | None = None
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
    fraud_assessment: dict[str, Any] | None = None


class CounterOfferStatus(str, Enum):
    """Lifecycle status of a structured counter-offer (Phase 12.4)."""

    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    WITHDRAWN = "WITHDRAWN"
    SUPERSEDED = "SUPERSEDED"


class CounterOfferResponseAction(str, Enum):
    """Claimant response direction for a counter-offer (Phase 12.4)."""

    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    REQUEST_REVISION = "REQUEST_REVISION"


class CounterOffer(BaseModel):
    """Structured settlement counter-offer (Phase 12 §3 & Phase 12.4).

    Proposed when repair or loss validation identifies line-item deductions.
    Advisory when generated by agents; legally binding only when authorized by HumanActor.
    """

    counter_offer_id: UUID = Field(default_factory=uuid4)
    claim_id: UUID
    workflow_run_id: UUID | None = None
    sequence_number: int = Field(default=1, ge=1, description="Offer sequence/version number")
    original_amount: Decimal = Field(..., gt=Decimal("0.00"), description="Original claimed amount")
    previously_approved_amount: Decimal | None = Field(
        default=None, description="Previously authorized partial amount if any"
    )
    proposed_amount: Decimal = Field(
        ..., gt=Decimal("0.00"), description="Proposed partial settlement"
    )
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    status: CounterOfferStatus = Field(
        default=CounterOfferStatus.PENDING, description="Current offer status"
    )
    reduction_reason: str = Field(..., min_length=1, description="Primary rationale for reduction")
    disputed_items: list[str] = Field(
        default_factory=list, description="Specific line items or charges disputed"
    )
    created_by: str = Field(default="system", description="Actor who created the offer")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = Field(
        default=None, description="Expiration timestamp for the offer"
    )
    responded_at: datetime | None = Field(
        default=None, description="Timestamp of claimant response"
    )
    response_action: CounterOfferResponseAction | None = Field(
        default=None, description="Claimant response action (ACCEPT, REJECT, REQUEST_REVISION)"
    )
    response_reason: str | None = Field(
        default=None, description="Claimant rationale or revision notes"
    )
    responded_by: str | None = Field(default=None, description="Identity of claimant who responded")
    superseded_by_offer_id: UUID | None = Field(
        default=None, description="UUID of successor offer if superseded"
    )
    schema_version: str = "1.0.0"

    @property
    def offer_id(self) -> UUID:
        """Alias for counter_offer_id."""
        return self.counter_offer_id

    @property
    def claim_amount(self) -> Decimal:
        """Alias for original_amount."""
        return self.original_amount

    @property
    def reason(self) -> str:
        """Alias for reduction_reason."""
        return self.reduction_reason

    @model_validator(mode="after")
    def _validate_counter_offer_bounds(self) -> CounterOffer:
        if self.proposed_amount > self.original_amount:
            raise ValueError(
                f"proposed_amount (${self.proposed_amount}) cannot exceed original_amount (${self.original_amount})"
            )
        p_exp = self.proposed_amount.as_tuple().exponent
        if isinstance(p_exp, int) and p_exp < -2:
            raise ValueError("proposed_amount cannot exceed 2 decimal places")
        o_exp = self.original_amount.as_tuple().exponent
        if isinstance(o_exp, int) and o_exp < -2:
            raise ValueError("original_amount cannot exceed 2 decimal places")
        if self.previously_approved_amount is not None:
            if self.previously_approved_amount <= Decimal("0.00"):
                raise ValueError("previously_approved_amount must be positive")
            if self.previously_approved_amount > self.original_amount:
                raise ValueError("previously_approved_amount cannot exceed original_amount")
            pa_exp = self.previously_approved_amount.as_tuple().exponent
            if isinstance(pa_exp, int) and pa_exp < -2:
                raise ValueError("previously_approved_amount cannot exceed 2 decimal places")
        if self.expires_at is not None:
            created = (
                self.created_at.replace(tzinfo=UTC)
                if self.created_at.tzinfo is None
                else self.created_at
            )
            expires = (
                self.expires_at.replace(tzinfo=UTC)
                if self.expires_at.tzinfo is None
                else self.expires_at
            )
            if expires <= created:
                raise ValueError("expires_at must follow created_at")
        return self


class CounterOfferResponse(BaseModel):
    """Claimant response contract for a counter-offer (Phase 12.4)."""

    model_config = {"frozen": True}

    offer_id: UUID
    claim_id: UUID
    action: CounterOfferResponseAction
    reason: str | None = None
    requested_amount: Decimal | None = None
    responded_by: str
    responded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def _validate_response_fields(self) -> CounterOfferResponse:
        if self.action == CounterOfferResponseAction.REQUEST_REVISION and (
            not self.reason or not self.reason.strip()
        ):
            raise ValueError("reason is required when requesting revision")
        if self.requested_amount is not None:
            if self.requested_amount <= Decimal("0.00"):
                raise ValueError("requested_amount must be positive")
            exp = self.requested_amount.as_tuple().exponent
            if isinstance(exp, int) and exp < -2:
                raise ValueError("requested_amount cannot exceed 2 decimal places")
        return self


class CounterOfferSettlement(BaseModel):
    """Final insurer settlement confirmation contract for an accepted counter-offer (Phase 12.4)."""

    model_config = {"frozen": True}

    offer_id: UUID
    claim_id: UUID
    workflow_run_id: UUID
    settled_amount: Decimal = Field(..., gt=Decimal("0.00"))
    settled_by: str
    settled_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    approval_id: UUID | None = None
    schema_version: str = "1.0.0"


class DisasterContext(BaseModel):
    """
    Shared catastrophic event context for grouped claim processing (Phase 12 §4).
    Enriches investigation queries while preserving individual claim budget isolation.
    """

    disaster_id: str = Field(
        ..., min_length=1, description="Unique catastrophe code, e.g. CAT-2026-04"
    )
    disaster_name: str = Field(..., min_length=1, description="Human-readable event name")
    disaster_type: str = Field(
        ..., min_length=1, description="Event classification (HURRICANE, WILDFIRE, HAIL, FLOOD)"
    )
    geographic_zone: str = Field(default="", description="Affected region or postal code prefix")
    declared_date: str | None = Field(default=None, description="ISO date of emergency declaration")
    emergency_deductible_policy: str | None = Field(
        default=None, description="Special catastrophe deductible rule"
    )
    shared_notes: str = Field(default="", description="Operational notes for adjusters")
    schema_version: str = "1.0.0"


class BatchSubmissionRequest(BaseModel):
    """
    Intake contract for bulk claim submissions (Phase 12 §4).
    Dispatches multiple claims with shared disaster context where applicable.
    """

    batch_id: UUID = Field(default_factory=uuid4)
    batch_name: str = Field(default="", description="Optional human-readable batch label")
    claims: list[ClaimInput] = Field(..., min_length=1, description="Claims to enroll in batch")
    disaster_context: DisasterContext | None = Field(
        default=None, description="Optional disaster event context applied to all claims"
    )
    submitted_by: str = Field(default="system", description="Submitting user or batch service")
    submitted_at: datetime = Field(default_factory=datetime.utcnow)
    schema_version: str = "1.0.0"

    @model_validator(mode="after")
    def _assign_batch_ids_to_claims(self) -> BatchSubmissionRequest:
        for claim in self.claims:
            if claim.batch_id is None:
                claim.batch_id = self.batch_id
        return self


class CounterfactualScenario(BaseModel):
    """
    Sensitivity explanation scenario ('what if X were different') (Phase 12 §5).
    Demonstrates minimal attribute shifts that would alter adjudication outcome.
    """

    scenario_id: UUID = Field(default_factory=uuid4)
    factor_name: str = Field(
        ..., min_length=1, description="Modified factor (e.g., repair_cost, prior_claims)"
    )
    current_value: str = Field(..., min_length=1, description="Observed factual value")
    counterfactual_value: str = Field(..., min_length=1, description="Hypothetical altered value")
    hypothetical_outcome: str = Field(
        ..., min_length=1, description="Outcome if factor had been altered"
    )
    explanation: str = Field(default="", description="Mechanism connecting factor shift to outcome")
    schema_version: str = "1.0.0"


class FactorContribution(BaseModel):
    """Specific feature/factor contribution to adjudication recommendation (Phase 12 §5)."""

    factor_name: str = Field(..., min_length=1)
    impact: Literal["INCREASES_RISK", "REDUCES_RISK", "NEUTRAL"] = "NEUTRAL"
    description: str = Field(default="")
    feature_value: str | None = None
    weight: float = Field(default=0.0, ge=-1.0, le=1.0)


class DecisionExplanation(BaseModel):
    """
    Human-readable adjudication brief with evidentiary citations and counterfactuals (Phase 12 §5).
    """

    explanation_id: UUID = Field(default_factory=uuid4)
    workflow_run_id: UUID
    claim_id: UUID
    summary: str = Field(
        ..., min_length=1, description="Plain-language executive summary of adjudication"
    )
    recommendation: str = Field(
        default="APPROVE",
        description="Recommended outcome (APPROVE, APPROVE_PARTIAL, REJECT, REWORK)",
    )
    confidence_score: float = Field(default=1.0, ge=0.0, le=1.0)
    key_factors: list[str] = Field(
        default_factory=list, description="Primary drivers of recommendation"
    )
    positive_factors: list[str] = Field(
        default_factory=list, description="Factors reducing risk or supporting approval"
    )
    negative_factors: list[str] = Field(
        default_factory=list, description="Factors increasing risk or supporting rejection/rework"
    )
    factor_contributions: list[FactorContribution] = Field(
        default_factory=list, description="Itemized feature contributions"
    )
    evidence_citations: list[str] = Field(
        default_factory=list, description="Specific document, policy, or estimate references cited"
    )
    counterfactuals: list[CounterfactualScenario] = Field(
        default_factory=list, description="Computed sensitivity scenarios"
    )
    model_name: str = "casefile-explanation-engine"
    model_version: str = "1.0.0"
    assessment_id: UUID | None = None
    missing_or_uncertain_information: list[str] = Field(
        default_factory=list, description="Gaps, missing documents, or unverified items"
    )
    limitations: list[str] = Field(
        default_factory=list, description="Explicit boundaries and disclaimer of explanation"
    )
    is_claimant_safe: bool = Field(
        default=False, description="True if sanitized for claimant presentation"
    )
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    schema_version: str = "1.0.0"

    def to_claimant_view(self) -> DecisionExplanation:
        """Produce a sanitized claimant-safe view omitting internal weights and security thresholds."""
        safe_counterfactuals = [
            sc.model_copy()
            for sc in self.counterfactuals
            if not any(
                k in sc.factor_name.lower()
                for k in ("fraud", "blacklist", "database", "anomaly_score")
            )
        ]
        safe_citations = [
            c
            for c in self.evidence_citations
            if not any(k in c.lower() for k in ("blacklist", "secret", "internal_rule"))
        ]
        return DecisionExplanation(
            explanation_id=self.explanation_id,
            workflow_run_id=self.workflow_run_id,
            claim_id=self.claim_id,
            summary=self.summary,
            recommendation=self.recommendation,
            confidence_score=self.confidence_score,
            key_factors=self.key_factors[:5],
            positive_factors=self.positive_factors[:3],
            negative_factors=self.negative_factors[:3],
            factor_contributions=[],  # Redacted: internal mathematical weights
            evidence_citations=safe_citations,
            counterfactuals=safe_counterfactuals,
            model_name="adjudication-explanation-service",
            model_version="1.0",
            assessment_id=None,  # Redacted
            missing_or_uncertain_information=self.missing_or_uncertain_information,
            limitations=[
                "This explanation is advisory and summarizes evidentiary findings.",
                "Hypothetical counterfactual scenarios do not constitute eligibility or guaranteed payout.",
            ],
            is_claimant_safe=True,
            generated_at=self.generated_at,
            schema_version=self.schema_version,
        )


class ApprovalFeedback(BaseModel):
    """
    Structured human adjuster feedback for active learning and prompt refinement (Phase 12 §6).
    Guaranteed PII-sanitized before retention.
    """

    feedback_id: UUID = Field(default_factory=uuid4)
    approval_id: UUID
    claim_id: UUID
    workflow_run_id: UUID | None = None
    actor_id: str = Field(..., min_length=1, description="Adjuster identity who provided feedback")
    category: str = Field(
        ...,
        min_length=1,
        description="Feedback category (e.g. EVIDENCE_DISCREPANCY, POLICY_INTERPRETATION)",
    )
    adjuster_notes: str = Field(
        ..., min_length=1, description="Adjuster reasoning and correction details"
    )
    overridden_recommendation: str | None = Field(
        default=None, description="Original agent recommendation if overridden"
    )
    sanitized_sample: dict[str, Any] | None = Field(
        default=None, description="PII-scrubbed context exemplar for training"
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    schema_version: str = "1.0.0"


class ReviewResult(BaseModel):
    """Reviewer -> Supervisor agent contract."""

    workflow_id: UUID
    decision: Literal["APPROVE", "APPROVE_PARTIAL", "REJECT", "REWORK"]
    reasoning: str
    confidence_score: float = Field(ge=0.0, le=1.0)
    evidence_completeness: float = Field(default=1.0, ge=0.0, le=1.0)
    identified_gaps: list[str] = Field(default_factory=list)
    rework_feedback: str | None = None
    fraud_risk_level: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "LOW"
    fraud_signals_detected: bool = False
    counter_offer: CounterOffer | None = None
    explanation_brief: DecisionExplanation | None = None
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

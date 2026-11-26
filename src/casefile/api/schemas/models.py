"""
API Schema Catalog (Phase 7).

Strongly typed Pydantic models for REST request and response contracts.
Never exposes internal SQLAlchemy ORM entities or raw database rows.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


# ======================================================================
# Claim Schemas
# ======================================================================
class ClaimSubmissionRequest(BaseModel):
    """Client request to submit an insurance claim."""

    policy_id: str = Field(
        ..., min_length=1, max_length=100, description="Insurance policy number / reference"
    )
    claimant_name: str = Field(
        ..., min_length=1, max_length=200, description="Full legal name of claimant"
    )
    incident_date: str = Field(
        ..., min_length=4, max_length=50, description="Date of incident (YYYY-MM-DD)"
    )
    claim_amount: Decimal = Field(
        ..., gt=0, le=Decimal("100000000.00"), description="Monetary claim amount"
    )
    description: str = Field(
        ..., min_length=5, max_length=10_000, description="Factual description of the incident"
    )
    documents: list[str] = Field(
        default_factory=list, max_length=50, description="List of submitted document identifiers"
    )
    claim_id: str | None = Field(
        default=None, max_length=100, description="Optional external or pre-assigned claim ID"
    )
    language: str = Field(default="en", description="Claim language code: en (default), es, fr, de")
    batch_id: str | None = Field(
        default=None, max_length=100, description="Optional batch identifier"
    )


class ClaimSubmissionResponse(BaseModel):
    """Immediate acknowledgment returned upon claim intake."""

    claim_id: str = Field(..., description="Unique claim identifier")
    workflow_run_id: str = Field(..., description="Assigned workflow run identifier")
    current_state: str = Field(..., description="Current state of workflow execution")
    created_at: str = Field(..., description="Intake timestamp (ISO 8601)")
    status_url: str = Field(..., description="URL to poll workflow execution status")
    correlation_id: str = Field(..., description="Tracing correlation identifier")


# ======================================================================
# Workflow Schemas
# ======================================================================
class BudgetSummary(BaseModel):
    """Sanitized budget consumption metrics for a workflow run."""

    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    estimated_cost_usd: Decimal = Field(default=Decimal("0.0000"))
    max_cost_usd: Decimal | None = Field(
        default=None, description="Envelope ceiling; null when unrecorded"
    )
    max_steps: int | None = Field(
        default=None, description="Envelope ceiling; null when unrecorded"
    )
    steps_used: int = Field(default=0)


class WorkflowStatusResponse(BaseModel):
    """Operational status and details of a workflow run."""

    workflow_run_id: str
    claim_id: str
    current_state: str
    is_terminal: bool
    terminal_reason: str | None = None
    created_at: str
    updated_at: str
    current_step: int
    step_count: int
    rework_count: int
    retry_count: int
    budget_usage: BudgetSummary
    estimated_cost: Decimal
    pending_approval: bool
    checkpoint_count: int
    correlation_id: str


class StateTransitionItem(BaseModel):
    """One immutable transition in the workflow history."""

    sequence: int
    from_state: str | None = None
    to_state: str
    reason: str
    timestamp: str
    correlation_id: str
    execution_id: str | None = None
    actor: str | None = None


class WorkflowHistoryResponse(BaseModel):
    """Append-only audit history of workflow state transitions."""

    workflow_run_id: str
    transitions: list[StateTransitionItem]


class WorkflowSummaryItem(BaseModel):
    """Compact summary for list/table rendering."""

    workflow_run_id: str
    claim_id: str
    current_state: str
    is_terminal: bool
    step_count: int
    created_at: str
    updated_at: str


class WorkflowListResponse(BaseModel):
    workflows: list[WorkflowSummaryItem]
    total: int


# ======================================================================
# Approval Schemas
# ======================================================================
class ApprovalItemResponse(BaseModel):
    """Pending or decided human-in-the-loop approval item."""

    approval_id: str
    workflow_run_id: str
    claim_id: str
    status: str
    requested_by: str
    requested_at: str
    deadline: str
    required_role: str
    recommendation_summary: str
    estimated_payout: Decimal | None = None
    confidence: float | None = None
    approver_id: str | None = None
    approver_role: str | None = None
    decided_at: str | None = None
    decision_reason: str | None = None


class ApprovalListResponse(BaseModel):
    approvals: list[ApprovalItemResponse]
    total: int


class ApprovalDecisionRequest(BaseModel):
    """Operator decision submission."""

    decision: str = Field(
        ...,
        min_length=3,
        max_length=20,
        description="APPROVE, APPROVED, REJECT, REJECTED, or APPROVE_PARTIAL",
    )
    reason: str = Field(
        default="", max_length=2_000, description="Operator rationale for the decision"
    )
    approved_amount: Decimal | None = Field(
        default=None, description="Optional authorized payout amount for APPROVE / APPROVE_PARTIAL"
    )


class ApprovalDecisionResponse(BaseModel):
    """Result of an applied human decision."""

    approval_id: str
    workflow_run_id: str
    status: str
    outcome: str
    verdict: str | None = None
    decided_by: str
    decided_at: str
    decision_reason: str
    resumed_workflow_state: str | None = None
    approved_amount: Decimal | None = None


# ======================================================================
# Checkpoint & Replay Schemas
# ======================================================================
class CheckpointMetadataResponse(BaseModel):
    checkpoint_id: str
    workflow_run_id: str
    claim_id: str
    sequence_no: int
    state: str
    kind: str
    created_at: str
    integrity_valid: bool
    resumable: bool
    step_count: int
    rework_count: int


class CheckpointListResponse(BaseModel):
    workflow_run_id: str
    checkpoints: list[CheckpointMetadataResponse]


class ReplayRequest(BaseModel):
    checkpoint_id: str | None = Field(default=None, max_length=100)


class ReplayResponse(BaseModel):
    replay_id: str
    original_workflow_run_id: str
    replay_status: str
    original_terminal_state: str | None = None
    replay_terminal_state: str | None = None
    path_matched: bool
    deterministic_match: bool
    is_simulation: bool = True
    message: str = "Deterministic simulation replay complete"
    details: dict[str, Any] | None = None


# ======================================================================
# Agent & Tool Visibility Schemas
# ======================================================================
class AgentExecutionResponse(BaseModel):
    execution_id: str
    agent_type: str
    status: str
    started_at: str
    completed_at: str | None = None
    duration_ms: int
    retry_count: int
    failure_classification: str | None = None
    tokens_summary: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str


class AgentExecutionListResponse(BaseModel):
    workflow_run_id: str
    executions: list[AgentExecutionResponse]


class ToolInvocationResponse(BaseModel):
    invocation_id: str
    tool_name: str
    tool_version: str
    status: str
    requesting_agent: str
    started_at: str
    finished_at: str | None = None
    duration_ms: int
    failure_type: str | None = None
    sanitized_input_summary: str
    sanitized_output_summary: str
    correlation_id: str


class ToolInvocationListResponse(BaseModel):
    workflow_run_id: str
    invocations: list[ToolInvocationResponse]


# ======================================================================
# Evaluation & Audit Schemas
# ======================================================================
class EvaluationCaseSummary(BaseModel):
    case_id: str
    scenario_name: str
    category: str
    status: str
    expected_terminal_state: str
    terminal_state: str
    path_matched: bool
    terminal_matched: bool
    cost_usd: str
    steps: int


class EvaluationSummaryResponse(BaseModel):
    total_cases: int
    passed: int
    failed: int
    pass_rate: float
    path_accuracy: float
    terminal_state_accuracy: float
    replay_determinism_rate: float
    budget_enforcement_rate: float
    approval_safety_rate: float
    failure_containment_rate: float
    average_cost: str
    average_steps: float
    cases: list[EvaluationCaseSummary] = Field(default_factory=list)


class AuditEventResponse(BaseModel):
    event_id: str
    timestamp: str
    event_type: str
    workflow_run_id: str | None = None
    claim_id: str | None = None
    actor_id: str
    action: str
    result: str
    correlation_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class AuditListResponse(BaseModel):
    events: list[AuditEventResponse]
    total: int


# ======================================================================
# Health & Operations Metrics Schemas
# ======================================================================
class HealthResponse(BaseModel):
    status: str
    service: str
    environment: str
    version: str


class ReadinessResponse(BaseModel):
    ready: bool
    status: str
    services: dict[str, Any]


class OperationalMetricsResponse(BaseModel):
    active_workflows: int
    completed_workflows: int
    failed_workflows: int
    pending_approvals: int
    budget_exhausted_workflows: int
    average_duration_seconds: float
    average_cost_usd: float
    recent_failures_count: int
    evaluation_pass_rate: float
    system_health: str


# ======================================================================
# Counter-Offer & Negotiation Schemas (Phase 12.4)
# ======================================================================
class CreateCounterOfferRequest(BaseModel):
    """Insurer reviewer request to create a counter-offer."""

    proposed_amount: Decimal = Field(
        ...,
        gt=Decimal("0.00"),
        description="Proposed partial settlement amount",
    )
    reason: str = Field(
        ...,
        min_length=1,
        max_length=2_000,
        description="Reviewer explanation and justification for reduction",
    )
    disputed_items: list[str] = Field(
        default_factory=list,
        description="Line items or specific costs disputed by insurer",
    )
    expires_in_days: int = Field(
        default=7,
        ge=1,
        le=90,
        description="Counter-offer validity period in days",
    )
    workflow_run_id: str | None = Field(
        default=None,
        description="Optional workflow run ID associated with the claim",
    )


class CounterOfferResponseRequest(BaseModel):
    """Claimant response to an insurer counter-offer."""

    action: str = Field(
        ...,
        description="Response action: ACCEPT, REJECT, or REQUEST_REVISION",
    )
    reason: str | None = Field(
        default=None,
        max_length=2_000,
        description="Optional claimant notes or rationale for revision request",
    )
    requested_amount: Decimal | None = Field(
        default=None,
        gt=Decimal("0.00"),
        description="Optional revised amount proposed by claimant",
    )


class CounterOfferItemResponse(BaseModel):
    """Counter-offer view representation."""

    offer_id: str
    claim_id: str
    workflow_run_id: str | None = None
    sequence_number: int
    original_amount: Decimal
    previously_approved_amount: Decimal | None = None
    proposed_amount: Decimal
    currency: str
    status: str
    reason: str
    disputed_items: list[str]
    created_by: str
    created_at: str
    expires_at: str
    responded_at: str | None = None
    response_action: str | None = None
    response_reason: str | None = None
    responded_by: str | None = None
    superseded_by_offer_id: str | None = None


class CounterOfferListResponse(BaseModel):
    """List of all counter-offers for a claim."""

    claim_id: str
    offers: list[CounterOfferItemResponse]
    total: int


class CounterOfferSettlementResponse(BaseModel):
    """Result of insurer settlement authorization."""

    offer_id: str
    claim_id: str
    workflow_run_id: str
    settled_amount: Decimal
    status: str
    settled_by: str
    settled_at: str
    approval_id: str | None = None


class FactorContributionResponse(BaseModel):
    factor_name: str
    impact: str
    description: str
    feature_value: str | None = None
    weight: float | None = None


class CounterfactualScenarioResponse(BaseModel):
    scenario_id: str
    factor_name: str
    current_value: str
    counterfactual_value: str
    hypothetical_outcome: str
    explanation: str


class DecisionExplanationResponse(BaseModel):
    explanation_id: str
    workflow_run_id: str
    claim_id: str
    summary: str
    recommendation: str
    confidence_score: float
    key_factors: list[str]
    positive_factors: list[str]
    negative_factors: list[str]
    factor_contributions: list[FactorContributionResponse]
    evidence_citations: list[str]
    counterfactuals: list[CounterfactualScenarioResponse]
    missing_or_uncertain_information: list[str]
    limitations: list[str]
    is_claimant_safe: bool
    model_name: str
    model_version: str
    assessment_id: str | None = None
    generated_at: str


# ======================================================================
# Batch Processing & Disaster Scenario Schemas (Phase 12 §4, Phase 12.6)
# ======================================================================


class DisasterContextSchema(BaseModel):
    """Disaster/catastrophe event context for batch processing."""

    disaster_id: str = Field(
        ..., min_length=1, max_length=64, description="Unique catastrophe code, e.g. CAT-2026-04"
    )
    disaster_name: str = Field(
        ..., min_length=1, max_length=128, description="Human-readable event name"
    )
    disaster_type: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Event classification (HURRICANE, WILDFIRE, HAIL, FLOOD)",
    )
    geographic_zone: str = Field(
        default="", max_length=128, description="Affected region or postal code prefix"
    )
    declared_date: str | None = Field(
        default=None, max_length=32, description="ISO date of emergency declaration"
    )
    emergency_deductible_policy: str | None = Field(
        default=None, max_length=256, description="Special catastrophe deductible rule"
    )
    shared_notes: str = Field(
        default="", max_length=1000, description="Operational notes for adjusters"
    )


class BatchClaimSubmissionItem(BaseModel):
    """Claim intake item within a batch submission."""

    policy_id: str = Field(
        ..., min_length=3, max_length=50, description="Insurance policy identifier"
    )
    claimant_name: str = Field(
        ..., min_length=2, max_length=200, description="Full name of claimant"
    )
    incident_date: str = Field(..., max_length=32, description="ISO 8601 date of incident")
    claim_amount: Decimal = Field(
        ..., gt=0, le=Decimal("100000000.00"), description="Monetary claim amount"
    )
    description: str = Field(
        ..., min_length=5, max_length=10000, description="Factual incident description"
    )
    documents: list[str] = Field(
        default_factory=list, max_length=50, description="Submitted document identifiers"
    )
    claim_id: str | None = Field(
        default=None, max_length=100, description="Optional pre-assigned claim ID"
    )
    language: str = Field(
        default="en", max_length=10, description="Claim language code: en, es, fr, de"
    )


class BatchClaimSubmissionRequest(BaseModel):
    """Request payload for enrolling multiple claims in a batch."""

    batch_name: str = Field(
        default="", max_length=128, description="Optional human-readable batch label"
    )
    claims: list[BatchClaimSubmissionItem] = Field(
        ..., min_length=1, description="List of claims to process in batch"
    )
    disaster_context: DisasterContextSchema | None = Field(
        default=None, description="Optional catastrophic event context"
    )
    batch_id: str | None = Field(
        default=None, max_length=100, description="Optional pre-assigned batch ID"
    )


class BatchClaimItemResponse(BaseModel):
    """Execution status and outcome for an individual claim in a batch."""

    claim_id: str = Field(..., description="Unique claim identifier")
    workflow_run_id: str | None = Field(
        default=None, description="Assigned workflow run identifier"
    )
    policy_id: str = Field(..., description="Insurance policy identifier")
    claimant_name: str = Field(..., description="Name of claimant")
    claim_amount: Decimal = Field(..., description="Authoritative claim amount")
    status: str = Field(
        ..., description="Item execution status: PENDING, PROCESSING, COMPLETED, FAILED"
    )
    current_state: str | None = Field(
        default=None, description="Current workflow state, e.g. HUMAN_APPROVAL"
    )
    approved_amount: Decimal | None = Field(
        default=None, description="Authorized settlement amount if decided"
    )
    error_message: str | None = Field(default=None, description="Error reason if item failed")
    retry_count: int = Field(default=0, description="Number of execution retry attempts")
    duration_seconds: float = Field(default=0.0, description="Execution duration in seconds")


class BatchStatusResponse(BaseModel):
    """Batch progress, execution state, and aggregate financial totals."""

    batch_id: str = Field(..., description="Unique batch identifier")
    batch_name: str = Field(default="", description="Batch label")
    status: str = Field(
        ...,
        description="Overall status: PENDING, PROCESSING, COMPLETED, PARTIALLY_COMPLETED, FAILED",
    )
    total_claims: int = Field(..., description="Total claims enrolled in batch")
    processed_claims: int = Field(default=0, description="Number of processed claims")
    successful_claims: int = Field(default=0, description="Number of successfully processed claims")
    failed_claims: int = Field(default=0, description="Number of failed claims")
    total_requested_amount: Decimal = Field(
        default=Decimal("0.00"), description="Sum of authoritative claim amounts"
    )
    total_approved_amount: Decimal = Field(
        default=Decimal("0.00"), description="Sum of human-approved settlement amounts"
    )
    disaster_context: DisasterContextSchema | None = Field(
        default=None, description="Catastrophe context if disaster batch"
    )
    claims: list[BatchClaimItemResponse] = Field(
        default_factory=list, description="Itemized claim execution records"
    )
    submitted_by: str = Field(default="system", description="Submitting user or service")
    created_at: str = Field(..., description="Intake timestamp (ISO 8601)")
    updated_at: str = Field(..., description="Last update timestamp (ISO 8601)")
    completed_at: str | None = Field(default=None, description="Completion timestamp if finished")


class BatchClaimsListResponse(BaseModel):
    """Response containing claims belonging to a batch."""

    batch_id: str = Field(..., description="Batch identifier")
    total: int = Field(..., description="Total claim items returned")
    items: list[BatchClaimItemResponse] = Field(..., description="Claim items list")


class BatchDisasterResponse(BaseModel):
    """Disaster context and association summary for a batch."""

    batch_id: str = Field(..., description="Batch identifier")
    disaster_context: DisasterContextSchema | None = Field(
        default=None, description="Catastrophe context"
    )
    claims_count: int = Field(default=0, description="Total claims linked to this disaster batch")
    total_requested_amount: Decimal = Field(
        default=Decimal("0.00"), description="Total requested amount across claims"
    )


class BatchRetryRequest(BaseModel):
    """Request payload to retry specific failed claims in a batch."""

    claim_ids: list[str] | None = Field(
        default=None,
        description="Optional specific failed claim IDs to retry. If omitted, retries all failed claims.",
    )

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
    max_cost_usd: Decimal | None = Field(default=None)
    max_steps: int = Field(default=50)
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
        ..., min_length=3, max_length=20, description="APPROVE, APPROVED, REJECT, or REJECTED"
    )
    reason: str = Field(
        default="", max_length=2_000, description="Operator rationale for the decision"
    )


class ApprovalDecisionResponse(BaseModel):
    """Result of an applied human decision."""

    approval_id: str
    workflow_run_id: str
    status: str
    outcome: str
    decided_by: str
    decided_at: str
    decision_reason: str
    resumed_workflow_state: str | None = None


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

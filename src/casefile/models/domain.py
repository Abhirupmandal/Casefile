"""
CASEFILE Phase 2 domain entities and typed contracts.

Fully typed Pydantic v2 models for the domain concepts defined in
docs/agent-contracts.md. These models layer alongside the frozen Phase 1
contracts in `contracts.py` (which must remain byte-identical for the
Phase 1 gate) and provide the rich, validated vocabulary that agents,
persistence, and the future API build on.

Rules enforced here:
- No floating-point money (Money uses Decimal + explicit ISO-4217 currency)
- No free-form dict payloads (structured fields everywhere)
- Confidence scores bounded to [0.0, 1.0]
- UUID identifiers for all distributed workflow concepts
- Every model carries a validated semver `schema_version`
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from casefile.models.contracts import BudgetState, TokenUsage, WorkflowState
from casefile.models.versioning import SchemaVersion


def _utcnow() -> datetime:
    """Timezone-aware UTC timestamp factory."""
    return datetime.now(UTC)


def _non_empty(value: str, field_name: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} cannot be empty")
    return stripped


class ClaimStatus(str, Enum):
    """Lifecycle status of a claim (distinct from WorkflowState)."""

    SUBMITTED = "SUBMITTED"
    ACCEPTED = "ACCEPTED"
    UNDER_INVESTIGATION = "UNDER_INVESTIGATION"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    CLOSED = "CLOSED"


class ClaimType(str, Enum):
    """Insurance claim coverage type (docs/agent-contracts.md)."""

    COLLISION = "COLLISION"
    COMPREHENSIVE = "COMPREHENSIVE"
    LIABILITY = "LIABILITY"
    UNINSURED_MOTORIST = "UNINSURED_MOTORIST"
    MEDICAL_PAYMENTS = "MEDICAL_PAYMENTS"
    PERSONAL_INJURY = "PERSONAL_INJURY"


class ClaimPriority(str, Enum):
    """Claim handling priority."""

    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"


class AgentType(str, Enum):
    """Participants allowed on the contract envelope."""

    SUPERVISOR = "SUPERVISOR"
    EXTRACTOR = "EXTRACTOR"
    INVESTIGATOR = "INVESTIGATOR"
    REVIEWER = "REVIEWER"
    HUMAN = "HUMAN"
    SYSTEM = "SYSTEM"


class ExecutionStatus(str, Enum):
    """Outcome of a single agent execution."""

    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILURE = "FAILURE"
    TIMEOUT = "TIMEOUT"


class ApprovalStatus(str, Enum):
    """State of a human approval request."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class RecommendationType(str, Enum):
    """Reviewer recommendation vocabulary (docs/agent-contracts.md)."""

    APPROVE_FULL = "APPROVE_FULL"
    APPROVE_PARTIAL = "APPROVE_PARTIAL"
    APPROVE_WITH_CONDITIONS = "APPROVE_WITH_CONDITIONS"
    DENY = "DENY"
    INVESTIGATE_FURTHER = "INVESTIGATE_FURTHER"


class RiskLevel(str, Enum):
    """Fraud risk level."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class FraudSeverity(str, Enum):
    """Severity of a single fraud indicator."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class FocusArea(str, Enum):
    """Rework focus areas (docs/agent-contracts.md)."""

    POLICY_COVERAGE = "POLICY_COVERAGE"
    CLAIM_HISTORY = "CLAIM_HISTORY"
    REPAIR_COSTS = "REPAIR_COSTS"
    FRAUD_INDICATORS = "FRAUD_INDICATORS"
    DAMAGE_ASSESSMENT = "DAMAGE_ASSESSMENT"
    WITNESS_STATEMENTS = "WITNESS_STATEMENTS"
    ACCIDENT_RECONSTRUCTION = "ACCIDENT_RECONSTRUCTION"


class ReworkPriority(str, Enum):
    """Rework request priority."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class DocumentType(str, Enum):
    """Claim document type (docs/agent-contracts.md)."""

    POLICE_REPORT = "POLICE_REPORT"
    ACCIDENT_REPORT = "ACCIDENT_REPORT"
    PHOTO_VEHICLE = "PHOTO_VEHICLE"
    PHOTO_SCENE = "PHOTO_SCENE"
    REPAIR_ESTIMATE = "REPAIR_ESTIMATE"
    REPAIR_INVOICE = "REPAIR_INVOICE"
    MEDICAL_RECORD = "MEDICAL_RECORD"
    STATEMENT_DRIVER = "STATEMENT_DRIVER"
    STATEMENT_WITNESS = "STATEMENT_WITNESS"
    INSPECTION_REPORT = "INSPECTION_REPORT"
    OTHER = "OTHER"


class DocumentContentType(str, Enum):
    """Document encoding/content kind."""

    PDF = "PDF"
    IMAGE_JPEG = "IMAGE_JPEG"
    IMAGE_PNG = "IMAGE_PNG"
    TEXT = "TEXT"
    JSON = "JSON"


class DocumentExtractionStatus(str, Enum):
    """Extraction lifecycle of a single document."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ActorType(str, Enum):
    """Audit event actor category."""

    SYSTEM = "SYSTEM"
    AGENT = "AGENT"
    TOOL = "TOOL"
    HUMAN = "HUMAN"


class AuditResult(str, Enum):
    """Audit event outcome."""

    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    TIMEOUT = "TIMEOUT"


class EvidenceSourceType(str, Enum):
    """Provenance category for a normalized evidence item."""

    DOCUMENT = "DOCUMENT"
    POLICY_RECORD = "POLICY_RECORD"
    ESTIMATE_RECORD = "ESTIMATE_RECORD"
    HISTORY_RECORD = "HISTORY_RECORD"
    FRAUD_PROFILE = "FRAUD_PROFILE"


class EvidenceItem(BaseModel):
    """One normalized evidence item with full provenance."""

    model_config = {"frozen": True}

    evidence_id: UUID
    source: str
    source_type: EvidenceSourceType
    claim_ref: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    value: str
    provenance: str
    collected_at: datetime = Field(default_factory=_utcnow)
    version: str = "1.0"


class AuditEventType(str, Enum):
    """Audit event vocabulary (docs/agent-contracts.md)."""

    WORKFLOW_STARTED = "WORKFLOW_STARTED"
    WORKFLOW_COMPLETED = "WORKFLOW_COMPLETED"
    STATE_TRANSITION = "STATE_TRANSITION"
    NODE_EXECUTION_STARTED = "NODE_EXECUTION_STARTED"
    NODE_EXECUTION_COMPLETED = "NODE_EXECUTION_COMPLETED"
    NODE_EXECUTION_FAILED = "NODE_EXECUTION_FAILED"
    TOOL_CALL_STARTED = "TOOL_CALL_STARTED"
    TOOL_CALL_COMPLETED = "TOOL_CALL_COMPLETED"
    TOOL_CALL_FAILED = "TOOL_CALL_FAILED"
    LLM_CALL_STARTED = "LLM_CALL_STARTED"
    LLM_CALL_COMPLETED = "LLM_CALL_COMPLETED"
    LLM_CALL_FAILED = "LLM_CALL_FAILED"
    CHECKPOINT_CREATED = "CHECKPOINT_CREATED"
    CHECKPOINT_LOADED = "CHECKPOINT_LOADED"
    CHECKPOINT_RESTORED = "CHECKPOINT_RESTORED"
    REPLAY_STARTED = "REPLAY_STARTED"
    REPLAY_COMPLETED = "REPLAY_COMPLETED"
    REPLAY_MISMATCH = "REPLAY_MISMATCH"
    HUMAN_APPROVAL_REQUESTED = "HUMAN_APPROVAL_REQUESTED"
    HUMAN_APPROVAL_RECEIVED = "HUMAN_APPROVAL_RECEIVED"
    HUMAN_APPROVAL_EXPIRED = "HUMAN_APPROVAL_EXPIRED"
    HUMAN_APPROVAL_CANCELLED = "HUMAN_APPROVAL_CANCELLED"
    BUDGET_EVALUATED = "BUDGET_EVALUATED"
    BUDGET_WARNING = "BUDGET_WARNING"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    STEP_LIMIT_REACHED = "STEP_LIMIT_REACHED"
    TERMINATION_DECISION = "TERMINATION_DECISION"
    ERROR_OCCURRED = "ERROR_OCCURRED"
    RETRY_ATTEMPTED = "RETRY_ATTEMPTED"


class Money(BaseModel):
    """Precise monetary value. Never float; currency always explicit."""

    model_config = {"frozen": True}

    amount: Decimal = Field(..., ge=Decimal("0.00"))
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")


class Address(BaseModel):
    """Postal address value object."""

    model_config = {"frozen": True}

    street: str
    city: str
    state: str
    zip_code: str
    country: str = "USA"

    @field_validator("street", "city", "state", "zip_code")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        return _non_empty(v, "address field")


class ClaimDocumentReference(BaseModel):
    """Metadata for an untrusted input document. Never carries raw content,
    keeping handoff contracts bounded and replay-friendly."""

    model_config = {"frozen": True}

    document_id: UUID = Field(default_factory=uuid4)
    document_type: DocumentType
    filename: str | None = None
    content_type: DocumentContentType = DocumentContentType.PDF
    content_hash: str = Field(..., min_length=8)
    source: str
    ingested_at: datetime = Field(default_factory=_utcnow)
    extraction_status: DocumentExtractionStatus = DocumentExtractionStatus.PENDING
    trusted_source: bool = False

    @field_validator("source")
    @classmethod
    def _validate_source(cls, v: str) -> str:
        return _non_empty(v, "source")


class Claim(BaseModel):
    """Core claim entity."""

    claim_id: UUID = Field(default_factory=uuid4)
    external_claim_id: str
    policy_number: str
    customer_id: str
    claim_type: ClaimType
    status: ClaimStatus = ClaimStatus.SUBMITTED
    date_of_loss: date
    date_reported: date
    description: str
    claim_amount: Money
    documents: list[ClaimDocumentReference] = Field(default_factory=list)
    priority: ClaimPriority = ClaimPriority.NORMAL
    submitted_at: datetime = Field(default_factory=_utcnow)
    submitted_by: str = "system"
    schema_version: SchemaVersion = "1.0.0"

    @field_validator("external_claim_id", "customer_id", "description")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        return _non_empty(v, "claim field")

    @field_validator("policy_number")
    @classmethod
    def _validate_policy_number(cls, v: str) -> str:
        return _non_empty(v, "policy_number").upper()

    @model_validator(mode="after")
    def _validate_dates(self) -> Claim:
        if self.date_reported < self.date_of_loss:
            raise ValueError("date_reported cannot precede date_of_loss")
        return self


class CoverageLimits(BaseModel):
    """Policy coverage limits. per-accident must cover per-person."""

    model_config = {"frozen": True}

    bodily_injury_per_person: Decimal = Field(..., ge=Decimal("0.00"))
    bodily_injury_per_accident: Decimal = Field(..., ge=Decimal("0.00"))
    property_damage: Decimal = Field(..., ge=Decimal("0.00"))
    collision_deductible: Decimal | None = Field(default=None, ge=Decimal("0.00"))
    comprehensive_deductible: Decimal | None = Field(default=None, ge=Decimal("0.00"))

    @model_validator(mode="after")
    def _validate_consistency(self) -> CoverageLimits:
        if self.bodily_injury_per_accident < self.bodily_injury_per_person:
            raise ValueError("bodily_injury_per_accident must cover bodily_injury_per_person")
        return self


class Policy(BaseModel):
    """Policy information and limits."""

    policy_number: str
    policyholder_name: str
    policy_type: str
    effective_date: date
    expiration_date: date
    coverage: CoverageLimits
    exclusions: list[str] = Field(default_factory=list)
    is_active: bool = True
    schema_version: SchemaVersion = "1.0.0"

    @field_validator("policy_number", "policyholder_name", "policy_type")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        return _non_empty(v, "policy field")

    @model_validator(mode="after")
    def _validate_dates(self) -> Policy:
        if self.expiration_date <= self.effective_date:
            raise ValueError("expiration_date must follow effective_date")
        return self


class PriorClaim(BaseModel):
    """A single prior claim on a policy/customer history."""

    model_config = {"frozen": True}

    claim_reference: str
    claim_date: date
    claim_type: ClaimType
    amount: Money
    status: str
    at_fault: bool | None = None

    @field_validator("claim_reference", "status")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        return _non_empty(v, "prior claim field")


class DamageLineItem(BaseModel):
    """One line of a damage/repair estimate with a consistency rule."""

    model_config = {"frozen": True}

    description: str
    quantity: int = Field(..., ge=1)
    unit_cost: Money
    total_cost: Money

    @field_validator("description")
    @classmethod
    def _validate_description(cls, v: str) -> str:
        return _non_empty(v, "description")

    @model_validator(mode="after")
    def _validate_total(self) -> DamageLineItem:
        if self.unit_cost.currency != self.total_cost.currency:
            raise ValueError("line item currencies must match")
        if self.total_cost.amount != self.unit_cost.amount * self.quantity:
            raise ValueError("total_cost must equal unit_cost * quantity")
        return self


class DamageEstimate(BaseModel):
    """A repair/damage estimate with totals reconciled to line items."""

    estimate_id: UUID = Field(default_factory=uuid4)
    source: str
    estimate_date: date
    line_items: list[DamageLineItem] = Field(..., min_length=1)
    labor_hours: float = Field(default=0.0, ge=0.0)
    total_cost: Money
    schema_version: SchemaVersion = "1.0.0"

    @field_validator("source")
    @classmethod
    def _validate_source(cls, v: str) -> str:
        return _non_empty(v, "source")

    @model_validator(mode="after")
    def _validate_totals(self) -> DamageEstimate:
        currencies = {item.total_cost.currency for item in self.line_items}
        currencies.add(self.total_cost.currency)
        if len(currencies) != 1:
            raise ValueError("all estimate amounts must share one currency")
        item_total = sum((item.total_cost.amount for item in self.line_items), Decimal("0.00"))
        if item_total != self.total_cost.amount:
            raise ValueError("total_cost must equal the sum of line item totals")
        return self


class FraudIndicator(BaseModel):
    """A single typed fraud signal."""

    model_config = {"frozen": True}

    indicator_type: str
    description: str
    severity: FraudSeverity
    detected_at: datetime = Field(default_factory=_utcnow)

    @field_validator("indicator_type", "description")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        return _non_empty(v, "fraud indicator field")


class CostAssessment(BaseModel):
    """Repair-cost validation against an expected range."""

    model_config = {"frozen": True}

    submitted: Money
    submitted_source: str
    expected_low: Money
    expected_high: Money
    is_within_expected_range: bool
    variance_pct: float

    @field_validator("submitted_source")
    @classmethod
    def _validate_source(cls, v: str) -> str:
        return _non_empty(v, "submitted_source")

    @model_validator(mode="after")
    def _validate_range(self) -> CostAssessment:
        for field_name in ("submitted", "expected_low", "expected_high"):
            if getattr(self, field_name).currency != self.submitted.currency:
                raise ValueError("all cost assessment amounts must share one currency")
        if self.expected_low.amount > self.expected_high.amount:
            raise ValueError("expected_low cannot exceed expected_high")
        in_range = self.expected_low.amount <= self.submitted.amount <= self.expected_high.amount
        if in_range != self.is_within_expected_range:
            raise ValueError("is_within_expected_range contradicts the expected range")
        return self


class InvestigationFindings(BaseModel):
    """Typed investigator output complementing the Phase 1 result envelope."""

    workflow_run_id: UUID
    claim_id: UUID
    coverage_verified: bool
    prior_claims: list[PriorClaim] = Field(default_factory=list)
    cost_assessment: CostAssessment
    fraud_risk_score: float = Field(..., ge=0.0, le=1.0)
    fraud_risk_level: RiskLevel
    fraud_indicators: list[FraudIndicator] = Field(default_factory=list)
    summary: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    schema_version: SchemaVersion = "1.0.0"

    @field_validator("summary")
    @classmethod
    def _validate_summary(cls, v: str) -> str:
        return _non_empty(v, "summary")


class ReworkRequest(BaseModel):
    """Structured rework guidance from Reviewer to Investigator."""

    rework_id: UUID = Field(default_factory=uuid4)
    focus_areas: list[FocusArea] = Field(..., min_length=1)
    specific_questions: list[str] = Field(default_factory=list)
    rationale: str
    previous_issues: list[str] = Field(default_factory=list)
    priority: ReworkPriority = ReworkPriority.MEDIUM
    deadline: datetime | None = None

    @field_validator("rationale")
    @classmethod
    def _validate_rationale(cls, v: str) -> str:
        return _non_empty(v, "rationale")


class RejectionDetails(BaseModel):
    """Structured rejection rationale."""

    primary_reason: str
    secondary_reasons: list[str] = Field(default_factory=list)
    policy_references: list[str] = Field(default_factory=list)
    appeal_process: str | None = None

    @field_validator("primary_reason")
    @classmethod
    def _validate_reason(cls, v: str) -> str:
        return _non_empty(v, "primary_reason")


class Recommendation(BaseModel):
    """Reviewer recommendation with payout/condition invariants."""

    claim_id: UUID
    recommendation_type: RecommendationType
    estimated_payout: Money | None = None
    conditions: list[str] = Field(default_factory=list)
    notes: str
    confidence: float = Field(..., ge=0.0, le=1.0)
    supporting_evidence: list[str] = Field(default_factory=list)
    schema_version: SchemaVersion = "1.0.0"

    @field_validator("notes")
    @classmethod
    def _validate_notes(cls, v: str) -> str:
        return _non_empty(v, "notes")

    @model_validator(mode="after")
    def _validate_payout_rules(self) -> Recommendation:
        needs_payout = self.recommendation_type in {
            RecommendationType.APPROVE_FULL,
            RecommendationType.APPROVE_PARTIAL,
            RecommendationType.APPROVE_WITH_CONDITIONS,
        }
        if needs_payout and self.estimated_payout is None:
            raise ValueError("approval recommendations require estimated_payout")
        if (
            self.recommendation_type == RecommendationType.DENY
            and self.estimated_payout is not None
        ):
            raise ValueError("DENY recommendations must not carry estimated_payout")
        return self


class ReviewFindings(BaseModel):
    """Typed reviewer output complementing the Phase 1 result envelope."""

    workflow_run_id: UUID
    claim_id: UUID
    evidence_summary: str
    key_findings: list[str] = Field(default_factory=list)
    concerns: list[str] = Field(default_factory=list)
    identified_gaps: list[str] = Field(default_factory=list)
    evidence_completeness: float = Field(..., ge=0.0, le=1.0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    recommendation: Recommendation
    rework_request: ReworkRequest | None = None
    rejection: RejectionDetails | None = None
    schema_version: SchemaVersion = "1.0.0"

    @field_validator("evidence_summary")
    @classmethod
    def _validate_summary(cls, v: str) -> str:
        return _non_empty(v, "evidence_summary")

    @model_validator(mode="after")
    def _validate_outcome_shapes(self) -> ReviewFindings:
        if (
            self.recommendation.recommendation_type == RecommendationType.DENY
            and self.rejection is None
        ):
            raise ValueError("DENY recommendations require rejection details")
        if (
            self.recommendation.recommendation_type == RecommendationType.INVESTIGATE_FURTHER
            and self.rework_request is None
        ):
            raise ValueError("INVESTIGATE_FURTHER requires a rework_request")
        return self


class HumanApprovalRequest(BaseModel):
    """Reviewer → Human: approval context package (ADR-009)."""

    approval_id: UUID = Field(default_factory=uuid4)
    workflow_run_id: UUID
    claim_id: UUID
    execution_id: UUID | None = None
    checkpoint_id: UUID | None = None
    request_version: int = Field(default=1, ge=1)
    recommendation: Recommendation
    reviewer_summary: str
    requested_at: datetime = Field(default_factory=_utcnow)
    deadline: datetime
    requested_by: str = "reviewer"
    required_role: str | None = None
    schema_version: SchemaVersion = "1.0.0"

    @field_validator("reviewer_summary")
    @classmethod
    def _validate_summary(cls, v: str) -> str:
        return _non_empty(v, "reviewer_summary")

    @model_validator(mode="after")
    def _validate_deadline(self) -> HumanApprovalRequest:
        if self.deadline <= self.requested_at:
            raise ValueError("deadline must follow requested_at")
        return self


class HumanApproval(BaseModel):
    """Human → Supervisor: recorded approval state. Approved/rejected states
    cannot exist without approver identity and decision timestamp."""

    approval_id: UUID = Field(default_factory=uuid4)
    workflow_run_id: UUID
    claim_id: UUID
    execution_id: UUID | None = None
    checkpoint_id: UUID | None = None
    request_version: int = Field(default=1, ge=1)
    decision_key: str | None = None
    status: ApprovalStatus = ApprovalStatus.PENDING
    approver_id: str | None = None
    approver_name: str | None = None
    approver_role: str | None = None
    requested_at: datetime = Field(default_factory=_utcnow)
    decided_at: datetime | None = None
    deadline: datetime | None = None
    notes: str | None = None
    conditions: list[str] = Field(default_factory=list)
    is_override: bool = False
    override_justification: str | None = None
    schema_version: SchemaVersion = "1.0.0"

    @model_validator(mode="after")
    def _validate_decision_metadata(self) -> HumanApproval:
        decided = self.status in {ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}
        if decided:
            missing = [
                name
                for name, value in (
                    ("approver_id", self.approver_id),
                    ("approver_name", self.approver_name),
                    ("approver_role", self.approver_role),
                )
                if not (value or "").strip()
            ]
            if missing:
                raise ValueError(f"decided approvals require: {', '.join(missing)}")
            if self.decided_at is None:
                raise ValueError("decided approvals require decided_at")
            if self.decided_at < self.requested_at:
                raise ValueError("decided_at cannot precede requested_at")
        if self.is_override and not (self.override_justification or "").strip():
            raise ValueError("overrides require override_justification")
        return self


class WorkflowRun(BaseModel):
    """Run metadata: identity, state, progress. Terminal states cannot carry
    contradictory active-work state (state-machine.md invariants)."""

    workflow_run_id: UUID = Field(default_factory=uuid4)
    claim_id: UUID
    current_state: WorkflowState
    step_count: int = Field(default=0, ge=0)
    rework_count: int = Field(default=0, ge=0)
    started_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None
    terminal_state: WorkflowState | None = None
    termination_reason: str | None = None
    budget_state: BudgetState = Field(default_factory=BudgetState)
    trace_id: str = Field(default_factory=lambda: uuid4().hex)
    schema_version: SchemaVersion = "1.0.0"

    @model_validator(mode="after")
    def _validate_terminal_consistency(self) -> WorkflowRun:
        is_terminal = self.current_state.is_terminal
        if self.terminal_state is not None:
            if not self.terminal_state.is_terminal:
                raise ValueError("terminal_state must be a terminal state")
            if self.current_state != self.terminal_state:
                raise ValueError("current_state must equal terminal_state once terminated")
            if self.completed_at is None:
                raise ValueError("terminated runs require completed_at")
            if not (self.termination_reason or "").strip():
                raise ValueError("terminated runs require termination_reason")
        if self.completed_at is not None and self.terminal_state is None:
            raise ValueError("completed_at without terminal_state is contradictory")
        if is_terminal and self.terminal_state is None:
            raise ValueError("terminal current_state requires terminal_state")
        return self


class AgentExecution(BaseModel):
    """Execution metadata for one agent invocation (observability/replay)."""

    execution_id: UUID = Field(default_factory=uuid4)
    workflow_run_id: UUID
    claim_id: UUID
    agent: AgentType
    operation: str
    status: ExecutionStatus = ExecutionStatus.SUCCESS
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: datetime | None = None
    tokens_used: TokenUsage = Field(default_factory=TokenUsage)
    cost_usd: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))
    idempotency_key: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    schema_version: SchemaVersion = "1.0.0"

    @field_validator("operation")
    @classmethod
    def _validate_operation(cls, v: str) -> str:
        return _non_empty(v, "operation")

    @model_validator(mode="after")
    def _validate_timing(self) -> AgentExecution:
        if self.finished_at is not None and self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        if self.status == ExecutionStatus.FAILURE and not (self.error_code or "").strip():
            raise ValueError("failed executions require error_code")
        return self


class AuditEvent(BaseModel):
    """Structured audit event. Typed fields are the primary representation;
    `summary`/`attributes` carry supplementary context only."""

    event_id: UUID = Field(default_factory=uuid4)
    workflow_run_id: UUID
    claim_id: UUID
    trace_id: str
    event_type: AuditEventType
    timestamp: datetime = Field(default_factory=_utcnow)
    actor_type: ActorType
    actor_id: str
    action: str
    summary: str = ""
    attributes: dict[str, str] = Field(default_factory=dict)
    result: AuditResult = AuditResult.SUCCESS
    error: str | None = None
    from_state: WorkflowState | None = None
    to_state: WorkflowState | None = None
    correlation_id: UUID = Field(default_factory=uuid4)
    schema_version: SchemaVersion = "1.0.0"

    @field_validator("trace_id", "actor_id", "action")
    @classmethod
    def _validate_non_empty(cls, v: str) -> str:
        return _non_empty(v, "audit field")

    @model_validator(mode="after")
    def _validate_result(self) -> AuditEvent:
        if self.result == AuditResult.FAILURE and not (self.error or "").strip():
            raise ValueError("FAILURE events require error text")
        return self

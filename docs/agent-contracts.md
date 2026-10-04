# CASEFILE Agent Contracts

## Implementation Status (Phase 2)

The sketches below are implemented as real, tested modules in `src/casefile/models/`:

| Document concept | Implementation |
|---|---|
| Claim / policy / history / estimates / findings / recommendation / approval | `domain.py` (`Claim`, `Policy`, `PriorClaim`, `DamageEstimate`, `InvestigationFindings`, `ReviewFindings`, `Recommendation`, `HumanApproval`, `HumanApprovalRequest`, `WorkflowRun`, `AgentExecution`, `AuditEvent`, `Money`) |
| Handoff contracts (Supervisor ↔ Extractor/Investigator/Reviewer/Human) | `contracts.py` (Phase 1, frozen) wrapped by `envelope.py` (`ContractEnvelope`, 8 registered routes, no agent-to-agent shortcuts) |
| Schema versioning | `versioning.py` (semver registry, compatibility rules, rejection of malformed/major-mismatch versions) |
| Enums (claim/workflow/agent/execution/approval/recommendation states) | `domain.py` (reuses the single `WorkflowState` machine; no duplicate decision enum) |

Phase 1 handoff models in `contracts.py` are intentionally frozen for the
Phase 1 gate; the richer typed finding models in `domain.py` sit alongside
them for later phases to adopt. Reviewer decisions stay `APPROVE`/`REJECT`/
`REWORK` per AGENTS.md (the `APPROVED`/`REJECTED`/`REQUEST_REWORK` sketch in
older drafts is not a second vocabulary).

## Implementation Status (Phase 4)

The handoff contracts above are now produced by real agents in
`src/casefile/agents/`: `ExtractorAgent` → `ExtractionResult`,
`InvestigatorAgent` → `InvestigationResult`, `ReviewerAgent` →
`ReviewResult`, all wrapped in `ContractEnvelope` routes for transport.
Each production path is provider JSON → Pydantic validation → documented
domain consistency checks, with typed `AgentError` failures instead of
coerced successes.

## Implementation Status (Phase 5)

Tool contracts live in `src/casefile/tools/` (`docs/tools.md`): agents
fetch typed tool outputs through `ToolRegistry` and feed them into the
request contracts above. No raw store output crosses into agent contracts;
evidence reaches agents as `DocumentRetrievalOutput`, `PolicyLookupOutput`,
prior-claim lists, reconciled estimates, `EvidenceItem`s, and fraud
assessments.

## Overview

All inter-agent communication in CASEFILE uses strictly typed Pydantic v2 models. This document defines every contract used for communication between the Supervisor and specialist agents, as well as external API contracts.

## Design Principles

### 1. No Free-Form Communication

Agents never exchange raw text or dictionaries. Every message is a validated Pydantic model.

### 2. Schema Versioning

All contracts include a schema version to support evolution without breaking replay.

### 3. Validation at Boundaries

Every contract validates its data at construction time. Invalid data is rejected before it enters the system.

### 4. Immutability Where Possible

Contracts are designed to be immutable after creation. State mutations happen through explicit transitions.

## Core Domain Models

### Claim Input

```python
from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from typing import Optional, List, Dict, Any
from uuid import UUID, uuid4
from pydantic import BaseModel, Field, validator, model_validator


class ClaimInput(BaseModel):
    """
    Initial claim submission from external system.

    This is the entry point for all claim processing.
    """

    # Identification
    claim_id: UUID = Field(default_factory=uuid4)
    external_claim_id: str = Field(..., description="ID from external claims system")

    # Policy information
    policy_number: str
    customer_id: str

    # Claim details
    claim_type: "ClaimType"
    date_of_loss: date
    date_reported: date
    description: str

    # Documents
    documents: List["Document"]

    # Metadata
    submitted_at: datetime = Field(default_factory=datetime.utcnow)
    submitted_by: str
    priority: "ClaimPriority" = ClaimPriority.NORMAL

    # Schema versioning
    schema_version: str = "1.0.0"

    @validator('policy_number')
    def validate_policy_number(cls, v):
        if not v or len(v.strip()) == 0:
            raise ValueError('Policy number cannot be empty')
        return v.strip().upper()

    @validator('documents')
    def validate_documents_not_empty(cls, v):
        if not v or len(v) == 0:
            raise ValueError('At least one document is required')
        return v


class ClaimType(str, Enum):
    COLLISION = "COLLISION"
    COMPREHENSIVE = "COMPREHENSIVE"
    LIABILITY = "LIABILITY"
    UNINSURED_MOTORIST = "UNINSURED_MOTORIST"
    MEDICAL_PAYMENTS = "MEDICAL_PAYMENTS"
    PERSONAL_INJURY = "PERSONAL_INJURY"


class ClaimPriority(str, Enum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    URGENT = "URGENT"
```

### Document Model

```python
class Document(BaseModel):
    """
    A claim document (police report, photo, estimate, etc.).
    """

    document_id: UUID = Field(default_factory=uuid4)
    document_type: "DocumentType"

    # Content
    content: str = Field(..., description="Base64 encoded content or text")
    content_type: "DocumentContentType"
    content_encoding: str = Field(default="base64", description="Encoding for binary content")

    # Metadata
    filename: Optional[str] = None
    page_count: Optional[int] = None
    file_size_bytes: Optional[int] = None

    # Provenance
    source: str = Field(..., description="Where the document came from")
    received_at: datetime = Field(default_factory=datetime.utcnow)

    # Processing status
    processing_status: "DocumentProcessingStatus" = DocumentProcessingStatus.PENDING

    @validator('content')
    def validate_content_not_empty(cls, v):
        if not v or len(v.strip()) == 0:
            raise ValueError('Document content cannot be empty')
        return v


class DocumentType(str, Enum):
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
    PDF = "PDF"
    IMAGE_JPEG = "IMAGE_JPEG"
    IMAGE_PNG = "IMAGE_PNG"
    TEXT = "TEXT"
    JSON = "JSON"


class DocumentProcessingStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
```

## Workflow State Models

### Workflow Run

```python
class WorkflowRun(BaseModel):
    """
    Represents a single execution of the claim workflow.
    """

    # Identification
    workflow_run_id: UUID = Field(default_factory=uuid4)
    claim_id: UUID

    # State
    current_state: "WorkflowState"
    state_data: Dict[str, Any]  # Serialized state-specific data

    # Execution tracking
    step_count: int = 0
    started_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None

    # Budget
    budget_state: "BudgetState"

    # Rework
    rework_count: int = 0

    # Terminal state info
    terminal_state: Optional["WorkflowState"] = None
    termination_reason: Optional[str] = None

    # Checkpoint reference
    last_checkpoint_id: Optional[UUID] = None

    # Trace correlation
    trace_id: str = Field(default_factory=lambda: str(uuid4()))

    @model_validator(mode='after')
    def validate_terminal_state_consistency(self):
        if self.terminal_state and not self.completed_at:
            raise ValueError('Terminal state must have completed_at')
        return self


class WorkflowState(str, Enum):
    """
    All possible workflow states.
    """

    # Entry
    RECEIVED = "RECEIVED"

    # Processing
    EXTRACTION = "EXTRACTION"
    INVESTIGATION = "INVESTIGATION"
    REVIEW = "REVIEW"

    # Control
    REWORK_LOOP = "REWORK_LOOP"

    # Wait
    HUMAN_APPROVAL = "HUMAN_APPROVAL"

    # Terminal - Success
    APPROVED = "APPROVED"

    # Terminal - Failure
    REJECTED = "REJECTED"
    FAILED = "FAILED"

    # Terminal - Exception
    ESCALATION = "ESCALATION"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    TIMEOUT = "TIMEOUT"
    MAX_STEPS_EXCEEDED = "MAX_STEPS_EXCEEDED"
    MAX_REWORK_EXCEEDED = "MAX_REWORK_EXCEEDED"
```

### Budget State

```python
class BudgetState(BaseModel):
    """
    Tracks resource usage and budget limits for a workflow.
    """

    # Token usage
    input_tokens_used: int = 0
    output_tokens_used: int = 0
    total_tokens_used: int = 0

    # Cost tracking
    estimated_cost_usd: Decimal = Decimal("0.00")

    # Step tracking
    steps_completed: int = 0

    # Time tracking
    elapsed_time_seconds: int = 0

    # Tool calls
    tool_calls_made: int = 0

    # Limits
    limits: "BudgetLimits"

    # Status
    is_exhausted: bool = False
    exhaustion_reason: Optional[str] = None

    @model_validator(mode='after')
    def update_totals(self):
        self.total_tokens_used = self.input_tokens_used + self.output_tokens_used
        return self

    def check_limits(self) -> Optional[str]:
        """
        Check if any limit has been exceeded.

        Returns the reason if exhausted, None otherwise.
        """

        if self.total_tokens_used >= self.limits.max_total_tokens:
            self.is_exhausted = True
            self.exhaustion_reason = "MAX_TOKENS_EXCEEDED"
            return "MAX_TOKENS_EXCEEDED"

        if self.estimated_cost_usd >= self.limits.max_cost_usd:
            self.is_exhausted = True
            self.exhaustion_reason = "MAX_COST_EXCEEDED"
            return "MAX_COST_EXCEEDED"

        if self.steps_completed >= self.limits.max_steps:
            self.is_exhausted = True
            self.exhaustion_reason = "MAX_STEPS_EXCEEDED"
            return "MAX_STEPS_EXCEEDED"

        if self.elapsed_time_seconds >= self.limits.max_execution_time_seconds:
            self.is_exhausted = True
            self.exhaustion_reason = "MAX_TIME_EXCEEDED"
            return "MAX_TIME_EXCEEDED"

        return None

    def add_usage(self, tokens: "TokenUsage", cost: Decimal) -> None:
        """
        Add usage from an LLM call.
        """
        self.input_tokens_used += tokens.input_tokens
        self.output_tokens_used += tokens.output_tokens
        self.total_tokens_used += tokens.total_tokens
        self.estimated_cost_usd += cost
        self.check_limits()


class BudgetLimits(BaseModel):
    """
    Budget limits for a workflow.
    """

    max_input_tokens: int = 100_000
    max_output_tokens: int = 20_000
    max_total_tokens: int = 150_000
    max_cost_usd: Decimal = Decimal("5.00")
    max_steps: int = 50
    max_execution_time_seconds: int = 1800  # 30 minutes
    max_tool_calls: int = 100
    max_rework_cycles: int = 3


class TokenUsage(BaseModel):
    """
    Token usage from an LLM call.
    """

    input_tokens: int
    output_tokens: int
    total_tokens: int

    @model_validator(mode='after')
    def validate_total(self):
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError('total_tokens must equal input_tokens + output_tokens')
        return self
```

## Agent Request Contracts

### Extraction Request

```python
class ExtractionRequest(BaseModel):
    """
    Request to the Extractor agent.
    """

    # Identification
    request_id: UUID = Field(default_factory=uuid4)
    workflow_run_id: UUID
    claim_id: UUID

    # Documents to process
    documents: List[Document]

    # Extraction configuration
    extraction_types: List["ExtractionType"] = Field(
        default_factory=lambda: list(ExtractionType)
    )

    # Context
    language_hint: Optional[str] = None
    previous_extraction: Optional["ExtractionResult"] = None

    # Execution parameters
    max_tokens: int = 50_000
    timeout_seconds: int = 300  # 5 minutes

    # Schema version
    schema_version: str = "1.0.0"


class ExtractionType(str, Enum):
    VEHICLE_INFO = "VEHICLE_INFO"
    DRIVER_INFO = "DRIVER_INFO"
    ACCIDENT_DETAILS = "ACCIDENT_DETAILS"
    DAMAGES = "DAMAGES"
    WITNESSES = "WITNESSES"
    REPAIR_ESTIMATES = "REPAIR_ESTIMATES"
    ALL_PARTIES = "ALL_PARTIES"
```

### Investigation Request

```python
class InvestigationRequest(BaseModel):
    """
    Request to the Investigator agent.
    """

    # Identification
    request_id: UUID = Field(default_factory=uuid4)
    workflow_run_id: UUID
    claim_id: UUID

    # Extracted data to investigate
    extracted_data: "ExtractedData"

    # Policy context
    policy_number: str
    customer_id: str

    # Rework context
    rework_instructions: Optional["ReworkInstructions"] = None
    previous_investigation: Optional["InvestigationResult"] = None

    # Execution parameters
    max_tokens: int = 30_000
    timeout_seconds: int = 600  # 10 minutes

    # Schema version
    schema_version: str = "1.0.0"


class ReworkInstructions(BaseModel):
    """
    Instructions from Reviewer for rework.
    """

    rework_id: UUID = Field(default_factory=uuid4)

    # What to focus on
    focus_areas: List["FocusArea"]
    specific_questions: List[str] = []

    # Context
    rationale: str
    previous_issues: List[str] = []

    # Priority
    priority: "ReworkPriority" = ReworkPriority.MEDIUM

    # Constraints
    deadline: Optional[datetime] = None
    max_additional_time_seconds: Optional[int] = None


class FocusArea(str, Enum):
    POLICY_COVERAGE = "POLICY_COVERAGE"
    CLAIM_HISTORY = "CLAIM_HISTORY"
    REPAIR_COSTS = "REPAIR_COSTS"
    FRAUD_INDICATORS = "FRAUD_INDICATORS"
    DAMAGE_ASSESSMENT = "DAMAGE_ASSESSMENT"
    WITNESS_STATEMENTS = "WITNESS_STATEMENTS"
    ACCIDENT_RECONSTRUCTION = "ACCIDENT_RECONSTRUCTION"


class ReworkPriority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
```

### Review Request

```python
class ReviewRequest(BaseModel):
    """
    Request to the Reviewer agent.
    """

    # Identification
    request_id: UUID = Field(default_factory=uuid4)
    workflow_run_id: UUID
    claim_id: UUID

    # Inputs from previous stages
    extracted_data: "ExtractedData"
    investigation_result: "InvestigationResult"

    # Rework context
    rework_count: int = 0
    previous_rework_requests: List["ReworkRequest"] = []

    # Execution parameters
    max_tokens: int = 20_000
    timeout_seconds: int = 300  # 5 minutes

    # Schema version
    schema_version: str = "1.0.0"
```

## Agent Result Contracts

### Extraction Result

```python
class ExtractionResult(BaseModel):
    """
    Result from the Extractor agent.
    """

    # Identification
    result_id: UUID = Field(default_factory=uuid4)
    request_id: UUID
    workflow_run_id: UUID
    claim_id: UUID

    # Status
    status: "ExtractionStatus"

    # Extracted data
    extracted_data: Optional["ExtractedData"] = None
    partial_data: Optional["PartialExtractedData"] = None

    # Processing details
    documents_processed: int
    documents_failed: List["DocumentFailure"]

    # Confidence
    confidence: "ExtractionConfidence"

    # Execution metadata
    tokens_used: TokenUsage
    estimated_cost_usd: Decimal
    execution_time_ms: int

    # Error
    error: Optional["ErrorDetails"] = None

    # Timestamp
    completed_at: datetime = Field(default_factory=datetime.utcnow)

    # Schema version
    schema_version: str = "1.0.0"


class ExtractionStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILURE = "FAILURE"
    TIMEOUT = "TIMEOUT"


class ExtractedData(BaseModel):
    """
    Structured data extracted from claim documents.
    """

    # Vehicle information
    vehicle: "VehicleInfo"

    # Driver information
    driver: "DriverInfo"
    other_parties: List["PartyInfo"] = []

    # Accident details
    accident: "AccidentDetails"

    # Damages
    damages: List["DamageInfo"]

    # Witnesses
    witnesses: List["WitnessInfo"] = []

    # Repair estimates
    repair_estimates: List["RepairEstimate"] = []

    # Additional notes
    additional_notes: Optional[str] = None

    # Raw extractions (for debugging)
    raw_extractions: Optional[Dict[str, Any]] = None


class VehicleInfo(BaseModel):
    make: str
    model: str
    year: int
    vin: Optional[str] = None
    license_plate: Optional[str] = None
    state: Optional[str] = None
    color: Optional[str] = None
    mileage: Optional[int] = None


class DriverInfo(BaseModel):
    name: str
    date_of_birth: Optional[date] = None
    license_number: Optional[str] = None
    license_state: Optional[str] = None
    address: Optional["Address"] = None
    phone: Optional[str] = None
    email: Optional[str] = None


class PartyInfo(BaseModel):
    role: str  # "other_driver", "passenger", "pedestrian", etc.
    name: str
    contact_info: Optional[str] = None
    insurance: Optional["InsuranceInfo"] = None
    vehicle: Optional[VehicleInfo] = None


class AccidentDetails(BaseModel):
    date: date
    time: Optional[str] = None
    location: "AccidentLocation"
    description: str
    weather_conditions: Optional[str] = None
    road_conditions: Optional[str] = None
    accident_type: Optional[str] = None
    point_of_impact: Optional[str] = None
    police_report_number: Optional[str] = None
    police_jurisdiction: Optional[str] = None


class AccidentLocation(BaseModel):
    address: Optional[str] = None
    city: str
    state: str
    zip_code: Optional[str] = None
    intersection: Optional[str] = None
    landmark: Optional[str] = None


class DamageInfo(BaseModel):
    location: str  # "front bumper", "rear quarter panel", etc.
    type: str  # "dent", "scratch", "broken", etc.
    severity: str  # "minor", "moderate", "severe"
    description: str
    estimated_repair_cost: Optional[Decimal] = None


class WitnessInfo(BaseModel):
    name: str
    contact_info: Optional[str] = None
    statement_summary: Optional[str] = None


class RepairEstimate(BaseModel):
    source: str  # "body_shop", "insurance_adjuster", etc.
    date: date
    total_cost: Decimal
    labor_hours: Optional[float] = None
    parts_cost: Optional[Decimal] = None
    labor_cost: Optional[Decimal] = None
    breakdown: Optional[List["RepairLineItem"]] = None


class RepairLineItem(BaseModel):
    description: str
    quantity: int
    unit_cost: Decimal
    total_cost: Decimal


class PartialExtractedData(BaseModel):
    """
    Partial extraction when some documents failed.
    """

    vehicle: Optional[VehicleInfo] = None
    driver: Optional[DriverInfo] = None
    accident: Optional[AccidentDetails] = None
    damages: List[DamageInfo] = []
    witnesses: List[WitnessInfo] = []
    repair_estimates: List[RepairEstimate] = []

    available_fields: List[str]
    missing_fields: List[str]


class DocumentFailure(BaseModel):
    document_id: UUID
    document_type: DocumentType
    error: str
    is_retryable: bool = False


class ExtractionConfidence(BaseModel):
    overall_confidence: float = Field(ge=0.0, le=1.0)
    vehicle_confidence: float = Field(ge=0.0, le=1.0)
    driver_confidence: float = Field(ge=0.0, le=1.0)
    accident_confidence: float = Field(ge=0.0, le=1.0)
    damages_confidence: float = Field(ge=0.0, le=1.0)
    estimates_confidence: float = Field(ge=0.0, le=1.0)
```

### Investigation Result

```python
class InvestigationResult(BaseModel):
    """
    Result from the Investigator agent.
    """

    # Identification
    result_id: UUID = Field(default_factory=uuid4)
    request_id: UUID
    workflow_run_id: UUID
    claim_id: UUID

    # Status
    status: "InvestigationStatus"

    # Findings
    policy_verification: "PolicyVerification"
    claim_history: "ClaimHistory"
    cost_analysis: "CostAnalysis"
    fraud_assessment: "FraudAssessment"

    # Summary
    investigation_summary: str
    flags: List["InvestigationFlag"]

    # Tool calls
    tool_calls: List["ToolCallRecord"]

    # Execution metadata
    tokens_used: TokenUsage
    estimated_cost_usd: Decimal
    execution_time_ms: int

    # Partial results
    partial_findings: Optional["PartialInvestigationFindings"] = None

    # Error
    error: Optional["ErrorDetails"] = None

    # Timestamp
    completed_at: datetime = Field(default_factory=datetime.utcnow)

    # Schema version
    schema_version: str = "1.0.0"


class InvestigationStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILURE = "FAILURE"
    TIMEOUT = "TIMEOUT"


class PolicyVerification(BaseModel):
    """
    Policy verification results from policy_lookup tool.
    """

    policy_number: str
    is_valid: bool
    is_active: bool

    # Policy details
    policyholder_name: str
    policy_type: str
    effective_date: date
    expiration_date: date

    # Coverage
    coverage_limits: "CoverageLimits"
    deductible: Decimal
    exclusions: List[str] = []

    # Additional insured
    additional_insured: List[str] = []

    # Verification
    verification_timestamp: datetime = Field(default_factory=datetime.utcnow)
    verification_confidence: float = Field(ge=0.0, le=1.0)


class CoverageLimits(BaseModel):
    bodily_injury_per_person: Decimal
    bodily_injury_per_accident: Decimal
    property_damage: Decimal
    comprehensive_deductible: Optional[Decimal] = None
    collision_deductible: Optional[Decimal] = None
    rental_reimbursement_daily: Optional[Decimal] = None
    rental_reimbursement_max: Optional[Decimal] = None


class ClaimHistory(BaseModel):
    """
    Previous claims history from claim_history_lookup tool.
    """

    policy_number: str
    customer_id: str

    # Previous claims
    previous_claims: List["PreviousClaim"]

    # Statistics
    total_previous_claims: int
    total_previous_payout: Decimal
    first_claim_date: Optional[date] = None
    most_recent_claim_date: Optional[date] = None

    # Patterns
    patterns: List[str] = []
    concerns: List[str] = []

    # Timestamp
    lookup_timestamp: datetime = Field(default_factory=datetime.utcnow)


class PreviousClaim(BaseModel):
    claim_id: str
    claim_date: date
    claim_type: ClaimType
    amount: Decimal
    status: str
    at_fault: Optional[bool] = None
    description: Optional[str] = None


class CostAnalysis(BaseModel):
    """
    Repair cost analysis from repair_cost_lookup tool.
    """

    # Submitted estimate
    submitted_estimate: Decimal
    submitted_source: str

    # Expected range
    expected_range_low: Decimal
    expected_range_high: Decimal
    expected_range_average: Decimal

    # Assessment
    is_within_expected_range: bool
    variance_percentage: float
    variance_description: str

    # Breakdown
    breakdown: "CostBreakdown"

    # Recommendations
    recommendations: List[str] = []

    # Timestamp
    analysis_timestamp: datetime = Field(default_factory=datetime.utcnow)


class CostBreakdown(BaseModel):
    labor_hours: float
    labor_rate: Decimal
    labor_total: Decimal
    parts_total: Decimal
    paint_materials: Optional[Decimal] = None
    towing: Optional[Decimal] = None
    rental: Optional[Decimal] = None
    other: Optional[Decimal] = None
    subtotal: Decimal
    tax: Optional[Decimal] = None
    total: Decimal


class FraudAssessment(BaseModel):
    """
    Fraud assessment from fraud_signal_lookup tool.
    """

    risk_score: float = Field(ge=0.0, le=1.0)
    risk_level: "RiskLevel"

    # Indicators
    indicators: List["FraudIndicator"]

    # Recommendation
    requires_manual_review: bool
    recommended_actions: List[str] = []

    # Timestamp
    assessment_timestamp: datetime = Field(default_factory=datetime.utcnow)


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class FraudIndicator(BaseModel):
    indicator_type: str
    description: str
    severity: str  # "low", "medium", "high"
    detected_at: datetime = Field(default_factory=datetime.utcnow)


class InvestigationFlag(BaseModel):
    flag_type: str
    description: str
    severity: str
    requires_attention: bool


class PartialInvestigationFindings(BaseModel):
    """
    Partial findings when some tools failed.
    """

    policy_verification: Optional[PolicyVerification] = None
    claim_history: Optional[ClaimHistory] = None
    cost_analysis: Optional[CostAnalysis] = None
    fraud_assessment: Optional[FraudAssessment] = None

    available_findings: List[str]
    missing_findings: List[str]
```

### Review Result

```python
class ReviewResult(BaseModel):
    """
    Result from the Reviewer agent.
    """

    # Identification
    result_id: UUID = Field(default_factory=uuid4)
    request_id: UUID
    workflow_run_id: UUID
    claim_id: UUID

    # Decision
    decision: "ReviewDecision"

    # Recommendation (if approved)
    recommendation: Optional["ClaimRecommendation"] = None

    # Rework request (if rework needed)
    rework_request: Optional["ReworkRequest"] = None

    # Rejection (if rejected)
    rejection_reason: Optional[str] = None
    rejection_details: Optional["RejectionDetails"] = None

    # Synthesis
    evidence_summary: str
    key_findings: List[str]
    concerns: List[str]

    # Execution metadata
    tokens_used: TokenUsage
    estimated_cost_usd: Decimal
    execution_time_ms: int

    # Error
    error: Optional["ErrorDetails"] = None

    # Timestamp
    completed_at: datetime = Field(default_factory=datetime.utcnow)

    # Schema version
    schema_version: str = "1.0.0"


class ReviewDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REQUEST_REWORK = "REQUEST_REWORK"


class ClaimRecommendation(BaseModel):
    """
    Final recommendation for a claim.
    """

    claim_id: UUID
    recommendation_type: "RecommendationType"

    # Payout (if applicable)
    estimated_payout: Optional[Decimal] = None
    payout_breakdown: Optional["PayoutBreakdown"] = None

    # Conditions
    conditions: List[str] = []

    # Notes
    notes: str

    # Confidence
    confidence: float = Field(ge=0.0, le=1.0)

    # Supporting evidence
    supporting_evidence: List[str] = []


class RecommendationType(str, Enum):
    APPROVE_FULL = "APPROVE_FULL"
    APPROVE_PARTIAL = "APPROVE_PARTIAL"
    APPROVE_WITH_CONDITIONS = "APPROVE_WITH_CONDITIONS"
    DENY = "DENY"
    INVESTIGATE_FURTHER = "INVESTIGATE_FURTHER"


class PayoutBreakdown(BaseModel):
    vehicle_damage: Decimal
    deductible: Decimal
    less_salvage: Optional[Decimal] = None
    less_depreciation: Optional[Decimal] = None
    additional_coverages: Optional[Decimal] = None
    total: Decimal


class ReworkRequest(BaseModel):
    """
    Request for additional investigation.
    """

    rework_id: UUID = Field(default_factory=uuid4)

    # Focus
    focus_areas: List[FocusArea]
    specific_questions: List[str]

    # Rationale
    rationale: str
    previous_issues: List[str]

    # Priority
    priority: ReworkPriority = ReworkPriority.MEDIUM

    # Constraints
    deadline: Optional[datetime] = None


class RejectionDetails(BaseModel):
    """
    Details about why a claim was rejected.
    """

    primary_reason: str
    secondary_reasons: List[str] = []
    policy_references: List[str] = []
    supporting_evidence: List[str] = []
    appeal_process: Optional[str] = None
```

## Tool Contracts

### Tool Call Record

```python
class ToolCallRecord(BaseModel):
    """
    Record of a tool call made by an agent.
    """

    call_id: UUID = Field(default_factory=uuid4)
    workflow_run_id: UUID

    # Tool info
    tool_name: str
    tool_version: str = "1.0.0"

    # Input/Output
    input: Dict[str, Any]
    output: Optional[Dict[str, Any]] = None

    # Execution
    started_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None
    execution_time_ms: Optional[int] = None

    # Status
    status: "ToolCallStatus"

    # Retry
    retry_count: int = 0
    max_retries: int = 3

    # Error
    error: Optional["ErrorDetails"] = None

    # Authorization
    authorized: bool = True
    authorization_check_id: Optional[UUID] = None


class ToolCallStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    TIMEOUT = "TIMEOUT"
    UNAUTHORIZED = "UNAUTHORIZED"
```

### Tool Results

```python
class PolicyLookupResult(BaseModel):
    """
    Result from policy_lookup tool.
    """

    policy_number: str
    is_found: bool
    policy: Optional[PolicyVerification] = None
    error: Optional["ErrorDetails"] = None


class ClaimHistoryResult(BaseModel):
    """
    Result from claim_history_lookup tool.
    """

    policy_number: str
    is_found: bool
    history: Optional[ClaimHistory] = None
    error: Optional["ErrorDetails"] = None


class RepairCostResult(BaseModel):
    """
    Result from repair_cost_lookup tool.
    """

    vehicle_info: VehicleInfo
    damage_type: str
    is_found: bool
    analysis: Optional[CostAnalysis] = None
    error: Optional["ErrorDetails"] = None


class FraudSignalResult(BaseModel):
    """
    Result from fraud_signal_lookup tool.
    """

    claim_id: UUID
    assessment: FraudAssessment
    error: Optional["ErrorDetails"] = None
```

## Human Interaction Contracts

### Human Approval

```python
class HumanApproval(BaseModel):
    """
    Human approval for a claim recommendation.
    """

    approval_id: UUID = Field(default_factory=uuid4)
    workflow_run_id: UUID
    claim_id: UUID

    # Decision
    decision: "HumanApprovalDecision"

    # Context
    recommendation: ClaimRecommendation

    # Approver info
    approver_id: str
    approver_name: str
    approver_role: str

    # Timestamps
    requested_at: datetime
    reviewed_at: datetime = Field(default_factory=datetime.utcnow)
    timeout_at: datetime

    # Additional info
    notes: Optional[str] = None
    conditions: List[str] = []

    # Override
    is_override: bool = False
    override_justification: Optional[str] = None


class HumanApprovalDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    ESCALATED = "ESCALATED"
```

## Audit Contracts

### Audit Event

```python
class AuditEvent(BaseModel):
    """
    Immutable audit event for compliance and debugging.
    """

    event_id: UUID = Field(default_factory=uuid4)

    # Correlation
    workflow_run_id: UUID
    claim_id: UUID
    trace_id: str

    # Event details
    event_type: "AuditEventType"
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Actor
    actor_type: str  # "SYSTEM", "AGENT", "TOOL", "HUMAN"
    actor_id: str
    actor_name: str

    # Action
    action: str
    details: Dict[str, Any]

    # Result
    result: str  # "SUCCESS", "FAILURE", "TIMEOUT"
    error: Optional[str] = None

    # State context
    from_state: Optional[WorkflowState] = None
    to_state: Optional[WorkflowState] = None

    # Resource usage
    tokens_used: Optional[TokenUsage] = None
    cost_usd: Optional[Decimal] = None

    # Schema version
    schema_version: str = "1.0.0"


class AuditEventType(str, Enum):
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
    HUMAN_APPROVAL_REQUESTED = "HUMAN_APPROVAL_REQUESTED"
    HUMAN_APPROVAL_RECEIVED = "HUMAN_APPROVAL_RECEIVED"
    BUDGET_WARNING = "BUDGET_WARNING"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    ERROR_OCCURRED = "ERROR_OCCURRED"
    RETRY_ATTEMPTED = "RETRY_ATTEMPTED"
```

## Checkpoint Contracts

### Checkpoint

```python
class Checkpoint(BaseModel):
    """
    Snapshot of workflow state for recovery and replay.
    """

    checkpoint_id: UUID = Field(default_factory=uuid4)
    workflow_run_id: UUID
    claim_id: UUID

    # State snapshot
    state: WorkflowState
    state_data: Dict[str, Any]

    # Execution context
    step_count: int
    rework_count: int
    budget_state: BudgetState

    # Recorded tool outputs (for deterministic replay)
    recorded_tool_outputs: Dict[str, Dict[str, Any]] = {}

    # Timestamp
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Versioning
    schema_version: str = "1.0.0"
    application_version: str

    # Metadata
    checkpoint_type: "CheckpointType"
    parent_checkpoint_id: Optional[UUID] = None


class CheckpointType(str, Enum):
    BEFORE_NODE = "BEFORE_NODE"
    AFTER_NODE = "AFTER_NODE"
    BEFORE_TRANSITION = "BEFORE_TRANSITION"
    AFTER_TRANSITION = "AFTER_TRANSITION"
    BEFORE_HUMAN_APPROVAL = "BEFORE_HUMAN_APPROVAL"
    AFTER_HUMAN_APPROVAL = "AFTER_HUMAN_APPROVAL"
    ON_ERROR = "ON_ERROR"
    MANUAL = "MANUAL"
```

## Error Handling

### Error Details

```python
class ErrorDetails(BaseModel):
    """
    Standardized error information.
    """

    error_id: UUID = Field(default_factory=uuid4)
    error_type: str
    error_code: str
    message: str

    # Context
    component: str  # "extractor", "investigator", etc.
    operation: str

    # Stack trace (for debugging)
    stack_trace: Optional[str] = None

    # Retry info
    is_retryable: bool = False
    retry_after_seconds: Optional[int] = None

    # Timestamp
    occurred_at: datetime = Field(default_factory=datetime.utcnow)

    # Additional context
    context: Dict[str, Any] = {}
```

## Address and Insurance Models

```python
class Address(BaseModel):
    street_address: str
    city: str
    state: str
    zip_code: str
    country: str = "USA"


class InsuranceInfo(BaseModel):
    company_name: str
    policy_number: str
    effective_date: Optional[date] = None
    expiration_date: Optional[date] = None
    agent_name: Optional[str] = None
    agent_phone: Optional[str] = None
```

## Contract Versioning Strategy

### Version Compatibility

```python
class ContractVersion(BaseModel):
    """
    Version information for contract compatibility.
    """

    schema_version: str
    min_compatible_version: str
    deprecated: bool = False
    deprecation_date: Optional[datetime] = None
    removal_date: Optional[datetime] = None


def validate_schema_version(
    data: BaseModel,
    expected_version: str
) -> bool:
    """
    Validates that the data's schema version is compatible.
    """
    if not hasattr(data, 'schema_version'):
        raise ValueError('Data missing schema_version')

    # Parse versions
    data_version = tuple(map(int, data.schema_version.split('.')))
    expected = tuple(map(int, expected_version.split('.')))

    # Major version must match
    if data_version[0] != expected[0]:
        raise ValueError(
            f'Incompatible schema version: {data.schema_version} vs {expected_version}'
        )

    return True
```

## Summary

The contract system provides:
- **Type safety**: All communication is through validated Pydantic models
- **Schema versioning**: Contracts can evolve without breaking replay
- **Complete traceability**: Every action has identifiers and timestamps
- **Graceful degradation**: Partial results for failures
- **Budget integration**: Every result includes resource usage
- **Audit compliance**: Immutable records of all actions

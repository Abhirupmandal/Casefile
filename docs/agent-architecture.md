# CASEFILE Agent Architecture

## Implementation Status (Phase 3)

Orchestration backbone is live in `src/casefile/workflow/` with no agent
intelligence yet: `SupervisorRouter` (deterministic routing per this
document's Supervisor spec), LangGraph graph (`graph.py`) with the
supervisor↔specialist topology, placeholder node boundaries (`nodes.py`)
that validate typed input and DEFER to Phase 4 (no synthesized outputs),
human-approval parking, and the engine's bounded rework path
(Reviewer→REWORK→Investigator→Reviewer, max 3 cycles). Real Extractor /
Investigator / Reviewer behavior belongs to Phase 4.

## Implementation Status (Phase 4)

Specialists implemented in `src/casefile/agents/` behind the node
boundaries above: each agent consumes its typed request contract and
produces its typed result contract through the provider → JSON →
Pydantic → domain-check pipeline, with per-agent tool allowlists
(Supervisor/Extractor: none; Investigator: 5 tools declared; Reviewer:
`document_retrieval` only), versioned prompts, and execution records.
Tools themselves arrive in Phase 5; agents receive tool names only.

## Implementation Status (Phase 5)

Tool boundaries from `docs/tools.md`: Investigator holds six tool names,
Reviewer four, Extractor one, Supervisor none. Agents call tools only via
`ToolRegistry` (`lookup()`/`fetch_documents()` helpers), which re-checks
authorization before execution; outputs are typed tool contracts mapped
into agent requests, never raw store rows.

## Implementation Status (Phase 6)

Trust boundary reaffirmed and tested: agents import no storage modules,
receive no sessions/connections/SQL, and reach data only through
`ToolRegistry` → typed tools → repositories. A source scan test
(`test_agents_never_import_storage`) enforces this structurally.

## Overview

CASEFILE implements a supervisor-worker pattern with strict separation of concerns. The Supervisor orchestrates workflow execution deterministically, while specialist agents (Extractor, Investigator, Reviewer) perform specific tasks within bounded contexts.

## Core Design Principles

### 1. No Autonomous Agent Communication

Agents do not communicate directly with each other. All communication flows through the Supervisor via typed contracts.

```
❌ WRONG: Free-form agent communication

┌───────────┐          ┌───────────────┐
│ EXTRACTOR │ ───────► │ INVESTIGATOR  │
└───────────┘  "text"  └───────────────┘


✅ CORRECT: Typed contracts via Supervisor

┌───────────┐          ┌───────────┐          ┌───────────────┐
│ EXTRACTOR │ ───────► │SUPERVISOR │ ───────► │ INVESTIGATOR  │
└───────────┘ Contract └───────────┘ Contract └───────────────┘
```

### 2. Deterministic Orchestration

The Supervisor makes all routing decisions based on typed results, not LLM suggestions. The workflow path is determined by code, not prompts.

### 3. Bounded Execution

Every agent execution has:
- Maximum input/output tokens
- Maximum execution time
- Maximum retry count
- Defined failure behavior

### 4. Explicit Contracts

All inter-agent communication uses Pydantic models. No dictionaries, no free-form text between agents.

## Agent Hierarchy

```
┌─────────────────────────────────────────────────────────────────┐
│                         SUPERVISOR                               │
│                                                                  │
│  Responsibilities:                                               │
│  • State machine management                                      │
│  • Transition decisions                                          │
│  • Budget enforcement                                            │
│  • Checkpoint coordination                                       │
│  • Failure handling                                              │
│  • Audit logging                                                 │
│                                                                  │
│  Does NOT:                                                       │
│  • Perform LLM calls                                             │
│  • Parse documents                                               │
│  • Make recommendations                                          │
└───────────────────────────┬─────────────────────────────────────┘
                            │
            ┌───────────────┼───────────────┐
            │               │               │
            ▼               ▼               ▼
    ┌───────────────┐ ┌───────────────┐ ┌───────────────┐
    │   EXTRACTOR   │ │ INVESTIGATOR  │ │   REVIEWER    │
    │               │ │               │ │               │
    │ Document      │ │ Policy        │ │ Evidence      │
    │ parsing       │ │ verification  │ │ synthesis     │
    │ Data          │ │ History check │ │ Decision      │
    │ extraction    │ │ Cost analysis │ │ Rework        │
    └───────────────┘ └───────────────┘ └───────────────┘
```

## Supervisor Agent

### Role

The Supervisor is the single orchestration authority. It is NOT an LLM-powered agent—it is deterministic code that manages workflow execution.

### Responsibilities

```python
class SupervisorResponsibilities:
    """
    The Supervisor owns:
    """

    # 1. State transitions
    def determine_next_state(
        self,
        current_state: WorkflowState,
        result: AgentResult
    ) -> WorkflowState:
        """Deterministic routing based on typed result."""
        ...

    # 2. Budget enforcement
    def check_budget_limits(
        self,
        budget_state: BudgetState
    ) -> Optional[WorkflowState]:
        """Returns terminal state if limits exceeded."""
        ...

    # 3. Checkpoint management
    async def create_checkpoint(
        self,
        workflow_run_id: UUID,
        state: WorkflowState,
        state_data: BaseModel
    ) -> Checkpoint:
        """Persists state for recovery/replay."""
        ...

    # 4. Failure handling
    async def handle_failure(
        self,
        node: str,
        error: Exception,
        state_data: BaseModel
    ) -> WorkflowState:
        """Routes to appropriate terminal or recovery state."""
        ...

    # 5. Audit logging
    async def record_transition(
        self,
        workflow_run_id: UUID,
        source: WorkflowState,
        destination: WorkflowState,
        result: AgentResult
    ) -> None:
        """Writes to immutable audit log."""
        ...
```

### Supervisor State Management

```python
class SupervisorState(BaseModel):
    """
    Internal state maintained by the Supervisor.
    """

    # Workflow identification
    workflow_run_id: UUID
    claim_id: UUID

    # Current state
    current_state: WorkflowState
    state_data: BaseModel

    # Execution tracking
    step_count: int = 0
    started_at: datetime

    # Budget tracking
    budget_state: BudgetState

    # Rework tracking
    rework_count: int = 0
    rework_history: List[ReworkRequest] = []

    # Checkpoint reference
    last_checkpoint_id: Optional[UUID] = None

    # Failure tracking
    failure_count: int = 0
    last_error: Optional[ErrorDetails] = None
```

### Supervisor Decision Logic

```python
class SupervisorDecisionEngine:
    """
    Pure deterministic logic for workflow routing.
    No LLM calls here.
    """

    def decide_next_state(
        self,
        current_state: WorkflowState,
        agent_result: AgentResult
    ) -> WorkflowState:
        """
        Determines the next state based on current state and result.
        """

        # Budget check (always)
        if self.budget_state.is_exhausted:
            return WorkflowState.BUDGET_EXHAUSTED

        # Step limit check (always)
        if self.step_count >= MAX_STEPS:
            return WorkflowState.MAX_STEPS_EXCEEDED

        # State-specific routing
        if current_state == WorkflowState.EXTRACTION:
            return self._route_from_extraction(agent_result)

        elif current_state == WorkflowState.INVESTIGATION:
            return self._route_from_investigation(agent_result)

        elif current_state == WorkflowState.REVIEW:
            return self._route_from_review(agent_result)

        else:
            raise InvalidStateError(f"Unexpected state: {current_state}")

    def _route_from_extraction(
        self,
        result: ExtractionResult
    ) -> WorkflowState:
        """Routing logic after extraction."""

        if result.status == ExtractionStatus.SUCCESS:
            return WorkflowState.INVESTIGATION

        elif result.status == ExtractionStatus.FAILURE:
            # Check if we have enough to continue
            if result.partial_data is not None:
                return WorkflowState.INVESTIGATION
            return WorkflowState.FAILED

        elif result.status == ExtractionStatus.TIMEOUT:
            return WorkflowState.ESCALATION

        else:
            return WorkflowState.FAILED

    def _route_from_investigation(
        self,
        result: InvestigationResult
    ) -> WorkflowState:
        """Routing logic after investigation."""

        if result.status == InvestigationStatus.SUCCESS:
            return WorkflowState.REVIEW

        elif result.status == InvestigationStatus.FAILURE:
            # Check if we have partial results
            if result.partial_findings is not None:
                return WorkflowState.REVIEW
            return WorkflowState.FAILED

        elif result.status == InvestigationStatus.TIMEOUT:
            return WorkflowState.ESCALATION

        else:
            return WorkflowState.FAILED

    def _route_from_review(
        self,
        result: ReviewResult
    ) -> WorkflowState:
        """Routing logic after review."""

        if result.decision == ReviewDecision.APPROVED:
            return WorkflowState.HUMAN_APPROVAL

        elif result.decision == ReviewDecision.REJECTED:
            return WorkflowState.REJECTED

        elif result.decision == ReviewDecision.REQUEST_REWORK:
            # Check rework limit
            if self.rework_count < MAX_REWORK_CYCLES:
                return WorkflowState.REWORK_LOOP
            return WorkflowState.MAX_REWORK_EXCEEDED

        else:
            return WorkflowState.FAILED
```

## Specialist Agents

### Common Agent Interface

```python
class SpecialistAgent(ABC):
    """
    Base class for all specialist agents.
    """

    agent_name: str
    max_input_tokens: int
    max_output_tokens: int
    timeout: timedelta

    @abstractmethod
    async def execute(
        self,
        request: AgentRequest
    ) -> AgentResult:
        """
        Execute the agent's primary task.

        Must:
        - Return within timeout
        - Return typed result
        - Handle errors gracefully
        - Not exceed token limits
        """
        ...

    @abstractmethod
    def validate_input(
        self,
        request: AgentRequest
    ) -> ValidationResult:
        """Validate input before execution."""
        ...

    @abstractmethod
    def validate_output(
        self,
        result: AgentResult
    ) -> ValidationResult:
        """Validate output after execution."""
        ...
```

### EXTRACTOR Agent

#### Purpose

Parse claim documents and extract structured data.

#### Input Contract

```python
class ExtractionRequest(BaseModel):
    """
    Request for the Extractor agent.
    """

    claim_id: UUID
    documents: List[Document]

    # Configuration
    extraction_types: List[ExtractionType]
    language_hint: Optional[str] = None

    # Context
    previous_extraction: Optional[ExtractionResult] = None


class Document(BaseModel):
    """
    A claim document.
    """

    document_id: UUID
    document_type: DocumentType  # POLICE_REPORT, PHOTO, REPAIR_ESTIMATE, etc.
    content: str  # Base64 encoded or text
    content_type: ContentType  # PDF, IMAGE, TEXT
    metadata: Dict[str, Any]

    @validator('content')
    def validate_content_not_empty(cls, v):
        if not v or len(v.strip()) == 0:
            raise ValueError('Document content cannot be empty')
        return v


class ExtractionType(str, Enum):
    VEHICLE_INFO = "VEHICLE_INFO"
    DRIVER_INFO = "DRIVER_INFO"
    ACCIDENT_DETAILS = "ACCIDENT_DETAILS"
    DAMAGES = "DAMAGES"
    WITNESSES = "WITNESSES"
    REPAIR_ESTIMATES = "REPAIR_ESTIMATES"
```

#### Output Contract

```python
class ExtractionResult(BaseModel):
    """
    Result from the Extractor agent.
    """

    claim_id: UUID
    status: ExtractionStatus
    extracted_data: Optional[ExtractedData]
    partial_data: Optional[PartialExtractedData]

    # Metadata
    documents_processed: int
    documents_failed: List[DocumentFailure]

    # Execution metadata
    tokens_used: TokenUsage
    execution_time_ms: int

    # Error details (if failure)
    error: Optional[ErrorDetails]


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
    vehicle: VehicleInfo

    # Driver information
    driver: DriverInfo

    # Accident details
    accident: AccidentDetails

    # Damages
    damages: List[DamageInfo]

    # Witnesses
    witnesses: List[WitnessInfo]

    # Repair estimates
    repair_estimates: List[RepairEstimate]

    # Confidence scores
    confidence: ExtractionConfidence


class ExtractionConfidence(BaseModel):
    """
    Confidence scores for extracted data.
    """

    overall_confidence: float  # 0.0 to 1.0
    vehicle_confidence: float
    driver_confidence: float
    accident_confidence: float
    damages_confidence: float
    estimates_confidence: float
```

#### Execution Logic

```python
class ExtractorAgent(SpecialistAgent):
    """
    Document extraction specialist.
    """

    agent_name = "extractor"
    max_input_tokens = 50_000
    max_output_tokens = 5_000
    timeout = timedelta(minutes=5)

    def __init__(
        self,
        llm_provider: LLMProvider,
        document_processor: DocumentProcessor,
        budget_tracker: BudgetTracker
    ):
        self.llm = llm_provider
        self.doc_processor = document_processor
        self.budget = budget_tracker

    async def execute(
        self,
        request: ExtractionRequest
    ) -> ExtractionResult:
        """
        Execute document extraction.
        """

        start_time = datetime.utcnow()

        try:
            # 1. Validate input
            validation = self.validate_input(request)
            if not validation.is_valid:
                return self._create_failure_result(
                    request.claim_id,
                    validation.errors
                )

            # 2. Process each document
            extracted_parts = []
            failures = []

            for doc in request.documents:
                try:
                    result = await self._extract_from_document(doc)
                    extracted_parts.append(result)
                except Exception as e:
                    failures.append(DocumentFailure(
                        document_id=doc.document_id,
                        error=str(e)
                    ))

            # 3. Merge extracted data
            merged_data = self._merge_extractions(extracted_parts)

            # 4. Calculate confidence
            confidence = self._calculate_confidence(merged_data, failures)

            # 5. Determine status
            status = self._determine_status(merged_data, failures)

            # 6. Create result
            return ExtractionResult(
                claim_id=request.claim_id,
                status=status,
                extracted_data=merged_data if status == ExtractionStatus.SUCCESS else None,
                partial_data=merged_data if status == ExtractionStatus.PARTIAL_SUCCESS else None,
                documents_processed=len(request.documents),
                documents_failed=failures,
                tokens_used=self.budget.get_session_usage(),
                execution_time_ms=int((datetime.utcnow() - start_time).total_seconds() * 1000),
                error=None
            )

        except asyncio.TimeoutError:
            return self._create_timeout_result(request.claim_id, start_time)

        except Exception as e:
            return self._create_failure_result(request.claim_id, [str(e)])

    async def _extract_from_document(
        self,
        document: Document
    ) -> ExtractedData:
        """
        Extract structured data from a single document.
        """

        # 1. Prepare document for LLM
        prepared_content = await self.doc_processor.prepare(document)

        # 2. Build extraction prompt
        prompt = self._build_extraction_prompt(document.document_type, prepared_content)

        # 3. Call LLM with structured output
        response = await self.llm.complete(
            prompt=prompt,
            response_model=ExtractedData,
            max_tokens=self.max_output_tokens
        )

        # 4. Validate response
        validation = self.validate_output(response)
        if not validation.is_valid:
            raise ExtractionError(f"Invalid extraction: {validation.errors}")

        return response
```

### INVESTIGATOR Agent

#### Purpose

Verify policy, check claim history, validate repair costs, identify fraud signals.

#### Input Contract

```python
class InvestigationRequest(BaseModel):
    """
    Request for the Investigator agent.
    """

    claim_id: UUID
    extracted_data: ExtractedData

    # Policy context
    policy_number: str

    # Rework context (if applicable)
    rework_instructions: Optional[ReworkInstructions] = None


class ReworkInstructions(BaseModel):
    """
    Instructions from Reviewer for additional investigation.
    """

    rework_id: UUID
    focus_areas: List[FocusArea]
    specific_questions: List[str]
    previous_issues: List[str]


class FocusArea(str, Enum):
    POLICY_COVERAGE = "POLICY_COVERAGE"
    CLAIM_HISTORY = "CLAIM_HISTORY"
    REPAIR_COSTS = "REPAIR_COSTS"
    FRAUD_INDICATORS = "FRAUD_INDICATORS"
    DAMAGE_ASSESSMENT = "DAMAGE_ASSESSMENT"
```

#### Output Contract

```python
class InvestigationResult(BaseModel):
    """
    Result from the Investigator agent.
    """

    claim_id: UUID
    status: InvestigationStatus

    # Findings
    policy_verification: PolicyVerification
    claim_history: ClaimHistory
    cost_analysis: CostAnalysis
    fraud_assessment: FraudAssessment

    # Summary
    investigation_summary: str
    flags: List[InvestigationFlag]

    # Execution metadata
    tool_calls: List[ToolCallRecord]
    tokens_used: TokenUsage
    execution_time_ms: int

    # Error details (if failure)
    error: Optional[ErrorDetails]

    # Partial results (for graceful degradation)
    partial_findings: Optional[PartialInvestigationFindings]


class InvestigationStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILURE = "FAILURE"
    TIMEOUT = "TIMEOUT"


class PolicyVerification(BaseModel):
    """
    Policy verification results.
    """

    policy_number: str
    is_valid: bool
    policyholder_name: str
    policy_type: str
    coverage_limits: CoverageLimits
    deductible: Decimal
    is_active: bool
    exclusions: List[str]
    verification_confidence: float


class ClaimHistory(BaseModel):
    """
    Claim history results.
    """

    previous_claims: List[PreviousClaim]
    total_previous_claims: int
    total_previous_payout: Decimal
    patterns: List[str]
    concerns: List[str]


class CostAnalysis(BaseModel):
    """
    Repair cost analysis results.
    """

    submitted_estimate: Decimal
    expected_range_low: Decimal
    expected_range_high: Decimal
    is_within_expected_range: bool
    variance_percentage: float
    breakdown: CostBreakdown
    recommendations: List[str]


class FraudAssessment(BaseModel):
    """
    Fraud assessment results.
    """

    risk_score: float  # 0.0 to 1.0
    risk_level: RiskLevel
    indicators: List[FraudIndicator]
    requires_manual_review: bool


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
```

#### Tools

```python
class InvestigatorTools:
    """
    Tools available to the Investigator agent.
    """

    @tool
    async def policy_lookup(
        self,
        policy_number: str
    ) -> PolicyLookupResult:
        """
        Look up policy details.

        Args:
            policy_number: The policy number to look up

        Returns:
            Policy details including coverage, limits, and status
        """
        ...

    @tool
    async def claim_history_lookup(
        self,
        policy_number: str,
        customer_id: Optional[str]
    ) -> ClaimHistoryResult:
        """
        Look up previous claims.

        Args:
            policy_number: The policy number
            customer_id: Optional customer ID for cross-policy lookup

        Returns:
            List of previous claims and patterns
        """
        ...

    @tool
    async def repair_cost_lookup(
        self,
        vehicle_make: str,
        vehicle_model: str,
        vehicle_year: int,
        damage_type: str,
        region: str
    ) -> RepairCostResult:
        """
        Look up expected repair cost range.

        Args:
            vehicle_make: Vehicle manufacturer
            vehicle_model: Vehicle model
            vehicle_year: Model year
            damage_type: Type of damage
            region: Geographic region

        Returns:
            Expected cost range and breakdown
        """
        ...

    @tool
    async def fraud_signal_lookup(
        self,
        claim_id: UUID,
        policy_number: str,
        claim_data: ExtractedData
    ) -> FraudSignalResult:
        """
        Look up fraud signals and risk indicators.

        Args:
            claim_id: Current claim ID
            policy_number: Policy number
            claim_data: Extracted claim data

        Returns:
            Fraud risk assessment and indicators
        """
        ...
```

### REVIEWER Agent

#### Purpose

Synthesize evidence from extraction and investigation, make recommendation decision, request rework if needed.

#### Input Contract

```python
class ReviewRequest(BaseModel):
    """
    Request for the Reviewer agent.
    """

    claim_id: UUID
    extracted_data: ExtractedData
    investigation_result: InvestigationResult

    # Context
    rework_count: int
    previous_rework_requests: List[ReworkRequest]
```

#### Output Contract

```python
class ReviewResult(BaseModel):
    """
    Result from the Reviewer agent.
    """

    claim_id: UUID
    decision: ReviewDecision

    # Recommendation (if approved)
    recommendation: Optional[ClaimRecommendation]

    # Rework request (if rework needed)
    rework_request: Optional[ReworkRequest]

    # Rejection details (if rejected)
    rejection_reason: Optional[str]

    # Synthesis
    evidence_summary: str
    key_findings: List[str]
    concerns: List[str]

    # Execution metadata
    tokens_used: TokenUsage
    execution_time_ms: int

    # Error details
    error: Optional[ErrorDetails]


class ReviewDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REQUEST_REWORK = "REQUEST_REWORK"


class ClaimRecommendation(BaseModel):
    """
    Final recommendation for a claim.
    """

    claim_id: UUID
    recommendation_type: RecommendationType
    estimated_payout: Optional[Decimal]
    payout_breakdown: Optional[PayoutBreakdown]
    conditions: List[str]
    notes: str
    confidence: float


class RecommendationType(str, Enum):
    APPROVE_FULL = "APPROVE_FULL"
    APPROVE_PARTIAL = "APPROVE_PARTIAL"
    APPROVE_WITH_CONDITIONS = "APPROVE_WITH_CONDITIONS"
    DENY = "DENY"
    INVESTIGATE_FURTHER = "INVESTIGATE_FURTHER"


class ReworkRequest(BaseModel):
    """
    Request for additional investigation work.
    """

    rework_id: UUID
    focus_areas: List[FocusArea]
    specific_questions: List[str]
    rationale: str
    priority: ReworkPriority
    deadline: Optional[datetime]


class ReworkPriority(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
```

#### Execution Logic

```python
class ReviewerAgent(SpecialistAgent):
    """
    Evidence synthesis and recommendation specialist.
    """

    agent_name = "reviewer"
    max_input_tokens = 30_000
    max_output_tokens = 3_000
    timeout = timedelta(minutes=5)

    def __init__(
        self,
        llm_provider: LLMProvider,
        budget_tracker: BudgetTracker
    ):
        self.llm = llm_provider
        self.budget = budget_tracker

    async def execute(
        self,
        request: ReviewRequest
    ) -> ReviewResult:
        """
        Execute evidence synthesis and recommendation.
        """

        start_time = datetime.utcnow()

        try:
            # 1. Build synthesis prompt
            prompt = self._build_synthesis_prompt(request)

            # 2. Call LLM for synthesis
            synthesis = await self.llm.complete(
                prompt=prompt,
                response_model=ReviewerSynthesis,
                max_tokens=self.max_output_tokens
            )

            # 3. Determine decision based on synthesis
            decision = self._determine_decision(synthesis, request.rework_count)

            # 4. Create result based on decision
            if decision == ReviewDecision.APPROVED:
                return self._create_approved_result(
                    request.claim_id,
                    synthesis,
                    start_time
                )

            elif decision == ReviewDecision.REQUEST_REWORK:
                return self._create_rework_result(
                    request.claim_id,
                    synthesis,
                    request.rework_count,
                    start_time
                )

            else:  # REJECTED
                return self._create_rejected_result(
                    request.claim_id,
                    synthesis,
                    start_time
                )

        except asyncio.TimeoutError:
            return self._create_timeout_result(request.claim_id, start_time)

        except Exception as e:
            return self._create_failure_result(request.claim_id, str(e), start_time)

    def _determine_decision(
        self,
        synthesis: ReviewerSynthesis,
        rework_count: int
    ) -> ReviewDecision:
        """
        Determine the review decision based on synthesis.

        This is NOT delegated to the LLM. The synthesis provides
        evidence assessment, but the decision logic is deterministic.
        """

        # If fraud risk is high, reject
        if synthesis.fraud_risk_level in [RiskLevel.HIGH, RiskLevel.CRITICAL]:
            return ReviewDecision.REJECTED

        # If coverage is insufficient, reject
        if not synthesis.coverage_adequate:
            return ReviewDecision.REJECTED

        # If there are significant concerns and rework available
        if synthesis.has_significant_concerns and rework_count < MAX_REWORK_CYCLES:
            return ReviewDecision.REQUEST_REWORK

        # If all checks pass, approve
        if synthesis.overall_confidence >= MIN_CONFIDENCE_THRESHOLD:
            return ReviewDecision.APPROVED

        # Default to rework if confidence is low
        if rework_count < MAX_REWORK_CYCLES:
            return ReviewDecision.REQUEST_REWORK

        # If max rework reached and still uncertain, reject
        return ReviewDecision.REJECTED
```

## Agent Execution Wrapper

```python
class AgentExecutor:
    """
    Wraps agent execution with:
    - Timeout enforcement
    - Budget checking
    - Error handling
    - Observability
    """

    async def execute_agent(
        self,
        agent: SpecialistAgent,
        request: AgentRequest,
        context: ExecutionContext
    ) -> AgentResult:
        """
        Execute an agent with all safety measures.
        """

        span = tracer.start_span(f"agent.{agent.agent_name}")

        try:
            # 1. Pre-execution checks
            if context.budget_state.is_exhausted:
                raise BudgetExhaustedError()

            if context.step_count >= MAX_STEPS:
                raise MaxStepsExceededError()

            # 2. Execute with timeout
            result = await asyncio.wait_for(
                agent.execute(request),
                timeout=agent.timeout.total_seconds()
            )

            # 3. Update budget
            context.budget_state.add_usage(result.tokens_used)

            # 4. Validate output
            validation = agent.validate_output(result)
            if not validation.is_valid:
                raise AgentOutputValidationError(validation.errors)

            # 5. Record success
            span.set_attribute("status", "success")

            return result

        except asyncio.TimeoutError:
            span.set_attribute("status", "timeout")
            span.record_exception(TimeoutError())
            return agent.create_timeout_result()

        except BudgetExhaustedError:
            span.set_attribute("status", "budget_exhausted")
            raise  # Propagate to Supervisor

        except Exception as e:
            span.set_attribute("status", "error")
            span.record_exception(e)
            return agent.create_error_result(e)

        finally:
            span.end()
```

## Summary

The agent architecture provides:
- **Clear separation**: Supervisor handles orchestration, specialists handle tasks
- **Typed contracts**: No free-form communication between agents
- **Bounded execution**: Every agent has explicit limits
- **Deterministic routing**: State transitions are code, not LLM decisions
- **Graceful degradation**: Partial results and failure handling
- **Observability**: Every agent execution is traced

# CASEFILE Tool Architecture

## Overview

Tools are the interface between CASEFILE agents and external systems/data. The Investigator agent uses tools to query policy information, claim history, repair costs, and fraud signals.

This document defines the tool architecture, contracts, authorization model, and implementation patterns.

## Design Principles

### 1. Typed Interface

All tools have typed inputs and outputs using Pydantic models. No loose dictionaries or untyped returns.

### 2. Authorization Required

Every tool call must be authorized. Agents cannot call tools they don't have permission for.

### 3. Timeout and Retry

All tools have timeout limits and retry policies. No tool can hang indefinitely.

### 4. Audit Logged

Every tool call is recorded in the audit trail with input, output, and metadata.

### 5. Idempotency

Tools that perform side effects must support idempotency via idempotency keys.

### 6. Mocked for Development

All tools have deterministic mock implementations for development and testing.

## Tool Taxonomy

### Available Tools

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           CASEFILE TOOLS                                     │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                      INVESTIGATOR TOOLS                               │  │
│  │                                                                       │  │
│  │  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐      │  │
│  │  │ policy_lookup   │  │ claim_history   │  │ repair_cost     │      │  │
│  │  │                 │  │ _lookup         │  │ _lookup         │      │  │
│  │  │                 │  │                 │  │                 │      │  │
│  │  │ • Policy status │  │ • Previous      │  │ • Expected      │      │  │
│  │  │ • Coverage      │  │   claims        │  │   cost range    │      │  │
│  │  │ • Limits        │  │ • Patterns      │  │ • Labor hours   │      │  │
│  │  │ • Exclusions    │  │ • Total payout  │  │ • Parts cost    │      │  │
│  │  └─────────────────┘  └─────────────────┘  └─────────────────┘      │  │
│  │                                                                       │  │
│  │  ┌─────────────────┐                                                 │  │
│  │  │ fraud_signal    │                                                 │  │
│  │  │ _lookup         │                                                 │  │
│  │  │                 │                                                 │  │
│  │  │ • Risk score    │                                                 │  │
│  │  │ • Indicators    │                                                 │  │
│  │  │ • Flags         │                                                 │  │
│  │  └─────────────────┘                                                 │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                      EXTRACTOR TOOLS                                   │  │
│  │                                                                       │  │
│  │  ┌─────────────────┐                                                 │  │
│  │  │ document_       │                                                 │  │
│  │  │ retrieval       │                                                 │  │
│  │  │                 │                                                 │  │
│  │  │ • Fetch docs    │                                                 │  │
│  │  │ • Content       │                                                 │  │
│  │  │ • Metadata      │                                                 │  │
│  │  └─────────────────┘                                                 │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Tool Interface

### Base Tool Interface

```python
from abc import ABC, abstractmethod
from typing import TypeVar, Generic, Type, Optional
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel


InputT = TypeVar('InputT', bound=BaseModel)
OutputT = TypeVar('OutputT', bound=BaseModel)


class BaseTool(ABC, Generic[InputT, OutputT]):
    """
    Base interface for all tools.
    """

    # Tool metadata
    tool_name: str
    tool_version: str = "1.0.0"
    tool_description: str

    # Type hints
    input_model: Type[InputT]
    output_model: Type[OutputT]

    # Configuration
    timeout_seconds: int = 30
    max_retries: int = 3
    retry_delay_seconds: float = 1.0

    @abstractmethod
    async def execute(
        self,
        input_data: InputT,
        context: ToolContext
    ) -> OutputT:
        """
        Execute the tool.

        Args:
            input_data: Typed input for the tool
            context: Execution context with auth, tracing, etc.

        Returns:
            Typed output from the tool

        Raises:
            ToolValidationError: If input is invalid
            ToolAuthorizationError: If not authorized
            ToolTimeoutError: If execution times out
            ToolExecutionError: If execution fails
        """
        ...

    def validate_input(self, input_data: InputT) -> ValidationResult:
        """
        Validate input before execution.
        """
        # Default: rely on Pydantic validation
        return ValidationResult(is_valid=True)

    def validate_output(self, output_data: OutputT) -> ValidationResult:
        """
        Validate output after execution.
        """
        # Default: rely on Pydantic validation
        return ValidationResult(is_valid=True)

    def get_authorization_requirements(self) -> AuthorizationRequirements:
        """
        Get authorization requirements for this tool.
        """
        return AuthorizationRequirements(
            required_permissions=[f"tool:{self.tool_name}:execute"]
        )

    def is_idempotent(self) -> bool:
        """
        Whether this tool is idempotent.

        Idempotent tools can be safely retried.
        """
        return True  # Default: all tools are idempotent (read-only)
```

### Tool Context

```python
class ToolContext(BaseModel):
    """
    Context for tool execution.
    """

    # Workflow context
    workflow_run_id: UUID
    claim_id: UUID
    trace_id: str

    # Authorization
    agent_name: str
    permissions: List[str]

    # Execution
    call_id: UUID
    parent_span_id: Optional[str] = None

    # Idempotency
    idempotency_key: Optional[str] = None

    # Budget
    budget_state: BudgetState

    # Timestamps
    started_at: datetime = Field(default_factory=datetime.utcnow)

    # Mode
    mode: ToolExecutionMode = ToolExecutionMode.LIVE


class ToolExecutionMode(str, Enum):
    LIVE = "LIVE"  # Execute against real systems
    MOCK = "MOCK"  # Use mock implementation
    REPLAY = "REPLAY"  # Use recorded output


class ValidationResult(BaseModel):
    is_valid: bool
    errors: List[str] = []


class AuthorizationRequirements(BaseModel):
    required_permissions: List[str]
    optional_permissions: List[str] = []
```

## Tool Contracts

### Policy Lookup Tool

```python
class PolicyLookupInput(BaseModel):
    """
    Input for policy_lookup tool.
    """

    policy_number: str = Field(..., description="Policy number to look up")

    @validator('policy_number')
    def validate_policy_number(cls, v):
        return v.strip().upper()


class PolicyLookupOutput(BaseModel):
    """
    Output from policy_lookup tool.
    """

    is_found: bool
    policy: Optional[PolicyVerification] = None

    # Error info
    error_code: Optional[str] = None
    error_message: Optional[str] = None


class PolicyLookupTool(BaseTool[PolicyLookupInput, PolicyLookupOutput]):
    """
    Look up policy details.

    Returns policy status, coverage, limits, and exclusions.
    """

    tool_name = "policy_lookup"
    tool_description = "Look up insurance policy details including coverage and limits"
    input_model = PolicyLookupInput
    output_model = PolicyLookupOutput

    timeout_seconds = 10
    max_retries = 3

    async def execute(
        self,
        input_data: PolicyLookupInput,
        context: ToolContext
    ) -> PolicyLookupOutput:
        """
        Execute policy lookup.
        """

        # Check authorization
        if not self._is_authorized(context):
            raise ToolAuthorizationError(
                f"Agent {context.agent_name} not authorized for policy_lookup"
            )

        # Execute based on mode
        if context.mode == ToolExecutionMode.MOCK:
            return await self._execute_mock(input_data, context)
        elif context.mode == ToolExecutionMode.REPLAY:
            return await self._execute_replay(input_data, context)
        else:
            return await self._execute_live(input_data, context)

    async def _execute_live(
        self,
        input_data: PolicyLookupInput,
        context: ToolContext
    ) -> PolicyLookupOutput:
        """
        Execute against real policy system (future).
        """

        # For Phase 0, this delegates to mock
        # In production, this would call the actual policy system API
        return await self._execute_mock(input_data, context)

    async def _execute_mock(
        self,
        input_data: PolicyLookupInput,
        context: ToolContext
    ) -> PolicyLookupOutput:
        """
        Execute using mock data.
        """

        # Deterministic mock based on policy number
        mock_data = self._get_mock_data(input_data.policy_number)

        return PolicyLookupOutput(
            is_found=mock_data is not None,
            policy=mock_data
        )

    def _get_mock_data(self, policy_number: str) -> Optional[PolicyVerification]:
        """
        Get mock policy data.

        Uses policy number hash for deterministic results.
        """

        # Seed based on policy number for determinism
        seed = hash(policy_number) % 100

        # Generate deterministic mock
        if seed < 5:  # 5% not found
            return None

        # Generate policy details
        return PolicyVerification(
            policy_number=policy_number,
            is_valid=True,
            is_active=True,
            policyholder_name=f"Policyholder {policy_number[:8]}",
            policy_type="COMPREHENSIVE",
            effective_date=date(2024, 1, 1),
            expiration_date=date(2025, 1, 1),
            coverage_limits=CoverageLimits(
                bodily_injury_per_person=Decimal("100000"),
                bodily_injury_per_accident=Decimal("300000"),
                property_damage=Decimal("50000"),
                comprehensive_deductible=Decimal("500"),
                collision_deductible=Decimal("500")
            ),
            deductible=Decimal("500"),
            exclusions=[],
            verification_confidence=0.95
        )
```

### Claim History Lookup Tool

```python
class ClaimHistoryLookupInput(BaseModel):
    """
    Input for claim_history_lookup tool.
    """

    policy_number: str
    customer_id: Optional[str] = None

    # Filters
    include_closed: bool = True
    years_back: int = Field(default=5, ge=1, le=10)


class ClaimHistoryLookupOutput(BaseModel):
    """
    Output from claim_history_lookup tool.
    """

    is_found: bool
    history: Optional[ClaimHistory] = None

    error_code: Optional[str] = None
    error_message: Optional[str] = None


class ClaimHistoryLookupTool(BaseTool[ClaimHistoryLookupInput, ClaimHistoryLookupOutput]):
    """
    Look up claim history.

    Returns previous claims, patterns, and total payouts.
    """

    tool_name = "claim_history_lookup"
    tool_description = "Look up previous claims history for a policy"
    input_model = ClaimHistoryLookupInput
    output_model = ClaimHistoryLookupOutput

    timeout_seconds = 15
    max_retries = 3

    async def execute(
        self,
        input_data: ClaimHistoryLookupInput,
        context: ToolContext
    ) -> ClaimHistoryLookupOutput:
        """
        Execute claim history lookup.
        """

        # Generate deterministic mock
        history = self._generate_mock_history(
            input_data.policy_number,
            input_data.years_back
        )

        return ClaimHistoryLookupOutput(
            is_found=True,
            history=history
        )

    def _generate_mock_history(
        self,
        policy_number: str,
        years_back: int
    ) -> ClaimHistory:
        """
        Generate deterministic mock claim history.
        """

        # Use hash for deterministic generation
        seed = hash(policy_number) % 100

        # Generate 0-3 previous claims based on seed
        num_claims = seed % 4

        previous_claims = []
        total_payout = Decimal("0")

        for i in range(num_claims):
            claim_date = date.today() - timedelta(days=(i + 1) * 365)
            amount = Decimal(str((seed * (i + 1) * 500) % 5000 + 500))

            previous_claims.append(PreviousClaim(
                claim_id=f"CLM-{policy_number[:4]}-{i}",
                claim_date=claim_date,
                claim_type=ClaimType.COLLISION if i % 2 == 0 else ClaimType.COMPREHENSIVE,
                amount=amount,
                status="CLOSED",
                at_fault=i % 2 == 0
            ))

            total_payout += amount

        return ClaimHistory(
            policy_number=policy_number,
            customer_id=f"CUST-{policy_number[:8]}",
            previous_claims=previous_claims,
            total_previous_claims=num_claims,
            total_previous_payout=total_payout,
            patterns=["frequent_small_claims"] if num_claims > 2 else [],
            concerns=[]
        )
```

### Repair Cost Lookup Tool

```python
class RepairCostLookupInput(BaseModel):
    """
    Input for repair_cost_lookup tool.
    """

    vehicle_make: str
    vehicle_model: str
    vehicle_year: int = Field(..., ge=1990, le=2025)
    damage_type: str  # "front_bumper", "rear_quarter", "windshield", etc.
    region: str = "default"  # Geographic region for labor rates

    @validator('vehicle_make', 'vehicle_model')
    def normalize_strings(cls, v):
        return v.strip().lower().title()


class RepairCostLookupOutput(BaseModel):
    """
    Output from repair_cost_lookup tool.
    """

    is_found: bool
    analysis: Optional[CostAnalysis] = None

    error_code: Optional[str] = None
    error_message: Optional[str] = None


class RepairCostLookupTool(BaseTool[RepairCostLookupInput, RepairCostLookupOutput]):
    """
    Look up expected repair cost range.

    Returns expected cost range based on vehicle and damage type.
    """

    tool_name = "repair_cost_lookup"
    tool_description = "Look up expected repair cost range for vehicle damage"
    input_model = RepairCostLookupInput
    output_model = RepairCostLookupOutput

    timeout_seconds = 10
    max_retries = 2

    # Base labor rates by region
    LABOR_RATES = {
        "default": Decimal("75"),
        "urban": Decimal("95"),
        "rural": Decimal("65"),
    }

    async def execute(
        self,
        input_data: RepairCostLookupInput,
        context: ToolContext
    ) -> RepairCostLookupOutput:
        """
        Execute repair cost lookup.
        """

        # Get base cost range
        base_range = self._get_base_cost_range(input_data.damage_type)

        # Adjust for vehicle
        vehicle_multiplier = self._get_vehicle_multiplier(
            input_data.vehicle_make,
            input_data.vehicle_year
        )

        # Calculate range
        labor_rate = self.LABOR_RATES.get(input_data.region, self.LABOR_RATES["default"])

        low = base_range["low"] * vehicle_multiplier
        high = base_range["high"] * vehicle_multiplier
        average = (low + high) / 2

        analysis = CostAnalysis(
            submitted_estimate=Decimal("0"),  # Filled by caller
            expected_range_low=low,
            expected_range_high=high,
            expected_range_average=average,
            is_within_expected_range=True,  # Calculated after comparison
            variance_percentage=0.0,
            variance_description="",
            breakdown=CostBreakdown(
                labor_hours=base_range["labor_hours"],
                labor_rate=labor_rate,
                labor_total=labor_rate * Decimal(str(base_range["labor_hours"])),
                parts_total=average * Decimal("0.4"),
                subtotal=average,
                total=average
            ),
            recommendations=[]
        )

        return RepairCostLookupOutput(
            is_found=True,
            analysis=analysis
        )

    def _get_base_cost_range(self, damage_type: str) -> Dict[str, Any]:
        """
        Get base cost range for damage type.
        """

        # Simplified cost ranges
        ranges = {
            "front_bumper": {"low": Decimal("800"), "high": Decimal("2500"), "labor_hours": 4},
            "rear_bumper": {"low": Decimal("700"), "high": Decimal("2000"), "labor_hours": 3.5},
            "front_quarter": {"low": Decimal("1200"), "high": Decimal("3500"), "labor_hours": 8},
            "rear_quarter": {"low": Decimal("1000"), "high": Decimal("3000"), "labor_hours": 6},
            "windshield": {"low": Decimal("200"), "high": Decimal("800"), "labor_hours": 2},
            "door": {"low": Decimal("600"), "high": Decimal("1800"), "labor_hours": 4},
            "hood": {"low": Decimal("500"), "high": Decimal("1500"), "labor_hours": 3},
            "trunk": {"low": Decimal("500"), "high": Decimal("1500"), "labor_hours": 3},
            "fender": {"low": Decimal("400"), "high": Decimal("1200"), "labor_hours": 3},
        }

        return ranges.get(damage_type.lower(), {"low": Decimal("500"), "high": Decimal("2000"), "labor_hours": 4})

    def _get_vehicle_multiplier(self, make: str, year: int) -> Decimal:
        """
        Get cost multiplier based on vehicle.
        """

        # Luxury brands have higher costs
        luxury = ["bmw", "mercedes", "audi", "lexus", "cadillac", "porsche"]
        premium = ["acura", "infiniti", "volvo", "lincoln"]

        make_lower = make.lower()

        if make_lower in luxury:
            multiplier = Decimal("1.8")
        elif make_lower in premium:
            multiplier = Decimal("1.4")
        else:
            multiplier = Decimal("1.0")

        # Newer cars slightly more expensive
        if year >= 2022:
            multiplier *= Decimal("1.1")

        return multiplier
```

### Fraud Signal Lookup Tool

```python
class FraudSignalLookupInput(BaseModel):
    """
    Input for fraud_signal_lookup tool.
    """

    claim_id: UUID
    policy_number: str

    # Claim data for analysis
    claim_data: ExtractedData

    # Optional flags
    include_behavioral_analysis: bool = True


class FraudSignalLookupOutput(BaseModel):
    """
    Output from fraud_signal_lookup tool.
    """

    assessment: FraudAssessment

    error_code: Optional[str] = None
    error_message: Optional[str] = None


class FraudSignalLookupTool(BaseTool[FraudSignalLookupInput, FraudSignalLookupOutput]):
    """
    Look up fraud signals and risk indicators.

    Returns fraud risk score and indicators.
    """

    tool_name = "fraud_signal_lookup"
    tool_description = "Assess fraud risk for a claim"
    input_model = FraudSignalLookupInput
    output_model = FraudSignalLookupOutput

    timeout_seconds = 20
    max_retries = 2

    async def execute(
        self,
        input_data: FraudSignalLookupInput,
        context: ToolContext
    ) -> FraudSignalLookupOutput:
        """
        Execute fraud signal lookup.
        """

        # Analyze claim for fraud indicators
        indicators = self._analyze_fraud_indicators(
            input_data.claim_data,
            input_data.policy_number
        )

        # Calculate risk score
        risk_score = self._calculate_risk_score(indicators)

        # Determine risk level
        risk_level = self._determine_risk_level(risk_score)

        assessment = FraudAssessment(
            risk_score=risk_score,
            risk_level=risk_level,
            indicators=indicators,
            requires_manual_review=risk_score > 0.7
        )

        return FraudSignalLookupOutput(assessment=assessment)

    def _analyze_fraud_indicators(
        self,
        claim_data: ExtractedData,
        policy_number: str
    ) -> List[FraudIndicator]:
        """
        Analyze claim for fraud indicators.
        """

        indicators = []
        seed = hash(policy_number) % 100

        # Check for suspicious patterns (simplified mock)

        # 1. Recently purchased policy
        if seed < 10:
            indicators.append(FraudIndicator(
                indicator_type="RECENT_POLICY",
                description="Policy purchased within 30 days of claim",
                severity="high"
            ))

        # 2. Multiple estimates with wide variance
        if len(claim_data.repair_estimates) > 1:
            estimates = [e.total_cost for e in claim_data.repair_estimates]
            variance = (max(estimates) - min(estimates)) / min(estimates)
            if variance > Decimal("0.5"):
                indicators.append(FraudIndicator(
                    indicator_type="ESTIMATE_VARIANCE",
                    description="High variance between repair estimates",
                    severity="medium"
                ))

        # 3. High damage for reported accident type
        if seed > 80:
            indicators.append(FraudIndicator(
                indicator_type="DAMAGE_SEVERITY",
                description="Damage severity exceeds typical for accident type",
                severity="low"
            ))

        return indicators

    def _calculate_risk_score(
        self,
        indicators: List[FraudIndicator]
    ) -> float:
        """
        Calculate overall risk score from indicators.
        """

        if not indicators:
            return 0.0

        severity_weights = {
            "low": 0.1,
            "medium": 0.3,
            "high": 0.5,
            "critical": 0.8
        }

        total_weight = sum(severity_weights.get(i.severity, 0.1) for i in indicators)

        # Cap at 1.0
        return min(1.0, total_weight)

    def _determine_risk_level(self, risk_score: float) -> RiskLevel:
        """
        Determine risk level from score.
        """

        if risk_score < 0.2:
            return RiskLevel.LOW
        elif risk_score < 0.5:
            return RiskLevel.MEDIUM
        elif risk_score < 0.8:
            return RiskLevel.HIGH
        else:
            return RiskLevel.CRITICAL
```

## Tool Executor

### Execution Wrapper

```python
from datetime import datetime, timedelta
import asyncio


class ToolExecutor:
    """
    Executes tools with all safety measures.
    """

    def __init__(
        self,
        tools: Dict[str, BaseTool],
        audit_repository: AuditEventRepository,
        budget_tracker: BudgetTracker,
        tool_output_recorder: Optional[ToolOutputRecorder] = None
    ):
        self.tools = tools
        self.audit = audit_repository
        self.budget = budget_tracker
        self.output_recorder = tool_output_recorder

    async def execute(
        self,
        tool_name: str,
        input_data: BaseModel,
        context: ToolContext
    ) -> BaseModel:
        """
        Execute a tool with full safety wrapper.
        """

        # 1. Get tool
        if tool_name not in self.tools:
            raise ToolNotFoundError(f"Tool '{tool_name}' not found")

        tool = self.tools[tool_name]

        # 2. Validate input
        validation = tool.validate_input(input_data)
        if not validation.is_valid:
            raise ToolValidationError(f"Invalid input: {validation.errors}")

        # 3. Check authorization
        if not self._is_authorized(tool, context):
            raise ToolAuthorizationError(
                f"Agent {context.agent_name} not authorized for {tool_name}"
            )

        # 4. Check budget
        can_proceed, reason = self.budget.check_can_proceed(context.budget_state)
        if not can_proceed:
            raise ToolBudgetExhaustedError(f"Budget exhausted: {reason}")

        # 5. Record start
        call_record = ToolCallRecord(
            call_id=context.call_id,
            workflow_run_id=context.workflow_run_id,
            tool_name=tool_name,
            tool_version=tool.tool_version,
            input=input_data.model_dump(),
            status=ToolCallStatus.RUNNING,
            started_at=datetime.utcnow(),
            authorized=True
        )

        # 6. Execute with timeout and retry
        try:
            result = await self._execute_with_retry(
                tool,
                input_data,
                context
            )

            call_record.status = ToolCallStatus.SUCCESS
            call_record.output = result.model_dump()
            call_record.completed_at = datetime.utcnow()
            call_record.execution_time_ms = int(
                (call_record.completed_at - call_record.started_at).total_seconds() * 1000
            )

        except asyncio.TimeoutError:
            call_record.status = ToolCallStatus.TIMEOUT
            call_record.completed_at = datetime.utcnow()
            call_record.error = "Tool execution timed out"
            raise ToolTimeoutError(f"Tool {tool_name} timed out after {tool.timeout_seconds}s")

        except Exception as e:
            call_record.status = ToolCallStatus.FAILURE
            call_record.completed_at = datetime.utcnow()
            call_record.error = str(e)
            raise

        finally:
            # 7. Record audit event
            await self._record_tool_call(call_record, context)

            # 8. Record output for replay
            if self.output_recorder and call_record.status == ToolCallStatus.SUCCESS:
                self.output_recorder.record(
                    tool_name,
                    context.call_id,
                    call_record.output
                )

        return result

    async def _execute_with_retry(
        self,
        tool: BaseTool,
        input_data: BaseModel,
        context: ToolContext
    ) -> BaseModel:
        """
        Execute with retry logic.
        """

        last_error = None

        for attempt in range(tool.max_retries + 1):
            try:
                # Execute with timeout
                result = await asyncio.wait_for(
                    tool.execute(input_data, context),
                    timeout=tool.timeout_seconds
                )

                # Validate output
                validation = tool.validate_output(result)
                if not validation.is_valid:
                    raise ToolExecutionError(f"Invalid output: {validation.errors}")

                return result

            except asyncio.TimeoutError:
                raise  # Don't retry timeouts

            except (ToolValidationError, ToolAuthorizationError):
                raise  # Don't retry validation/authorization errors

            except Exception as e:
                last_error = e

                if attempt < tool.max_retries:
                    # Wait before retry
                    await asyncio.sleep(tool.retry_delay_seconds * (attempt + 1))

                    # Record retry
                    await self._record_retry(tool, context, attempt, e)

        # All retries exhausted
        raise ToolExecutionError(
            f"Tool {tool.tool_name} failed after {tool.max_retries} retries: {last_error}"
        )

    def _is_authorized(
        self,
        tool: BaseTool,
        context: ToolContext
    ) -> bool:
        """
        Check if agent is authorized to call tool.
        """

        requirements = tool.get_authorization_requirements()

        for permission in requirements.required_permissions:
            if permission not in context.permissions:
                return False

        return True

    async def _record_tool_call(
        self,
        record: ToolCallRecord,
        context: ToolContext
    ) -> None:
        """
        Record tool call in audit log.
        """

        event = AuditEvent(
            workflow_run_id=context.workflow_run_id,
            claim_id=context.claim_id,
            trace_id=context.trace_id,
            event_type=AuditEventType.TOOL_CALL_COMPLETED if record.status == ToolCallStatus.SUCCESS else AuditEventType.TOOL_CALL_FAILED,
            actor_type="AGENT",
            actor_id=context.agent_name,
            actor_name=context.agent_name,
            action=f"Called tool {record.tool_name}",
            details={
                "call_id": str(record.call_id),
                "tool_name": record.tool_name,
                "tool_version": record.tool_version,
                "status": record.status,
                "execution_time_ms": record.execution_time_ms,
                "retry_count": record.retry_count
            },
            result="SUCCESS" if record.status == ToolCallStatus.SUCCESS else "FAILURE"
        )

        await self.audit.append(event)

    async def _record_retry(
        self,
        tool: BaseTool,
        context: ToolContext,
        attempt: int,
        error: Exception
    ) -> None:
        """
        Record retry attempt.
        """

        event = AuditEvent(
            workflow_run_id=context.workflow_run_id,
            claim_id=context.claim_id,
            trace_id=context.trace_id,
            event_type=AuditEventType.RETRY_ATTEMPTED,
            actor_type="SYSTEM",
            actor_id="tool_executor",
            actor_name="Tool Executor",
            action=f"Retry {attempt + 1} for tool {tool.tool_name}",
            details={
                "tool_name": tool.tool_name,
                "attempt": attempt + 1,
                "max_retries": tool.max_retries,
                "error": str(error)
            },
            result="FAILURE"
        )

        await self.audit.append(event)
```

## Tool Registry

### Registration

```python
class ToolRegistry:
    """
    Registry for all available tools.
    """

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
        self._tool_configs: Dict[str, ToolConfig] = {}

    def register(
        self,
        tool: BaseTool,
        config: Optional[ToolConfig] = None
    ) -> None:
        """
        Register a tool.
        """

        self._tools[tool.tool_name] = tool
        self._tool_configs[tool.tool_name] = config or ToolConfig()

    def get(self, tool_name: str) -> BaseTool:
        """
        Get a tool by name.
        """

        if tool_name not in self._tools:
            raise ToolNotFoundError(f"Tool '{tool_name}' not registered")

        return self._tools[tool_name]

    def list_tools(self) -> List[str]:
        """
        List all registered tools.
        """

        return list(self._tools.keys())

    def get_tools_for_agent(
        self,
        agent_name: str
    ) -> List[str]:
        """
        Get tools available to an agent.
        """

        return [
            name for name, config in self._tool_configs.items()
            if agent_name in config.allowed_agents
        ]


# Default tool registry
def create_default_tool_registry() -> ToolRegistry:
    """
    Create registry with default tools.
    """

    registry = ToolRegistry()

    # Register Investigator tools
    registry.register(
        PolicyLookupTool(),
        ToolConfig(allowed_agents=["investigator"])
    )

    registry.register(
        ClaimHistoryLookupTool(),
        ToolConfig(allowed_agents=["investigator"])
    )

    registry.register(
        RepairCostLookupTool(),
        ToolConfig(allowed_agents=["investigator"])
    )

    registry.register(
        FraudSignalLookupTool(),
        ToolConfig(allowed_agents=["investigator"])
    )

    # Register Extractor tools
    registry.register(
        DocumentRetrievalTool(),
        ToolConfig(allowed_agents=["extractor"])
    )

    return registry


@dataclass
class ToolConfig:
    """
    Configuration for a tool.
    """

    allowed_agents: List[str] = field(default_factory=list)
    rate_limit_per_minute: int = 60
    cache_ttl_seconds: int = 300
```

## Summary

The tool architecture provides:
- **Typed interface**: All tools have Pydantic input/output models
- **Authorization**: Agents can only call authorized tools
- **Safety**: Timeout, retry, and budget checking
- **Auditability**: Every tool call is logged
- **Idempotency**: Support for idempotent operations
- **Mocking**: Deterministic mock implementations for development
- **Replay**: Recorded outputs for deterministic replay

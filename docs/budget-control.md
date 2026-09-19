# CASEFILE Budget Control and Termination Architecture

## Overview

CASEFILE enforces hard limits on resource consumption to ensure predictable costs and guaranteed workflow termination. Budget control is implemented in code, not delegated to LLM prompts or external systems.

This document defines the budget model, enforcement mechanisms, and termination guarantees.

## Design Principles

### 1. Code-Level Enforcement

Budgets are enforced in Python code. The LLM cannot bypass limits by generating creative responses.

### 2. Pre-Call Checking

Budgets are checked BEFORE resource-consuming operations (LLM calls, tool calls). Post-hoc accounting alone is insufficient.

### 3. Fail-Safe Defaults

When in doubt, terminate. It's better to fail a workflow than to exceed budgets silently.

### 4. Granular Tracking

Track multiple resource dimensions: tokens, cost, steps, time, and tool calls.

### 5. Immediate Termination

When a limit is exceeded, the workflow immediately transitions to the appropriate terminal state.

## Budget Dimensions

### Tracked Resources

| Dimension | Unit | Storage Type | Checkpointed |
|-----------|------|--------------|--------------|
| Input tokens | Count | Integer | Yes |
| Output tokens | Count | Integer | Yes |
| Total tokens | Count | Integer | Yes |
| Estimated cost | USD | Decimal | Yes |
| Workflow steps | Count | Integer | Yes |
| Execution time | Seconds | Integer | Yes |
| Tool calls | Count | Integer | Yes |
| Rework cycles | Count | Integer | Yes |

### Budget Limits

```python
from decimal import Decimal
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BudgetLimits:
    """
    Hard limits for workflow execution.

    These are NOT suggestions. Exceeding any limit triggers immediate termination.
    """

    # Token limits
    max_input_tokens: int = 100_000
    max_output_tokens: int = 20_000
    max_total_tokens: int = 150_000

    # Cost limit
    max_cost_usd: Decimal = Decimal("5.00")

    # Execution limits
    max_steps: int = 50
    max_execution_time_seconds: int = 1800  # 30 minutes
    max_tool_calls: int = 100

    # Rework limit
    max_rework_cycles: int = 3

    # Node-specific timeouts (in seconds)
    node_timeouts: dict = field(default_factory=lambda: {
        "extractor": 300,      # 5 minutes
        "investigator": 600,   # 10 minutes
        "reviewer": 300,       # 5 minutes
    })

    # Warning thresholds (percentage of limit)
    warning_threshold: float = 0.8  # Warn at 80% of limit
```

### Budget State Model

```python
from datetime import datetime, timedelta
from enum import Enum
from pydantic import BaseModel, Field, computed_field
from uuid import UUID


class BudgetState(BaseModel):
    """
    Current budget usage and status for a workflow.
    """

    workflow_run_id: UUID

    # Token usage
    input_tokens_used: int = 0
    output_tokens_used: int = 0

    # Cost tracking
    estimated_cost_usd: Decimal = Decimal("0.00")

    # Execution tracking
    steps_completed: int = 0
    elapsed_time_seconds: int = 0
    tool_calls_made: int = 0

    # Rework tracking
    rework_cycles: int = 0

    # Limits reference
    limits: BudgetLimits

    # Status
    is_exhausted: bool = False
    exhaustion_reason: Optional[str] = None
    exhaustion_timestamp: Optional[datetime] = None

    # Warnings
    warnings_issued: list = []

    # Timing
    started_at: datetime = Field(default_factory=datetime.utcnow)
    last_updated_at: datetime = Field(default_factory=datetime.utcnow)

    @computed_field
    @property
    def total_tokens_used(self) -> int:
        return self.input_tokens_used + self.output_tokens_used

    @computed_field
    @property
    def remaining_tokens(self) -> int:
        return max(0, self.limits.max_total_tokens - self.total_tokens_used)

    @computed_field
    @property
    def remaining_cost(self) -> Decimal:
        return max(Decimal("0"), self.limits.max_cost_usd - self.estimated_cost_usd)

    @computed_field
    @property
    def remaining_steps(self) -> int:
        return max(0, self.limits.max_steps - self.steps_completed)

    @computed_field
    @property
    def remaining_time_seconds(self) -> int:
        elapsed = (datetime.utcnow() - self.started_at).total_seconds()
        return max(0, int(self.limits.max_execution_time_seconds - elapsed))

    @computed_field
    @property
    def utilization_percentage(self) -> dict:
        """Calculate utilization percentage for each dimension."""
        return {
            "tokens": (self.total_tokens_used / self.limits.max_total_tokens) * 100,
            "cost": float(self.estimated_cost_usd / self.limits.max_cost_usd) * 100,
            "steps": (self.steps_completed / self.limits.max_steps) * 100,
            "time": (self.elapsed_time_seconds / self.limits.max_execution_time_seconds) * 100,
            "tool_calls": (self.tool_calls_made / self.limits.max_tool_calls) * 100,
        }
```

## Budget Tracker

### Core Interface

```python
from abc import ABC, abstractmethod
from typing import Optional, Tuple


class BudgetTracker(ABC):
    """
    Tracks and enforces budget limits.
    """

    @abstractmethod
    def check_can_proceed(
        self,
        state: BudgetState,
        requested_tokens: Optional[int] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if execution can proceed.

        Returns:
            (can_proceed, reason_if_not)
        """
        ...

    @abstractmethod
    def record_usage(
        self,
        state: BudgetState,
        tokens: TokenUsage,
        cost: Decimal
    ) -> BudgetState:
        """
        Record resource usage and return updated state.
        """
        ...

    @abstractmethod
    def record_step(
        self,
        state: BudgetState
    ) -> BudgetState:
        """
        Record a completed step.
        """
        ...

    @abstractmethod
    def record_tool_call(
        self,
        state: BudgetState
    ) -> BudgetState:
        """
        Record a tool call.
        """
        ...

    @abstractmethod
    def check_time_limit(
        self,
        state: BudgetState
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if time limit has been exceeded.
        """
        ...
```

### Implementation

```python
from datetime import datetime


class DefaultBudgetTracker(BudgetTracker):
    """
    Default budget tracker implementation.
    """

    def __init__(self, limits: BudgetLimits):
        self.limits = limits

    def check_can_proceed(
        self,
        state: BudgetState,
        requested_tokens: Optional[int] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Check all limits before proceeding.
        """

        # 1. Check if already exhausted
        if state.is_exhausted:
            return False, state.exhaustion_reason

        # 2. Check token limit
        if state.total_tokens_used >= self.limits.max_total_tokens:
            return False, "MAX_TOKENS_EXCEEDED"

        if requested_tokens:
            projected = state.total_tokens_used + requested_tokens
            if projected > self.limits.max_total_tokens:
                return False, "INSUFFICIENT_TOKEN_BUDGET"

        # 3. Check cost limit
        if state.estimated_cost_usd >= self.limits.max_cost_usd:
            return False, "MAX_COST_EXCEEDED"

        # 4. Check step limit
        if state.steps_completed >= self.limits.max_steps:
            return False, "MAX_STEPS_EXCEEDED"

        # 5. Check time limit
        elapsed = (datetime.utcnow() - state.started_at).total_seconds()
        if elapsed >= self.limits.max_execution_time_seconds:
            return False, "MAX_TIME_EXCEEDED"

        # 6. Check tool call limit
        if state.tool_calls_made >= self.limits.max_tool_calls:
            return False, "MAX_TOOL_CALLS_EXCEEDED"

        # 7. Check rework limit
        if state.rework_cycles >= self.limits.max_rework_cycles:
            return False, "MAX_REWORK_EXCEEDED"

        # All checks passed
        return True, None

    def record_usage(
        self,
        state: BudgetState,
        tokens: TokenUsage,
        cost: Decimal
    ) -> BudgetState:
        """
        Record token and cost usage.
        """

        # Update usage
        state.input_tokens_used += tokens.input_tokens
        state.output_tokens_used += tokens.output_tokens
        state.estimated_cost_usd += cost
        state.last_updated_at = datetime.utcnow()

        # Check limits
        violation = self._check_violations(state)
        if violation:
            state.is_exhausted = True
            state.exhaustion_reason = violation
            state.exhaustion_timestamp = datetime.utcnow()

        # Check warnings
        self._check_warnings(state)

        return state

    def record_step(
        self,
        state: BudgetState
    ) -> BudgetState:
        """
        Record a completed step.
        """

        state.steps_completed += 1
        state.last_updated_at = datetime.utcnow()

        # Check step limit
        if state.steps_completed >= self.limits.max_steps:
            state.is_exhausted = True
            state.exhaustion_reason = "MAX_STEPS_EXCEEDED"
            state.exhaustion_timestamp = datetime.utcnow()

        return state

    def record_tool_call(
        self,
        state: BudgetState
    ) -> BudgetState:
        """
        Record a tool call.
        """

        state.tool_calls_made += 1
        state.last_updated_at = datetime.utcnow()

        # Check tool call limit
        if state.tool_calls_made >= self.limits.max_tool_calls:
            state.is_exhausted = True
            state.exhaustion_reason = "MAX_TOOL_CALLS_EXCEEDED"
            state.exhaustion_timestamp = datetime.utcnow()

        return state

    def check_time_limit(
        self,
        state: BudgetState
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if time limit has been exceeded.
        """

        elapsed = (datetime.utcnow() - state.started_at).total_seconds()
        state.elapsed_time_seconds = int(elapsed)

        if elapsed >= self.limits.max_execution_time_seconds:
            state.is_exhausted = True
            state.exhaustion_reason = "MAX_TIME_EXCEEDED"
            state.exhaustion_timestamp = datetime.utcnow()
            return False, "MAX_TIME_EXCEEDED"

        return True, None

    def _check_violations(self, state: BudgetState) -> Optional[str]:
        """Check for any limit violations."""

        if state.total_tokens_used > self.limits.max_total_tokens:
            return "MAX_TOKENS_EXCEEDED"

        if state.estimated_cost_usd > self.limits.max_cost_usd:
            return "MAX_COST_EXCEEDED"

        return None

    def _check_warnings(self, state: BudgetState) -> None:
        """Check and issue warnings for approaching limits."""

        threshold = self.limits.warning_threshold
        utilization = state.utilization_percentage

        for dimension, percentage in utilization.items():
            if percentage >= threshold * 100:
                warning = f"{dimension.upper()}_APPROACHING_LIMIT"
                if warning not in state.warnings_issued:
                    state.warnings_issued.append(warning)
                    # Emit warning event
                    self._emit_warning_event(state, dimension, percentage)

    def _emit_warning_event(
        self,
        state: BudgetState,
        dimension: str,
        percentage: float
    ) -> None:
        """Emit a budget warning event."""

        event = BudgetWarningEvent(
            workflow_run_id=state.workflow_run_id,
            dimension=dimension,
            current_usage=percentage,
            limit=100.0,
            threshold=self.limits.warning_threshold * 100
        )

        # Publish to observability
        observability.emit_budget_warning(event)
```

## Cost Calculation

### Provider-Agnostic Cost Model

```python
from typing import Dict


class CostCalculator:
    """
    Calculates costs for LLM usage across different providers.
    """

    # Cost per 1K tokens (as of documentation date)
    # These should be configurable and updated as prices change
    DEFAULT_COSTS: Dict[str, Dict[str, Decimal]] = {
        "openai": {
            "gpt-4o": {"input": Decimal("0.005"), "output": Decimal("0.015")},
            "gpt-4o-mini": {"input": Decimal("0.00015"), "output": Decimal("0.0006")},
            "gpt-4-turbo": {"input": Decimal("0.01"), "output": Decimal("0.03")},
            "gpt-3.5-turbo": {"input": Decimal("0.0005"), "output": Decimal("0.0015")},
        },
        "anthropic": {
            "claude-3-5-sonnet": {"input": Decimal("0.003"), "output": Decimal("0.015")},
            "claude-3-opus": {"input": Decimal("0.015"), "output": Decimal("0.075")},
            "claude-3-haiku": {"input": Decimal("0.00025"), "output": Decimal("0.00125")},
        },
        "local": {
            "default": {"input": Decimal("0.00"), "output": Decimal("0.00")},
        }
    }

    def __init__(self, custom_costs: Optional[Dict] = None):
        self.costs = self.DEFAULT_COSTS.copy()
        if custom_costs:
            self._merge_custom_costs(custom_costs)

    def calculate_cost(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int
    ) -> Decimal:
        """
        Calculate cost for an LLM call.

        Args:
            provider: Provider name (e.g., "openai")
            model: Model name (e.g., "gpt-4o")
            input_tokens: Number of input tokens
            output_tokens: Number of output tokens

        Returns:
            Estimated cost in USD
        """

        provider_costs = self.costs.get(provider, {})
        model_costs = provider_costs.get(model, {"input": Decimal("0"), "output": Decimal("0")})

        input_cost = (Decimal(input_tokens) / 1000) * model_costs["input"]
        output_cost = (Decimal(output_tokens) / 1000) * model_costs["output"]

        return input_cost + output_cost

    def estimate_call_cost(
        self,
        provider: str,
        model: str,
        estimated_input_tokens: int,
        max_output_tokens: int
    ) -> Decimal:
        """
        Estimate cost for a planned LLM call.
        """

        return self.calculate_cost(
            provider,
            model,
            estimated_input_tokens,
            max_output_tokens
        )

    def _merge_custom_costs(self, custom: Dict) -> None:
        """Merge custom cost configurations."""

        for provider, models in custom.items():
            if provider not in self.costs:
                self.costs[provider] = {}

            for model, rates in models.items():
                self.costs[provider][model] = rates
```

## Budget Enforcement Points

### Before LLM Call

```python
class LLMBudgetInterceptor:
    """
    Intercepts LLM calls to enforce budget limits.
    """

    def __init__(
        self,
        budget_tracker: BudgetTracker,
        cost_calculator: CostCalculator
    ):
        self.tracker = budget_tracker
        self.calculator = cost_calculator

    async def before_call(
        self,
        state: BudgetState,
        provider: str,
        model: str,
        estimated_input_tokens: int,
        max_output_tokens: int
    ) -> None:
        """
        Check budget before LLM call.

        Raises:
            BudgetExhaustedError: If budget is insufficient
        """

        # 1. Check if we can proceed
        can_proceed, reason = self.tracker.check_can_proceed(state)
        if not can_proceed:
            raise BudgetExhaustedError(
                f"Budget exhausted: {reason}",
                reason=reason
            )

        # 2. Estimate cost
        estimated_cost = self.calculator.estimate_call_cost(
            provider,
            model,
            estimated_input_tokens,
            max_output_tokens
        )

        # 3. Check if we have budget for this call
        remaining_cost = state.remaining_cost
        if estimated_cost > remaining_cost:
            raise BudgetExhaustedError(
                f"Insufficient budget for LLM call. "
                f"Estimated: ${estimated_cost}, Remaining: ${remaining_cost}",
                reason="INSUFFICIENT_COST_BUDGET"
            )

        # 4. Check token budget
        total_estimated = estimated_input_tokens + max_output_tokens
        if total_estimated > state.remaining_tokens:
            raise BudgetExhaustedError(
                f"Insufficient token budget. "
                f"Estimated: {total_estimated}, Remaining: {state.remaining_tokens}",
                reason="INSUFFICIENT_TOKEN_BUDGET"
            )

    async def after_call(
        self,
        state: BudgetState,
        provider: str,
        model: str,
        actual_input_tokens: int,
        actual_output_tokens: int
    ) -> BudgetState:
        """
        Record actual usage after LLM call.
        """

        # Calculate actual cost
        actual_cost = self.calculator.calculate_cost(
            provider,
            model,
            actual_input_tokens,
            actual_output_tokens
        )

        # Record usage
        tokens = TokenUsage(
            input_tokens=actual_input_tokens,
            output_tokens=actual_output_tokens,
            total_tokens=actual_input_tokens + actual_output_tokens
        )

        return self.tracker.record_usage(state, tokens, actual_cost)
```

### Before Tool Call

```python
class ToolBudgetInterceptor:
    """
    Intercepts tool calls to enforce budget limits.
    """

    def __init__(self, budget_tracker: BudgetTracker):
        self.tracker = budget_tracker

    async def before_call(
        self,
        state: BudgetState
    ) -> None:
        """
        Check budget before tool call.

        Raises:
            BudgetExhaustedError: If tool call limit exceeded
        """

        # Check tool call limit
        if state.tool_calls_made >= state.limits.max_tool_calls:
            raise BudgetExhaustedError(
                f"Tool call limit exceeded: {state.tool_calls_made}/{state.limits.max_tool_calls}",
                reason="MAX_TOOL_CALLS_EXCEEDED"
            )

        # Check step limit
        if state.steps_completed >= state.limits.max_steps:
            raise BudgetExhaustedError(
                f"Step limit exceeded: {state.steps_completed}/{state.limits.max_steps}",
                reason="MAX_STEPS_EXCEEDED"
            )

    async def after_call(
        self,
        state: BudgetState
    ) -> BudgetState:
        """
        Record tool call after completion.
        """

        return self.tracker.record_tool_call(state)
```

### Before State Transition

```python
class TransitionBudgetInterceptor:
    """
    Intercepts state transitions to enforce budget limits.
    """

    def __init__(self, budget_tracker: BudgetTracker):
        self.tracker = budget_tracker

    async def before_transition(
        self,
        state: BudgetState
    ) -> None:
        """
        Check budget before state transition.
        """

        # Check time limit
        can_proceed, reason = self.tracker.check_time_limit(state)
        if not can_proceed:
            raise BudgetExhaustedError(
                f"Time limit exceeded",
                reason=reason
            )

        # Check step limit
        if state.steps_completed >= state.limits.max_steps:
            raise BudgetExhaustedError(
                f"Step limit exceeded",
                reason="MAX_STEPS_EXCEEDED"
            )

    async def after_transition(
        self,
        state: BudgetState
    ) -> BudgetState:
        """
        Record step after transition.
        """

        return self.tracker.record_step(state)
```

## Termination Guarantees

### Termination Analysis

CASEFILE guarantees termination through multiple mechanisms:

1. **Finite State Machine**: The workflow is a directed graph with no unbounded cycles.
2. **Bounded Rework**: Maximum rework cycles prevent infinite loops.
3. **Step Limit**: Absolute maximum on node executions.
4. **Time Limit**: Wall-clock timeout for overall execution.
5. **Budget Limit**: Resource consumption cap.

### Termination Proof

```
Theorem: Every CASEFILE workflow execution terminates.

Proof:
1. The workflow graph G has a finite set of states S.
2. Every state s ∈ S has a finite set of outgoing transitions T(s).
3. Every transition leads to either:
   a. A terminal state (APPROVED, REJECTED, FAILED, etc.)
   b. A non-terminal state with an incrementing counter (step_count, rework_count)
4. Counters are bounded by constants (MAX_STEPS, MAX_REWORK_CYCLES).
5. Therefore, no infinite path exists in G.
6. Budget exhaustion triggers immediate transition to BUDGET_EXHAUSTED.
7. Time limit triggers immediate transition to TIMEOUT.
8. QED: All executions reach a terminal state.
```

### Termination State Mapping

```python
TERMINAL_STATES = {
    # Normal termination
    WorkflowState.APPROVED: TerminationCategory.SUCCESS,
    WorkflowState.REJECTED: TerminationCategory.FAILURE,
    WorkflowState.FAILED: TerminationCategory.FAILURE,

    # Exceptional termination
    WorkflowState.ESCALATION: TerminationCategory.EXCEPTION,
    WorkflowState.BUDGET_EXHAUSTED: TerminationCategory.EXCEPTION,
    WorkflowState.TIMEOUT: TerminationCategory.EXCEPTION,
    WorkflowState.MAX_STEPS_EXCEEDED: TerminationCategory.EXCEPTION,
    WorkflowState.MAX_REWORK_EXCEEDED: TerminationCategory.EXCEPTION,
}


class TerminationCategory(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    EXCEPTION = "EXCEPTION"
```

### Termination Handler

```python
class TerminationHandler:
    """
    Handles workflow termination.
    """

    async def terminate_workflow(
        self,
        workflow_run_id: UUID,
        terminal_state: WorkflowState,
        reason: str,
        context: ExecutionContext
    ) -> None:
        """
        Terminate a workflow execution.
        """

        # 1. Update workflow run
        await self.workflow_repository.update(
            workflow_run_id,
            {
                "current_state": terminal_state,
                "terminal_state": terminal_state,
                "termination_reason": reason,
                "completed_at": datetime.utcnow()
            }
        )

        # 2. Create final checkpoint
        await self.checkpoint_manager.create_checkpoint(
            workflow_run_id=workflow_run_id,
            state=terminal_state,
            state_data=context.state_data,
            checkpoint_type=CheckpointType.AFTER_TRANSITION,
            budget_state=context.budget_state
        )

        # 3. Record audit event
        await self.audit_repository.append(AuditEvent(
            workflow_run_id=workflow_run_id,
            claim_id=context.claim_id,
            trace_id=context.trace_id,
            event_type=AuditEventType.WORKFLOW_COMPLETED,
            actor_type="SYSTEM",
            actor_id="termination_handler",
            actor_name="Termination Handler",
            action=f"Workflow terminated: {terminal_state}",
            details={
                "terminal_state": terminal_state,
                "reason": reason,
                "step_count": context.step_count,
                "total_tokens": context.budget_state.total_tokens_used,
                "total_cost": str(context.budget_state.estimated_cost_usd)
            },
            result="SUCCESS" if terminal_state == WorkflowState.APPROVED else "FAILURE",
            to_state=terminal_state
        ))

        # 4. Emit termination event
        observability.emit_workflow_termination(
            workflow_run_id=workflow_run_id,
            terminal_state=terminal_state,
            reason=reason
        )

        # 5. Send notification (if configured)
        if terminal_state in NOTIFICATION_STATES:
            await self.notification_service.notify(
                workflow_run_id=workflow_run_id,
                event="workflow_terminated",
                data={
                    "terminal_state": terminal_state,
                    "reason": reason
                }
            )
```

## Budget Events

### Event Types

```python
class BudgetEventType(str, Enum):
    CHECK_PASSED = "CHECK_PASSED"
    WARNING_ISSUED = "WARNING_ISSUED"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    USAGE_RECORDED = "USAGE_RECORDED"
    STEP_RECORDED = "STEP_RECORDED"


class BudgetEvent(BaseModel):
    """
    Event emitted during budget tracking.
    """

    event_id: UUID = Field(default_factory=uuid4)
    workflow_run_id: UUID
    event_type: BudgetEventType
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Details
    dimension: Optional[str] = None
    current_value: Optional[float] = None
    limit: Optional[float] = None
    percentage: Optional[float] = None

    # Additional context
    details: Dict[str, Any] = {}


class BudgetWarningEvent(BudgetEvent):
    """Warning event when approaching limits."""

    event_type: BudgetEventType = BudgetEventType.WARNING_ISSUED
    threshold: float = 0.0
```

## Configuration

### Environment-Based Configuration

```python
from pydantic_settings import BaseSettings


class BudgetSettings(BaseSettings):
    """
    Budget configuration from environment.
    """

    # Token limits
    budget_max_input_tokens: int = 100_000
    budget_max_output_tokens: int = 20_000
    budget_max_total_tokens: int = 150_000

    # Cost limit
    budget_max_cost_usd: Decimal = Decimal("5.00")

    # Execution limits
    budget_max_steps: int = 50
    budget_max_time_seconds: int = 1800
    budget_max_tool_calls: int = 100
    budget_max_rework_cycles: int = 3

    # Warning threshold
    budget_warning_threshold: float = 0.8

    # Node timeouts
    budget_extractor_timeout_seconds: int = 300
    budget_investigator_timeout_seconds: int = 600
    budget_reviewer_timeout_seconds: int = 300

    class Config:
        env_prefix = "CASEFILE_"
```

### Dynamic Budget Adjustment

```python
class DynamicBudgetManager:
    """
    Allows runtime budget adjustment for specific scenarios.
    """

    def __init__(
        self,
        base_limits: BudgetLimits,
        adjustments: Optional[Dict[str, BudgetLimits]] = None
    ):
        self.base_limits = base_limits
        self.adjustments = adjustments or {}

    def get_limits_for_claim(
        self,
        claim: ClaimInput
    ) -> BudgetLimits:
        """
        Get budget limits for a specific claim.

        Can adjust based on claim complexity, priority, etc.
        """

        # Priority-based adjustment
        if claim.priority == ClaimPriority.URGENT:
            return self._adjust_limits(multiplier=2.0)
        elif claim.priority == ClaimPriority.HIGH:
            return self._adjust_limits(multiplier=1.5)

        # Complexity-based adjustment (future)
        complexity = self._estimate_complexity(claim)
        if complexity > 0.8:
            return self._adjust_limits(multiplier=1.25)

        return self.base_limits

    def _adjust_limits(self, multiplier: float) -> BudgetLimits:
        """Create adjusted limits."""

        return BudgetLimits(
            max_input_tokens=int(self.base_limits.max_input_tokens * multiplier),
            max_output_tokens=int(self.base_limits.max_output_tokens * multiplier),
            max_total_tokens=int(self.base_limits.max_total_tokens * multiplier),
            max_cost_usd=self.base_limits.max_cost_usd * Decimal(str(multiplier)),
            max_steps=int(self.base_limits.max_steps * multiplier),
            max_execution_time_seconds=int(self.base_limits.max_execution_time_seconds * multiplier),
            max_tool_calls=int(self.base_limits.max_tool_calls * multiplier),
            max_rework_cycles=self.base_limits.max_rework_cycles,
            node_timeouts={
                k: int(v * multiplier)
                for k, v in self.base_limits.node_timeouts.items()
            }
        )

    def _estimate_complexity(self, claim: ClaimInput) -> float:
        """Estimate claim complexity (0.0 to 1.0)."""

        # Simple heuristic based on document count
        doc_count = len(claim.documents)
        if doc_count > 10:
            return 0.9
        elif doc_count > 5:
            return 0.7
        elif doc_count > 3:
            return 0.5
        else:
            return 0.3
```

## Summary

The budget control system provides:
- **Code-level enforcement**: Limits checked and enforced in Python, not prompts
- **Pre-call checking**: Budgets verified before resource consumption
- **Multi-dimensional tracking**: Tokens, cost, steps, time, tool calls
- **Immediate termination**: Workflows stop when limits are exceeded
- **Guaranteed termination**: Mathematical proof that all workflows terminate
- **Configurable limits**: Environment-based configuration
- **Dynamic adjustment**: Priority and complexity-based budget scaling

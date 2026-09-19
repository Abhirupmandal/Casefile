# CASEFILE Termination Architecture

## Overview

Guaranteed termination is a core guarantee of CASEFILE. Every workflow execution MUST reach a terminal state within defined bounds, regardless of:
- LLM behavior
- Tool failures
- External system issues
- Programming errors

This document defines the termination model, guarantees, and recovery mechanisms.

## Design Principles

### 1. Fail-Safe Termination

When in doubt, terminate safely. It's better to fail a claim than to have a runaway workflow.

### 2. Multiple Termination Triggers

Termination can be triggered by:
- Normal completion (approval/rejection)
- Budget exhaustion
- Timeout
- Maximum steps exceeded
- Maximum rework exceeded
- Unrecoverable error
- Human intervention

### 3. Terminal State Finality

Once a workflow enters a terminal state, it cannot transition to any other state. Terminal states are truly terminal.

### 4. Complete Audit Trail

Every termination is fully documented with:
- Terminal state
- Reason
- All execution history up to termination point
- Final checkpoint

## Terminal States

### State Categories

```python
from enum import Enum


class WorkflowState(str, Enum):
    """
    All workflow states, categorized.
    """

    # === ENTRY STATE ===
    RECEIVED = "RECEIVED"

    # === PROCESSING STATES ===
    EXTRACTION = "EXTRACTION"
    INVESTIGATION = "INVESTIGATION"
    REVIEW = "REVIEW"

    # === CONTROL STATES ===
    REWORK_LOOP = "REWORK_LOOP"

    # === WAIT STATES ===
    HUMAN_APPROVAL = "HUMAN_APPROVAL"

    # === TERMINAL STATES - SUCCESS ===
    APPROVED = "APPROVED"

    # === TERMINAL STATES - FAILURE ===
    REJECTED = "REJECTED"
    FAILED = "FAILED"

    # === TERMINAL STATES - EXCEPTION ===
    ESCALATION = "ESCALATION"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    TIMEOUT = "TIMEOUT"
    MAX_STEPS_EXCEEDED = "MAX_STEPS_EXCEEDED"
    MAX_REWORK_EXCEEDED = "MAX_REWORK_EXCEEDED"


# Terminal states set for quick lookup
TERMINAL_STATES = {
    WorkflowState.APPROVED,
    WorkflowState.REJECTED,
    WorkflowState.FAILED,
    WorkflowState.ESCALATION,
    WorkflowState.BUDGET_EXHAUSTED,
    WorkflowState.TIMEOUT,
    WorkflowState.MAX_STEPS_EXCEEDED,
    WorkflowState.MAX_REWORK_EXCEEDED,
}

# Success terminal states
SUCCESS_TERMINAL_STATES = {
    WorkflowState.APPROVED,
}

# Failure terminal states
FAILURE_TERMINAL_STATES = {
    WorkflowState.REJECTED,
    WorkflowState.FAILED,
}

# Exception terminal states
EXCEPTION_TERMINAL_STATES = {
    WorkflowState.ESCALATION,
    WorkflowState.BUDGET_EXHAUSTED,
    WorkflowState.TIMEOUT,
    WorkflowState.MAX_STEPS_EXCEEDED,
    WorkflowState.MAX_REWORK_EXCEEDED,
}


def is_terminal(state: WorkflowState) -> bool:
    """Check if a state is terminal."""
    return state in TERMINAL_STATES


def is_success(state: WorkflowState) -> bool:
    """Check if a terminal state indicates success."""
    return state in SUCCESS_TERMINAL_STATES


def is_failure(state: WorkflowState) -> bool:
    """Check if a terminal state indicates failure."""
    return state in FAILURE_TERMINAL_STATES


def is_exception(state: WorkflowState) -> bool:
    """Check if a terminal state indicates exception."""
    return state in EXCEPTION_TERMINAL_STATES
```

### Terminal State Descriptions

| State | Category | Description | Typical Cause |
|-------|----------|-------------|---------------|
| APPROVED | Success | Claim approved, pending payout | Human approval received |
| REJECTED | Failure | Claim denied | Policy mismatch, fraud, human rejection |
| FAILED | Failure | Processing failed | Agent error, tool error, validation error |
| ESCALATION | Exception | Requires human escalation | Timeout, unexpected condition |
| BUDGET_EXHAUSTED | Exception | Resource limits exceeded | Token/cost/step limit reached |
| TIMEOUT | Exception | Time limit exceeded | Workflow timeout, node timeout |
| MAX_STEPS_EXCEEDED | Exception | Step limit exceeded | Potential infinite loop detected |
| MAX_REWORK_EXCEEDED | Exception | Rework limit exceeded | Reviewer repeatedly requests rework |

## Termination Triggers

### Trigger Taxonomy

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        TERMINATION TRIGGERS                                  │
│                                                                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐            │
│  │     NORMAL      │  │     BUDGET      │  │      ERROR      │            │
│  │   COMPLETION    │  │   EXHAUSTION    │  │     CONDITIONS  │            │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘            │
│           │                    │                    │                      │
│           ▼                    ▼                    ▼                      │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐            │
│  │ • Approved      │  │ • Tokens used   │  │ • Agent error   │            │
│  │ • Rejected      │  │ • Cost exceeded │  │ • Tool error    │            │
│  │ • No coverage   │  │ • Steps reached │  │ • Validation    │            │
│  │                 │  │ • Time exceeded │  │   failure       │            │
│  │                 │  │ • Tools called   │  │ • Schema error  │            │
│  │                 │  │   too many      │  │ • LLM error     │            │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘            │
│                                                                              │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐            │
│  │     REWORK      │  │     TIMEOUT     │  │     HUMAN       │            │
│  │     LIMIT       │  │                 │  │   INTERVENTION  │            │
│  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘            │
│           │                    │                    │                      │
│           ▼                    ▼                    ▼                      │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐            │
│  │ • Max rework    │  │ • Node timeout  │  │ • Manual stop   │            │
│  │   cycles hit    │  │ • Workflow      │  │ • Admin kill    │            │
│  │ • Reviewer      │  │   timeout       │  │ • Escalation    │            │
│  │   keeps         │  │ • Human         │  │   request       │            │
│  │   requesting    │  │   approval      │  │                 │            │
│  │                 │  │   timeout       │  │                 │            │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘            │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Trigger Detection

```python
from typing import Optional, Tuple
from datetime import datetime


class TerminationTriggerDetector:
    """
    Detects when termination should occur.
    """

    def __init__(
        self,
        budget_tracker: BudgetTracker,
        time_limits: TimeLimits
    ):
        self.budget = budget_tracker
        self.time_limits = time_limits

    async def check_termination_triggers(
        self,
        context: ExecutionContext
    ) -> Optional[Tuple[WorkflowState, str]]:
        """
        Check all termination triggers.

        Returns:
            (terminal_state, reason) if termination should occur, None otherwise
        """

        # 1. Check budget exhaustion
        if context.budget_state.is_exhausted:
            reason = context.budget_state.exhaustion_reason
            return self._map_budget_reason_to_state(reason)

        # 2. Check time limits
        time_check = self._check_time_limits(context)
        if time_check:
            return time_check

        # 3. Check step limit
        if context.step_count >= context.budget_state.limits.max_steps:
            return (WorkflowState.MAX_STEPS_EXCEEDED, "Maximum steps reached")

        # 4. Check rework limit
        if context.rework_count >= context.budget_state.limits.max_rework_cycles:
            return (WorkflowState.MAX_REWORK_EXCEEDED, "Maximum rework cycles reached")

        # 5. Check for stuck state (same state for too many steps)
        stuck_check = self._check_stuck_state(context)
        if stuck_check:
            return stuck_check

        return None

    def _map_budget_reason_to_state(
        self,
        reason: str
    ) -> Tuple[WorkflowState, str]:
        """Map budget exhaustion reason to terminal state."""

        mapping = {
            "MAX_TOKENS_EXCEEDED": (WorkflowState.BUDGET_EXHAUSTED, "Token limit exceeded"),
            "MAX_COST_EXCEEDED": (WorkflowState.BUDGET_EXHAUSTED, "Cost limit exceeded"),
            "MAX_STEPS_EXCEEDED": (WorkflowState.MAX_STEPS_EXCEEDED, "Step limit exceeded"),
            "MAX_TIME_EXCEEDED": (WorkflowState.TIMEOUT, "Time limit exceeded"),
            "MAX_TOOL_CALLS_EXCEEDED": (WorkflowState.BUDGET_EXHAUSTED, "Tool call limit exceeded"),
        }

        return mapping.get(reason, (WorkflowState.BUDGET_EXHAUSTED, reason))

    def _check_time_limits(
        self,
        context: ExecutionContext
    ) -> Optional[Tuple[WorkflowState, str]]:
        """Check all time-related limits."""

        elapsed = (datetime.utcnow() - context.started_at).total_seconds()

        # Workflow time limit
        if elapsed >= context.budget_state.limits.max_execution_time_seconds:
            return (WorkflowState.TIMEOUT, "Workflow time limit exceeded")

        # Node-specific timeout (checked at node level)
        node = context.current_state.value.lower()
        if node in context.budget_state.limits.node_timeouts:
            node_elapsed = (datetime.utcnow() - context.node_started_at).total_seconds()
            node_limit = context.budget_state.limits.node_timeouts[node]
            if node_elapsed >= node_limit:
                return (WorkflowState.TIMEOUT, f"Node {node} timeout exceeded")

        return None

    def _check_stuck_state(
        self,
        context: ExecutionContext
    ) -> Optional[Tuple[WorkflowState, str]]:
        """Check if workflow is stuck in same state."""

        # If we've been in the same state for > 5 consecutive steps
        if len(context.state_history) >= 5:
            recent = context.state_history[-5:]
            if all(s == context.current_state for s in recent):
                return (
                    WorkflowState.ESCALATION,
                    f"Workflow stuck in state {context.current_state} for 5+ steps"
                )

        return None
```

## Termination Handling

### Termination Process

```python
class TerminationHandler:
    """
    Handles workflow termination.
    """

    def __init__(
        self,
        workflow_repository: WorkflowRunRepository,
        checkpoint_manager: CheckpointManager,
        audit_repository: AuditEventRepository,
        notification_service: NotificationService
    ):
        self.workflows = workflow_repository
        self.checkpoints = checkpoint_manager
        self.audit = audit_repository
        self.notifications = notification_service

    async def terminate(
        self,
        workflow_run_id: UUID,
        terminal_state: WorkflowState,
        reason: str,
        context: ExecutionContext,
        error_details: Optional[ErrorDetails] = None
    ) -> TerminalStateRecord:
        """
        Terminate a workflow execution.

        This is the ONLY way a workflow enters a terminal state.
        """

        # 1. Validate this is a terminal state
        if not is_terminal(terminal_state):
            raise InvalidTerminalStateError(
                f"State {terminal_state} is not a terminal state"
            )

        # 2. Check if already terminated
        workflow = await self.workflows.get_by_id(workflow_run_id)
        if is_terminal(workflow.current_state):
            raise AlreadyTerminatedError(
                f"Workflow {workflow_run_id} already terminated as {workflow.current_state}"
            )

        # 3. Create terminal record
        record = TerminalStateRecord(
            workflow_run_id=workflow_run_id,
            claim_id=context.claim_id,
            terminal_state=terminal_state,
            reason=reason,
            error_details=error_details,
            step_count=context.step_count,
            budget_state=context.budget_state,
            timestamp=datetime.utcnow()
        )

        # 4. Update workflow run
        await self.workflows.update(
            workflow_run_id,
            {
                "current_state": terminal_state,
                "terminal_state": terminal_state,
                "termination_reason": reason,
                "completed_at": datetime.utcnow()
            }
        )

        # 5. Create final checkpoint
        await self.checkpoints.create_checkpoint(
            workflow_run_id=workflow_run_id,
            state=terminal_state,
            state_data={
                **context.state_data,
                "terminal_state": terminal_state,
                "termination_reason": reason
            },
            checkpoint_type=CheckpointType.AFTER_TRANSITION,
            budget_state=context.budget_state
        )

        # 6. Record audit event
        await self.audit.append(AuditEvent(
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
                "rework_count": context.rework_count,
                "total_tokens": context.budget_state.total_tokens_used,
                "total_cost": str(context.budget_state.estimated_cost_usd),
                "elapsed_time_seconds": int(
                    (datetime.utcnow() - context.started_at).total_seconds()
                ),
                "error_details": error_details.model_dump() if error_details else None
            },
            result="SUCCESS" if is_success(terminal_state) else "FAILURE",
            to_state=terminal_state
        ))

        # 7. Emit observability event
        observability.emit_workflow_termination(
            workflow_run_id=workflow_run_id,
            terminal_state=terminal_state,
            reason=reason
        )

        # 8. Send notification
        await self._send_notification(record, context)

        return record

    async def _send_notification(
        self,
        record: TerminalStateRecord,
        context: ExecutionContext
    ) -> None:
        """Send termination notification."""

        notification_type = self._determine_notification_type(record.terminal_state)

        if notification_type:
            await self.notifications.send(
                type=notification_type,
                workflow_run_id=record.workflow_run_id,
                claim_id=record.claim_id,
                data={
                    "terminal_state": record.terminal_state,
                    "reason": record.reason,
                    "recommendation": context.state_data.get("recommendation"),
                    "requires_action": record.terminal_state == WorkflowState.ESCALATION
                }
            )

    def _determine_notification_type(
        self,
        terminal_state: WorkflowState
    ) -> Optional[str]:
        """Determine notification type based on terminal state."""

        if terminal_state == WorkflowState.APPROVED:
            return "CLAIM_APPROVED"
        elif terminal_state == WorkflowState.REJECTED:
            return "CLAIM_REJECTED"
        elif terminal_state == WorkflowState.ESCALATION:
            return "CLAIM_ESCALATION"
        elif terminal_state in EXCEPTION_TERMINAL_STATES:
            return "CLAIM_EXCEPTION"

        return None
```

### Terminal State Record

```python
class TerminalStateRecord(BaseModel):
    """
    Complete record of workflow termination.
    """

    workflow_run_id: UUID
    claim_id: UUID
    terminal_state: WorkflowState
    reason: str
    error_details: Optional[ErrorDetails]
    step_count: int
    budget_state: BudgetState
    timestamp: datetime

    # Results (if applicable)
    extraction_result: Optional[ExtractionResult] = None
    investigation_result: Optional[InvestigationResult] = None
    review_result: Optional[ReviewResult] = None
    recommendation: Optional[ClaimRecommendation] = None
    human_approval: Optional[HumanApproval] = None

    def get_summary(self) -> str:
        """Get human-readable summary."""

        if self.terminal_state == WorkflowState.APPROVED:
            return f"Claim approved: {self.reason}"
        elif self.terminal_state == WorkflowState.REJECTED:
            return f"Claim rejected: {self.reason}"
        elif self.terminal_state == WorkflowState.FAILED:
            return f"Processing failed: {self.reason}"
        elif self.terminal_state == WorkflowState.ESCALATION:
            return f"Escalation required: {self.reason}"
        elif self.terminal_state == WorkflowState.BUDGET_EXHAUSTED:
            return f"Budget exhausted: {self.reason}"
        elif self.terminal_state == WorkflowState.TIMEOUT:
            return f"Timeout: {self.reason}"
        elif self.terminal_state == WorkflowState.MAX_STEPS_EXCEEDED:
            return f"Max steps exceeded after {self.step_count} steps"
        elif self.terminal_state == WorkflowState.MAX_REWORK_EXCEEDED:
            return f"Max rework cycles exceeded: {self.reason}"
        else:
            return f"Terminated: {self.terminal_state} - {self.reason}"
```

## Termination Guarantees

### Formal Guarantee Statement

```
CASEFILE Termination Guarantee
==============================

For any workflow execution W:

1. W will reach a terminal state T ∈ TERMINAL_STATES within:
   - MAX_STEPS steps
   - MAX_TIME seconds
   - MAX_TOKENS tokens
   - MAX_COST USD
   - MAX_REWORK_CYCLES rework cycles

2. Once in terminal state T, W will never transition to another state.

3. The termination reason R will be recorded and auditable.

4. A final checkpoint C will exist documenting the complete state at termination.

5. The terminal state T can be determined from the workflow history without ambiguity.
```

### Guarantee Enforcement

```python
class TerminationGuarantee:
    """
    Enforces termination guarantees.
    """

    @staticmethod
    def verify_workflow_can_terminate(
        workflow_definition: WorkflowDefinition
    ) -> TerminationVerification:
        """
        Verify that a workflow definition guarantees termination.

        This is called at workflow definition load time.
        """

        errors = []
        warnings = []

        # 1. Check all states have paths to terminal states
        states = workflow_definition.states
        terminal_states = {s for s in states if is_terminal(s)}
        non_terminal = {s for s in states if not is_terminal(s)}

        for state in non_terminal:
            if not TerminationGuarantee._has_path_to_terminal(state, states, workflow_definition.transitions):
                errors.append(f"State {state} has no path to terminal state")

        # 2. Check for cycles without exit
        cycles = TerminationGuarantee._find_cycles(workflow_definition)
        for cycle in cycles:
            if not TerminationGuarantee._cycle_has_exit(cycle, workflow_definition):
                errors.append(f"Cycle detected with no exit: {' -> '.join(cycle)}")

        # 3. Check all counters are bounded
        for state in non_terminal:
            transitions = workflow_definition.get_transitions_from(state)
            for t in transitions:
                if t.has_counter and not t.counter_is_bounded:
                    errors.append(f"Unbounded counter in transition from {state}")

        return TerminationVerification(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings
        )

    @staticmethod
    def _has_path_to_terminal(
        state: WorkflowState,
        all_states: set,
        transitions: List[Transition]
    ) -> bool:
        """Check if a state has a path to any terminal state."""

        visited = set()
        queue = [state]

        while queue:
            current = queue.pop(0)

            if current in visited:
                continue
            visited.add(current)

            if is_terminal(current):
                return True

            # Add reachable states
            for t in transitions:
                if t.source == current:
                    queue.append(t.destination)

        return False

    @staticmethod
    def _find_cycles(workflow_definition: WorkflowDefinition) -> List[List[WorkflowState]]:
        """Find all cycles in the workflow graph."""

        # Implementation uses depth-first search
        # Returns list of cycles found
        ...

    @staticmethod
    def _cycle_has_exit(cycle: List[WorkflowState], workflow_definition: WorkflowDefinition) -> bool:
        """Check if a cycle has an exit condition."""

        for state in cycle:
            transitions = workflow_definition.get_transitions_from(state)
            for t in transitions:
                # Exit if transition leads outside cycle
                if t.destination not in cycle:
                    return True
                # Exit if transition has bounded counter
                if t.has_counter and t.counter_is_bounded:
                    return True

        return False
```

## Recovery from Exceptional Termination

### Recovery Strategies

```python
class TerminationRecoveryStrategy(str, Enum):
    """
    Strategy for recovering from exceptional termination.
    """

    # Cannot recover
    NONE = "NONE"

    # Resume from last checkpoint
    RESUME = "RESUME"

    # Retry with increased limits
    RETRY_INCREASED_LIMITS = "RETRY_INCREASED_LIMITS"

    # Escalate to human
    ESCALATE = "ESCALATE"

    # Restart from beginning
    RESTART = "RESTART"


class TerminationRecoveryAnalyzer:
    """
    Analyzes termination and recommends recovery strategy.
    """

    def analyze(
        self,
        record: TerminalStateRecord
    ) -> RecoveryRecommendation:
        """
        Analyze termination and recommend recovery approach.
        """

        if record.terminal_state == WorkflowState.APPROVED:
            return RecoveryRecommendation(
                strategy=TerminationRecoveryStrategy.NONE,
                reason="Workflow completed successfully",
                action_required=False
            )

        elif record.terminal_state == WorkflowState.REJECTED:
            return RecoveryRecommendation(
                strategy=TerminationRecoveryStrategy.NONE,
                reason="Workflow rejected - no automatic recovery",
                action_required=False
            )

        elif record.terminal_state == WorkflowState.BUDGET_EXHAUSTED:
            if record.budget_state.total_tokens_used >= record.budget_state.limits.max_total_tokens * 0.9:
                # Legitimate high-complexity claim
                return RecoveryRecommendation(
                    strategy=TerminationRecoveryStrategy.RETRY_INCREASED_LIMITS,
                    reason="High-complexity claim exhausted budget legitimately",
                    action_required=True,
                    recommended_limits=self._increase_limits(record.budget_state.limits, 1.5)
                )
            else:
                # Potential issue
                return RecoveryRecommendation(
                    strategy=TerminationRecoveryStrategy.ESCALATE,
                    reason="Budget exhausted unexpectedly - investigate potential issue",
                    action_required=True
                )

        elif record.terminal_state == WorkflowState.TIMEOUT:
            return RecoveryRecommendation(
                strategy=TerminationRecoveryStrategy.RESUME,
                reason="Timeout likely due to external factors - resume from checkpoint",
                action_required=True
            )

        elif record.terminal_state == WorkflowState.MAX_STEPS_EXCEEDED:
            return RecoveryRecommendation(
                strategy=TerminationRecoveryStrategy.ESCALATE,
                reason="Potential infinite loop - manual investigation required",
                action_required=True
            )

        elif record.terminal_state == WorkflowState.MAX_REWORK_EXCEEDED:
            return RecoveryRecommendation(
                strategy=TerminationRecoveryStrategy.ESCALATE,
                reason="Reviewer repeatedly requesting rework - human decision needed",
                action_required=True
            )

        else:
            return RecoveryRecommendation(
                strategy=TerminationRecoveryStrategy.ESCALATE,
                reason=f"Unknown termination state: {record.terminal_state}",
                action_required=True
            )

    def _increase_limits(
        self,
        limits: BudgetLimits,
        multiplier: float
    ) -> BudgetLimits:
        """Create increased limits."""

        return BudgetLimits(
            max_input_tokens=int(limits.max_input_tokens * multiplier),
            max_output_tokens=int(limits.max_output_tokens * multiplier),
            max_total_tokens=int(limits.max_total_tokens * multiplier),
            max_cost_usd=limits.max_cost_usd * Decimal(str(multiplier)),
            max_steps=int(limits.max_steps * multiplier),
            max_execution_time_seconds=int(limits.max_execution_time_seconds * multiplier),
            max_tool_calls=int(limits.max_tool_calls * multiplier),
            max_rework_cycles=limits.max_rework_cycles
        )


@dataclass
class RecoveryRecommendation:
    """
    Recommendation for recovery from exceptional termination.
    """

    strategy: TerminationRecoveryStrategy
    reason: str
    action_required: bool
    recommended_limits: Optional[BudgetLimits] = None
```

## Monitoring and Alerting

### Termination Metrics

```python
class TerminationMetrics:
    """
    Metrics for termination monitoring.
    """

    def __init__(self, metrics_client: MetricsClient):
        self.metrics = metrics_client

    def record_termination(
        self,
        record: TerminalStateRecord
    ) -> None:
        """Record termination metrics."""

        # Count by terminal state
        self.metrics.increment(
            "workflow.termination.count",
            tags={
                "terminal_state": record.terminal_state,
                "category": self._categorize(record.terminal_state)
            }
        )

        # Record step count
        self.metrics.histogram(
            "workflow.termination.steps",
            record.step_count,
            tags={"terminal_state": record.terminal_state}
        )

        # Record token usage
        self.metrics.histogram(
            "workflow.termination.tokens",
            record.budget_state.total_tokens_used,
            tags={"terminal_state": record.terminal_state}
        )

        # Record cost
        self.metrics.histogram(
            "workflow.termination.cost_usd",
            float(record.budget_state.estimated_cost_usd),
            tags={"terminal_state": record.terminal_state}
        )

        # Record duration
        duration = (record.timestamp - record.budget_state.started_at).total_seconds()
        self.metrics.histogram(
            "workflow.termination.duration_seconds",
            duration,
            tags={"terminal_state": record.terminal_state}
        )

    def _categorize(self, state: WorkflowState) -> str:
        """Categorize terminal state."""

        if is_success(state):
            return "success"
        elif is_failure(state):
            return "failure"
        elif is_exception(state):
            return "exception"
        return "unknown"
```

### Alerting Rules

```python
TERMINATION_ALERTING_RULES = [
    {
        "name": "high_exception_rate",
        "condition": "rate(workflow.termination.count{category:exception}) > 0.1",
        "message": "Exception termination rate exceeds 10%",
        "severity": "warning"
    },
    {
        "name": "budget_exhaustion_spike",
        "condition": "rate(workflow.termination.count{terminal_state:BUDGET_EXHAUSTED}) > 0.05",
        "message": "Budget exhaustion rate exceeds 5%",
        "severity": "warning"
    },
    {
        "name": "timeout_spike",
        "condition": "rate(workflow.termination.count{terminal_state:TIMEOUT}) > 0.05",
        "message": "Timeout rate exceeds 5%",
        "severity": "warning"
    },
    {
        "name": "max_steps_exceeded",
        "condition": "rate(workflow.termination.count{terminal_state:MAX_STEPS_EXCEEDED}) > 0",
        "message": "Workflow hit max steps limit - potential infinite loop",
        "severity": "critical"
    },
]
```

## Summary

The termination architecture provides:
- **Guaranteed termination**: Every workflow reaches a terminal state
- **Multiple triggers**: Budget, time, steps, errors, human intervention
- **Formal verification**: Workflow definitions are verified to guarantee termination
- **Complete audit trail**: Every termination is fully documented
- **Recovery strategies**: Defined approaches for exceptional terminations
- **Monitoring and alerting**: Metrics and alerts for termination patterns

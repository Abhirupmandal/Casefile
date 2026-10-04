# ADR-007: Multi-Dimensional Budget Enforcement

> **Amendment (2026-09-22)**: Per [ADR-011](ADR-011-sqlite-local-persistence.md),
> durable budget state lives in SQLite; Redis remains a read-through cache only.

**Status**: Accepted (amended by ADR-011)
**Date**: 2026-09-19
**Deciders**: Architecture Team
**Related Documents**: `budget-control.md`, `termination.md`, `agent-architecture.md`

## Context

CASEFILE requires strict resource control to prevent:
- **Runaway costs**: LLM API costs can escalate quickly without limits
- **Infinite loops**: Rework cycles could continue indefinitely
- **Slow workflows**: Long-running workflows tie up resources
- **Resource exhaustion**: Unbounded token usage could overwhelm context windows

Requirements:
- **Multiple budget dimensions**: Tokens, USD cost, steps, time, rework cycles
- **Pre-flight enforcement**: Check budget BEFORE agent invocation, not after
- **Guaranteed termination**: Every workflow must reach terminal state in bounded time
- **Graceful degradation**: Budget exhaustion transitions to terminal state with reason
- **Observability**: Track budget consumption in real-time

## Decision

We will implement **pre-flight budget checks with multi-dimensional limits**.

### Budget Dimensions

```python
class BudgetLimits(BaseModel):
    """Hard limits for workflow execution."""
    max_input_tokens: int = 100_000
    max_output_tokens: int = 20_000
    max_total_tokens: int = 150_000  # Safety: input + output + overhead
    max_cost_usd: Decimal = Decimal("5.00")
    max_steps: int = 50  # Total agent invocations
    max_execution_time_seconds: int = 1_800  # 30 minutes
    max_rework_cycles: int = 3

class BudgetState(BaseModel):
    """Current budget consumption."""
    workflow_id: UUID
    input_tokens: int = 0
    output_tokens: int = 0
    total_cost_usd: Decimal = Decimal("0.00")
    step_count: int = 0
    execution_time_seconds: int = 0
    rework_count: int = 0
    updated_at: datetime
```

### Enforcement Strategy

#### 1. Pre-Flight Check (Before Every Agent Invocation)
```python
async def pre_flight_budget_check(workflow_id: UUID, estimated_tokens: int) -> BudgetCheckResult:
    """
    Check if agent invocation would exceed budget.
    Called BEFORE every LLM invocation.
    """
    budget_state = await load_budget_state(workflow_id)
    limits = BudgetLimits()

    # Check each dimension
    if budget_state.total_tokens + estimated_tokens > limits.max_total_tokens:
        return BudgetCheckResult(allowed=False, reason="token_limit_exceeded")

    estimated_cost = estimate_cost(estimated_tokens, model)
    if budget_state.total_cost_usd + estimated_cost > limits.max_cost_usd:
        return BudgetCheckResult(allowed=False, reason="cost_limit_exceeded")

    if budget_state.step_count >= limits.max_steps:
        return BudgetCheckResult(allowed=False, reason="max_steps_exceeded")

    if budget_state.execution_time_seconds >= limits.max_execution_time_seconds:
        return BudgetCheckResult(allowed=False, reason="timeout")

    if budget_state.rework_count >= limits.max_rework_cycles:
        return BudgetCheckResult(allowed=False, reason="max_rework_exceeded")

    return BudgetCheckResult(allowed=True)
```

#### 2. Post-Flight Update (After Agent Completion)
```python
async def update_budget_state(workflow_id: UUID, invocation_metadata: InvocationMetadata):
    """Update budget state after agent invocation."""
    async with db.transaction():
        budget_state = await load_budget_state(workflow_id, for_update=True)

        budget_state.input_tokens += invocation_metadata.input_tokens
        budget_state.output_tokens += invocation_metadata.output_tokens
        budget_state.total_cost_usd += invocation_metadata.cost_usd
        budget_state.step_count += 1
        budget_state.execution_time_seconds = int((datetime.utcnow() - budget_state.started_at).total_seconds())

        await save_budget_state(budget_state)
```

#### 3. Terminal State Transition
```python
def handle_budget_exceeded(workflow_id: UUID, reason: str):
    """Transition to terminal state when budget exceeded."""
    terminal_state_map = {
        "token_limit_exceeded": WorkflowState.BUDGET_EXHAUSTED,
        "cost_limit_exceeded": WorkflowState.BUDGET_EXHAUSTED,
        "max_steps_exceeded": WorkflowState.MAX_STEPS_EXCEEDED,
        "timeout": WorkflowState.TIMEOUT,
        "max_rework_exceeded": WorkflowState.MAX_REWORK_EXCEEDED,
    }

    terminal_state = terminal_state_map[reason]
    transition_to_terminal_state(workflow_id, terminal_state, reason)
```

## Consequences

### Positive
- **Cost predictability**: Hard cap on LLM API costs prevents runaway expenses
- **Guaranteed termination**: All workflows terminate in ≤30 minutes, ≤50 steps
- **Resource protection**: Prevents single workflow from consuming excessive resources
- **Early detection**: Pre-flight checks reject agent calls that would exceed budget
- **Granular limits**: Multiple dimensions catch different failure modes
- **Observable**: Budget state tracked in database, visible in UI

### Negative
- **False positives**: Conservative estimates may reject valid workflows
- **Latency overhead**: Pre-flight check adds ~10-20ms per agent invocation
- **Complexity**: Multiple dimensions require careful tuning
- **Workflow truncation**: Legitimate workflows may hit limits on complex cases

### Mitigations
- **Token estimation buffer**: Add 10% safety margin to token estimates
- **Budget caching**: Cache budget state in Redis (30s TTL) to reduce PostgreSQL queries
- **Configurable limits**: Per-workflow budget overrides for special cases
- **Soft warnings**: Alert at 75% budget consumption, fail at 100%
- **Budget analysis**: Track typical budget consumption to tune limits

## Alternatives Considered

### 1. Post-hoc budget tracking (no enforcement)
- **Pros**: Simple, no pre-flight overhead
- **Rejected**: Doesn't prevent budget overruns, only reports them after the fact

### 2. Single-dimension limit (cost only)
- **Pros**: Simple to understand and implement
- **Rejected**: Doesn't catch runaway loops (low-cost models can still loop infinitely)

### 3. Rate limiting (requests per minute)
- **Pros**: Protects external APIs
- **Rejected**: Doesn't map to workflow-level budgets, doesn't prevent total cost overruns

### 4. Hard timeouts (kill process after 30 min)
- **Pros**: Guaranteed termination
- **Rejected**: Ungraceful shutdown, loses workflow state, no terminal state transition

## Budget Limit Rationale

### Token Limits
- **Input: 100K tokens**: Supports ~70 pages of claim documentation + context
- **Output: 20K tokens**: Sufficient for detailed investigation reports
- **Total: 150K tokens**: Safety margin for multiple agent invocations

### Cost Limit
- **$5.00**: Approximately:
  - 10 Claude Sonnet invocations (2K in, 1K out each) = $0.60
  - 5 GPT-4o invocations (5K in, 2K out each) = $0.95
  - Total: ~15-20 agent invocations with mixed models

### Step Limit
- **50 steps**: Typical workflow:
  - Extractor: 1 step
  - Investigator: 5-10 tool calls
  - Reviewer: 1 step
  - Rework loop (max 3): 3 × (Investigator + Reviewer) = 6 steps
  - Total: ~20 steps typical, 50 allows headroom

### Time Limit
- **1800 seconds (30 min)**: Prevents indefinite hangs
  - Most workflows complete in 2-5 minutes
  - Complex cases with external API latency: 10-15 minutes
  - 30 minutes provides 2× safety margin

### Rework Limit
- **3 cycles**: Prevents infinite rework loops
  - Most workflows: 0 rework
  - Acceptable rework: 1-2 cycles (missing evidence, clarifications)
  - 3 cycles is reasonable upper bound before escalation

## Monitoring and Alerting

```python
# Budget consumption metrics (OpenTelemetry)
budget_consumption_gauge = Gauge("casefile_budget_consumption_pct", ["dimension", "workflow_id"])

# Alert thresholds
BUDGET_WARNING_THRESHOLD = 0.75  # 75%
BUDGET_CRITICAL_THRESHOLD = 0.90  # 90%

async def check_budget_thresholds(workflow_id: UUID):
    budget_state = await load_budget_state(workflow_id)
    limits = BudgetLimits()

    for dimension, current, limit in [
        ("tokens", budget_state.total_tokens, limits.max_total_tokens),
        ("cost", budget_state.total_cost_usd, limits.max_cost_usd),
        ("steps", budget_state.step_count, limits.max_steps),
    ]:
        consumption_pct = current / limit
        budget_consumption_gauge.labels(dimension=dimension, workflow_id=workflow_id).set(consumption_pct)

        if consumption_pct >= BUDGET_CRITICAL_THRESHOLD:
            logger.warning(f"Budget critical: {dimension} at {consumption_pct:.0%} for {workflow_id}")
```

## References

- CASEFILE budget control: `docs/budget-control.md`
- CASEFILE termination guarantees: `docs/termination.md`
- OpenTelemetry metrics: https://opentelemetry.io/docs/specs/otel/metrics/

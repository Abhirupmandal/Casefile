# ADR-008: OpenTelemetry for Observability

**Status**: Accepted
**Date**: 2026-09-19
**Deciders**: Architecture Team
**Related Documents**: `observability.md`, `evaluation.md`, `budget-control.md`

## Implementation Status (Phase 10)

Implemented in `src/casefile/observability/` per this ADR: guarded
`OtelTracer` with null-span degradation, exact 16-metric
`CasefileMeters` with forbidden high-cardinality labels, sanitized
attribute builders, optional `obs` parameters across workflow/agents/
tools/checkpoints/budget/approval, hook→metric bridge, OTLP HTTP →
Jaeger export, and fully offline tests (`InMemorySpanExporter` + unit
netguard). Acceptance matrix A–AC and 7 end-to-end scenarios:
`tests/unit/test_observability_core.py`,
`tests/integration/test_observability_lifecycle.py`.

## Context

CASEFILE requires comprehensive observability to:
- **Debug failures**: Trace exact execution path through state machine
- **Monitor performance**: Measure agent latency, tool call duration, workflow end-to-end time
- **Track costs**: Attribute token usage and USD costs per agent, per workflow
- **Audit compliance**: Provide immutable audit trail of all actions
- **Optimize workflows**: Identify bottlenecks and unnecessary tool calls
- **Detect anomalies**: Alert on unusual patterns (high cost, long duration, frequent rework)

Requirements:
- **Distributed tracing**: Track workflow execution across agents and tools
- **Structured logging**: Machine-readable logs with workflow_id, agent, step_number
- **Metrics collection**: Token counts, costs, latencies, error rates
- **Correlation**: Link traces, logs, metrics via workflow_id and trace_id
- **Standard format**: Use industry-standard observability protocol
- **Vendor-agnostic**: Export to multiple backends (Datadog, Honeycomb, Jaeger, local)

## Decision

We will use **OpenTelemetry (OTel)** as the unified observability framework for traces, logs, and metrics.

### OpenTelemetry Integration

#### 1. Trace Structure

```python
from opentelemetry import trace
from opentelemetry.trace import Span

tracer = trace.get_tracer("casefile")

async def execute_workflow(claim_input: ClaimInput) -> WorkflowResult:
    """Execute workflow with full OTel instrumentation."""

    # Root span: entire workflow execution
    with tracer.start_as_current_span("workflow_run") as workflow_span:
        workflow_span.set_attribute("workflow_id", str(workflow_id))
        workflow_span.set_attribute("claim_id", claim_input.claim_id)
        workflow_span.set_attribute("workflow_version", WORKFLOW_VERSION)

        # Agent invocation spans (nested under workflow_run)
        with tracer.start_as_current_span("agent_invocation") as agent_span:
            agent_span.set_attribute("agent_name", "extractor")
            agent_span.set_attribute("model", "claude-3-5-sonnet-20241022")
            agent_span.set_attribute("input_tokens", 1250)
            agent_span.set_attribute("output_tokens", 450)
            agent_span.set_attribute("cost_usd", 0.011)
            agent_span.set_attribute("step_number", 1)

            # Tool call spans (nested under agent_invocation)
            with tracer.start_as_current_span("tool_call") as tool_span:
                tool_span.set_attribute("tool_name", "policy_lookup")
                tool_span.set_attribute("cache_hit", False)
                tool_span.set_attribute("latency_ms", 245)
```

**Trace hierarchy**:
```
workflow_run (root)
├── agent_invocation (Extractor)
│   └── (no tool calls for Extractor)
├── agent_invocation (Investigator)
│   ├── tool_call (policy_lookup)
│   ├── tool_call (claim_history_lookup)
│   ├── tool_call (repair_cost_lookup)
│   └── tool_call (fraud_signal_lookup)
├── agent_invocation (Reviewer)
│   └── tool_call (document_retrieval)
└── state_transition
    └── event: REVIEW → HUMAN_APPROVAL
```

#### 2. Span Attributes (Semantic Conventions)

```python
# Workflow-level attributes
workflow_span.set_attributes({
    "workflow.id": str(workflow_id),
    "workflow.state": "INVESTIGATION",
    "workflow.version": "1.2.0",
    "workflow.terminal_state": "APPROVED",
    "workflow.duration_seconds": 127.3,
})

# Agent-level attributes
agent_span.set_attributes({
    "agent.name": "investigator",
    "agent.provider": "openai",
    "agent.model": "gpt-4o",
    "agent.temperature": 0.3,
    "agent.input_tokens": 3450,
    "agent.output_tokens": 890,
    "agent.cost_usd": 0.0172,
    "agent.latency_ms": 2340,
})

# Tool-level attributes
tool_span.set_attributes({
    "tool.name": "policy_lookup",
    "tool.cache_hit": False,
    "tool.latency_ms": 245,
    "tool.rate_limited": False,
    "tool.error": None,
})

# Budget-level attributes
workflow_span.set_attributes({
    "budget.total_tokens": 45230,
    "budget.total_cost_usd": 0.342,
    "budget.step_count": 12,
    "budget.rework_count": 1,
})
```

#### 3. Structured Logging

```python
import structlog

logger = structlog.get_logger()

# All logs include workflow_id and trace_id for correlation
logger.info(
    "agent_invocation_started",
    workflow_id=workflow_id,
    trace_id=trace.get_current_span().get_span_context().trace_id,
    agent="investigator",
    step_number=5,
)

logger.error(
    "tool_call_failed",
    workflow_id=workflow_id,
    trace_id=trace.get_current_span().get_span_context().trace_id,
    tool_name="fraud_signal_lookup",
    error="ConnectionTimeout",
    retry_count=2,
)
```

#### 4. Metrics Collection

```python
from opentelemetry import metrics

meter = metrics.get_meter("casefile")

# Counters
agent_invocations = meter.create_counter(
    "casefile.agent.invocations",
    description="Total agent invocations",
    unit="1",
)

tool_calls = meter.create_counter(
    "casefile.tool.calls",
    description="Total tool calls",
    unit="1",
)

# Histograms
agent_latency = meter.create_histogram(
    "casefile.agent.latency",
    description="Agent invocation latency",
    unit="ms",
)

token_usage = meter.create_histogram(
    "casefile.agent.tokens",
    description="Token usage per agent invocation",
    unit="tokens",
)

cost_per_workflow = meter.create_histogram(
    "casefile.workflow.cost",
    description="Total cost per workflow",
    unit="usd",
)

# Gauges
active_workflows = meter.create_up_down_counter(
    "casefile.workflows.active",
    description="Number of active workflows",
    unit="1",
)
```

## Consequences

### Positive
- **Unified observability**: Single framework for traces, logs, metrics
- **Vendor-agnostic**: Export to any OTel-compatible backend
- **Standard format**: Industry-standard semantic conventions
- **Rich context**: Trace IDs correlate logs, metrics, traces
- **Cost attribution**: Token and USD costs tracked per span
- **Debugging**: Visualize exact execution path through state machine
- **Performance analysis**: Identify slow agents, expensive tool calls
- **Compliance**: Immutable audit trail in trace backend

### Negative
- **Performance overhead**: Span creation/export adds ~1-5ms per operation
- **Storage cost**: Trace data requires backend storage (Datadog, Honeycomb)
- **Complexity**: OTel configuration and semantic conventions have learning curve
- **Sampling decisions**: High-cardinality spans (per tool call) can be expensive

### Mitigations
- **Adaptive sampling**: Sample 100% of failed workflows, 10% of successful
- **Batch export**: Buffer spans and export in batches to reduce network calls
- **Async export**: Don't block workflow on span export
- **Tail-based sampling**: Keep all spans for workflows that fail or exceed budget
- **Local development**: Use Jaeger all-in-one for local trace visualization

## Alternatives Considered

### 1. Custom logging (no tracing)
- **Pros**: Simple, no dependencies, full control
- **Rejected**: No distributed context, hard to correlate logs, no standardized format

### 2. Application Performance Monitoring (APM) vendor SDK (Datadog, New Relic)
- **Pros**: Rich UI, automatic instrumentation, alerting
- **Rejected**: Vendor lock-in, expensive, less control over data export

### 3. Prometheus + Grafana (metrics only)
- **Pros**: Open source, mature ecosystem, good for metrics
- **Rejected**: No distributed tracing, weak log correlation, requires separate trace solution

### 4. AWS X-Ray
- **Pros**: Managed service, AWS integration
- **Rejected**: AWS-only, less flexible than OTel, vendor lock-in

### 5. Jaeger (tracing only)
- **Pros**: Open source, mature, good UI
- **Rejected**: Tracing only (no metrics/logs), OTel subsumes Jaeger's use case

## Trace Backend Options

### Development
- **Jaeger all-in-one**: Local Docker container for development
- **Console exporter**: Print spans to stdout for debugging

### Production
- **Datadog**: Full-featured APM, excellent UI, expensive
- **Honeycomb**: Best-in-class querying, great for high-cardinality data
- **Grafana Cloud**: Open source stack, good balance of features/cost
- **AWS X-Ray**: If already on AWS, low operational overhead

## Sampling Strategy

```python
from opentelemetry.sdk.trace.sampling import ParentBasedTraceIdRatioBased, TraceIdRatioBased

# Development: Sample 100% of traces
sampler = TraceIdRatioBased(1.0)

# Production: Head-based sampling (10% of all workflows)
sampler = ParentBasedTraceIdRatioBased(0.1)

# Production: Tail-based sampling (all failures, 10% of successes)
# Requires OTel Collector with tail sampling processor
# Config in otel-collector.yaml:
processors:
  tail_sampling:
    decision_wait: 10s
    policies:
      - name: sample_all_errors
        type: status_code
        status_code:
          status_codes: [ERROR]
      - name: sample_budget_exceeded
        type: string_attribute
        string_attribute:
          key: workflow.terminal_state
          values: [BUDGET_EXHAUSTED, TIMEOUT, MAX_STEPS_EXCEEDED]
      - name: sample_10pct_success
        type: probabilistic
        probabilistic:
          sampling_percentage: 10
```

## Audit Trail Requirements

Per `security.md`, audit log must capture:
- **Who**: agent_name, model_name
- **What**: action (agent_invocation, tool_call, state_transition)
- **When**: timestamp (span start/end)
- **Outcome**: success/failure, error details

This is automatically satisfied by OTel spans with proper attributes.

## Cost Attribution

```python
# Query traces to calculate cost per agent, per workflow
SELECT
    span_attributes['agent.name'] AS agent,
    SUM(span_attributes['agent.cost_usd']) AS total_cost
FROM traces
WHERE span_name = 'agent_invocation'
    AND trace_attributes['workflow.terminal_state'] = 'APPROVED'
    AND timestamp >= NOW() - INTERVAL '7 days'
GROUP BY agent
ORDER BY total_cost DESC;
```

## References

- OpenTelemetry documentation: https://opentelemetry.io/docs/
- OTel semantic conventions: https://opentelemetry.io/docs/specs/semconv/
- CASEFILE observability design: `docs/observability.md`
- Tail-based sampling: https://opentelemetry.io/docs/collector/processors/tailsamplingprocessor/

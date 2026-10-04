# CASEFILE Observability Architecture

## Implementation Status (Phase 5)

Phase 5 introduces production-oriented, OpenTelemetry-compatible observability across the multi-agent orchestration lifecycle:
- **Trace Context Propagation**: `TraceContext` carries `workflow_run_id`, `execution_id`, `correlation_id`, `claim_id`, `checkpoint_id`, and `approval_id`, alongside execution mode (`LIVE` vs `REPLAY`).
- **Standard Span Catalog**: Stable, non-user-influenced span names (`casefile.workflow`, `casefile.agent`, `casefile.tool`, `casefile.persistence`, `casefile.checkpoint`, `casefile.budget`, `casefile.approval`).
- **Structured Metrics Catalog**: 16 structured instruments tracking workflow completions/failures/durations, agent executions/failures, tool invocations/failures, retries, rework cycles, budget exhaustion, step exhaustion, checkpoints, replay mismatches, and approval lifecycle metrics.
- **Centralized Privacy Sanitization**: `Sanitizer` strips credentials, auth tokens, passwords, credit card numbers, SSNs, raw documents, and free-form statements; truncates long values at 256 characters.
- **Fail-Safe Operation**: Observability provider supports in-memory testing (`test_observability()`), OTLP export, and disabled/no-op mode. Backend unavailability or collector crashes never interrupt or fail workflow execution.

OpenTelemetry-backed telemetry implemented in `src/casefile/observability/`
per this document and ADR-008:

- **Package layout**: `context.py` (TraceContext + LIVE/REPLAY mode),
  `sanitize.py` (attribute deny-list, 256-char cap, `safe_error`),
  `tracer.py` (`OtelTracer` + `maybe_span` — SDK adapter with null-span
  degradation; setup failures never re-enter contextmanagers),
  `meters.py` (exact 16-metric set, forbidden high-cardinality label keys,
  in-memory store + optional OTel instruments), `spans.py` (10 span-name
  constants + typed attribute builders), `provider.py` (`Observability`,
  `configure_observability` with OTLP HTTP exporter, `test_observability`
  returning `InMemorySpanExporter`, `disabled()`), `bridge.py`
  (workflow hook EventSink → counters).
- **Instrumentation** (optional `obs` parameter; business logic unchanged
  when absent): workflow `apply` transitions, supervisor `route`, agent
  `run` + metric recording, tool registry `execute` + metrics, checkpoint
  create/get/load, resume, replay (`mode=REPLAY`), budget pre-flight /
  terminate / model-call accounting, approval request + decide.
- **Guarantees**: telemetry never sits inside business transactions;
  exporter/collector failure cannot fail a workflow; span attributes and
  metric labels exclude documents, credentials, and reasoning; metric
  labels are low-cardinality (`claim_id`/`execution_id`/`approval_id`
  keys raise); tests are fully offline (SDK in-memory exporter;
  `config/test.yaml` disables OTLP).
- **Stack**: OTLP HTTP → Jaeger (docker-compose, ports 4317/4318,
  UI 16686); Prometheus metrics endpoint reserved in config.

Proven by `tests/unit/test_observability_core.py` (full Phase 10
acceptance matrix A–AC: config, TraceContext, sanitize, tracer, meters,
hook bridge, span catalog, attribute builders, shutdown/exports) and
`tests/integration/test_observability_lifecycle.py` (7 end-to-end
scenarios: full live trace tree with parent-child links, rework
counters, budget termination, checkpoint resume identity, replay mode
span, broken-exporter green path, privacy sweep over all finished span
attributes).

## Overview

CASEFILE implements comprehensive observability using OpenTelemetry-compatible tracing, structured logging, and custom metrics. Every workflow run produces a complete, queryable trace that can be used for debugging, performance analysis, and compliance auditing.

## Design Principles

### 1. Trace Everything

Every action within CASEFILE produces trace spans. The complete execution path for any claim can be reconstructed from telemetry data.

### 2. Structured Data

All logs and metrics use structured formats (JSON) for machine parsing and querying.

### 3. Correlation IDs

Every workflow run has a trace ID that propagates through all spans, logs, and events for correlation.

### 4. Low Overhead

Observability adds minimal latency. Critical path spans are sampled at 100%, others at configurable rates.

### 5. Export Flexibility

OpenTelemetry allows exporting to any compatible backend (Jaeger, Prometheus, Datadog, etc.).

## Observability Stack

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        OBSERVABILITY STACK                                   │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                      APPLICATION LAYER                                │  │
│  │                                                                       │  │
│  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐            │  │
│  │  │   Workflow    │  │     Agent     │  │     Tool      │            │  │
│  │  │   Execution   │  │   Execution   │  │   Execution   │            │  │
│  │  └───────┬───────┘  └───────┬───────┘  └───────┬───────┘            │  │
│  │          │                  │                  │                     │  │
│  └──────────┼──────────────────┼──────────────────┼─────────────────────┘  │
│             │                  │                  │                         │
│             ▼                  ▼                  ▼                         │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                    OPENTELEMETRY LAYER                                │  │
│  │                                                                       │  │
│  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐            │  │
│  │  │    Tracing    │  │    Metrics    │  │    Logging    │            │  │
│  │  │   (Spans)     │  │  (Counters,   │  │  (Structured  │            │  │
│  │  │               │  │   Histograms) │  │    JSON)      │            │  │
│  │  └───────┬───────┘  └───────┬───────┘  └───────┬───────┘            │  │
│  │          │                  │                  │                     │  │
│  └──────────┼──────────────────┼──────────────────┼─────────────────────┘  │
│             │                  │                  │                         │
│             ▼                  ▼                  ▼                         │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                      EXPORT LAYER                                     │  │
│  │                                                                       │  │
│  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐            │  │
│  │  │ OTLP Exporter │  │ Prometheus    │  │  stdout/      │            │  │
│  │  │               │  │ Exporter      │  │  file         │            │  │
│  │  └───────┬───────┘  └───────┬───────┘  └───────┬───────┘            │  │
│  │          │                  │                  │                     │  │
│  └──────────┼──────────────────┼──────────────────┼─────────────────────┘  │
│             │                  │                  │                         │
│             ▼                  ▼                  ▼                         │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                     BACKEND LAYER                                     │  │
│  │                                                                       │  │
│  │  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐            │  │
│  │  │    Jaeger     │  │   Prometheus  │  │   ELK Stack   │            │  │
│  │  │   (traces)    │  │   (metrics)   │  │   (logs)      │            │  │
│  │  └───────────────┘  └───────────────┘  └───────────────┘            │  │
│  │                                                                       │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Trace Structure

### Workflow Trace Hierarchy

```
WorkflowRun (Trace)
│
├── Span: workflow.received
│   └── Attributes: claim_id, policy_number, document_count
│
├── Span: transition.RECEIVED.EXTRACTION
│   └── Attributes: from_state, to_state, trigger
│
├── Span: node.extractor
│   ├── Attributes: agent_name, timeout_seconds
│   │
│   ├── Span: llm.call
│   │   ├── Attributes: provider, model, input_tokens, output_tokens
│   │   └── Events: prompt_sent, response_received
│   │
│   ├── Span: tool.document_retrieval
│   │   ├── Attributes: tool_name, timeout_seconds
│   │   └── Events: call_started, call_completed
│   │
│   └── Events: node_started, node_completed
│
├── Span: transition.EXTRACTION.INVESTIGATION
│   └── Attributes: from_state, to_state, trigger
│
├── Span: node.investigator
│   ├── Attributes: agent_name, timeout_seconds
│   │
│   ├── Span: tool.policy_lookup
│   │   ├── Attributes: policy_number, is_found
│   │   └── Events: call_started, call_completed
│   │
│   ├── Span: tool.claim_history_lookup
│   │   └── Attributes: policy_number, previous_claims_count
│   │
│   ├── Span: tool.repair_cost_lookup
│   │   └── Attributes: vehicle_make, damage_type
│   │
│   ├── Span: tool.fraud_signal_lookup
│   │   └── Attributes: risk_score, risk_level
│   │
│   └── Span: llm.call
│       └── Attributes: provider, model, tokens
│
├── Span: transition.INVESTIGATION.REVIEW
│   └── Attributes: from_state, to_state, trigger
│
├── Span: node.reviewer
│   ├── Attributes: agent_name, rework_count
│   │
│   └── Span: llm.call
│       └── Attributes: provider, model, tokens
│
├── Span: transition.REVIEW.HUMAN_APPROVAL
│   └── Attributes: from_state, to_state, recommendation_type
│
├── Span: human_approval.wait
│   ├── Attributes: approver_id, timeout_at
│   └── Events: approval_requested, approval_received
│
└── Span: workflow.completed
    └── Attributes: terminal_state, reason, total_tokens, total_cost
```

## Tracing Implementation

### OpenTelemetry Setup

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource


def setup_tracing(
    service_name: str = "casefile",
    otlp_endpoint: str = "http://localhost:4317",
    sample_rate: float = 1.0
) -> TracerProvider:
    """
    Set up OpenTelemetry tracing.
    """

    # Create resource with service info
    resource = Resource(attributes={
        "service.name": service_name,
        "service.version": "1.0.0",
    })

    # Create tracer provider
    provider = TracerProvider(resource=resource)

    # Add OTLP exporter
    otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
    processor = BatchSpanProcessor(otlp_exporter)
    provider.add_span_processor(processor)

    # Set as global tracer provider
    trace.set_tracer_provider(provider)

    return provider


# Tracer instance
tracer = trace.get_tracer("casefile")
```

### Span Creation

```python
from opentelemetry.trace import Status, StatusCode
from contextlib import contextmanager


class ObservabilityContext:
    """
    Context for observability within workflow execution.
    """

    def __init__(
        self,
        workflow_run_id: UUID,
        claim_id: UUID,
        trace_id: str
    ):
        self.workflow_run_id = workflow_run_id
        self.claim_id = claim_id
        self.trace_id = trace_id


class TracingHelper:
    """
    Helper for creating and managing spans.
    """

    @staticmethod
    @contextmanager
    def workflow_span(
        name: str,
        context: ObservabilityContext,
        attributes: Optional[Dict[str, Any]] = None
    ):
        """
        Create a workflow-level span.
        """

        with tracer.start_as_current_span(name) as span:
            # Set standard attributes
            span.set_attribute("workflow.run_id", str(context.workflow_run_id))
            span.set_attribute("workflow.claim_id", str(context.claim_id))
            span.set_attribute("workflow.trace_id", context.trace_id)

            # Set custom attributes
            if attributes:
                for key, value in attributes.items():
                    span.set_attribute(key, value)

            try:
                yield span
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR))
                span.record_exception(e)
                raise

    @staticmethod
    @contextmanager
    def node_span(
        agent_name: str,
        context: ObservabilityContext,
        attributes: Optional[Dict[str, Any]] = None
    ):
        """
        Create a node execution span.
        """

        with tracer.start_as_current_span(f"node.{agent_name}") as span:
            span.set_attribute("node.agent_name", agent_name)
            span.set_attribute("workflow.run_id", str(context.workflow_run_id))
            span.set_attribute("workflow.claim_id", str(context.claim_id))

            if attributes:
                for key, value in attributes.items():
                    span.set_attribute(key, value)

            try:
                yield span
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR))
                span.record_exception(e)
                raise

    @staticmethod
    @contextmanager
    def tool_span(
        tool_name: str,
        context: ObservabilityContext,
        attributes: Optional[Dict[str, Any]] = None
    ):
        """
        Create a tool execution span.
        """

        with tracer.start_as_current_span(f"tool.{tool_name}") as span:
            span.set_attribute("tool.name", tool_name)
            span.set_attribute("workflow.run_id", str(context.workflow_run_id))

            if attributes:
                for key, value in attributes.items():
                    span.set_attribute(key, value)

            try:
                yield span
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR))
                span.record_exception(e)
                raise

    @staticmethod
    @contextmanager
    def llm_span(
        provider: str,
        model: str,
        context: ObservabilityContext,
        attributes: Optional[Dict[str, Any]] = None
    ):
        """
        Create an LLM call span.
        """

        with tracer.start_as_current_span(f"llm.call.{provider}.{model}") as span:
            span.set_attribute("llm.provider", provider)
            span.set_attribute("llm.model", model)
            span.set_attribute("workflow.run_id", str(context.workflow_run_id))

            if attributes:
                for key, value in attributes.items():
                    span.set_attribute(key, value)

            try:
                yield span
            except Exception as e:
                span.set_status(Status(StatusCode.ERROR))
                span.record_exception(e)
                raise

    @staticmethod
    @contextmanager
    def transition_span(
        from_state: WorkflowState,
        to_state: WorkflowState,
        context: ObservabilityContext
    ):
        """
        Create a state transition span.
        """

        with tracer.start_as_current_span(
            f"transition.{from_state}.{to_state}"
        ) as span:
            span.set_attribute("transition.from_state", from_state)
            span.set_attribute("transition.to_state", to_state)
            span.set_attribute("workflow.run_id", str(context.workflow_run_id))

            yield span


# Usage example
async def execute_agent_with_tracing(
    agent: SpecialistAgent,
    request: AgentRequest,
    context: ObservabilityContext
) -> AgentResult:
    """
    Execute agent with full tracing.
    """

    with TracingHelper.node_span(agent.agent_name, context) as span:
        # Add input attributes
        span.set_attribute("node.timeout_seconds", agent.timeout.total_seconds())
        span.set_attribute("node.max_tokens", agent.max_input_tokens)

        start_time = datetime.utcnow()

        try:
            # Execute agent
            result = await agent.execute(request)

            # Add result attributes
            span.set_attribute("node.status", result.status)
            span.set_attribute("node.tokens_input", result.tokens_used.input_tokens)
            span.set_attribute("node.tokens_output", result.tokens_used.output_tokens)
            span.set_attribute("node.cost_usd", str(result.estimated_cost_usd))
            span.set_attribute("node.execution_time_ms", result.execution_time_ms)

            span.set_status(Status(StatusCode.OK))

            return result

        except Exception as e:
            span.set_attribute("node.error", str(e))
            raise
```

### Span Events

```python
class SpanEventEmitter:
    """
    Emits events on spans for important occurrences.
    """

    @staticmethod
    def emit_llm_event(
        span,
        event_type: str,
        details: Dict[str, Any]
    ) -> None:
        """
        Emit an LLM-related event.
        """

        span.add_event(
            f"llm.{event_type}",
            attributes={
                "timestamp": datetime.utcnow().isoformat(),
                **details
            }
        )

    @staticmethod
    def emit_tool_event(
        span,
        event_type: str,
        tool_name: str,
        details: Dict[str, Any]
    ) -> None:
        """
        Emit a tool-related event.
        """

        span.add_event(
            f"tool.{event_type}",
            attributes={
                "tool.name": tool_name,
                "timestamp": datetime.utcnow().isoformat(),
                **details
            }
        )

    @staticmethod
    def emit_transition_event(
        span,
        from_state: WorkflowState,
        to_state: WorkflowState,
        trigger: str
    ) -> None:
        """
        Emit a transition event.
        """

        span.add_event(
            "state_transition",
            attributes={
                "transition.from": from_state,
                "transition.to": to_state,
                "transition.trigger": trigger,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
```

## Metrics

### Metric Types

```python
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.exporter.prometheus import PrometheusMetricReader


def setup_metrics(
    service_name: str = "casefile",
    prometheus_port: int = 9090
) -> MeterProvider:
    """
    Set up OpenTelemetry metrics with Prometheus export.
    """

    # Create Prometheus reader
    reader = PrometheusMetricReader()

    # Create meter provider
    provider = MeterProvider(
        resource=Resource(attributes={"service.name": service_name}),
        readers=[reader]
    )

    metrics.set_meter_provider(provider)

    return provider


# Meter instance
meter = metrics.get_meter("casefile")
```

### Defined Metrics

```python
class MetricsDefinitions:
    """
    All CASEFILE metrics definitions.
    """

    # Counters
    WORKFLOW_STARTED = meter.create_counter(
        "casefile.workflow.started",
        description="Number of workflows started",
        unit="1"
    )

    WORKFLOW_COMPLETED = meter.create_counter(
        "casefile.workflow.completed",
        description="Number of workflows completed",
        unit="1"
    )

    NODE_EXECUTIONS = meter.create_counter(
        "casefile.node.executions",
        description="Number of node executions",
        unit="1"
    )

    TOOL_CALLS = meter.create_counter(
        "casefile.tool.calls",
        description="Number of tool calls",
        unit="1"
    )

    LLM_CALLS = meter.create_counter(
        "casefile.llm.calls",
        description="Number of LLM calls",
        unit="1"
    )

    ERRORS = meter.create_counter(
        "casefile.errors",
        description="Number of errors",
        unit="1"
    )

    # Histograms
    WORKFLOW_DURATION = meter.create_histogram(
        "casefile.workflow.duration_ms",
        description="Workflow execution duration",
        unit="ms"
    )

    NODE_DURATION = meter.create_histogram(
        "casefile.node.duration_ms",
        description="Node execution duration",
        unit="ms"
    )

    TOOL_DURATION = meter.create_histogram(
        "casefile.tool.duration_ms",
        description="Tool call duration",
        unit="ms"
    )

    LLM_DURATION = meter.create_histogram(
        "casefile.llm.duration_ms",
        description="LLM call duration",
        unit="ms"
    )

    LLM_TOKENS = meter.create_histogram(
        "casefile.llm.tokens",
        description="LLM token usage",
        unit="1"
    )

    LLM_COST = meter.create_histogram(
        "casefile.llm.cost_usd",
        description="LLM cost",
        unit="USD"
    )

    WORKFLOW_COST = meter.create_histogram(
        "casefile.workflow.cost_usd",
        description="Total workflow cost",
        unit="USD"
    )

    WORKFLOW_STEPS = meter.create_histogram(
        "casefile.workflow.steps",
        description="Number of steps per workflow",
        unit="1"
    )

    # Gauges (up-down counters)
    ACTIVE_WORKFLOWS = meter.create_up_down_counter(
        "casefile.workflow.active",
        description="Number of active workflows",
        unit="1"
    )

    PENDING_APPROVALS = meter.create_up_down_counter(
        "casefile.approval.pending",
        description="Number of pending approvals",
        unit="1"
    )
```

### Metrics Recording

```python
class MetricsRecorder:
    """
    Records metrics for CASEFILE operations.
    """

    @staticmethod
    def record_workflow_started(
        claim_type: str,
        priority: str
    ) -> None:
        """
        Record workflow start.
        """

        MetricsDefinitions.WORKFLOW_STARTED.add(
            1,
            attributes={
                "claim_type": claim_type,
                "priority": priority
            }
        )

        MetricsDefinitions.ACTIVE_WORKFLOWS.add(1)

    @staticmethod
    def record_workflow_completed(
        terminal_state: str,
        duration_ms: int,
        total_cost: Decimal,
        steps: int
    ) -> None:
        """
        Record workflow completion.
        """

        MetricsDefinitions.WORKFLOW_COMPLETED.add(
            1,
            attributes={"terminal_state": terminal_state}
        )

        MetricsDefinitions.WORKFLOW_DURATION.record(duration_ms)
        MetricsDefinitions.WORKFLOW_COST.record(float(total_cost))
        MetricsDefinitions.WORKFLOW_STEPS.record(steps)

        MetricsDefinitions.ACTIVE_WORKFLOWS.add(-1)

    @staticmethod
    def record_node_execution(
        agent_name: str,
        status: str,
        duration_ms: int,
        tokens: int,
        cost: Decimal
    ) -> None:
        """
        Record node execution.
        """

        MetricsDefinitions.NODE_EXECUTIONS.add(
            1,
            attributes={
                "agent": agent_name,
                "status": status
            }
        )

        MetricsDefinitions.NODE_DURATION.record(
            duration_ms,
            attributes={"agent": agent_name}
        )

        if tokens > 0:
            MetricsDefinitions.LLM_TOKENS.record(
                tokens,
                attributes={"agent": agent_name}
            )

        if cost > 0:
            MetricsDefinitions.LLM_COST.record(
                float(cost),
                attributes={"agent": agent_name}
            )

    @staticmethod
    def record_tool_call(
        tool_name: str,
        status: str,
        duration_ms: int
    ) -> None:
        """
        Record tool call.
        """

        MetricsDefinitions.TOOL_CALLS.add(
            1,
            attributes={
                "tool": tool_name,
                "status": status
            }
        )

        MetricsDefinitions.TOOL_DURATION.record(
            duration_ms,
            attributes={"tool": tool_name}
        )

    @staticmethod
    def record_error(
        error_type: str,
        component: str
    ) -> None:
        """
        Record error.
        """

        MetricsDefinitions.ERRORS.add(
            1,
            attributes={
                "error_type": error_type,
                "component": component
            }
        )
```

## Structured Logging

### Log Format

```python
import structlog
import json
from datetime import datetime


def configure_logging() -> None:
    """
    Configure structured logging.
    """

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            # JSON output
            structlog.processors.JSONRenderer()
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        cache_logger_on_first_use=True,
    )


# Logger instance
logger = structlog.get_logger("casefile")
```

### Logging Usage

```python
class WorkflowLogger:
    """
    Structured logging for workflow operations.
    """

    def __init__(
        self,
        workflow_run_id: UUID,
        claim_id: UUID,
        trace_id: str
    ):
        self.workflow_run_id = workflow_run_id
        self.claim_id = claim_id
        self.trace_id = trace_id

        # Bind context to logger
        self.log = logger.bind(
            workflow_run_id=str(workflow_run_id),
            claim_id=str(claim_id),
            trace_id=trace_id
        )

    def log_workflow_started(
        self,
        claim_type: str,
        policy_number: str
    ) -> None:
        """
        Log workflow start.
        """

        self.log.info(
            "workflow_started",
            claim_type=claim_type,
            policy_number=self._mask_policy(policy_number)
        )

    def log_transition(
        self,
        from_state: WorkflowState,
        to_state: WorkflowState,
        trigger: str
    ) -> None:
        """
        Log state transition.
        """

        self.log.info(
            "state_transition",
            from_state=from_state,
            to_state=to_state,
            trigger=trigger
        )

    def log_node_started(
        self,
        agent_name: str,
        step_count: int
    ) -> None:
        """
        Log node execution start.
        """

        self.log.info(
            "node_started",
            agent=agent_name,
            step_count=step_count
        )

    def log_node_completed(
        self,
        agent_name: str,
        status: str,
        duration_ms: int,
        tokens: int,
        cost: Decimal
    ) -> None:
        """
        Log node execution completion.
        """

        self.log.info(
            "node_completed",
            agent=agent_name,
            status=status,
            duration_ms=duration_ms,
            tokens=tokens,
            cost_usd=str(cost)
        )

    def log_tool_call(
        self,
        tool_name: str,
        status: str,
        duration_ms: int
    ) -> None:
        """
        Log tool call.
        """

        self.log.info(
            "tool_call",
            tool=tool_name,
            status=status,
            duration_ms=duration_ms
        )

    def log_error(
        self,
        error: Exception,
        context: Dict[str, Any]
    ) -> None:
        """
        Log error.
        """

        self.log.error(
            "error_occurred",
            error_type=type(error).__name__,
            error_message=str(error),
            **context
        )

    def log_workflow_completed(
        self,
        terminal_state: WorkflowState,
        reason: str,
        total_cost: Decimal
    ) -> None:
        """
        Log workflow completion.
        """

        self.log.info(
            "workflow_completed",
            terminal_state=terminal_state,
            reason=reason,
            total_cost_usd=str(total_cost)
        )

    def _mask_policy(self, policy_number: str) -> str:
        """
        Mask policy number for logging.
        """

        if len(policy_number) <= 8:
            return "***"

        return f"{policy_number[:4]}...{policy_number[-4:]}"
```

## Dashboards and Queries

### Common Queries

```yaml
# Example Grafana/PromQL queries

# Workflow completion rate (per minute)
rate(casefile_workflow_completed_total[1m])

# Average workflow duration
rate(casefile_workflow_duration_ms_sum[5m]) / rate(casefile_workflow_duration_ms_count[5m])

# P95 workflow duration
histogram_quantile(0.95, rate(casefile_workflow_duration_ms_bucket[5m]))

# Error rate by component
sum by (component) (rate(casefile_errors_total[5m]))

# Active workflows
casefile_workflow_active

# Pending approvals
casefile_approval_pending

# Token usage per workflow
rate(casefile_llm_tokens_sum[1h]) / rate(casefile_workflow_completed_total[1h])

# Cost per workflow
rate(casefile_workflow_cost_usd_sum[1h]) / rate(casefile_workflow_completed_total[1h])

# Tool call success rate
sum(rate(casefile_tool_calls_total{status="SUCCESS"}[5m])) / sum(rate(casefile_tool_calls_total[5m]))
```

### Dashboard Panels

```
CASEFILE Overview Dashboard
============================

Row 1: Throughput
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  Workflows      │  │  Claims/Hour    │  │  Active         │
│  Started        │  │                 │  │  Workflows      │
│  [Count]        │  │  [Rate]         │  │  [Gauge]        │
└─────────────────┘  └─────────────────┘  └─────────────────┘

Row 2: Latency
┌─────────────────────────────────────────────────────────────┐
│  Workflow Duration (P50, P95, P99)                          │
│  [Time Series Graph]                                        │
└─────────────────────────────────────────────────────────────┘

Row 3: Errors
┌─────────────────┐  ┌─────────────────────────────────────────┐
│  Error Rate     │  │  Errors by Type                         │
│  [Percentage]   │  │  [Pie Chart]                            │
└─────────────────┘  └─────────────────────────────────────────┘

Row 4: Cost
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  Cost/Claim     │  │  Token Usage    │  │  LLM Calls      │
│  [Dollars]      │  │  [Count]        │  │  [Count]        │
└─────────────────┘  └─────────────────┘  └─────────────────┘

Row 5: Terminal States
┌─────────────────────────────────────────────────────────────┐
│  Terminal State Distribution                                │
│  [Pie Chart: APPROVED, REJECTED, FAILED, EXCEPTION]         │
└─────────────────────────────────────────────────────────────┘
```

## Alerting

### Alert Rules

```yaml
# Example alerting rules

groups:
  - name: casefile.rules
    rules:
      - alert: HighErrorRate
        expr: rate(casefile_errors_total[5m]) > 0.1
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "High error rate detected"
          description: "Error rate is {{ $value }}/s"

      - alert: WorkflowTimeoutSpike
        expr: rate(casefile_workflow_completed_total{terminal_state="TIMEOUT"}[5m]) > 0.05
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "Workflow timeout spike"
          description: "Timeout rate is {{ $value }}/s"

      - alert: HighCostPerClaim
        expr: rate(casefile_workflow_cost_usd_sum[1h]) / rate(casefile_workflow_completed_total[1h]) > 3
        for: 15m
        labels:
          severity: warning
        annotations:
          summary: "Cost per claim above threshold"
          description: "Average cost is ${{ $value }}"

      - alert: BudgetExhaustionEvents
        expr: rate(casefile_workflow_completed_total{terminal_state="BUDGET_EXHAUSTED"}[5m]) > 0
        for: 1m
        labels:
          severity: critical
        annotations:
          summary: "Budget exhaustion detected"
          description: "{{ $value }} workflows exhausted budget"

      - alert: PendingApprovalsBacklog
        expr: casefile_approval_pending > 50
        for: 15m
        labels:
          severity: warning
        annotations:
          summary: "Pending approvals backlog"
          description: "{{ $value }} approvals pending"
```

## Summary

The observability architecture provides:
- **Complete tracing**: Every action produces traceable spans
- **Structured logging**: JSON logs with context correlation
- **Comprehensive metrics**: Counters, histograms, and gauges
- **OpenTelemetry**: Vendor-neutral telemetry export
- **Dashboards**: Pre-built visualizations for monitoring
- **Alerting**: Proactive notification of issues
- **Correlation**: Trace IDs link all telemetry together

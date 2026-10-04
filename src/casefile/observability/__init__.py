"""
CASEFILE observability package (Phase 10).

Diagnostic telemetry (traces, metrics) strictly separated from durable
business history (SQLite audit/checkpoints). Telemetry failure can never
break workflow correctness.
"""

from casefile.observability.bridge import HookBridge
from casefile.observability.context import EMPTY_CONTEXT, ExecMode, TraceContext
from casefile.observability.meters import (
    FORBIDDEN_LABEL_KEYS,
    METRIC_NAMES,
    CasefileMeters,
    check_labels,
)
from casefile.observability.provider import (
    Observability,
    configure_observability,
    test_observability,
)
from casefile.observability.sanitize import (
    MAX_ATTRIBUTE_LENGTH,
    SafeErrorModel,
    safe_error,
    sanitize_attributes,
    sanitize_value,
)
from casefile.observability.spans import (
    SPAN_AGENT,
    SPAN_AGENT_RUN,
    SPAN_APPROVAL,
    SPAN_APPROVAL_DECIDE,
    SPAN_BUDGET,
    SPAN_BUDGET_CHECK,
    SPAN_CHECKPOINT,
    SPAN_CHECKPOINT_CREATE,
    SPAN_CHECKPOINT_LOAD,
    SPAN_PERSISTENCE,
    SPAN_REPLAY_RUN,
    SPAN_SUPERVISOR_ROUTE,
    SPAN_TOOL,
    SPAN_TOOL_CALL,
    SPAN_TRANSITION,
    SPAN_WORKFLOW,
    SPAN_WORKFLOW_RUN,
    agent_attributes,
    approval_attributes,
    budget_attributes,
    checkpoint_attributes,
    persistence_attributes,
    supervisor_attributes,
    tool_attributes,
    transition_attributes,
    workflow_attributes,
)
from casefile.observability.tracer import OtelTracer, SpanHandle, maybe_span

__all__ = [
    "HookBridge",
    "EMPTY_CONTEXT",
    "ExecMode",
    "TraceContext",
    "FORBIDDEN_LABEL_KEYS",
    "METRIC_NAMES",
    "CasefileMeters",
    "check_labels",
    "Observability",
    "configure_observability",
    "test_observability",
    "MAX_ATTRIBUTE_LENGTH",
    "SafeErrorModel",
    "safe_error",
    "sanitize_attributes",
    "sanitize_value",
    "SPAN_AGENT_RUN",
    "SPAN_APPROVAL_DECIDE",
    "SPAN_BUDGET_CHECK",
    "SPAN_CHECKPOINT_CREATE",
    "SPAN_CHECKPOINT_LOAD",
    "SPAN_REPLAY_RUN",
    "SPAN_SUPERVISOR_ROUTE",
    "SPAN_TOOL_CALL",
    "SPAN_TRANSITION",
    "SPAN_WORKFLOW_RUN",
    "SPAN_PERSISTENCE",
    "SPAN_WORKFLOW",
    "SPAN_AGENT",
    "SPAN_TOOL",
    "SPAN_CHECKPOINT",
    "SPAN_BUDGET",
    "SPAN_APPROVAL",
    "agent_attributes",
    "approval_attributes",
    "budget_attributes",
    "checkpoint_attributes",
    "persistence_attributes",
    "supervisor_attributes",
    "tool_attributes",
    "transition_attributes",
    "workflow_attributes",
    "OtelTracer",
    "SpanHandle",
    "maybe_span",
]

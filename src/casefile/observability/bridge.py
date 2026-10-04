"""
Hook bridge: existing Phase 3 hook stream → metrics (Phase 10 §20).

HookPayloads already flow through EventSinks; this sink implementation
translates them into low-cardinality metric increments. Spans are owned
by explicit operation boundaries (see spans.py), not by this bridge —
business state commits never wait on telemetry.
"""

from __future__ import annotations

from casefile.observability.meters import CasefileMeters
from casefile.workflow.hooks import EventSink, HookPayload, WorkflowHookEvent

_EVENT_METRICS: dict[WorkflowHookEvent, tuple[str, dict[str, str]]] = {
    WorkflowHookEvent.WORKFLOW_STARTED: ("casefile.workflow.runs", {"outcome": "started"}),
    WorkflowHookEvent.NODE_COMPLETED: ("casefile.agent.executions", {"outcome": "success"}),
    WorkflowHookEvent.NODE_FAILED: ("casefile.agent.failures", {"outcome": "failure"}),
    WorkflowHookEvent.STRUCTURED_OUTPUT_INVALID: (
        "casefile.agent.failures",
        {"outcome": "invalid_output"},
    ),
    WorkflowHookEvent.RETRY_REQUESTED: ("casefile.workflow.retries", {"outcome": "requested"}),
    WorkflowHookEvent.TOOL_COMPLETED: ("casefile.tool.invocations", {"outcome": "success"}),
    WorkflowHookEvent.TOOL_FAILED: ("casefile.tool.failures", {"outcome": "failure"}),
    WorkflowHookEvent.TOOL_AUTHORIZATION_DENIED: (
        "casefile.tool.failures",
        {"outcome": "denied"},
    ),
    WorkflowHookEvent.CHECKPOINT_CREATED: ("casefile.workflow.runs", {"outcome": "checkpoint"}),
    WorkflowHookEvent.REPLAY_COMPLETED: ("casefile.workflow.runs", {"outcome": "replayed"}),
    WorkflowHookEvent.REPLAY_FAILED: ("casefile.workflow.failures", {"outcome": "replay_failed"}),
    WorkflowHookEvent.APPROVAL_REQUESTED: ("casefile.approval.decisions", {"outcome": "requested"}),
    WorkflowHookEvent.WORKFLOW_TERMINATED: ("casefile.workflow.runs", {"outcome": "terminated"}),
}


class HookBridge(EventSink):
    """EventSink that records metrics without touching business state."""

    def __init__(
        self,
        meters: CasefileMeters | None = None,
        tracer: object = None,
    ) -> None:
        self._meters = meters
        self._tracer = tracer

    def emit(self, payload: HookPayload) -> None:
        """Translate one hook into a metric increment; never raises."""
        try:
            if self._meters is None:
                return
            mapping = _EVENT_METRICS.get(payload.event)
            if mapping is None:
                return
            name, labels = mapping
            self._meters.record_counter(name, 1.0, dict(labels))
        except Exception:
            pass

    def on_event(self, event: WorkflowHookEvent, data: dict[str, object] | None = None) -> None:
        """Compatibility method for direct event dispatch; never raises."""
        try:
            if self._meters is None:
                return
            mapping = _EVENT_METRICS.get(event)
            if mapping is None:
                return
            name, labels = mapping
            self._meters.record_counter(name, 1.0, dict(labels))
        except Exception:
            pass

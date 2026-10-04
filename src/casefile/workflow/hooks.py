"""
CASEFILE workflow observability hooks (Phase 3 §18).

Clean event boundary for Phase 10 OpenTelemetry instrumentation. Business
logic emits typed hook payloads to an EventSink; no tracing code lives in
the engine, router, or nodes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel, Field


class WorkflowHookEvent(str, Enum):
    """Observable lifecycle events of a workflow run."""

    WORKFLOW_STARTED = "WORKFLOW_STARTED"
    STATE_TRANSITION = "STATE_TRANSITION"
    TRANSITION_APPLIED = "TRANSITION_APPLIED"
    NODE_STARTED = "NODE_STARTED"
    AGENT_STARTED = "AGENT_STARTED"
    NODE_COMPLETED = "NODE_COMPLETED"
    AGENT_COMPLETED = "AGENT_COMPLETED"
    NODE_FAILED = "NODE_FAILED"
    AGENT_FAILED = "AGENT_FAILED"
    STRUCTURED_OUTPUT_INVALID = "STRUCTURED_OUTPUT_INVALID"
    RETRY_REQUESTED = "RETRY_REQUESTED"
    REWORK_REQUESTED = "REWORK_REQUESTED"
    TOOL_REQUESTED = "TOOL_REQUESTED"
    TOOL_STARTED = "TOOL_STARTED"
    TOOL_COMPLETED = "TOOL_COMPLETED"
    TOOL_FAILED = "TOOL_FAILED"
    TOOL_AUTHORIZATION_DENIED = "TOOL_AUTHORIZATION_DENIED"
    CHECKPOINT_CREATED = "CHECKPOINT_CREATED"
    CHECKPOINT_LOADED = "CHECKPOINT_LOADED"
    CHECKPOINT_RESTORED = "CHECKPOINT_RESTORED"
    REPLAY_STARTED = "REPLAY_STARTED"
    REPLAY_COMPLETED = "REPLAY_COMPLETED"
    REPLAY_FAILED = "REPLAY_FAILED"
    REPLAY_MISMATCH = "REPLAY_MISMATCH"
    BUDGET_EVALUATED = "BUDGET_EVALUATED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    STEP_LIMIT_REACHED = "STEP_LIMIT_REACHED"
    TERMINATION_DECISION = "TERMINATION_DECISION"
    APPROVAL_REQUESTED = "APPROVAL_REQUESTED"
    WORKFLOW_TERMINATED = "WORKFLOW_TERMINATED"
    WORKFLOW_TERMINAL = "WORKFLOW_TERMINAL"


class HookPayload(BaseModel):
    """Typed payload for one hook emission."""

    event: WorkflowHookEvent
    workflow_run_id: UUID
    claim_id: UUID
    correlation_id: UUID
    node: str = ""
    from_state: str = ""
    to_state: str = ""
    detail: str = ""
    emitted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class EventSink(Protocol):
    """Destination for workflow hook payloads."""

    def emit(self, payload: HookPayload) -> None:
        """Accept one hook payload."""
        ...


class NullSink:
    """Default sink: discards all hooks."""

    def emit(self, payload: HookPayload) -> None:
        """Accept and discard one hook payload."""
        _ = payload


class RecordingSink:
    """Test sink: retains every emitted payload in order."""

    def __init__(self) -> None:
        self.payloads: list[HookPayload] = []

    def emit(self, payload: HookPayload) -> None:
        """Record one hook payload."""
        self.payloads.append(payload)

    def events(self) -> list[WorkflowHookEvent]:
        """Event types in emission order."""
        return [payload.event for payload in self.payloads]

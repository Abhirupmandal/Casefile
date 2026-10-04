"""
Trace context: stable correlation enrichment for every execution.

TraceContext carries OTel trace/span identity alongside the existing
domain IDs (run, execution, claim, checkpoint, approval, correlation).
It enriches — never replaces — business identity. Explicitly passed,
never global.
"""

from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import BaseModel


class ExecMode(str, Enum):
    """LIVE production execution vs REPLAY reconstruction."""

    LIVE = "LIVE"
    REPLAY = "REPLAY"


class TraceContext(BaseModel):
    """Correlation context attached to spans, metrics, and audit."""

    model_config = {"frozen": True}

    trace_id: str = ""
    span_id: str = ""
    workflow_run_id: UUID | None = None
    execution_id: UUID | None = None
    claim_id: UUID | None = None
    checkpoint_id: UUID | None = None
    approval_id: UUID | None = None
    correlation_id: UUID | None = None
    mode: ExecMode = ExecMode.LIVE

    def with_span(self, trace_id: str, span_id: str) -> TraceContext:
        """Return a copy bound to concrete OTel span identity."""
        return self.model_copy(update={"trace_id": trace_id, "span_id": span_id})

    def for_replay(self) -> TraceContext:
        """Return a copy explicitly tagged as REPLAY."""
        return self.model_copy(update={"mode": ExecMode.REPLAY})

    def attribute_dict(self) -> dict[str, str]:
        """Sanitized span-attribute form (IDs only, no payloads)."""
        attributes: dict[str, str] = {
            "casefile.mode": self.mode.value,
            "casefile.exec_mode": self.mode.value,
        }
        if self.trace_id:
            attributes["casefile.trace_id"] = self.trace_id
        if self.workflow_run_id is not None:
            attributes["casefile.workflow_run_id"] = str(self.workflow_run_id)
        if self.execution_id is not None:
            attributes["casefile.execution_id"] = str(self.execution_id)
        if self.claim_id is not None:
            attributes["casefile.claim_id"] = str(self.claim_id)
        if self.checkpoint_id is not None:
            attributes["casefile.checkpoint_id"] = str(self.checkpoint_id)
        if self.approval_id is not None:
            attributes["casefile.approval_id"] = str(self.approval_id)
        if self.correlation_id is not None:
            attributes["casefile.correlation_id"] = str(self.correlation_id)
        return attributes

    def as_span_attributes(self) -> dict[str, str]:
        """Sanitized span-attribute form (IDs only, no payloads)."""
        return self.attribute_dict()

    def child(self, execution_id: UUID | None = None) -> TraceContext:
        """Return a child context inheriting correlation context with a fresh execution_id."""
        from uuid import uuid4

        return self.model_copy(update={"execution_id": execution_id or uuid4()})


EMPTY_CONTEXT = TraceContext()

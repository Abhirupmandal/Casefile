"""
Resume from checkpoint (Phase 7 §7).

Load → integrity → version compatibility → typed state reconstruction →
fresh execution context. Invalid checkpoints never resume. Terminal
checkpoints load fine but the engine will reject further transitions;
human-wait checkpoints stay waiting until an explicit human event.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from pydantic import BaseModel

from casefile.checkpoint.model import (
    Checkpoint,
    CheckpointCorruptError,
    CheckpointIncompatibleError,
    CheckpointNotFoundError,
)
from casefile.checkpoint.repository import CheckpointRepository
from casefile.models.versioning import IncompatibleSchemaVersionError
from casefile.workflow.context import RunContext, WorkflowSnapshot

if TYPE_CHECKING:
    from casefile.observability.provider import Observability


class ResumeResult(BaseModel):
    """Reconstructed resume state: snapshot, checkpoint, fresh context."""

    model_config = {"arbitrary_types_allowed": True}

    snapshot: WorkflowSnapshot
    checkpoint: Checkpoint
    context: RunContext


class CheckpointResumeError(Exception):
    """Resume-time failure with a clear cause."""


def resume_from_checkpoint(
    checkpoint_id: object,
    *,
    checkpoints: CheckpointRepository,
    max_rework_cycles: int = 3,
    obs: Observability | None = None,
) -> ResumeResult:
    """Load, verify, and reconstruct execution state for a checkpoint id."""
    from casefile.observability.spans import SPAN_CHECKPOINT_LOAD
    from casefile.observability.tracer import maybe_span

    tracer = obs.tracer if obs is not None else None
    with maybe_span(tracer, SPAN_CHECKPOINT_LOAD, {"casefile.operation": "resume"}) as span:
        try:
            result = _resume_inner(checkpoint_id, checkpoints, max_rework_cycles)
        except Exception as exc:
            span.set_status_error(type(exc).__name__)
            raise
        span.set_attribute("casefile.success", True)
        return result


def _resume_inner(
    checkpoint_id: object,
    checkpoints: CheckpointRepository,
    max_rework_cycles: int,
) -> ResumeResult:
    """Uninstrumented resume body (logic unchanged)."""
    try:
        parsed_id = checkpoint_id if isinstance(checkpoint_id, UUID) else UUID(str(checkpoint_id))
    except ValueError as exc:
        raise CheckpointNotFoundError(f"Malformed checkpoint id {checkpoint_id!r}") from exc
    checkpoint = checkpoints.get(parsed_id)
    if not checkpoint.verify_integrity():
        raise CheckpointCorruptError(f"Checkpoint {parsed_id} failed integrity verification")
    try:
        checkpoint.check_compatible()
    except IncompatibleSchemaVersionError as exc:
        raise CheckpointIncompatibleError(str(exc)) from exc
    context = RunContext(
        claim_id=checkpoint.claim_id,
        workflow_run_id=checkpoint.workflow_run_id,
        execution_id=uuid4(),
        correlation_id=checkpoint.correlation_id,
        max_rework_cycles=max_rework_cycles,
    )
    return ResumeResult(snapshot=checkpoint.snapshot, checkpoint=checkpoint, context=context)

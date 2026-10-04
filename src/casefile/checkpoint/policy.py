"""
Checkpoint policy: when to snapshot (Phase 7 §6).

- After every successful node transition → TRANSITION
- Before entering human-wait (destination HUMAN_APPROVAL) → HUMAN_WAIT
- Before terminal completion → TERMINAL
- Never twice for the same applied transition (path-based dedupe)
"""

from __future__ import annotations

from casefile.checkpoint.model import Checkpoint, CheckpointKind
from casefile.models.contracts import WorkflowState
from casefile.workflow.states import requires_human_approval
from casefile.workflow.transitions import TransitionRecord


def checkpoint_kind_for(destination: WorkflowState) -> CheckpointKind:
    """Classify a checkpoint by its destination state."""
    if destination.is_terminal:
        return CheckpointKind.TERMINAL
    if requires_human_approval(destination):
        return CheckpointKind.HUMAN_WAIT
    return CheckpointKind.TRANSITION


def should_checkpoint(
    record: TransitionRecord,
    latest: Checkpoint | None,
) -> tuple[bool, CheckpointKind]:
    """Decide whether an applied transition warrants a checkpoint.

    Returns (take_checkpoint, kind). Skips only transitions already
    recorded on the latest checkpoint's path (same sequence, trigger,
    and actor).
    """
    kind = checkpoint_kind_for(record.destination)
    if latest is not None and any(
        entry.sequence_no == record.sequence_no
        and entry.trigger == record.trigger
        and entry.actor == record.actor
        for entry in latest.path
    ):
        return False, kind
    return True, kind

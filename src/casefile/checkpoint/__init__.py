"""
CASEFILE durable checkpointing, resumability, and deterministic replay
(Phase 7).

Checkpoint → process stops → load → verify → resume → same terminal
outcome. Recorded agent/tool outputs make replay faithful without live
providers or networks.

Lazy exports (PEP 562): importing this package must never pull agent,
tool, or workflow graphs as a side effect, so storage-layer modules can
import checkpoint models without import cycles.
"""

from __future__ import annotations

from typing import Any

_EXPORTS: dict[str, str] = {
    "DuplicateClaimError": "casefile.checkpoint.ledger",
    "IdempotencyLedger": "casefile.checkpoint.ledger",
    "ApplicationVersion": "casefile.checkpoint.model",
    "APPLICATION_VERSION": "casefile.checkpoint.model",
    "Checkpoint": "casefile.checkpoint.model",
    "WorkflowCheckpoint": "casefile.checkpoint.model",
    "CheckpointCorruptError": "casefile.checkpoint.model",
    "CheckpointError": "casefile.checkpoint.model",
    "CheckpointIncompatibleError": "casefile.checkpoint.model",
    "CheckpointKind": "casefile.checkpoint.model",
    "CheckpointNotFoundError": "casefile.checkpoint.model",
    "CheckpointSequenceError": "casefile.checkpoint.model",
    "RecordedAgentOutput": "casefile.checkpoint.model",
    "RecordedToolOutput": "casefile.checkpoint.model",
    "TransitionPathEntry": "casefile.checkpoint.model",
    "checkpoint_kind_for": "casefile.checkpoint.policy",
    "should_checkpoint": "casefile.checkpoint.policy",
    "ReplayAgentProvider": "casefile.checkpoint.replay",
    "ReplayMismatch": "casefile.checkpoint.replay",
    "ReplayResult": "casefile.checkpoint.replay",
    "compare_replay_runs": "casefile.checkpoint.replay",
    "replay_workflow": "casefile.checkpoint.replay",
    "replay_from_checkpoints": "casefile.checkpoint.replay",
    "ReplayMissingArtifactError": "casefile.checkpoint.replay",
    "ReplayMode": "casefile.checkpoint.replay",
    "ReplayReport": "casefile.checkpoint.replay",
    "ReplayToolRegistry": "casefile.checkpoint.replay",
    "resume_for_replay": "casefile.checkpoint.replay",
    "run_replay": "casefile.checkpoint.replay",
    "CheckpointRepository": "casefile.checkpoint.repository",
    "SqlCheckpointRepository": "casefile.checkpoint.repository",
    "SqlCheckpointSessionRepository": "casefile.checkpoint.repository",
    "CheckpointResumeError": "casefile.checkpoint.resume",
    "ResumeResult": "casefile.checkpoint.resume",
    "resume_from_checkpoint": "casefile.checkpoint.resume",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    """Lazily resolve public names to keep package import side-effect free."""
    if name in _EXPORTS:
        from importlib import import_module

        module = import_module(_EXPORTS[name])
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

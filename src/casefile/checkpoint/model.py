"""
Typed checkpoint model (Phase 7 §2, §4, §5, §17).

A Checkpoint carries everything needed to reconstruct and resume a
workflow run: identities, versioned snapshot, recorded agent/tool
outputs, transition path position, and (for waiting/terminal kinds) the
approval request or terminal outcome. Payload schemas are explicit
Pydantic models — never raw dicts, never pickle, never live objects,
never secrets.

Integrity: sha256 over the canonical JSON serialization (sorted keys,
checksum excluded). Any payload change breaks verification.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from casefile.models.contracts import WorkflowState
from casefile.models.domain import AgentType, HumanApprovalRequest
from casefile.models.versioning import SchemaVersion, validate_schema_version
from casefile.workflow.context import WorkflowSnapshot
from casefile.workflow.triggers import Trigger

APPLICATION_VERSION = "0.1.0"


class CheckpointError(Exception):
    """Base class for checkpoint failures."""


class CheckpointNotFoundError(CheckpointError):
    """No checkpoint with the requested identity exists."""


class CheckpointCorruptError(CheckpointError):
    """Integrity check failed: payload was modified or malformed."""


class CheckpointIncompatibleError(CheckpointError):
    """Schema version is incompatible under the Phase 2 policy."""


class CheckpointSequenceError(CheckpointError):
    """Duplicate, gapped, or cross-run sequence violation."""


class CheckpointKind(str, Enum):
    """Why a checkpoint was taken."""

    TRANSITION = "TRANSITION"
    HUMAN_WAIT = "HUMAN_WAIT"
    TERMINAL = "TERMINAL"


class RecordedToolOutput(BaseModel):
    """One recorded tool result for replay fidelity."""

    model_config = {"frozen": True}

    tool_name: str
    invocation_id: UUID
    input_hash: str
    output_json: str
    idempotency_key: str
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RecordedAgentOutput(BaseModel):
    """One recorded agent result for replay fidelity (no live LLM)."""

    model_config = {"frozen": True}

    agent: AgentType
    contract_type: str
    output_json: str
    execution_id: UUID
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TransitionPathEntry(BaseModel):
    """One applied transition on the path to this checkpoint."""

    model_config = {"frozen": True}

    sequence_no: int = Field(ge=1)
    trigger: Trigger
    actor: AgentType
    reason: str = ""


class Checkpoint(BaseModel):
    """Durable, verifiable workflow snapshot for resume and replay."""

    checkpoint_id: UUID = Field(default_factory=uuid4)
    claim_id: UUID
    workflow_run_id: UUID
    execution_id: UUID = Field(default_factory=uuid4)
    sequence_no: int = Field(default=0, ge=0)
    parent_checkpoint_id: UUID | None = None
    state: WorkflowState
    kind: CheckpointKind = CheckpointKind.TRANSITION
    snapshot: WorkflowSnapshot
    step_count: int = Field(ge=0)
    rework_count: int = Field(ge=0)
    recorded_tools: tuple[RecordedToolOutput, ...] = ()
    recorded_agents: tuple[RecordedAgentOutput, ...] = ()
    path: tuple[TransitionPathEntry, ...] = ()
    approval_request: HumanApprovalRequest | None = None
    terminal_reason: str | None = None
    correlation_id: UUID = Field(default_factory=uuid4)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    schema_version: SchemaVersion = "1.0.0"
    application_version: str = "0.1.0"
    checksum: str = ""

    @field_validator("terminal_reason")
    @classmethod
    def _validate_reason(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("terminal_reason cannot be blank")
        return value

    @model_validator(mode="after")
    def _validate_kind_shapes(self) -> Checkpoint:
        if self.kind == CheckpointKind.TERMINAL:
            if not self.state.is_terminal:
                raise ValueError("TERMINAL checkpoints require a terminal state")
            if not (self.terminal_reason or "").strip():
                raise ValueError("TERMINAL checkpoints require terminal_reason")
        if self.kind == CheckpointKind.HUMAN_WAIT:
            if self.state != WorkflowState.HUMAN_APPROVAL:
                raise ValueError("HUMAN_WAIT checkpoints require HUMAN_APPROVAL state")
            if self.approval_request is None:
                raise ValueError("HUMAN_WAIT checkpoints require approval_request")
        return self

    def canonical_bytes(self) -> bytes:
        """Deterministic canonical serialization (checksum excluded)."""
        payload = self.model_dump(mode="json", exclude={"checksum"})
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def compute_checksum(self) -> str:
        """sha256 over the canonical payload."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def sealed(self) -> Checkpoint:
        """Return a copy carrying a valid integrity checksum."""
        copy = self.model_copy(deep=True)
        copy.checksum = self.compute_checksum()
        return copy

    def verify_integrity(self) -> bool:
        """True only when a non-empty checksum matches the payload."""
        if not self.checksum:
            return False
        return self.compute_checksum() == self.checksum

    def check_compatible(self) -> str:
        """Validate schema version under the Phase 2 policy (raises if not)."""
        return validate_schema_version(self.schema_version, artifact_type="checkpoint")

    @property
    def checkpoint_version(self) -> str:
        """Alias for schema_version."""
        return str(self.schema_version)

    @property
    def workflow_state(self) -> WorkflowState:
        """Alias for state."""
        return self.state


WorkflowCheckpoint = Checkpoint

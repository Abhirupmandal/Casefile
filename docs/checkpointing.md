# CASEFILE Checkpointing Architecture

## Overview

Checkpointing is a core capability of CASEFILE that enables:
- Workflow resumability after interruption
- Deterministic replay for debugging and audit
- State recovery from failures
- Time-travel debugging

This document defines when checkpoints are created, what they contain, and how they are managed.

## Design Principles

### 1. Consistency Over Performance

Checkpoints must represent a consistent, recoverable state. We accept a small performance cost to ensure correctness.

### 2. Explicit Checkpoint Triggers

Checkpoints are created at well-defined points in the workflow, not randomly. Every trigger has a documented reason.

### 3. Complete State Capture

A checkpoint contains everything needed to resume execution from that point, including recorded tool outputs for determinism.

### 4. Version Compatibility

Checkpoints are versioned to support application evolution without breaking stored snapshots.

## Checkpoint Lifecycle

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        CHECKPOINT LIFECYCLE                              │
│                                                                          │
│  ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐          │
│  │ CREATED  │───►│  ACTIVE  │───►│ ARCHIVED │───►│ DELETED  │          │
│  └──────────┘    └──────────┘    └──────────┘    └──────────┘          │
│       │               │               │               │                 │
│       │               │               │               │                 │
│       ▼               ▼               ▼               ▼                 │
│   State          Workflow         Retention      Cleanup                │
│   transition     completed        period         job                    │
│   completed      or failed        expired                               │
│                                                                          │
└─────────────────────────────────────────────────────────────────────────┘
```

## Checkpoint Triggers

### Mandatory Checkpoints

| Trigger Point | Checkpoint Type | Reason | Required Data |
|---------------|-----------------|--------|---------------|
| After RECEIVED validation | BEFORE_NODE | Enable restart before first agent | Initial state |
| After successful EXTRACTION | AFTER_NODE | Recovery from investigation failure | Extraction result |
| After successful INVESTIGATION | AFTER_NODE | Recovery from review failure | Investigation result |
| After successful REVIEW | AFTER_NODE | Recovery from approval failure | Review result |
| Before HUMAN_APPROVAL | BEFORE_HUMAN_APPROVAL | Recovery from timeout/rejection | Approval request |
| After HUMAN_APPROVAL | AFTER_HUMAN_APPROVAL | Record final decision | Approval decision |
| Before any error state | ON_ERROR | Enable investigation of failure | Error context |

### Optional Checkpoints

| Trigger Point | Checkpoint Type | Reason | Conditions |
|---------------|-----------------|--------|------------|
| Before any state transition | BEFORE_TRANSITION | Fine-grained recovery | Configurable |
| After any state transition | AFTER_TRANSITION | Complete audit trail | Configurable |
| Manual trigger | MANUAL | Operator-initiated | Admin action |

### Checkpoint Trigger Implementation

```python
from enum import Enum
from typing import List, Callable
from dataclasses import dataclass


class CheckpointTrigger:
    """
    Defines when checkpoints should be created.
    """

    @staticmethod
    def should_checkpoint(
        current_state: WorkflowState,
        next_state: Optional[WorkflowState],
        event: str,
        config: CheckpointConfig
    ) -> Optional[CheckpointType]:
        """
        Determines if a checkpoint should be created based on the current context.

        Returns the checkpoint type if should create, None otherwise.
        """

        # Mandatory checkpoints (always create)
        if event == "node_completed":
            if current_state == WorkflowState.EXTRACTION:
                return CheckpointType.AFTER_NODE
            if current_state == WorkflowState.INVESTIGATION:
                return CheckpointType.AFTER_NODE
            if current_state == WorkflowState.REVIEW:
                return CheckpointType.AFTER_NODE

        if event == "before_human_approval":
            return CheckpointType.BEFORE_HUMAN_APPROVAL

        if event == "after_human_approval":
            return CheckpointType.AFTER_HUMAN_APPROVAL

        if event == "error_occurred":
            return CheckpointType.ON_ERROR

        # Optional checkpoints (based on config)
        if config.checkpoint_before_transitions and event == "before_transition":
            return CheckpointType.BEFORE_TRANSITION

        if config.checkpoint_after_transitions and event == "after_transition":
            return CheckpointType.AFTER_TRANSITION

        return None


@dataclass
class CheckpointConfig:
    """
    Configuration for checkpoint behavior.
    """

    # Mandatory checkpoints (cannot be disabled)
    checkpoint_after_nodes: bool = True
    checkpoint_before_human_approval: bool = True
    checkpoint_after_human_approval: bool = True
    checkpoint_on_error: bool = True

    # Optional checkpoints
    checkpoint_before_transitions: bool = False
    checkpoint_after_transitions: bool = False

    # Retention
    max_checkpoints_per_workflow: int = 100
    checkpoint_retention_days: int = 90

    # Performance
    compress_checkpoints: bool = True
    async_checkpoint_write: bool = True
```

## Checkpoint Data Model

### Complete Checkpoint Structure

```python
from datetime import datetime
from decimal import Decimal
from typing import Dict, Any, Optional, List
from uuid import UUID
from pydantic import BaseModel, Field


class Checkpoint(BaseModel):
    """
    Complete checkpoint representing recoverable workflow state.
    """

    # Identification
    checkpoint_id: UUID = Field(default_factory=uuid4)
    workflow_run_id: UUID
    claim_id: UUID

    # State snapshot
    state: WorkflowState
    state_data: Dict[str, Any] = Field(
        ...,
        description="Complete serialized state for this workflow state"
    )

    # Execution context
    step_count: int
    rework_count: int = 0
    budget_state: BudgetState

    # Recorded outputs for deterministic replay
    recorded_tool_outputs: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Map of tool call ID to output for replay"
    )

    # Timestamp
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Versioning
    schema_version: str = "1.0.0"
    application_version: str

    # Metadata
    checkpoint_type: CheckpointType
    parent_checkpoint_id: Optional[UUID] = None

    # Integrity
    checksum: Optional[str] = None

    def compute_checksum(self) -> str:
        """
        Compute checksum for integrity verification.
        """
        import hashlib
        import json

        data = json.dumps(self.model_dump(), default=str, sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()

    def verify_integrity(self) -> bool:
        """
        Verify checkpoint integrity using checksum.
        """
        if not self.checksum:
            return True  # No checksum to verify

        computed = self.compute_checksum()
        return computed == self.checksum


class CheckpointBlob(BaseModel):
    """
    Large checkpoint data stored separately.
    """

    checkpoint_id: UUID
    compressed_data: bytes
    compression_algorithm: str = "gzip"
    uncompressed_size: int
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

### State-Specific Checkpoint Data

```python
class ReceivedCheckpointData(BaseModel):
    """Checkpoint data for RECEIVED state."""
    claim_input: ClaimInput
    workflow_run: WorkflowRun


class ExtractionCheckpointData(BaseModel):
    """Checkpoint data for EXTRACTION state."""
    claim_input: ClaimInput
    extraction_request: ExtractionRequest
    extraction_result: Optional[ExtractionResult]


class InvestigationCheckpointData(BaseModel):
    """Checkpoint data for INVESTIGATION state."""
    claim_input: ClaimInput
    extraction_result: ExtractionResult
    investigation_request: InvestigationRequest
    investigation_result: Optional[InvestigationResult]


class ReviewCheckpointData(BaseModel):
    """Checkpoint data for REVIEW state."""
    claim_input: ClaimInput
    extraction_result: ExtractionResult
    investigation_result: InvestigationResult
    review_request: ReviewRequest
    review_result: Optional[ReviewResult]


class HumanApprovalCheckpointData(BaseModel):
    """Checkpoint data for HUMAN_APPROVAL state."""
    claim_input: ClaimInput
    extraction_result: ExtractionResult
    investigation_result: InvestigationResult
    review_result: ReviewResult
    recommendation: ClaimRecommendation
    approval_request: HumanApprovalRequest


class TerminalCheckpointData(BaseModel):
    """Checkpoint data for terminal states."""
    claim_input: ClaimInput
    final_state: WorkflowState
    termination_reason: str
    all_results: Dict[str, Any]
```

## Checkpoint Manager

### Core Interface

```python
from abc import ABC, abstractmethod
from typing import Optional


class CheckpointManager(ABC):
    """
    Manages checkpoint lifecycle.
    """

    @abstractmethod
    async def create_checkpoint(
        self,
        workflow_run_id: UUID,
        state: WorkflowState,
        state_data: Dict[str, Any],
        checkpoint_type: CheckpointType,
        budget_state: BudgetState,
        recorded_tool_outputs: Optional[Dict[str, Dict[str, Any]]] = None
    ) -> Checkpoint:
        """
        Create and persist a checkpoint.
        """
        ...

    @abstractmethod
    async def load_checkpoint(
        self,
        checkpoint_id: UUID
    ) -> Checkpoint:
        """
        Load a checkpoint from storage.
        """
        ...

    @abstractmethod
    async def get_latest_checkpoint(
        self,
        workflow_run_id: UUID
    ) -> Optional[Checkpoint]:
        """
        Get the most recent checkpoint for a workflow.
        """
        ...

    @abstractmethod
    async def get_checkpoint_for_state(
        self,
        workflow_run_id: UUID,
        state: WorkflowState
    ) -> Optional[Checkpoint]:
        """
        Get the checkpoint for a specific state.
        """
        ...

    @abstractmethod
    async def delete_checkpoint(
        self,
        checkpoint_id: UUID
    ) -> bool:
        """
        Delete a checkpoint.
        """
        ...

    @abstractmethod
    async def cleanup_old_checkpoints(
        self,
        workflow_run_id: UUID,
        keep_last_n: int = 10
    ) -> int:
        """
        Remove old checkpoints, keeping only the last N.
        """
        ...
```

### Implementation

```python
import gzip
import json
from datetime import datetime, timedelta


class PostgresCheckpointManager(CheckpointManager):
    """
    PostgreSQL-backed checkpoint manager with Redis cache.
    """

    def __init__(
        self,
        database: Database,
        redis: Redis,
        config: CheckpointConfig
    ):
        self.db = database
        self.redis = redis
        self.config = config
        self.application_version = "1.0.0"  # From settings

    async def create_checkpoint(
        self,
        workflow_run_id: UUID,
        state: WorkflowState,
        state_data: Dict[str, Any],
        checkpoint_type: CheckpointType,
        budget_state: BudgetState,
        recorded_tool_outputs: Optional[Dict[str, Dict[str, Any]]] = None
    ) -> Checkpoint:
        """
        Create and persist a checkpoint.
        """

        # Create checkpoint object
        checkpoint = Checkpoint(
            workflow_run_id=workflow_run_id,
            claim_id=state_data.get("claim_id"),
            state=state,
            state_data=state_data,
            step_count=budget_state.steps_completed,
            rework_count=state_data.get("rework_count", 0),
            budget_state=budget_state,
            recorded_tool_outputs=recorded_tool_outputs or {},
            application_version=self.application_version,
            checkpoint_type=checkpoint_type
        )

        # Compute checksum
        checkpoint.checksum = checkpoint.compute_checksum()

        # Serialize for storage
        serialized = checkpoint.model_dump_json()

        # Compress if enabled
        if self.config.compress_checkpoints:
            compressed = gzip.compress(serialized.encode())
        else:
            compressed = serialized.encode()

        # Store in PostgreSQL
        async with self.db.transaction() as tx:
            # Insert checkpoint metadata
            await tx.execute(
                """
                INSERT INTO checkpoints
                (checkpoint_id, workflow_run_id, state, state_data, step_count,
                 rework_count, budget_state, recorded_tool_outputs, checkpoint_type,
                 schema_version, application_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                """,
                checkpoint.checkpoint_id,
                checkpoint.workflow_run_id,
                checkpoint.state,
                checkpoint.state_data,
                checkpoint.step_count,
                checkpoint.rework_count,
                checkpoint.budget_state.model_dump(),
                checkpoint.recorded_tool_outputs,
                checkpoint.checkpoint_type,
                checkpoint.schema_version,
                checkpoint.application_version
            )

            # Store blob if large
            if len(compressed) > 1024 * 1024:  # > 1MB
                await tx.execute(
                    """
                    INSERT INTO checkpoint_blobs
                    (checkpoint_id, compressed_data, compression_algorithm, uncompressed_size)
                    VALUES ($1, $2, $3, $4)
                    """,
                    checkpoint.checkpoint_id,
                    compressed,
                    "gzip" if self.config.compress_checkpoints else "none",
                    len(serialized)
                )

        # Cache in Redis
        await self._cache_checkpoint(checkpoint)

        # Update workflow run
        await self.db.execute(
            """
            UPDATE workflow_runs
            SET last_checkpoint_id = $1, updated_at = NOW()
            WHERE workflow_run_id = $2
            """,
            checkpoint.checkpoint_id,
            workflow_run_id
        )

        # Record audit event
        await self._record_audit_event(checkpoint)

        # Cleanup old checkpoints
        await self.cleanup_old_checkpoints(
            workflow_run_id,
            self.config.max_checkpoints_per_workflow
        )

        return checkpoint

    async def load_checkpoint(
        self,
        checkpoint_id: UUID
    ) -> Checkpoint:
        """
        Load a checkpoint from storage.
        """

        # Check Redis cache first
        cached = await self._get_cached_checkpoint(checkpoint_id)
        if cached:
            return cached

        # Load from PostgreSQL
        row = await self.db.fetch_one(
            """
            SELECT * FROM checkpoints WHERE checkpoint_id = $1
            """,
            checkpoint_id
        )

        if not row:
            raise CheckpointNotFoundError(f"Checkpoint {checkpoint_id} not found")

        # Check for blob storage
        blob = await self.db.fetch_one(
            """
            SELECT compressed_data, compression_algorithm, uncompressed_size
            FROM checkpoint_blobs
            WHERE checkpoint_id = $1
            """,
            checkpoint_id
        )

        if blob:
            # Decompress
            if blob["compression_algorithm"] == "gzip":
                state_data_str = gzip.decompress(blob["compressed_data"]).decode()
            else:
                state_data_str = blob["compressed_data"].decode()
        else:
            state_data_str = json.dumps(row["state_data"])

        # Reconstruct checkpoint
        checkpoint = Checkpoint.model_validate_json(state_data_str)

        # Verify integrity
        if not checkpoint.verify_integrity():
            raise CheckpointIntegrityError(
                f"Checkpoint {checkpoint_id} failed integrity check"
            )

        # Cache for future access
        await self._cache_checkpoint(checkpoint)

        return checkpoint

    async def get_latest_checkpoint(
        self,
        workflow_run_id: UUID
    ) -> Optional[Checkpoint]:
        """
        Get the most recent checkpoint for a workflow.
        """

        row = await self.db.fetch_one(
            """
            SELECT checkpoint_id FROM checkpoints
            WHERE workflow_run_id = $1
            ORDER BY created_at DESC
            LIMIT 1
            """,
            workflow_run_id
        )

        if not row:
            return None

        return await self.load_checkpoint(row["checkpoint_id"])

    async def get_checkpoint_for_state(
        self,
        workflow_run_id: UUID,
        state: WorkflowState
    ) -> Optional[Checkpoint]:
        """
        Get the checkpoint for a specific state.
        """

        row = await self.db.fetch_one(
            """
            SELECT checkpoint_id FROM checkpoints
            WHERE workflow_run_id = $1 AND state = $2
            ORDER BY created_at DESC
            LIMIT 1
            """,
            workflow_run_id,
            state
        )
        if not row:
            return None

        return await self.load_checkpoint(row["checkpoint_id"])

    async def delete_checkpoint(
        self,
        checkpoint_id: UUID
    ) -> bool:
        """
        Delete a checkpoint.
        """

        result = await self.db.execute(
            """
            DELETE FROM checkpoints WHERE checkpoint_id = $1
            """,
            checkpoint_id
        )

        # Also remove from cache
        await self.redis.delete(f"checkpoint:{checkpoint_id}")

        return result > 0

    async def cleanup_old_checkpoints(
        self,
        workflow_run_id: UUID,
        keep_last_n: int = 10
    ) -> int:
        """
        Remove old checkpoints, keeping only the last N.
        """

        # Get checkpoint IDs to keep
        rows = await self.db.fetch_all(
            """
            SELECT checkpoint_id FROM checkpoints
            WHERE workflow_run_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            workflow_run_id,
            keep_last_n
        )

        keep_ids = [row["checkpoint_id"] for row in rows]

        # Delete others
        if keep_ids:
            result = await self.db.execute(
                """
                DELETE FROM checkpoints
                WHERE workflow_run_id = $1
                AND checkpoint_id NOT IN (SELECT unnest($2::uuid[]))
                """,
                workflow_run_id,
                keep_ids
            )
        else:
            result = 0

        return result

    async def _cache_checkpoint(self, checkpoint: Checkpoint) -> None:
        """Cache checkpoint in Redis."""
        key = f"checkpoint:{checkpoint.checkpoint_id}"
        await self.redis.setex(
            key,
            3600,  # 1 hour TTL
            checkpoint.model_dump_json()
        )

    async def _get_cached_checkpoint(
        self,
        checkpoint_id: UUID
    ) -> Optional[Checkpoint]:
        """Get checkpoint from Redis cache."""
        key = f"checkpoint:{checkpoint_id}"
        data = await self.redis.get(key)

        if data:
            return Checkpoint.model_validate_json(data)
        return None

    async def _record_audit_event(self, checkpoint: Checkpoint) -> None:
        """Record checkpoint creation in audit log."""
        event = AuditEvent(
            workflow_run_id=checkpoint.workflow_run_id,
            claim_id=checkpoint.claim_id,
            trace_id=str(checkpoint.workflow_run_id),  # Simplified
            event_type=AuditEventType.CHECKPOINT_CREATED,
            actor_type="SYSTEM",
            actor_id="checkpoint_manager",
            actor_name="Checkpoint Manager",
            action=f"Created checkpoint at state {checkpoint.state}",
            details={
                "checkpoint_id": str(checkpoint.checkpoint_id),
                "checkpoint_type": checkpoint.checkpoint_type,
                "step_count": checkpoint.step_count
            },
            result="SUCCESS"
        )

        await self.audit_repository.append(event)
```

## Checkpoint Integration Points

### Supervisor Integration

```python
class Supervisor:
    """
    Supervisor that coordinates checkpoint creation.
    """

    def __init__(
        self,
        checkpoint_manager: CheckpointManager,
        checkpoint_config: CheckpointConfig
    ):
        self.checkpoint_manager = checkpoint_manager
        self.checkpoint_config = checkpoint_config

    async def execute_node(
        self,
        agent: SpecialistAgent,
        request: AgentRequest,
        context: ExecutionContext
    ) -> AgentResult:
        """
        Execute an agent node with checkpointing.
        """

        # Record tool outputs for determinism
        tool_output_recorder = ToolOutputRecorder()

        try:
            # Execute node
            result = await agent.execute(request)

            # Determine if checkpoint needed
            checkpoint_type = CheckpointTrigger.should_checkpoint(
                current_state=context.current_state,
                next_state=None,  # Determined after execution
                event="node_completed",
                config=self.checkpoint_config
            )

            if checkpoint_type:
                await self.checkpoint_manager.create_checkpoint(
                    workflow_run_id=context.workflow_run_id,
                    state=context.current_state,
                    state_data=context.state_data,
                    checkpoint_type=checkpoint_type,
                    budget_state=context.budget_state,
                    recorded_tool_outputs=tool_output_recorder.get_recorded_outputs()
                )

            return result

        except Exception as e:
            # Create error checkpoint
            await self.checkpoint_manager.create_checkpoint(
                workflow_run_id=context.workflow_run_id,
                state=context.current_state,
                state_data={
                    **context.state_data,
                    "error": str(e)
                },
                checkpoint_type=CheckpointType.ON_ERROR,
                budget_state=context.budget_state,
                recorded_tool_outputs=tool_output_recorder.get_recorded_outputs()
            )

            raise

    async def execute_transition(
        self,
        source: WorkflowState,
        destination: WorkflowState,
        context: ExecutionContext
    ) -> None:
        """
        Execute a state transition with checkpointing.
        """

        # Checkpoint before transition
        if self.checkpoint_config.checkpoint_before_transitions:
            await self.checkpoint_manager.create_checkpoint(
                workflow_run_id=context.workflow_run_id,
                state=source,
                state_data=context.state_data,
                checkpoint_type=CheckpointType.BEFORE_TRANSITION,
                budget_state=context.budget_state
            )

        # Execute transition
        await self._do_transition(source, destination, context)

        # Checkpoint after transition
        if self.checkpoint_config.checkpoint_after_transitions:
            await self.checkpoint_manager.create_checkpoint(
                workflow_run_id=context.workflow_run_id,
                state=destination,
                state_data=context.state_data,
                checkpoint_type=CheckpointType.AFTER_TRANSITION,
                budget_state=context.budget_state
            )
```

### Tool Output Recording

```python
class ToolOutputRecorder:
    """
    Records tool outputs for deterministic replay.
    """

    def __init__(self):
        self.recorded_outputs: Dict[str, Dict[str, Any]] = {}

    def record(
        self,
        tool_name: str,
        call_id: UUID,
        output: Dict[str, Any]
    ) -> None:
        """
        Record a tool output.
        """
        key = f"{tool_name}:{call_id}"
        self.recorded_outputs[key] = {
            "tool_name": tool_name,
            "call_id": str(call_id),
            "output": output,
            "recorded_at": datetime.utcnow().isoformat()
        }

    def get_recorded_outputs(self) -> Dict[str, Dict[str, Any]]:
        """
        Get all recorded outputs.
        """
        return self.recorded_outputs.copy()
```

## Checkpoint Validation

### Integrity Verification

```python
async def verify_checkpoint_integrity(
    checkpoint: Checkpoint
) -> IntegrityVerificationResult:
    """
    Verify checkpoint integrity.
    """

    errors = []
    warnings = []

    # 1. Checksum verification
    if not checkpoint.verify_integrity():
        errors.append("Checksum verification failed")

    # 2. Schema version compatibility
    if not is_schema_compatible(checkpoint.schema_version):
        errors.append(
            f"Incompatible schema version: {checkpoint.schema_version}"
        )

    # 3. Required fields check
    required_fields = get_required_fields_for_state(checkpoint.state)
    missing = [f for f in required_fields if f not in checkpoint.state_data]
    if missing:
        errors.append(f"Missing required fields: {missing}")

    # 4. Budget state consistency
    if checkpoint.budget_state.is_exhausted:
        warnings.append("Budget exhausted at checkpoint time")

    # 5. Tool output completeness
    if checkpoint.state in [WorkflowState.INVESTIGATION]:
        if not checkpoint.recorded_tool_outputs:
            warnings.append("No tool outputs recorded for investigation state")

    return IntegrityVerificationResult(
        is_valid=len(errors) == 0,
        errors=errors,
        warnings=warnings
    )
```

### Schema Compatibility

```python
def is_schema_compatible(checkpoint_version: str) -> bool:
    """
    Check if checkpoint schema version is compatible with current version.
    """

    current = parse_version("1.0.0")  # Current application version
    checkpoint = parse_version(checkpoint_version)

    # Major version must match
    if current[0] != checkpoint[0]:
        return False

    # Minor version can be older or same
    if current[1] < checkpoint[1]:
        return False

    return True


def parse_version(version: str) -> tuple:
    """Parse semantic version string."""
    return tuple(map(int, version.split('.')))
```

## Recovery from Checkpoint

### Recovery Process

```python
async def recover_from_checkpoint(
    checkpoint_id: UUID,
    recovery_mode: RecoveryMode = RecoveryMode.CONTINUE
) -> WorkflowRun:
    """
    Recover workflow execution from a checkpoint.
    """

    # 1. Load checkpoint
    checkpoint = await checkpoint_manager.load_checkpoint(checkpoint_id)

    # 2. Verify integrity
    verification = await verify_checkpoint_integrity(checkpoint)
    if not verification.is_valid:
        raise CheckpointRecoveryError(
            f"Checkpoint integrity check failed: {verification.errors}"
        )

    # 3. Reconstruct execution context
    context = ExecutionContext(
        workflow_run_id=checkpoint.workflow_run_id,
        claim_id=checkpoint.claim_id,
        current_state=checkpoint.state,
        state_data=checkpoint.state_data,
        step_count=checkpoint.step_count,
        rework_count=checkpoint.rework_count,
        budget_state=checkpoint.budget_state,
        recorded_tool_outputs=checkpoint.recorded_tool_outputs
    )

    # 4. Update workflow run record
    await database.execute(
        """
        UPDATE workflow_runs
        SET current_state = $1,
            state_data = $2,
            step_count = $3,
            rework_count = $4,
            updated_at = NOW()
        WHERE workflow_run_id = $5
        """,
        checkpoint.state,
        checkpoint.state_data,
        checkpoint.step_count,
        checkpoint.rework_count,
        checkpoint.workflow_run_id
    )

    # 5. Resume execution based on recovery mode
    if recovery_mode == RecoveryMode.CONTINUE:
        # Continue from current state
        return await supervisor.resume(context)

    elif recovery_mode == RecoveryMode.REPLAY:
        # Replay from checkpoint (see replay.md)
        return await replayer.replay_from_checkpoint(checkpoint)

    elif recovery_mode == RecoveryMode.MANUAL:
        # Return context for manual inspection
        return context


class RecoveryMode(str, Enum):
    CONTINUE = "CONTINUE"  # Continue normal execution
    REPLAY = "REPLAY"     # Deterministic replay
    MANUAL = "MANUAL"     # Return for manual handling
```

## Checkpoint Storage Optimization

### Compression Strategy

```python
async def compress_checkpoint_data(
    data: Dict[str, Any],
    algorithm: str = "gzip"
) -> bytes:
    """
    Compress checkpoint data.
    """

    serialized = json.dumps(data, default=str)

    if algorithm == "gzip":
        return gzip.compress(serialized.encode(), compresslevel=6)
    elif algorithm == "none":
        return serialized.encode()
    else:
        raise ValueError(f"Unknown compression algorithm: {algorithm}")


async def decompress_checkpoint_data(
    compressed: bytes,
    algorithm: str = "gzip"
) -> Dict[str, Any]:
    """
    Decompress checkpoint data.
    """

    if algorithm == "gzip":
        decompressed = gzip.decompress(compressed).decode()
    elif algorithm == "none":
        decompressed = compressed.decode()
    else:
        raise ValueError(f"Unknown compression algorithm: {algorithm}")

    return json.loads(decompressed)
```

### Selective Storage

```python
async def optimize_checkpoint_storage(
    checkpoint: Checkpoint
) -> Checkpoint:
    """
    Optimize checkpoint storage by removing redundant data.
    """

    # For AFTER_NODE checkpoints, we can store deltas instead of full state
    if checkpoint.checkpoint_type == CheckpointType.AFTER_NODE:
        # Get previous checkpoint
        previous = await checkpoint_manager.get_previous_checkpoint(
            checkpoint.workflow_run_id,
            checkpoint.checkpoint_id
        )

        if previous:
            # Store only changed fields
            delta = compute_delta(previous.state_data, checkpoint.state_data)
            checkpoint.state_data = {
                "__delta__": True,
                "__base_checkpoint_id__": str(previous.checkpoint_id),
                "changes": delta
            }

    return checkpoint
```

## Summary

The checkpointing architecture provides:
- **Recovery**: Resume workflow after interruption
- **Consistency**: Verified, versioned state snapshots
- **Determinism**: Recorded tool outputs for replay
- **Performance**: Compression and caching
- **Auditability**: Immutable record of workflow progression
- **Flexibility**: Multiple checkpoint triggers and recovery modes

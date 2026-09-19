# CASEFILE Persistence Architecture

## Overview

CASEFILE requires durable persistence to support:
- Workflow resumability after interruption
- Complete audit trail for compliance
- Checkpoint storage for replay
- Historical analysis and reporting
- Human approval workflow

This document defines the persistence architecture, data models, and access patterns.

## Design Principles

### 1. Durability Over Performance

Data integrity is paramount. We prefer correct, durable writes over fast, potentially inconsistent ones.

### 2. Event Sourcing for Audit

Audit events are immutable and append-only. They represent the authoritative record of what happened.

### 3. State Snapshotting

Current workflow state is stored as a snapshot for quick recovery, with events providing the full history.

### 4. Versioned State

State schemas are versioned to support application evolution without breaking stored checkpoints.

## Database Architecture

### Primary Database: PostgreSQL

PostgreSQL serves as the authoritative data store for:
- Claims and documents
- Workflow runs and states
- Agent execution records
- Tool execution records
- Audit events
- Checkpoints

### Cache Layer: Redis

Redis provides:
- Active workflow state cache
- Checkpoint serialization cache
- Distributed locks for idempotency
- Rate limiting counters

### Data Flow

```
┌─────────────┐
│   Agent     │
│ Execution   │
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────────────────────┐
│               PERSISTENCE LAYER                      │
│                                                      │
│  ┌────────────────┐         ┌──────────────────┐   │
│  │  Write Cache   │         │  Read Cache      │   │
│  │   (Redis)      │         │   (Redis)        │   │
│  └───────┬────────┘         └────────┬─────────┘   │
│          │                           │              │
│          ▼                           │              │
│  ┌─────────────────────────────────────────────┐   │
│  │              POSTGRESQL                      │   │
│  │                                              │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐    │   │
│  │  │ claims   │ │workflows │ │ audits   │    │   │
│  │  └──────────┘ └──────────┘ └──────────┘    │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐    │   │
│  │  │checkpoints│ │tool_calls│ │budgets   │    │   │
│  │  └──────────┘ └──────────┘ └──────────┘    │   │
│  └─────────────────────────────────────────────┘   │
│                                                      │
└─────────────────────────────────────────────────────┘
```

## Entity Models

### Core Tables

```sql
-- Claims
CREATE TABLE claims (
    claim_id UUID PRIMARY KEY,
    external_claim_id VARCHAR(100) NOT NULL UNIQUE,
    policy_number VARCHAR(50) NOT NULL,
    customer_id VARCHAR(100) NOT NULL,
    claim_type VARCHAR(50) NOT NULL,
    date_of_loss DATE NOT NULL,
    date_reported DATE NOT NULL,
    description TEXT NOT NULL,
    priority VARCHAR(20) NOT NULL DEFAULT 'NORMAL',
    status VARCHAR(50) NOT NULL DEFAULT 'RECEIVED',
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_policy FOREIGN KEY (policy_number) REFERENCES policies(policy_number)
);

CREATE INDEX idx_claims_policy ON claims(policy_number);
CREATE INDEX idx_claims_customer ON claims(customer_id);
CREATE INDEX idx_claims_status ON claims(status);
CREATE INDEX idx_claims_date_of_loss ON claims(date_of_loss);

-- Documents
CREATE TABLE documents (
    document_id UUID PRIMARY KEY,
    claim_id UUID NOT NULL,
    document_type VARCHAR(50) NOT NULL,
    content_type VARCHAR(50) NOT NULL,
    content TEXT NOT NULL,  -- Base64 encoded for binary
    filename VARCHAR(255),
    file_size_bytes INTEGER,
    page_count INTEGER,
    source VARCHAR(100) NOT NULL,
    processing_status VARCHAR(50) NOT NULL DEFAULT 'PENDING',
    received_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMP WITH TIME ZONE,

    CONSTRAINT fk_document_claim FOREIGN KEY (claim_id) REFERENCES claims(claim_id) ON DELETE CASCADE
);

CREATE INDEX idx_documents_claim ON documents(claim_id);
CREATE INDEX idx_documents_type ON documents(document_type);

-- Workflow Runs
CREATE TABLE workflow_runs (
    workflow_run_id UUID PRIMARY KEY,
    claim_id UUID NOT NULL,
    current_state VARCHAR(50) NOT NULL,
    state_data JSONB NOT NULL,
    step_count INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE,
    terminal_state VARCHAR(50),
    termination_reason TEXT,
    rework_count INTEGER NOT NULL DEFAULT 0,
    last_checkpoint_id UUID,
    trace_id VARCHAR(100) NOT NULL,

    CONSTRAINT fk_workflow_claim FOREIGN KEY (claim_id) REFERENCES claims(claim_id) ON DELETE CASCADE
);

CREATE INDEX idx_workflow_runs_claim ON workflow_runs(claim_id);
CREATE INDEX idx_workflow_runs_state ON workflow_runs(current_state);
CREATE INDEX idx_workflow_runs_trace ON workflow_runs(trace_id);

-- Budget State
CREATE TABLE budget_states (
    budget_state_id UUID PRIMARY KEY,
    workflow_run_id UUID NOT NULL UNIQUE,
    input_tokens_used BIGINT NOT NULL DEFAULT 0,
    output_tokens_used BIGINT NOT NULL DEFAULT 0,
    total_tokens_used BIGINT NOT NULL DEFAULT 0,
    estimated_cost_usd DECIMAL(10, 4) NOT NULL DEFAULT 0,
    steps_completed INTEGER NOT NULL DEFAULT 0,
    elapsed_time_seconds INTEGER NOT NULL DEFAULT 0,
    tool_calls_made INTEGER NOT NULL DEFAULT 0,
    is_exhausted BOOLEAN NOT NULL DEFAULT FALSE,
    exhaustion_reason VARCHAR(100),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_budget_workflow FOREIGN KEY (workflow_run_id) REFERENCES workflow_runs(workflow_run_id) ON DELETE CASCADE
);

CREATE INDEX idx_budget_workflow ON budget_states(workflow_run_id);
```

### Execution Records

```sql
-- Agent Execution Records
CREATE TABLE agent_executions (
    execution_id UUID PRIMARY KEY,
    workflow_run_id UUID NOT NULL,
    agent_name VARCHAR(50) NOT NULL,
    request_id UUID NOT NULL,
    result_id UUID,
    status VARCHAR(50) NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    completed_at TIMESTAMP WITH TIME ZONE,
    execution_time_ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    estimated_cost_usd DECIMAL(10, 4),
    error_type VARCHAR(100),
    error_message TEXT,

    CONSTRAINT fk_agent_workflow FOREIGN KEY (workflow_run_id) REFERENCES workflow_runs(workflow_run_id) ON DELETE CASCADE
);

CREATE INDEX idx_agent_executions_workflow ON agent_executions(workflow_run_id);
CREATE INDEX idx_agent_executions_agent ON agent_executions(agent_name);

-- Tool Call Records
CREATE TABLE tool_call_records (
    call_id UUID PRIMARY KEY,
    workflow_run_id UUID NOT NULL,
    agent_name VARCHAR(50) NOT NULL,
    tool_name VARCHAR(100) NOT NULL,
    tool_version VARCHAR(20) NOT NULL DEFAULT '1.0.0',
    input JSONB NOT NULL,
    output JSONB,
    status VARCHAR(50) NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    completed_at TIMESTAMP WITH TIME ZONE,
    execution_time_ms INTEGER,
    retry_count INTEGER NOT NULL DEFAULT 0,
    error_type VARCHAR(100),
    error_message TEXT,
    authorized BOOLEAN NOT NULL DEFAULT TRUE,

    CONSTRAINT fk_tool_workflow FOREIGN KEY (workflow_run_id) REFERENCES workflow_runs(workflow_run_id) ON DELETE CASCADE
);

CREATE INDEX idx_tool_calls_workflow ON tool_call_records(workflow_run_id);
CREATE INDEX idx_tool_calls_tool ON tool_call_records(tool_name);

-- State Transitions
CREATE TABLE state_transitions (
    transition_id UUID PRIMARY KEY,
    workflow_run_id UUID NOT NULL,
    from_state VARCHAR(50),
    to_state VARCHAR(50) NOT NULL,
    trigger VARCHAR(100) NOT NULL,
    reason TEXT,
    step_number INTEGER NOT NULL,
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    checkpoint_created BOOLEAN NOT NULL DEFAULT FALSE,
    checkpoint_id UUID,

    CONSTRAINT fk_transition_workflow FOREIGN KEY (workflow_run_id) REFERENCES workflow_runs(workflow_run_id) ON DELETE CASCADE
);

CREATE INDEX idx_transitions_workflow ON state_transitions(workflow_run_id);
CREATE INDEX idx_transitions_to_state ON state_transitions(to_state);
```

### Checkpoints

```sql
-- Checkpoints
CREATE TABLE checkpoints (
    checkpoint_id UUID PRIMARY KEY,
    workflow_run_id UUID NOT NULL,
    state VARCHAR(50) NOT NULL,
    state_data JSONB NOT NULL,
    step_count INTEGER NOT NULL,
    rework_count INTEGER NOT NULL DEFAULT 0,
    budget_state JSONB NOT NULL,
    recorded_tool_outputs JSONB NOT NULL DEFAULT '{}',
    checkpoint_type VARCHAR(50) NOT NULL,
    parent_checkpoint_id UUID,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    schema_version VARCHAR(20) NOT NULL DEFAULT '1.0.0',
    application_version VARCHAR(50) NOT NULL,

    CONSTRAINT fk_checkpoint_workflow FOREIGN KEY (workflow_run_id) REFERENCES workflow_runs(workflow_run_id) ON DELETE CASCADE,
    CONSTRAINT fk_checkpoint_parent FOREIGN KEY (parent_checkpoint_id) REFERENCES checkpoints(checkpoint_id)
);

CREATE INDEX idx_checkpoints_workflow ON checkpoints(workflow_run_id);
CREATE INDEX idx_checkpoints_state ON checkpoints(state);
CREATE INDEX idx_checkpoints_type ON checkpoints(checkpoint_type);

-- Checkpoint BLOB Storage (for large state)
CREATE TABLE checkpoint_blobs (
    checkpoint_id UUID PRIMARY KEY,
    compressed_data BYTEA NOT NULL,
    compression_algorithm VARCHAR(20) NOT NULL DEFAULT 'gzip',
    uncompressed_size BIGINT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),

    CONSTRAINT fk_blob_checkpoint FOREIGN KEY (checkpoint_id) REFERENCES checkpoints(checkpoint_id) ON DELETE CASCADE
);
```

### Audit Trail

```sql
-- Audit Events (Append-Only)
CREATE TABLE audit_events (
    event_id UUID PRIMARY KEY,
    workflow_run_id UUID NOT NULL,
    claim_id UUID NOT NULL,
    trace_id VARCHAR(100) NOT NULL,
    event_type VARCHAR(100) NOT NULL,
    timestamp TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    actor_type VARCHAR(50) NOT NULL,
    actor_id VARCHAR(100) NOT NULL,
    actor_name VARCHAR(100) NOT NULL,
    action VARCHAR(200) NOT NULL,
    details JSONB NOT NULL DEFAULT '{}',
    result VARCHAR(50) NOT NULL,
    error TEXT,
    from_state VARCHAR(50),
    to_state VARCHAR(50),
    input_tokens BIGINT,
    output_tokens BIGINT,
    cost_usd DECIMAL(10, 6),
    schema_version VARCHAR(20) NOT NULL DEFAULT '1.0.0',

    -- No foreign key constraints to allow audit to persist even if related records are deleted
    -- This is intentional for compliance
);

-- Append-only trigger (prevent updates and deletes)
CREATE OR REPLACE FUNCTION prevent_audit_modification()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Audit events are immutable and cannot be modified';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_no_update
    BEFORE UPDATE ON audit_events
    FOR EACH ROW
    EXECUTE FUNCTION prevent_audit_modification();

CREATE TRIGGER audit_no_delete
    BEFORE DELETE ON audit_events
    FOR EACH ROW
    EXECUTE FUNCTION prevent_audit_modification();

-- Indexes for audit queries
CREATE INDEX idx_audit_workflow ON audit_events(workflow_run_id);
CREATE INDEX idx_audit_claim ON audit_events(claim_id);
CREATE INDEX idx_audit_type ON audit_events(event_type);
CREATE INDEX idx_audit_timestamp ON audit_events(timestamp);
CREATE INDEX idx_audit_actor ON audit_events(actor_type, actor_id);

-- Partitioning for large-scale deployments (future)
-- CREATE TABLE audit_events_2024_01 PARTITION OF audit_events
--     FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
```

### Results and Recommendations

```sql
-- Extraction Results
CREATE TABLE extraction_results (
    result_id UUID PRIMARY KEY,
    workflow_run_id UUID NOT NULL,
    claim_id UUID NOT NULL,
    status VARCHAR(50) NOT NULL,
    extracted_data JSONB,
    partial_data JSONB,
    documents_processed INTEGER NOT NULL,
    documents_failed JSONB NOT NULL DEFAULT '[]',
    confidence JSONB NOT NULL,
    execution_time_ms INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    estimated_cost_usd DECIMAL(10, 4) NOT NULL,
    error JSONB,
    completed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    schema_version VARCHAR(20) NOT NULL DEFAULT '1.0.0',

    CONSTRAINT fk_extraction_workflow FOREIGN KEY (workflow_run_id) REFERENCES workflow_runs(workflow_run_id) ON DELETE CASCADE
);

-- Investigation Results
CREATE TABLE investigation_results (
    result_id UUID PRIMARY KEY,
    workflow_run_id UUID NOT NULL,
    claim_id UUID NOT NULL,
    status VARCHAR(50) NOT NULL,
    policy_verification JSONB NOT NULL,
    claim_history JSONB NOT NULL,
    cost_analysis JSONB NOT NULL,
    fraud_assessment JSONB NOT NULL,
    investigation_summary TEXT NOT NULL,
    flags JSONB NOT NULL DEFAULT '[]',
    tool_calls JSONB NOT NULL DEFAULT '[]',
    execution_time_ms INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    estimated_cost_usd DECIMAL(10, 4) NOT NULL,
    partial_findings JSONB,
    error JSONB,
    completed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    schema_version VARCHAR(20) NOT NULL DEFAULT '1.0.0',

    CONSTRAINT fk_investigation_workflow FOREIGN KEY (workflow_run_id) REFERENCES workflow_runs(workflow_run_id) ON DELETE CASCADE
);

-- Review Results
CREATE TABLE review_results (
    result_id UUID PRIMARY KEY,
    workflow_run_id UUID NOT NULL,
    claim_id UUID NOT NULL,
    decision VARCHAR(50) NOT NULL,
    recommendation JSONB,
    rework_request JSONB,
    rejection_reason TEXT,
    rejection_details JSONB,
    evidence_summary TEXT NOT NULL,
    key_findings JSONB NOT NULL DEFAULT '[]',
    concerns JSONB NOT NULL DEFAULT '[]',
    execution_time_ms INTEGER NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    estimated_cost_usd DECIMAL(10, 4) NOT NULL,
    error JSONB,
    completed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    schema_version VARCHAR(20) NOT NULL DEFAULT '1.0.0',

    CONSTRAINT fk_review_workflow FOREIGN KEY (workflow_run_id) REFERENCES workflow_runs(workflow_run_id) ON DELETE CASCADE
);

-- Human Approvals
CREATE TABLE human_approvals (
    approval_id UUID PRIMARY KEY,
    workflow_run_id UUID NOT NULL,
    claim_id UUID NOT NULL,
    decision VARCHAR(50) NOT NULL,
    recommendation JSONB NOT NULL,
    approver_id VARCHAR(100) NOT NULL,
    approver_name VARCHAR(100) NOT NULL,
    approver_role VARCHAR(100) NOT NULL,
    requested_at TIMESTAMP WITH TIME ZONE NOT NULL,
    reviewed_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    timeout_at TIMESTAMP WITH TIME ZONE NOT NULL,
    notes TEXT,
    conditions JSONB NOT NULL DEFAULT '[]',
    is_override BOOLEAN NOT NULL DEFAULT FALSE,
    override_justification TEXT,

    CONSTRAINT fk_approval_workflow FOREIGN KEY (workflow_run_id) REFERENCES workflow_runs(workflow_run_id) ON DELETE CASCADE
);

CREATE INDEX idx_approvals_workflow ON human_approvals(workflow_run_id);
CREATE INDEX idx_approvals_approver ON human_approvals(approver_id);
```

## Repository Pattern

### Base Repository

```python
from abc import ABC, abstractmethod
from typing import TypeVar, Generic, Optional, List
from uuid import UUID
from datetime import datetime

T = TypeVar('T')


class BaseRepository(ABC, Generic[T]):
    """
    Base repository interface for all entities.
    """

    @abstractmethod
    async def get_by_id(self, id: UUID) -> Optional[T]:
        """Get entity by ID."""
        ...

    @abstractmethod
    async def save(self, entity: T) -> T:
        """Save entity (insert or update)."""
        ...

    @abstractmethod
    async def delete(self, id: UUID) -> bool:
        """Delete entity by ID."""
        ...

    @abstractmethod
    async def exists(self, id: UUID) -> bool:
        """Check if entity exists."""
        ...
```

### Workflow Run Repository

```python
class WorkflowRunRepository(BaseRepository[WorkflowRun]):
    """
    Repository for workflow run entities.
    """

    async def get_by_claim_id(self, claim_id: UUID) -> Optional[WorkflowRun]:
        """Get the most recent workflow run for a claim."""
        ...

    async def get_by_trace_id(self, trace_id: str) -> Optional[WorkflowRun]:
        """Get workflow run by trace ID."""
        ...

    async def get_active_by_claim(self, claim_id: UUID) -> List[WorkflowRun]:
        """Get all active (non-terminal) workflows for a claim."""
        ...

    async def update_state(
        self,
        workflow_run_id: UUID,
        new_state: WorkflowState,
        state_data: Dict[str, Any]
    ) -> WorkflowRun:
        """Update workflow state atomically."""
        ...

    async def increment_step(
        self,
        workflow_run_id: UUID
    ) -> int:
        """Increment step count and return new value."""
        ...

    async def increment_rework(
        self,
        workflow_run_id: UUID
    ) -> int:
        """Increment rework count and return new value."""
        ...

    async def mark_completed(
        self,
        workflow_run_id: UUID,
        terminal_state: WorkflowState,
        termination_reason: str
    ) -> WorkflowRun:
        """Mark workflow as completed."""
        ...
```

### Checkpoint Repository

```python
class CheckpointRepository(BaseRepository[Checkpoint]):
    """
    Repository for checkpoint entities.
    """

    async def get_latest_for_workflow(
        self,
        workflow_run_id: UUID
    ) -> Optional[Checkpoint]:
        """Get the most recent checkpoint for a workflow."""
        ...

    async def get_by_state(
        self,
        workflow_run_id: UUID,
        state: WorkflowState
    ) -> List[Checkpoint]:
        """Get all checkpoints for a specific state."""
        ...

    async def get_with_blob(
        self,
        checkpoint_id: UUID
    ) -> Checkpoint:
        """Get checkpoint with decompressed blob data."""
        ...

    async def save_with_blob(
        self,
        checkpoint: Checkpoint,
        blob_data: bytes
    ) -> Checkpoint:
        """Save checkpoint with compressed blob data."""
        ...

    async def cleanup_old_checkpoints(
        self,
        workflow_run_id: UUID,
        keep_last_n: int = 10
    ) -> int:
        """Remove old checkpoints, keeping only the last N."""
        ...
```

### Audit Event Repository

```python
class AuditEventRepository(BaseRepository[AuditEvent]):
    """
    Repository for audit events (append-only).
    """

    async def append(self, event: AuditEvent) -> AuditEvent:
        """Append a new audit event (only insertion allowed)."""
        ...

    async def get_by_workflow(
        self,
        workflow_run_id: UUID,
        limit: int = 100
    ) -> List[AuditEvent]:
        """Get all events for a workflow, ordered by timestamp."""
        ...

    async def get_by_claim(
        self,
        claim_id: UUID,
        limit: int = 100
    ) -> List[AuditEvent]:
        """Get all events for a claim, ordered by timestamp."""
        ...

    async def get_by_time_range(
        self,
        start: datetime,
        end: datetime,
        event_types: Optional[List[AuditEventType]] = None,
        limit: int = 1000
    ) -> List[AuditEvent]:
        """Get events within a time range, optionally filtered by type."""
        ...

    async def get_by_actor(
        self,
        actor_type: str,
        actor_id: str,
        limit: int = 100
    ) -> List[AuditEvent]:
        """Get all events by a specific actor."""
        ...

    # No update or delete methods - audit is append-only
```

## Redis Cache Patterns

### Workflow State Cache

```python
class WorkflowStateCache:
    """
    Redis cache for active workflow state.
    """

    def __init__(self, redis: Redis, ttl_seconds: int = 3600):
        self.redis = redis
        self.ttl = ttl_seconds

    def _key(self, workflow_run_id: UUID) -> str:
        return f"workflow:state:{workflow_run_id}"

    async def get(
        self,
        workflow_run_id: UUID
    ) -> Optional[Dict[str, Any]]:
        """Get cached workflow state."""
        data = await self.redis.get(self._key(workflow_run_id))
        return json.loads(data) if data else None

    async def set(
        self,
        workflow_run_id: UUID,
        state_data: Dict[str, Any]
    ) -> None:
        """Cache workflow state."""
        await self.redis.setex(
            self._key(workflow_run_id),
            self.ttl,
            json.dumps(state_data, default=str)
        )

    async def delete(self, workflow_run_id: UUID) -> None:
        """Remove cached state (on completion or checkpoint load)."""
        await self.redis.delete(self._key(workflow_run_id))

    async def refresh_ttl(self, workflow_run_id: UUID) -> None:
        """Refresh TTL for active workflow."""
        await self.redis.expire(self._key(workflow_run_id), self.ttl)
```

### Idempotency Lock

```python
class IdempotencyLock:
    """
    Redis-based distributed lock for idempotent operations.
    """

    def __init__(self, redis: Redis, lock_ttl_seconds: int = 300):
        self.redis = redis
        self.lock_ttl = lock_ttl_seconds

    def _key(self, operation: str, identifier: str) -> str:
        return f"lock:{operation}:{identifier}"

    async def acquire(
        self,
        operation: str,
        identifier: str,
        owner_id: str
    ) -> bool:
        """
        Attempt to acquire a lock.

        Returns True if lock acquired, False if already held.
        """
        key = self._key(operation, identifier)
        acquired = await self.redis.set(
            key,
            owner_id,
            nx=True,  # Only set if not exists
            ex=self.lock_ttl
        )
        return acquired is not None

    async def release(
        self,
        operation: str,
        identifier: str,
        owner_id: str
    ) -> bool:
        """
        Release a lock if owned by the requester.

        Returns True if released, False if not owned or doesn't exist.
        """
        key = self._key(operation, identifier)

        # Lua script for atomic check-and-delete
        script = """
        if redis.call("GET", KEYS[1]) == ARGV[1] then
            return redis.call("DEL", KEYS[1])
        else
            return 0
        end
        """

        result = await self.redis.eval(script, [key], [owner_id])
        return result == 1

    async def is_locked(
        self,
        operation: str,
        identifier: str
    ) -> bool:
        """Check if a lock exists."""
        key = self._key(operation, identifier)
        return await self.redis.exists(key) > 0
```

### Tool Output Cache

```python
class ToolOutputCache:
    """
    Cache for tool outputs to support replay determinism.
    """

    def __init__(self, redis: Redis, ttl_seconds: int = 86400):
        self.redis = redis
        self.ttl = ttl_seconds

    def _key(
        self,
        workflow_run_id: UUID,
        tool_name: str,
        call_id: UUID
    ) -> str:
        return f"tool:output:{workflow_run_id}:{tool_name}:{call_id}"

    async def store(
        self,
        workflow_run_id: UUID,
        tool_name: str,
        call_id: UUID,
        output: Dict[str, Any]
    ) -> None:
        """Store tool output for potential replay."""
        key = self._key(workflow_run_id, tool_name, call_id)
        await self.redis.setex(
            key,
            self.ttl,
            json.dumps(output, default=str)
        )

    async def get(
        self,
        workflow_run_id: UUID,
        tool_name: str,
        call_id: UUID
    ) -> Optional[Dict[str, Any]]:
        """Get cached tool output."""
        key = self._key(workflow_run_id, tool_name, call_id)
        data = await self.redis.get(key)
        return json.loads(data) if data else None
```

## Transaction Boundaries

### Workflow State Update Transaction

```python
async def update_workflow_state_atomic(
    workflow_run_id: UUID,
    new_state: WorkflowState,
    state_data: Dict[str, Any],
    checkpoint: Optional[Checkpoint] = None
) -> WorkflowRun:
    """
    Atomically update workflow state and optionally create checkpoint.
    """

    async with database.transaction() as tx:
        # 1. Update workflow run
        workflow = await tx.execute(
            """
            UPDATE workflow_runs
            SET current_state = $1,
                state_data = $2,
                step_count = step_count + 1,
                updated_at = NOW()
            WHERE workflow_run_id = $3
            RETURNING *
            """,
            new_state,
            json.dumps(state_data),
            workflow_run_id
        )

        # 2. Record state transition
        await tx.execute(
            """
            INSERT INTO state_transitions
            (workflow_run_id, from_state, to_state, trigger, reason, step_number)
            VALUES ($1, $2, $3, $4, $5, $6)
            """,
            workflow_run_id,
            workflow.current_state,
            new_state,
            determine_trigger(state_data),
            determine_reason(state_data),
            workflow.step_count
        )

        # 3. Create checkpoint if provided
        if checkpoint:
            await tx.execute(
                """
                INSERT INTO checkpoints
                (workflow_run_id, state, state_data, step_count, budget_state, checkpoint_type)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                checkpoint.workflow_run_id,
                checkpoint.state,
                json.dumps(checkpoint.state_data),
                checkpoint.step_count,
                json.dumps(checkpoint.budget_state),
                checkpoint.checkpoint_type
            )

            # Update workflow with checkpoint reference
            await tx.execute(
                """
                UPDATE workflow_runs
                SET last_checkpoint_id = $1
                WHERE workflow_run_id = $2
                """,
                checkpoint.checkpoint_id,
                workflow_run_id
            )

        return workflow
```

## Data Retention

### Retention Policies

```python
class RetentionPolicy:
    """
    Data retention configuration.
    """

    # Claims data - 7 years (insurance regulation)
    CLAIMS_RETENTION_YEARS = 7

    # Audit events - 7 years (compliance)
    AUDIT_RETENTION_YEARS = 7

    # Workflow runs - 3 years
    WORKFLOW_RETENTION_YEARS = 3

    # Checkpoints - 90 days after workflow completion
    CHECKPOINT_RETENTION_DAYS = 90

    # Tool output cache - 24 hours
    TOOL_OUTPUT_CACHE_HOURS = 24

    # Redis state cache - 1 hour
    STATE_CACHE_HOURS = 1


async def cleanup_expired_data():
    """
    Background task to clean up expired data.
    Runs daily.
    """

    # Clean up old checkpoints
    await database.execute(
        """
        DELETE FROM checkpoints
        WHERE created_at < NOW() - INTERVAL '%s days'
        AND workflow_run_id IN (
            SELECT workflow_run_id FROM workflow_runs
            WHERE completed_at IS NOT NULL
        )
        """,
        RetentionPolicy.CHECKPOINT_RETENTION_DAYS
    )

    # Clean up old tool output cache entries
    await redis.eval(
        """
        local keys = redis.call('KEYS', 'tool:output:*')
        for i=1, #keys do
            redis.call('DEL', keys[i])
        end
        return #keys
        """,
    )
```

## Backup Strategy

### PostgreSQL Backup

```yaml
# Backup configuration
backup:
  postgresql:
    # Full backup daily
    full_backup_schedule: "0 2 * * *"
    retention_days: 30

    # Point-in-time recovery
    wal_archiving: true
    wal_retention_days: 7

    # Backup location
    storage: "s3://casefile-backups/postgresql/"

  redis:
    # RDB snapshots every 15 minutes
    rdb_schedule: "*/15 * * * *"
    retention_days: 7

    # AOF for durability
    append_only: true
    fsync: "everysec"
```

## Performance Considerations

### Indexing Strategy

1. **Primary keys** on all ID columns
2. **Foreign keys** indexed automatically
3. **Query-specific indexes** based on access patterns
4. **Composite indexes** for common filter combinations

### Connection Pooling

```python
# Async connection pool configuration
DATABASE_CONFIG = {
    "min_size": 5,
    "max_size": 20,
    "max_queries": 50000,
    "max_inactive_connection_lifetime": 300,
}

REDIS_CONFIG = {
    "max_connections": 50,
    "retry_on_timeout": True,
    "health_check_interval": 30,
}
```

### Query Optimization

- Use `EXPLAIN ANALYZE` for slow queries
- Avoid N+1 queries with proper joins
- Use JSONB for flexible fields with GIN indexes
- Batch inserts for bulk operations

## Summary

The persistence architecture provides:
- **Durability**: All critical data persisted in PostgreSQL
- **Performance**: Redis caching for hot paths
- **Auditability**: Immutable audit trail for compliance
- **Resumability**: Checkpoints for workflow recovery
- **Scalability**: Partitioning strategy for growth
- **Consistency**: Transaction boundaries for atomic updates

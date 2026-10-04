# ADR-003: PostgreSQL as System of Record

> **Status**: SUPERSEDED by [ADR-011](ADR-011-sqlite-local-persistence.md) (2026-09-22).
> PostgreSQL is no longer used. Local persistence is SQLite; see ADR-011.
> This document is preserved unchanged as historical record.
>
> **Status (original)**: Accepted
**Date**: 2026-09-19
**Deciders**: Architecture Team
**Related Documents**: `persistence.md`, `architecture.md`, `checkpointing.md`

## Context

CASEFILE requires durable persistence for:
- **Workflow state**: Current state, agent outputs, transitions
- **Checkpoints**: Full workflow snapshots for resume/replay
- **Event log**: Immutable audit trail of all actions
- **Budget tracking**: Token counts, costs, step counts
- **Evaluation runs**: Golden datasets for regression testing

Requirements:
- **ACID guarantees**: Consistency critical for workflow correctness
- **Complex queries**: Filter/aggregate evaluation runs, analyze costs
- **Schema evolution**: Support migrations as contracts evolve
- **Point-in-time recovery**: Restore system state for incident investigation
- **Concurrent access**: Multiple workflows executing simultaneously
- **JSON support**: Store Pydantic models efficiently

## Decision

We will use **PostgreSQL 15+** as the system of record for all persistent state.

### Schema Design
```sql
-- Workflow runs
CREATE TABLE workflow_runs (
    workflow_id UUID PRIMARY KEY,
    state TEXT NOT NULL,  -- WorkflowState enum
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    terminal_state TEXT,
    terminal_reason TEXT
);

-- Checkpoints
CREATE TABLE checkpoints (
    checkpoint_id UUID PRIMARY KEY,
    workflow_id UUID REFERENCES workflow_runs,
    step_number INT NOT NULL,
    state_snapshot JSONB NOT NULL,  -- Full workflow state
    version_hash TEXT NOT NULL,     -- Workflow definition version
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE(workflow_id, step_number)
);

-- Event log (immutable audit trail)
CREATE TABLE event_log (
    event_id BIGSERIAL PRIMARY KEY,
    workflow_id UUID REFERENCES workflow_runs,
    event_type TEXT NOT NULL,       -- agent_invocation, tool_call, state_transition
    event_payload JSONB NOT NULL,
    timestamp TIMESTAMPTZ NOT NULL,
    trace_id TEXT NOT NULL          -- OpenTelemetry trace ID
);

-- Budget state
CREATE TABLE budget_state (
    workflow_id UUID PRIMARY KEY REFERENCES workflow_runs,
    input_tokens INT NOT NULL,
    output_tokens INT NOT NULL,
    total_cost_usd DECIMAL(10,4) NOT NULL,
    step_count INT NOT NULL,
    execution_time_seconds INT NOT NULL,
    rework_count INT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

-- Evaluation runs
CREATE TABLE evaluation_runs (
    run_id UUID PRIMARY KEY,
    scenario_name TEXT NOT NULL,
    workflow_id UUID REFERENCES workflow_runs,
    expected_terminal_state TEXT NOT NULL,
    actual_terminal_state TEXT,
    passed BOOLEAN,
    created_at TIMESTAMPTZ NOT NULL
);
```

## Consequences

### Positive
- **ACID compliance**: Strong consistency guarantees for workflow state
- **JSONB support**: Efficient storage and querying of Pydantic models
- **Rich query capability**: Complex analytics on evaluation runs, costs
- **Mature tooling**: Migrations (Alembic), backup/restore, monitoring
- **PostgreSQL ecosystem**: Extensions (pg_stat_statements, timescaledb for metrics)
- **Point-in-time recovery**: Restore to any timestamp for incident response
- **Concurrent transactions**: Multiple workflows don't interfere

### Negative
- **Operational overhead**: Database server to run, tune, backup
- **Vertical scaling limit**: Single database instance has capacity ceiling
- **Query complexity**: ORM abstraction can generate inefficient queries
- **Schema migrations**: Breaking changes require careful migration planning

### Mitigations
- **Connection pooling**: Use PgBouncer to handle connection limits
- **Read replicas**: Offload analytics queries to replicas
- **Partitioning**: Time-based partitioning for event_log table
- **Migration testing**: Test migrations on production-like data before deploy
- **Query monitoring**: Use pg_stat_statements to identify slow queries
- **Backup automation**: Automated daily backups with point-in-time recovery

## Alternatives Considered

### 1. DynamoDB
- **Pros**: Managed service, horizontal scaling, low operational overhead
- **Rejected**: Weak query capabilities, no ACID across partitions, higher cost for CASEFILE access patterns

### 2. MongoDB
- **Pros**: JSON-native, flexible schema, horizontal scaling
- **Rejected**: Weaker ACID guarantees (before v4), less mature ecosystem for analytical queries

### 3. SQLite
- **Pros**: Zero configuration, embedded, simple deployment
- **Rejected**: Single-writer limit prohibits concurrent workflows, no network access

### 4. Redis (as primary store)
- **Pros**: In-memory speed, simple data model
- **Rejected**: Not designed for durable system-of-record, complex queries difficult, snapshot persistence slower than WAL

## Integration Points

### LangGraph Checkpointing
- LangGraph checkpoint saver writes to `checkpoints` table
- Custom checkpoint implementation wraps PostgreSQL storage

### Event Log
- Every state transition, agent invocation, tool call logged to `event_log`
- Immutable append-only log (no updates/deletes)

### Budget Enforcement
- Pre-flight checks query `budget_state` before agent invocation
- Budget updates are atomic (transaction-protected)

## References

- PostgreSQL 15 documentation: https://www.postgresql.org/docs/15/
- CASEFILE persistence design: `docs/persistence.md`
- Checkpoint strategy: `docs/checkpointing.md`

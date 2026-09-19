# ADR-006: Checkpoint and Replay Strategy

**Status**: Accepted
**Date**: 2026-09-19
**Deciders**: Architecture Team
**Related Documents**: `checkpointing.md`, `replay.md`, `persistence.md`, `testing.md`

## Context

CASEFILE requires the ability to:
- **Resume interrupted workflows**: Network failures, server restarts, deployments shouldn't lose progress
- **Replay for debugging**: Reproduce exact workflow execution for incident investigation
- **Evaluation testing**: Verify deterministic behavior by replaying from checkpoints
- **Version validation**: Confirm new code versions produce same results as old versions
- **Cost control**: Avoid recomputing expensive LLM calls after failures

Requirements:
- **Granular checkpoints**: Save state after every agent transition, before expensive operations
- **Deterministic replay**: Replaying from checkpoint must produce identical results
- **Version compatibility**: Detect when checkpoint is incompatible with current code
- **Efficient storage**: Minimize checkpoint size while capturing full state
- **Fast resume**: Restore from checkpoint in <1 second

## Decision

We will implement **checkpoint-after-every-agent-transition** with **deterministic replay from event log**.

### Checkpoint Strategy

#### When to Checkpoint
1. **After successful agent invocation**: Extractor, Investigator, Reviewer complete
2. **Before state transitions**: REVIEW → HUMAN_APPROVAL, REWORK_LOOP → INVESTIGATION
3. **On budget thresholds**: 50%, 75%, 90% of token/cost limits
4. **Before expensive operations**: External API calls, database writes

#### Checkpoint Content
```python
class Checkpoint(BaseModel):
    checkpoint_id: UUID
    workflow_id: UUID
    step_number: int
    timestamp: datetime

    # State snapshot
    workflow_state: WorkflowState  # Current state enum
    agent_outputs: dict[str, BaseModel]  # All agent results so far
    budget_state: BudgetState

    # Version tracking
    workflow_definition_version: str  # e.g., "1.2.0"
    langgraph_version: str

    # Replay support
    event_log_offset: int  # Position in event log
    tool_call_history: list[ToolCallRecord]
```

### Replay Strategy

#### Replay Process
1. **Load checkpoint** from PostgreSQL
2. **Validate version compatibility**: Check workflow_definition_version
3. **Restore agent state**: Reconstruct agent outputs from checkpoint
4. **Replay tool calls**: Re-execute tools from event log (for verification)
5. **Compare outputs**: Ensure deterministic execution (tool outputs match)
6. **Resume or diverge**: Continue workflow or flag divergence

#### Determinism Guarantees
- **LLM calls**: Recorded in event log, NOT replayed (use cached outputs)
- **Tool calls**: Replayed to verify determinism (or use recorded outputs)
- **Random seeds**: Captured in checkpoint, restored for replay
- **Timestamps**: Logical clock (step number), not wall clock

```python
async def replay_from_checkpoint(checkpoint_id: UUID, mode: ReplayMode) -> ReplayResult:
    """
    Replay workflow from checkpoint.

    Args:
        checkpoint_id: Checkpoint to replay from
        mode: VERIFY (re-execute and compare) or RESUME (use cached outputs)

    Returns:
        ReplayResult with success status and divergence report
    """
    checkpoint = load_checkpoint(checkpoint_id)

    # Validate version compatibility
    if checkpoint.workflow_definition_version != CURRENT_VERSION:
        raise IncompatibleCheckpoint(f"Checkpoint version {checkpoint.workflow_definition_version} incompatible with {CURRENT_VERSION}")

    # Restore state
    workflow_state = restore_workflow_state(checkpoint)

    if mode == ReplayMode.VERIFY:
        # Re-execute tool calls and compare outputs
        divergences = await verify_determinism(checkpoint)
        if divergences:
            return ReplayResult(success=False, divergences=divergences)

    # Resume from checkpoint
    return ReplayResult(success=True, resumed_from_step=checkpoint.step_number)
```

## Consequences

### Positive
- **Resilience**: Workflows survive failures, resume from last checkpoint
- **Debuggability**: Reproduce exact execution path for bug investigation
- **Cost efficiency**: Don't recompute LLM calls after failures
- **Deterministic testing**: Evaluation runs replay from checkpoints reliably
- **Version safety**: Detect incompatible code changes before resuming

### Negative
- **Storage overhead**: Checkpoint per agent transition increases database size
- **Checkpoint latency**: Writing checkpoint adds ~50-100ms per transition
- **Complexity**: Checkpoint serialization/deserialization code to maintain
- **Non-determinism risk**: External API changes break replay verification

### Mitigations
- **Checkpoint compression**: Use JSONB in PostgreSQL (automatic compression)
- **Async checkpoint writes**: Don't block workflow on checkpoint persistence
- **Incremental checkpoints**: Delta encoding (only changed fields)
- **Checkpoint pruning**: Delete old checkpoints after workflow completion (keep evaluation corpus)
- **Replay mode flag**: Choose VERIFY (strict) or RESUME (fast) based on use case

## Alternatives Considered

### 1. Single checkpoint at workflow end
- **Pros**: Minimal storage, simple
- **Rejected**: No resume capability, all progress lost on failure

### 2. Checkpoint only before expensive operations
- **Pros**: Fewer checkpoints, lower storage cost
- **Rejected**: Lose progress between checkpoints, harder to debug

### 3. Snapshot-based checkpointing (save entire process memory)
- **Pros**: Guaranteed complete state capture
- **Rejected**: Massive storage overhead, non-portable (OS-specific), slow to save/restore

### 4. Event sourcing (no snapshots, replay from event log)
- **Pros**: Complete audit trail, no state duplication
- **Rejected**: Slow to resume (must replay all events), non-deterministic LLM calls can't be replayed

## Checkpoint Granularity Analysis

| Checkpoint Frequency | Storage Cost | Resume Time | Debug Granularity |
|---------------------|--------------|-------------|-------------------|
| Per agent transition | HIGH | LOW | EXCELLENT |
| Per state transition | MEDIUM | MEDIUM | GOOD |
| Per expensive operation | LOW | HIGH | FAIR |
| Workflow start/end only | VERY LOW | N/A | POOR |

**Decision**: Per-agent-transition provides best balance of resume time and debug granularity.

## Replay Use Cases

### 1. Resume After Failure
```python
# Workflow execution interrupted
workflow_id = "abc-123"
last_checkpoint = get_latest_checkpoint(workflow_id)
result = await replay_from_checkpoint(last_checkpoint.id, mode=ReplayMode.RESUME)
# Continue from where we left off
```

### 2. Regression Testing
```python
# Verify new code version produces same results
evaluation_checkpoint = load_evaluation_checkpoint("scenario-001")
result = await replay_from_checkpoint(evaluation_checkpoint.id, mode=ReplayMode.VERIFY)
assert result.success, f"Divergences detected: {result.divergences}"
```

### 3. Incident Investigation
```python
# Reproduce production issue
incident_workflow_id = "prod-failure-456"
checkpoint_before_failure = get_checkpoint_before_failure(incident_workflow_id)
result = await replay_from_checkpoint(checkpoint_before_failure.id, mode=ReplayMode.VERIFY)
# Inspect divergences, logs, traces
```

## Evaluation Integration

- **Evaluation corpus**: Minimum 30 checkpoints representing key scenarios
- **Regression suite**: Replay all corpus checkpoints on every code change
- **Divergence alerting**: CI fails if any replay produces different terminal state

```yaml
evaluation_corpus:
  - scenario: happy_path_approval
    checkpoint_id: eval-001
    expected_terminal_state: APPROVED

  - scenario: reviewer_triggers_rework
    checkpoint_id: eval-002
    expected_terminal_state: APPROVED
    expected_rework_count: 1

  - scenario: budget_exhaustion
    checkpoint_id: eval-003
    expected_terminal_state: BUDGET_EXHAUSTED
```

## References

- CASEFILE checkpointing design: `docs/checkpointing.md`
- CASEFILE replay design: `docs/replay.md`
- LangGraph checkpointing: https://langchain-ai.github.io/langgraph/how-tos/persistence/
- Event sourcing patterns: Martin Fowler's Event Sourcing article

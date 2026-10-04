# CASEFILE Failure Modes

## Implementation Status (Phase 11)

Typed failure injection and golden recovery scenarios are implemented per
`docs/failure-injection.md`: 15 observation points, 7 failure types,
opt-in rules owned exclusively by the evaluation runner. G03–G08, G11–G15
cover tool unavailability, provider retry exhaustion, budget stops,
corrupt checkpoints, races, missing replay artifacts, and broken telemetry.

## Implementation Status (Phase 10)

Telemetry failures are isolated by design: OTLP exporter/collector
errors, sink exceptions, and SDK setup failures never propagate into
workflow, budget, checkpoint, or approval business paths (proven by
broken-exporter and failing-sink green-path tests). Error spans carry
`safe_error` category/code/component only — no raw exception text.

## Overview

CASEFILE is designed to handle failures gracefully. Every potential failure point has defined handling behavior, retry policies, and escalation paths. This document catalogs all failure modes and their mitigations.

## Failure Mode Taxonomy

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        FAILURE MODE CATEGORIES                               │
│                                                                              │
│  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐      │
│  │   LLM FAILURES    │  │   TOOL FAILURES   │  │ INFRASTRUCTURE    │      │
│  │                   │  │                   │  │   FAILURES        │      │
│  │ • Timeout         │  │ • Timeout         │  │ • Database        │      │
│  │ • Provider error  │  │ • Unavailable     │  │ • Redis           │      │
│  │ • Malformed out   │  │ • Authorization   │  │ • Network         │      │
│  │ • Rate limit      │  │ • Validation      │  │ • External API    │      │
│  │ • Budget exceed   │  │ • Retry exhaust   │  │                   │      │
│  └───────────────────┘  └───────────────────┘  └───────────────────┘      │
│                                                                              │
│  ┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐      │
│  │  WORKFLOW LOGIC   │  │   DATA ISSUES     │  │   HUMAN FACTOR    │      │
│  │   FAILURES        │  │                   │  │                   │      │
│  │ • Max steps       │  │ • Malformed doc   │  │ • Approval timeout│      │
│  │ • Max rework      │  │ • Missing data    │  │ • Rejection       │      │
│  │ • Budget exhaust  │  │ • Schema invalid  │  │ • Escalation      │      │
│  │ • Timeout         │  │ • Checkpoint fail │  │                   │      │
│  └───────────────────┘  └───────────────────┘  └───────────────────┘      │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## LLM Failures

### LLM Timeout

**Detection**: asyncio.TimeoutError after configured timeout

**Handling**:
```python
async def handle_llm_timeout(
    agent: SpecialistAgent,
    request: AgentRequest,
    context: ExecutionContext
) -> AgentResult:
    """Handle LLM timeout with retry and fallback."""

    # Record timeout event
    await audit_log.record(AuditEvent(
        event_type=AuditEventType.LLM_TIMEOUT,
        details={"agent": agent.agent_name, "timeout_seconds": agent.timeout}
    ))

    # Check retry count
    if context.retry_count < agent.max_retries:
        # Retry with exponential backoff
        await asyncio.sleep(2 ** context.retry_count)
        context.retry_count += 1
        return await agent.execute(request)

    # Max retries exceeded - return timeout result
    return agent.create_timeout_result(
        request=request,
        error="LLM call timed out after retries"
    )
```

**Terminal State**: TIMEOUT (if max retries exceeded)

**Recovery**: Checkpoint exists, can be resumed with increased timeout

---

### LLM Provider Error

**Detection**: Exception from LLM provider (API error, rate limit, etc.)

**Handling**:
```python
async def handle_llm_provider_error(
    error: LLMProviderError,
    agent: SpecialistAgent,
    context: ExecutionContext
) -> AgentResult:
    """Handle LLM provider errors."""

    # Log error details
    logger.error("llm_provider_error", error=str(error), error_type=type(error).__name__)

    # Categorize error
    if error.is_rate_limit:
        # Wait and retry
        await asyncio.sleep(error.retry_after or 60)
        return await retry_execution(agent, context)

    if error.is_retryable:
        # Retry with backoff
        return await retry_with_backoff(agent, context)

    if error.is_budget_related:
        # Budget exhausted
        context.budget_state.is_exhausted = True
        context.budget_state.exhaustion_reason = "LLM_BUDGET_EXCEEDED"
        raise BudgetExhaustedError()

    # Non-retryable error
    return agent.create_error_result(
        error=error,
        error_type="PROVIDER_ERROR"
    )
```

**Terminal State**: FAILED or BUDGET_EXHAUSTED

---

### Malformed LLM Output

**Detection**: Pydantic ValidationError on structured output

**Handling**:
```python
async def handle_malformed_output(
    raw_output: str,
    response_model: type,
    agent: SpecialistAgent,
    context: ExecutionContext
) -> BaseModel:
    """Handle malformed LLM structured output."""

    # Attempt to parse with lenience
    try:
        # Try parsing as-is
        return response_model.model_validate_json(raw_output)
    except ValidationError:
        pass

    # Try to repair common issues
    repaired = attempt_repair(raw_output, response_model)

    try:
        return response_model.model_validate_json(repaired)
    except ValidationError:
        pass

    # Retry with explicit schema in prompt
    if context.schema_retry_count < 2:
        context.schema_retry_count += 1
        return await retry_with_explicit_schema(agent, context, response_model)

    # Cannot recover - fail
    raise OutputValidationError(
        f"Could not parse LLM output as {response_model.__name__}"
    )
```

**Terminal State**: FAILED

---

## Tool Failures

### Tool Timeout

**Detection**: asyncio.TimeoutError during tool execution

**Handling**:
```python
async def handle_tool_timeout(
    tool_name: str,
    call_id: UUID,
    context: ExecutionContext
) -> ToolResult:
    """Handle tool execution timeout."""

    # Record timeout
    await record_tool_call(
        call_id=call_id,
        tool_name=tool_name,
        status=ToolCallStatus.TIMEOUT
    )

    # Check retry policy
    retry_policy = tool.get_retry_policy()

    if context.tool_retry_count < retry_policy.max_retries:
        await asyncio.sleep(retry_policy.backoff_seconds)
        return await retry_tool_call(tool_name, context)

    # Return failure
    return ToolResult(
        call_id=call_id,
        status=ToolCallStatus.TIMEOUT,
        error="Tool execution timed out"
    )
```

**Impact**: May allow workflow to continue with partial results or trigger FAILED state

---

### Tool Unavailable

**Detection**: ToolNotRegisteredError or connection failure

**Handling**:
```python
async def handle_tool_unavailable(
    tool_name: str,
    context: ExecutionContext
) -> None:
    """Handle tool unavailable scenario."""

    # Check if tool is required for current state
    if is_tool_required(tool_name, context.current_state):
        # Cannot proceed - fail workflow
        raise ToolUnavailableError(
            f"Required tool {tool_name} is unavailable"
        )

    # Tool is optional - continue with partial data
    logger.warning(
        "optional_tool_unavailable",
        tool=tool_name,
        state=context.current_state
    )

    # Mark tool as skipped
    context.skipped_tools.append(tool_name)
```

**Terminal State**: FAILED if required tool, else continues

---

### Tool Authorization Failure

**Detection**: ToolAuthorizationError from authorization check

**Handling**:
```python
async def handle_tool_authorization_failure(
    tool_name: str,
    agent_name: str,
    context: ExecutionContext
) -> None:
    """Handle tool authorization failure."""

    # Log security event
    await security_logger.log_authorization_failure(
        actor=agent_name,
        resource=tool_name,
        action="execute"
    )

    # This is a configuration error - fail immediately
    raise ConfigurationError(
        f"Agent {agent_name} is not authorized to call tool {tool_name}. "
        "Check tool configuration and agent permissions."
    )
```

**Terminal State**: FAILED (configuration error)

---

## Infrastructure Failures

### Database Failure

**Detection**: Database connection error, query error, or timeout

**Handling**:
```python
async def handle_database_failure(
    error: DatabaseError,
    operation: str,
    context: ExecutionContext
) -> None:
    """Handle database failures."""

    # Log failure
    logger.error("database_failure", operation=operation, error=str(error))

    # Check if transient
    if is_transient_error(error):
        # Retry with backoff
        for attempt in range(3):
            await asyncio.sleep(2 ** attempt)
            try:
                return await retry_database_operation(operation, context)
            except DatabaseError:
                continue

    # Check if write operation
    if operation in ["save_checkpoint", "update_state", "record_audit"]:
        # Critical - cannot continue
        raise DatabaseFailureError(
            f"Critical database operation failed: {operation}"
        )

    # Read operation - may be able to use cache
    cached = await try_get_from_cache(operation, context)
    if cached:
        return cached

    # Cannot recover
    raise DatabaseFailureError(f"Database unavailable for operation: {operation}")
```

**Terminal State**: FAILED or ESCALATION

---

### Redis Failure

**Detection**: Redis connection error or timeout

**Handling**:
```python
async def handle_redis_failure(
    error: RedisError,
    operation: str,
    context: ExecutionContext
) -> None:
    """Handle Redis failures with graceful degradation."""

    logger.warning("redis_failure", operation=operation, error=str(error))

    # Redis is cache - can degrade gracefully
    if operation == "get_cached_state":
        # Fall back to database
        return await load_state_from_database(context)

    if operation == "acquire_lock":
        # Proceed without distributed lock (single instance mode)
        logger.warning("proceeding_without_distributed_lock")
        return True

    if operation == "cache_checkpoint":
        # Continue without cache - checkpoint still in database
        return None

    # Other operations - try fallback
    return await fallback_handler.handle(error, operation, context)
```

**Terminal State**: Usually continues (graceful degradation)

---

## Workflow Logic Failures

### Max Steps Exceeded

**Detection**: step_count >= max_steps

**Handling**:
```python
async def handle_max_steps_exceeded(
    context: ExecutionContext
) -> TerminalStateRecord:
    """Handle max steps exceeded."""

    # This is a safety termination, not an error
    logger.warning(
        "max_steps_exceeded",
        workflow_run_id=context.workflow_run_id,
        step_count=context.step_count,
        max_steps=context.budget_state.limits.max_steps
    )

    # Create termination record
    return await termination_handler.terminate_workflow(
        workflow_run_id=context.workflow_run_id,
        terminal_state=WorkflowState.MAX_STEPS_EXCEEDED,
        reason=f"Maximum steps ({context.budget_state.limits.max_steps}) exceeded",
        context=context
    )
```

**Terminal State**: MAX_STEPS_EXCEEDED

**Recovery**: Investigate cause, may need to increase limit or fix infinite loop

---

### Max Rework Exceeded

**Detection**: rework_count >= max_rework_cycles

**Handling**:
```python
async def handle_max_rework_exceeded(
    context: ExecutionContext
) -> TerminalStateRecord:
    """Handle max rework cycles exceeded."""

    logger.info(
        "max_rework_exceeded",
        workflow_run_id=context.workflow_run_id,
        rework_count=context.rework_count,
        rework_history=context.rework_history
    )

    # This indicates reviewer cannot reach a decision
    return await termination_handler.terminate_workflow(
        workflow_run_id=context.workflow_run_id,
        terminal_state=WorkflowState.MAX_REWORK_EXCEEDED,
        reason=f"Maximum rework cycles ({context.budget_state.limits.max_rework_cycles}) exceeded",
        context=context
    )
```

**Terminal State**: MAX_REWORK_EXCEEDED

**Recovery**: Requires human intervention to resolve

---

### Budget Exhaustion

**Detection**: budget_state.is_exhausted = True

**Handling**:
```python
async def handle_budget_exhaustion(
    context: ExecutionContext
) -> TerminalStateRecord:
    """Handle budget exhaustion."""

    logger.warning(
        "budget_exhausted",
        workflow_run_id=context.workflow_run_id,
        reason=context.budget_state.exhaustion_reason,
        tokens_used=context.budget_state.total_tokens_used,
        cost_usd=str(context.budget_state.estimated_cost_usd)
    )

    # Emit budget warning
    metrics.record_budget_exhaustion(
        reason=context.budget_state.exhaustion_reason,
        claim_id=context.claim_id
    )

    return await termination_handler.terminate_workflow(
        workflow_run_id=context.workflow_run_id,
        terminal_state=WorkflowState.BUDGET_EXHAUSTED,
        reason=context.budget_state.exhaustion_reason,
        context=context
    )
```

**Terminal State**: BUDGET_EXHAUSTED

**Recovery**: May retry with increased budget if legitimate

---

## Data Issues

### Malformed Document

**Detection**: Document validation failure during extraction

**Handling**:
```python
async def handle_malformed_document(
    document: Document,
    error: ValidationError,
    context: ExecutionContext
) -> DocumentProcessingResult:
    """Handle malformed document."""

    # Log issue
    logger.warning(
        "malformed_document",
        document_id=document.document_id,
        document_type=document.document_type,
        error=str(error)
    )

    # Record failure
    return DocumentProcessingResult(
        document_id=document.document_id,
        status=DocumentProcessingStatus.FAILED,
        error=str(error)
    )

    # Extraction will continue with other documents
    # If all documents fail, extraction returns PARTIAL_SUCCESS or FAILURE
```

**Impact**: Partial extraction or extraction failure

---

### Checkpoint Integrity Failure

**Detection**: Checksum mismatch during checkpoint load

**Handling**:
```python
async def handle_checkpoint_integrity_failure(
    checkpoint: Checkpoint,
    context: ExecutionContext
) -> Checkpoint:
    """Handle checkpoint integrity failure."""

    logger.error(
        "checkpoint_integrity_failure",
        checkpoint_id=checkpoint.checkpoint_id,
        expected_checksum=checkpoint.checksum,
        computed_checksum=checkpoint.compute_checksum()
    )

    # Try loading previous checkpoint
    previous = await checkpoint_manager.get_previous_checkpoint(
        checkpoint.workflow_run_id,
        checkpoint.checkpoint_id
    )

    if previous and previous.verify_integrity():
        logger.info("using_previous_checkpoint", checkpoint_id=previous.checkpoint_id)
        return previous

    # No valid checkpoint - cannot recover
    raise CheckpointIntegrityError(
        f"Checkpoint {checkpoint.checkpoint_id} failed integrity check and no valid backup exists"
    )
```

**Terminal State**: FAILED if no valid checkpoint

---

## Human Factor Failures

### Human Approval Timeout

**Detection**: Approval not received within timeout period

**Handling**:
```python
async def handle_human_approval_timeout(
    workflow_run_id: UUID,
    context: ExecutionContext
) -> TerminalStateRecord:
    """Handle human approval timeout."""

    logger.warning(
        "human_approval_timeout",
        workflow_run_id=workflow_run_id,
        timeout_at=context.approval_timeout_at
    )

    # Escalate to operations
    await notification_service.notify_operations(
        workflow_run_id=workflow_run_id,
        event="approval_timeout",
        message=f"Human approval not received within {context.budget_state.limits.human_approval_timeout_seconds}s"
    )

    return await termination_handler.terminate_workflow(
        workflow_run_id=workflow_run_id,
        terminal_state=WorkflowState.ESCALATION,
        reason="Human approval timeout",
        context=context
    )
```

**Terminal State**: ESCALATION

---

### Human Rejection

**Detection**: HumanApproval with decision=REJECTED

**Handling**:
```python
async def handle_human_rejection(
    approval: HumanApproval,
    context: ExecutionContext
) -> TerminalStateRecord:
    """Handle human rejection of recommendation."""

    logger.info(
        "human_rejection",
        workflow_run_id=context.workflow_run_id,
        approver_id=approval.approver_id,
        reason=approval.notes
    )

    # Record rejection
    await audit_log.record(AuditEvent(
        event_type=AuditEventType.HUMAN_APPROVAL_RECEIVED,
        details={"decision": "REJECTED", "approver": approval.approver_id}
    ))

    return await termination_handler.terminate_workflow(
        workflow_run_id=context.workflow_run_id,
        terminal_state=WorkflowState.REJECTED,
        reason=f"Rejected by {approval.approver_name}: {approval.notes}",
        context=context
    )
```

**Terminal State**: REJECTED

---

## Failure Mode Summary Table

| Failure Type | Detection | Handling | Terminal State | Recovery |
|-------------|-----------|----------|----------------|----------|
| LLM Timeout | TimeoutError | Retry, then fail | TIMEOUT | Resume from checkpoint |
| LLM Provider Error | Exception | Retry/escalate | FAILED/BUDGET_EXHAUSTED | Check provider status |
| Malformed Output | ValidationError | Retry, then fail | FAILED | Fix prompt/schema |
| Tool Timeout | TimeoutError | Retry, partial result | FAILED (if required) | Resume with cached data |
| Tool Unavailable | ConnectionError | Fail if required | FAILED (if required) | Fix tool registration |
| Database Failure | DatabaseError | Retry, fallback | FAILED/ESCALATION | Restore database |
| Redis Failure | RedisError | Graceful degradation | Usually continues | Fix Redis |
| Max Steps | step_count >= limit | Terminate | MAX_STEPS_EXCEEDED | Investigate loop |
| Max Rework | rework_count >= limit | Terminate | MAX_REWORK_EXCEEDED | Human intervention |
| Budget Exhaustion | budget_state check | Terminate | BUDGET_EXHAUSTED | Increase budget |
| Malformed Document | ValidationError | Skip document | PARTIAL/FAILED | Fix document |
| Checkpoint Corrupt | Checksum mismatch | Use backup | FAILED (if no backup) | Restore from backup |
| Approval Timeout | Time check | Escalate | ESCALATION | Manual intervention |
| Human Rejection | Decision | Record and terminate | REJECTED | N/A (normal flow) |

## Recovery Procedures

### Standard Recovery Workflow

```
1. Identify failure type from audit log
2. Check if checkpoint exists
3. If checkpoint exists:
   a. Verify checkpoint integrity
   b. Determine if root cause is fixed
   c. Resume from checkpoint or restart
4. If no checkpoint:
   a. Determine if claim needs reprocessing
   b. Create new workflow or mark as failed
5. Document resolution
6. Update metrics
```

## Summary

The failure mode documentation provides:
- Complete catalog of failure scenarios
- Defined detection mechanisms
- Explicit handling procedures
- Terminal state mappings
- Recovery procedures
- Graceful degradation paths

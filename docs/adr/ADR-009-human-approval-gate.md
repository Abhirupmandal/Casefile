# ADR-009: Human Approval Gate

**Status**: Accepted
**Date**: 2026-09-19
**Deciders**: Architecture Team
**Related Documents**: `state-machine.md`, `architecture.md`, `agent-architecture.md`

## Context

CASEFILE adjudicates insurance claims with financial and reputational consequences. While AI agents handle investigation and analysis, human oversight is required for:

- **Legal compliance**: Regulations may require human-in-the-loop for claim decisions
- **Risk management**: High-value or complex claims need human judgment
- **Accountability**: Humans must approve final decisions for auditability
- **Error correction**: Catch agent mistakes before final claim disposition
- **Customer experience**: Claimants expect human review for fairness

Requirements:
- **State machine integration**: Approval gate is a workflow state, not external process
- **Blocking**: Workflow cannot progress to terminal state without approval
- **Timeout handling**: Stale approvals (no response after N hours) handled gracefully
- **Approval context**: Human sees full investigation, evidence, recommendation
- **Decision recording**: Approval/rejection stored with reason and approver identity
- **Resumable**: Workflow resumes from checkpoint after approval

## Decision

We will implement **HUMAN_APPROVAL as a workflow state** with explicit APPROVED/REJECTED transitions.

### State Machine Integration

```
REVIEW (by Reviewer agent)
  └─> HUMAN_APPROVAL (blocking state)
       ├─> APPROVED (terminal state - claim approved)
       └─> REJECTED (terminal state - claim rejected)
```

The HUMAN_APPROVAL state is **blocking**: the workflow pauses and cannot progress until a human makes a decision.

### Approval Request Structure

```python
class HumanApprovalRequest(BaseModel):
    """Context presented to human approver."""
    workflow_id: UUID
    claim_id: str

    # Investigation summary
    extraction_result: ExtractionResult
    investigation_result: InvestigationResult
    review_result: ReviewResult

    # Evidence
    tool_calls_made: list[ToolCallRecord]
    evidence_documents: list[str]

    # Recommendation
    reviewer_recommendation: Literal["APPROVE", "REJECT"]
    reviewer_reasoning: str
    confidence_score: float

    # Context
    budget_consumed: BudgetState
    rework_count: int
    workflow_duration_seconds: int

    # Approval metadata
    created_at: datetime
    approval_deadline: datetime  # Timeout if no response by this time

class HumanApprovalDecision(BaseModel):
    """Human decision on approval request."""
    workflow_id: UUID
    decision: Literal["APPROVED", "REJECTED"]
    approver_id: str
    approver_name: str
    decision_reason: str
    decided_at: datetime
```

### Approval Flow

#### 1. Transition to HUMAN_APPROVAL
```python
async def transition_to_human_approval(workflow_id: UUID, review_result: ReviewResult):
    """
    Workflow reaches HUMAN_APPROVAL state.
    Create approval request and notify approvers.
    """
    # Save checkpoint before blocking
    await create_checkpoint(workflow_id)

    # Transition state
    await update_workflow_state(workflow_id, WorkflowState.HUMAN_APPROVAL)

    # Create approval request
    approval_request = HumanApprovalRequest(
        workflow_id=workflow_id,
        extraction_result=...,
        investigation_result=...,
        review_result=review_result,
        reviewer_recommendation=review_result.decision,
        approval_deadline=datetime.utcnow() + timedelta(hours=24),
    )

    await save_approval_request(approval_request)

    # Notify approvers (email, Slack, dashboard alert)
    await notify_approvers(approval_request)
```

#### 2. Human Decision
```python
async def submit_approval_decision(decision: HumanApprovalDecision):
    """
    Human submits approval decision.
    Resume workflow from checkpoint.
    """
    workflow_id = decision.workflow_id

    # Validate workflow is in HUMAN_APPROVAL state
    workflow_state = await get_workflow_state(workflow_id)
    if workflow_state != WorkflowState.HUMAN_APPROVAL:
        raise InvalidStateTransition(f"Workflow not in HUMAN_APPROVAL state: {workflow_state}")

    # Record decision
    await save_approval_decision(decision)

    # Transition to terminal state
    if decision.decision == "APPROVED":
        await transition_to_terminal(workflow_id, WorkflowState.APPROVED, decision.decision_reason)
    else:
        await transition_to_terminal(workflow_id, WorkflowState.REJECTED, decision.decision_reason)

    # Emit event for audit trail
    await emit_event(
        workflow_id=workflow_id,
        event_type="human_approval_decision",
        payload=decision.model_dump(),
    )
```

#### 3. Timeout Handling
```python
async def check_approval_timeouts():
    """
    Periodic task: Check for approval requests past deadline.
    Transition to ESCALATION state.
    """
    expired_requests = await get_expired_approval_requests()

    for request in expired_requests:
        logger.warning(
            "approval_timeout",
            workflow_id=request.workflow_id,
            deadline=request.approval_deadline,
        )

        # Transition to ESCALATION terminal state
        await transition_to_terminal(
            request.workflow_id,
            WorkflowState.ESCALATION,
            reason=f"No approval decision by deadline: {request.approval_deadline}",
        )

        # Notify escalation team
        await notify_escalation_team(request)
```

## Consequences

### Positive
- **Compliance**: Human-in-the-loop satisfies regulatory requirements
- **Risk mitigation**: Humans catch agent errors before final decision
- **Auditability**: Approval decision recorded with approver identity and reason
- **Workflow integration**: Approval is state machine state, not external process
- **Resumable**: Workflow resumes from checkpoint after approval (no re-execution)
- **Timeout handling**: Stale approvals escalated automatically

### Negative
- **Latency**: Workflow blocks until human responds (hours to days)
- **Manual workload**: Humans must review every claim (no auto-approval)
- **Bottleneck**: Approval queue can grow if approvers overwhelmed
- **Context switching**: Humans must context-switch to review approval requests

### Mitigations
- **Approval dashboard**: Dedicated UI for approvers to review queue
- **Notification system**: Email/Slack alerts for new approval requests
- **Prioritization**: High-value claims prioritized in approval queue
- **Batch review**: Approvers can review multiple claims in single session
- **Timeout escalation**: Stale approvals escalated to senior team
- **Future: Auto-approval**: Low-risk claims (e.g., <$5K, high confidence) skip human approval

## Alternatives Considered

### 1. No human approval (full automation)
- **Pros**: No latency, no manual workload, pure automation
- **Rejected**: Too risky for production launch, regulatory concerns, lacks accountability

### 2. External approval system (e.g., Jira ticket)
- **Pros**: Reuse existing approval tooling
- **Rejected**: Workflow state managed in two systems (CASEFILE + Jira), complex synchronization

### 3. Synchronous approval (HTTP request blocks)
- **Pros**: Simple request/response pattern
- **Rejected**: HTTP timeouts, no resume capability, poor user experience

### 4. Approval after terminal state (post-hoc review)
- **Pros**: No workflow blocking, humans review completed workflows
- **Rejected**: Too late to prevent bad decisions, defeats purpose of approval

### 5. Agent-suggested auto-approval (confidence threshold)
- **Pros**: Low-risk claims skip human review
- **Considered for Phase 1+**: Requires confidence calibration, risk modeling

## Approval UI Requirements

### Approval Dashboard
- **Queue view**: List of pending approval requests, sorted by priority
- **Claim detail view**: Full investigation context, evidence, recommendation
- **Decision form**: Radio buttons (APPROVE/REJECT), text field (reason)
- **Audit trail**: History of approver decisions, reasons, timestamps

### Approval Request Details
```
Claim: CLM-2024-001234
Claimant: John Doe
Incident Date: 2024-09-15
Claim Amount: $12,450.00

Reviewer Recommendation: APPROVE
Confidence: 0.87
Reasoning: "All policy conditions met, repair cost validated, no fraud signals"

Investigation Summary:
- Policy active on incident date
- Repair cost estimate: $12,450 (validated against market rates)
- No prior claims for similar damage
- No fraud indicators

Evidence:
- Policy document (retrieved)
- Repair estimate (retrieved)
- Claim history (retrieved)
- Fraud database (no matches)

Budget Consumed:
- Tokens: 42,340 / 150,000
- Cost: $0.68 / $5.00
- Duration: 3m 24s

[APPROVE] [REJECT]
Reason: [text field]
```

## Approval Metrics

```python
# Metrics to track
approval_request_created = meter.create_counter("casefile.approval.requests.created")
approval_decision_latency = meter.create_histogram("casefile.approval.decision_latency_seconds")
approval_timeouts = meter.create_counter("casefile.approval.timeouts")

# Labels: decision=APPROVED|REJECTED
approval_decisions = meter.create_counter("casefile.approval.decisions", unit="1")

# SLO: 95% of approvals decided within 24 hours
APPROVAL_SLO_HOURS = 24
```

## Future Enhancements (Phase 1+)

### Auto-Approval for Low-Risk Claims
```python
class AutoApprovalPolicy(BaseModel):
    """Policy for auto-approving claims without human review."""
    max_claim_amount: Decimal = Decimal("5000.00")
    min_confidence_score: float = 0.95
    no_fraud_signals: bool = True
    no_rework_cycles: bool = True

def should_auto_approve(review_result: ReviewResult, claim_amount: Decimal) -> bool:
    policy = AutoApprovalPolicy()
    return (
        claim_amount <= policy.max_claim_amount
        and review_result.confidence_score >= policy.min_confidence_score
        and not review_result.fraud_signals_detected
        and review_result.rework_count == 0
    )
```

If auto-approval criteria met:
```
REVIEW → APPROVED (skip HUMAN_APPROVAL)
```

Otherwise:
```
REVIEW → HUMAN_APPROVAL → APPROVED/REJECTED
```

## References

- CASEFILE state machine: `docs/state-machine.md`
- Human-in-the-loop patterns: https://hai.stanford.edu/news/humans-loop-design-interactive-ai-systems
- NIST AI Risk Management: https://www.nist.gov/itl/ai-risk-management-framework

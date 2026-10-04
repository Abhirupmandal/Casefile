"""
Domain ↔ database mapping boundary (Phase 6 §6).

Explicit functions translate Pydantic domain objects to SQLAlchemy rows
and back. Domain validation always runs on construction, so repositories
cannot persist invalid objects; stored rows are re-validated into domain
objects on load, so corrupt data surfaces as MappingError instead of
silently wrong objects. Money stays Decimal end to end — never float.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

if TYPE_CHECKING:
    from casefile.agents.results import AgentExecutionRecord
    from casefile.models.persistence import ToolInvocationRecord
    from casefile.tools.contracts import ToolCall

from pydantic import BaseModel, ValidationError

from casefile.checkpoint.model import (
    Checkpoint,
    CheckpointKind,
    RecordedAgentOutput,
    RecordedToolOutput,
    TransitionPathEntry,
)
from casefile.models.contracts import BudgetState, WorkflowState
from casefile.models.domain import (
    ActorType,
    ApprovalStatus,
    AuditEvent,
    AuditEventType,
    AuditResult,
    Claim,
    ClaimDocumentReference,
    ClaimPriority,
    ClaimStatus,
    ClaimType,
    CoverageLimits,
    DamageEstimate,
    DamageLineItem,
    DocumentContentType,
    DocumentExtractionStatus,
    DocumentType,
    EvidenceItem,
    EvidenceSourceType,
    FraudIndicator,
    FraudSeverity,
    HumanApproval,
    HumanApprovalRequest,
    Money,
    Policy,
    PriorClaim,
    WorkflowRun,
)
from casefile.models.persistence import (
    AgentExecutionRow,
    AuditEventRecord,
    BudgetSnapshotRecord,
    CheckpointRow,
    ClaimDocumentRecord,
    ClaimRecord,
    DamageEstimateRecord,
    DamageLineItemRecord,
    EvidenceItemRecord,
    FraudIndicatorRecord,
    HumanApprovalRecord,
    PolicyRecord,
    PriorClaimRecord,
    WorkflowRunRecord,
)
from casefile.storage.errors import PersistenceError, PersistenceErrorCode


def _mapping_error(model: str, exc: Exception) -> PersistenceError:
    return PersistenceError(
        PersistenceErrorCode.MAPPING_FAILED, f"Cannot map stored row to {model}: {exc}"
    )


def _as_aware(moment: datetime) -> datetime:
    """Restore UTC on naive datetimes from SQLite (which stores no tzinfo)."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment


def claim_to_record(claim: Claim) -> ClaimRecord:
    """Validate (by construction) and map a Claim to its row."""
    return ClaimRecord(
        claim_id=str(claim.claim_id),
        external_claim_id=claim.external_claim_id,
        policy_number=claim.policy_number,
        customer_id=claim.customer_id,
        claim_type=claim.claim_type.value,
        status=claim.status.value,
        claim_amount=claim.claim_amount.amount,
        currency=claim.claim_amount.currency,
        incident_date=claim.date_of_loss.isoformat(),
        reported_date=claim.date_reported.isoformat(),
        description=claim.description,
        priority=claim.priority.value,
        submitted_by=claim.submitted_by,
        submitted_at=claim.submitted_at,
    )


def record_to_claim(row: ClaimRecord) -> Claim:
    """Re-validate a stored row back into a Claim."""
    try:
        return Claim(
            claim_id=UUID(row.claim_id),
            external_claim_id=row.external_claim_id,
            policy_number=row.policy_number,
            customer_id=row.customer_id,
            claim_type=ClaimType(row.claim_type),
            status=ClaimStatus(row.status),
            date_of_loss=date.fromisoformat(row.incident_date),
            date_reported=date.fromisoformat(row.reported_date or row.incident_date),
            description=row.description,
            claim_amount=Money(amount=Decimal(row.claim_amount), currency=row.currency),
            priority=ClaimPriority(row.priority or "NORMAL"),
            submitted_at=_as_aware(row.submitted_at) if row.submitted_at else datetime.now(UTC),
            submitted_by=row.submitted_by or "system",
        )
    except (ValidationError, ValueError) as exc:
        raise _mapping_error("Claim", exc) from exc


def policy_to_record(policy: Policy) -> PolicyRecord:
    """Map a validated Policy to its row."""
    return PolicyRecord(
        policy_number=policy.policy_number,
        policyholder_name=policy.policyholder_name,
        policy_type=policy.policy_type,
        effective_date=policy.effective_date.isoformat(),
        expiration_date=policy.expiration_date.isoformat(),
        bodily_injury_per_person=policy.coverage.bodily_injury_per_person,
        bodily_injury_per_accident=policy.coverage.bodily_injury_per_accident,
        property_damage=policy.coverage.property_damage,
        collision_deductible=policy.coverage.collision_deductible,
        comprehensive_deductible=policy.coverage.comprehensive_deductible,
        exclusions=list(policy.exclusions),
        is_active=policy.is_active,
    )


def record_to_policy(row: PolicyRecord) -> Policy:
    """Re-validate a stored row back into a Policy."""
    try:
        return Policy(
            policy_number=row.policy_number,
            policyholder_name=row.policyholder_name,
            policy_type=row.policy_type,
            effective_date=date.fromisoformat(row.effective_date),
            expiration_date=date.fromisoformat(row.expiration_date),
            coverage=CoverageLimits(
                bodily_injury_per_person=Decimal(row.bodily_injury_per_person),
                bodily_injury_per_accident=Decimal(row.bodily_injury_per_accident),
                property_damage=Decimal(row.property_damage),
                collision_deductible=(
                    Decimal(row.collision_deductible)
                    if row.collision_deductible is not None
                    else None
                ),
                comprehensive_deductible=(
                    Decimal(row.comprehensive_deductible)
                    if row.comprehensive_deductible is not None
                    else None
                ),
            ),
            exclusions=list(row.exclusions or []),
            is_active=row.is_active,
        )
    except (ValidationError, ValueError) as exc:
        raise _mapping_error("Policy", exc) from exc


def prior_to_record(prior: PriorClaim, customer_id: str, policy_number: str) -> PriorClaimRecord:
    """Map a validated PriorClaim to its customer-scoped row."""
    return PriorClaimRecord(
        claim_reference=prior.claim_reference,
        customer_id=customer_id,
        policy_number=policy_number,
        claim_date=prior.claim_date.isoformat(),
        claim_type=prior.claim_type.value,
        amount=prior.amount.amount,
        currency=prior.amount.currency,
        status=prior.status,
        at_fault=prior.at_fault,
    )


def record_to_prior(row: PriorClaimRecord) -> PriorClaim:
    """Re-validate a stored row back into a PriorClaim."""
    try:
        return PriorClaim(
            claim_reference=row.claim_reference,
            claim_date=date.fromisoformat(row.claim_date),
            claim_type=ClaimType(row.claim_type),
            amount=Money(amount=Decimal(row.amount), currency=row.currency),
            status=row.status,
            at_fault=row.at_fault,
        )
    except (ValidationError, ValueError) as exc:
        raise _mapping_error("PriorClaim", exc) from exc


def estimate_to_records(
    estimate: DamageEstimate, claim_ref: str, estimate_ref: str
) -> tuple[DamageEstimateRecord, list[DamageLineItemRecord]]:
    """Split a validated estimate into header + line rows."""
    header = DamageEstimateRecord(
        estimate_ref=estimate_ref,
        estimate_id=str(estimate.estimate_id),
        claim_ref=claim_ref,
        source=estimate.source,
        estimate_date=estimate.estimate_date.isoformat(),
        labor_hours=estimate.labor_hours,
        declared_total=estimate.total_cost.amount,
        declared_currency=estimate.total_cost.currency,
    )
    lines = [
        DamageLineItemRecord(
            line_id=f"{estimate_ref}:{index}",
            estimate_ref=estimate_ref,
            description=item.description,
            quantity=item.quantity,
            unit_amount=item.unit_cost.amount,
            unit_currency=item.unit_cost.currency,
            total_amount=item.total_cost.amount,
            total_currency=item.total_cost.currency,
        )
        for index, item in enumerate(estimate.line_items)
    ]
    return header, lines


def records_to_estimate(
    header: DamageEstimateRecord, lines: list[DamageLineItemRecord]
) -> DamageEstimate:
    """Re-validate stored header + lines back into a DamageEstimate."""
    try:
        items = [
            DamageLineItem(
                description=row.description,
                quantity=row.quantity,
                unit_cost=Money(amount=Decimal(row.unit_amount), currency=row.unit_currency),
                total_cost=Money(amount=Decimal(row.total_amount), currency=row.total_currency),
            )
            for row in sorted(lines, key=lambda row: row.line_id)
        ]
        return DamageEstimate(
            estimate_id=UUID(header.estimate_id) if header.estimate_id else uuid4(),
            source=header.source,
            estimate_date=date.fromisoformat(header.estimate_date),
            line_items=items,
            labor_hours=header.labor_hours,
            total_cost=Money(
                amount=Decimal(header.declared_total), currency=header.declared_currency
            ),
        )
    except (ValidationError, ValueError) as exc:
        raise _mapping_error("DamageEstimate", exc) from exc


def document_to_record(
    ref: ClaimDocumentReference, claim_ref: str, content: str
) -> ClaimDocumentRecord:
    """Map document metadata + bounded content to its row."""
    return ClaimDocumentRecord(
        document_id=str(ref.document_id),
        claim_ref=claim_ref,
        document_type=ref.document_type.value,
        filename=ref.filename,
        content_type=ref.content_type.value,
        content_hash=ref.content_hash,
        content=content,
        source=ref.source,
        trusted_source=ref.trusted_source,
        ingested_at=ref.ingested_at,
        extraction_status=ref.extraction_status.value,
    )


def record_to_document(row: ClaimDocumentRecord) -> tuple[ClaimDocumentReference, str]:
    """Re-validate a stored row back into (reference, content)."""
    try:
        return (
            ClaimDocumentReference(
                document_id=UUID(row.document_id),
                document_type=DocumentType(row.document_type),
                filename=row.filename,
                content_type=DocumentContentType(row.content_type),
                content_hash=row.content_hash,
                source=row.source,
                trusted_source=row.trusted_source,
                ingested_at=(
                    _as_aware(row.ingested_at) if row.ingested_at is not None else datetime.now(UTC)
                ),
                extraction_status=DocumentExtractionStatus(row.extraction_status or "PENDING"),
            ),
            row.content,
        )
    except (ValidationError, ValueError) as exc:
        raise _mapping_error("ClaimDocumentReference", exc) from exc


def evidence_to_record(item: EvidenceItem) -> EvidenceItemRecord:
    """Map a validated evidence item to its row."""
    return EvidenceItemRecord(
        evidence_id=str(item.evidence_id),
        claim_ref=item.claim_ref,
        source=item.source,
        source_type=item.source_type.value,
        confidence=item.confidence,
        value=item.value,
        provenance=item.provenance,
        collected_at=item.collected_at,
    )


def record_to_evidence(row: EvidenceItemRecord) -> EvidenceItem:
    """Re-validate a stored row back into an EvidenceItem."""
    try:
        return EvidenceItem(
            evidence_id=UUID(row.evidence_id),
            source=row.source,
            source_type=EvidenceSourceType(row.source_type),
            claim_ref=row.claim_ref,
            confidence=row.confidence,
            value=row.value,
            provenance=row.provenance,
            collected_at=(
                _as_aware(row.collected_at) if row.collected_at is not None else datetime.now(UTC)
            ),
        )
    except (ValidationError, ValueError) as exc:
        raise _mapping_error("EvidenceItem", exc) from exc


def fraud_to_record(indicator: FraudIndicator, claim_ref: str) -> FraudIndicatorRecord:
    """Map a validated fraud indicator to its claim-scoped row."""
    return FraudIndicatorRecord(
        indicator_id=f"{claim_ref}:{indicator.indicator_type}",
        claim_ref=claim_ref,
        indicator_type=indicator.indicator_type,
        description=indicator.description,
        severity=indicator.severity.value,
    )


def record_to_fraud(row: FraudIndicatorRecord) -> FraudIndicator:
    """Re-validate a stored row back into a FraudIndicator."""
    try:
        return FraudIndicator(
            indicator_type=row.indicator_type,
            description=row.description,
            severity=FraudSeverity(row.severity),
        )
    except (ValidationError, ValueError) as exc:
        raise _mapping_error("FraudIndicator", exc) from exc


def run_to_record(run: WorkflowRun) -> WorkflowRunRecord:
    """Map a validated WorkflowRun to its row."""
    return WorkflowRunRecord(
        workflow_run_id=str(run.workflow_run_id),
        claim_id=str(run.claim_id),
        current_state=run.current_state.value,
        step_count=run.step_count,
        rework_count=run.rework_count,
        started_at=run.started_at,
        updated_at=run.updated_at,
        completed_at=run.completed_at,
        terminal_state=run.terminal_state.value if run.terminal_state else None,
        termination_reason=run.termination_reason,
        trace_id=run.trace_id,
    )


def record_to_run(row: WorkflowRunRecord) -> WorkflowRun:
    """Re-validate a stored row back into a WorkflowRun."""
    try:
        return WorkflowRun(
            workflow_run_id=UUID(row.workflow_run_id),
            claim_id=UUID(row.claim_id),
            current_state=WorkflowState(row.current_state),
            step_count=row.step_count,
            rework_count=row.rework_count,
            started_at=_as_aware(row.started_at),
            updated_at=_as_aware(row.updated_at),
            completed_at=_as_aware(row.completed_at) if row.completed_at is not None else None,
            terminal_state=WorkflowState(row.terminal_state) if row.terminal_state else None,
            termination_reason=row.termination_reason,
            trace_id=row.trace_id,
        )
    except (ValidationError, ValueError) as exc:
        raise _mapping_error("WorkflowRun", exc) from exc


def audit_to_record(event: AuditEvent) -> AuditEventRecord:
    """Map a validated AuditEvent to its row, including full payload JSON."""
    return AuditEventRecord(
        event_id=str(event.event_id),
        workflow_run_id=str(event.workflow_run_id),
        claim_id=str(event.claim_id),
        trace_id=event.trace_id,
        event_type=event.event_type.value,
        timestamp=event.timestamp,
        actor_type=event.actor_type.value,
        actor_id=event.actor_id,
        action=event.action,
        summary=event.summary,
        attributes=dict(event.attributes),
        result=event.result.value,
        error=event.error,
        from_state=event.from_state.value if event.from_state else None,
        to_state=event.to_state.value if event.to_state else None,
        correlation_id=str(event.correlation_id),
        payload_json=event.model_dump_json(),
    )


def record_to_audit(row: AuditEventRecord) -> AuditEvent:
    """Re-validate a stored row back into an AuditEvent."""
    try:
        return AuditEvent(
            event_id=UUID(row.event_id),
            workflow_run_id=UUID(row.workflow_run_id),
            claim_id=UUID(row.claim_id),
            trace_id=row.trace_id,
            event_type=AuditEventType(row.event_type),
            timestamp=_as_aware(row.timestamp),
            actor_type=ActorType(row.actor_type),
            actor_id=row.actor_id,
            action=row.action,
            summary=row.summary,
            attributes=dict(row.attributes or {}),
            result=AuditResult(row.result),
            error=row.error,
            from_state=WorkflowState(row.from_state) if row.from_state else None,
            to_state=WorkflowState(row.to_state) if row.to_state else None,
            correlation_id=UUID(row.correlation_id),
        )
    except (ValidationError, ValueError) as exc:
        raise _mapping_error("AuditEvent", exc) from exc


def approval_to_record(approval: HumanApproval) -> HumanApprovalRecord:
    """Map a validated HumanApproval to its row."""
    return HumanApprovalRecord(
        approval_id=str(approval.approval_id),
        workflow_run_id=str(approval.workflow_run_id),
        claim_id=str(approval.claim_id),
        execution_id=str(approval.execution_id) if approval.execution_id else None,
        checkpoint_id=str(approval.checkpoint_id) if approval.checkpoint_id else None,
        request_version=approval.request_version,
        decision_key=approval.decision_key,
        status=approval.status.value,
        approver_id=approval.approver_id,
        approver_name=approval.approver_name,
        approver_role=approval.approver_role,
        requested_at=approval.requested_at,
        decided_at=approval.decided_at,
        notes=approval.notes,
        is_override=approval.is_override,
    )


def record_to_approval(row: HumanApprovalRecord) -> HumanApproval:
    """Re-validate a stored row back into a HumanApproval."""
    try:
        return HumanApproval(
            approval_id=UUID(row.approval_id),
            workflow_run_id=UUID(row.workflow_run_id),
            claim_id=UUID(row.claim_id),
            execution_id=UUID(row.execution_id) if row.execution_id else None,
            checkpoint_id=UUID(row.checkpoint_id) if row.checkpoint_id else None,
            request_version=row.request_version or 1,
            decision_key=row.decision_key,
            status=ApprovalStatus(row.status),
            approver_id=row.approver_id,
            approver_name=row.approver_name,
            approver_role=row.approver_role,
            requested_at=_as_aware(row.requested_at),
            decided_at=_as_aware(row.decided_at) if row.decided_at is not None else None,
            deadline=(
                _as_aware(row.deadline) if getattr(row, "deadline", None) is not None else None  # type: ignore[arg-type]
            ),
            notes=row.notes,
            is_override=row.is_override,
        )
    except (ValidationError, ValueError) as exc:
        raise _mapping_error("HumanApproval", exc) from exc


def request_to_record(request: Any) -> HumanApprovalRecord:
    """Persist an approval request as a PENDING row with its recommendation."""
    rec = getattr(request, "recommendation", None)
    rec_json: str | None = None
    if rec is not None:
        rec_json = rec.model_dump_json() if hasattr(rec, "model_dump_json") else str(rec)
    deadline = getattr(request, "deadline", getattr(request, "decision_deadline", None))
    requested_by = getattr(request, "requested_by", "reviewer")
    required_role = getattr(request, "required_role", None)
    if required_role is not None and hasattr(required_role, "value"):
        required_role = required_role.value
    summary = getattr(request, "reviewer_summary", "")
    if not summary and rec is not None:
        summary = getattr(rec, "notes", str(rec))

    return HumanApprovalRecord(
        approval_id=str(request.approval_id),
        workflow_run_id=str(request.workflow_run_id),
        claim_id=str(request.claim_id),
        execution_id=str(request.execution_id) if getattr(request, "execution_id", None) else None,
        checkpoint_id=(
            str(request.checkpoint_id) if getattr(request, "checkpoint_id", None) else None
        ),
        request_version=getattr(request, "request_version", 1),
        status=ApprovalStatus.PENDING.value,
        requested_at=request.requested_at,
        deadline=deadline,
        requested_by=str(requested_by) if requested_by else None,
        required_role=str(required_role) if required_role else None,
        notes=summary,
        is_override=False,
        recommendation_json=rec_json,
    )


def budget_to_snapshot(budget: BudgetState, workflow_run_id: UUID) -> BudgetSnapshotRecord:
    """Map a BudgetState to a point-in-time snapshot row."""
    from uuid import uuid4

    return BudgetSnapshotRecord(
        snapshot_id=str(uuid4()),
        workflow_run_id=str(workflow_run_id),
        input_tokens_used=budget.input_tokens_used,
        output_tokens_used=budget.output_tokens_used,
        total_tokens_used=budget.total_tokens_used,
        estimated_cost_usd=budget.estimated_cost_usd,
        steps_completed=budget.steps_completed,
        tool_calls_made=budget.tool_calls_made,
        rework_count=budget.rework_count,
        is_exhausted=budget.is_exhausted,
    )


def snapshot_to_budget(row: BudgetSnapshotRecord) -> BudgetState:
    """Re-validate a stored row back into a BudgetState."""
    try:
        return BudgetState(
            input_tokens_used=row.input_tokens_used,
            output_tokens_used=row.output_tokens_used,
            total_tokens_used=row.total_tokens_used,
            estimated_cost_usd=Decimal(row.estimated_cost_usd),
            steps_completed=row.steps_completed,
            tool_calls_made=row.tool_calls_made,
            rework_count=row.rework_count,
            is_exhausted=row.is_exhausted,
        )
    except (ValidationError, ValueError) as exc:
        raise _mapping_error("BudgetState", exc) from exc


def execution_to_row(
    record: AgentExecutionRecord,
    operation: str,
    *,
    claim_id: UUID,
    workflow_run_id: UUID,
) -> AgentExecutionRow:
    """Map a Phase 4 execution record to its row. Secrets are never present
    on the record by construction, so none can reach the database."""
    return AgentExecutionRow(
        execution_id=str(record.execution_id),
        workflow_run_id=str(workflow_run_id),
        claim_id=str(claim_id),
        agent=record.agent.value,
        operation=operation,
        status="FAILURE" if record.error is not None else "SUCCESS",
        started_at=record.started_at,
        finished_at=record.finished_at,
        input_tokens=record.input_tokens,
        output_tokens=record.output_tokens,
        cost_usd=Decimal("0.0000"),
        provider_name=record.provider_name or None,
        model_name=record.model_name or None,
        attempts=record.attempts,
        error_code=record.error.code if record.error else None,
        error_message=record.error.message if record.error else None,
        correlation_id=str(record.input_correlation_id),
        idempotency_key=None,
    )


def row_to_execution_summary(row: AgentExecutionRow) -> dict[str, str]:
    """Read back execution identity metadata without reconstructing providers."""
    return {
        "execution_id": row.execution_id,
        "agent": row.agent,
        "status": row.status,
        "correlation_id": row.correlation_id,
    }


def checkpoint_to_row(checkpoint: Checkpoint) -> CheckpointRow:
    """Map a sealed Checkpoint to its append-only row."""
    from casefile.checkpoint.model import Checkpoint as CheckpointModel

    if not isinstance(checkpoint, CheckpointModel):
        raise _mapping_error("Checkpoint", ValueError("not a Checkpoint model"))
    return CheckpointRow(
        checkpoint_id=str(checkpoint.checkpoint_id),
        workflow_run_id=str(checkpoint.workflow_run_id),
        claim_id=str(checkpoint.claim_id),
        execution_id=str(checkpoint.execution_id),
        sequence_no=checkpoint.sequence_no,
        parent_checkpoint_id=(
            str(checkpoint.parent_checkpoint_id) if checkpoint.parent_checkpoint_id else None
        ),
        state=checkpoint.state.value,
        kind=checkpoint.kind.value,
        snapshot_json=checkpoint.snapshot.model_dump_json(),
        recorded_tools_json=_dump_all(checkpoint.recorded_tools),
        recorded_agents_json=_dump_all(checkpoint.recorded_agents),
        path_json=_dump_all(checkpoint.path),
        approval_json=(
            checkpoint.approval_request.model_dump_json()
            if checkpoint.approval_request is not None
            else None
        ),
        terminal_reason=checkpoint.terminal_reason,
        correlation_id=str(checkpoint.correlation_id),
        created_at=checkpoint.created_at,
        schema_version=checkpoint.schema_version,
        application_version=checkpoint.application_version,
        checksum=checkpoint.checksum,
    )


def row_to_checkpoint(row: CheckpointRow) -> Checkpoint:
    """Re-validate a stored row back into a Checkpoint (integrity unchecked).

    Step/rework counts derive from the stored snapshot: one source of truth.
    """
    # Local import: workflow.context's parent package pulls the workflow
    # graph; deferring keeps storage imports cycle-free.
    from casefile.workflow.context import WorkflowSnapshot

    try:
        snapshot = WorkflowSnapshot.model_validate_json(row.snapshot_json)
        return Checkpoint(
            checkpoint_id=UUID(row.checkpoint_id),
            claim_id=UUID(row.claim_id),
            workflow_run_id=UUID(row.workflow_run_id),
            execution_id=UUID(row.execution_id),
            sequence_no=row.sequence_no,
            parent_checkpoint_id=(
                UUID(row.parent_checkpoint_id) if row.parent_checkpoint_id else None
            ),
            state=WorkflowState(row.state),
            kind=CheckpointKind(row.kind),
            snapshot=snapshot,
            step_count=snapshot.step_count,
            rework_count=snapshot.rework_count,
            recorded_tools=tuple(
                RecordedToolOutput.model_validate(item)
                for item in _load_all(row.recorded_tools_json)
            ),
            recorded_agents=tuple(
                RecordedAgentOutput.model_validate(item)
                for item in _load_all(row.recorded_agents_json)
            ),
            path=tuple(
                TransitionPathEntry.model_validate(item) for item in _load_all(row.path_json)
            ),
            approval_request=(
                HumanApprovalRequest.model_validate_json(row.approval_json)
                if row.approval_json
                else None
            ),
            terminal_reason=row.terminal_reason,
            correlation_id=UUID(row.correlation_id),
            created_at=_as_aware(row.created_at),
            schema_version=row.schema_version,
            application_version=row.application_version,
            checksum=row.checksum,
        )
    except (ValidationError, ValueError) as exc:
        raise _mapping_error("Checkpoint", exc) from exc


def _dump_all(items: tuple[BaseModel, ...]) -> str:
    import json as _json

    return _json.dumps([item.model_dump(mode="json") for item in items], sort_keys=True)


def _load_all(payload: str) -> list[dict[str, object]]:
    import json as _json

    data = _json.loads(payload)
    if not isinstance(data, list):
        raise ValueError("checkpoint collection payload must be a JSON list")
    return data


def tool_call_to_record(call: ToolCall) -> ToolInvocationRecord:
    """Map a domain ToolCall to its durable ToolInvocationRecord row."""
    from casefile.models.persistence import ToolInvocationRecord

    return ToolInvocationRecord(
        tool_invocation_id=str(call.invocation_id),
        tool_name=call.tool_name,
        tool_version=call.tool_version,
        claim_id=str(call.claim_id),
        workflow_run_id=str(call.workflow_run_id),
        execution_id=str(call.execution_id),
        correlation_id=str(call.correlation_id),
        requesting_agent=call.requesting_agent.value,
        input_json=call.input_json,
        output_json=call.output_json,
        status=call.status.value,
        started_at=call.started_at,
        finished_at=call.finished_at,
        duration_ms=call.duration_ms,
        error_code=call.error.code.value if call.error else None,
        error_message=call.error.message if call.error else None,
        idempotency_key=call.idempotency_key,
        cost_units=0,
    )


def record_to_tool_call(row: ToolInvocationRecord) -> ToolCall:
    """Reconstruct a domain ToolCall from a stored ToolInvocationRecord row."""
    from casefile.models.domain import AgentType
    from casefile.tools.contracts import ToolCall, ToolError, ToolErrorCode, ToolStatus

    error = None
    if row.error_code:
        error = ToolError(
            code=ToolErrorCode(row.error_code),
            message=row.error_message or "",
        )
    return ToolCall(
        invocation_id=UUID(row.tool_invocation_id),
        tool_name=row.tool_name,
        tool_version=row.tool_version,
        claim_id=UUID(row.claim_id),
        workflow_run_id=UUID(row.workflow_run_id),
        execution_id=UUID(row.execution_id),
        correlation_id=UUID(row.correlation_id),
        requesting_agent=AgentType(row.requesting_agent),
        input_json=row.input_json,
        output_json=row.output_json,
        status=ToolStatus(row.status),
        started_at=_as_aware(row.started_at),
        finished_at=_as_aware(row.finished_at) if row.finished_at else None,
        duration_ms=row.duration_ms,
        error=error,
        idempotency_key=row.idempotency_key or "",
    )

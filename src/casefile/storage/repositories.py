"""
Typed repository protocols and SQLAlchemy implementations (Phase 6 §3).

Domain operations only (get/save/update/list/exists) — no Session,
Connection, Query, or SQLAlchemy model ever leaves this package. Agents
and tools receive domain objects; repositories own all ORM access.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol, cast
from uuid import UUID

from sqlalchemy import CursorResult, update
from sqlalchemy.exc import IntegrityError, NoResultFound
from sqlalchemy.orm import Session

from casefile.models.contracts import BudgetState
from casefile.models.domain import (
    AuditEvent,
    Claim,
    ClaimDocumentReference,
    DamageEstimate,
    EvidenceItem,
    FraudIndicator,
    HumanApproval,
    HumanApprovalRequest,
    Policy,
    PriorClaim,
    WorkflowRun,
)
from casefile.models.persistence import (
    AgentExecutionRow,
    AuditEventRecord,
    BudgetCounterRow,
    BudgetSnapshotRecord,
    CheckpointBudgetRow,
    ClaimDocumentRecord,
    ClaimRecord,
    DamageEstimateRecord,
    DamageLineItemRecord,
    EvidenceItemRecord,
    FraudIndicatorRecord,
    HumanApprovalRecord,
    PolicyRecord,
    PriorClaimRecord,
    RunBudgetEnvelopeRow,
    WorkflowRunRecord,
)
from casefile.storage import mappings
from casefile.storage.errors import PersistenceError, PersistenceErrorCode

if TYPE_CHECKING:
    from casefile.agents.results import AgentExecutionRecord
    from casefile.tools.contracts import ToolCall


def _not_found(entity: str, identifier: str) -> PersistenceError:
    return PersistenceError(PersistenceErrorCode.NOT_FOUND, f"{entity} {identifier!r} not found")


def _integrity(entity: str, exc: Exception) -> PersistenceError:
    return PersistenceError(
        PersistenceErrorCode.INTEGRITY_VIOLATION, f"{entity} integrity violation: {exc}"
    )


class ClaimRepository(Protocol):
    """Durable Claim aggregate boundary."""

    def save(self, claim: Claim) -> None: ...
    def get(self, claim_id: UUID) -> Claim: ...
    def exists(self, claim_id: UUID) -> bool: ...


class DocumentRepository(Protocol):
    """Durable claim-document boundary (metadata + bounded content)."""

    def save(self, ref: ClaimDocumentReference, claim_ref: str, content: str) -> None: ...
    def get(self, document_id: UUID) -> tuple[ClaimDocumentReference, str]: ...
    def list_for_claim(self, claim_ref: str) -> list[tuple[ClaimDocumentReference, str]]: ...
    def exists(self, document_id: UUID) -> bool: ...


class PolicyRepository(Protocol):
    """Durable read-model boundary for policies."""

    def save(self, policy: Policy) -> None: ...
    def get(self, policy_number: str) -> Policy: ...
    def exists(self, policy_number: str) -> bool: ...


class PriorClaimRepository(Protocol):
    """Durable customer-scoped prior-claim boundary."""

    def save(self, prior: PriorClaim, customer_id: str, policy_number: str) -> None: ...
    def list_for_customer(self, customer_id: str, limit: int = 50) -> list[PriorClaim]: ...


class DamageEstimateRepository(Protocol):
    """Durable damage-estimate boundary (header + lines atomically)."""

    def save(self, estimate: DamageEstimate, claim_ref: str, estimate_ref: str) -> None: ...
    def get(self, estimate_ref: str) -> DamageEstimate: ...
    def list_for_claim(self, claim_ref: str) -> list[str]: ...


class EvidenceRepository(Protocol):
    """Durable normalized-evidence boundary."""

    def save(self, item: EvidenceItem) -> None: ...
    def get(self, evidence_id: UUID) -> EvidenceItem: ...
    def list_for_claim(self, claim_ref: str, limit: int = 50) -> list[EvidenceItem]: ...


class FraudRepository(Protocol):
    """Durable fraud-indicator boundary scoped to one claim."""

    def save_all(self, claim_ref: str, indicators: list[FraudIndicator]) -> None: ...
    def list_for_claim(self, claim_ref: str) -> list[FraudIndicator]: ...


class WorkflowRunRepository(Protocol):
    """Durable workflow-run boundary."""

    def save(self, run: WorkflowRun) -> None: ...
    def get(self, workflow_run_id: UUID) -> WorkflowRun: ...
    def exists(self, workflow_run_id: UUID) -> bool: ...


class AgentExecutionRepository(Protocol):
    """Durable agent-execution boundary (no secrets by construction)."""

    def save(
        self, record: AgentExecutionRecord, operation: str, claim_id: UUID, workflow_run_id: UUID
    ) -> None: ...
    def get(self, execution_id: UUID) -> AgentExecutionRow: ...
    def list_for_run(self, workflow_run_id: UUID) -> list[AgentExecutionRow]: ...


class AuditRepository(Protocol):
    """Durable append-only audit boundary with deterministic ordering."""

    def append(self, event: AuditEvent) -> None: ...
    def list_for_run(self, workflow_run_id: UUID) -> list[AuditEvent]: ...
    def list_for_claim(self, claim_id: UUID) -> list[AuditEvent]: ...


class ApprovalRepository(Protocol):
    """Durable human-approval boundary (requests + decisions)."""

    def save_request(self, request: HumanApprovalRequest) -> None: ...
    def save_decision(self, approval: HumanApproval) -> None: ...
    def get(self, approval_id: UUID) -> HumanApproval: ...
    def list_for_run(self, workflow_run_id: UUID) -> list[HumanApproval]: ...
    def recommendation_json(self, approval_id: UUID) -> str | None: ...


class BudgetRepository(Protocol):
    """Durable budget-snapshot boundary (storage only, no enforcement)."""

    def save_snapshot(self, budget: BudgetState, workflow_run_id: UUID) -> str: ...
    def latest_for_run(self, workflow_run_id: UUID) -> BudgetState | None: ...
    def list_for_run(self, workflow_run_id: UUID) -> list[BudgetState]: ...


class ToolInvocationRepository(Protocol):
    """Durable tool-invocation boundary."""

    def save(self, call: ToolCall) -> None: ...
    def get(self, invocation_id: UUID) -> ToolCall: ...
    def list_for_run(self, workflow_run_id: UUID) -> list[ToolCall]: ...
    def list_for_execution(self, execution_id: UUID) -> list[ToolCall]: ...
    def list_for_claim(self, claim_id: UUID) -> list[ToolCall]: ...


WorkflowEventRepository = AuditRepository


class SqlClaimRepository:
    """SQLAlchemy ClaimRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, claim: Claim) -> None:
        row = mappings.claim_to_record(claim)
        try:
            self._session.merge(row)
            self._session.flush()
        except IntegrityError as exc:
            raise _integrity("Claim", exc) from exc

    def get(self, claim_id: UUID) -> Claim:
        row = self._session.get(ClaimRecord, str(claim_id))
        if row is None:
            raise _not_found("Claim", str(claim_id))
        return mappings.record_to_claim(row)

    def exists(self, claim_id: UUID) -> bool:
        return self._session.get(ClaimRecord, str(claim_id)) is not None


class SqlDocumentRepository:
    """SQLAlchemy DocumentRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, ref: ClaimDocumentReference, claim_ref: str, content: str) -> None:
        if len(content) > 32768:
            raise PersistenceError(
                PersistenceErrorCode.VALIDATION_FAILED, "Document content exceeds 32768 chars"
            )
        try:
            self._session.merge(mappings.document_to_record(ref, claim_ref, content))
            self._session.flush()
        except IntegrityError as exc:
            raise _integrity("ClaimDocument", exc) from exc

    def get(self, document_id: UUID) -> tuple[ClaimDocumentReference, str]:
        row = self._session.get(ClaimDocumentRecord, str(document_id))
        if row is None:
            raise _not_found("ClaimDocument", str(document_id))
        return mappings.record_to_document(row)

    def list_for_claim(self, claim_ref: str) -> list[tuple[ClaimDocumentReference, str]]:
        rows = (
            self._session.query(ClaimDocumentRecord)
            .filter_by(claim_ref=claim_ref)
            .order_by(ClaimDocumentRecord.document_id)
            .all()
        )
        return [mappings.record_to_document(row) for row in rows]

    def exists(self, document_id: UUID) -> bool:
        return self._session.get(ClaimDocumentRecord, str(document_id)) is not None


class SqlPolicyRepository:
    """SQLAlchemy PolicyRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, policy: Policy) -> None:
        try:
            self._session.merge(mappings.policy_to_record(policy))
            self._session.flush()
        except IntegrityError as exc:
            raise _integrity("Policy", exc) from exc

    def get(self, policy_number: str) -> Policy:
        row = self._session.get(PolicyRecord, policy_number.strip().upper())
        if row is None:
            raise _not_found("Policy", policy_number)
        return mappings.record_to_policy(row)

    def exists(self, policy_number: str) -> bool:
        return self._session.get(PolicyRecord, policy_number.strip().upper()) is not None


class SqlPriorClaimRepository:
    """SQLAlchemy PriorClaimRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, prior: PriorClaim, customer_id: str, policy_number: str) -> None:
        try:
            self._session.merge(mappings.prior_to_record(prior, customer_id, policy_number))
            self._session.flush()
        except IntegrityError as exc:
            raise _integrity("PriorClaim", exc) from exc

    def list_for_customer(self, customer_id: str, limit: int = 50) -> list[PriorClaim]:
        rows = (
            self._session.query(PriorClaimRecord)
            .filter_by(customer_id=customer_id)
            .order_by(PriorClaimRecord.claim_date.desc(), PriorClaimRecord.claim_reference)
            .limit(max(1, limit))
            .all()
        )
        return [mappings.record_to_prior(row) for row in rows]


class SqlDamageEstimateRepository:
    """SQLAlchemy DamageEstimateRepository (header + lines in one flush)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, estimate: DamageEstimate, claim_ref: str, estimate_ref: str) -> None:
        header, lines = mappings.estimate_to_records(estimate, claim_ref, estimate_ref)
        try:
            self._session.merge(header)
            for line in lines:
                self._session.merge(line)
            self._session.flush()
        except IntegrityError as exc:
            raise _integrity("DamageEstimate", exc) from exc

    def get(self, estimate_ref: str) -> DamageEstimate:
        header = self._session.get(DamageEstimateRecord, estimate_ref)
        if header is None:
            raise _not_found("DamageEstimate", estimate_ref)
        lines = (
            self._session.query(DamageLineItemRecord)
            .filter_by(estimate_ref=estimate_ref)
            .order_by(DamageLineItemRecord.line_id)
            .all()
        )
        return mappings.records_to_estimate(header, list(lines))

    def list_for_claim(self, claim_ref: str) -> list[str]:
        rows = (
            self._session.query(DamageEstimateRecord)
            .filter_by(claim_ref=claim_ref)
            .order_by(DamageEstimateRecord.estimate_ref)
            .all()
        )
        return [row.estimate_ref for row in rows]


class SqlEvidenceRepository:
    """SQLAlchemy EvidenceRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, item: EvidenceItem) -> None:
        try:
            self._session.merge(mappings.evidence_to_record(item))
            self._session.flush()
        except IntegrityError as exc:
            raise _integrity("EvidenceItem", exc) from exc

    def get(self, evidence_id: UUID) -> EvidenceItem:
        row = self._session.get(EvidenceItemRecord, str(evidence_id))
        if row is None:
            raise _not_found("EvidenceItem", str(evidence_id))
        return mappings.record_to_evidence(row)

    def list_for_claim(self, claim_ref: str, limit: int = 50) -> list[EvidenceItem]:
        rows = (
            self._session.query(EvidenceItemRecord)
            .filter_by(claim_ref=claim_ref)
            .order_by(EvidenceItemRecord.source_type, EvidenceItemRecord.source)
            .limit(max(1, limit))
            .all()
        )
        return [mappings.record_to_evidence(row) for row in rows]


class SqlFraudRepository:
    """SQLAlchemy FraudRepository (replace-all per claim for determinism)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save_all(self, claim_ref: str, indicators: list[FraudIndicator]) -> None:
        try:
            self._session.query(FraudIndicatorRecord).filter_by(claim_ref=claim_ref).delete()
            for indicator in indicators:
                self._session.add(mappings.fraud_to_record(indicator, claim_ref))
            self._session.flush()
        except IntegrityError as exc:
            raise _integrity("FraudIndicator", exc) from exc

    def list_for_claim(self, claim_ref: str) -> list[FraudIndicator]:
        rows = (
            self._session.query(FraudIndicatorRecord)
            .filter_by(claim_ref=claim_ref)
            .order_by(FraudIndicatorRecord.indicator_type)
            .all()
        )
        return [mappings.record_to_fraud(row) for row in rows]


class SqlWorkflowRunRepository:
    """SQLAlchemy WorkflowRunRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, run: WorkflowRun) -> None:
        try:
            self._session.merge(mappings.run_to_record(run))
            self._session.flush()
        except IntegrityError as exc:
            raise _integrity("WorkflowRun", exc) from exc

    def get(self, workflow_run_id: UUID) -> WorkflowRun:
        row = self._session.get(WorkflowRunRecord, str(workflow_run_id))
        if row is None:
            raise _not_found("WorkflowRun", str(workflow_run_id))
        return mappings.record_to_run(row)

    def exists(self, workflow_run_id: UUID) -> bool:
        return self._session.get(WorkflowRunRecord, str(workflow_run_id)) is not None


class SqlAgentExecutionRepository:
    """SQLAlchemy AgentExecutionRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(
        self, record: AgentExecutionRecord, operation: str, claim_id: UUID, workflow_run_id: UUID
    ) -> None:
        try:
            self._session.merge(
                mappings.execution_to_row(
                    record, operation, claim_id=claim_id, workflow_run_id=workflow_run_id
                )
            )
            self._session.flush()
        except IntegrityError as exc:
            raise _integrity("AgentExecution", exc) from exc

    def get(self, execution_id: UUID) -> AgentExecutionRow:
        try:
            return (
                self._session.query(AgentExecutionRow)
                .filter_by(execution_id=str(execution_id))
                .one()
            )
        except NoResultFound:
            raise _not_found("AgentExecution", str(execution_id)) from None

    def list_for_run(self, workflow_run_id: UUID) -> list[AgentExecutionRow]:
        return (
            self._session.query(AgentExecutionRow)
            .filter_by(workflow_run_id=str(workflow_run_id))
            .order_by(AgentExecutionRow.started_at, AgentExecutionRow.execution_id)
            .all()
        )


class SqlAuditRepository:
    """SQLAlchemy AuditRepository (append-only, deterministic ordering)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, event: AuditEvent) -> None:
        try:
            self._session.add(mappings.audit_to_record(event))
            self._session.flush()
        except IntegrityError as exc:
            raise _integrity("AuditEvent", exc) from exc

    def _ordered(self, **filters: str) -> list[AuditEvent]:
        rows = (
            self._session.query(AuditEventRecord)
            .filter_by(**filters)
            .order_by(AuditEventRecord.timestamp, AuditEventRecord.event_id)
            .all()
        )
        return [mappings.record_to_audit(row) for row in rows]

    def list_for_run(self, workflow_run_id: UUID) -> list[AuditEvent]:
        """History for a run in deterministic timestamp order."""
        return self._ordered(workflow_run_id=str(workflow_run_id))

    def list_for_claim(self, claim_id: UUID) -> list[AuditEvent]:
        """History for a claim in deterministic timestamp order."""
        return self._ordered(claim_id=str(claim_id))


SqlWorkflowEventRepository = SqlAuditRepository


class SqlToolInvocationRepository:
    """SQLAlchemy ToolInvocationRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(self, call: ToolCall) -> None:
        row = mappings.tool_call_to_record(call)
        try:
            self._session.merge(row)
            self._session.flush()
        except IntegrityError as exc:
            raise _integrity("ToolInvocation", exc) from exc

    def get(self, invocation_id: UUID) -> ToolCall:
        from casefile.models.persistence import ToolInvocationRecord

        row = self._session.get(ToolInvocationRecord, str(invocation_id))
        if row is None:
            raise _not_found("ToolInvocation", str(invocation_id))
        return mappings.record_to_tool_call(row)

    def list_for_run(self, workflow_run_id: UUID) -> list[ToolCall]:
        from sqlalchemy import select

        from casefile.models.persistence import ToolInvocationRecord

        stmt = (
            select(ToolInvocationRecord)
            .where(ToolInvocationRecord.workflow_run_id == str(workflow_run_id))
            .order_by(ToolInvocationRecord.started_at.asc())
        )
        rows = self._session.execute(stmt).scalars().all()
        return [mappings.record_to_tool_call(r) for r in rows]

    def list_for_execution(self, execution_id: UUID) -> list[ToolCall]:
        from sqlalchemy import select

        from casefile.models.persistence import ToolInvocationRecord

        stmt = (
            select(ToolInvocationRecord)
            .where(ToolInvocationRecord.execution_id == str(execution_id))
            .order_by(ToolInvocationRecord.started_at.asc())
        )
        rows = self._session.execute(stmt).scalars().all()
        return [mappings.record_to_tool_call(r) for r in rows]

    def list_for_claim(self, claim_id: UUID) -> list[ToolCall]:
        from sqlalchemy import select

        from casefile.models.persistence import ToolInvocationRecord

        stmt = (
            select(ToolInvocationRecord)
            .where(ToolInvocationRecord.claim_id == str(claim_id))
            .order_by(ToolInvocationRecord.started_at.asc())
        )
        rows = self._session.execute(stmt).scalars().all()
        return [mappings.record_to_tool_call(r) for r in rows]


class SqlApprovalRepository:
    """SQLAlchemy ApprovalRepository (requests + decisions)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save_request(self, request: HumanApprovalRequest) -> None:
        try:
            self._session.merge(mappings.request_to_record(request))
            self._session.flush()
        except IntegrityError as exc:
            raise _integrity("HumanApproval", exc) from exc

    def save_decision(self, approval: HumanApproval) -> None:
        try:
            self._session.merge(mappings.approval_to_record(approval))
            self._session.flush()
        except IntegrityError as exc:
            raise _integrity("HumanApproval", exc) from exc

    def get(self, approval_id: UUID) -> HumanApproval:
        row = self._session.get(HumanApprovalRecord, str(approval_id))
        if row is None:
            raise _not_found("HumanApproval", str(approval_id))
        return mappings.record_to_approval(row)

    def list_for_run(self, workflow_run_id: UUID) -> list[HumanApproval]:
        rows = (
            self._session.query(HumanApprovalRecord)
            .filter_by(workflow_run_id=str(workflow_run_id))
            .order_by(HumanApprovalRecord.requested_at, HumanApprovalRecord.approval_id)
            .all()
        )
        return [mappings.record_to_approval(row) for row in rows]

    def recommendation_json(self, approval_id: UUID) -> str | None:
        """Stored recommendation snapshot for an approval request, if any."""
        row = self._session.get(HumanApprovalRecord, str(approval_id))
        if row is None:
            raise _not_found("HumanApproval", str(approval_id))
        return row.recommendation_json


class SqlBudgetRepository:
    """SQLAlchemy BudgetRepository (snapshots only, no enforcement)."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save_snapshot(self, budget: BudgetState, workflow_run_id: UUID) -> str:
        row = mappings.budget_to_snapshot(budget, workflow_run_id)
        try:
            self._session.add(row)
            self._session.flush()
            return row.snapshot_id
        except IntegrityError as exc:
            raise _integrity("BudgetSnapshot", exc) from exc

    def _rows_for_run(self, workflow_run_id: UUID) -> list[BudgetSnapshotRecord]:
        return (
            self._session.query(BudgetSnapshotRecord)
            .filter_by(workflow_run_id=str(workflow_run_id))
            .order_by(BudgetSnapshotRecord.created_at, BudgetSnapshotRecord.snapshot_id)
            .all()
        )

    def latest_for_run(self, workflow_run_id: UUID) -> BudgetState | None:
        """Most recent snapshot, or None when the run has none."""
        rows = self._rows_for_run(workflow_run_id)
        if not rows:
            return None
        return mappings.snapshot_to_budget(rows[-1])

    def list_for_run(self, workflow_run_id: UUID) -> list[BudgetState]:
        """All snapshots in deterministic creation order."""
        return [mappings.snapshot_to_budget(row) for row in self._rows_for_run(workflow_run_id)]


class RunBudgetRepository(Protocol):
    """Immutable per-run envelope + monotonic terminal latch."""

    def save_envelope(self, workflow_run_id: UUID, envelope_json: str) -> None: ...
    def get_envelope(self, workflow_run_id: UUID) -> str: ...
    def set_terminal(self, workflow_run_id: UUID, reason: str) -> None: ...
    def get_terminal(self, workflow_run_id: UUID) -> str | None: ...


class BudgetCounterRepository(Protocol):
    """Atomic per-dimension counters for concurrency-safe reservation."""

    def init_counters(self, workflow_run_id: UUID) -> None: ...
    def reserve(self, workflow_run_id: UUID, dimension: str, limit: int) -> bool: ...
    def reserve_tokens(
        self, workflow_run_id: UUID, input_delta: int, output_delta: int, max_total: int
    ) -> bool: ...
    def get_counts(self, workflow_run_id: UUID) -> dict[str, int]: ...


class CheckpointBudgetRepository(Protocol):
    """Budget state pinned to checkpoints for resume/replay restore."""

    def save(
        self, checkpoint_id: UUID, workflow_run_id: UUID, envelope_json: str, usage_json: str
    ) -> None: ...
    def get_for_checkpoint(self, checkpoint_id: UUID) -> dict[str, str]: ...


_RESERVE_COLUMNS = {
    "steps": BudgetCounterRow.steps,
    "agent_steps": BudgetCounterRow.agent_steps,
    "tool_calls": BudgetCounterRow.tool_calls,
    "rework_cycles": BudgetCounterRow.rework_cycles,
    "agent_retries": BudgetCounterRow.agent_retries,
}


class SqlRunBudgetRepository:
    """SQLAlchemy RunBudgetRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save_envelope(self, workflow_run_id: UUID, envelope_json: str) -> None:
        try:
            self._session.merge(
                RunBudgetEnvelopeRow(
                    workflow_run_id=str(workflow_run_id),
                    envelope_json=envelope_json,
                )
            )
            self._session.flush()
        except IntegrityError as exc:
            raise _integrity("RunBudgetEnvelope", exc) from exc

    def get_envelope(self, workflow_run_id: UUID) -> str:
        row = self._session.get(RunBudgetEnvelopeRow, str(workflow_run_id))
        if row is None:
            raise _not_found("RunBudgetEnvelope", str(workflow_run_id))
        return row.envelope_json

    def set_terminal(self, workflow_run_id: UUID, reason: str) -> None:
        row = self._session.get(RunBudgetEnvelopeRow, str(workflow_run_id))
        if row is None:
            raise _not_found("RunBudgetEnvelope", str(workflow_run_id))
        if row.terminal_reason is not None:
            raise PersistenceError(
                PersistenceErrorCode.INTEGRITY_VIOLATION,
                f"Run {workflow_run_id} already terminated ({row.terminal_reason})",
            )
        row.terminal_reason = reason
        self._session.flush()

    def get_terminal(self, workflow_run_id: UUID) -> str | None:
        row = self._session.get(RunBudgetEnvelopeRow, str(workflow_run_id))
        if row is None:
            return None
        return row.terminal_reason


class SqlBudgetCounterRepository:
    """SQLAlchemy BudgetCounterRepository with single-statement claims."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def init_counters(self, workflow_run_id: UUID, wall_start: datetime) -> None:
        try:
            self._session.merge(
                BudgetCounterRow(workflow_run_id=str(workflow_run_id), wall_start=wall_start)
            )
            self._session.flush()
        except IntegrityError as exc:
            raise _integrity("BudgetCounter", exc) from exc

    def reserve(self, workflow_run_id: UUID, dimension: str, limit: int) -> bool:
        """Atomically consume one unit iff below limit. Exactly one racer wins."""
        if dimension not in _RESERVE_COLUMNS:
            raise PersistenceError(
                PersistenceErrorCode.VALIDATION_FAILED,
                f"Unknown budget dimension {dimension!r}",
            )
        column = _RESERVE_COLUMNS[dimension]
        statement = (
            update(BudgetCounterRow)
            .where(BudgetCounterRow.workflow_run_id == str(workflow_run_id))
            .where(column < limit)
            .values({column.key: column + 1})
        )
        result = cast("CursorResult[Any]", self._session.execute(statement))
        self._session.flush()
        return result.rowcount == 1

    def reserve_tokens(
        self, workflow_run_id: UUID, input_delta: int, output_delta: int, max_total: int
    ) -> bool:
        """Atomically reserve token usage iff the new total stays within limit."""
        if input_delta < 0 or output_delta < 0:
            raise PersistenceError(
                PersistenceErrorCode.VALIDATION_FAILED, "Token deltas cannot be negative"
            )
        statement = (
            update(BudgetCounterRow)
            .where(BudgetCounterRow.workflow_run_id == str(workflow_run_id))
            .where(BudgetCounterRow.total_tokens + input_delta + output_delta <= max_total)
            .values(
                {
                    "input_tokens": BudgetCounterRow.input_tokens + input_delta,
                    "output_tokens": BudgetCounterRow.output_tokens + output_delta,
                    "total_tokens": BudgetCounterRow.total_tokens + input_delta + output_delta,
                }
            )
        )
        result = cast("CursorResult[Any]", self._session.execute(statement))
        self._session.flush()
        return result.rowcount == 1

    def get_counts(self, workflow_run_id: UUID) -> dict[str, int]:
        """Current counter values (empty when uninitialized)."""
        row = self._session.get(BudgetCounterRow, str(workflow_run_id))
        if row is None:
            return {}
        return {
            "steps": row.steps,
            "agent_steps": row.agent_steps,
            "tool_calls": row.tool_calls,
            "rework_cycles": row.rework_cycles,
            "agent_retries": row.agent_retries,
            "input_tokens": row.input_tokens,
            "output_tokens": row.output_tokens,
            "total_tokens": row.total_tokens,
        }

    def add_cost(self, workflow_run_id: UUID, delta: Decimal) -> None:
        """Persist priced cost (called after record_model_call)."""
        if delta < Decimal("0.00"):
            raise PersistenceError(
                PersistenceErrorCode.VALIDATION_FAILED, "Cost delta cannot be negative"
            )
        statement = (
            update(BudgetCounterRow)
            .where(BudgetCounterRow.workflow_run_id == str(workflow_run_id))
            .values({BudgetCounterRow.cost_usd: BudgetCounterRow.cost_usd + delta})
        )
        result = cast("CursorResult[Any]", self._session.execute(statement))
        self._session.flush()
        if result.rowcount != 1:
            raise _not_found("BudgetCounter", str(workflow_run_id))

    def get_cost_and_start(self, workflow_run_id: UUID) -> tuple[Decimal, datetime | None]:
        """Durable (cost, wall_start) for restart restore."""
        from datetime import UTC

        row = self._session.get(BudgetCounterRow, str(workflow_run_id))
        if row is None:
            raise _not_found("BudgetCounter", str(workflow_run_id))
        start = row.wall_start
        if start is not None and start.tzinfo is None:
            start = start.replace(tzinfo=UTC)
        return Decimal(row.cost_usd), start


class SqlCheckpointBudgetRepository:
    """SQLAlchemy CheckpointBudgetRepository."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def save(
        self, checkpoint_id: UUID, workflow_run_id: UUID, envelope_json: str, usage_json: str
    ) -> None:
        try:
            self._session.merge(
                CheckpointBudgetRow(
                    checkpoint_id=str(checkpoint_id),
                    workflow_run_id=str(workflow_run_id),
                    envelope_json=envelope_json,
                    usage_json=usage_json,
                )
            )
            self._session.flush()
        except IntegrityError as exc:
            raise _integrity("CheckpointBudget", exc) from exc

    def get_for_checkpoint(self, checkpoint_id: UUID) -> dict[str, str]:
        """Envelope/usage JSON pinned to a checkpoint."""
        row = self._session.get(CheckpointBudgetRow, str(checkpoint_id))
        if row is None:
            raise _not_found("CheckpointBudget", str(checkpoint_id))
        return {"envelope_json": row.envelope_json, "usage_json": row.usage_json}

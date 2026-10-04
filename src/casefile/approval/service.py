"""
Approval service: atomic, idempotent human decisions (Phase 5).

All mutations run inside one UnitOfWork with conditional single-statement
UPDATEs (status must be PENDING, version must match): exactly one
concurrent decider wins; losers get deterministic conflict/stale results.
Same decision key + same verdict replays the stored outcome without new
audit or transition side effects. Terminal runs reject new requests and
decisions. Budgets and workflow transitions stay with their owners — this
service persists approval state and audit only.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import Engine, update
from sqlalchemy.orm import Session, sessionmaker

from casefile.approval.model import (
    ApprovalActor,
    ApprovalAuditRecord,
    ApprovalAuthorizer,
    ApprovalDecision,
    ApprovalError,
    ApprovalErrorCode,
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalStateMachine,
    ApprovalVerdict,
    CancelApproval,
    DecisionResult,
    ExpireApproval,
    HumanActor,
)
from casefile.checkpoint.model import Checkpoint
from casefile.models.contracts import WorkflowState
from casefile.models.domain import (
    ActorType,
    ApprovalStatus,
    AuditEvent,
    AuditEventType,
    AuditResult,
    HumanApproval,
    HumanApprovalRequest,
)
from casefile.models.persistence import HumanApprovalRecord, get_session_factory
from casefile.storage.mappings import (
    record_to_approval,
)
from casefile.storage.unit_of_work import UnitOfWork
from casefile.workflow.context import Clock, SystemClock
from casefile.workflow.triggers import Trigger

if TYPE_CHECKING:
    from casefile.observability.provider import Observability

_DECISION_STATUS = {
    ApprovalVerdict.APPROVE: ApprovalStatus.APPROVED,
    ApprovalVerdict.REJECT: ApprovalStatus.REJECTED,
}


class ApprovalService:
    """Durable human-approval boundary."""

    def __init__(
        self,
        sessions: sessionmaker[Session] | Engine,
        clock: Clock | None = None,
    ) -> None:
        if isinstance(sessions, sessionmaker):
            self._sessions = sessions
        else:
            self._sessions = get_session_factory(sessions)
        self._clock: Clock = clock or SystemClock()

    def _unit(self) -> UnitOfWork:
        return UnitOfWork(self._sessions)

    @property
    def sessions(self) -> sessionmaker[Session]:
        """Session factory for read-side companions (audit reads, tests)."""
        return self._sessions

    def now(self) -> datetime:
        """Injectable clock timestamp."""
        return self._clock.now()

    # -- requests ------------------------------------------------------

    def request_approval(
        self,
        request: HumanApprovalRequest | ApprovalRequest,
        current_state: WorkflowState | None = None,
        checkpoint_id: UUID | None = None,
        obs: Observability | None = None,
    ) -> HumanApproval:
        """Create a versioned PENDING request, idempotent per run+checkpoint.

        Terminal workflow states reject new requests. The same run and
        checkpoint returns the existing request (no duplicates).
        """
        from casefile.observability.spans import SPAN_APPROVAL_DECIDE
        from casefile.observability.tracer import maybe_span

        effective_state = (
            current_state
            if current_state is not None
            else getattr(request, "current_workflow_state", WorkflowState.HUMAN_APPROVAL)
        )
        if isinstance(effective_state, str):
            effective_state = WorkflowState(effective_state)

        domain_req = (
            request.to_domain_request() if isinstance(request, ApprovalRequest) else request
        )

        tracer = obs.tracer if obs is not None else None
        with maybe_span(tracer, SPAN_APPROVAL_DECIDE, {"casefile.operation": "request"}) as span:
            try:
                approval = self._request_inner(domain_req, effective_state, checkpoint_id)
            except Exception as exc:
                span.set_status_error(type(exc).__name__)
                raise
            span.set_attribute("casefile.approval_outcome", "requested")
            if obs is not None:
                obs.meters.record_counter(
                    "casefile.approval.decisions", 1.0, {"outcome": "requested"}
                )
            return approval

    def _request_inner(
        self,
        request: HumanApprovalRequest,
        current_state: WorkflowState,
        checkpoint_id: UUID | None,
    ) -> HumanApproval:
        """Uninstrumented request body (logic unchanged)."""
        if current_state.is_terminal:
            raise ApprovalError(
                ApprovalErrorCode.TERMINAL_RUN,
                f"Run is terminal ({current_state.value}); no new approval requests",
            )
        with self._unit() as uow:
            existing = self._find_pending(uow, request.workflow_run_id, checkpoint_id)
            if existing is not None:
                return existing
            version = self._next_version(uow, request.workflow_run_id)
            stored_request = request.model_copy(
                update={
                    "checkpoint_id": checkpoint_id,
                    "request_version": version,
                }
            )
            uow.approvals.save_request(stored_request)
            self._audit(
                uow,
                AuditEventType.HUMAN_APPROVAL_REQUESTED,
                stored_request,
                actor_id="control-plane",
                action="approval_requested",
                result=AuditResult.SUCCESS,
            )
            return self._read(uow, stored_request.approval_id)

    def request_approval_with_checkpoint(
        self,
        request: HumanApprovalRequest | ApprovalRequest,
        checkpoint: Checkpoint,
        current_state: WorkflowState,
        obs: Observability | None = None,
    ) -> tuple[HumanApproval, Checkpoint]:
        """Atomically persist the approval request and its checkpoint.

        One transaction covers both rows: the workflow can never claim
        approval-required without the request existing, and the request
        can never exist without its checkpoint. Returns (approval, stored
        checkpoint with assigned sequence).
        """
        from casefile.observability.spans import SPAN_APPROVAL_DECIDE
        from casefile.observability.tracer import maybe_span

        domain_req = (
            request.to_domain_request() if isinstance(request, ApprovalRequest) else request
        )

        tracer = obs.tracer if obs is not None else None
        with maybe_span(tracer, SPAN_APPROVAL_DECIDE, {"casefile.operation": "request"}) as span:
            try:
                approval, stored = self._request_with_checkpoint_inner(
                    domain_req, checkpoint, current_state, obs=obs
                )
            except Exception as exc:
                span.set_status_error(type(exc).__name__)
                raise
            span.set_attribute("casefile.approval_outcome", "requested")
            if obs is not None:
                obs.meters.record_counter(
                    "casefile.approval.decisions", 1.0, {"outcome": "requested"}
                )
            return approval, stored

    def _request_with_checkpoint_inner(
        self,
        request: HumanApprovalRequest,
        checkpoint: Checkpoint,
        current_state: WorkflowState,
        *,
        obs: Observability | None = None,
    ) -> tuple[HumanApproval, Checkpoint]:
        from casefile.checkpoint.repository import SqlCheckpointRepository

        if current_state.is_terminal:
            raise ApprovalError(
                ApprovalErrorCode.TERMINAL_RUN,
                f"Run is terminal ({current_state.value}); no new approval requests",
            )
        if (
            checkpoint.workflow_run_id != request.workflow_run_id
            or checkpoint.claim_id != request.claim_id
        ):
            raise ApprovalError(
                ApprovalErrorCode.NOT_FOUND,
                "Checkpoint identity does not match the approval request",
            )
        checkpointer = SqlCheckpointRepository
        with self._unit() as uow:
            existing = self._find_pending(uow, request.workflow_run_id, None)
            if existing is not None and existing.checkpoint_id is not None:
                stored = checkpointer.create_in(uow.session, checkpoint, obs=obs)
                return existing, stored
            version = self._next_version(uow, request.workflow_run_id)
            stored_request = request.model_copy(update={"request_version": version})
            uow.approvals.save_request(stored_request)
            stored_checkpoint = checkpointer.create_in(uow.session, checkpoint, obs=obs)
            patched = stored_request.model_copy(
                update={"checkpoint_id": stored_checkpoint.checkpoint_id}
            )
            uow.approvals.save_request(patched)
            self._audit(
                uow,
                AuditEventType.HUMAN_APPROVAL_REQUESTED,
                patched,
                actor_id="control-plane",
                action="approval_requested",
                result=AuditResult.SUCCESS,
            )
            return (
                self._read(uow, stored_request.approval_id),
                stored_checkpoint,
            )

    # -- decisions -----------------------------------------------------

    def decide(
        self,
        command: ApprovalDecision | None = None,
        current_state: WorkflowState | None = None,
        obs: Observability | None = None,
        *,
        approval_id: UUID | None = None,
        actor: HumanActor | ApprovalActor | None = None,
        verdict: ApprovalVerdict | None = None,
        reason: str = "",
        decision_key: str | None = None,
        expected_version: int = 1,
        correlation_id: UUID | None = None,
        workflow_run_id: UUID | None = None,
        claim_id: UUID | None = None,
    ) -> DecisionResult:
        """Apply one human decision atomically. See module docstring."""
        from casefile.observability.spans import SPAN_APPROVAL_DECIDE
        from casefile.observability.tracer import maybe_span

        if command is None:
            if approval_id is None or actor is None:
                raise ValueError("approval_id and actor must be provided")
            cmd_verdict = verdict if verdict is not None else ApprovalVerdict.APPROVE
            command = ApprovalDecision(
                approval_id=approval_id,
                actor=actor,
                verdict=cmd_verdict,
                reason=reason,
                decision_key=decision_key or uuid4().hex,
                expected_version=expected_version,
                correlation_id=correlation_id or uuid4(),
                workflow_run_id=workflow_run_id,
                claim_id=claim_id,
            )

        tracer = obs.tracer if obs is not None else None
        with maybe_span(tracer, SPAN_APPROVAL_DECIDE, {"casefile.operation": "decide"}) as span:
            try:
                result = self._decide_inner(command, current_state)
            except Exception as exc:
                span.set_status_error(type(exc).__name__)
                raise
            span.set_attribute("casefile.approval_outcome", result.outcome.value)
            if obs is not None:
                obs.meters.record_counter(
                    "casefile.approval.decisions",
                    1.0,
                    {"outcome": result.outcome.value.lower()},
                )
            return result

    def _decide_inner(
        self,
        command: ApprovalDecision,
        current_state: WorkflowState | None,
    ) -> DecisionResult:
        """Uninstrumented decision body with security, role, and invariant checks."""
        with self._unit() as uow:
            row = uow.session.get(HumanApprovalRecord, str(command.approval_id))
            if row is None:
                raise ApprovalError(
                    ApprovalErrorCode.NOT_FOUND,
                    f"Approval {command.approval_id} not found",
                )

            # Security check: wrong workflow_run_id
            if (
                command.workflow_run_id is not None
                and str(command.workflow_run_id) != row.workflow_run_id
            ):
                self._audit_attempt(
                    uow,
                    row,
                    command,
                    "wrong workflow_run_id",
                    AuditResult.FAILURE,
                )
                raise ApprovalError(
                    ApprovalErrorCode.CONFLICT,
                    f"Approval {command.approval_id} does not belong to workflow {command.workflow_run_id}",
                )

            # Security check: wrong claim_id
            if command.claim_id is not None and str(command.claim_id) != row.claim_id:
                self._audit_attempt(
                    uow,
                    row,
                    command,
                    "wrong claim_id",
                    AuditResult.FAILURE,
                )
                raise ApprovalError(
                    ApprovalErrorCode.CONFLICT,
                    f"Approval {command.approval_id} does not belong to claim {command.claim_id}",
                )

            # Authorization & role check
            try:
                ApprovalAuthorizer.authorize(
                    command.actor,
                    required_role=getattr(row, "required_role", None),
                )
            except ApprovalError as exc:
                self._audit_attempt(
                    uow,
                    row,
                    command,
                    f"unauthorized actor: {exc}",
                    AuditResult.FAILURE,
                )
                raise

            # Separation of duties: requester cannot approve
            requested_by = getattr(row, "requested_by", None)
            if requested_by and command.actor.actor_id == requested_by:
                self._audit_attempt(
                    uow,
                    row,
                    command,
                    "separation of duties violation",
                    AuditResult.FAILURE,
                )
                raise ApprovalError(
                    ApprovalErrorCode.UNAUTHORIZED,
                    f"Separation of duties violation: requester {command.actor.actor_id} cannot approve",
                )

            # Settled state check
            if row.status != ApprovalStatus.PENDING.value:
                return self._handle_settled(uow, row, command)

            if (row.request_version or 1) != command.expected_version:
                self._audit_attempt(uow, row, command, "stale version", AuditResult.FAILURE)
                raise ApprovalError(
                    ApprovalErrorCode.STALE_VERSION,
                    f"Expected version {row.request_version}, got {command.expected_version}",
                )

            if current_state is not None and current_state.is_terminal:
                raise ApprovalError(
                    ApprovalErrorCode.TERMINAL_RUN,
                    f"Run is terminal ({current_state.value}); approval cannot revive it",
                )

            target = _DECISION_STATUS[command.verdict]
            # State machine transition verification
            ApprovalStateMachine.validate_transition(ApprovalStatus(row.status), target)

            actor_role = (
                command.actor.role.value
                if hasattr(command.actor.role, "value")
                else str(command.actor.role)
            )

            row_req_at = (
                row.requested_at.replace(tzinfo=UTC)
                if row.requested_at.tzinfo is None
                else row.requested_at
            )
            now_ts = self.now()
            now_aware = now_ts.replace(tzinfo=UTC) if now_ts.tzinfo is None else now_ts
            decided_ts = max(now_aware, row_req_at)

            statement = (
                update(HumanApprovalRecord)
                .where(HumanApprovalRecord.approval_id == str(command.approval_id))
                .where(HumanApprovalRecord.status == ApprovalStatus.PENDING.value)
                .where(HumanApprovalRecord.request_version == command.expected_version)
                .execution_options(synchronize_session=False)
                .values(
                    {
                        "status": target.value,
                        "approver_id": command.actor.actor_id,
                        "approver_name": command.actor.display_name,
                        "approver_role": actor_role,
                        "decided_at": decided_ts,
                        "decision_key": command.decision_key,
                        "notes": command.reason or None,
                    }
                )
            )
            result = uow.session.execute(statement)
            uow.session.flush()
            if result.rowcount != 1:  # type: ignore[attr-defined]
                uow.session.expire_all()
                return self._handle_settled(uow, self._reload(uow, command.approval_id), command)
            uow.session.expire_all()
            decided = self._read(uow, command.approval_id)
            self._audit(
                uow,
                AuditEventType.HUMAN_APPROVAL_RECEIVED,
                decided,
                actor_id=command.actor.actor_id,
                action=f"approval_{target.value.lower()}",
                result=AuditResult.SUCCESS,
                correlation_id=command.correlation_id,
            )
            return DecisionResult(outcome=ApprovalOutcome.DECIDED, approval=decided)

    def _handle_settled(
        self,
        uow: UnitOfWork,
        row: HumanApprovalRecord,
        command: ApprovalDecision,
    ) -> DecisionResult:
        current = self._read(uow, command.approval_id)
        if row.status == ApprovalStatus.EXPIRED.value:
            self._audit_attempt(uow, row, command, "decision after expiration", AuditResult.FAILURE)
            raise ApprovalError(
                ApprovalErrorCode.CONFLICT,
                f"Approval {command.approval_id} already expired",
            )
        if row.decision_key == command.decision_key and _status_matches_verdict(
            row.status, command.verdict
        ):
            return DecisionResult(outcome=ApprovalOutcome.IDEMPOTENT_REPLAY, approval=current)
        self._audit_attempt(uow, row, command, "conflicting decision", AuditResult.FAILURE)
        raise ApprovalError(
            ApprovalErrorCode.CONFLICT,
            f"Approval {command.approval_id} already decided as {row.status}",
        )

    # -- expiry / cancellation ------------------------------------------

    def get_approval(self, approval_id: UUID) -> HumanApproval:
        with self._unit() as uow:
            return self._read(uow, approval_id)

    def expire(
        self,
        command: ExpireApproval | UUID,
        actor: HumanActor | ApprovalActor | None = None,
        reason: str = "expired",
    ) -> DecisionResult:
        """Expire a PENDING request. Never approves. PENDING-only."""
        from casefile.approval.model import ApprovalRole

        if isinstance(command, UUID):
            command = ExpireApproval(
                approval_id=command,
                actor=actor or ApprovalActor(actor_id="system-clock", role=ApprovalRole.ADMIN),
                reason=reason,
            )
        self._require_human(command.actor)
        return self._settle_pending(
            command.approval_id,
            ApprovalStatus.EXPIRED,
            AuditEventType.HUMAN_APPROVAL_EXPIRED,
            "approval_expired",
            actor_id=command.actor.actor_id,
            correlation_id=command.correlation_id,
            reason=command.reason,
        )

    def expire_if_past_due(
        self,
        approval_id: UUID,
        actor: HumanActor | ApprovalActor,
        deadline: datetime,
        reason: str = "deadline passed",
    ) -> DecisionResult | None:
        dl = deadline.replace(tzinfo=UTC) if deadline.tzinfo is None else deadline
        now_ts = self.now().replace(tzinfo=UTC) if self.now().tzinfo is None else self.now()
        if now_ts < dl:
            return None
        return self.expire(ExpireApproval(approval_id=approval_id, actor=actor, reason=reason))

    def cancel(self, command: CancelApproval) -> DecisionResult:
        """Cancel a PENDING request. Distinct from rejection — the
        CANCELLED status is preserved durably and never rewritten."""
        self._require_human(command.actor)
        return self._settle_pending(
            command.approval_id,
            ApprovalStatus.CANCELLED,
            AuditEventType.HUMAN_APPROVAL_CANCELLED,
            "approval_cancelled",
            actor_id=command.actor.actor_id,
            correlation_id=command.correlation_id,
            reason=command.reason,
        )

    def get_audit_trail(self, approval_id: UUID) -> list[ApprovalAuditRecord]:
        """Query audit trail records associated with an approval_id."""
        with self._unit() as uow:
            row = uow.session.get(HumanApprovalRecord, str(approval_id))
            if row is None:
                return []
            run_events = uow.audit.list_for_run(UUID(row.workflow_run_id))
            records: list[ApprovalAuditRecord] = []
            for ev in run_events:
                ev_attrs = ev.attributes or {}
                ev_app_id = ev_attrs.get("approval_id")
                if (
                    ev_app_id == str(approval_id)
                    or str(ev.correlation_id) == str(approval_id)
                    or str(ev.trace_id) == str(approval_id)
                ):
                    ev_type_str = (
                        ev.event_type.value
                        if hasattr(ev.event_type, "value")
                        else str(ev.event_type)
                    )
                    if "REQUESTED" in ev_type_str:
                        ev_name = "APPROVAL_REQUESTED"
                    elif "EXPIRED" in ev_type_str:
                        ev_name = "APPROVAL_EXPIRED"
                    elif "CANCELLED" in ev_type_str:
                        ev_name = "APPROVAL_CANCELLED"
                    elif (
                        "UNAUTHORIZED" in ev_type_str
                        or "REJECTED" in ev.action
                        or (hasattr(ev.result, "value") and ev.result.value == "FAILURE")
                    ):
                        ev_name = "UNAUTHORIZED_ATTEMPT"
                    else:
                        ev_name = "APPROVAL_DECIDED"
                    records.append(
                        ApprovalAuditRecord(
                            event_id=ev.event_id,
                            event_type=ev_name,
                            actor=ev.actor_id,
                            timestamp=ev.timestamp,
                            workflow_run_id=ev.workflow_run_id,
                            approval_id=approval_id,
                            correlation_id=ev.correlation_id,
                            action=ev.action,
                            result=(
                                ev.result.value if hasattr(ev.result, "value") else str(ev.result)
                            ),
                            details=dict(ev_attrs),
                        )
                    )
            return records

    def _settle_pending(
        self,
        approval_id: UUID,
        status: ApprovalStatus,
        event_type: AuditEventType,
        action: str,
        *,
        actor_id: str,
        correlation_id: UUID,
        reason: str,
    ) -> DecisionResult:
        with self._unit() as uow:
            row = uow.session.get(HumanApprovalRecord, str(approval_id))
            if row is None:
                raise ApprovalError(
                    ApprovalErrorCode.NOT_FOUND,
                    f"Approval {approval_id} not found",
                )
            if row.status != ApprovalStatus.PENDING.value:
                self._audit_attempt_row(uow, row, action, AuditResult.FAILURE)
                raise ApprovalError(
                    ApprovalErrorCode.CONFLICT,
                    f"Approval {approval_id} already decided as {row.status}",
                )
            # State machine transition verification
            ApprovalStateMachine.validate_transition(ApprovalStatus(row.status), status)

            row_req_at = (
                row.requested_at.replace(tzinfo=UTC)
                if row.requested_at.tzinfo is None
                else row.requested_at
            )
            now_ts = self.now()
            now_aware = now_ts.replace(tzinfo=UTC) if now_ts.tzinfo is None else now_ts
            decided_ts = max(now_aware, row_req_at)

            statement = (
                update(HumanApprovalRecord)
                .where(HumanApprovalRecord.approval_id == str(approval_id))
                .where(HumanApprovalRecord.status == ApprovalStatus.PENDING.value)
                .execution_options(synchronize_session=False)
                .values(
                    {
                        "status": status.value,
                        "approver_id": actor_id,
                        "approver_name": actor_id,
                        "approver_role": "HUMAN",
                        "decided_at": decided_ts,
                        "notes": reason or None,
                    }
                )
            )
            result = uow.session.execute(statement)
            uow.session.flush()
            if result.rowcount != 1:  # type: ignore[attr-defined]
                uow.session.expire_all()
                current = self._read(uow, approval_id)
                raise ApprovalError(
                    ApprovalErrorCode.CONFLICT,
                    f"Approval {approval_id} settled concurrently as {current.status.value}",
                )
            uow.session.expire_all()
            decided = self._read(uow, approval_id)
            self._audit(
                uow,
                event_type,
                decided,
                actor_id=actor_id,
                action=action,
                result=AuditResult.SUCCESS,
                correlation_id=correlation_id,
            )
            outcome = (
                ApprovalOutcome.EXPIRED
                if status == ApprovalStatus.EXPIRED
                else (
                    ApprovalOutcome.CANCELLED
                    if status == ApprovalStatus.CANCELLED
                    else ApprovalOutcome.DECIDED
                )
            )
            return DecisionResult(outcome=outcome, approval=decided)

    # -- reads ----------------------------------------------------------

    def get(self, approval_id: UUID) -> HumanApproval:
        """Load one approval or raise NOT_FOUND."""
        with self._unit() as uow:
            return self._read(uow, approval_id)

    def pending_for_run(self, workflow_run_id: UUID) -> HumanApproval | None:
        """The PENDING approval for a run, if any (at most one by construction)."""
        with self._unit() as uow:
            rows = (
                uow.session.query(HumanApprovalRecord)
                .filter_by(
                    workflow_run_id=str(workflow_run_id),
                    status=ApprovalStatus.PENDING.value,
                )
                .order_by(HumanApprovalRecord.requested_at)
                .all()
            )
            if not rows:
                return None
            from casefile.storage.mappings import record_to_approval

            return record_to_approval(rows[0])

    def list_for_run(self, workflow_run_id: UUID) -> list[HumanApproval]:
        """All approvals for a run in request order."""
        with self._unit() as uow:
            return uow.approvals.list_for_run(workflow_run_id)

    # -- workflow mapping -------------------------------------------------

    def decision_trigger(self, approval_id: UUID) -> Trigger:
        """Load a decided approval and map it onto the engine trigger.

        Used by resume-after-approval and replay: missing or undecided
        approvals fail loudly instead of fabricating a transition.
        """
        approval = self.get(approval_id)
        if approval.status not in (
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.EXPIRED,
            ApprovalStatus.CANCELLED,
        ):
            raise ApprovalError(
                ApprovalErrorCode.NOT_PENDING,
                f"Approval {approval_id} is {approval.status.value}; no transition available",
            )
        return self.trigger_for(approval.status)

    @staticmethod
    def trigger_for(status: ApprovalStatus) -> Trigger:
        """Map a terminal approval status onto the engine trigger vocabulary.

        CANCELLED maps to APPROVAL_REJECTED with the cancellation preserved
        in the approval record, audit trail, and reason — explicit, never
        silent. EXPIRED maps to APPROVAL_TIMED_OUT.
        """
        mapping = {
            ApprovalStatus.APPROVED: Trigger.APPROVAL_GRANTED,
            ApprovalStatus.REJECTED: Trigger.APPROVAL_REJECTED,
            ApprovalStatus.EXPIRED: Trigger.APPROVAL_TIMED_OUT,
            ApprovalStatus.CANCELLED: Trigger.APPROVAL_REJECTED,
        }
        try:
            return mapping[status]
        except KeyError:
            raise ApprovalError(
                ApprovalErrorCode.NOT_PENDING,
                f"Approval status {status.value} drives no workflow transition",
            ) from None

    # -- internals --------------------------------------------------------

    @staticmethod
    def _require_human(actor: HumanActor | ApprovalActor) -> None:
        ApprovalAuthorizer.authorize(actor)

    def _find_pending(
        self, uow: UnitOfWork, workflow_run_id: UUID, checkpoint_id: UUID | None
    ) -> HumanApproval | None:
        query = uow.session.query(HumanApprovalRecord).filter_by(
            workflow_run_id=str(workflow_run_id),
            status=ApprovalStatus.PENDING.value,
        )
        if checkpoint_id is not None:
            query = query.filter_by(checkpoint_id=str(checkpoint_id))
        rows = query.order_by(HumanApprovalRecord.requested_at).all()
        if not rows:
            return None

        return record_to_approval(rows[0])

    def _next_version(self, uow: UnitOfWork, workflow_run_id: UUID) -> int:
        rows = (
            uow.session.query(HumanApprovalRecord)
            .filter_by(workflow_run_id=str(workflow_run_id))
            .all()
        )
        return len(rows) + 1

    def _read(self, uow: UnitOfWork, approval_id: UUID) -> HumanApproval:
        row = uow.session.get(HumanApprovalRecord, str(approval_id))
        if row is None:
            raise ApprovalError(ApprovalErrorCode.NOT_FOUND, f"Approval {approval_id} not found")

        return record_to_approval(row)

    def _reload(self, uow: UnitOfWork, approval_id: UUID) -> HumanApprovalRecord:
        row = uow.session.get(HumanApprovalRecord, str(approval_id))
        if row is None:
            raise ApprovalError(ApprovalErrorCode.NOT_FOUND, f"Approval {approval_id} not found")
        return row

    def _audit(
        self,
        uow: UnitOfWork,
        event_type: AuditEventType,
        approval: HumanApproval | HumanApprovalRequest,
        *,
        actor_id: str,
        action: str,
        result: AuditResult,
        correlation_id: UUID | None = None,
        error: str | None = None,
        actor_type: ActorType | None = None,
    ) -> None:
        from casefile.models.domain import ActorType as _ActorType

        uow.audit.append(
            AuditEvent(
                workflow_run_id=approval.workflow_run_id,
                claim_id=approval.claim_id,
                trace_id=str(correlation_id or approval.approval_id),
                event_type=event_type,
                timestamp=self.now(),
                actor_type=actor_type or _ActorType.HUMAN,
                actor_id=actor_id,
                action=action,
                summary=f"{action} {approval.approval_id}",
                attributes={"approval_id": str(approval.approval_id)},
                result=result,
                error=error,
                correlation_id=correlation_id or approval.approval_id,
            )
        )

    def _audit_attempt(
        self,
        uow: UnitOfWork,
        row: HumanApprovalRecord,
        command: ApprovalDecision,
        detail: str,
        result: AuditResult,
    ) -> None:
        self._audit(
            uow,
            AuditEventType.HUMAN_APPROVAL_RECEIVED,
            self._read(uow, command.approval_id),
            actor_id=command.actor.actor_id,
            action=f"approval_attempt_rejected:{detail}",
            result=result,
            correlation_id=command.correlation_id,
            error=f"{detail} on {row.status}",
        )

    def _audit_attempt_row(
        self,
        uow: UnitOfWork,
        row: HumanApprovalRecord,
        action: str,
        result: AuditResult,
    ) -> None:
        self._audit(
            uow,
            AuditEventType.HUMAN_APPROVAL_RECEIVED,
            self._read(uow, UUID(row.approval_id)),
            actor_id=row.approver_id or "unknown",
            action=action,
            result=result,
            error=f"{action} on settled {row.status}",
        )


def _status_matches_verdict(status: str, verdict: ApprovalVerdict) -> bool:
    """True when a stored terminal status equals the commanded verdict."""
    expected = {
        ApprovalVerdict.APPROVE: ApprovalStatus.APPROVED.value,
        ApprovalVerdict.REJECT: ApprovalStatus.REJECTED.value,
    }[verdict]
    return status == expected

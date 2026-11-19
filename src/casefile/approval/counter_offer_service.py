"""Counter-Offer & Negotiation Service (Phase 12.4).

Provides:
- Insurer counter-offer creation with strict authoritative financial validation.
- Bounded negotiation versioning (superseded chain, max negotiation limits).
- Authenticated claimant response processing (ACCEPT, REJECT, REQUEST_REVISION).
- Separation of duties and anti-impersonation protection.
- Transactional settlement confirmation returning to insurer-side approval.
- Full immutable audit trail and telemetry.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session, sessionmaker

from casefile.approval.model import (
    ApprovalActor,
    ApprovalRole,
)
from casefile.models.contracts import (
    CounterOffer,
    CounterOfferResponseAction,
    CounterOfferSettlement,
    CounterOfferStatus,
    WorkflowState,
)
from casefile.models.domain import (
    ActorType,
    AuditEvent,
    AuditEventType,
    AuditResult,
)
from casefile.models.persistence import (
    ClaimRecord,
    HumanApprovalRecord,
    WorkflowRunRecord,
    get_session_factory,
)
from casefile.storage.unit_of_work import UnitOfWork
from casefile.workflow.context import Clock, SystemClock

if TYPE_CHECKING:
    from sqlalchemy import Engine

    from casefile.approval.service import ApprovalService
    from casefile.observability.provider import Observability

MAX_COUNTER_OFFERS_PER_CLAIM = 3


class CounterOfferErrorCode(str, Enum):
    """Typed error codes for counter-offer and negotiation failures."""

    NOT_FOUND = "NOT_FOUND"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    CONFLICT = "CONFLICT"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"
    NEGOTIATION_LIMIT_EXCEEDED = "NEGOTIATION_LIMIT_EXCEEDED"


class CounterOfferError(Exception):
    """Typed counter-offer domain exception carrying a stable error code."""

    def __init__(self, code: CounterOfferErrorCode, message: str) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code
        self.message = message


class CounterOfferService:
    """Domain service managing counter-offers and multi-turn negotiation."""

    def __init__(
        self,
        sessions: sessionmaker[Session] | Engine,
        approval_service: ApprovalService | None = None,
        clock: Clock | None = None,
    ) -> None:
        if isinstance(sessions, sessionmaker):
            self._sessions = sessions
        else:
            self._sessions = get_session_factory(sessions)
        self._approval_service = approval_service
        self._clock: Clock = clock or SystemClock()

    def _unit(self) -> UnitOfWork:
        return UnitOfWork(self._sessions)

    def now(self) -> datetime:
        return self._clock.now()

    def _record_audit(
        self,
        uow: UnitOfWork,
        event_type: AuditEventType,
        claim_id: UUID,
        workflow_run_id: UUID | None,
        actor_id: str,
        actor_type: ActorType,
        action: str,
        summary: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Record an immutable audit event for negotiation lifecycle."""
        attrs = {str(k): str(v) for k, v in (payload or {}).items()}
        w_id = workflow_run_id or claim_id
        event = AuditEvent(
            event_id=uuid4(),
            workflow_run_id=w_id,
            claim_id=claim_id,
            trace_id=uuid4().hex,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            summary=summary,
            attributes=attrs,
            result=AuditResult.SUCCESS,
            correlation_id=uuid4(),
            timestamp=self.now(),
        )
        uow.audit.append(event)

    def create_counter_offer(
        self,
        claim_id: UUID,
        actor: ApprovalActor,
        proposed_amount: Decimal,
        reduction_reason: str,
        disputed_items: list[str] | None = None,
        expires_in_days: int = 7,
        workflow_run_id: UUID | None = None,
        previously_approved_amount: Decimal | None = None,
        obs: Observability | None = None,
    ) -> CounterOffer:
        """Create a new counter-offer from an authorized insurer reviewer."""
        # 1. Authorize actor: must have human reviewer authority
        if not actor.is_human:
            raise CounterOfferError(
                CounterOfferErrorCode.UNAUTHORIZED,
                f"Actor {actor.actor_id} is not a verified human reviewer",
            )
        allowed_roles = {
            ApprovalRole.CLAIM_REVIEWER,
            ApprovalRole.SENIOR_REVIEWER,
            ApprovalRole.CLAIM_SUPERVISOR,
            ApprovalRole.ADMIN,
            ApprovalRole.HUMAN,
        }
        actor_role = (
            actor.role if isinstance(actor.role, ApprovalRole) else ApprovalRole(str(actor.role))
        )
        if actor_role not in allowed_roles:
            raise CounterOfferError(
                CounterOfferErrorCode.UNAUTHORIZED,
                f"Actor role '{actor_role.value}' is not authorized to create counter-offers",
            )

        # 2. Basic monetary validation
        if not isinstance(proposed_amount, Decimal):
            try:
                proposed_amount = Decimal(str(proposed_amount))
            except Exception as exc:
                raise CounterOfferError(
                    CounterOfferErrorCode.VALIDATION_ERROR,
                    f"Invalid proposed amount: {proposed_amount}",
                ) from exc

        if proposed_amount.is_nan() or proposed_amount.is_infinite():
            raise CounterOfferError(
                CounterOfferErrorCode.VALIDATION_ERROR,
                "Proposed amount cannot be NaN or infinite",
            )

        if proposed_amount <= Decimal("0.00"):
            raise CounterOfferError(
                CounterOfferErrorCode.VALIDATION_ERROR,
                f"Proposed amount must be positive, got {proposed_amount}",
            )

        p_exp = proposed_amount.as_tuple().exponent
        if isinstance(p_exp, int) and p_exp < -2:
            raise CounterOfferError(
                CounterOfferErrorCode.VALIDATION_ERROR,
                "Proposed amount cannot exceed 2 decimal places",
            )

        if not reduction_reason or not reduction_reason.strip():
            raise CounterOfferError(
                CounterOfferErrorCode.VALIDATION_ERROR,
                "Reduction reason is required",
            )

        # 3. Transactional validation against authoritative claim
        with self._unit() as uow:
            claim_row = uow.session.get(ClaimRecord, str(claim_id))
            if claim_row is None:
                raise CounterOfferError(
                    CounterOfferErrorCode.NOT_FOUND,
                    f"Claim {claim_id} not found",
                )

            auth_amount = Decimal(str(claim_row.claim_amount))
            if auth_amount <= Decimal("0.00"):
                raise CounterOfferError(
                    CounterOfferErrorCode.VALIDATION_ERROR,
                    f"Authoritative claim amount is invalid: {auth_amount}",
                )

            # Security invariant: proposed amount cannot exceed authoritative requested claim amount
            if proposed_amount > auth_amount:
                raise CounterOfferError(
                    CounterOfferErrorCode.VALIDATION_ERROR,
                    f"Proposed amount (${proposed_amount}) cannot exceed authoritative claim amount (${auth_amount})",
                )

            # Separation of duties: claimant or customer cannot create counter-offer for themselves
            if actor.actor_id == claim_row.customer_id:
                raise CounterOfferError(
                    CounterOfferErrorCode.FORBIDDEN,
                    f"Claimant {actor.actor_id} cannot create counter-offers for their own claim",
                )

            # Check workflow run if available
            w_id = workflow_run_id
            if w_id is None:
                # Find active workflow run for claim
                run_row = (
                    uow.session.query(WorkflowRunRecord)
                    .filter_by(claim_id=str(claim_id))
                    .order_by(WorkflowRunRecord.started_at.desc())
                    .first()
                )
                if run_row:
                    w_id = UUID(run_row.workflow_run_id)

            # Check existing counter-offers for sequence and negotiation limit
            existing_offers = uow.counter_offers.list_for_claim(claim_id)
            if len(existing_offers) >= MAX_COUNTER_OFFERS_PER_CLAIM:
                raise CounterOfferError(
                    CounterOfferErrorCode.NEGOTIATION_LIMIT_EXCEEDED,
                    f"Maximum negotiation limit ({MAX_COUNTER_OFFERS_PER_CLAIM}) reached for claim {claim_id}",
                )

            # Any offer already accepted?
            for off in existing_offers:
                if off.status == CounterOfferStatus.ACCEPTED:
                    raise CounterOfferError(
                        CounterOfferErrorCode.CONFLICT,
                        f"Claim {claim_id} already has an accepted counter-offer {off.counter_offer_id}",
                    )

            next_sequence = 1
            if existing_offers:
                next_sequence = max(off.sequence_number for off in existing_offers) + 1

            new_offer_id = uuid4()
            now_dt = self.now()
            expires_dt = now_dt + timedelta(days=expires_in_days)

            # Supersede any currently active pending offer or offer awaiting revision
            for off in existing_offers:
                if off.status == CounterOfferStatus.PENDING or (
                    off.response_action == CounterOfferResponseAction.REQUEST_REVISION
                    and off.superseded_by_offer_id is None
                ):
                    superseded_off = off.model_copy(
                        update={
                            "status": CounterOfferStatus.SUPERSEDED,
                            "superseded_by_offer_id": new_offer_id,
                        }
                    )
                    uow.counter_offers.save(superseded_off)
                    self._record_audit(
                        uow,
                        AuditEventType.COUNTER_OFFER_SUPERSEDED,
                        claim_id,
                        w_id,
                        actor.actor_id,
                        ActorType.HUMAN,
                        "counter_offer_superseded",
                        f"Offer {off.counter_offer_id} (seq {off.sequence_number}) superseded by {new_offer_id}",
                        {
                            "old_offer_id": str(off.counter_offer_id),
                            "new_offer_id": str(new_offer_id),
                        },
                    )

            # Create new offer domain object
            counter_offer = CounterOffer(
                counter_offer_id=new_offer_id,
                claim_id=claim_id,
                workflow_run_id=w_id,
                sequence_number=next_sequence,
                original_amount=auth_amount,
                previously_approved_amount=previously_approved_amount,
                proposed_amount=proposed_amount,
                currency=claim_row.currency or "USD",
                status=CounterOfferStatus.PENDING,
                reduction_reason=reduction_reason,
                disputed_items=disputed_items or [],
                created_by=actor.actor_id,
                created_at=now_dt,
                expires_at=expires_dt,
            )

            uow.counter_offers.save(counter_offer)

            self._record_audit(
                uow,
                AuditEventType.COUNTER_OFFER_CREATED,
                claim_id,
                w_id,
                actor.actor_id,
                ActorType.HUMAN,
                "counter_offer_created",
                f"Counter-offer {new_offer_id} sequence {next_sequence} created for ${proposed_amount}",
                {
                    "offer_id": str(new_offer_id),
                    "sequence_number": next_sequence,
                    "proposed_amount": str(proposed_amount),
                    "original_amount": str(auth_amount),
                    "expires_at": expires_dt.isoformat(),
                },
            )

            return counter_offer

    def respond_to_offer(
        self,
        offer_id: UUID,
        claim_id: UUID,
        action: CounterOfferResponseAction,
        claimant_id: str,
        reason: str | None = None,
        requested_amount: Decimal | None = None,
        obs: Observability | None = None,
    ) -> CounterOffer:
        """Process an authenticated claimant response to an active counter-offer."""
        if not claimant_id or not claimant_id.strip():
            raise CounterOfferError(
                CounterOfferErrorCode.UNAUTHORIZED,
                "Claimant identity cannot be empty",
            )

        with self._unit() as uow:
            # 1. Verify claim existence and claimant identity
            claim_row = uow.session.get(ClaimRecord, str(claim_id))
            if claim_row is None:
                raise CounterOfferError(
                    CounterOfferErrorCode.NOT_FOUND,
                    f"Claim {claim_id} not found",
                )

            # Security: claimant must match claim customer_id (anti-impersonation)
            if claim_row.customer_id != claimant_id:
                raise CounterOfferError(
                    CounterOfferErrorCode.FORBIDDEN,
                    f"Claimant '{claimant_id}' is not authorized to respond to claim owned by '{claim_row.customer_id}'",
                )

            # 2. Verify offer existence and ownership
            offer = uow.counter_offers.get(offer_id)
            if offer is None:
                raise CounterOfferError(
                    CounterOfferErrorCode.NOT_FOUND,
                    f"Counter-offer {offer_id} not found",
                )

            if offer.claim_id != claim_id:
                raise CounterOfferError(
                    CounterOfferErrorCode.CONFLICT,
                    f"Offer {offer_id} does not belong to claim {claim_id}",
                )

            # 3. Verify status
            if offer.status == CounterOfferStatus.SUPERSEDED:
                raise CounterOfferError(
                    CounterOfferErrorCode.SUPERSEDED,
                    f"Counter-offer {offer_id} has been superseded by a newer offer",
                )
            if offer.status == CounterOfferStatus.EXPIRED:
                raise CounterOfferError(
                    CounterOfferErrorCode.EXPIRED,
                    f"Counter-offer {offer_id} has expired",
                )
            if offer.status == CounterOfferStatus.WITHDRAWN:
                raise CounterOfferError(
                    CounterOfferErrorCode.CONFLICT,
                    f"Counter-offer {offer_id} was withdrawn by the insurer",
                )
            if offer.status != CounterOfferStatus.PENDING:
                raise CounterOfferError(
                    CounterOfferErrorCode.CONFLICT,
                    f"Counter-offer {offer_id} is not pending (current status: {offer.status.value})",
                )

            now_dt = self.now()

            # 4. Expiration check
            if offer.expires_at is not None:
                exp = (
                    offer.expires_at.replace(tzinfo=UTC)
                    if offer.expires_at.tzinfo is None
                    else offer.expires_at
                )
                now_aware = now_dt.replace(tzinfo=UTC) if now_dt.tzinfo is None else now_dt
                if now_aware > exp:
                    expired_offer = offer.model_copy(update={"status": CounterOfferStatus.EXPIRED})
                    uow.counter_offers.save(expired_offer)
                    self._record_audit(
                        uow,
                        AuditEventType.COUNTER_OFFER_EXPIRED,
                        claim_id,
                        offer.workflow_run_id,
                        claimant_id,
                        ActorType.HUMAN,
                        "counter_offer_expired",
                        f"Counter-offer {offer_id} expired at {offer.expires_at.isoformat()}",
                        {"offer_id": str(offer_id)},
                    )
                    uow.commit()
                    raise CounterOfferError(
                        CounterOfferErrorCode.EXPIRED,
                        f"Counter-offer {offer_id} has expired",
                    )

            # 5. Process response action
            if action == CounterOfferResponseAction.ACCEPT:
                # Ensure no competing offer was accepted concurrently
                for other in uow.counter_offers.list_for_claim(claim_id):
                    if (
                        other.counter_offer_id != offer_id
                        and other.status == CounterOfferStatus.ACCEPTED
                    ):
                        raise CounterOfferError(
                            CounterOfferErrorCode.CONFLICT,
                            f"Another counter-offer ({other.counter_offer_id}) has already been accepted",
                        )

                updated_offer = offer.model_copy(
                    update={
                        "status": CounterOfferStatus.ACCEPTED,
                        "responded_at": now_dt,
                        "response_action": action,
                        "response_reason": reason or "",
                        "responded_by": claimant_id,
                    }
                )
                uow.counter_offers.save(updated_offer)

                self._record_audit(
                    uow,
                    AuditEventType.COUNTER_OFFER_ACCEPTED,
                    claim_id,
                    offer.workflow_run_id,
                    claimant_id,
                    ActorType.HUMAN,
                    "counter_offer_accepted",
                    f"Claimant accepted counter-offer {offer_id} for ${offer.proposed_amount}",
                    {
                        "offer_id": str(offer_id),
                        "accepted_amount": str(offer.proposed_amount),
                        "claimant_id": claimant_id,
                    },
                )
                return updated_offer

            elif action == CounterOfferResponseAction.REJECT:
                updated_offer = offer.model_copy(
                    update={
                        "status": CounterOfferStatus.REJECTED,
                        "responded_at": now_dt,
                        "response_action": action,
                        "response_reason": reason or "",
                        "responded_by": claimant_id,
                    }
                )
                uow.counter_offers.save(updated_offer)

                self._record_audit(
                    uow,
                    AuditEventType.COUNTER_OFFER_REJECTED,
                    claim_id,
                    offer.workflow_run_id,
                    claimant_id,
                    ActorType.HUMAN,
                    "counter_offer_rejected",
                    f"Claimant rejected counter-offer {offer_id}: {reason or 'No reason provided'}",
                    {
                        "offer_id": str(offer_id),
                        "reason": reason or "",
                        "claimant_id": claimant_id,
                    },
                )
                return updated_offer

            elif action == CounterOfferResponseAction.REQUEST_REVISION:
                if not reason or not reason.strip():
                    raise CounterOfferError(
                        CounterOfferErrorCode.VALIDATION_ERROR,
                        "Reason is required when requesting revision",
                    )
                if requested_amount is not None:
                    if requested_amount <= Decimal("0.00"):
                        raise CounterOfferError(
                            CounterOfferErrorCode.VALIDATION_ERROR,
                            "Requested amount must be positive",
                        )
                    auth_amount = Decimal(str(claim_row.claim_amount))
                    if requested_amount > auth_amount:
                        raise CounterOfferError(
                            CounterOfferErrorCode.VALIDATION_ERROR,
                            f"Requested amount (${requested_amount}) cannot exceed original claim amount (${auth_amount})",
                        )

                updated_offer = offer.model_copy(
                    update={
                        "status": CounterOfferStatus.REJECTED,
                        "responded_at": now_dt,
                        "response_action": action,
                        "response_reason": reason,
                        "responded_by": claimant_id,
                    }
                )
                uow.counter_offers.save(updated_offer)

                self._record_audit(
                    uow,
                    AuditEventType.COUNTER_OFFER_REVISION_REQUESTED,
                    claim_id,
                    offer.workflow_run_id,
                    claimant_id,
                    ActorType.HUMAN,
                    "counter_offer_revision_requested",
                    f"Claimant requested revision on offer {offer_id}: {reason}",
                    {
                        "offer_id": str(offer_id),
                        "reason": reason,
                        "requested_amount": str(requested_amount) if requested_amount else None,
                        "claimant_id": claimant_id,
                    },
                )
                return updated_offer

            else:
                raise CounterOfferError(
                    CounterOfferErrorCode.VALIDATION_ERROR,
                    f"Unsupported response action: {action}",
                )

    def withdraw_offer(
        self,
        offer_id: UUID,
        claim_id: UUID,
        actor: ApprovalActor,
        reason: str = "",
        obs: Observability | None = None,
    ) -> CounterOffer:
        """Insurer reviewer withdraws a pending counter-offer before claimant acceptance."""
        allowed_roles = {
            ApprovalRole.CLAIM_REVIEWER,
            ApprovalRole.SENIOR_REVIEWER,
            ApprovalRole.CLAIM_SUPERVISOR,
            ApprovalRole.ADMIN,
            ApprovalRole.HUMAN,
        }
        actor_role = (
            actor.role if isinstance(actor.role, ApprovalRole) else ApprovalRole(str(actor.role))
        )
        if actor_role not in allowed_roles:
            raise CounterOfferError(
                CounterOfferErrorCode.UNAUTHORIZED,
                f"Actor role '{actor_role.value}' is not authorized to withdraw counter-offers",
            )

        with self._unit() as uow:
            offer = uow.counter_offers.get(offer_id)
            if offer is None:
                raise CounterOfferError(
                    CounterOfferErrorCode.NOT_FOUND,
                    f"Counter-offer {offer_id} not found",
                )
            if offer.claim_id != claim_id:
                raise CounterOfferError(
                    CounterOfferErrorCode.CONFLICT,
                    f"Offer {offer_id} does not belong to claim {claim_id}",
                )
            if offer.status != CounterOfferStatus.PENDING:
                raise CounterOfferError(
                    CounterOfferErrorCode.CONFLICT,
                    f"Cannot withdraw offer with status {offer.status.value}",
                )

            withdrawn_offer = offer.model_copy(update={"status": CounterOfferStatus.WITHDRAWN})
            uow.counter_offers.save(withdrawn_offer)

            self._record_audit(
                uow,
                AuditEventType.COUNTER_OFFER_WITHDRAWN,
                claim_id,
                offer.workflow_run_id,
                actor.actor_id,
                ActorType.HUMAN,
                "counter_offer_withdrawn",
                f"Counter-offer {offer_id} withdrawn by {actor.actor_id}: {reason or 'No reason provided'}",
                {"offer_id": str(offer_id), "reason": reason},
            )
            return withdrawn_offer

    def settle_offer(
        self,
        offer_id: UUID,
        claim_id: UUID,
        actor: ApprovalActor,
        reason: str = "",
        obs: Observability | None = None,
    ) -> CounterOfferSettlement:
        """
        Execute final insurer-side settlement confirmation for an accepted counter-offer.
        Applies partial approval through the domain approval system and transitions workflow to APPROVED.
        """
        allowed_roles = {
            ApprovalRole.CLAIM_REVIEWER,
            ApprovalRole.SENIOR_REVIEWER,
            ApprovalRole.CLAIM_SUPERVISOR,
            ApprovalRole.ADMIN,
            ApprovalRole.HUMAN,
        }
        actor_role = (
            actor.role if isinstance(actor.role, ApprovalRole) else ApprovalRole(str(actor.role))
        )
        if actor_role not in allowed_roles:
            raise CounterOfferError(
                CounterOfferErrorCode.UNAUTHORIZED,
                f"Actor role '{actor_role.value}' is not authorized to settle claims",
            )

        with self._unit() as uow:
            # 1. Authoritative checks
            claim_row = uow.session.get(ClaimRecord, str(claim_id))
            if claim_row is None:
                raise CounterOfferError(
                    CounterOfferErrorCode.NOT_FOUND,
                    f"Claim {claim_id} not found",
                )

            offer = uow.counter_offers.get(offer_id)
            if offer is None:
                raise CounterOfferError(
                    CounterOfferErrorCode.NOT_FOUND,
                    f"Counter-offer {offer_id} not found",
                )

            if offer.claim_id != claim_id:
                raise CounterOfferError(
                    CounterOfferErrorCode.CONFLICT,
                    f"Offer {offer_id} does not belong to claim {claim_id}",
                )

            if offer.status != CounterOfferStatus.ACCEPTED:
                raise CounterOfferError(
                    CounterOfferErrorCode.CONFLICT,
                    f"Cannot settle counter-offer {offer_id} because status is {offer.status.value} (must be ACCEPTED)",
                )

            # Separation of duties: reviewer settling cannot be the claimant
            if actor.actor_id == claim_row.customer_id or actor.actor_id == offer.responded_by:
                raise CounterOfferError(
                    CounterOfferErrorCode.FORBIDDEN,
                    f"Separation of duties violation: claimant {actor.actor_id} cannot authorize their own settlement",
                )

            auth_amount = Decimal(str(claim_row.claim_amount))
            settled_amount = offer.proposed_amount

            # Absolute financial cap
            if settled_amount > auth_amount:
                raise CounterOfferError(
                    CounterOfferErrorCode.VALIDATION_ERROR,
                    f"Settled amount (${settled_amount}) cannot exceed authoritative claim amount (${auth_amount})",
                )

            now_dt = self.now()
            w_id = offer.workflow_run_id or claim_id

            # Update workflow run to terminal APPROVED state
            run_row = uow.session.get(WorkflowRunRecord, str(w_id))
            if run_row and run_row.current_state in (
                WorkflowState.HUMAN_APPROVAL.value,
                "HUMAN_APPROVAL",
            ):
                run_row.current_state = WorkflowState.APPROVED.value
                run_row.termination_reason = f"COUNTER_OFFER_ACCEPTED_SETTLED (${settled_amount})"
                run_row.step_count += 1
                run_row.updated_at = now_dt
                uow.session.merge(run_row)

            # Update claim record
            claim_row.status = "APPROVED"
            uow.session.merge(claim_row)

            # Update associated human approval record if present
            app_row = (
                uow.session.query(HumanApprovalRecord)
                .filter_by(claim_id=str(claim_id))
                .order_by(HumanApprovalRecord.requested_at.desc())
                .first()
            )
            app_id: UUID | None = None
            if app_row:
                app_id = UUID(app_row.approval_id)
                app_row.status = "APPROVED"
                app_row.approver_id = actor.actor_id
                app_row.approver_name = actor.display_name or actor.actor_id
                app_row.approver_role = str(actor.role)
                app_row.decided_at = now_dt
                app_row.approved_amount = settled_amount
                app_row.notes = (
                    f"Settled counter-offer {offer_id}: {reason or 'Agreed partial settlement'}"
                )
                uow.session.merge(app_row)

            self._record_audit(
                uow,
                AuditEventType.COUNTER_OFFER_SETTLED,
                claim_id,
                w_id,
                actor.actor_id,
                ActorType.HUMAN,
                "counter_offer_settled",
                f"Settled counter-offer {offer_id} for ${settled_amount} by {actor.actor_id}",
                {
                    "offer_id": str(offer_id),
                    "settled_amount": str(settled_amount),
                    "original_amount": str(auth_amount),
                    "settled_by": actor.actor_id,
                },
            )

            return CounterOfferSettlement(
                offer_id=offer_id,
                claim_id=claim_id,
                workflow_run_id=w_id,
                settled_amount=settled_amount,
                settled_by=actor.actor_id,
                settled_at=now_dt,
                approval_id=app_id,
            )

    def get_offer(self, offer_id: UUID) -> CounterOffer | None:
        """Retrieve a specific counter-offer by ID."""
        with self._unit() as uow:
            return uow.counter_offers.get(offer_id)

    def list_for_claim(self, claim_id: UUID) -> list[CounterOffer]:
        """Retrieve all counter-offers for a claim in sequence order."""
        with self._unit() as uow:
            return uow.counter_offers.list_for_claim(claim_id)

    def get_active_for_claim(self, claim_id: UUID) -> CounterOffer | None:
        """Retrieve the currently active pending counter-offer for a claim."""
        with self._unit() as uow:
            offer = uow.counter_offers.get_active_for_claim(claim_id)
            if offer and offer.expires_at is not None:
                exp = (
                    offer.expires_at.replace(tzinfo=UTC)
                    if offer.expires_at.tzinfo is None
                    else offer.expires_at
                )
                now_aware = (
                    self.now().replace(tzinfo=UTC) if self.now().tzinfo is None else self.now()
                )
                if now_aware > exp:
                    # Dynamically mark as expired
                    expired_offer = offer.model_copy(update={"status": CounterOfferStatus.EXPIRED})
                    uow.counter_offers.save(expired_offer)
                    return None
            return offer

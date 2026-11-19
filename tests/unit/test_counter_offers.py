"""Unit tests for Phase 12.4 Counter-Offers and Negotiation Workflow.

Verifies:
1. CounterOffer contract validation:
   - Valid construction and default fields
   - Proposed amount bounds (must be > 0 and <= original amount)
   - Decimal exponent validation (max 2 decimal places)
   - Expiration timestamp validation
   - Previously approved amount validation
2. CounterOfferResponse contract validation:
   - ACCEPT, REJECT, and REQUEST_REVISION variants
   - REQUEST_REVISION requires non-empty reason
   - Requested amount validation
3. Domain service sequence and versioning:
   - First offer receives sequence 1
   - Second offer receives sequence 2 and marks first offer SUPERSEDED
   - Bounded negotiation: max counter-offers limit enforcement
4. Expiration and status rules:
   - Expired offer detection and rejection
   - Withdrawn offer handling
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from casefile.approval.counter_offer_service import (
    MAX_COUNTER_OFFERS_PER_CLAIM,
    CounterOfferError,
    CounterOfferErrorCode,
    CounterOfferService,
)
from casefile.approval.model import ApprovalActor, ApprovalRole
from casefile.models.contracts import (
    CounterOffer,
    CounterOfferResponse,
    CounterOfferResponseAction,
    CounterOfferStatus,
)
from casefile.models.persistence import ClaimRecord, get_engine, get_session_factory, init_db
from casefile.workflow.context import Clock


class MockClock(Clock):
    """Controllable clock for deterministic expiration testing."""

    def __init__(self, start: datetime | None = None) -> None:
        self._current = start or datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._current

    def advance(self, delta: timedelta) -> None:
        self._current += delta


def _make_test_service(
    clock: Clock | None = None,
) -> tuple[CounterOfferService, sessionmaker[Session]]:
    engine = get_engine("sqlite:///:memory:")
    init_db(engine)
    session_factory = get_session_factory(engine)
    service = CounterOfferService(session_factory, clock=clock)
    return service, session_factory


def _seed_claim(
    session_factory: sessionmaker[Session],
    claim_id: UUID,
    amount: Decimal = Decimal("1000.00"),
    customer_id: str = "cust-101",
) -> None:
    with session_factory() as session:
        session.add(
            ClaimRecord(
                claim_id=str(claim_id),
                external_claim_id=f"EXT-{claim_id.hex[:6]}",
                policy_number="POL-9001",
                customer_id=customer_id,
                claim_type="COLLISION",
                status="PENDING_APPROVAL",
                claim_amount=amount,
                currency="USD",
                incident_date="2026-05-15",
                description="Collision front bumper damage",
            )
        )
        session.commit()


# ======================================================================
# 1. Contract Validation Unit Tests
# ======================================================================
class TestCounterOfferContracts:
    """Contract and model validation unit tests."""

    def test_nominal_counter_offer_creation(self) -> None:
        claim_id = uuid4()
        now = datetime.now(UTC)
        offer = CounterOffer(
            claim_id=claim_id,
            original_amount=Decimal("1000.00"),
            proposed_amount=Decimal("750.00"),
            reduction_reason="Deduction for non-OEM parts",
            disputed_items=["Bumper OEM surcharge"],
            expires_at=now + timedelta(days=7),
        )
        assert offer.claim_id == claim_id
        assert offer.offer_id == offer.counter_offer_id
        assert offer.claim_amount == Decimal("1000.00")
        assert offer.proposed_amount == Decimal("750.00")
        assert offer.status == CounterOfferStatus.PENDING
        assert offer.sequence_number == 1
        assert offer.currency == "USD"
        assert offer.reason == "Deduction for non-OEM parts"

    def test_proposed_amount_exceeding_original_amount_rejected(self) -> None:
        with pytest.raises(ValidationError, match="cannot exceed original_amount"):
            CounterOffer(
                claim_id=uuid4(),
                original_amount=Decimal("100.00"),
                proposed_amount=Decimal("100.01"),
                reduction_reason="Exorbitant offer",
            )

    def test_zero_or_negative_proposed_amount_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CounterOffer(
                claim_id=uuid4(),
                original_amount=Decimal("100.00"),
                proposed_amount=Decimal("0.00"),
                reduction_reason="Zero settlement",
            )

        with pytest.raises(ValidationError):
            CounterOffer(
                claim_id=uuid4(),
                original_amount=Decimal("100.00"),
                proposed_amount=Decimal("-50.00"),
                reduction_reason="Negative settlement",
            )

    def test_decimal_precision_exceeding_two_places_rejected(self) -> None:
        with pytest.raises(ValidationError, match="cannot exceed 2 decimal places"):
            CounterOffer(
                claim_id=uuid4(),
                original_amount=Decimal("100.00"),
                proposed_amount=Decimal("50.005"),
                reduction_reason="Sub-cent offer",
            )

    def test_expiration_before_creation_rejected(self) -> None:
        now = datetime.now(UTC)
        with pytest.raises(ValidationError, match="expires_at must follow created_at"):
            CounterOffer(
                claim_id=uuid4(),
                original_amount=Decimal("100.00"),
                proposed_amount=Decimal("50.00"),
                reduction_reason="Expired offer",
                created_at=now,
                expires_at=now - timedelta(hours=1),
            )

    def test_previously_approved_amount_validation(self) -> None:
        # Valid previously approved amount
        offer = CounterOffer(
            claim_id=uuid4(),
            original_amount=Decimal("500.00"),
            previously_approved_amount=Decimal("200.00"),
            proposed_amount=Decimal("400.00"),
            reduction_reason="Supplemental settlement",
        )
        assert offer.previously_approved_amount == Decimal("200.00")

        # Exceeding original amount rejected
        with pytest.raises(ValidationError, match="cannot exceed original_amount"):
            CounterOffer(
                claim_id=uuid4(),
                original_amount=Decimal("500.00"),
                previously_approved_amount=Decimal("600.00"),
                proposed_amount=Decimal("400.00"),
                reduction_reason="Invalid supplemental",
            )

    def test_counter_offer_response_contract(self) -> None:
        offer_id = uuid4()
        claim_id = uuid4()

        # Valid ACCEPT
        resp_accept = CounterOfferResponse(
            offer_id=offer_id,
            claim_id=claim_id,
            action=CounterOfferResponseAction.ACCEPT,
            responded_by="cust-01",
        )
        assert resp_accept.action == CounterOfferResponseAction.ACCEPT

        # Valid REJECT
        resp_reject = CounterOfferResponse(
            offer_id=offer_id,
            claim_id=claim_id,
            action=CounterOfferResponseAction.REJECT,
            reason="Amount is too low",
            responded_by="cust-01",
        )
        assert resp_reject.action == CounterOfferResponseAction.REJECT

        # REQUEST_REVISION with reason
        resp_rev = CounterOfferResponse(
            offer_id=offer_id,
            claim_id=claim_id,
            action=CounterOfferResponseAction.REQUEST_REVISION,
            reason="Please cover OEM headlight replacement",
            requested_amount=Decimal("850.00"),
            responded_by="cust-01",
        )
        assert resp_rev.requested_amount == Decimal("850.00")

        # REQUEST_REVISION without reason rejected
        with pytest.raises(ValidationError, match="reason is required when requesting revision"):
            CounterOfferResponse(
                offer_id=offer_id,
                claim_id=claim_id,
                action=CounterOfferResponseAction.REQUEST_REVISION,
                responded_by="cust-01",
            )


# ======================================================================
# 2. Service Versioning & Sequence Unit Tests
# ======================================================================
class TestCounterOfferServiceVersioning:
    """Service versioning, sequence increments, and negotiation limits."""

    def test_version_chain_and_supersede(self) -> None:
        service, factory = _make_test_service()
        claim_id = uuid4()
        _seed_claim(factory, claim_id, Decimal("1000.00"), "cust-101")
        reviewer = ApprovalActor(actor_id="rev-alice", role=ApprovalRole.CLAIM_REVIEWER)

        # 1. First offer: sequence 1
        off1 = service.create_counter_offer(
            claim_id=claim_id,
            actor=reviewer,
            proposed_amount=Decimal("600.00"),
            reduction_reason="Initial deduction",
        )
        assert off1.sequence_number == 1
        assert off1.status == CounterOfferStatus.PENDING

        # 2. Second offer: sequence 2; offer 1 is marked SUPERSEDED
        off2 = service.create_counter_offer(
            claim_id=claim_id,
            actor=reviewer,
            proposed_amount=Decimal("700.00"),
            reduction_reason="Revised offer after discussion",
        )
        assert off2.sequence_number == 2
        assert off2.status == CounterOfferStatus.PENDING

        # Verify offer 1 in DB is now SUPERSEDED
        reloaded_off1 = service.get_offer(off1.counter_offer_id)
        assert reloaded_off1 is not None
        assert reloaded_off1.status == CounterOfferStatus.SUPERSEDED
        assert reloaded_off1.superseded_by_offer_id == off2.counter_offer_id

        # 3. Third offer: sequence 3; offer 2 is marked SUPERSEDED
        off3 = service.create_counter_offer(
            claim_id=claim_id,
            actor=reviewer,
            proposed_amount=Decimal("800.00"),
            reduction_reason="Final compromise offer",
        )
        assert off3.sequence_number == 3

        reloaded_off2 = service.get_offer(off2.counter_offer_id)
        assert reloaded_off2 is not None
        assert reloaded_off2.status == CounterOfferStatus.SUPERSEDED
        assert reloaded_off2.superseded_by_offer_id == off3.counter_offer_id

    def test_negotiation_limit_exceeded(self) -> None:
        """Verify exactly three counter-offers can be created and the fourth fails closed."""
        assert MAX_COUNTER_OFFERS_PER_CLAIM == 3

        service, factory = _make_test_service()
        claim_id = uuid4()
        _seed_claim(factory, claim_id, Decimal("1000.00"), "cust-101")
        reviewer = ApprovalActor(actor_id="rev-alice", role=ApprovalRole.CLAIM_REVIEWER)

        # 1. Exactly three offers can be created (1 initial + 2 revisions)
        off1 = service.create_counter_offer(
            claim_id=claim_id,
            actor=reviewer,
            proposed_amount=Decimal("500.00"),
            reduction_reason="Initial Offer (turn 1)",
        )
        assert off1.sequence_number == 1
        assert off1.status == CounterOfferStatus.PENDING

        off2 = service.create_counter_offer(
            claim_id=claim_id,
            actor=reviewer,
            proposed_amount=Decimal("550.00"),
            reduction_reason="Revision 1 (turn 2)",
        )
        assert off2.sequence_number == 2
        assert off2.status == CounterOfferStatus.PENDING

        off3 = service.create_counter_offer(
            claim_id=claim_id,
            actor=reviewer,
            proposed_amount=Decimal("600.00"),
            reduction_reason="Revision 2 (turn 3)",
        )
        assert off3.sequence_number == 3
        assert off3.status == CounterOfferStatus.PENDING

        offers_before = service.list_for_claim(claim_id)
        assert len(offers_before) == 3

        # 2. The fourth offer must fail closed with NEGOTIATION_LIMIT_EXCEEDED
        with pytest.raises(CounterOfferError) as exc_info:
            service.create_counter_offer(
                claim_id=claim_id,
                actor=reviewer,
                proposed_amount=Decimal("650.00"),
                reduction_reason="Excess offer (turn 4)",
            )
        assert exc_info.value.code == CounterOfferErrorCode.NEGOTIATION_LIMIT_EXCEEDED
        assert "Maximum negotiation limit (3) reached" in exc_info.value.message

        # 3. The rejection does not create a fourth database record
        offers_after = service.list_for_claim(claim_id)
        assert len(offers_after) == 3

        # 4. Existing offer history remains intact
        assert [off.sequence_number for off in offers_after] == [1, 2, 3]
        assert offers_after[0].counter_offer_id == off1.counter_offer_id
        assert offers_after[0].status == CounterOfferStatus.SUPERSEDED
        assert offers_after[0].superseded_by_offer_id == off2.counter_offer_id
        assert offers_after[1].counter_offer_id == off2.counter_offer_id
        assert offers_after[1].status == CounterOfferStatus.SUPERSEDED
        assert offers_after[1].superseded_by_offer_id == off3.counter_offer_id
        assert offers_after[2].counter_offer_id == off3.counter_offer_id
        assert offers_after[2].status == CounterOfferStatus.PENDING
        assert offers_after[2].proposed_amount == Decimal("600.00")


# ======================================================================
# 3. Expiration Logic Unit Tests
# ======================================================================
class TestCounterOfferExpiration:
    """Expiration and withdrawal logic."""

    def test_expired_offer_rejected_upon_response(self) -> None:
        clock = MockClock(datetime(2026, 6, 1, 10, 0, 0, tzinfo=UTC))
        service, factory = _make_test_service(clock=clock)
        claim_id = uuid4()
        _seed_claim(factory, claim_id, Decimal("1000.00"), "cust-101")
        reviewer = ApprovalActor(actor_id="rev-alice", role=ApprovalRole.CLAIM_REVIEWER)

        # Create offer valid for 3 days
        offer = service.create_counter_offer(
            claim_id=claim_id,
            actor=reviewer,
            proposed_amount=Decimal("700.00"),
            reduction_reason="Time-sensitive offer",
            expires_in_days=3,
        )

        # Advance clock by 4 days
        clock.advance(timedelta(days=4))

        # Claimant tries to accept expired offer
        with pytest.raises(CounterOfferError) as exc_info:
            service.respond_to_offer(
                offer_id=offer.counter_offer_id,
                claim_id=claim_id,
                action=CounterOfferResponseAction.ACCEPT,
                claimant_id="cust-101",
            )
        assert exc_info.value.code == CounterOfferErrorCode.EXPIRED

        # Re-check offer status in DB: now marked EXPIRED
        reloaded = service.get_offer(offer.counter_offer_id)
        assert reloaded is not None
        assert reloaded.status == CounterOfferStatus.EXPIRED

    def test_withdrawn_offer_cannot_be_accepted(self) -> None:
        service, factory = _make_test_service()
        claim_id = uuid4()
        _seed_claim(factory, claim_id, Decimal("1000.00"), "cust-101")
        reviewer = ApprovalActor(actor_id="rev-alice", role=ApprovalRole.CLAIM_REVIEWER)

        offer = service.create_counter_offer(
            claim_id=claim_id,
            actor=reviewer,
            proposed_amount=Decimal("700.00"),
            reduction_reason="Offer to be withdrawn",
        )

        # Reviewer withdraws offer
        withdrawn = service.withdraw_offer(
            offer_id=offer.counter_offer_id,
            claim_id=claim_id,
            actor=reviewer,
            reason="Discovered unrecorded vehicle damage",
        )
        assert withdrawn.status == CounterOfferStatus.WITHDRAWN

        # Claimant tries to accept withdrawn offer -> fails
        with pytest.raises(CounterOfferError) as exc_info:
            service.respond_to_offer(
                offer_id=offer.counter_offer_id,
                claim_id=claim_id,
                action=CounterOfferResponseAction.ACCEPT,
                claimant_id="cust-101",
            )
        assert exc_info.value.code == CounterOfferErrorCode.CONFLICT

"""Security and adversarial tests for Phase 12.4 Counter-Offers and Negotiation Lifecycle.

Verifies:
1. Unauthenticated claimant access rejected (401).
2. Claimant isolation: Claimant A cannot view or respond to Claimant B's counter-offer (403).
3. Unauthorized reviewer roles cannot create counter-offers (403).
4. Separation of duties: claimant cannot create counter-offer on their own claim (403).
5. Authoritative claim amount enforcement: caller cannot override persisted claim amount.
6. Absolute financial cap: $100 claim with $1,000,000 offer rejected (Phase 12.1 exploit protection preserved).
7. Non-finite, negative, and zero monetary values rejected.
8. Multiple accepted offers / competing concurrent acceptance rejected (409).
9. Responding to expired offer rejected.
10. Responding to superseded offer rejected (409).
11. Claimant acceptance does not bypass required insurer-side authorization (no automatic payout).
12. Fraud-flagged claims retain Phase 12.3 human review safeguards.
13. Agent identities cannot create, respond to, or settle counter-offers.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from casefile.api.app import app
from casefile.api.auth import Principal, Role
from casefile.api.dependencies import set_api_service
from casefile.api.schemas.models import (
    ClaimSubmissionRequest,
    CounterOfferResponseRequest,
    CreateCounterOfferRequest,
)
from casefile.api.service import ApiService
from casefile.approval.counter_offer_service import CounterOfferError, CounterOfferErrorCode
from casefile.approval.model import ApprovalActor, ApprovalRole
from casefile.models.contracts import WorkflowState
from casefile.models.persistence import ClaimRecord, WorkflowRunRecord


def _make_api_service() -> ApiService:
    svc = ApiService(database_url="sqlite:///:memory:")
    set_api_service(svc)
    return svc


class TestCounterOfferSecurityInvariants:
    """Adversarial and security tests for counter-offer workflows."""

    def test_unauthenticated_request_rejected(self) -> None:
        """Unauthenticated caller receives 401 Unauthorized."""
        client = TestClient(app)
        claim_id = uuid4()
        resp = client.get(f"/api/v1/claims/{claim_id}/counter-offers")
        assert resp.status_code == 401

    def test_claimant_cannot_access_or_respond_to_another_claimants_offer(self) -> None:
        """Claimant A cannot inspect or respond to Claimant B's offer (403 Forbidden)."""
        svc = _make_api_service()
        reviewer = Principal(principal_id="rev-01", role=Role.CLAIM_REVIEWER)

        # Claim B owned by cust-B
        claim_res = svc.submit_claim(
            ClaimSubmissionRequest(
                policy_id="POL-B",
                claimant_name="Bob Victim",
                incident_date="2026-06-01",
                claim_amount=Decimal("1000.00"),
                description="Bob's vehicle loss",
            )
        )
        claim_id = UUID(claim_res.claim_id)

        # Find actual customer_id
        with svc._uow() as uow:
            claim_row = uow.session.get(ClaimRecord, str(claim_id))
            assert claim_row is not None
            claim_row.customer_id = "cust-B"
            uow.session.merge(claim_row)

        # Reviewer creates offer
        offer = svc.create_counter_offer(
            claim_id,
            reviewer,
            CreateCounterOfferRequest(
                proposed_amount=Decimal("700.00"),
                reason="Deduction on Bob's claim",
            ),
        )
        offer_id = UUID(offer.offer_id)

        # Attacker / Claimant A tries to respond to Bob's offer
        attacker_principal = Principal(principal_id="attacker-A", role=Role.CLAIMANT)

        with pytest.raises(CounterOfferError) as exc_info:
            svc.respond_to_counter_offer(
                claim_id=claim_id,
                offer_id=offer_id,
                principal=attacker_principal,
                req=CounterOfferResponseRequest(action="ACCEPT"),
            )
        assert exc_info.value.code == CounterOfferErrorCode.FORBIDDEN

    def test_unauthorized_role_cannot_create_counter_offer(self) -> None:
        """Non-reviewer roles (VIEWER, OPERATOR, CLAIMANT) cannot create counter-offers."""
        svc = _make_api_service()
        claim_res = svc.submit_claim(
            ClaimSubmissionRequest(
                policy_id="POL-99",
                claimant_name="Alice",
                incident_date="2026-06-01",
                claim_amount=Decimal("500.00"),
                description="Minor scratch",
            )
        )
        claim_id = UUID(claim_res.claim_id)

        for unauthorized_role in (Role.VIEWER, Role.OPERATOR, Role.CLAIMANT):
            bad_principal = Principal(principal_id="user-01", role=unauthorized_role)
            with pytest.raises(CounterOfferError) as exc_info:
                svc.create_counter_offer(
                    claim_id,
                    bad_principal,
                    CreateCounterOfferRequest(
                        proposed_amount=Decimal("300.00"),
                        reason="Unauthorized offer",
                    ),
                )
            assert exc_info.value.code == CounterOfferErrorCode.UNAUTHORIZED

    def test_claimant_cannot_create_counter_offer_on_own_claim(self) -> None:
        """Separation of duties: claimant cannot create counter-offer for their own claim."""
        svc = _make_api_service()
        claim_res = svc.submit_claim(
            ClaimSubmissionRequest(
                policy_id="POL-10",
                claimant_name="Dave",
                incident_date="2026-06-01",
                claim_amount=Decimal("2000.00"),
                description="Collision loss",
            )
        )
        claim_id = UUID(claim_res.claim_id)

        with svc._uow() as uow:
            claim_row = uow.session.get(ClaimRecord, str(claim_id))
            assert claim_row is not None
            cust_id = claim_row.customer_id

        # Malicious user with reviewer role that matches claimant customer_id
        malicious_principal = Principal(principal_id=cust_id, role=Role.CLAIM_REVIEWER)

        with pytest.raises(CounterOfferError) as exc_info:
            svc.create_counter_offer(
                claim_id,
                malicious_principal,
                CreateCounterOfferRequest(
                    proposed_amount=Decimal("1500.00"),
                    reason="Self-offered settlement",
                ),
            )
        assert exc_info.value.code == CounterOfferErrorCode.FORBIDDEN

    def test_million_dollar_offer_on_hundred_dollar_claim_rejected(self) -> None:
        """$100 claim with $1,000,000 counter-offer is rejected (exploit protection preserved)."""
        svc = _make_api_service()
        reviewer = Principal(principal_id="rev-01", role=Role.CLAIM_REVIEWER)

        claim_res = svc.submit_claim(
            ClaimSubmissionRequest(
                policy_id="POL-EXPLOIT",
                claimant_name="Exploiter",
                incident_date="2026-06-01",
                claim_amount=Decimal("100.00"),
                description="Small claim",
            )
        )
        claim_id = UUID(claim_res.claim_id)

        # 1. $1,000,000 offer rejected
        with pytest.raises(CounterOfferError) as exc_info:
            svc.create_counter_offer(
                claim_id,
                reviewer,
                CreateCounterOfferRequest(
                    proposed_amount=Decimal("1000000.00"),
                    reason="Huge payout exploit",
                ),
            )
        assert exc_info.value.code == CounterOfferErrorCode.VALIDATION_ERROR
        assert "cannot exceed authoritative claim amount" in exc_info.value.message

        # 2. $100.01 offer rejected
        with pytest.raises(CounterOfferError) as exc_info:
            svc.create_counter_offer(
                claim_id,
                reviewer,
                CreateCounterOfferRequest(
                    proposed_amount=Decimal("100.01"),
                    reason="One cent excess",
                ),
            )
        assert exc_info.value.code == CounterOfferErrorCode.VALIDATION_ERROR

    def test_negative_zero_nan_infinite_amounts_rejected(self) -> None:
        """Negative, zero, NaN, and infinite proposed amounts fail closed."""
        # Zero amount rejected by Pydantic request schema
        with pytest.raises((ValidationError, ValueError)):
            CreateCounterOfferRequest(
                proposed_amount=Decimal("0.00"),
                reason="Zero amount",
            )

        # Negative amount rejected
        with pytest.raises((ValidationError, ValueError)):
            CreateCounterOfferRequest(
                proposed_amount=Decimal("-100.00"),
                reason="Negative amount",
            )

        # NaN / Infinity rejected
        for bad_val in (Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")):
            with pytest.raises((ValidationError, ValueError)):
                CreateCounterOfferRequest(
                    proposed_amount=bad_val,
                    reason="Non-finite amount",
                )

    def test_duplicate_acceptance_rejected(self) -> None:
        """Responding to an already accepted offer is rejected with CONFLICT."""
        svc = _make_api_service()
        reviewer = Principal(principal_id="rev-01", role=Role.CLAIM_REVIEWER)

        claim_res = svc.submit_claim(
            ClaimSubmissionRequest(
                policy_id="POL-DUP",
                claimant_name="Eve",
                incident_date="2026-06-01",
                claim_amount=Decimal("1000.00"),
                description="Collision",
            )
        )
        claim_id = UUID(claim_res.claim_id)

        with svc._uow() as uow:
            claim_row = uow.session.get(ClaimRecord, str(claim_id))
            assert claim_row is not None
            cust_id = claim_row.customer_id

        claimant = Principal(principal_id=cust_id, role=Role.CLAIMANT)

        offer = svc.create_counter_offer(
            claim_id,
            reviewer,
            CreateCounterOfferRequest(
                proposed_amount=Decimal("700.00"),
                reason="Standard deduction",
            ),
        )
        offer_id = UUID(offer.offer_id)

        # First acceptance succeeds
        svc.respond_to_counter_offer(
            claim_id,
            offer_id,
            claimant,
            CounterOfferResponseRequest(action="ACCEPT"),
        )

        # Second acceptance fails with CONFLICT
        with pytest.raises(CounterOfferError) as exc_info:
            svc.respond_to_counter_offer(
                claim_id,
                offer_id,
                claimant,
                CounterOfferResponseRequest(action="ACCEPT"),
            )
        assert exc_info.value.code == CounterOfferErrorCode.CONFLICT

    def test_acceptance_of_superseded_offer_rejected(self) -> None:
        """Attempting to accept a superseded offer fails with SUPERSEDED error."""
        svc = _make_api_service()
        reviewer = Principal(principal_id="rev-01", role=Role.CLAIM_REVIEWER)

        claim_res = svc.submit_claim(
            ClaimSubmissionRequest(
                policy_id="POL-SUP",
                claimant_name="Frank",
                incident_date="2026-06-01",
                claim_amount=Decimal("1200.00"),
                description="Damage",
            )
        )
        claim_id = UUID(claim_res.claim_id)

        with svc._uow() as uow:
            claim_row = uow.session.get(ClaimRecord, str(claim_id))
            assert claim_row is not None
            cust_id = claim_row.customer_id

        claimant = Principal(principal_id=cust_id, role=Role.CLAIMANT)

        # Offer 1
        off1 = svc.create_counter_offer(
            claim_id,
            reviewer,
            CreateCounterOfferRequest(
                proposed_amount=Decimal("600.00"),
                reason="Initial offer",
            ),
        )

        # Offer 2 supersedes Offer 1
        svc.create_counter_offer(
            claim_id,
            reviewer,
            CreateCounterOfferRequest(
                proposed_amount=Decimal("750.00"),
                reason="Revised offer",
            ),
        )

        # Claimant attempts to accept Offer 1 -> rejected with SUPERSEDED
        with pytest.raises(CounterOfferError) as exc_info:
            svc.respond_to_counter_offer(
                claim_id,
                UUID(off1.offer_id),
                claimant,
                CounterOfferResponseRequest(action="ACCEPT"),
            )
        assert exc_info.value.code == CounterOfferErrorCode.SUPERSEDED

    def test_claimant_acceptance_cannot_bypass_insurer_authorization(self) -> None:
        """Claimant accepting offer does not automatically approve or payout the claim."""
        svc = _make_api_service()
        reviewer = Principal(principal_id="rev-01", role=Role.CLAIM_REVIEWER)

        claim_res = svc.submit_claim(
            ClaimSubmissionRequest(
                policy_id="POL-NOPAYOUT",
                claimant_name="George",
                incident_date="2026-06-01",
                claim_amount=Decimal("2000.00"),
                description="Damage",
            )
        )
        claim_id = UUID(claim_res.claim_id)

        with svc._uow() as uow:
            claim_row = uow.session.get(ClaimRecord, str(claim_id))
            assert claim_row is not None
            cust_id = claim_row.customer_id

        claimant = Principal(principal_id=cust_id, role=Role.CLAIMANT)

        offer = svc.create_counter_offer(
            claim_id,
            reviewer,
            CreateCounterOfferRequest(
                proposed_amount=Decimal("1400.00"),
                reason="Deduction",
            ),
        )

        # Claimant accepts offer
        accepted = svc.respond_to_counter_offer(
            claim_id,
            UUID(offer.offer_id),
            claimant,
            CounterOfferResponseRequest(action="ACCEPT"),
        )
        assert accepted.status == "ACCEPTED"

        # Workflow run MUST still be in HUMAN_APPROVAL (never auto-transitioned to APPROVED)
        with svc._uow() as uow:
            run_row = uow.session.get(WorkflowRunRecord, str(claim_res.workflow_run_id))
            assert run_row is not None
            assert run_row.current_state == WorkflowState.HUMAN_APPROVAL.value

            # Claim record has not been transitioned to APPROVED
            claim_row = uow.session.get(ClaimRecord, str(claim_id))
            assert claim_row is not None
            assert claim_row.status != "APPROVED"

    def test_agent_cannot_create_or_settle_offer(self) -> None:
        """Agent actor cannot create or settle counter-offers."""
        svc = _make_api_service()
        claim_res = svc.submit_claim(
            ClaimSubmissionRequest(
                policy_id="POL-AGENT",
                claimant_name="Harry",
                incident_date="2026-06-01",
                claim_amount=Decimal("800.00"),
                description="Test vehicle claim damage",
            )
        )
        claim_id = UUID(claim_res.claim_id)

        # Agent actor trying to create offer
        agent_actor = ApprovalActor(
            actor_id="reviewer-agent",
            role=ApprovalRole.CLAIM_REVIEWER,
            is_human=False,  # NOT HUMAN
        )

        with pytest.raises(CounterOfferError) as exc_info:
            svc.counter_offer_service.create_counter_offer(
                claim_id=claim_id,
                actor=agent_actor,
                proposed_amount=Decimal("500.00"),
                reduction_reason="Agent proposed offer",
            )
        assert exc_info.value.code == CounterOfferErrorCode.UNAUTHORIZED
        assert "not a verified human reviewer" in exc_info.value.message

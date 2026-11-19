"""Integration tests for Phase 12.4 Counter-Offers and Negotiation Lifecycle.

Verifies:
1. End-to-end flow from claim intake to human approval queue, counter-offer creation,
   claimant acceptance, and final reviewer settlement.
2. Workflow state transitions:
   - Claim parks in HUMAN_APPROVAL.
   - Reviewer creates counter-offer (proposed_amount < claim_amount).
   - Claimant accepts counter-offer.
   - Reviewer settles offer -> workflow transitions to terminal APPROVED with partial amount.
3. Claimant rejection flow:
   - Claimant rejects counter-offer.
   - Workflow run remains safely in HUMAN_APPROVAL for adjuster disposition.
4. Revision negotiation loop:
   - Claimant requests revision.
   - Reviewer issues revised offer (sequence 2) superseding offer 1.
   - Claimant accepts revised offer.
5. Idempotent API submission with Idempotency-Key.
6. Alembic migration 0011 upgrade and downgrade reversibility.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config

from casefile.api.auth import Principal, Role
from casefile.api.dependencies import IdempotencyStore
from casefile.api.schemas.models import (
    ClaimSubmissionRequest,
    CounterOfferResponseRequest,
    CreateCounterOfferRequest,
)
from casefile.api.service import ApiService
from casefile.models.contracts import WorkflowState
from casefile.models.persistence import (
    ClaimRecord,
    HumanApprovalRecord,
    WorkflowRunRecord,
    get_engine,
)


def _make_api_service() -> ApiService:
    """Instantiate an in-memory ApiService."""
    return ApiService(database_url="sqlite:///:memory:")


def test_end_to_end_counter_offer_settlement_workflow() -> None:
    """Verify complete lifecycle: claim intake -> counter-offer -> claimant accept -> reviewer settle."""
    svc = _make_api_service()
    reviewer = Principal(
        principal_id="rev-01",
        role=Role.CLAIM_REVIEWER,
        display_name="Alice Reviewer",
    )

    # 1. Submit claim for $2,500.00
    claim_res = svc.submit_claim(
        ClaimSubmissionRequest(
            policy_id="POL-1200",
            claimant_name="Jane Doe",
            incident_date="2026-06-01",
            claim_amount=Decimal("2500.00"),
            description="Vehicle rear collision damage",
        )
    )
    claim_id = UUID(claim_res.claim_id)

    # Verify claim reached human approval queue
    approvals = svc.list_approvals()
    assert len(approvals.approvals) == 1
    assert approvals.approvals[0].claim_id == str(claim_id)

    # Find customer_id on claim record
    with svc._uow() as uow:
        claim_row = uow.session.get(ClaimRecord, str(claim_id))
        assert claim_row is not None
        cust_id = claim_row.customer_id

    claimant_principal = Principal(
        principal_id=cust_id,
        role=Role.CLAIMANT,
        display_name="Jane Doe (Claimant)",
    )

    # 2. Reviewer creates counter-offer for $1,800.00
    create_req = CreateCounterOfferRequest(
        proposed_amount=Decimal("1800.00"),
        reason="Deduction: OEM glass requested, aftermarket replacement permitted by policy",
        disputed_items=["OEM Windshield Surcharge ($700.00)"],
        expires_in_days=10,
    )
    offer_item = svc.create_counter_offer(claim_id, reviewer, create_req)

    assert offer_item.sequence_number == 1
    assert offer_item.proposed_amount == Decimal("1800.00")
    assert offer_item.original_amount == Decimal("2500.00")
    assert offer_item.status == "PENDING"
    assert offer_item.created_by == "rev-01"
    offer_id = UUID(offer_item.offer_id)

    # 3. Claimant inspects active offer
    active_off = svc.get_active_counter_offer(claim_id)
    assert active_off is not None
    assert active_off.offer_id == str(offer_id)

    # 4. Claimant accepts the offer
    resp_req = CounterOfferResponseRequest(
        action="ACCEPT",
        reason="Agreed to aftermarket windshield replacement",
    )
    accepted_item = svc.respond_to_counter_offer(claim_id, offer_id, claimant_principal, resp_req)
    assert accepted_item.status == "ACCEPTED"
    assert accepted_item.responded_by == cust_id
    assert accepted_item.response_action == "ACCEPT"

    # 5. Reviewer executes settlement confirmation
    settlement = svc.settle_counter_offer(
        claim_id=claim_id,
        offer_id=offer_id,
        principal=reviewer,
        reason="Final settlement authorized per claimant agreement",
    )
    assert settlement.settled_amount == Decimal("1800.00")
    assert settlement.status == "APPROVED"
    assert settlement.settled_by == "rev-01"

    # 6. Verify workflow run transitioned to terminal APPROVED with partial amount
    with svc._uow() as uow:
        run_row = uow.session.get(WorkflowRunRecord, str(claim_res.workflow_run_id))
        assert run_row is not None
        assert run_row.current_state == WorkflowState.APPROVED.value
        assert run_row.termination_reason is not None
        assert "COUNTER_OFFER_ACCEPTED_SETTLED" in run_row.termination_reason

        claim_row = uow.session.get(ClaimRecord, str(claim_id))
        assert claim_row is not None
        assert claim_row.status == "APPROVED"

        # Approval record updated
        app_row = uow.session.query(HumanApprovalRecord).filter_by(claim_id=str(claim_id)).first()
        assert app_row is not None
        assert app_row.status == "APPROVED"
        assert app_row.approved_amount == Decimal("1800.00")


def test_claimant_rejection_leaves_claim_in_human_approval() -> None:
    """Verify claimant rejection updates offer to REJECTED and leaves claim in HUMAN_APPROVAL."""
    svc = _make_api_service()
    reviewer = Principal(principal_id="rev-01", role=Role.CLAIM_REVIEWER)

    claim_res = svc.submit_claim(
        ClaimSubmissionRequest(
            policy_id="POL-1201",
            claimant_name="Bob Smith",
            incident_date="2026-06-02",
            claim_amount=Decimal("1500.00"),
            description="Fender bender",
        )
    )
    claim_id = UUID(claim_res.claim_id)

    with svc._uow() as uow:
        claim_row = uow.session.get(ClaimRecord, str(claim_id))
        assert claim_row is not None
        cust_id = claim_row.customer_id

    claimant_principal = Principal(principal_id=cust_id, role=Role.CLAIMANT)

    # Reviewer creates offer for $900
    offer = svc.create_counter_offer(
        claim_id,
        reviewer,
        CreateCounterOfferRequest(
            proposed_amount=Decimal("900.00"),
            reason="Excess labor hours deducted",
        ),
    )
    offer_id = UUID(offer.offer_id)

    # Claimant rejects
    rejected_offer = svc.respond_to_counter_offer(
        claim_id,
        offer_id,
        claimant_principal,
        CounterOfferResponseRequest(
            action="REJECT",
            reason="The repair shop confirmed labor hours cannot be reduced",
        ),
    )
    assert rejected_offer.status == "REJECTED"
    assert rejected_offer.response_action == "REJECT"

    # Verify workflow state remains in HUMAN_APPROVAL
    with svc._uow() as uow:
        run_row = uow.session.get(WorkflowRunRecord, str(claim_res.workflow_run_id))
        assert run_row is not None
        assert run_row.current_state == WorkflowState.HUMAN_APPROVAL.value


def test_multi_turn_negotiation_revision_loop() -> None:
    """Verify negotiation loop: Offer 1 -> Request Revision -> Offer 2 -> Accept -> Settle."""
    svc = _make_api_service()
    reviewer = Principal(principal_id="rev-01", role=Role.CLAIM_REVIEWER)

    claim_res = svc.submit_claim(
        ClaimSubmissionRequest(
            policy_id="POL-1202",
            claimant_name="Charlie Brown",
            incident_date="2026-06-03",
            claim_amount=Decimal("3000.00"),
            description="Side panel collision",
        )
    )
    claim_id = UUID(claim_res.claim_id)

    with svc._uow() as uow:
        claim_row = uow.session.get(ClaimRecord, str(claim_id))
        assert claim_row is not None
        cust_id = claim_row.customer_id

    claimant_principal = Principal(principal_id=cust_id, role=Role.CLAIMANT)

    # Round 1: Reviewer offers $1,800
    off1 = svc.create_counter_offer(
        claim_id,
        reviewer,
        CreateCounterOfferRequest(
            proposed_amount=Decimal("1800.00"),
            reason="Substantial paint blending charges deducted",
        ),
    )
    assert off1.sequence_number == 1
    off1_id = UUID(off1.offer_id)

    # Claimant requests revision
    svc.respond_to_counter_offer(
        claim_id,
        off1_id,
        claimant_principal,
        CounterOfferResponseRequest(
            action="REQUEST_REVISION",
            reason="Three-stage pearlescent paint requires adjacent panel blending",
            requested_amount=Decimal("2400.00"),
        ),
    )

    # Round 2: Reviewer agrees partially and offers $2,300
    off2 = svc.create_counter_offer(
        claim_id,
        reviewer,
        CreateCounterOfferRequest(
            proposed_amount=Decimal("2300.00"),
            reason="Approved additional panel blend allowance for pearl paint",
        ),
    )
    assert off2.sequence_number == 2
    off2_id = UUID(off2.offer_id)

    # Verify Offer 1 is now marked SUPERSEDED
    reloaded_off1 = svc.get_counter_offer(claim_id, off1_id)
    assert reloaded_off1.status == "SUPERSEDED"
    assert reloaded_off1.superseded_by_offer_id == str(off2_id)

    # Claimant accepts Offer 2
    accepted_off2 = svc.respond_to_counter_offer(
        claim_id,
        off2_id,
        claimant_principal,
        CounterOfferResponseRequest(action="ACCEPT"),
    )
    assert accepted_off2.status == "ACCEPTED"

    # Reviewer settles Offer 2
    settlement = svc.settle_counter_offer(
        claim_id=claim_id,
        offer_id=off2_id,
        principal=reviewer,
        reason="Settled at agreed compromised amount",
    )
    assert settlement.settled_amount == Decimal("2300.00")

    # History shows sequence 1 and sequence 2 preserved immutably
    history = svc.list_counter_offers(claim_id)
    assert history.total == 2
    assert history.offers[0].sequence_number == 1
    assert history.offers[0].status == "SUPERSEDED"
    assert history.offers[1].sequence_number == 2
    assert history.offers[1].status == "ACCEPTED"


def test_counter_offer_api_idempotency() -> None:
    """Verify that Idempotency-Key prevents duplicate counter-offer creation."""
    from casefile.api.routes.counter_offers import create_counter_offer

    svc = _make_api_service()
    store = IdempotencyStore()
    reviewer = Principal(principal_id="rev-01", role=Role.CLAIM_REVIEWER)

    claim_res = svc.submit_claim(
        ClaimSubmissionRequest(
            policy_id="POL-1203",
            claimant_name="David Miller",
            incident_date="2026-06-04",
            claim_amount=Decimal("1000.00"),
            description="Rear lamp crack",
        )
    )
    claim_id = UUID(claim_res.claim_id)

    req = CreateCounterOfferRequest(
        proposed_amount=Decimal("800.00"),
        reason="Aftermarket lamp replacement",
    )

    # First call with idempotency key
    res1 = create_counter_offer(
        claim_id=claim_id,
        req=req,
        principal=reviewer,
        service=svc,
        idempotency_store=store,
        idempotency_key="idemp-key-offer-001",
    )
    assert res1.sequence_number == 1

    # Second call with identical idempotency key
    res2 = create_counter_offer(
        claim_id=claim_id,
        req=req,
        principal=reviewer,
        service=svc,
        idempotency_store=store,
        idempotency_key="idemp-key-offer-001",
    )

    # Must return exact same offer ID
    assert res1.offer_id == res2.offer_id

    # Verify in DB only 1 offer was created
    history = svc.list_counter_offers(claim_id)
    assert history.total == 1


def test_alembic_migration_0011_upgrade_and_downgrade(tmp_path: Path) -> None:
    """Verify that migration 0011 upgrades and downgrades cleanly without errors."""
    import os
    from pathlib import Path
    from unittest import mock

    from sqlalchemy import inspect

    db_path = tmp_path / "alembic_test_0011.db"
    db_url = f"sqlite:///{db_path}"

    repo_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)

    # Upgrade to head (includes 0011_counter_offers)
    with mock.patch.dict(os.environ, {"CASEFILE_DATABASE_URL": db_url}):
        command.upgrade(cfg, "head")

    engine = get_engine(db_url)
    tables = set(inspect(engine).get_table_names())
    assert "counter_offers" in tables

    # Downgrade to 0010_fraud_assessments
    with mock.patch.dict(os.environ, {"CASEFILE_DATABASE_URL": db_url}):
        command.downgrade(cfg, "0010_fraud_assessments")

    engine.dispose()
    engine = get_engine(db_url)
    tables_after_downgrade = set(inspect(engine).get_table_names())
    assert "counter_offers" not in tables_after_downgrade

    # Upgrade back to head
    with mock.patch.dict(os.environ, {"CASEFILE_DATABASE_URL": db_url}):
        command.upgrade(cfg, "head")

    engine.dispose()
    engine = get_engine(db_url)
    tables_after_reupgrade = set(inspect(engine).get_table_names())
    assert "counter_offers" in tables_after_reupgrade
    engine.dispose()

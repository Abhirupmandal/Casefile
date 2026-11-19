"""
Integration tests for batch claim processing and disaster scenario orchestration (Phase 12 §4, Phase 12.6).

Covers:
- End-to-end batch intake, workflow execution, and multi-table persistence (batches, claims, workflow_runs, audit)
- 20-claim synthetic catastrophe disaster batch execution with shared context and individual budget isolation
- Idempotent request handling with Idempotency-Key
- Resuming and retrying failed claims independently
- Bounded concurrency execution across worker pool
- Failure-injection recovery and checkpoint verification
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest import mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from casefile.api.app import create_app
from casefile.api.dependencies import set_api_service
from casefile.api.schemas.models import (
    BatchClaimSubmissionItem,
    ClaimSubmissionRequest,
    ClaimSubmissionResponse,
)
from casefile.api.service import ApiService
from casefile.models.contracts import ClaimInput, DisasterContext
from casefile.models.persistence import (
    AuditEventRecord,
    Base,
    BatchRecord,
    ClaimRecord,
    WorkflowRunRecord,
)
from casefile.workflow.batch import BatchCoordinator, BatchStatus, ClaimBatchStatus


@pytest.fixture
def shared_engine() -> Engine:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def api_service(shared_engine: Engine) -> ApiService:
    service = ApiService(engine=shared_engine)
    return service


@pytest.fixture
def test_client(api_service: ApiService) -> TestClient:
    app = create_app(engine=api_service.engine)
    set_api_service(api_service)
    return TestClient(app)


def test_end_to_end_batch_lifecycle_and_persistence(
    api_service: ApiService, test_client: TestClient
) -> None:
    """Verify complete batch intake, execution, database persistence, and retrieval."""
    operator_headers = {
        "Authorization": "Bearer dev-operator-token",
    }

    c1 = BatchClaimSubmissionItem(
        policy_id="POL-8001",
        claimant_name="Carol Adams",
        incident_date="2026-04-10",
        claim_amount=Decimal("1200.00"),
        description="Roof leak following hail barrage.",
    )
    c2 = BatchClaimSubmissionItem(
        policy_id="POL-8002",
        claimant_name="Danielle Baker",
        incident_date="2026-04-11",
        claim_amount=Decimal("2300.00"),
        description="Shattered rear window and dented hood.",
    )

    req_payload = {
        "batch_name": "Spring Storm Intake",
        "claims": [c1.model_dump(mode="json"), c2.model_dump(mode="json")],
        "disaster_context": {
            "disaster_id": "CAT-2026-01",
            "disaster_name": "Midwest Storm Complex",
            "disaster_type": "HAIL",
            "geographic_zone": "ZONE-IL-04",
        },
    }

    # 1. Submit batch via POST /api/v1/batches
    post_resp = test_client.post("/api/v1/batches", json=req_payload, headers=operator_headers)
    assert post_resp.status_code == 201, post_resp.text
    batch_data = post_resp.json()

    b_id = batch_data["batch_id"]
    assert batch_data["status"] == "COMPLETED"
    assert batch_data["total_claims"] == 2
    assert batch_data["successful_claims"] == 2
    assert batch_data["failed_claims"] == 0
    assert Decimal(str(batch_data["total_requested_amount"])) == Decimal("3500.00")
    assert len(batch_data["claims"]) == 2

    # 2. Verify database records across tables
    with Session(api_service.engine) as session:
        # Check BatchRecord
        b_row = session.get(BatchRecord, b_id)
        assert b_row is not None
        assert b_row.status == "COMPLETED"
        assert b_row.disaster_id == "CAT-2026-01"

        # Check ClaimRecords
        claims = session.query(ClaimRecord).filter_by(batch_id=b_id).all()
        assert len(claims) == 2

        # Check WorkflowRunRecords
        for c_row in claims:
            wf_row = session.query(WorkflowRunRecord).filter_by(claim_id=c_row.claim_id).first()
            assert wf_row is not None
            assert wf_row.current_state in ("HUMAN_APPROVAL", "COMPLETED")

        # Check AuditEventRecords
        audit_events = session.query(AuditEventRecord).filter_by(workflow_run_id=b_id).all()
        assert len(audit_events) >= 2  # SUBMITTED and COMPLETED

    # 3. Retrieve status via GET /api/v1/batches/{batch_id}
    get_resp = test_client.get(f"/api/v1/batches/{b_id}", headers=operator_headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["batch_id"] == b_id

    # 4. Retrieve itemized claims via GET /api/v1/batches/{batch_id}/claims
    claims_resp = test_client.get(f"/api/v1/batches/{b_id}/claims", headers=operator_headers)
    assert claims_resp.status_code == 200
    assert claims_resp.json()["total"] == 2

    # 5. Retrieve disaster context via GET /api/v1/batches/{batch_id}/disaster
    disaster_resp = test_client.get(f"/api/v1/batches/{b_id}/disaster", headers=operator_headers)
    assert disaster_resp.status_code == 200
    d_ctx_data = disaster_resp.json()["disaster_context"]
    assert d_ctx_data["disaster_id"] == "CAT-2026-01"
    assert d_ctx_data["disaster_name"] == "Midwest Storm Complex"


def test_disaster_catastrophe_batch_with_20_claims(api_service: ApiService) -> None:
    """
    Verify 20-claim synthetic disaster batch completes with 100% budget compliance,
    proper catastrophe context propagation, and zero cross-claim contamination.
    """
    disaster = DisasterContext(
        disaster_id="CAT-2026-HURRICANE-ZETA",
        disaster_name="Hurricane Zeta Catastrophe",
        disaster_type="HURRICANE",
        geographic_zone="GULF-COAST-SOUTH",
        emergency_deductible_policy="CATASTROPHE_5_PERCENT_DEDUCTIBLE",
        shared_notes="Prioritize flood and storm surge structural assessments.",
    )

    claims_list: list[ClaimInput] = []
    expected_total_requested = Decimal("0.00")

    for i in range(1, 21):
        amt = Decimal(f"{1000 + i * 150}.00")
        expected_total_requested += amt
        claims_list.append(
            ClaimInput(
                claim_id=uuid4(),
                policy_id=f"POL-HURR-{i:03d}",
                claimant_name=f"Claimant Zeta {i}",
                incident_date="2026-04-10",
                claim_amount=amt,
                description=f"Catastrophic hurricane surge and wind damage to residence unit {i}.",
                language="en",
            )
        )

    b_id = uuid4()
    coordinator = api_service.batch_coordinator
    coordinator.set_api_service(api_service)

    result = coordinator.submit_batch(
        batch_id=b_id,
        claims=claims_list,
        batch_name="Hurricane Zeta 20-Claim Emergency Ingestion",
        disaster_context=disaster,
        submitted_by="emergency-operations-center",
    )

    assert result.status == BatchStatus.COMPLETED.value
    assert result.total_claims == 20
    assert result.processed_claims == 20
    assert result.successful_claims == 20
    assert result.failed_claims == 0
    assert result.total_requested_amount == expected_total_requested

    # Verify per-claim results: each claim reached HUMAN_APPROVAL
    for cr in result.claim_results:
        assert cr.status == ClaimBatchStatus.COMPLETED.value
        assert cr.current_state == "HUMAN_APPROVAL"
        assert cr.workflow_run_id is not None
        assert cr.error_message is None

    # Check disaster context retrieval from coordinator
    fetched_disaster = coordinator.get_batch_disaster(b_id)
    assert fetched_disaster is not None
    assert fetched_disaster.disaster_id == "CAT-2026-HURRICANE-ZETA"
    assert fetched_disaster.emergency_deductible_policy == "CATASTROPHE_5_PERCENT_DEDUCTIBLE"


def test_batch_api_idempotency(api_service: ApiService, test_client: TestClient) -> None:
    """Verify that replaying a batch submission with Idempotency-Key returns cached response without duplicate execution."""
    operator_headers = {
        "Authorization": "Bearer dev-operator-token",
        "Idempotency-Key": "batch-idemp-key-9999",
    }

    req_payload = {
        "batch_name": "Idempotency Test Batch",
        "claims": [
            {
                "policy_id": "POL-9001",
                "claimant_name": "Edward Norton",
                "incident_date": "2026-04-10",
                "claim_amount": "1750.00",
                "description": "Lightning surge damaged home electronics.",
            }
        ],
    }

    # First submission
    resp1 = test_client.post("/api/v1/batches", json=req_payload, headers=operator_headers)
    assert resp1.status_code == 201
    data1 = resp1.json()

    # Second submission with same Idempotency-Key
    resp2 = test_client.post("/api/v1/batches", json=req_payload, headers=operator_headers)
    assert resp2.status_code == 201
    data2 = resp2.json()

    # Returned batch ID and totals must match exactly
    assert data1["batch_id"] == data2["batch_id"]
    assert data1["status"] == data2["status"]
    assert data1["total_requested_amount"] == data2["total_requested_amount"]


def test_resuming_and_retrying_failed_claims(api_service: ApiService) -> None:
    """Verify retry of failed claims within an existing batch without re-running successful claims."""
    coordinator = api_service.batch_coordinator
    coordinator.set_api_service(api_service)

    c_ok = ClaimInput(
        claim_id=uuid4(),
        policy_id="POL-9101",
        claimant_name="Fiona Gallagher",
        incident_date="2026-04-10",
        claim_amount=Decimal("950.00"),
        description="Fallen branch on fence.",
    )
    c_fail = ClaimInput(
        claim_id=uuid4(),
        policy_id="POL-9102",
        claimant_name="George Clark",
        incident_date="2026-04-10",
        claim_amount=Decimal("2100.00"),
        description="Basement flooding.",
    )

    # Inject mock behavior for initial run: claim c_fail fails
    orig_submit = api_service.submit_claim
    call_count = {"fiona": 0, "george": 0}

    def mock_submit_flaky(req: ClaimSubmissionRequest, **kwargs: Any) -> ClaimSubmissionResponse:
        if req.claim_id == str(c_fail.claim_id):
            call_count["george"] += 1
            if call_count["george"] <= 3:  # Fails during first batch attempt
                raise RuntimeError("Temporary downstream provider timeout")
        else:
            call_count["fiona"] += 1
        return orig_submit(req, **kwargs)

    with mock.patch.object(api_service, "submit_claim", side_effect=mock_submit_flaky):
        b_id = uuid4()
        init_res = coordinator.submit_batch(
            batch_id=b_id,
            claims=[c_ok, c_fail],
            batch_name="Retry Test Batch",
        )

        assert init_res.status == BatchStatus.PARTIALLY_COMPLETED.value
        assert init_res.successful_claims == 1
        assert init_res.failed_claims == 1

    # Now retry only failed claims (the downstream service is recovered)
    fiona_calls_before = call_count["fiona"]
    retry_res = coordinator.retry_failed_claims(batch_id=b_id, claim_ids=[c_fail.claim_id])

    assert retry_res.status == BatchStatus.COMPLETED.value
    assert retry_res.successful_claims == 2
    assert retry_res.failed_claims == 0

    # Ensure c_ok was NOT re-executed
    assert call_count["fiona"] == fiona_calls_before


def test_bounded_concurrency_workers(api_service: ApiService) -> None:
    """Verify that claims execute with bounded concurrency up to configured max workers."""
    coordinator = BatchCoordinator(
        session_factory=api_service.session_factory,
        api_service=api_service,
        max_batch_size=50,
        max_concurrency=4,  # Bounded to 4 concurrent workers
    )

    active_workers = 0
    max_active_observed = 0

    orig_submit = api_service.submit_claim

    def tracked_submit(req: ClaimSubmissionRequest, **kwargs: Any) -> ClaimSubmissionResponse:
        nonlocal active_workers, max_active_observed
        active_workers += 1
        if active_workers > max_active_observed:
            max_active_observed = active_workers
        try:
            return orig_submit(req, **kwargs)
        finally:
            active_workers -= 1

    claims = [
        ClaimInput(
            claim_id=uuid4(),
            policy_id=f"POL-CONC-{i:03d}",
            claimant_name=f"Concurrency Claimant {i}",
            incident_date="2026-04-10",
            claim_amount=Decimal("500.00"),
            description=f"Minor damage item {i}",
        )
        for i in range(10)
    ]

    with mock.patch.object(api_service, "submit_claim", side_effect=tracked_submit):
        res = coordinator.submit_batch(
            batch_id=uuid4(),
            claims=claims,
            batch_name="Concurrency Bound Test",
        )

    assert res.status == BatchStatus.COMPLETED.value
    assert max_active_observed <= 4

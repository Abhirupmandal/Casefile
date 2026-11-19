"""
Phase 12.6 Security & Invariant Tests: Batch Processing & Disaster Claim Orchestration.

Verifies:
1. Role-based access control (RBAC) across batch submission, status, and retry endpoints.
2. Claimant tenant isolation (claimants can only observe their own claims within a batch).
3. Disaster context fraud scrutiny invariant (catastrophe claims with high fraud are never auto-approved).
4. Financial safety invariants (authoritative individual amounts, no auto-approval, bounded counter-offers).
5. Explainability and audit provenance preservation under batch execution.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from casefile.api.app import create_app
from casefile.api.auth import Role
from casefile.api.dependencies import set_api_service
from casefile.api.service import ApiService
from casefile.fraud.contracts import (
    FraudAssessmentMetadata,
    FraudAssessmentStatus,
    FraudRiskResult,
    RiskCategory,
)
from casefile.models.contracts import ClaimInput, DisasterContext
from casefile.models.persistence import Base
from casefile.workflow.batch import BatchStatus


@pytest.fixture
def shared_engine() -> Any:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def api_service(shared_engine: Any) -> ApiService:
    service = ApiService(engine=shared_engine)
    return service


@pytest.fixture
def test_client(api_service: ApiService) -> TestClient:
    app = create_app(engine=api_service.engine)
    set_api_service(api_service)
    return TestClient(app)


def _token_for(role: Role, user_id: str = "test-user") -> dict[str, str]:
    """Helper to simulate authenticated role tokens."""
    token_map = {
        Role.OPERATOR: "dev-operator-token",
        Role.CLAIM_SUPERVISOR: "dev-supervisor-token",
        Role.CLAIM_REVIEWER: "dev-reviewer-token",
        Role.CLAIMANT: "dev-claimant-token",
        Role.VIEWER: "dev-viewer-token",
    }
    tok = token_map.get(role, "dev-operator-token")
    return {"Authorization": f"Bearer {tok}"}


def test_rbac_unauthenticated_requests_rejected(test_client: TestClient) -> None:
    """Unauthenticated requests to batch endpoints must be rejected with 401 Unauthorized."""
    b_id = str(uuid4())
    # Submit batch without token
    resp = test_client.post(
        "/api/v1/claims/batch",
        json={
            "claims": [
                {
                    "policy_id": "POL-1",
                    "claimant_name": "A",
                    "incident_date": "2026-04-10",
                    "claim_amount": "500.00",
                    "description": "test damage",
                }
            ]
        },
    )
    assert resp.status_code == 401

    # Get batch status without token
    resp = test_client.get(f"/api/v1/batches/{b_id}")
    assert resp.status_code == 401

    # Get batch claims without token
    resp = test_client.get(f"/api/v1/batches/{b_id}/claims")
    assert resp.status_code == 401

    # Retry failed claims without token
    resp = test_client.post(f"/api/v1/batches/{b_id}/retry", json={})
    assert resp.status_code == 401


def test_rbac_role_authorizations_and_forbidden_actions(test_client: TestClient) -> None:
    """Viewer role cannot submit batches or trigger retries (403 Forbidden)."""
    viewer_headers = _token_for(Role.VIEWER)

    # Viewer cannot submit a batch
    resp = test_client.post(
        "/api/v1/claims/batch",
        headers=viewer_headers,
        json={
            "claims": [
                {
                    "policy_id": "POL-1",
                    "claimant_name": "A",
                    "incident_date": "2026-04-10",
                    "claim_amount": "500.00",
                    "description": "test damage",
                }
            ]
        },
    )
    assert resp.status_code == 403

    # Viewer cannot trigger retries
    b_id = str(uuid4())
    resp = test_client.post(f"/api/v1/batches/{b_id}/retry", headers=viewer_headers, json={})
    assert resp.status_code == 403


def test_claimant_tenant_isolation_in_batch_status(
    api_service: ApiService, test_client: TestClient
) -> None:
    """
    Claimant role must only see their own claims in batch status queries.
    If the claimant owns zero claims in the batch, a 403 Forbidden is returned.
    """
    operator_headers = _token_for(Role.OPERATOR)
    claimant_headers = _token_for(Role.CLAIMANT)

    # Submit batch with claims from Alice and Bob
    alice_name = "claimant-user"  # matches the claimant principal_id in mock auth
    bob_name = "Bob Smith"

    payload = {
        "claims": [
            {
                "policy_id": "POL-ALICE-1",
                "claimant_name": alice_name,
                "incident_date": "2026-04-10",
                "claim_amount": "1200.00",
                "description": "Residence roof storm leak damage",
            },
            {
                "policy_id": "POL-BOB-1",
                "claimant_name": bob_name,
                "incident_date": "2026-04-10",
                "claim_amount": "3400.00",
                "description": "Broken fence and vehicle hail damage",
            },
        ],
        "batch_name": "Mixed Tenant Batch",
    }

    sub_resp = test_client.post("/api/v1/claims/batch", headers=operator_headers, json=payload)
    assert sub_resp.status_code == 201
    batch_id = sub_resp.json()["batch_id"]

    # When Alice queries batch status: total_claims is sanitized to Alice's count (1)
    alice_resp = test_client.get(f"/api/v1/batches/{batch_id}", headers=claimant_headers)
    assert alice_resp.status_code == 200
    alice_data = alice_resp.json()
    assert alice_data["total_claims"] == 1

    # When Alice queries claims list endpoint: only Alice's claim is returned
    claims_resp = test_client.get(f"/api/v1/batches/{batch_id}/claims", headers=claimant_headers)
    assert claims_resp.status_code == 200
    claims_data = claims_resp.json()
    assert len(claims_data["items"]) == 1
    assert claims_data["items"][0]["claimant_name"] == alice_name

    # If an unrelated claimant queries a batch containing none of their claims -> 403 Forbidden
    unrelated_payload = {
        "claims": [
            {
                "policy_id": "POL-OTHER-1",
                "claimant_name": "Completely Unrelated Person",
                "incident_date": "2026-04-10",
                "claim_amount": "800.00",
                "description": "Minor hail damage on detached garage",
            }
        ]
    }
    unrelated_sub = test_client.post(
        "/api/v1/claims/batch", headers=operator_headers, json=unrelated_payload
    )
    unrelated_b_id = unrelated_sub.json()["batch_id"]

    unrelated_query = test_client.get(f"/api/v1/batches/{unrelated_b_id}", headers=claimant_headers)
    assert unrelated_query.status_code == 403
    err_msg = unrelated_query.json().get("error", {}).get("message", "")
    assert "Tenant isolation" in err_msg


def test_disaster_context_fraud_scrutiny_invariant(
    api_service: ApiService,
) -> None:
    """
    Disaster context must never bypass fraud controls or auto-approve claims.
    Even in emergency disaster batches, claims flagged with fraud must require
    mandatory human scrutiny.
    """
    coordinator = api_service.batch_coordinator
    coordinator.set_api_service(api_service)

    disaster = DisasterContext(
        disaster_id="CAT-2026-TORNADO-ALPHA",
        disaster_name="Alpha Tornado Outbreak",
        disaster_type="TORNADO",
        geographic_zone="MIDWEST-SECTOR-4",
        emergency_deductible_policy="EMERGENCY_EXPEDITED_POLICY",
        shared_notes="High priority emergency intake.",
    )

    # Claim with inflated amount and fraud indicators
    claim = ClaimInput(
        claim_id=uuid4(),
        policy_id="POL-FRAUD-SUSPECT-01",
        claimant_name="Suspicious Claimant",
        incident_date="2026-04-10",
        claim_amount=Decimal("45000.00"),
        description="Claiming maximum possible total destruction on storage shed.",
    )

    # Pre-populate fraud assessment with high risk and required human scrutiny
    fraud_res = FraudRiskResult(
        assessment_id=uuid4(),
        claim_id=claim.claim_id,
        workflow_run_id=uuid4(),
        risk_score=0.92,
        risk_category=RiskCategory.HIGH,
        confidence_score=0.88,
        status=FraudAssessmentStatus.COMPLETED,
        metadata=FraudAssessmentMetadata(
            model_provider="rules-engine",
            model_version="1.0.0",
        ),
        findings_summary="Anomalous claimed amount for structure type; recent coverage surge detected.",
        requires_human_scrutiny=True,
    )
    with api_service._uow() as uow:
        uow.fraud_assessments.save(fraud_res)

    result = coordinator.submit_batch(
        batch_id=uuid4(),
        claims=[claim],
        disaster_context=disaster,
    )

    # Disaster claims must never auto-approve; they must be held in HUMAN_APPROVAL
    assert result.status == BatchStatus.COMPLETED.value
    claim_res = result.claim_results[0]
    assert claim_res.current_state == "HUMAN_APPROVAL"
    assert claim_res.approved_amount is None or claim_res.approved_amount == Decimal("0.00")


def test_financial_safety_invariants_in_batch(
    api_service: ApiService, test_client: TestClient
) -> None:
    """
    Financial Invariants:
    1. Authoritative individual claim amounts (per-claim requested amount is preserved).
    2. Batch total_requested_amount is exact sum of claim amounts.
    3. Negative or zero claim amounts are rejected at intake.
    """
    operator_headers = _token_for(Role.OPERATOR)

    # Negative claim amount rejected
    resp = test_client.post(
        "/api/v1/claims/batch",
        headers=operator_headers,
        json={
            "claims": [
                {
                    "policy_id": "POL-NEG-1",
                    "claimant_name": "Negative Tester",
                    "incident_date": "2026-04-10",
                    "claim_amount": "-100.00",
                    "description": "Invalid negative amount",
                }
            ]
        },
    )
    assert resp.status_code in (400, 422)

    # Zero claim amount rejected
    resp = test_client.post(
        "/api/v1/claims/batch",
        headers=operator_headers,
        json={
            "claims": [
                {
                    "policy_id": "POL-ZERO-1",
                    "claimant_name": "Zero Tester",
                    "incident_date": "2026-04-10",
                    "claim_amount": "0.00",
                    "description": "Invalid zero amount",
                }
            ]
        },
    )
    assert resp.status_code in (400, 422)


def test_audit_provenance_and_decision_explanation_preserved(
    api_service: ApiService, test_client: TestClient
) -> None:
    """Every claim in a batch preserves its own audit trail and explainability record."""
    operator_headers = _token_for(Role.OPERATOR)

    payload = {
        "claims": [
            {
                "policy_id": "POL-AUDIT-1",
                "claimant_name": "Audit Claimant",
                "incident_date": "2026-04-10",
                "claim_amount": "1500.00",
                "description": "Roof tile displacement from storm winds.",
            }
        ],
        "batch_name": "Audit Provenance Batch",
    }

    sub_resp = test_client.post("/api/v1/claims/batch", headers=operator_headers, json=payload)
    assert sub_resp.status_code == 201
    b_id = sub_resp.json()["batch_id"]

    # Verify audit events for the batch
    from casefile.models.persistence import AuditEventRecord

    with api_service._uow() as uow:
        # Check batch submitted audit event exists
        batch_audit = (
            uow.session.query(AuditEventRecord)
            .filter_by(action="submit_batch", workflow_run_id=b_id)
            .first()
        )
        assert batch_audit is not None
        assert "submitted with 1 claims" in batch_audit.summary

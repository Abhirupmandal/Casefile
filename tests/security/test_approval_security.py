"""Security Tests: HITL & Approval Security (Phase 8 Step 10 & 26).

Adversarial tests covering:
- Actor spoofing in request body is ignored (authenticated principal is authoritative)
- Role spoofing is rejected
- Self-approval attempt is rejected (separation of duties)
- Expired approval cannot be decided
- Duplicate decisions on terminal approval are rejected
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from casefile.api.app import create_app
from casefile.api.auth import DevAuthProvider, set_auth_provider
from casefile.api.dependencies import get_api_service
from casefile.models.domain import (
    HumanApprovalRequest,
    Money,
    Recommendation,
    RecommendationType,
)


@pytest.fixture
def approval_client(tmp_path):
    db_file = tmp_path / "approval_sec.db"
    app = create_app(database_url=f"sqlite:///{db_file}")
    set_auth_provider(DevAuthProvider())
    client = TestClient(app)
    service = get_api_service()
    return client, service


def _create_test_approval(service, requested_by: str = "operator-charlie", expired: bool = False):
    wf_id = uuid4()
    c_id = uuid4()
    if expired:
        past = datetime.now(UTC) - timedelta(hours=2)
        req = HumanApprovalRequest(
            workflow_run_id=wf_id,
            claim_id=c_id,
            requested_by=requested_by,
            requested_at=past - timedelta(hours=1),
            deadline=past,
            recommendation=Recommendation(
                claim_id=c_id,
                recommendation_type=RecommendationType.APPROVE_FULL,
                estimated_payout=Money(amount=Decimal("1200.00")),
                notes="Valid claim supporting documentation.",
                confidence=0.95,
            ),
            reviewer_summary="Queue item test",
        )
        with service._uow() as uow:
            uow.approvals.save_request(req)
        return req.approval_id, wf_id, c_id

    req = HumanApprovalRequest(
        workflow_run_id=wf_id,
        claim_id=c_id,
        requested_by=requested_by,
        recommendation=Recommendation(
            claim_id=c_id,
            recommendation_type=RecommendationType.APPROVE_FULL,
            estimated_payout=Money(amount=Decimal("1200.00")),
            notes="Valid claim supporting documentation.",
            confidence=0.95,
        ),
        reviewer_summary="Queue item test",
        deadline=datetime.now(UTC) + timedelta(hours=24),
    )
    service.approval_service.request_approval(req)
    return req.approval_id, wf_id, c_id


def test_actor_identity_derived_from_principal(approval_client):
    """Actor identity in approval decision is taken from authenticated principal, ignoring body overrides."""
    client, service = approval_client
    app_id, wf_id, c_id = _create_test_approval(service)

    # Bob (SENIOR_REVIEWER) authenticates, but request body tries to claim decision was made by "alice" or "admin"
    headers = {"Authorization": "Bearer dev-senior-token"}
    payload = {
        "decision": "APPROVE",
        "reason": "Legitimate approval.",
        "actor_id": "malicious-injected-actor",
        "approver_id": "forged-admin",
    }
    resp = client.post(f"/api/v1/approvals/{app_id}/decision", json=payload, headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    # Must be decided by reviewer-bob, not malicious-injected-actor
    assert data["decided_by"] == "reviewer-bob"


def test_self_approval_protection(approval_client):
    """Requester cannot approve their own claim (Separation of Duties)."""
    client, service = approval_client
    # Approval requested by 'reviewer-alice'
    app_id, wf_id, c_id = _create_test_approval(service, requested_by="reviewer-alice")

    # Alice tries to approve her own request
    headers = {"Authorization": "Bearer dev-reviewer-token"}
    resp = client.post(
        f"/api/v1/approvals/{app_id}/decision",
        json={"decision": "APPROVE", "reason": "Self-approving my claim"},
        headers=headers,
    )
    assert resp.status_code in (400, 403, 422)
    assert "Separation of duties" in resp.text


def test_expired_approval_cannot_be_decided(approval_client):
    """Approval whose deadline has passed cannot receive decisions."""
    client, service = approval_client
    # Create expired approval
    app_id, wf_id, c_id = _create_test_approval(service, expired=True)

    headers = {"Authorization": "Bearer dev-reviewer-token"}
    resp = client.post(
        f"/api/v1/approvals/{app_id}/decision",
        json={"decision": "APPROVE", "reason": "Late approval attempt"},
        headers=headers,
    )
    assert resp.status_code in (400, 409, 422)
    assert "expired" in resp.text.lower()


def test_duplicate_decision_rejected(approval_client):
    """Once an approval is in terminal state, subsequent different decisions must fail closed."""
    client, service = approval_client
    app_id, wf_id, c_id = _create_test_approval(service)

    headers = {"Authorization": "Bearer dev-reviewer-token"}
    # First decision
    resp1 = client.post(
        f"/api/v1/approvals/{app_id}/decision",
        json={"decision": "APPROVE", "reason": "First valid decision"},
        headers=headers,
    )
    assert resp1.status_code == 200

    # Second decision attempt (trying to alter to REJECT)
    resp2 = client.post(
        f"/api/v1/approvals/{app_id}/decision",
        json={"decision": "REJECT", "reason": "Attempting to change decision"},
        headers=headers,
    )
    assert resp2.status_code in (400, 409, 422)

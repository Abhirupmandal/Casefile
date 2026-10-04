"""Security Tests: RBAC Hardening & Authorization (Phase 8 Step 9 & 26).

Covers:
- Viewer cannot submit claims (403)
- Viewer cannot decide approvals (403)
- Viewer cannot replay workflows (403)
- Operator cannot decide approvals (403)
- Reviewer cannot escalate to replay workflows (403)
- Authorization matrix boundaries across all roles
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from casefile.api.app import create_app
from casefile.api.auth import DevAuthProvider, set_auth_provider


@pytest.fixture
def rbac_client(tmp_path):
    db_file = tmp_path / "rbac_sec.db"
    app = create_app(database_url=f"sqlite:///{db_file}")
    set_auth_provider(DevAuthProvider())
    yield TestClient(app)


def test_viewer_cannot_submit_claim(rbac_client):
    """VIEWER role cannot submit claims (requires SUBMIT_CLAIM)."""
    headers = {"Authorization": "Bearer dev-viewer-token"}
    payload = {
        "policy_id": "POL-V001",
        "claimant_name": "Viewer Attempt",
        "incident_date": "2026-05-01",
        "claim_amount": "5000.00",
        "description": "Viewer attempting claim submission.",
    }
    resp = rbac_client.post("/api/v1/claims", json=payload, headers=headers)
    assert resp.status_code == 403


def test_viewer_cannot_decide_approval(rbac_client):
    """VIEWER role cannot access approval decision endpoint."""
    headers = {"Authorization": "Bearer dev-viewer-token"}
    approval_id = uuid4()
    resp = rbac_client.post(
        f"/api/v1/approvals/{approval_id}/decision",
        json={"decision": "APPROVE", "reason": "Unauthorized"},
        headers=headers,
    )
    assert resp.status_code == 403


def test_viewer_cannot_replay_workflow(rbac_client):
    """VIEWER role cannot initiate workflow replay simulation."""
    headers = {"Authorization": "Bearer dev-viewer-token"}
    run_id = uuid4()
    resp = rbac_client.post(
        f"/api/v1/workflows/{run_id}/replay",
        json={},
        headers=headers,
    )
    assert resp.status_code == 403


def test_operator_cannot_decide_approval(rbac_client):
    """OPERATOR role can submit claims and replay, but CANNOT decide approvals."""
    headers = {"Authorization": "Bearer dev-operator-token"}
    approval_id = uuid4()
    resp = rbac_client.post(
        f"/api/v1/approvals/{approval_id}/decision",
        json={"decision": "APPROVE", "reason": "Operator decision attempt"},
        headers=headers,
    )
    assert resp.status_code == 403


def test_reviewer_cannot_replay_workflow(rbac_client):
    """CLAIM_REVIEWER can decide approvals, but CANNOT trigger workflow replay."""
    headers = {"Authorization": "Bearer dev-reviewer-token"}
    run_id = uuid4()
    resp = rbac_client.post(
        f"/api/v1/workflows/{run_id}/replay",
        json={},
        headers=headers,
    )
    assert resp.status_code == 403


def test_reviewer_cannot_submit_claims(rbac_client):
    """CLAIM_REVIEWER cannot submit claims (separation of submission vs adjudication)."""
    headers = {"Authorization": "Bearer dev-reviewer-token"}
    payload = {
        "policy_id": "POL-R001",
        "claimant_name": "Reviewer Attempt",
        "incident_date": "2026-05-01",
        "claim_amount": "5000.00",
        "description": "Reviewer attempting intake.",
    }
    resp = rbac_client.post("/api/v1/claims", json=payload, headers=headers)
    assert resp.status_code == 403

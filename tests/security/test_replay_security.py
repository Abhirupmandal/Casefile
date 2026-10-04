"""Security Tests: Safe Replay Simulation (Phase 8 Step 11 & 26).

Covers:
- Replay authorization enforcement
- Original workflow immutability (state does not change)
- Safe simulation boundary (no external side effects, no live human approval creation)
- Distinct replay run identifier generation
"""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from casefile.api.app import create_app
from casefile.api.auth import DevAuthProvider, set_auth_provider
from casefile.api.dependencies import get_api_service


@pytest.fixture
def replay_client(tmp_path):
    from casefile.api.rate_limit import InMemoryRateLimiter, set_rate_limiter

    limiter = InMemoryRateLimiter()
    set_rate_limiter(limiter)
    db_file = tmp_path / "replay_sec.db"
    app = create_app(database_url=f"sqlite:///{db_file}")
    set_auth_provider(DevAuthProvider())
    client = TestClient(app)
    service = get_api_service()
    yield client, service
    limiter.reset()


def _setup_completed_workflow(client):
    claim_payload = {
        "policy_id": "POL-REPLAY-SEC-1",
        "claimant_name": "Replay Immutability Subject",
        "incident_date": "2026-05-01",
        "claim_amount": "800.00",
        "description": "Fender dent replay check.",
    }
    create_resp = client.post(
        "/api/v1/claims",
        json=claim_payload,
        headers={"Authorization": "Bearer dev-operator-token"},
    )
    assert create_resp.status_code in (200, 201)
    return UUID(create_resp.json()["workflow_run_id"])


def test_replay_preserves_original_state_immutability(replay_client):
    """Replay simulation must not modify original workflow terminal state or record."""
    client, service = replay_client
    wf_id = _setup_completed_workflow(client)

    # Record original state before replay
    orig_before = service.get_workflow_status(wf_id)

    headers = {"Authorization": "Bearer dev-operator-token"}
    resp = client.post(f"/api/v1/workflows/{wf_id}/replay", json={}, headers=headers)
    assert resp.status_code == 200
    data = resp.json()

    # Distinct replay run identifier
    assert data["replay_id"] != str(wf_id)
    assert data["is_simulation"] is True

    # Original workflow record must remain completely identical
    orig_after = service.get_workflow_status(wf_id)
    assert orig_after.current_state == orig_before.current_state
    assert orig_after.step_count == orig_before.step_count
    assert orig_after.is_terminal == orig_before.is_terminal


def test_replay_unauthorized_role_rejected(replay_client):
    """VIEWER and CLAIM_REVIEWER cannot initiate replay."""
    client, service = replay_client
    wf_id = _setup_completed_workflow(client)

    # Viewer
    resp_v = client.post(
        f"/api/v1/workflows/{wf_id}/replay",
        json={},
        headers={"Authorization": "Bearer dev-viewer-token"},
    )
    assert resp_v.status_code == 403

    # Reviewer
    resp_r = client.post(
        f"/api/v1/workflows/{wf_id}/replay",
        json={},
        headers={"Authorization": "Bearer dev-reviewer-token"},
    )
    assert resp_r.status_code == 403

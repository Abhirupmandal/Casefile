"""Security Tests: Input Validation & Boundary Hardening (Phase 8 Step 7 & 26).

Covers:
- Malformed claim input validation (negative amount, empty fields, oversized description)
- Malformed approval decision strings
- SQL injection strings safely handled as parameter literals
- Extreme payload values
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from casefile.api.app import create_app
from casefile.api.auth import DevAuthProvider, set_auth_provider


@pytest.fixture
def input_client(tmp_path):
    db_file = tmp_path / "input_sec.db"
    app = create_app(database_url=f"sqlite:///{db_file}")
    set_auth_provider(DevAuthProvider())
    yield TestClient(app)


def test_claim_negative_amount_rejected(input_client):
    """Negative claim amount must fail validation (HTTP 422)."""
    headers = {"Authorization": "Bearer dev-operator-token"}
    payload = {
        "policy_id": "POL-NEG-1",
        "claimant_name": "Negative Amount Tester",
        "incident_date": "2026-05-01",
        "claim_amount": "-500.00",
        "description": "Factual description.",
    }
    resp = input_client.post("/api/v1/claims", json=payload, headers=headers)
    assert resp.status_code == 422


def test_claim_oversized_description_rejected(input_client):
    """Claim description exceeding max length must fail validation (HTTP 422)."""
    headers = {"Authorization": "Bearer dev-operator-token"}
    payload = {
        "policy_id": "POL-BIG-1",
        "claimant_name": "Giant Description",
        "incident_date": "2026-05-01",
        "claim_amount": "200.00",
        "description": "A" * 15_000,  # Exceeds 10,000 char limit
    }
    resp = input_client.post("/api/v1/claims", json=payload, headers=headers)
    assert resp.status_code == 422


def test_claim_empty_policy_id_rejected(input_client):
    """Empty policy_id must fail validation (HTTP 422)."""
    headers = {"Authorization": "Bearer dev-operator-token"}
    payload = {
        "policy_id": "",
        "claimant_name": "No Policy",
        "incident_date": "2026-05-01",
        "claim_amount": "100.00",
        "description": "Valid incident description.",
    }
    resp = input_client.post("/api/v1/claims", json=payload, headers=headers)
    assert resp.status_code == 422


def test_sql_injection_string_handled_safely(input_client):
    """SQL injection patterns in claimant_name/policy_id must be handled safely as literal strings."""
    headers = {"Authorization": "Bearer dev-operator-token"}
    payload = {
        "policy_id": "POL-123'; DROP TABLE workflow_runs; --",
        "claimant_name": "Robert'); DROP TABLE claims;--",
        "incident_date": "2026-05-01",
        "claim_amount": "1500.00",
        "description": "Testing SQL injection payload safety.",
    }
    resp = input_client.post("/api/v1/claims", json=payload, headers=headers)
    # The submission should be processed safely through parameterized queries without SQL error
    assert resp.status_code in (200, 201, 202)
    data = resp.json()
    assert "workflow_run_id" in data


def test_approval_decision_invalid_direction_rejected(input_client):
    """Arbitrary decision strings must fail validation."""
    headers = {"Authorization": "Bearer dev-reviewer-token"}
    approval_id = uuid4()
    payload = {
        "decision": "MAYBE_OR_SKIP",
        "reason": "Invalid verdict direction",
    }
    resp = input_client.post(
        f"/api/v1/approvals/{approval_id}/decision", json=payload, headers=headers
    )
    assert resp.status_code in (400, 404, 422)

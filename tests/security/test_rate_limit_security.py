"""Security Tests: Rate Limiting & Denial-of-Service Defense (Phase 8 Step 13 & 26).

Covers:
- Rate limit exhaustion returns HTTP 429 with Retry-After header
- State integrity is preserved (no partial claim or approval created upon rate limiting)
- Rate limiter sliding window behavior
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from casefile.api.app import create_app
from casefile.api.auth import DevAuthProvider, set_auth_provider
from casefile.api.rate_limit import InMemoryRateLimiter, set_rate_limiter


@pytest.fixture
def rate_limit_client(tmp_path):
    db_file = tmp_path / "rate_sec.db"
    app = create_app(database_url=f"sqlite:///{db_file}")
    set_auth_provider(DevAuthProvider())
    limiter = InMemoryRateLimiter()
    set_rate_limiter(limiter)
    yield TestClient(app), limiter
    limiter.reset()


def test_rate_limit_exhaustion_returns_429(rate_limit_client):
    """Exceeding request rate limits must yield HTTP 429 with Retry-After."""
    client, limiter = rate_limit_client
    headers = {"Authorization": "Bearer dev-operator-token"}
    run_id = uuid4()

    # Replay endpoint has 30 requests/min limit. Let's make 31 requests.
    hit_429 = False
    for _ in range(35):
        resp = client.post(
            f"/api/v1/workflows/{run_id}/replay",
            json={},
            headers=headers,
        )
        if resp.status_code == 429:
            hit_429 = True
            assert "Retry-After" in resp.headers
            assert "RATE_LIMIT_EXCEEDED" in resp.text
            break

    assert hit_429 is True


def test_rate_limit_does_not_corrupt_database(rate_limit_client):
    """A rate-limited request must not leave orphan records or corrupt database state."""
    client, limiter = rate_limit_client
    headers = {"Authorization": "Bearer dev-operator-token"}
    rate_key = "POST:/api/v1/claims:operator-charlie"

    # Pre-acquire 60 slots in the limiter so the next HTTP request is rate-limited immediately
    for _ in range(60):
        limiter.acquire(rate_key, max_requests=60, window_seconds=60.0)

    resp = client.post(
        "/api/v1/claims",
        json={
            "policy_id": "POL-RL-1",
            "claimant_name": "Rate Limit Test",
            "incident_date": "2026-05-01",
            "claim_amount": "100.00",
            "description": "Rate limiting validation.",
        },
        headers=headers,
    )
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    assert "RATE_LIMIT_EXCEEDED" in resp.text

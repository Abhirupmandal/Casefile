"""Security Tests: API Boundaries & Request Limits (Phase 8 Step 6 & 26).

Covers:
- Request size limiter rejecting oversized payloads (HTTP 413)
- Defense-in-depth security headers injection
- Uniform structured error formatting without stack trace leakage
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from casefile.api.app import create_app
from casefile.api.auth import DevAuthProvider, set_auth_provider


@pytest.fixture
def api_client(tmp_path):
    db_file = tmp_path / "api_sec.db"
    app = create_app(database_url=f"sqlite:///{db_file}")
    set_auth_provider(DevAuthProvider())
    yield TestClient(app)


def test_oversized_payload_rejected_with_413(api_client):
    """Payloads exceeding maximum body size must be rejected with HTTP 413."""
    headers = {
        "Authorization": "Bearer dev-operator-token",
        "Content-Type": "application/json",
        "Content-Length": str(5 * 1024 * 1024),  # 5MB header
    }
    # Large payload
    resp = api_client.post(
        "/api/v1/claims",
        content="x" * 1000,
        headers=headers,
    )
    assert resp.status_code == 413
    assert resp.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_security_headers_present(api_client):
    """All responses must include standard HTTP defense-in-depth security headers."""
    resp = api_client.get("/health")
    assert resp.status_code == 200

    headers = resp.headers
    assert headers.get("X-Content-Type-Options") == "nosniff"
    assert headers.get("X-Frame-Options") == "DENY"
    assert headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert "camera=()" in headers.get("Permissions-Policy", "")
    assert "default-src 'self'" in headers.get("Content-Security-Policy", "")


def test_structured_error_no_stack_traces_leaked(api_client):
    """Errors must return standard structured JSON without Python tracebacks or secrets."""
    headers = {"Authorization": "Bearer dev-viewer-token"}
    resp = api_client.get("/api/v1/workflows/00000000-0000-0000-0000-000000000000", headers=headers)
    assert resp.status_code == 404
    data = resp.json()
    assert "error" in data
    assert "traceback" not in data
    assert "Traceback (most recent call last)" not in resp.text

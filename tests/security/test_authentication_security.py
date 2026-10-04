"""Security Tests: Authentication Hardening (Phase 8 Step 8 & 26).

Covers:
- Unauthenticated access rejection (HTTP 401)
- Invalid bearer tokens
- Malformed Authorization headers
- Production auth provider rejection of dev tokens
- Prohibition of header-based identity spoofing in production
- DevAuthProvider fail-closed behavior in production environment
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from casefile.api.app import create_app
from casefile.api.auth import (
    DevAuthProvider,
    ProductionAuthProvider,
    Role,
    set_auth_provider,
)


@pytest.fixture
def api_client(tmp_path):
    db_file = tmp_path / "auth_sec.db"
    app = create_app(database_url=f"sqlite:///{db_file}")
    set_auth_provider(DevAuthProvider())
    yield TestClient(app)


def test_unauthenticated_request_rejected(api_client):
    """Protected endpoints must reject unauthenticated requests with 401."""
    resp = api_client.get("/api/v1/approvals")
    assert resp.status_code == 401
    assert "Missing or invalid authentication credentials" in resp.text


def test_invalid_bearer_token_rejected(api_client):
    """Forged or unknown bearer tokens must be rejected with 401."""
    headers = {"Authorization": "Bearer totally-invalid-token-12345"}
    resp = api_client.get("/api/v1/approvals", headers=headers)
    assert resp.status_code == 401


def test_malformed_auth_header_rejected(api_client):
    """Malformed Authorization header schemes must be rejected."""
    cases = [
        "Basic dXNlcjpwYXNz",
        "Bearer",
        "Bearer too many tokens here",
        "Token abcdef",
        "",
    ]
    for h in cases:
        resp = api_client.get("/api/v1/approvals", headers={"Authorization": h})
        assert resp.status_code == 401


def test_production_auth_provider_rejects_dev_tokens():
    """Production auth provider must fail closed on mock dev-* tokens."""
    prod_provider = ProductionAuthProvider(
        token_principals={
            "prod-secret-token-xyz": ("prod-admin", Role.ADMIN, "Prod Admin"),
        }
    )
    # Dev token attempt
    p = prod_provider.authenticate(authorization="Bearer dev-admin-token")
    assert p is None

    p2 = prod_provider.authenticate(x_api_key="dev-reviewer-token")
    assert p2 is None


def test_production_auth_provider_rejects_header_spoofing():
    """Production auth provider strictly ignores X-Principal-Role and X-Principal-Id."""
    prod_provider = ProductionAuthProvider()
    # Attacker tries to inject admin role via headers
    p = prod_provider.authenticate(
        x_principal_id="attacker-user",
        x_principal_role="ADMIN",
    )
    assert p is None


def test_production_auth_provider_accepts_valid_prod_token():
    """Production auth provider succeeds with configured production token."""
    prod_provider = ProductionAuthProvider(
        token_principals={
            "valid-prod-token": ("ops-agent-1", Role.OPERATOR, "Operations Agent"),
        }
    )
    p = prod_provider.authenticate(authorization="Bearer valid-prod-token")
    assert p is not None
    assert p.principal_id == "ops-agent-1"
    assert p.role == Role.OPERATOR
    assert p.auth_method == "production_token"


def test_dev_auth_provider_fails_closed_in_production(monkeypatch):
    """DevAuthProvider must raise an internal security fault if invoked in production mode."""
    monkeypatch.setenv("CASEFILE_ENV", "production")
    dev_provider = DevAuthProvider()
    with pytest.raises(Exception) as exc_info:
        dev_provider.authenticate(authorization="Bearer dev-admin-token")
    assert "prohibited in production mode" in str(exc_info.value)

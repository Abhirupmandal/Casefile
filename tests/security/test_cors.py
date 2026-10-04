"""Security Tests: CORS Configuration & Isolation (Phase 8 Step 4 & 26).

Covers:
- Allowed origins receive correct CORS headers
- Disallowed origins are rejected / do not receive Access-Control-Allow-Origin
- Wildcard with credentials rejection in production configuration
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from casefile.api.app import create_app
from casefile.config import ApiSecurityConfig, AppConfig


@pytest.fixture
def cors_client(tmp_path):
    db_file = tmp_path / "cors_sec.db"
    cfg = AppConfig(
        environment="test",
        security=ApiSecurityConfig(
            cors_allowed_origins=["https://console.casefile.internal", "http://localhost:3000"],
            cors_allow_credentials=True,
        ),
    )
    app = create_app(database_url=f"sqlite:///{db_file}", config=cfg)
    yield TestClient(app)


def test_cors_allowed_origin(cors_client):
    """Allowed origin receives Access-Control-Allow-Origin and credentials headers."""
    headers = {"Origin": "https://console.casefile.internal"}
    resp = cors_client.get("/health", headers=headers)
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "https://console.casefile.internal"
    assert resp.headers.get("access-control-allow-credentials") == "true"


def test_cors_disallowed_origin_rejected(cors_client):
    """Disallowed origin does NOT receive Access-Control-Allow-Origin header."""
    headers = {"Origin": "https://malicious-site.example.com"}
    resp = cors_client.get("/health", headers=headers)
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") is None


def test_cors_preflight_disallowed_origin(cors_client):
    """OPTIONS preflight from disallowed origin must not grant access."""
    headers = {
        "Origin": "https://malicious-site.example.com",
        "Access-Control-Request-Method": "POST",
    }
    resp = cors_client.options("/api/v1/claims", headers=headers)
    assert resp.headers.get("access-control-allow-origin") is None


def test_wildcard_with_credentials_prohibited_in_production():
    """AppConfig fail-closed check must reject wildcard origin if credentials enabled in production."""
    with pytest.raises(ValueError) as exc_info:
        AppConfig(
            environment="production",
            security=ApiSecurityConfig(
                cors_allowed_origins=["*"],
                cors_allow_credentials=True,
            ),
        ).validate_production()
    assert "wildcard CORS origin" in str(exc_info.value)

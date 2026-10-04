"""Security Tests: Security Headers (Phase 8 Step 5 & 26).

Covers:
- Presence and correct configuration of defense-in-depth HTTP security headers
- Verification that headers do not break API or console access
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from casefile.api.app import create_app
from casefile.config import ApiSecurityConfig, AppConfig


@pytest.fixture
def headers_client(tmp_path):
    db_file = tmp_path / "headers_sec.db"
    cfg = AppConfig(
        environment="test",
        security=ApiSecurityConfig(
            enable_security_headers=True,
            strict_transport_security=True,
        ),
    )
    app = create_app(database_url=f"sqlite:///{db_file}", config=cfg)
    yield TestClient(app)


def test_x_content_type_options(headers_client):
    """X-Content-Type-Options must be 'nosniff'."""
    resp = headers_client.get("/health")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"


def test_x_frame_options(headers_client):
    """X-Frame-Options must be 'DENY' to protect against clickjacking."""
    resp = headers_client.get("/health")
    assert resp.headers.get("X-Frame-Options") == "DENY"


def test_referrer_policy(headers_client):
    """Referrer-Policy must be 'strict-origin-when-cross-origin'."""
    resp = headers_client.get("/health")
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"


def test_content_security_policy(headers_client):
    """Content-Security-Policy must restrict script and default sources."""
    resp = headers_client.get("/health")
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp


def test_permissions_policy(headers_client):
    """Permissions-Policy must disable unneeded browser features."""
    resp = headers_client.get("/health")
    pp = resp.headers.get("Permissions-Policy", "")
    assert "camera=()" in pp
    assert "microphone=()" in pp


def test_strict_transport_security(headers_client):
    """Strict-Transport-Security must be emitted when configured."""
    resp = headers_client.get("/health")
    hsts = resp.headers.get("Strict-Transport-Security", "")
    assert "max-age=31536000" in hsts

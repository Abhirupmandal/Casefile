"""Security Tests: Audit Trail & Log Integrity (Phase 8 Step 14 & 26).

Covers:
- CRLF injection prevention in audit log sanitization
- Redaction of credentials in audit/log strings
- Audit trail access authorization (VIEWER cannot access)
- Audit query parameter bounding (limit bounds)
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from casefile.api.app import create_app
from casefile.api.auth import DevAuthProvider, set_auth_provider
from casefile.observability.sanitize import sanitize_log_message


@pytest.fixture
def audit_client(tmp_path):
    db_file = tmp_path / "audit_sec.db"
    app = create_app(database_url=f"sqlite:///{db_file}")
    set_auth_provider(DevAuthProvider())
    yield TestClient(app)


def test_sanitize_log_message_prevents_crlf_injection():
    """Verify newlines and carriage returns are stripped to prevent log forging."""
    malicious_input = "Claim approved\r\n[2026-10-05] ADMIN user escalated permissions\n"
    sanitized = sanitize_log_message(malicious_input)

    assert "\r" not in sanitized
    assert "\n" not in sanitized
    assert "Claim approved" in sanitized


def test_sanitize_log_message_redacts_keys():
    """Verify embedded API keys or credentials in log messages are masked."""
    msg = "External provider error with key sk-abcdef1234567890 when calling endpoint"
    sanitized = sanitize_log_message(msg)

    assert "sk-abcdef1234567890" not in sanitized
    assert "[REDACTED]" in sanitized


def test_viewer_cannot_view_audit_trail(audit_client):
    """VIEWER role cannot inspect system audit trail."""
    headers = {"Authorization": "Bearer dev-viewer-token"}
    resp = audit_client.get("/api/v1/audit", headers=headers)
    assert resp.status_code == 403


def test_audit_limit_query_param_bounded(audit_client):
    """Audit endpoint rejects out-of-bounds limit parameters."""
    headers = {"Authorization": "Bearer dev-operator-token"}
    # limit > 200
    resp_large = audit_client.get("/api/v1/audit?limit=500", headers=headers)
    assert resp_large.status_code == 422

    # limit < 1
    resp_zero = audit_client.get("/api/v1/audit?limit=0", headers=headers)
    assert resp_zero.status_code == 422

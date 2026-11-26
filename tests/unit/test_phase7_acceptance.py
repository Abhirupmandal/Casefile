"""Phase 7 Acceptance Tests: API + Operations Console (Categories A through AC).

Validates all 29 acceptance categories:
A. API application creation
B. API versioning
C. claim submission
D. workflow status
E. workflow history
F. agent visibility
G. tool visibility
H. checkpoint visibility
I. replay
J. replay isolation
K. approval queue
L. approval authorization
M. approval decision
N. self-approval protection
O. expired approval protection
P. authentication
Q. role authorization
R. structured errors
S. idempotency
T. rate limiting
U. health
V. readiness
W. API observability
X. telemetry sanitization
Y. OpenAPI
Z. evaluation endpoint
AA. audit endpoint
AB. console data contracts
AC. E2E complete flow & Phase 2-6 regression
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from casefile.api.app import create_app
from casefile.api.auth import DevAuthProvider, set_auth_provider
from casefile.api.dependencies import set_api_service
from casefile.api.idempotency import IdempotencyStore, set_idempotency_store
from casefile.api.rate_limit import InMemoryRateLimiter, set_rate_limiter
from casefile.api.service import ApiService
from casefile.budget.envelope import BudgetEnvelope
from casefile.models.contracts import WorkflowState
from casefile.models.domain import (
    HumanApprovalRequest,
    Money,
    Recommendation,
    RecommendationType,
)
from casefile.models.persistence import RunBudgetEnvelopeRow, get_engine, init_db

pytestmark = pytest.mark.unit


# ----------------------------------------------------------------------
# Test Fixtures
# ----------------------------------------------------------------------
@pytest.fixture
def test_engine(tmp_path: Path) -> Engine:
    db_file = tmp_path / f"test_phase7_{uuid4().hex[:8]}.db"
    eng = get_engine(f"sqlite:///{db_file}")
    init_db(eng)
    return eng


@pytest.fixture
def api_service(test_engine: Engine) -> ApiService:
    return ApiService(engine=test_engine)


@pytest.fixture
def client(test_engine: Engine, api_service: ApiService) -> Iterator[TestClient]:
    # Reset stores
    set_idempotency_store(IdempotencyStore())
    set_rate_limiter(InMemoryRateLimiter())
    set_auth_provider(DevAuthProvider())

    app = create_app(engine=test_engine)
    set_api_service(api_service)

    with TestClient(app) as tc:
        yield tc


_API_SERVICE_PATH = Path(__file__).resolve().parents[2] / "src" / "casefile" / "api" / "service.py"
_COST_FIELDS = {
    "cost_usd",
    "average_cost",
    "average_cost_usd",
    "estimated_cost_usd",
    "estimated_cost_per_claim",
    "total_cost_usd",
}


# Helper headers
ADMIN_HEADERS = {"Authorization": "Bearer dev-admin-token"}
OPERATOR_HEADERS = {"Authorization": "Bearer dev-operator-token"}
REVIEWER_HEADERS = {"Authorization": "Bearer dev-reviewer-token"}
SENIOR_HEADERS = {"Authorization": "Bearer dev-senior-token"}
VIEWER_HEADERS = {"Authorization": "Bearer dev-viewer-token"}


# ======================================================================
# Category A: API Application Creation
# ======================================================================
def test_category_a_application_creation(client: TestClient) -> None:
    """A: FastAPI application factory correctly creates app instance."""
    app = create_app()
    assert app.title == "CASEFILE Adjudication Platform API"
    assert app.version == "0.1.0"
    assert len(app.routes) > 10


# ======================================================================
# Category B: API Versioning
# ======================================================================
def test_category_b_api_versioning(client: TestClient) -> None:
    """B: All business endpoints are mounted under /api/v1/ namespace."""
    # Health routes are unversioned root probes
    resp_health = client.get("/health")
    assert resp_health.status_code == 200

    resp_ready = client.get("/ready")
    assert resp_ready.status_code == 200

    # Business routes require /api/v1/...
    resp_unversioned = client.get("/claims", headers=OPERATOR_HEADERS)
    assert resp_unversioned.status_code == 404

    resp_versioned = client.get("/api/v1/workflows", headers=VIEWER_HEADERS)
    assert resp_versioned.status_code == 200


# ======================================================================
# Category C: Claim Submission
# ======================================================================
def test_category_c_claim_submission(client: TestClient) -> None:
    """C: POST /api/v1/claims accepts typed claim and initiates workflow."""
    claim_payload = {
        "policy_id": "POL-7001",
        "claimant_name": "Jane Doe",
        "incident_date": "2026-04-10",
        "claim_amount": "2500.00",
        "description": "Fender collision with parked vehicle in garage.",
        "documents": ["estimate_7001.pdf"],
    }
    resp = client.post("/api/v1/claims", json=claim_payload, headers=OPERATOR_HEADERS)
    assert resp.status_code == 201
    data = resp.json()
    assert "workflow_run_id" in data
    assert "claim_id" in data
    assert data["current_state"] in ("HUMAN_APPROVAL", "APPROVED", "REJECTED")
    assert "/api/v1/workflows/" in data["status_url"]


# ======================================================================
# Category D: Workflow Status
# ======================================================================
def test_category_d_workflow_status(client: TestClient) -> None:
    """D: GET /api/v1/workflows/{id} returns sanitized operational details."""
    claim_payload = {
        "policy_id": "POL-7002",
        "claimant_name": "John Smith",
        "incident_date": "2026-04-11",
        "claim_amount": "1200.00",
        "description": "Cracked windshield from road gravel.",
    }
    create_resp = client.post("/api/v1/claims", json=claim_payload, headers=OPERATOR_HEADERS)
    w_id = create_resp.json()["workflow_run_id"]

    resp = client.get(f"/api/v1/workflows/{w_id}", headers=VIEWER_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["workflow_run_id"] == w_id
    assert "budget_usage" in data
    assert "estimated_cost" in data
    assert "checkpoint_count" in data
    # Verify no raw documents or unrestricted prompts leaked
    assert "prompt" not in data
    assert "password" not in data


def _submit_claim(
    client: TestClient,
    policy_id: str,
    description: str = "Hail damage to roof panels.",
) -> str:
    resp = client.post(
        "/api/v1/claims",
        json={
            "policy_id": policy_id,
            "claimant_name": "Ada Lovelace",
            "incident_date": "2026-04-12",
            "claim_amount": "900.00",
            "description": description,
        },
        headers=OPERATOR_HEADERS,
    )
    return str(resp.json()["workflow_run_id"])


def test_category_d_budget_limits_come_from_run_envelope(
    client: TestClient, test_engine: Engine
) -> None:
    """D: status reports the run's real envelope ceilings, not hard-coded ones."""
    w_id = _submit_claim(client, "POL-7004")

    # Swap in a deliberately non-default envelope for the captured one.
    tightened = BudgetEnvelope(max_steps=7, max_cost_usd=Decimal("1.23"))
    with Session(test_engine) as session:
        row = session.get(RunBudgetEnvelopeRow, w_id)
        assert row is not None
        row.envelope_json = tightened.model_dump_json()
        session.commit()

    resp = client.get(f"/api/v1/workflows/{w_id}", headers=VIEWER_HEADERS)
    assert resp.status_code == 200
    usage = resp.json()["budget_usage"]
    assert usage["max_steps"] == 7
    assert Decimal(str(usage["max_cost_usd"])) == Decimal("1.23")


def test_category_d_submitted_runs_capture_envelope_and_usage(client: TestClient) -> None:
    """D: API-submitted runs get an envelope plus real token/step accounting."""
    w_id = _submit_claim(client, "POL-7005")

    resp = client.get(f"/api/v1/workflows/{w_id}", headers=VIEWER_HEADERS)
    assert resp.status_code == 200
    usage = resp.json()["budget_usage"]
    assert usage["max_steps"] == BudgetEnvelope().max_steps
    assert Decimal(str(usage["max_cost_usd"])) == BudgetEnvelope().max_cost_usd
    # Deterministic agents are priced at zero and report tokens proportional to
    # the payload, so usage is recorded but labelled synthetic.
    assert usage["usage_source"] == "synthetic"
    assert usage["total_tokens"] > 0
    assert usage["steps_used"] > 0
    assert Decimal(str(usage["estimated_cost_usd"])) == Decimal("0")


def test_category_d_synthetic_tokens_track_the_payload(client: TestClient) -> None:
    """D: token counts follow prompt and response size, not a fixed constant."""
    short_id = _submit_claim(client, "POL-7007", description="Broken window.")
    long_id = _submit_claim(client, "POL-7008", description="Hail damage to roof panels. " * 40)

    short = client.get(f"/api/v1/workflows/{short_id}", headers=VIEWER_HEADERS).json()
    long = client.get(f"/api/v1/workflows/{long_id}", headers=VIEWER_HEADERS).json()

    assert short["budget_usage"]["usage_source"] == "synthetic"
    assert long["budget_usage"]["usage_source"] == "synthetic"
    assert long["budget_usage"]["input_tokens"] > short["budget_usage"]["input_tokens"]
    assert short["budget_usage"]["total_tokens"] > 0


def test_category_d_budget_limits_unknown_without_envelope(
    client: TestClient, test_engine: Engine
) -> None:
    """D: without an envelope the API reports unknown limits, never invented ones."""
    w_id = _submit_claim(client, "POL-7006")

    with Session(test_engine) as session:
        row = session.get(RunBudgetEnvelopeRow, w_id)
        assert row is not None
        session.delete(row)
        session.commit()

    resp = client.get(f"/api/v1/workflows/{w_id}", headers=VIEWER_HEADERS)
    assert resp.status_code == 200
    usage = resp.json()["budget_usage"]
    assert usage["max_steps"] is None
    assert usage["max_cost_usd"] is None


# ======================================================================
# Category E: Workflow History
# ======================================================================
def test_category_e_workflow_history(client: TestClient) -> None:
    """E: GET /api/v1/workflows/{id}/history returns append-only transition trail."""
    claim_payload = {
        "policy_id": "POL-7003",
        "claimant_name": "Bob Vance",
        "incident_date": "2026-04-12",
        "claim_amount": "800.00",
        "description": "Side mirror damaged.",
    }
    create_resp = client.post("/api/v1/claims", json=claim_payload, headers=OPERATOR_HEADERS)
    w_id = create_resp.json()["workflow_run_id"]

    resp = client.get(f"/api/v1/workflows/{w_id}/history", headers=VIEWER_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "transitions" in data
    assert len(data["transitions"]) >= 1
    assert data["transitions"][0]["to_state"] in [s.value for s in WorkflowState]


# ======================================================================
# Category F: Agent Visibility
# ======================================================================
def test_category_f_agent_visibility(client: TestClient, api_service: ApiService) -> None:
    """F: GET /api/v1/workflows/{id}/agents returns sanitized execution records."""
    claim_payload = {
        "policy_id": "POL-7004",
        "claimant_name": "Phyllis Lapin",
        "incident_date": "2026-04-13",
        "claim_amount": "950.00",
        "description": "Minor bumper scratch.",
    }
    create_resp = client.post("/api/v1/claims", json=claim_payload, headers=OPERATOR_HEADERS)
    w_id = create_resp.json()["workflow_run_id"]

    resp = client.get(f"/api/v1/workflows/{w_id}/agents", headers=OPERATOR_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["executions"], "expected at least one recorded agent execution"
    for execution in data["executions"]:
        summary = execution["tokens_summary"]
        # Deterministic doubles produced these counts, so they are labelled
        # the same way the workflow status endpoint labels them.
        assert summary["usage_source"] == "synthetic"
        assert summary["total_tokens"] > 0


# ======================================================================
# Category G: Tool Visibility
# ======================================================================
def test_category_g_tool_visibility(client: TestClient) -> None:
    """G: GET /api/v1/workflows/{id}/tools returns sanitized invocations."""
    claim_payload = {
        "policy_id": "POL-7005",
        "claimant_name": "Stanley Hudson",
        "incident_date": "2026-04-14",
        "claim_amount": "3200.00",
        "description": "Hail storm body damage.",
    }
    create_resp = client.post("/api/v1/claims", json=claim_payload, headers=OPERATOR_HEADERS)
    w_id = create_resp.json()["workflow_run_id"]

    resp = client.get(f"/api/v1/workflows/{w_id}/tools", headers=OPERATOR_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "invocations" in data


# ======================================================================
# Category H: Checkpoint Visibility
# ======================================================================
def test_category_h_checkpoint_visibility(client: TestClient) -> None:
    """H: GET /api/v1/workflows/{id}/checkpoints returns metadata-only checkpoints."""
    claim_payload = {
        "policy_id": "POL-7006",
        "claimant_name": "Kevin Malone",
        "incident_date": "2026-04-15",
        "claim_amount": "1400.00",
        "description": "Pothole suspension damage.",
    }
    create_resp = client.post("/api/v1/claims", json=claim_payload, headers=OPERATOR_HEADERS)
    w_id = create_resp.json()["workflow_run_id"]

    resp = client.get(f"/api/v1/workflows/{w_id}/checkpoints", headers=OPERATOR_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "checkpoints" in data
    assert len(data["checkpoints"]) >= 1
    cp = data["checkpoints"][0]
    assert cp["integrity_valid"] is True
    assert "checkpoint_id" in cp


# ======================================================================
# Category I: Replay Endpoint
# ======================================================================
def test_category_i_replay(client: TestClient) -> None:
    """I: POST /api/v1/workflows/{id}/replay executes deterministic simulation."""
    claim_payload = {
        "policy_id": "POL-7007",
        "claimant_name": "Oscar Martinez",
        "incident_date": "2026-04-16",
        "claim_amount": "750.00",
        "description": "Broken side glass.",
    }
    create_resp = client.post("/api/v1/claims", json=claim_payload, headers=OPERATOR_HEADERS)
    w_id = create_resp.json()["workflow_run_id"]

    resp = client.post(f"/api/v1/workflows/{w_id}/replay", headers=OPERATOR_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["replay_status"] in ("MATCH", "DIVERGENCE")
    assert data["original_workflow_run_id"] == w_id
    assert data["replay_id"] != w_id
    assert data["is_simulation"] is True


# ======================================================================
# Category J: Replay Isolation
# ======================================================================
def test_category_j_replay_isolation(client: TestClient) -> None:
    """J: Replay execution never mutates original workflow run state."""
    claim_payload = {
        "policy_id": "POL-7008",
        "claimant_name": "Dwight Schrute",
        "incident_date": "2026-04-17",
        "claim_amount": "6000.00",
        "description": "Tractor collision damage.",
    }
    create_resp = client.post("/api/v1/claims", json=claim_payload, headers=OPERATOR_HEADERS)
    w_id = create_resp.json()["workflow_run_id"]

    orig_status = client.get(f"/api/v1/workflows/{w_id}", headers=VIEWER_HEADERS).json()

    # Run replay simulation
    client.post(f"/api/v1/workflows/{w_id}/replay", headers=OPERATOR_HEADERS)

    # Re-fetch status: original state must remain unchanged
    after_status = client.get(f"/api/v1/workflows/{w_id}", headers=VIEWER_HEADERS).json()
    assert orig_status["current_state"] == after_status["current_state"]
    assert orig_status["step_count"] == after_status["step_count"]


# ======================================================================
# Category K: Approval Queue
# ======================================================================
def test_category_k_approval_queue(client: TestClient, api_service: ApiService) -> None:
    """K: GET /api/v1/approvals returns pending approval queue items."""
    # Create a pending approval directly in service
    req = HumanApprovalRequest(
        workflow_run_id=uuid4(),
        claim_id=uuid4(),
        recommendation=Recommendation(
            claim_id=uuid4(),
            recommendation_type=RecommendationType.APPROVE_FULL,
            estimated_payout=Money(amount=Decimal("1500.00")),
            notes="Recommendation for queue test",
            confidence=0.95,
        ),
        reviewer_summary="Queue item test",
        deadline=datetime.now(UTC) + timedelta(hours=24),
    )
    api_service.approval_service.request_approval(req)

    resp = client.get("/api/v1/approvals", headers=REVIEWER_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "approvals" in data
    assert any(a["approval_id"] == str(req.approval_id) for a in data["approvals"])


# ======================================================================
# Category L: Approval Authorization
# ======================================================================
def test_category_l_approval_authorization(client: TestClient, api_service: ApiService) -> None:
    """L: Unauthorized roles (VIEWER, OPERATOR) cannot decide approvals."""
    req = HumanApprovalRequest(
        workflow_run_id=uuid4(),
        claim_id=uuid4(),
        recommendation=Recommendation(
            claim_id=uuid4(),
            recommendation_type=RecommendationType.APPROVE_FULL,
            estimated_payout=Money(amount=Decimal("1500.00")),
            notes="Auth check test",
            confidence=0.95,
        ),
        reviewer_summary="Auth check",
        deadline=datetime.now(UTC) + timedelta(hours=24),
    )
    api_service.approval_service.request_approval(req)

    # VIEWER cannot decide (403)
    resp_viewer = client.post(
        f"/api/v1/approvals/{req.approval_id}/decision",
        json={"decision": "APPROVED", "reason": "Viewer attempted approval"},
        headers=VIEWER_HEADERS,
    )
    assert resp_viewer.status_code == 403

    # OPERATOR cannot decide (403)
    resp_op = client.post(
        f"/api/v1/approvals/{req.approval_id}/decision",
        json={"decision": "APPROVED", "reason": "Operator attempted approval"},
        headers=OPERATOR_HEADERS,
    )
    assert resp_op.status_code == 403


# ======================================================================
# Category M: Approval Decision
# ======================================================================
def test_category_m_approval_decision(client: TestClient, api_service: ApiService) -> None:
    """M: Authorized CLAIM_REVIEWER can approve a pending request."""
    # First submit a claim that enters HUMAN_APPROVAL
    claim_payload = {
        "policy_id": "POL-7009",
        "claimant_name": "Angela Martin",
        "incident_date": "2026-04-18",
        "claim_amount": "1800.00",
        "description": "Fender collision damage.",
    }
    create_resp = client.post("/api/v1/claims", json=claim_payload, headers=OPERATOR_HEADERS)
    w_id = create_resp.json()["workflow_run_id"]

    # Locate pending approval for this run
    q_resp = client.get("/api/v1/approvals?status=PENDING", headers=REVIEWER_HEADERS)
    approvals = [a for a in q_resp.json()["approvals"] if a["workflow_run_id"] == w_id]

    if approvals:
        app_id = approvals[0]["approval_id"]
        dec_resp = client.post(
            f"/api/v1/approvals/{app_id}/decision",
            json={"decision": "APPROVED", "reason": "Verified estimate reasonableness"},
            headers=REVIEWER_HEADERS,
        )
        assert dec_resp.status_code == 200
        data = dec_resp.json()
        assert data["status"] == "APPROVED"
        assert data["outcome"] == "APPROVED"


# ======================================================================
# Category N: Self-Approval Protection
# ======================================================================
def test_category_n_self_approval_protection(client: TestClient, api_service: ApiService) -> None:
    """N: Separation of duties prevents requester from approving own request."""
    # Create request where requested_by == "reviewer-alice" (the identity in dev-reviewer-token)
    req = HumanApprovalRequest(
        workflow_run_id=uuid4(),
        claim_id=uuid4(),
        requested_by="reviewer-alice",
        recommendation=Recommendation(
            claim_id=uuid4(),
            recommendation_type=RecommendationType.APPROVE_FULL,
            estimated_payout=Money(amount=Decimal("1500.00")),
            notes="Self approval test",
            confidence=0.95,
        ),
        reviewer_summary="Self approval check",
        deadline=datetime.now(UTC) + timedelta(hours=24),
    )
    api_service.approval_service.request_approval(req)

    # reviewer-alice tries to approve: rejected
    resp = client.post(
        f"/api/v1/approvals/{req.approval_id}/decision",
        json={"decision": "APPROVED", "reason": "Approving own request"},
        headers=REVIEWER_HEADERS,  # Principal is reviewer-alice
    )
    assert resp.status_code == 403 or resp.status_code == 400
    assert "Separation of duties" in resp.text or "UNAUTHORIZED" in resp.text


# ======================================================================
# Category O: Expired Approval Protection
# ======================================================================
def test_category_o_expired_approval_protection(
    client: TestClient, api_service: ApiService
) -> None:
    """O: Deciding an expired approval request is rejected."""
    # Create request with past deadline
    past = datetime.now(UTC) - timedelta(hours=2)
    req = HumanApprovalRequest(
        workflow_run_id=uuid4(),
        claim_id=uuid4(),
        requested_at=past - timedelta(hours=1),
        deadline=past,
        recommendation=Recommendation(
            claim_id=uuid4(),
            recommendation_type=RecommendationType.APPROVE_FULL,
            estimated_payout=Money(amount=Decimal("1500.00")),
            notes="Expired test",
            confidence=0.95,
        ),
        reviewer_summary="Expired check",
    )
    # Persist directly with past deadline
    with api_service._uow() as uow:
        uow.approvals.save_request(req)

    resp = client.post(
        f"/api/v1/approvals/{req.approval_id}/decision",
        json={"decision": "APPROVED", "reason": "Late approval attempt"},
        headers=SENIOR_HEADERS,
    )
    assert resp.status_code == 409 or resp.status_code == 400
    assert "expired" in resp.text.lower()


# ======================================================================
# Category P: Authentication Abstraction
# ======================================================================
def test_category_p_authentication(client: TestClient) -> None:
    """P: Unauthenticated requests or invalid tokens are rejected with HTTP 401."""
    # No auth header
    resp_no_auth = client.get("/api/v1/workflows")
    assert resp_no_auth.status_code == 401
    assert "UNAUTHENTICATED" in resp_no_auth.text or "Missing" in resp_no_auth.text

    # Invalid token
    resp_bad = client.get("/api/v1/workflows", headers={"Authorization": "Bearer bad-token-999"})
    assert resp_bad.status_code == 401


# ======================================================================
# Category Q: Role-Based Authorization
# ======================================================================
def test_category_q_role_authorization(client: TestClient) -> None:
    """Q: RBAC matrix enforces role boundaries across routes."""
    # VIEWER cannot submit claims
    claim_payload = {
        "policy_id": "POL-7010",
        "claimant_name": "Toby Flenderson",
        "incident_date": "2026-04-19",
        "claim_amount": "500.00",
        "description": "Fender scratch.",
    }
    resp = client.post("/api/v1/claims", json=claim_payload, headers=VIEWER_HEADERS)
    assert resp.status_code == 403

    # VIEWER cannot access audit log
    resp_audit = client.get("/api/v1/audit", headers=VIEWER_HEADERS)
    assert resp_audit.status_code == 403


# ======================================================================
# Category R: Structured Error Model
# ======================================================================
def test_category_r_structured_errors(client: TestClient) -> None:
    """R: Error responses conform to uniform envelope without stack traces."""
    # 404 error
    bad_id = uuid4()
    resp = client.get(f"/api/v1/workflows/{bad_id}", headers=VIEWER_HEADERS)
    assert resp.status_code == 404
    data = resp.json()
    assert "error" in data
    assert "code" in data["error"]
    assert "message" in data["error"]
    assert "request_id" in data["error"]
    assert "correlation_id" in data["error"]
    assert "Traceback" not in resp.text


# ======================================================================
# Category S: Idempotency
# ======================================================================
def test_category_s_idempotency(client: TestClient) -> None:
    """S: Idempotent key returns identical result; conflicting reuse returns HTTP 409."""
    key = f"idem-key-{uuid4().hex}"
    claim_payload = {
        "policy_id": "POL-7011",
        "claimant_name": "Jim Halpert",
        "incident_date": "2026-04-20",
        "claim_amount": "1000.00",
        "description": "Rear light crack.",
    }

    # First attempt: succeeds
    resp1 = client.post(
        "/api/v1/claims",
        json=claim_payload,
        headers={**OPERATOR_HEADERS, "Idempotency-Key": key},
    )
    assert resp1.status_code == 201
    w_id1 = resp1.json()["workflow_run_id"]

    # Identical replay: returns same cached response
    resp2 = client.post(
        "/api/v1/claims",
        json=claim_payload,
        headers={**OPERATOR_HEADERS, "Idempotency-Key": key},
    )
    assert resp2.status_code == 201
    assert resp2.json()["workflow_run_id"] == w_id1

    # Conflicting reuse with different payload: returns 409
    conflicting_payload = {**claim_payload, "claim_amount": "9999.00"}
    resp3 = client.post(
        "/api/v1/claims",
        json=conflicting_payload,
        headers={**OPERATOR_HEADERS, "Idempotency-Key": key},
    )
    assert resp3.status_code == 409


# ======================================================================
# Category T: Rate Limiting
# ======================================================================
def test_category_t_rate_limiting(client: TestClient) -> None:
    """T: Exceeding route rate limit triggers HTTP 429 with Retry-After header."""
    # Use a custom tight rate limiter for this test
    tight_limiter = InMemoryRateLimiter()
    set_rate_limiter(tight_limiter)

    claim_payload = {
        "policy_id": "POL-7012",
        "claimant_name": "Pam Beesly",
        "incident_date": "2026-04-21",
        "claim_amount": "600.00",
        "description": "Side mirror dent.",
    }

    # Exhaust slots (claims limit is 60)
    for _ in range(60):
        tight_limiter.acquire("POST:/api/v1/claims:operator-charlie", 60, 60.0)

    # 61st request fails
    resp = client.post("/api/v1/claims", json=claim_payload, headers=OPERATOR_HEADERS)
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers


# ======================================================================
# Category U: Health
# ======================================================================
def test_category_u_health(client: TestClient) -> None:
    """U: GET /health indicates process liveness."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert data["service"] == "casefile-api"


# ======================================================================
# Category V: Readiness
# ======================================================================
def test_category_v_readiness(client: TestClient) -> None:
    """V: GET /ready checks SQLite connectivity."""
    resp = client.get("/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is True
    assert data["services"]["sqlite"]["status"] == "healthy"


# ======================================================================
# Category W: API Observability
# ======================================================================
def test_category_w_api_observability(client: TestClient) -> None:
    """W: API responses include X-Request-ID and X-Correlation-ID headers."""
    resp = client.get(
        "/api/v1/workflows",
        headers={**VIEWER_HEADERS, "X-Request-ID": "custom-req-123"},
    )
    assert resp.status_code == 200
    assert resp.headers["X-Request-ID"] == "custom-req-123"
    assert "X-Correlation-ID" in resp.headers


# ======================================================================
# Category X: Telemetry Sanitization
# ======================================================================
def test_category_x_telemetry_sanitization(client: TestClient) -> None:
    """X: Sensitive headers like Authorization are never echoed or leaked."""
    resp = client.get(
        "/api/v1/workflows",
        headers={"Authorization": "Bearer secret-token-that-must-not-leak"},
    )
    assert "secret-token-that-must-not-leak" not in resp.text


# ======================================================================
# Category Y: OpenAPI Schema
# ======================================================================
def test_category_y_openapi(client: TestClient) -> None:
    """Y: OpenAPI schema documents all route tags and models."""
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    data = resp.json()
    assert "paths" in data
    paths = data["paths"]
    assert "/api/v1/claims" in paths
    assert "/api/v1/workflows/{workflow_run_id}" in paths
    assert "/api/v1/approvals" in paths
    assert "/health" in paths


# ======================================================================
# Category Z: Evaluation Endpoint
# ======================================================================
def test_category_z_evaluation_endpoint(client: TestClient) -> None:
    """Z: GET /api/v1/evaluations reports the results recorded on disk."""
    report = json.loads(Path("evaluation-report.json").read_text(encoding="utf-8"))
    recorded = report["results"]

    resp = client.get("/api/v1/evaluations", headers=VIEWER_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_cases"] == len(recorded)
    assert data["passed"] == sum(1 for case in recorded if case["status"] == "PASS")
    assert data["pass_rate"] == report["metrics"]["scenario_pass_rate"]
    assert [case["case_id"] for case in data["cases"]] == [case["scenario_id"] for case in recorded]

    resp_cases = client.get("/api/v1/evaluations/cases", headers=VIEWER_HEADERS)
    assert resp_cases.status_code == 200
    assert len(resp_cases.json()) == len(recorded)


def test_category_z_evaluation_reports_only_recorded_cases(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Z: the endpoint never pads results beyond what the report contains."""
    report = {
        "results": [
            {"scenario_id": "CASE-001", "status": "PASS", "terminal_state": "APPROVED"},
            {"scenario_id": "CASE-002", "status": "FAIL", "terminal_state": "FAILED"},
        ]
    }
    (tmp_path / "evaluation-report.json").write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    data = client.get("/api/v1/evaluations", headers=VIEWER_HEADERS).json()
    assert data["total_cases"] == 2
    assert data["passed"] == 1
    assert data["failed"] == 1
    assert data["pass_rate"] == 0.5
    assert [case["case_id"] for case in data["cases"]] == ["CASE-001", "CASE-002"]
    # Fields the report does not record stay unknown instead of being assumed.
    assert data["path_accuracy"] is None
    assert data["average_steps"] is None
    assert all(case["path_matched"] is None for case in data["cases"])


def test_category_z_evaluation_with_no_recorded_cases(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Z: an empty report yields no cases and an unknown pass rate."""
    (tmp_path / "evaluation-report.json").write_text(json.dumps({"results": []}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    data = client.get("/api/v1/evaluations", headers=VIEWER_HEADERS).json()
    assert data["total_cases"] == 0
    assert data["cases"] == []
    assert data["pass_rate"] is None

    metrics = client.get("/api/v1/metrics", headers=VIEWER_HEADERS).json()
    assert metrics["evaluation_pass_rate"] is None


def test_category_z_no_fabricated_evaluation_results() -> None:
    """Z: the evaluation mapping cannot invent outcomes, steps or rates."""
    result_fields = {
        "status",
        "category",
        "expected_terminal_state",
        "terminal_state",
        "path_matched",
        "terminal_matched",
        "steps",
        "cost_usd",
        "pass_rate",
        "path_accuracy",
        "terminal_state_accuracy",
        "replay_determinism_rate",
        "budget_enforcement_rate",
        "approval_safety_rate",
        "failure_containment_rate",
        "average_cost",
        "average_steps",
    }
    tree = ast.parse(_API_SERVICE_PATH.read_text(encoding="utf-8"))
    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name in {"get_evaluation_summary", "_evaluation_case_summary"}
    ]
    assert functions, "evaluation mapping helpers not found"

    findings: list[str] = []
    for function in functions:
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name) and node.func.id == "range":
                findings.append(f"line {node.lineno}: padding loop")
            for keyword in node.keywords:
                if keyword.arg in result_fields and isinstance(keyword.value, ast.Constant):
                    findings.append(f"line {keyword.value.lineno}: {keyword.arg}=<literal>")

    assert not findings, "fabricated evaluation results: " + "; ".join(findings)


def test_category_z_evaluation_costs_are_unknown_when_unrecorded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Z: a report without cost data yields null, never a stand-in number."""
    report = {
        "results": [{"scenario_id": "CASE-001", "status": "PASS", "terminal_state": "APPROVED"}]
    }
    (tmp_path / "evaluation-report.json").write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    data = client.get("/api/v1/evaluations", headers=VIEWER_HEADERS).json()
    assert data["average_cost"] is None
    assert data["cases"]
    assert all(case["cost_usd"] is None for case in data["cases"])

    metrics = client.get("/api/v1/metrics", headers=VIEWER_HEADERS).json()
    assert metrics["average_cost_usd"] is None


def test_category_z_evaluation_costs_are_read_from_the_report(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Z: recorded costs are reported verbatim instead of a placeholder."""
    report = {
        "results": [
            {
                "scenario_id": "CASE-001",
                "status": "PASS",
                "terminal_state": "APPROVED",
                "cost_usd": "0.0123",
            }
        ],
        "metrics": {"estimated_cost_per_claim": "0.0456"},
    }
    (tmp_path / "evaluation-report.json").write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    data = client.get("/api/v1/evaluations", headers=VIEWER_HEADERS).json()
    assert data["average_cost"] == "0.0456"
    recorded = {case["case_id"]: case["cost_usd"] for case in data["cases"]}
    assert recorded == {"CASE-001": "0.0123"}


def test_category_z_no_fabricated_cost_constants() -> None:
    """Z: no cost field in the API layer is assigned a numeric literal."""

    def numeric_literal(node: ast.expr) -> object | None:
        if isinstance(node, ast.Constant):
            value = node.value
            if isinstance(value, bool):
                return None
            if isinstance(value, int | float):
                return value
            if isinstance(value, str):
                try:
                    float(value)
                except ValueError:
                    return None
                return value
        return None

    tree = ast.parse(_API_SERVICE_PATH.read_text(encoding="utf-8"))
    findings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg in _COST_FIELDS and numeric_literal(keyword.value) is not None:
                findings.append(f"line {keyword.value.lineno}: {keyword.arg}=<literal>")
        if isinstance(node.func, ast.Attribute) and node.func.attr == "get" and node.args:
            key = node.args[0]
            if isinstance(key, ast.Constant) and key.value in _COST_FIELDS:
                for default in node.args[1:]:
                    if numeric_literal(default) is not None:
                        findings.append(f"line {default.lineno}: default for {key.value!r}")

    assert not findings, "fabricated cost constants: " + "; ".join(findings)


# ======================================================================
# Category AA: Audit Endpoint
# ======================================================================
def test_category_aa_audit_endpoint(client: TestClient) -> None:
    """AA: GET /api/v1/audit returns immutable audit logs."""
    resp = client.get("/api/v1/audit", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "events" in data
    assert "total" in data


# ======================================================================
# Category AB: Console Data Contracts
# ======================================================================
def test_category_ab_console_data_contracts(client: TestClient) -> None:
    """AB: GET /api/v1/metrics returns dashboard metrics for Operations Console."""
    resp = client.get("/api/v1/metrics", headers=VIEWER_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "active_workflows" in data
    assert "completed_workflows" in data
    assert "failed_workflows" in data
    assert "pending_approvals" in data
    assert "system_health" in data


# ======================================================================
# Category AC: E2E Complete Adjudication Flow
# ======================================================================
def test_category_ac_e2e_complete_flow(client: TestClient) -> None:
    """
    AC: Complete API-level End-to-End flow:
    Claim submission -> Execution -> HUMAN_APPROVAL -> Approval Decision
    -> Terminal state -> History -> Checkpoints -> Replay -> Evaluation.
    """
    # 1. Claim submission
    claim_payload = {
        "policy_id": "POL-9900",
        "claimant_name": "Michael Scott",
        "incident_date": "2026-04-22",
        "claim_amount": "3500.00",
        "description": "Forklift collision with storage shelving.",
        "documents": ["report_9900.pdf"],
    }
    sub_resp = client.post("/api/v1/claims", json=claim_payload, headers=OPERATOR_HEADERS)
    assert sub_resp.status_code == 201
    w_id = sub_resp.json()["workflow_run_id"]

    # 2. Check workflow status (halts in HUMAN_APPROVAL)
    status_resp = client.get(f"/api/v1/workflows/{w_id}", headers=VIEWER_HEADERS)
    assert status_resp.status_code == 200
    assert status_resp.json()["current_state"] == "HUMAN_APPROVAL"

    # 3. List approvals and find pending request
    q_resp = client.get("/api/v1/approvals?status=PENDING", headers=REVIEWER_HEADERS)
    assert q_resp.status_code == 200
    approvals = [a for a in q_resp.json()["approvals"] if a["workflow_run_id"] == w_id]
    assert len(approvals) >= 1
    app_id = approvals[0]["approval_id"]

    # 4. Submit approval decision by authorized CLAIM_REVIEWER
    dec_resp = client.post(
        f"/api/v1/approvals/{app_id}/decision",
        json={"decision": "APPROVED", "reason": "E2E review confirmed complete evidence"},
        headers=REVIEWER_HEADERS,
    )
    assert dec_resp.status_code == 200
    assert dec_resp.json()["status"] == "APPROVED"

    # 5. Check terminal state is now APPROVED
    final_status = client.get(f"/api/v1/workflows/{w_id}", headers=VIEWER_HEADERS)
    assert final_status.status_code == 200
    assert final_status.json()["current_state"] == "APPROVED"
    assert final_status.json()["is_terminal"] is True

    # 6. Retrieve workflow history
    hist_resp = client.get(f"/api/v1/workflows/{w_id}/history", headers=VIEWER_HEADERS)
    assert hist_resp.status_code == 200
    assert len(hist_resp.json()["transitions"]) >= 1

    # 7. Retrieve checkpoints
    cp_resp = client.get(f"/api/v1/workflows/{w_id}/checkpoints", headers=OPERATOR_HEADERS)
    assert cp_resp.status_code == 200
    assert len(cp_resp.json()["checkpoints"]) >= 1

    # 8. Replay simulation
    replay_resp = client.post(f"/api/v1/workflows/{w_id}/replay", headers=OPERATOR_HEADERS)
    assert replay_resp.status_code == 200
    assert replay_resp.json()["is_simulation"] is True

    # 9. Verify evaluation visibility
    eval_resp = client.get("/api/v1/evaluations", headers=VIEWER_HEADERS)
    assert eval_resp.status_code == 200
    report = json.loads(Path("evaluation-report.json").read_text(encoding="utf-8"))
    assert eval_resp.json()["total_cases"] == len(report["results"])

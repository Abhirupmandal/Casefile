"""Integration tests for Phase 12.5 Explainability & Counterfactual Intelligence.

Verifies:
1. End-to-end workflow execution generates, attaches, and persists DecisionExplanation
   with sensitive counterfactuals and verifiable citations.
2. Audit trail captures EXPLANATION_GENERATED with explanation ID and factor summary.
3. REST API endpoints:
   - GET /api/v1/workflows/{workflow_run_id}/explain
   - GET /api/v1/claims/{claim_id}/explain
4. Role-based view filtering:
   - Adjuster/reviewer principal receives full explainability dossier.
   - Claimant principal receives strictly sanitized claimant-safe view without internal weights.
5. Alembic migration 0012 upgrade and downgrade reversibility.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from unittest import mock
from uuid import UUID

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from casefile.api.auth import Principal, Role
from casefile.api.schemas.models import ClaimSubmissionRequest
from casefile.api.service import ApiService
from casefile.models.domain import AuditEventType
from casefile.models.persistence import get_engine


def _make_api_service() -> ApiService:
    """Instantiate an in-memory ApiService with all tables initialized."""
    return ApiService(database_url="sqlite:///:memory:")


def test_end_to_end_explanation_lifecycle() -> None:
    """Verify that submitting a claim produces an explanation brief and audit event."""
    svc = _make_api_service()
    reviewer = Principal(
        principal_id="rev-101",
        role=Role.CLAIM_REVIEWER,
        display_name="Senior Reviewer",
    )

    req = ClaimSubmissionRequest(
        policy_id="POL-3001",
        claimant_name="Sarah Connor",
        claim_amount=Decimal("4200.00"),
        incident_date="2026-03-01",
        description="Collision with highway guardrail during snowstorm",
        documents=["police_report_3001.pdf", "shop_estimate_3001.pdf"],
    )

    resp = svc.submit_claim(req)
    assert resp.claim_id is not None
    assert resp.workflow_run_id is not None

    wf_run_id = UUID(resp.workflow_run_id)
    claim_id = UUID(resp.claim_id)

    # 1. Fetch explanation via Workflow endpoint
    exp_wf = svc.get_workflow_explanation(wf_run_id, principal=reviewer)
    assert exp_wf.workflow_run_id == str(wf_run_id)
    assert exp_wf.claim_id == str(claim_id)
    assert exp_wf.recommendation in ("APPROVE", "APPROVE_PARTIAL")
    assert len(exp_wf.key_factors) > 0
    assert len(exp_wf.counterfactuals) >= 2
    assert len(exp_wf.factor_contributions) > 0
    assert exp_wf.is_claimant_safe is False

    # 2. Fetch explanation via Claim endpoint
    exp_claim = svc.get_claim_explanation(claim_id, principal=reviewer)
    assert exp_claim.explanation_id == exp_wf.explanation_id
    assert exp_claim.summary == exp_wf.summary

    # 3. Verify database persistence and audit trail
    with svc._uow() as uow:
        db_exp = uow.explanations.get_for_workflow(wf_run_id)
        assert db_exp is not None
        assert db_exp.explanation_id == UUID(exp_wf.explanation_id)

        cfs = uow.explanations.list_counterfactuals_for_workflow(wf_run_id)
        assert len(cfs) >= 2

        events = uow.audit.list_for_run(wf_run_id)
        exp_events = [e for e in events if e.event_type == AuditEventType.EXPLANATION_GENERATED]
        assert len(exp_events) >= 1
        assert exp_events[0].attributes.get("explanation_id") == str(db_exp.explanation_id)


def test_claimant_sanitized_explanation_view() -> None:
    """Verify that a claimant principal receives a sanitized explanation without internal weights."""
    svc = _make_api_service()

    req = ClaimSubmissionRequest(
        policy_id="POL-7701",
        claimant_name="Kyle Reese",
        claim_amount=Decimal("2800.00"),
        incident_date="2026-03-02",
        description="Minor hail storm denting on hood",
        documents=["hail_estimate.pdf"],
    )

    resp = svc.submit_claim(req)
    claim_id = UUID(resp.claim_id)

    # Retrieve customer_id from claim record
    with svc._uow() as uow:
        from casefile.models.persistence import ClaimRecord

        crow = uow.session.get(ClaimRecord, str(claim_id))
        cust_id = crow.customer_id if crow else "CUST-UNKNOWN"

    claimant_principal = Principal(
        principal_id=cust_id,
        role=Role.CLAIMANT,
        display_name="Kyle Reese",
    )

    claimant_exp = svc.get_claim_explanation(claim_id, principal=claimant_principal)

    assert claimant_exp.is_claimant_safe is True
    # Factor contributions (internal weights) MUST be stripped
    assert len(claimant_exp.factor_contributions) == 0
    # Assessment ID MUST be redacted
    assert claimant_exp.assessment_id is None
    # Citations with sensitive internal keywords must not be present
    assert not any("blacklist" in c.lower() for c in claimant_exp.evidence_citations)
    # Limitations must clearly advise hypothetical nature
    assert any("advisory" in lim.lower() for lim in claimant_exp.limitations)


def test_alembic_migration_0012_upgrade_and_downgrade(tmp_path: Path) -> None:
    """Verify that migration 0012 upgrades and downgrades cleanly without errors."""
    db_path = tmp_path / "alembic_test_0012.db"
    db_url = f"sqlite:///{db_path}"

    repo_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(repo_root / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", db_url)

    # Upgrade to head (includes 0012_decision_explanations)
    with mock.patch.dict(os.environ, {"CASEFILE_DATABASE_URL": db_url}):
        command.upgrade(cfg, "head")

    engine = get_engine(db_url)
    tables = set(inspect(engine).get_table_names())
    assert "decision_explanations" in tables
    assert "counterfactual_scenarios" in tables

    # Downgrade to 0011_counter_offers
    with mock.patch.dict(os.environ, {"CASEFILE_DATABASE_URL": db_url}):
        command.downgrade(cfg, "0011_counter_offers")

    engine.dispose()
    engine = get_engine(db_url)
    tables_after = set(inspect(engine).get_table_names())
    assert "decision_explanations" not in tables_after
    assert "counter_offers" in tables_after

    # Re-upgrade to head
    with mock.patch.dict(os.environ, {"CASEFILE_DATABASE_URL": db_url}):
        command.upgrade(cfg, "head")

    engine.dispose()
    engine = get_engine(db_url)
    tables_reup = set(inspect(engine).get_table_names())
    assert "decision_explanations" in tables_reup
    engine.dispose()

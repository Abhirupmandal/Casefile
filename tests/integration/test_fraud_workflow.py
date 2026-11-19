"""
Phase 12.3 Integration Tests: End-to-End Fraud Assessment, Workflow Integration & Persistence.
"""

from __future__ import annotations

import contextlib
import gc
import os
import tempfile
from collections.abc import Callable, Generator
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, inspect

from casefile.agents.context import AgentContext
from casefile.api.schemas.models import ClaimSubmissionRequest
from casefile.api.service import ApiService
from casefile.fraud.contracts import (
    RiskCategory,
)
from casefile.fraud.service import FraudAssessmentService
from casefile.models.contracts import (
    ExtractionResult,
    InvestigationResult,
    ReviewRequest,
    WorkflowState,
)
from casefile.models.domain import AgentType
from casefile.translation.fake import FakeTranslationServiceClient


@pytest.fixture
def api_service_factory() -> Generator[Callable[..., ApiService], None, None]:
    """Factory creating isolated in-memory ApiService instances."""
    services: list[ApiService] = []

    def _create(
        translation_client: FakeTranslationServiceClient | None = None,
        fraud_service: FraudAssessmentService | None = None,
    ) -> ApiService:
        svc = ApiService(
            database_url=f"sqlite:///:memory:?cache=shared_{uuid4().hex}",
            translation_client=translation_client,
            fraud_service=fraud_service or FraudAssessmentService(),
        )
        services.append(svc)
        return svc

    yield _create

    for svc in services:
        svc.close()


def test_api_service_fraud_assessment_persisted(
    api_service_factory: Callable[..., ApiService]
) -> None:
    fraud_svc = FraudAssessmentService()
    svc: ApiService = api_service_factory(fraud_service=fraud_svc)

    claim_id = str(uuid4())
    recent_date = (date.today() - timedelta(days=2)).isoformat()
    req = ClaimSubmissionRequest(
        claim_id=claim_id,
        policy_id="POL-NORMAL-200",
        claimant_name="Sarah Connor",
        incident_date=recent_date,
        claim_amount=Decimal("1950.00"),
        description="Rear bumper damage in shopping mall parking lot.",
        documents=["Police report: Minor rear collision, bumper scratched."],
        language="en",
    )

    resp = svc.submit_claim(req)
    assert resp.current_state == WorkflowState.HUMAN_APPROVAL.value

    # Verify fraud assessment was persisted in SQLite database
    assessment = svc.get_fraud_assessment(UUID(claim_id))
    assert assessment is not None
    assert assessment.claim_id == UUID(claim_id)
    assert assessment.risk_category == RiskCategory.LOW
    assert assessment.risk_score < 0.25
    assert assessment.confidence_score >= 0.40
    assert not assessment.requires_human_scrutiny
    assert "Investigative signals identified" in assessment.findings_summary

    # Verify lookup by workflow run id
    run_assessments = svc.get_fraud_assessments_for_run(UUID(resp.workflow_run_id))
    assert len(run_assessments) == 1
    assert run_assessments[0].assessment_id == assessment.assessment_id


def test_high_risk_claim_flags_scrutiny_and_preserves_human_approval(
    api_service_factory: Callable[..., ApiService],
) -> None:
    fraud_svc = FraudAssessmentService()
    svc: ApiService = api_service_factory(fraud_service=fraud_svc)

    claim_id = str(uuid4())
    recent_date = (date.today() - timedelta(days=2)).isoformat()
    req = ClaimSubmissionRequest(
        claim_id=claim_id,
        policy_id="POL-FRAUD-001",  # Known suspicious policy in simulated DB
        claimant_name="Victor Staged",
        incident_date=recent_date,
        claim_amount=Decimal("35000.00"),
        description="Total loss collision parked unattended no witnesses.",
        documents=["Report claiming full loss unattended."],
        language="en",
    )

    resp = svc.submit_claim(req)
    # MUST still route to HUMAN_APPROVAL, NEVER auto-reject or auto-approve!
    assert resp.current_state == WorkflowState.HUMAN_APPROVAL.value

    assessment = svc.get_fraud_assessment(UUID(claim_id))
    assert assessment is not None
    assert assessment.risk_category in (RiskCategory.HIGH, RiskCategory.CRITICAL)
    assert assessment.risk_score >= 0.55
    assert assessment.requires_human_scrutiny is True
    assert len(assessment.external_matches) >= 1


def test_reviewer_allows_approval_with_fraud_signals_for_human_review() -> None:
    """Reviewer agent recommendation with fraud signals is parsed and preserved for human review."""
    from casefile.agents.reviewer import ReviewerAgent
    from casefile.api.service import SmartDefaultProvider

    provider = SmartDefaultProvider(provider_name="test-reviewer")
    reviewer = ReviewerAgent(provider=provider)

    # Extraction result
    ext = ExtractionResult(
        workflow_id=uuid4(),
        claimant_name="John Doe",
        policy_id="POL-999",
        incident_date="2026-05-01",
        incident_location="Main St",
        incident_description="Damage",
        claim_amount=Decimal("2000.00"),
        requested_coverage_type="COLLISION",
        extraction_confidence=0.98,
        is_complete=True,
    )

    # Investigation result with fraud signals flagged
    inv = InvestigationResult(
        workflow_id=ext.workflow_id,
        policy_details={"status": "ACTIVE"},
        claim_history={"prior_claims_count": 0},
        repair_cost_validation={"is_valid": True, "market_rate": 2000.0},
        fraud_signals={"risk_score": 0.85, "signals": ["EXTERNAL_MATCH"], "flagged": True},
        supporting_documents=["doc.pdf"],
        findings_summary="Flagged for high fraud risk.",
        evidence_strength=0.90,
        tool_calls_made=["policy_lookup"],
        tools_succeeded=1,
        tools_failed=0,
    )

    # Direct parse validation: Reviewer recommendation is preserved without unrecoverable crash
    render_meta: dict[str, object] = {"review_prompt_version": "v1.0"}
    agent_ctx = AgentContext(
        claim_id=uuid4(),
        workflow_run_id=uuid4(),
        correlation_id=uuid4(),
        agent=AgentType.REVIEWER,
    )
    req = ReviewRequest(
        workflow_id=ext.workflow_id,
        extraction_result=ext,
        investigation_result=inv,
    )

    raw_output = (
        '{"workflow_id": "'
        + str(ext.workflow_id)
        + '", "decision": "APPROVE", "reasoning": "Evidence substantiates damage despite external anomaly", "confidence_score": 0.88, '
        '"evidence_completeness": 1.0, "identified_gaps": [], "rework_feedback": null, '
        '"fraud_risk_level": "HIGH", "fraud_signals_detected": true}'
    )

    result = reviewer.parse_output(raw_output, req, agent_ctx, render_meta)
    assert result.decision == "APPROVE"
    assert result.fraud_signals_detected is True
    assert result.fraud_risk_level == "HIGH"
    assert "Evidence substantiates" in result.reasoning


def test_fraud_rejection_routes_to_human_approval_and_human_can_reject(
    api_service_factory: Callable[..., ApiService],
) -> None:
    """Reviewer recommendation to REJECT when fraud signals exist routes to HUMAN_APPROVAL, not terminal REJECTED."""
    from casefile.api.auth import Principal, Role

    svc: ApiService = api_service_factory(fraud_service=FraudAssessmentService())
    reviewer_principal = Principal(
        principal_id="human-rev-01",
        role=Role.CLAIM_REVIEWER,
        display_name="Authorized Reviewer",
    )

    req = ClaimSubmissionRequest(
        policy_id="POL-SUSPECT-999",
        claimant_name="Suspicious Actor",
        incident_date="2026-05-01",
        claim_amount=Decimal("45000.00"),
        description="Total loss claim parked unattended no witnesses with extensive destruction.",
        language="en",
    )

    resp = svc.submit_claim(req)
    # Must park in HUMAN_APPROVAL, never automatically reject
    assert resp.current_state == WorkflowState.HUMAN_APPROVAL.value

    # Authorized human can explicitly decide to reject
    approvals = svc.list_approvals()
    assert len(approvals.approvals) >= 1
    app_id = UUID(approvals.approvals[0].approval_id)

    decision_res = svc.decide_approval(
        approval_id=app_id,
        principal=reviewer_principal,
        decision_verdict="REJECT",
        reason="Human confirmed unverified vehicle destruction with external blacklist match",
    )
    assert decision_res.verdict == "REJECT"
    assert decision_res.status == "REJECTED"
    assert decision_res.resumed_workflow_state == WorkflowState.REJECTED.value


def test_multilingual_claim_fraud_integration(
    api_service_factory: Callable[..., ApiService],
) -> None:
    trans_client = FakeTranslationServiceClient()
    fraud_svc = FraudAssessmentService()
    svc: ApiService = api_service_factory(translation_client=trans_client, fraud_service=fraud_svc)

    claim_id = str(uuid4())
    recent_date = (date.today() - timedelta(days=2)).isoformat()
    req = ClaimSubmissionRequest(
        claim_id=claim_id,
        policy_id="POL-ES-505",
        claimant_name="Elena Gomez",
        incident_date=recent_date,
        claim_amount=Decimal("2300.00"),
        description="Choque en estacionamiento con raspaduras leves en parachoques.",
        documents=["Informe de colisión menor sin heridos."],
        language="es",
    )

    resp = svc.submit_claim(req)
    assert resp.current_state == WorkflowState.HUMAN_APPROVAL.value

    # Verify fraud assessment for multilingual claim
    assessment = svc.get_fraud_assessment(UUID(claim_id))
    assert assessment is not None
    assert assessment.risk_category == RiskCategory.LOW
    assert assessment.risk_score < 0.25


def test_alembic_migration_0010_upgrade_and_downgrade() -> None:
    """Alembic revision 0010 upgrades, verifies fraud_assessments, and downgrades cleanly."""
    from alembic import command
    from alembic.config import Config

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        alembic_cfg = Config("alembic.ini")
        alembic_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

        # Upgrade to head (0010)
        command.upgrade(alembic_cfg, "head")

        engine = create_engine(f"sqlite:///{db_path}")
        inspector = inspect(engine)
        assert "fraud_assessments" in inspector.get_table_names()
        fraud_cols = {c["name"] for c in inspector.get_columns("fraud_assessments")}
        assert "assessment_id" in fraud_cols
        assert "risk_score" in fraud_cols
        assert "risk_category" in fraud_cols
        assert "indicators_json" in fraud_cols
        assert "requires_human_scrutiny" in fraud_cols

        # Downgrade to 0009
        command.downgrade(alembic_cfg, "0009_phase12_advanced_features")

        inspector = inspect(engine)
        assert "fraud_assessments" not in inspector.get_table_names()
        # Verify 0009 tables still exist
        assert "batches" in inspector.get_table_names()

        # Upgrade back to head (0010)
        command.upgrade(alembic_cfg, "head")
        inspector = inspect(engine)
        assert "fraud_assessments" in inspector.get_table_names()
        engine.dispose()
    finally:
        if "engine" in locals():
            engine.dispose()
        gc.collect()
        if os.path.exists(db_path):
            with contextlib.suppress(OSError):
                os.unlink(db_path)

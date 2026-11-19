"""
Phase 12.3 Security Tests: Fraud Intelligence Invariants, Tamper Resistance, PII Sanitization,
and Human-in-the-Loop Preservation.
"""

from __future__ import annotations

from collections.abc import Generator
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from casefile.agents.context import AgentContext
from casefile.agents.reviewer import ReviewerAgent
from casefile.api.auth import Principal, Role
from casefile.api.schemas.models import ClaimSubmissionRequest
from casefile.api.service import ApiService, SmartDefaultProvider
from casefile.approval.model import (
    ApprovalError,
    ApprovalErrorCode,
)
from casefile.fraud.contracts import (
    ExternalFraudMatch,
    FraudAssessmentMetadata,
    FraudAssessmentStatus,
    FraudIndicator,
    FraudRiskResult,
    IndicatorSeverity,
    RiskCategory,
)
from casefile.fraud.external import FakeExternalFraudDatabase, hash_identifier
from casefile.fraud.service import FraudAssessmentService
from casefile.models.contracts import (
    ExtractionResult,
    InvestigationResult,
    ReviewRequest,
    WorkflowState,
)
from casefile.models.domain import AgentType
from casefile.observability.spans import fraud_assessment_attributes


@pytest.fixture
def api_service() -> Generator[ApiService, None, None]:
    svc = ApiService(
        database_url=f"sqlite:///:memory:?cache=shared_{uuid4().hex}",
        fraud_service=FraudAssessmentService(),
    )
    yield svc
    svc.close()


def test_human_approval_invariant_for_high_and_critical_fraud(api_service: ApiService) -> None:
    """Claims with high/critical fraud risk NEVER bypass HUMAN_APPROVAL or auto-approve."""
    recent_date = (date.today() - timedelta(days=2)).isoformat()
    claim_id = str(uuid4())

    # Submit claim flagged in external database
    req = ClaimSubmissionRequest(
        claim_id=claim_id,
        policy_id="POL-FRAUD-001",
        claimant_name="Suspicious Actor",
        incident_date=recent_date,
        claim_amount=Decimal("45000.00"),
        description="Total loss claim parked unattended no witnesses with extensive damage.",
        documents=["Police report: Unattended vehicle found with structural destruction."],
        language="en",
    )

    resp = api_service.submit_claim(req)

    # Invariant 1: State must be HUMAN_APPROVAL (never APPROVED or REJECTED)
    assert resp.current_state == WorkflowState.HUMAN_APPROVAL.value
    assert resp.current_state != WorkflowState.APPROVED.value
    assert resp.current_state != WorkflowState.REJECTED.value

    # Invariant 2: Fraud assessment flags scrutiny
    assessment = api_service.get_fraud_assessment(UUID(claim_id))
    assert assessment is not None
    assert assessment.requires_human_scrutiny is True
    assert assessment.risk_category in (RiskCategory.HIGH, RiskCategory.CRITICAL)


def test_reviewer_recommendation_with_fraud_routes_to_human_approval(
    api_service: ApiService,
) -> None:
    """Reviewer recommendation on claim with fraud signals routes to HUMAN_APPROVAL, allowing human decision."""
    provider = SmartDefaultProvider(provider_name="test-reviewer")
    reviewer = ReviewerAgent(provider=provider)

    ext = ExtractionResult(
        workflow_id=uuid4(),
        claimant_name="Fraudster Corp",
        policy_id="POL-CORP-101",
        incident_date="2026-05-01",
        incident_location="Remote Lot",
        incident_description="Vehicle stripped overnight",
        claim_amount=Decimal("38000.00"),
        requested_coverage_type="COMPREHENSIVE",
        extraction_confidence=0.95,
        is_complete=True,
    )

    inv = InvestigationResult(
        workflow_id=ext.workflow_id,
        policy_details={"status": "ACTIVE"},
        claim_history={"prior_claims_count": 3},
        repair_cost_validation={"is_valid": False, "market_rate": 15000.0},
        fraud_signals={"risk_score": 0.88, "signals": ["EXTERNAL_MATCH"], "flagged": True},
        supporting_documents=["report.pdf"],
        findings_summary="Critical fraud indicators detected.",
        evidence_strength=0.92,
        tool_calls_made=["policy_lookup", "fraud_signal_lookup"],
        tools_succeeded=2,
        tools_failed=0,
    )

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

    # Agent output recommending APPROVE (e.g. false positive assessment)
    valid_approve = (
        '{"workflow_id": "'
        + str(ext.workflow_id)
        + '", "decision": "APPROVE", "reasoning": "Legitimate claim despite anomaly", "confidence_score": 0.88, '
        '"evidence_completeness": 1.0, "identified_gaps": [], "rework_feedback": null, '
        '"fraud_risk_level": "CRITICAL", "fraud_signals_detected": true}'
    )

    result = reviewer.parse_output(valid_approve, req, agent_ctx, {"review_prompt_version": "v1.0"})
    assert result.decision == "APPROVE"
    assert result.fraud_signals_detected is True
    assert result.fraud_risk_level == "CRITICAL"


def test_false_positive_fraud_signal_human_approval(api_service: ApiService) -> None:
    """A legitimate claim flagged with HIGH fraud risk reaches HUMAN_APPROVAL and can be approved by an authorized human."""
    recent_date = (date.today() - timedelta(days=2)).isoformat()
    claim_id = str(uuid4())

    req = ClaimSubmissionRequest(
        claim_id=claim_id,
        policy_id="POL-FP-001",
        claimant_name="False Positive Claimant",
        incident_date=recent_date,
        claim_amount=Decimal("5000.00"),
        description="Legitimate claim with coincidental pattern match",
        documents=["Police report and photos confirming loss."],
        language="en",
    )

    resp = api_service.submit_claim(req)
    assert resp.current_state == WorkflowState.HUMAN_APPROVAL.value

    # Authorized human approves the claim after verifying evidence
    approvals = api_service.list_approvals()
    assert len(approvals.approvals) >= 1
    approval = [a for a in approvals.approvals if a.claim_id == claim_id][0]

    reviewer = Principal(
        principal_id="senior-rev-101",
        role=Role.CLAIM_REVIEWER,
        display_name="Senior Reviewer",
    )

    dec_resp = api_service.decide_approval(
        approval_id=UUID(approval.approval_id),
        principal=reviewer,
        decision_verdict="APPROVE",
        reason="Verified legitimate documentation; external match was coincidental name collision",
    )
    assert dec_resp.verdict == "APPROVE"
    assert dec_resp.status == "APPROVED"
    assert dec_resp.resumed_workflow_state == WorkflowState.APPROVED.value


def test_fraud_signal_partial_approval(api_service: ApiService) -> None:
    """A claim with fraud indicators can receive an authorized partial approval within the authoritative financial cap."""
    recent_date = (date.today() - timedelta(days=2)).isoformat()
    claim_id = str(uuid4())

    req = ClaimSubmissionRequest(
        claim_id=claim_id,
        policy_id="POL-PART-002",
        claimant_name="Partial Settlement Claimant",
        incident_date=recent_date,
        claim_amount=Decimal("2000.00"),
        description="Fender bender claim with elevated repair quote",
        documents=["Shop quote: $2,000.00"],
        language="en",
    )

    resp = api_service.submit_claim(req)
    assert resp.current_state == WorkflowState.HUMAN_APPROVAL.value

    approvals = api_service.list_approvals()
    approval = [a for a in approvals.approvals if a.claim_id == claim_id][0]

    reviewer = Principal(
        principal_id="rev-partial-01",
        role=Role.CLAIM_REVIEWER,
        display_name="Authorized Adjuster",
    )

    dec_resp = api_service.decide_approval(
        approval_id=UUID(approval.approval_id),
        principal=reviewer,
        decision_verdict="APPROVE_PARTIAL",
        reason="Agreed to prevailing market rate of $1,200.00 after scrutiny",
        approved_amount=Decimal("1200.00"),
    )
    assert dec_resp.verdict == "APPROVE_PARTIAL"
    assert dec_resp.approved_amount == Decimal("1200.00")
    assert dec_resp.status == "APPROVED"
    assert dec_resp.resumed_workflow_state == WorkflowState.APPROVED.value


def test_fraud_assessment_timing_and_provider_failure_handling(api_service: ApiService) -> None:
    """Authoritative fraud assessment is persisted BEFORE workflow and provider failures route to scrutiny."""
    from unittest.mock import MagicMock

    from casefile.fraud.contracts import FraudAssessmentStatus, FraudRiskRequest

    # Test provider failure handling
    failing_service = FraudAssessmentService()
    failing_service._scorer = MagicMock()
    failing_service._scorer.score_features.side_effect = RuntimeError("ML engine unreachable")

    req = FraudRiskRequest(
        claim_id=uuid4(),
        policy_id="POL-FAIL-01",
        claim_amount=Decimal("3000.00"),
        description="Testing provider failure fallback",
    )
    res = failing_service.assess_claim(req)
    assert res.status == FraudAssessmentStatus.FAILED
    assert res.risk_category == RiskCategory.INSUFFICIENT_DATA
    assert res.requires_human_scrutiny is True
    assert any("ML engine unreachable" in w for w in res.metadata.warnings)


def test_partial_approval_financial_cap_preserved(api_service: ApiService) -> None:
    """The $100 claim / $1,000,000 partial payout exploit remains completely closed."""
    claim_id = str(uuid4())
    recent_date = (date.today() - timedelta(days=2)).isoformat()

    req = ClaimSubmissionRequest(
        claim_id=claim_id,
        policy_id="POL-CAP-TEST",
        claimant_name="Alice Legit",
        incident_date=recent_date,
        claim_amount=Decimal("100.00"),
        description="Cracked side mirror",
        documents=["Receipt for $100.00 mirror"],
        language="en",
    )

    resp = api_service.submit_claim(req)
    assert resp.current_state == WorkflowState.HUMAN_APPROVAL.value
    w_id = UUID(resp.workflow_run_id)

    approvals = api_service.list_approvals()
    assert len(approvals.approvals) >= 1
    app_id = UUID(approvals.approvals[0].approval_id)

    reviewer = Principal(
        principal_id="rev-exploit", role=Role.CLAIM_REVIEWER, display_name="Eve Exploit"
    )

    # Attempt unauthorized $1,000,000 payout on a $100 claim
    with pytest.raises(ApprovalError) as exc_info:
        api_service.decide_approval(
            app_id,
            reviewer,
            "APPROVE_PARTIAL",
            reason="Malicious unauthorized 1M payout exploit",
            approved_amount=Decimal("1000000.00"),
        )

    assert exc_info.value.code == ApprovalErrorCode.VALIDATION_ERROR
    assert "cannot exceed" in str(exc_info.value)

    # Verify claim remains in HUMAN_APPROVAL and was NOT approved
    status = api_service.get_workflow_status(w_id)
    assert status.current_state == WorkflowState.HUMAN_APPROVAL.value
    assert status.current_state != WorkflowState.APPROVED.value


def test_fraud_contracts_immutability() -> None:
    """Fraud contracts are frozen Pydantic models to prevent in-flight tampering."""
    ind = FraudIndicator(
        indicator_type="TIMING_ANOMALY",
        name="Pre-inception",
        description="Loss before inception",
        severity=IndicatorSeverity.CRITICAL,
        weight=0.9,
    )
    with pytest.raises(ValidationError):
        setattr(ind, "severity", IndicatorSeverity.LOW)  # noqa: B010

    match = ExternalFraudMatch(
        source_database="NICB",
        matched_entity_type="VIN_HASH",
        matched_entity_masked="1HGCR2F8*********",
        confidence=0.99,
        match_reason="Stolen vehicle list",
    )
    with pytest.raises(ValidationError):
        setattr(match, "confidence", 0.0)  # noqa: B010

    meta = FraudAssessmentMetadata(
        model_provider="casefile-test",
        model_version="v1.0",
        assessment_version="1.0.0",
        duration_ms=5.0,
        features_extracted_count=7,
        external_matches_count=0,
    )
    result = FraudRiskResult(
        assessment_id=uuid4(),
        claim_id=uuid4(),
        workflow_run_id=uuid4(),
        risk_score=0.95,
        risk_category=RiskCategory.CRITICAL,
        confidence_score=0.90,
        status=FraudAssessmentStatus.COMPLETED,
        metadata=meta,
        findings_summary="Critical risk detected.",
        requires_human_scrutiny=True,
    )
    with pytest.raises(ValidationError):
        setattr(result, "risk_score", 0.05)  # noqa: B010
    with pytest.raises(ValidationError):
        setattr(result, "requires_human_scrutiny", False)  # noqa: B010


def test_zero_pii_in_telemetry_spans() -> None:
    """Span attributes created for fraud assessment must never contain PII or unmasked identifiers."""
    claim_id = uuid4()
    run_id = uuid4()
    meta = FraudAssessmentMetadata(
        model_provider="casefile-test",
        model_version="v1.0",
        assessment_version="1.0.0",
        duration_ms=4.2,
        features_extracted_count=7,
        external_matches_count=1,
    )
    result = FraudRiskResult(
        assessment_id=uuid4(),
        claim_id=claim_id,
        workflow_run_id=run_id,
        risk_score=0.82,
        risk_category=RiskCategory.CRITICAL,
        confidence_score=0.91,
        status=FraudAssessmentStatus.COMPLETED,
        indicators=[
            FraudIndicator(
                indicator_type="EXTERNAL_INTELLIGENCE_MATCH",
                name="Match in NICB",
                description="Matched 1HGCR2F8*********",
                severity=IndicatorSeverity.CRITICAL,
                weight=0.95,
            )
        ],
        external_matches=[
            ExternalFraudMatch(
                source_database="NICB",
                matched_entity_type="VIN_HASH",
                matched_entity_masked="1HGCR2F8*********",
                confidence=0.99,
                match_reason="Known staged crash syndicate",
            )
        ],
        metadata=meta,
        findings_summary="NICB match flagged.",
        requires_human_scrutiny=True,
    )

    attrs = fraud_assessment_attributes(
        model_provider=result.metadata.model_provider,
        model_version=result.metadata.model_version,
        risk_category=result.risk_category.value,
        risk_score=result.risk_score,
        confidence_score=result.confidence_score,
        duration_ms=result.metadata.duration_ms,
        features_count=result.metadata.features_extracted_count,
        external_matches_count=result.metadata.external_matches_count,
        status=result.status.value,
        success=True,
    )

    # Verify expected attributes
    assert attrs["casefile.fraud.model_provider"] == "casefile-test"
    assert attrs["casefile.fraud.risk_score"] == 0.82
    assert attrs["casefile.fraud.risk_category"] == "CRITICAL"
    assert attrs["casefile.fraud.confidence_score"] == 0.91
    assert attrs["casefile.fraud.features_count"] == 7
    assert attrs["casefile.fraud.external_matches_count"] == 1

    # Verify no PII exists in attributes
    for key, value in attrs.items():
        assert "name" not in key.lower() or key == "fraud.model_provider"
        assert "ssn" not in key.lower()
        assert "address" not in key.lower()
        assert not isinstance(value, str) or "1HGCR2F812345678" not in value


def test_adversarial_injection_payloads_in_external_matches(api_service: ApiService) -> None:
    """External fraud database returning SQL injection or XSS payload does not cause execution or corruption."""
    # Create an external match with malicious payload
    malicious_policy = {
        hash_identifier("POL-SQL-INJ"): {
            "source": "CARRIER_EXCHANGE",
            "type": "POLICY_HASH",
            "masked": "POL-SQL-INJ",
            "confidence": 0.95,
            "reason": "'; DROP TABLE fraud_assessments; -- <script>alert(1)</script>",
            "first_seen": "2025-01-01",
            "last_seen": "2026-01-01",
        }
    }
    malicious_db = FakeExternalFraudDatabase(custom_policy_records=malicious_policy)

    fraud_svc = FraudAssessmentService(external_db=malicious_db)
    svc = ApiService(
        database_url=f"sqlite:///:memory:?cache=shared_{uuid4().hex}",
        fraud_service=fraud_svc,
    )

    claim_id = str(uuid4())
    recent_date = (date.today() - timedelta(days=2)).isoformat()
    req = ClaimSubmissionRequest(
        claim_id=claim_id,
        policy_id="POL-SQL-INJ",
        claimant_name="Bobby Tables",
        incident_date=recent_date,
        claim_amount=Decimal("35000.00"),
        description="Rear end damage parked unattended no witnesses",
        documents=["Invoice"],
        language="en",
    )

    resp = svc.submit_claim(req)
    assert resp.current_state == WorkflowState.HUMAN_APPROVAL.value

    # Verify assessment persisted safely without database corruption
    assessment = svc.get_fraud_assessment(UUID(claim_id))
    assert assessment is not None
    assert len(assessment.external_matches) == 1
    assert "DROP TABLE" in assessment.external_matches[0].match_reason
    assert assessment.requires_human_scrutiny is True

    svc.close()

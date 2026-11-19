"""
Unit tests for Explainability & Counterfactual Intelligence Subsystem (Phase 12.5).

Covers:
- ExplanationEngine synthesis and factor weighting
- Evidentiary citation extraction
- Bounded sensitivity counterfactual generation
- Claimant view sanitization (internal weight and fraud indicator redaction)
- Persistence mapping and SqlExplanationRepository operations
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from casefile.models.contracts import (
    ClaimInput,
    CounterfactualScenario,
    DecisionExplanation,
    FactorContribution,
    ReviewResult,
)
from casefile.models.domain import (
    ClaimType,
    CoverageLimits,
    DamageEstimate,
    DamageLineItem,
    Money,
    Policy,
    PriorClaim,
)
from casefile.models.persistence import Base
from casefile.storage.repositories import SqlExplanationRepository
from casefile.workflow.context import WorkflowContext
from casefile.workflow.explainability import ExplanationEngine


@pytest.fixture
def test_engine() -> Iterator[Engine]:
    """Create in-memory SQLite database engine for testing."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture
def session_factory(test_engine: Engine) -> sessionmaker[Session]:
    """Create sessionmaker for the test engine."""
    return sessionmaker(bind=test_engine)


def test_factor_contribution_and_citations() -> None:
    """Verify ExplanationEngine computes risk factors and extracts citations correctly."""
    wf_ctx = WorkflowContext(
        workflow_run_id=uuid4(),
        claim_id=uuid4(),
    )
    wf_ctx.claim = ClaimInput(
        claim_id=wf_ctx.claim_id,
        policy_id="POL-5001",
        claimant_name="Jane Doe",
        incident_date="2026-03-01",
        claim_amount=Decimal("4500.00"),
        description="Fender bender collision repair",
        documents=["repair_invoice.pdf"],
    )
    wf_ctx.policy_information = Policy(
        policy_number="POL-5001",
        policyholder_name="Jane Doe",
        policy_type="COLLISION",
        effective_date=date(2026, 1, 1),
        expiration_date=date(2027, 1, 1),
        coverage=CoverageLimits(
            bodily_injury_per_person=Decimal("25000.00"),
            bodily_injury_per_accident=Decimal("50000.00"),
            property_damage=Decimal("25000.00"),
        ),
        is_active=True,
    )
    line_item = DamageLineItem(
        description="Bumper replacement",
        quantity=1,
        unit_cost=Money(amount=Decimal("4500.00")),
        total_cost=Money(amount=Decimal("4500.00")),
    )
    wf_ctx.damage_estimates = [
        DamageEstimate(
            estimate_id=uuid4(),
            source="CertifiedShop",
            estimate_date=date(2026, 3, 2),
            line_items=[line_item],
            labor_hours=4.0,
            total_cost=Money(amount=Decimal("4500.00")),
        )
    ]
    wf_ctx.prior_claims = []
    wf_ctx.review_result = ReviewResult(
        workflow_id=wf_ctx.workflow_run_id,
        decision="APPROVE",
        reasoning="All documentation verified and within limits.",
        confidence_score=0.95,
        evidence_completeness=0.90,
    )

    explanation = ExplanationEngine.generate_explanation(wf_ctx)

    assert explanation.recommendation == "APPROVE"
    assert explanation.confidence_score == 0.95
    assert len(explanation.positive_factors) >= 2
    assert any("POL-5001" in pf for pf in explanation.positive_factors)
    assert any(
        "repair_invoice.pdf" in c or "CertifiedShop" in c for c in explanation.evidence_citations
    )
    assert len(explanation.counterfactuals) >= 2
    assert explanation.is_claimant_safe is False


def test_claimant_view_sanitization() -> None:
    """Verify that to_claimant_view() strips internal mathematical weights and sensitive markers."""
    explanation = DecisionExplanation(
        workflow_run_id=uuid4(),
        claim_id=uuid4(),
        summary="Claim approved under comprehensive coverage.",
        recommendation="APPROVE",
        confidence_score=0.92,
        key_factors=["Valid policy coverage", "Verified damage invoice"],
        positive_factors=["Policy active", "Clean history"],
        negative_factors=["Delayed reporting by 3 days"],
        factor_contributions=[
            FactorContribution(
                factor_name="internal_ml_anomaly_weight",
                impact="REDUCES_RISK",
                description="Proprietary neural anomaly detector weight",
                weight=-0.42,
            ),
            FactorContribution(
                factor_name="fraud_database_risk",
                impact="NEUTRAL",
                description="Syndicate blacklist matching score",
                weight=0.01,
            ),
        ],
        evidence_citations=[
            "Document: police_report.pdf",
            "Internal blacklist matching: SIU_CLEAR",
        ],
        counterfactuals=[
            CounterfactualScenario(
                factor_name="claim_amount_vs_policy_limit",
                current_value="$3,000",
                counterfactual_value="$30,000",
                hypothetical_outcome="Exceeds limit",
                explanation="Exceeds coverage maximum",
            ),
            CounterfactualScenario(
                factor_name="fraud_intelligence_risk_level",
                current_value="LOW",
                counterfactual_value="HIGH",
                hypothetical_outcome="Fraud hold",
                explanation="Internal anomaly score elevated",
            ),
        ],
        assessment_id=uuid4(),
        is_claimant_safe=False,
    )

    claimant_view = explanation.to_claimant_view()

    assert claimant_view.is_claimant_safe is True
    # Internal weights must be completely purged
    assert len(claimant_view.factor_contributions) == 0
    # Assessment ID must be redacted
    assert claimant_view.assessment_id is None
    # Citations with sensitive blacklist/internal tags purged
    assert not any("blacklist" in c.lower() for c in claimant_view.evidence_citations)
    assert any("police_report.pdf" in c for c in claimant_view.evidence_citations)
    # Counterfactuals with fraud keywords filtered
    assert not any("fraud" in sc.factor_name.lower() for sc in claimant_view.counterfactuals)
    assert len(claimant_view.counterfactuals) == 1


def test_explanation_persistence_roundtrip(session_factory: sessionmaker[Session]) -> None:
    """Verify SqlExplanationRepository persists and reconstructs DecisionExplanation and counterfactuals."""
    wf_id = uuid4()
    claim_id = uuid4()

    explanation = DecisionExplanation(
        workflow_run_id=wf_id,
        claim_id=claim_id,
        summary="Adjudication brief for test claim.",
        recommendation="APPROVE_PARTIAL",
        confidence_score=0.88,
        key_factors=["Coverage valid", "Partial deductible applied"],
        positive_factors=["Coverage confirmed"],
        negative_factors=["Excess estimate item"],
        factor_contributions=[
            FactorContribution(
                factor_name="repair_cost_consistency",
                impact="INCREASES_RISK",
                description="Estimate exceeds OEM benchmark by 15%",
                weight=0.20,
            )
        ],
        evidence_citations=["Document: estimate_v1.pdf", "Policy: POL-900"],
        counterfactuals=[
            CounterfactualScenario(
                factor_name="claim_amount_vs_limit",
                current_value="$12,000",
                counterfactual_value="$8,000",
                hypothetical_outcome="Full APPROVE",
                explanation="Lower amount meets auto-approval threshold",
            )
        ],
        missing_or_uncertain_information=["Need final paint shop signoff"],
        limitations=["Advisory sensitivity analysis only."],
        is_claimant_safe=False,
    )

    with session_factory() as session:
        repo = SqlExplanationRepository(session)
        repo.save_explanation(explanation)
        for cf in explanation.counterfactuals:
            repo.save_counterfactual(cf, claim_id=claim_id, workflow_run_id=wf_id)
        session.commit()

    with session_factory() as session:
        repo = SqlExplanationRepository(session)
        loaded = repo.get_for_workflow(wf_id)
        assert loaded is not None
        assert loaded.explanation_id == explanation.explanation_id
        assert loaded.claim_id == claim_id
        assert loaded.recommendation == "APPROVE_PARTIAL"
        assert abs(loaded.confidence_score - 0.88) < 1e-4
        assert loaded.summary == explanation.summary
        assert loaded.key_factors == explanation.key_factors
        assert len(loaded.factor_contributions) == 1
        assert loaded.factor_contributions[0].factor_name == "repair_cost_consistency"
        assert loaded.factor_contributions[0].weight == 0.20
        assert len(loaded.counterfactuals) == 1
        assert loaded.counterfactuals[0].factor_name == "claim_amount_vs_limit"

        by_claim = repo.get_for_claim(claim_id)
        assert by_claim is not None
        assert by_claim.explanation_id == explanation.explanation_id


def test_fraud_risk_factors_and_rejection_summary() -> None:
    """Verify ExplanationEngine surfaces high fraud risk and generates accurate rejection summary."""
    wf_ctx = WorkflowContext(
        workflow_run_id=uuid4(),
        claim_id=uuid4(),
    )
    wf_ctx.claim = ClaimInput(
        claim_id=wf_ctx.claim_id,
        policy_id="POL-9999",
        claimant_name="Suspicious Party",
        incident_date="2026-02-15",
        claim_amount=Decimal("15000.00"),
        description="Total loss claim with conflicting narratives",
        documents=["unverified_damage.pdf"],
    )

    class MockFraudAssessment:
        assessment_id = uuid4()
        risk_score = Decimal("0.85")
        risk_category = "HIGH"
        requires_human_scrutiny = True
        findings_summary = "Multiple syndicate fraud matches identified"

    wf_ctx.fraud_assessment = MockFraudAssessment()
    wf_ctx.review_result = ReviewResult(
        workflow_id=wf_ctx.workflow_run_id,
        decision="REJECT",
        reasoning="Inconsistent damages and severe fraud indicators detected.",
        confidence_score=0.91,
        identified_gaps=["Police report missing", "Conflicting collision dates"],
    )

    explanation = ExplanationEngine.generate_explanation(wf_ctx)

    assert explanation.recommendation == "REJECT"
    assert explanation.assessment_id == MockFraudAssessment.assessment_id
    assert any("fraud" in nf.lower() for nf in explanation.negative_factors)
    assert any(
        "Police report missing" in gap for gap in explanation.missing_or_uncertain_information
    )
    assert "REJECTION" in explanation.summary
    assert any(
        "fraud_intelligence_risk_level" in sc.factor_name for sc in explanation.counterfactuals
    )


def test_multiple_prior_claims_and_rework_recommendation() -> None:
    """Verify that multiple prior claims elevate risk and reflect in rework recommendations."""
    wf_ctx = WorkflowContext(
        workflow_run_id=uuid4(),
        claim_id=uuid4(),
    )
    wf_ctx.claim = ClaimInput(
        claim_id=wf_ctx.claim_id,
        policy_id="POL-8000",
        claimant_name="Repeat Claimant",
        incident_date="2026-03-01",
        claim_amount=Decimal("8200.00"),
        description="Third rear-end collision this year",
        documents=[],
    )
    wf_ctx.prior_claims = [
        PriorClaim(
            claim_reference=f"PR-{i}",
            claim_date=date(2025, i + 1, 1),
            claim_type=ClaimType.COLLISION,
            amount=Money(amount=Decimal("3500.00")),
            status="SETTLED",
        )
        for i in range(4)
    ]
    wf_ctx.review_result = ReviewResult(
        workflow_id=wf_ctx.workflow_run_id,
        decision="REWORK",
        reasoning="Prior loss recurrence requires deeper investigation.",
        confidence_score=0.75,
        identified_gaps=["Prior repair documentation missing"],
    )

    explanation = ExplanationEngine.generate_explanation(wf_ctx)

    assert explanation.recommendation == "REWORK"
    assert any("Elevated prior claims frequency" in nf for nf in explanation.negative_factors)
    assert "REWORK" in explanation.summary
    assert len(explanation.limitations) == 3


def test_minimal_empty_context_safety() -> None:
    """Verify ExplanationEngine handles empty/minimal workflow context without exceptions."""
    wf_ctx = WorkflowContext(
        workflow_run_id=uuid4(),
        claim_id=uuid4(),
    )
    explanation = ExplanationEngine.generate_explanation(wf_ctx)

    assert explanation.recommendation == "APPROVE"
    assert explanation.confidence_score == 1.0
    assert len(explanation.key_factors) > 0
    assert len(explanation.counterfactuals) >= 2
    assert explanation.is_claimant_safe is False

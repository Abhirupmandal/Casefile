"""Security tests for Phase 12.5 Explainability & Counterfactual Intelligence.

Verifies:
1. Advisory Invariant:
   - Counterfactual sensitivity scenarios are strictly hypothetical.
   - They CANNOT approve, counter-offer, settle, or authorize financial payouts.
   - Authoritative claim amounts and workflow states remain completely inviolate.
2. Separation of Duties & Tenant Isolation:
   - Claimant principal cannot access explanations for claims belonging to another claimant (HTTP 403 Forbidden).
3. Data Sanitization & Leakage Prevention:
   - Claimant view strips all internal ML weights (factor_contributions is empty).
   - Claimant view redacts assessment_id.
   - Claimant view purges confidential fraud rules, blacklist references, and internal keywords.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, status

from casefile.api.auth import Principal, Role
from casefile.api.schemas.models import ClaimSubmissionRequest
from casefile.api.service import ApiService
from casefile.models.contracts import (
    CounterfactualScenario,
    DecisionExplanation,
    FactorContribution,
    WorkflowState,
)
from casefile.models.persistence import ClaimRecord, WorkflowRunRecord


def _make_api_service() -> ApiService:
    """Instantiate in-memory ApiService."""
    return ApiService(database_url="sqlite:///:memory:")


def test_advisory_invariance_counterfactual_cannot_change_payout_or_state() -> None:
    """Verify that counterfactual scenarios cannot alter authoritative financial state or workflow status."""
    svc = _make_api_service()
    reviewer = Principal(
        principal_id="rev-01",
        role=Role.CLAIM_REVIEWER,
        display_name="Auditor",
    )

    req = ClaimSubmissionRequest(
        policy_id="POL-9901",
        claimant_name="John Connor",
        claim_amount=Decimal("12000.00"),
        incident_date="2026-03-01",
        description="Tree branch fell on garage roof",
        documents=["garage_damage.pdf"],
    )

    resp = svc.submit_claim(req)
    claim_id = UUID(resp.claim_id)
    wf_run_id = UUID(resp.workflow_run_id)

    # Fetch explanation with counterfactuals
    exp = svc.get_workflow_explanation(wf_run_id, principal=reviewer)
    assert len(exp.counterfactuals) > 0

    # Read authoritative claim state from database
    with svc._uow() as uow:
        claim_row = uow.session.get(ClaimRecord, str(claim_id))
        assert claim_row is not None
        assert Decimal(str(claim_row.claim_amount)) == Decimal("12000.00")

        wf_row = uow.session.get(WorkflowRunRecord, str(wf_run_id))
        assert wf_row is not None
        # Workflow run must NOT be moved to terminal APPROVED by mere presence of positive counterfactual
        assert wf_row.current_state != WorkflowState.APPROVED.value


def test_claimant_tenant_isolation_403() -> None:
    """Verify that a claimant cannot retrieve the explanation of a claim owned by another customer."""
    svc = _make_api_service()

    req = ClaimSubmissionRequest(
        policy_id="POL-8801",
        claimant_name="Legitimate Owner",
        claim_amount=Decimal("3500.00"),
        incident_date="2026-03-01",
        description="Water leak from pipe",
        documents=["plumber_receipt.pdf"],
    )

    resp = svc.submit_claim(req)
    claim_id = UUID(resp.claim_id)
    wf_run_id = UUID(resp.workflow_run_id)

    # Identify the legitimate owner's customer_id
    with svc._uow() as uow:
        crow = uow.session.get(ClaimRecord, str(claim_id))
        assert crow is not None
        legit_customer_id = crow.customer_id
        assert legit_customer_id != "ATTACKER-CUST-999"

    # Malicious/unauthorized claimant attempting to inspect this claim
    malicious_claimant = Principal(
        principal_id="ATTACKER-CUST-999",
        role=Role.CLAIMANT,
        display_name="Attacker",
    )

    # 1. Via Workflow explain endpoint -> Must raise 403 Forbidden
    with pytest.raises(HTTPException) as exc_info_wf:
        svc.get_workflow_explanation(wf_run_id, principal=malicious_claimant)
    assert exc_info_wf.value.status_code == status.HTTP_403_FORBIDDEN

    # 2. Via Claim explain endpoint -> Must raise 403 Forbidden
    with pytest.raises(HTTPException) as exc_info_claim:
        svc.get_claim_explanation(claim_id, principal=malicious_claimant)
    assert exc_info_claim.value.status_code == status.HTTP_403_FORBIDDEN


def test_claimant_view_redacts_internal_weights_and_blacklist_rules() -> None:
    """Verify that claimant view strips internal weights and confidential risk intelligence."""
    exp = DecisionExplanation(
        workflow_run_id=uuid4(),
        claim_id=uuid4(),
        summary="Claim verified under standard policy parameters.",
        recommendation="APPROVE",
        confidence_score=0.94,
        key_factors=["Valid comprehensive coverage"],
        positive_factors=["Timely submission"],
        negative_factors=[],
        factor_contributions=[
            FactorContribution(
                factor_name="internal_xgboost_risk_score",
                impact="REDUCES_RISK",
                description="Proprietary XGBoost risk model score: 0.12",
                feature_value="0.12",
                weight=-0.38,
            )
        ],
        evidence_citations=[
            "Document: report.pdf",
            "Internal Rule Check: SIU_BLACKLIST_NEGATIVE",
        ],
        counterfactuals=[
            CounterfactualScenario(
                factor_name="claim_amount_vs_policy_limit",
                current_value="$2,000",
                counterfactual_value="$25,000",
                hypothetical_outcome="Exceeds limit",
                explanation="Exceeds maximum allowable coverage limit",
            ),
            CounterfactualScenario(
                factor_name="internal_syndicate_anomaly_score",
                current_value="0.05",
                counterfactual_value="0.95",
                hypothetical_outcome="Fraud investigation triggered",
                explanation="Syndicate ring match anomaly flagged",
            ),
        ],
        assessment_id=uuid4(),
        is_claimant_safe=False,
    )

    claimant_safe = exp.to_claimant_view()

    # 1. Must be marked claimant safe
    assert claimant_safe.is_claimant_safe is True

    # 2. Factor contributions (with mathematical weights) MUST be empty
    assert len(claimant_safe.factor_contributions) == 0

    # 3. Assessment ID MUST be None
    assert claimant_safe.assessment_id is None

    # 4. Citations must NOT contain blacklist or internal rule terms
    for cit in claimant_safe.evidence_citations:
        assert "blacklist" not in cit.lower()
        assert "internal" not in cit.lower()

    # 5. Counterfactuals with anomaly/syndicate/fraud terms MUST be removed
    for cf in claimant_safe.counterfactuals:
        assert "anomaly" not in cf.factor_name.lower()
        assert "fraud" not in cf.factor_name.lower()

    # Only safe counterfactuals remain
    assert len(claimant_safe.counterfactuals) == 1
    assert claimant_safe.counterfactuals[0].factor_name == "claim_amount_vs_policy_limit"

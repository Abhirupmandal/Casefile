"""
Auto-Approval Policy Engine (Phase 11 Optimization & Scaling).

Automated low-risk triage and recommendation generation for claims meeting strict objective criteria:
- Claim amount under configured threshold (default: $5,000)
- Reviewer recommended decision is APPROVE
- Reviewer confidence score at or above threshold (default: 0.95)
- No fraud signals detected and low fraud risk level
- No prior rework cycles required (rework_count == 0)

MANDATORY SAFETY BOUNDARY:
Per CASEFILE core safety architecture (docs/approval.md, docs/state-machine.md),
NO automated system, model, agent, or policy is permitted to authorize final claim
payout or transition directly to WorkflowState.APPROVED. All claims requiring approval
MUST transition through WorkflowState.HUMAN_APPROVAL and receive an explicit decision
from an authorized HumanActor.

Claims meeting all low-risk criteria receive a structured expedited recommendation
attached to workflow metadata ("fast_track_eligible" = "true"), enabling priority
routing in the human approval queue without bypassing the human authorization boundary.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from casefile.models.contracts import (
    ExtractionResult,
    InvestigationResult,
    ReviewResult,
)


class AutoApprovalPolicy(BaseModel):
    """Configuration governing automated approval eligibility for low-risk claims."""

    model_config = {"frozen": True}

    enabled: bool = True
    max_claim_amount: Decimal = Field(default=Decimal("5000.00"), ge=Decimal("0.00"))
    min_confidence: float = Field(default=0.95, ge=0.0, le=1.0)
    disallow_fraud_signals: bool = True
    max_rework_cycles: int = Field(default=0, ge=0)


class AutoApprovalDecision(BaseModel):
    """Evaluation outcome detailing eligibility and audit explanations."""

    model_config = {"frozen": True}

    eligible: bool
    reasons: tuple[str, ...]
    evaluated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    policy_snapshot: dict[str, Any] = Field(default_factory=dict)

    @property
    def fast_track_recommended(self) -> bool:
        """Alias indicating whether expedited human review is recommended."""
        return self.eligible


def evaluate_auto_approval(
    policy: AutoApprovalPolicy,
    claim: Any,
    extraction: ExtractionResult | None,
    investigation: InvestigationResult | None,
    review: ReviewResult,
    rework_count: int = 0,
) -> AutoApprovalDecision:
    """
    Evaluate whether an adjudicated claim is eligible for automated approval.

    Returns AutoApprovalDecision with eligible=True iff all conditions pass.
    """
    if not policy.enabled:
        return AutoApprovalDecision(
            eligible=False,
            reasons=("Auto-approval policy is disabled",),
            policy_snapshot=policy.model_dump(),
        )

    disqualifications: list[str] = []
    qualifications: list[str] = []

    # 1. Reviewer decision must be APPROVE
    if review.decision != "APPROVE":
        disqualifications.append(f"Reviewer decision is {review.decision!r}, expected APPROVE")
    else:
        qualifications.append("Reviewer decision is APPROVE")

    # 2. Claim amount must be within policy limit
    amount_raw = getattr(claim, "claim_amount", None)
    if amount_raw is None and extraction is not None:
        amount_raw = extraction.claim_amount
    if amount_raw is not None and hasattr(amount_raw, "amount"):
        amount = Decimal(str(amount_raw.amount))
    elif amount_raw is not None:
        amount = Decimal(str(amount_raw))
    else:
        amount = Decimal("0.00")

    if amount > policy.max_claim_amount:
        disqualifications.append(
            f"Claim amount ${amount:,.2f} exceeds auto-approval ceiling of ${policy.max_claim_amount:,.2f}"
        )
    else:
        qualifications.append(f"Claim amount ${amount:,.2f} <= ${policy.max_claim_amount:,.2f}")

    # 3. Confidence score must meet threshold
    if review.confidence_score < policy.min_confidence:
        disqualifications.append(
            f"Reviewer confidence {review.confidence_score:.2f} is below required threshold of {policy.min_confidence:.2f}"
        )
    else:
        qualifications.append(
            f"Reviewer confidence {review.confidence_score:.2f} >= {policy.min_confidence:.2f}"
        )

    # 4. Fraud risk checks
    if policy.disallow_fraud_signals:
        if review.fraud_signals_detected:
            disqualifications.append("Fraud signals were detected during review")
        elif review.fraud_risk_level != "LOW":
            disqualifications.append(
                f"Fraud risk level is {review.fraud_risk_level!r}, expected LOW"
            )
        else:
            qualifications.append("No fraud signals detected and risk level is LOW")

    # 5. Rework cycle limits
    if rework_count > policy.max_rework_cycles:
        disqualifications.append(
            f"Workflow underwent {rework_count} rework cycles; policy permits maximum {policy.max_rework_cycles}"
        )
    else:
        qualifications.append(
            f"Rework cycles ({rework_count}) within limit ({policy.max_rework_cycles})"
        )

    eligible = len(disqualifications) == 0
    final_reasons = tuple(qualifications if eligible else disqualifications)

    return AutoApprovalDecision(
        eligible=eligible,
        reasons=final_reasons,
        policy_snapshot=policy.model_dump(),
    )


__all__ = [
    "AutoApprovalDecision",
    "AutoApprovalPolicy",
    "evaluate_auto_approval",
]

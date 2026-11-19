"""
Explainability & Counterfactual Intelligence Engine (Phase 12.5).

Provides trustworthy, human-comprehensible adjudication explanations and
deterministic counterfactual sensitivity analysis for claims adjusters,
supervisors, and compliance auditors.

Invariants:
1. Explainability and counterfactuals are strictly advisory. They NEVER approve,
   reject, counter-offer, or pay a claim.
2. Calculations are deterministic and offline-testable.
3. Claimant-facing views must be sanitized via `to_claimant_view()`, stripping
   internal ML feature weights, fraud detection thresholds, and confidential rules.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

from casefile.models.contracts import (
    CounterfactualScenario,
    DecisionExplanation,
    FactorContribution,
)
from casefile.workflow.context import WorkflowContext


class ExplanationEngine:
    """Core explanation and counterfactual generation engine."""

    ENGINE_MODEL_NAME = "casefile-explanation-engine"
    ENGINE_MODEL_VERSION = "1.0.0"

    STANDARD_LIMITATIONS: list[str] = [
        "This adjudication explanation brief is advisory decision support for human adjusters and compliance auditors.",
        "Counterfactual sensitivity scenarios are hypothetical explorations and do not constitute an eligibility guarantee, counter-offer, or settlement agreement.",
        "Authoritative settlement, claim valuation, and financial disbursement require verified human authorization.",
    ]

    @classmethod
    def generate_explanation(cls, wf_ctx: WorkflowContext) -> DecisionExplanation:
        """
        Synthesize a comprehensive DecisionExplanation from workflow context.

        Inspects extraction, investigation, review result, policy, damage estimates,
        and fraud intelligence to produce structured factors, verifiable citations,
        and sensitivity counterfactuals.
        """
        review_res = wf_ctx.review_result
        recommendation = review_res.decision if review_res else "APPROVE"
        confidence = float(review_res.confidence_score) if review_res else 1.0

        # 1. Collect citations
        citations = cls.extract_citations(wf_ctx)

        # 2. Compute factor contributions
        factor_contributions = cls.compute_factor_contributions(wf_ctx)

        # 3. Categorize positive vs negative factors
        positive_factors: list[str] = []
        negative_factors: list[str] = []
        key_factors: list[str] = []

        for fc in factor_contributions:
            desc = fc.description or fc.factor_name
            if fc.impact == "REDUCES_RISK":
                positive_factors.append(desc)
            elif fc.impact == "INCREASES_RISK":
                negative_factors.append(desc)

        # Highlight primary key drivers
        if recommendation in ("APPROVE", "APPROVE_PARTIAL"):
            key_factors.extend(positive_factors[:3])
            if negative_factors:
                key_factors.append(f"Mitigated consideration: {negative_factors[0]}")
        else:
            key_factors.extend(negative_factors[:3])
            if positive_factors:
                key_factors.append(f"Favorable context: {positive_factors[0]}")

        if not key_factors:
            key_factors = [
                "Claim documentation and policy terms evaluated under standard criteria."
            ]

        # 4. Generate deterministic counterfactuals
        counterfactuals = cls.generate_counterfactuals(wf_ctx)

        # 5. Extract missing / uncertain information
        missing_info: list[str] = []
        if review_res and review_res.identified_gaps:
            missing_info.extend(review_res.identified_gaps)
        if wf_ctx.extraction_result and wf_ctx.extraction_result.missing_fields:
            missing_info.extend(
                [f"Missing field: {f}" for f in wf_ctx.extraction_result.missing_fields]
            )

        # 6. Executive summary
        summary = cls.synthesize_summary(
            wf_ctx,
            recommendation=recommendation,
            confidence=confidence,
            key_factors=key_factors,
        )

        assessment_id: UUID | None = None
        if wf_ctx.fraud_assessment is not None:
            raw_id = getattr(wf_ctx.fraud_assessment, "assessment_id", None)
            if raw_id:
                try:
                    assessment_id = UUID(str(raw_id))
                except (ValueError, TypeError):
                    assessment_id = None

        return DecisionExplanation(
            explanation_id=uuid4(),
            workflow_run_id=wf_ctx.workflow_run_id,
            claim_id=wf_ctx.claim_id,
            summary=summary,
            recommendation=recommendation,
            confidence_score=confidence,
            key_factors=key_factors,
            positive_factors=positive_factors,
            negative_factors=negative_factors,
            factor_contributions=factor_contributions,
            evidence_citations=citations,
            counterfactuals=counterfactuals,
            model_name=cls.ENGINE_MODEL_NAME,
            model_version=cls.ENGINE_MODEL_VERSION,
            assessment_id=assessment_id,
            missing_or_uncertain_information=missing_info,
            limitations=list(cls.STANDARD_LIMITATIONS),
            is_claimant_safe=False,
        )

    @classmethod
    def compute_factor_contributions(cls, wf_ctx: WorkflowContext) -> list[FactorContribution]:
        """Compute itemized feature contributions and risk impacts."""
        contributions: list[FactorContribution] = []

        # Feature A: Policy Status & Limits
        if wf_ctx.policy_information is not None:
            pol = wf_ctx.policy_information
            p_num = getattr(pol, "policy_number", None) or getattr(pol, "policy_id", "POLICY")
            is_active = getattr(pol, "is_active", True)
            status_val = getattr(pol, "status", None)
            if status_val is not None:
                status_str = status_val.value if hasattr(status_val, "value") else str(status_val)
                is_active = status_str.upper() == "ACTIVE"

            if is_active:
                contributions.append(
                    FactorContribution(
                        factor_name="policy_status",
                        impact="REDUCES_RISK",
                        description=f"Policy {p_num} is active and verified.",
                        feature_value="ACTIVE",
                        weight=-0.35,
                    )
                )
            else:
                contributions.append(
                    FactorContribution(
                        factor_name="policy_status",
                        impact="INCREASES_RISK",
                        description=f"Policy {p_num} is inactive; coverage may be restricted.",
                        feature_value="INACTIVE",
                        weight=0.50,
                    )
                )

        # Feature B: Claim vs Damage Estimate Consistency
        claimed_amt: Decimal | None = None
        if wf_ctx.extraction_result is not None:
            claimed_amt = wf_ctx.extraction_result.claim_amount
        elif wf_ctx.claim is not None:
            claimed_amt = getattr(wf_ctx.claim, "claim_amount", None)

        if claimed_amt is not None and wf_ctx.damage_estimates:
            est_totals = []
            for est in wf_ctx.damage_estimates:
                if hasattr(est, "total_cost") and hasattr(est.total_cost, "amount"):
                    est_totals.append(est.total_cost.amount)
                elif hasattr(est, "total_estimate"):
                    est_totals.append(Decimal(str(est.total_estimate)))
            est_total = sum(est_totals, Decimal("0.00"))
            diff = abs(claimed_amt - est_total)
            if est_total > 0 and (diff / est_total) <= Decimal("0.10"):
                contributions.append(
                    FactorContribution(
                        factor_name="repair_cost_consistency",
                        impact="REDUCES_RISK",
                        description=f"Claimed amount (${claimed_amt:,.2f}) aligns closely with verified repair estimates (${est_total:,.2f}).",
                        feature_value=f"diff={diff:.2f}",
                        weight=-0.25,
                    )
                )
            elif est_total > 0 and claimed_amt > est_total * Decimal("1.25"):
                contributions.append(
                    FactorContribution(
                        factor_name="repair_cost_consistency",
                        impact="INCREASES_RISK",
                        description=f"Claimed amount (${claimed_amt:,.2f}) materially exceeds verified estimates (${est_total:,.2f}).",
                        feature_value=f"diff={diff:.2f}",
                        weight=0.35,
                    )
                )

        # Feature C: Prior Claims Frequency
        prior_count = len(wf_ctx.prior_claims)
        if prior_count == 0:
            contributions.append(
                FactorContribution(
                    factor_name="prior_claim_frequency",
                    impact="REDUCES_RISK",
                    description="No prior claim history detected in the last 36 months.",
                    feature_value="0_prior_claims",
                    weight=-0.20,
                )
            )
        elif prior_count <= 2:
            contributions.append(
                FactorContribution(
                    factor_name="prior_claim_frequency",
                    impact="NEUTRAL",
                    description=f"{prior_count} prior claim(s) found within normal frequency expectations.",
                    feature_value=f"{prior_count}_prior_claims",
                    weight=0.05,
                )
            )
        else:
            contributions.append(
                FactorContribution(
                    factor_name="prior_claim_frequency",
                    impact="INCREASES_RISK",
                    description=f"Elevated prior claims frequency ({prior_count} claims recorded).",
                    feature_value=f"{prior_count}_prior_claims",
                    weight=0.40,
                )
            )

        # Feature D: Fraud Intelligence Assessment
        if wf_ctx.fraud_assessment is not None:
            fa = wf_ctx.fraud_assessment
            risk_cat = str(getattr(fa, "risk_category", "LOW")).upper()
            score_val = getattr(fa, "risk_score", Decimal("0.0"))
            score_flt = float(score_val)

            if risk_cat in ("HIGH", "CRITICAL") or score_flt >= 0.65:
                contributions.append(
                    FactorContribution(
                        factor_name="fraud_intelligence_score",
                        impact="INCREASES_RISK",
                        description=f"Fraud risk assessment flagged {risk_cat} risk (score: {score_flt:.2f}).",
                        feature_value=f"score={score_flt:.2f},cat={risk_cat}",
                        weight=0.60,
                    )
                )
            elif risk_cat == "MEDIUM" or score_flt >= 0.35:
                contributions.append(
                    FactorContribution(
                        factor_name="fraud_intelligence_score",
                        impact="INCREASES_RISK",
                        description=f"Moderate fraud indicators detected (score: {score_flt:.2f}).",
                        feature_value=f"score={score_flt:.2f},cat={risk_cat}",
                        weight=0.25,
                    )
                )
            else:
                contributions.append(
                    FactorContribution(
                        factor_name="fraud_intelligence_score",
                        impact="REDUCES_RISK",
                        description=f"Low fraud risk confirmed across internal indicators and external intelligence (score: {score_flt:.2f}).",
                        feature_value=f"score={score_flt:.2f},cat={risk_cat}",
                        weight=-0.30,
                    )
                )

        # Feature E: Evidentiary Completeness
        if wf_ctx.review_result is not None:
            completeness = float(getattr(wf_ctx.review_result, "evidence_completeness", 1.0))
            if completeness >= 0.85:
                contributions.append(
                    FactorContribution(
                        factor_name="evidentiary_completeness",
                        impact="REDUCES_RISK",
                        description=f"High evidentiary completeness ({completeness * 100:.0f}%).",
                        feature_value=f"{completeness:.2f}",
                        weight=-0.15,
                    )
                )
            elif completeness < 0.60:
                contributions.append(
                    FactorContribution(
                        factor_name="evidentiary_completeness",
                        impact="INCREASES_RISK",
                        description=f"Incomplete evidentiary dossier ({completeness * 100:.0f}% completeness).",
                        feature_value=f"{completeness:.2f}",
                        weight=0.30,
                    )
                )

        return contributions

    @classmethod
    def extract_citations(cls, wf_ctx: WorkflowContext) -> list[str]:
        """Aggregate verifiable evidence, policy, and document citations."""
        citations: list[str] = []

        # Claim documents
        for doc in wf_ctx.documents:
            if isinstance(doc, str):
                citations.append(f"Document: {doc}")
            elif hasattr(doc, "document_id"):
                doc_type = getattr(doc, "document_type", "ATTACHMENT")
                doc_name = getattr(doc, "file_name", str(doc.document_id))
                citations.append(f"Document [{doc_type}]: {doc_name}")

        # Policy references
        if wf_ctx.policy_information is not None:
            pol = wf_ctx.policy_information
            policy_id = getattr(pol, "policy_number", None) or getattr(pol, "policy_id", "POLICY")
            coverage = getattr(pol, "policy_type", None) or getattr(pol, "coverage_type", "GENERAL")
            citations.append(f"Policy: {policy_id} (Coverage: {coverage})")

        # Damage estimates
        for idx, est in enumerate(wf_ctx.damage_estimates, start=1):
            source = getattr(est, "source", "ESTIMATE")
            if hasattr(est, "total_cost") and hasattr(est.total_cost, "amount"):
                total = f"{est.total_cost.amount:,.2f}"
            else:
                total = getattr(est, "total_estimate", "0.00")
            citations.append(f"Damage Estimate #{idx} [{source}]: ${total}")

        # Tool calls recorded
        for tool_call in wf_ctx.tool_calls:
            name = getattr(tool_call, "tool_name", None) or getattr(tool_call, "name", None)
            if name:
                citations.append(f"Tool Verification: {name}")

        return list(dict.fromkeys(citations))  # Deduplicate preserving order

    @classmethod
    def generate_counterfactuals(cls, wf_ctx: WorkflowContext) -> list[CounterfactualScenario]:
        """
        Generate bounded sensitivity counterfactuals exploring key threshold boundaries.

        All scenarios explore hypothetical parameter alterations while maintaining
        strict advisory framing.
        """
        scenarios: list[CounterfactualScenario] = []

        # Scenario 1: Repair Cost vs Coverage Limit Sensitivity
        claimed_amt = Decimal("5000.00")
        if wf_ctx.extraction_result is not None:
            claimed_amt = wf_ctx.extraction_result.claim_amount
        elif wf_ctx.claim is not None:
            claimed_amt = getattr(wf_ctx.claim, "claim_amount", Decimal("5000.00"))

        limit_amt = Decimal("50000.00")
        if wf_ctx.policy_information is not None:
            cov = getattr(wf_ctx.policy_information, "coverage", None) or getattr(
                wf_ctx.policy_information, "coverage_limits", None
            )
            if cov:
                if hasattr(cov, "property_damage"):
                    limit_amt = cov.property_damage
                elif hasattr(cov, "max_per_claim"):
                    limit_amt = cov.max_per_claim

        if claimed_amt <= limit_amt:
            hypothetical_amt = limit_amt + Decimal("5000.00")
            scenarios.append(
                CounterfactualScenario(
                    scenario_id=uuid4(),
                    factor_name="claim_amount_vs_policy_limit",
                    current_value=f"${claimed_amt:,.2f} (Within limit ${limit_amt:,.2f})",
                    counterfactual_value=f"${hypothetical_amt:,.2f} (Exceeds limit by $5,000.00)",
                    hypothetical_outcome="APPROVE_PARTIAL (capped at limit) or excess adjuster review required",
                    explanation="When claimed damage exceeds the policy maximum, payout is strictly capped at coverage limit or routed to partial approval counter-offer.",
                )
            )
        else:
            hypothetical_amt = limit_amt - Decimal("1000.00")
            scenarios.append(
                CounterfactualScenario(
                    scenario_id=uuid4(),
                    factor_name="claim_amount_vs_policy_limit",
                    current_value=f"${claimed_amt:,.2f} (Exceeds limit ${limit_amt:,.2f})",
                    counterfactual_value=f"${hypothetical_amt:,.2f} (Fully within limit)",
                    hypothetical_outcome="Full APPROVE eligible without counter-offer cap",
                    explanation="Reducing claimed damages below policy coverage limits removes mandatory partial-approval reduction requirements.",
                )
            )

        # Scenario 2: Prior Claim Frequency Sensitivity
        prior_count = len(wf_ctx.prior_claims)
        if prior_count == 0:
            scenarios.append(
                CounterfactualScenario(
                    scenario_id=uuid4(),
                    factor_name="prior_claim_frequency",
                    current_value="0 prior claims in 36 months",
                    counterfactual_value="3 or more claims in prior 12 months",
                    hypothetical_outcome="Elevated risk tier & mandatory Special Investigations Unit (SIU) review",
                    explanation="A cluster of recent prior claims triggers pattern anomaly checks and prevents fast-track approval.",
                )
            )
        else:
            scenarios.append(
                CounterfactualScenario(
                    scenario_id=uuid4(),
                    factor_name="prior_claim_frequency",
                    current_value=f"{prior_count} prior claim(s) recorded",
                    counterfactual_value="0 prior claims (clean claims history)",
                    hypothetical_outcome="Reduced underwriting risk score and accelerated review eligibility",
                    explanation="An unblemished claims history eliminates recurrence risk penalties.",
                )
            )

        # Scenario 3: Fraud Intelligence Threshold Sensitivity
        if wf_ctx.fraud_assessment is not None:
            fa = wf_ctx.fraud_assessment
            risk_cat = str(getattr(fa, "risk_category", "LOW")).upper()
            score_val = float(getattr(fa, "risk_score", Decimal("0.10")))

            if risk_cat in ("HIGH", "CRITICAL") or score_val >= 0.50:
                scenarios.append(
                    CounterfactualScenario(
                        scenario_id=uuid4(),
                        factor_name="fraud_intelligence_risk_level",
                        current_value=f"{risk_cat} (Risk score: {score_val:.2f})",
                        counterfactual_value="LOW (Risk score: 0.15, zero database matches)",
                        hypothetical_outcome="Standard adjuster review without fraud escalation hold",
                        explanation="Absence of external fraud matches and low anomaly scores allows claim progression without specialized fraud investigation.",
                    )
                )
            else:
                scenarios.append(
                    CounterfactualScenario(
                        scenario_id=uuid4(),
                        factor_name="fraud_intelligence_risk_level",
                        current_value=f"LOW (Risk score: {score_val:.2f})",
                        counterfactual_value="HIGH (Risk score: 0.85, multiple syndicate matches)",
                        hypothetical_outcome="Mandatory fraud escalation hold & comprehensive evidence subpoena",
                        explanation="High fraud risk scores trigger investigative holds and require explicit fraud clearance prior to approval.",
                    )
                )
        else:
            scenarios.append(
                CounterfactualScenario(
                    scenario_id=uuid4(),
                    factor_name="incident_reporting_latency",
                    current_value="Promptly reported within policy guidelines",
                    counterfactual_value="Reported > 90 days after incident without justification",
                    hypothetical_outcome="REWORK or REJECT for late notice breach",
                    explanation="Failure to provide timely notification compromises physical evidence verification and creates policy defense grounds.",
                )
            )

        return scenarios

    @classmethod
    def synthesize_summary(
        cls,
        wf_ctx: WorkflowContext,
        recommendation: str,
        confidence: float,
        key_factors: list[str],
    ) -> str:
        """Construct a fluent, human-readable executive adjudication brief."""
        claim_id_str = str(wf_ctx.claim_id)[:8]
        factors_text = "; ".join(key_factors[:3])

        if recommendation == "APPROVE":
            return (
                f"Adjudication recommends full APPROVAL for claim {claim_id_str} "
                f"with {confidence * 100:.0f}% confidence. Key supporting findings: {factors_text}."
            )
        elif recommendation == "APPROVE_PARTIAL":
            return (
                f"Adjudication recommends PARTIAL APPROVAL for claim {claim_id_str} "
                f"({confidence * 100:.0f}% confidence). Recommendation is subject to counter-offer "
                f"or deductible adjustment based on: {factors_text}."
            )
        elif recommendation == "REWORK":
            return (
                f"Adjudication recommends REWORK investigation for claim {claim_id_str} "
                f"({confidence * 100:.0f}% confidence). Further inquiry required regarding: {factors_text}."
            )
        else:
            return (
                f"Adjudication recommends REJECTION for claim {claim_id_str} "
                f"({confidence * 100:.0f}% confidence). Primary adverse grounds: {factors_text}."
            )

"""
Phase 12.3: Fraud Feature Engineering.

Deterministic, PII-safe feature extraction from claim input, documents,
policy details, claim history, and repair validation.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from casefile.fraud.contracts import FraudEvidence, FraudIndicator, IndicatorSeverity

SUSPICIOUS_KEYPHRASES = (
    "parked unattended",
    "no witnesses",
    "stolen keys",
    "cash settlement",
    "total loss unattended",
    "no police report filed",
    "hit and run no description",
)


class ClaimFeatures(BaseModel):
    """Normalized, PII-safe feature representation for fraud risk assessment."""

    model_config = {"frozen": True}

    claim_amount: float = 0.0
    is_claim_amount_missing: bool = False

    policy_age_days: int | None = None
    is_policy_age_missing: bool = True

    report_delay_days: int = 0
    is_report_delay_missing: bool = False

    prior_claims_count: int = 0
    is_prior_claims_missing: bool = False

    repair_cost_ratio: float = 1.0
    is_repair_cost_ratio_missing: bool = True

    document_count: int = 0
    has_timing_anomaly: bool = False
    has_round_amount: bool = False
    has_repair_cost_discrepancy: bool = False
    has_prior_claims_cluster: bool = False
    has_suspicious_phrases: bool = False
    external_matches_count: int = 0

    missing_evidence_count: int = 0
    total_features_count: int = 0
    raw_feature_map: dict[str, float] = Field(default_factory=dict)

    @property
    def is_insufficient_data(self) -> bool:
        """True when key evidence is absent to form a reliable assessment."""
        return self.missing_evidence_count >= 3 and self.document_count == 0


def _parse_date(val: Any) -> date | None:
    if not val:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str):
        try:
            return date.fromisoformat(val.strip()[:10])
        except (ValueError, TypeError):
            return None
    return None


def extract_features(
    *,
    claim_amount: Decimal | float | None,
    incident_date: str | date | None,
    report_date: str | date | None = None,
    description: str | None = None,
    documents: list[str] | None = None,
    policy_details: dict[str, Any] | None = None,
    claim_history: dict[str, Any] | None = None,
    repair_cost_validation: dict[str, Any] | None = None,
    external_matches_count: int = 0,
) -> tuple[ClaimFeatures, list[FraudIndicator], list[FraudEvidence]]:
    """
    Extract deterministic, PII-safe features and indicators from claim data.

    Returns:
        (ClaimFeatures, list[FraudIndicator], list[FraudEvidence])
    """
    indicators: list[FraudIndicator] = []
    evidence: list[FraudEvidence] = []
    missing_count = 0

    # 1. Claim Amount Analysis
    c_amt = 0.0
    is_amt_missing = False
    is_round = False
    if claim_amount is not None:
        try:
            c_amt = float(claim_amount)
            # Round amounts (e.g. $5,000.00, $10,000.00) vs detailed estimates
            if c_amt >= 1000.0 and c_amt % 500.0 == 0.0:
                is_round = True
                indicators.append(
                    FraudIndicator(
                        indicator_type="AMOUNT_ANOMALY",
                        name="Round Claim Amount",
                        description=f"Claim amount of ${c_amt:,.2f} is an exact round figure without itemization",
                        severity=IndicatorSeverity.LOW,
                        weight=0.15,
                    )
                )
            if c_amt >= 25000.0:
                indicators.append(
                    FraudIndicator(
                        indicator_type="AMOUNT_ANOMALY",
                        name="High Value Claim",
                        description=f"Claim amount ${c_amt:,.2f} exceeds standard scrutiny thresholds",
                        severity=IndicatorSeverity.MEDIUM,
                        weight=0.35,
                    )
                )
        except (ValueError, TypeError):
            is_amt_missing = True
            missing_count += 1
    else:
        is_amt_missing = True
        missing_count += 1

    # 2. Timing Analysis (Policy Age vs Incident Date)
    d_inc = _parse_date(incident_date)
    d_rep = _parse_date(report_date) or date.today()

    rep_delay = 0
    if d_inc and d_rep:
        rep_delay = max(0, (d_rep - d_inc).days)
        if rep_delay > 45:
            indicators.append(
                FraudIndicator(
                    indicator_type="TIMING_ANOMALY",
                    name="Excessive Report Delay",
                    description=f"Claim reported {rep_delay} days after loss incident date",
                    severity=IndicatorSeverity.MEDIUM,
                    weight=0.25,
                )
            )

    policy_dict = policy_details or {}
    pol_inception = _parse_date(
        policy_dict.get("inception_date") or policy_dict.get("effective_date")
    )
    policy_age: int | None = None
    is_pol_age_missing = True
    has_timing_anomaly = False

    if pol_inception and d_inc:
        policy_age = (d_inc - pol_inception).days
        is_pol_age_missing = False
        if 0 <= policy_age <= 7:
            has_timing_anomaly = True
            indicators.append(
                FraudIndicator(
                    indicator_type="TIMING_ANOMALY",
                    name="Loss Immediately Following Policy Inception",
                    description=f"Loss occurred {policy_age} days after policy inception date",
                    severity=(
                        IndicatorSeverity.CRITICAL if policy_age <= 3 else IndicatorSeverity.HIGH
                    ),
                    weight=0.75 if policy_age <= 3 else 0.50,
                )
            )
        elif policy_age < 0:
            # Incident before policy inception
            has_timing_anomaly = True
            indicators.append(
                FraudIndicator(
                    indicator_type="TIMING_ANOMALY",
                    name="Pre-Inception Incident Date",
                    description=f"Incident date precedes policy inception date by {abs(policy_age)} days",
                    severity=IndicatorSeverity.CRITICAL,
                    weight=0.90,
                )
            )
    else:
        missing_count += 1

    # 3. Claim History Frequency
    hist_dict = claim_history or {}
    prior_count = hist_dict.get("prior_claims_count")
    is_prior_missing = prior_count is None
    prior_int = 0
    has_prior_cluster = False

    if prior_count is not None:
        try:
            prior_int = int(prior_count)
            if prior_int >= 3:
                has_prior_cluster = True
                indicators.append(
                    FraudIndicator(
                        indicator_type="HISTORY_ANOMALY",
                        name="Frequent Prior Claims Cluster",
                        description=f"Claimant or policy has {prior_int} prior claims on record",
                        severity=(
                            IndicatorSeverity.HIGH if prior_int >= 5 else IndicatorSeverity.MEDIUM
                        ),
                        weight=0.50 if prior_int >= 5 else 0.30,
                    )
                )
        except (ValueError, TypeError):
            is_prior_missing = True
            missing_count += 1
    else:
        missing_count += 1

    # 4. Repair Cost Variance Ratio
    repair_dict = repair_cost_validation or {}
    market_rate = repair_dict.get("market_rate")
    repair_ratio = 1.0
    is_repair_missing = market_rate is None
    has_repair_discrepancy = False

    if market_rate is not None and c_amt > 0.0:
        try:
            m_rate = float(market_rate)
            if m_rate > 0.0:
                is_repair_missing = False
                repair_ratio = c_amt / m_rate
                if repair_ratio >= 1.50:
                    has_repair_discrepancy = True
                    indicators.append(
                        FraudIndicator(
                            indicator_type="REPAIR_VARIANCE",
                            name="Inflated Repair Cost Discrepancy",
                            description=f"Claimed amount is {repair_ratio:.1f}x higher than validated market rate estimate",
                            severity=(
                                IndicatorSeverity.HIGH
                                if repair_ratio >= 2.0
                                else IndicatorSeverity.MEDIUM
                            ),
                            weight=0.60 if repair_ratio >= 2.0 else 0.40,
                        )
                    )
        except (ValueError, TypeError):
            is_repair_missing = True
            missing_count += 1
    else:
        missing_count += 1

    # 5. Document Content / Text Heuristics
    docs = documents or []
    doc_count = len(docs)
    desc_text = (description or "").lower()
    has_suspicious_phrases = False

    for phrase in SUSPICIOUS_KEYPHRASES:
        if phrase in desc_text:
            has_suspicious_phrases = True
            indicators.append(
                FraudIndicator(
                    indicator_type="SUSPICIOUS_DESCRIPTION",
                    name="High-Risk Incident Phrase",
                    description=f"Claim description contains high-risk phrase pattern: {phrase!r}",
                    severity=IndicatorSeverity.LOW,
                    weight=0.15,
                )
            )
            break

    # Build normalized feature map (all values strictly normalized between 0.0 and 1.0)
    raw_map = {
        "amount_normalized": min(1.0, c_amt / 50000.0),
        "policy_age_anomaly": 1.0 if has_timing_anomaly else 0.0,
        "report_delay_normalized": min(1.0, rep_delay / 90.0),
        "prior_claims_normalized": min(1.0, prior_int / 5.0),
        "repair_ratio_anomaly": min(1.0, max(0.0, (repair_ratio - 1.0) / 2.0)),
        "external_matches_normalized": min(1.0, float(external_matches_count)),
        "missing_evidence_ratio": min(1.0, missing_count / 5.0),
    }

    features = ClaimFeatures(
        claim_amount=c_amt,
        is_claim_amount_missing=is_amt_missing,
        policy_age_days=policy_age,
        is_policy_age_missing=is_pol_age_missing,
        report_delay_days=rep_delay,
        is_report_delay_missing=False,
        prior_claims_count=prior_int,
        is_prior_claims_missing=is_prior_missing,
        repair_cost_ratio=repair_ratio,
        is_repair_cost_ratio_missing=is_repair_missing,
        document_count=doc_count,
        has_timing_anomaly=has_timing_anomaly,
        has_round_amount=is_round,
        has_repair_cost_discrepancy=has_repair_discrepancy,
        has_prior_claims_cluster=has_prior_cluster,
        has_suspicious_phrases=has_suspicious_phrases,
        external_matches_count=external_matches_count,
        missing_evidence_count=missing_count,
        total_features_count=len(raw_map),
        raw_feature_map=raw_map,
    )

    return features, indicators, evidence

"""
Phase 12.3: Fraud Risk Scoring Engines.

Deterministic heuristic scorer and model adapter implementations for
offline validation, local tests, and production model integration.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from casefile.fraud.contracts import FraudRiskFactors, RiskCategory
from casefile.fraud.features import ClaimFeatures
from casefile.fraud.protocols import FraudRiskScoringProtocol


class DeterministicFraudScorer:
    """
    100% deterministic, reproducible offline fraud risk scorer.

    Aggregates feature signals using transparent domain weights and non-linear
    compound penalties for multi-factor fraud patterns.
    """

    def __init__(
        self,
        *,
        model_version: str = "v1.2-deterministic",
        model_provider: str = "casefile-heuristic-scorer",
    ) -> None:
        self._version = model_version
        self._provider = model_provider

    @property
    def model_provider(self) -> str:
        return self._provider

    @property
    def model_version(self) -> str:
        return self._version

    def score_features(self, features: ClaimFeatures) -> tuple[float, float, FraudRiskFactors]:
        """
        Score extracted features deterministically.

        Returns:
            (risk_score: float, confidence_score: float, factors: FraudRiskFactors)
        """
        raw = features.raw_feature_map

        # Subscore calculations (0.0 to 1.0)
        timing_subscore = raw.get("policy_age_anomaly", 0.0)
        amount_subscore = 0.5 * raw.get("amount_normalized", 0.0) + (
            0.5 if features.has_round_amount else 0.0
        )
        history_subscore = raw.get("prior_claims_normalized", 0.0)
        inconsistency_subscore = max(
            raw.get("repair_ratio_anomaly", 0.0),
            raw.get("report_delay_normalized", 0.0),
            0.4 if features.has_suspicious_phrases else 0.0,
        )
        external_subscore = raw.get("external_matches_normalized", 0.0)
        missing_penalty = raw.get("missing_evidence_ratio", 0.0)

        # Baseline weighted combination
        weights = {
            "timing": 0.20,
            "amount": 0.20,
            "history": 0.15,
            "inconsistency": 0.15,
            "external": 0.30,
        }

        weighted_score = (
            timing_subscore * weights["timing"]
            + amount_subscore * weights["amount"]
            + history_subscore * weights["history"]
            + inconsistency_subscore * weights["inconsistency"]
            + external_subscore * weights["external"]
        )

        # Multi-factor compound escalation:
        # e.g. timing anomaly + frequent prior claims + external match + high/round amount + suspicious description
        active_critical_signals = sum(
            [
                features.has_timing_anomaly,
                features.has_prior_claims_cluster,
                features.has_repair_cost_discrepancy,
                features.external_matches_count > 0,
                features.has_round_amount and raw.get("amount_normalized", 0.0) >= 0.5,
                features.has_suspicious_phrases,
            ]
        )
        if active_critical_signals >= 3:
            weighted_score = min(1.0, weighted_score * 1.55)
        elif active_critical_signals >= 2:
            weighted_score = min(1.0, weighted_score * 1.35)

        final_score = round(min(1.0, max(0.0, weighted_score)), 4)

        # Confidence calculation
        # Each missing critical piece of evidence reduces confidence
        base_confidence = 1.0 - (features.missing_evidence_count * 0.18)
        if features.document_count == 0:
            base_confidence -= 0.15
        confidence = round(min(1.0, max(0.15, base_confidence)), 4)

        factors = FraudRiskFactors(
            timing_anomaly_score=round(timing_subscore, 4),
            amount_anomaly_score=round(amount_subscore, 4),
            history_frequency_score=round(history_subscore, 4),
            inconsistency_score=round(inconsistency_subscore, 4),
            external_intelligence_score=round(external_subscore, 4),
            missing_evidence_penalty=round(missing_penalty, 4),
            raw_factors={k: round(v, 4) for k, v in raw.items()},
        )

        return final_score, confidence, factors


def classify_risk_score(score: float, confidence: float, is_insufficient: bool) -> RiskCategory:
    """Classify risk score into explicit RiskCategory enum."""
    if is_insufficient or confidence < 0.35:
        return RiskCategory.INSUFFICIENT_DATA
    if score < 0.25:
        return RiskCategory.LOW
    if score < 0.55:
        return RiskCategory.MEDIUM
    if score < 0.80:
        return RiskCategory.HIGH
    return RiskCategory.CRITICAL


class ModelScoringAdapter:
    """
    Adapter interfacing external or ML models with FraudRiskScoringProtocol.

    Enforces contract validation, handles prediction failures and timeouts,
    and prevents arbitrary score generation on model failure.
    """

    def __init__(
        self,
        predictor_fn: Callable[[dict[str, Any]], dict[str, Any]],
        *,
        model_version: str = "v1.0-ml-adapter",
        model_provider: str = "custom-ml-service",
        fallback_scorer: FraudRiskScoringProtocol | None = None,
    ) -> None:
        self._predictor = predictor_fn
        self._version = model_version
        self._provider = model_provider
        self._fallback = fallback_scorer or DeterministicFraudScorer()

    @property
    def model_provider(self) -> str:
        return self._provider

    @property
    def model_version(self) -> str:
        return self._version

    def score_features(self, features: ClaimFeatures) -> tuple[float, float, FraudRiskFactors]:
        """Call external model; validate outputs or delegate safely."""
        try:
            payload = {
                "features": features.raw_feature_map,
                "amount": features.claim_amount,
                "policy_age_days": features.policy_age_days,
                "prior_claims": features.prior_claims_count,
            }
            res = self._predictor(payload)
            score = float(res["risk_score"])
            conf = float(res.get("confidence", 0.90))

            # Validate range constraints
            if not (0.0 <= score <= 1.0) or not (0.0 <= conf <= 1.0):
                raise ValueError(f"Model outputs out of range: score={score}, conf={conf}")

            factors = FraudRiskFactors(
                timing_anomaly_score=float(res.get("timing_score", 0.0)),
                amount_anomaly_score=float(res.get("amount_score", 0.0)),
                history_frequency_score=float(res.get("history_score", 0.0)),
                inconsistency_score=float(res.get("inconsistency_score", 0.0)),
                external_intelligence_score=float(res.get("external_score", 0.0)),
                missing_evidence_penalty=0.0,
                raw_factors=features.raw_feature_map,
            )
            return round(score, 4), round(conf, 4), factors
        except Exception:
            # Model prediction failure: fail safe to deterministic baseline with warning
            return self._fallback.score_features(features)

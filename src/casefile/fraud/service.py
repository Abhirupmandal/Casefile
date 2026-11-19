"""
Phase 12.3: Fraud Assessment Coordinator Service.

Orchestrates feature extraction, local ML/heuristic risk scoring,
external fraud intelligence queries, and evidence normalization.
"""

from __future__ import annotations

import time
from uuid import uuid4

from casefile.fraud.contracts import (
    ExternalFraudMatch,
    FraudAssessmentMetadata,
    FraudAssessmentStatus,
    FraudDatabaseQuery,
    FraudIndicator,
    FraudRiskRequest,
    FraudRiskResult,
    IndicatorSeverity,
    RiskCategory,
)
from casefile.fraud.external import FakeExternalFraudDatabase, hash_identifier
from casefile.fraud.features import extract_features
from casefile.fraud.protocols import (
    ExternalFraudIntelligenceProtocol,
    FraudRiskScoringProtocol,
)
from casefile.fraud.scoring import DeterministicFraudScorer, classify_risk_score


class FraudAssessmentService:
    """
    Central coordinator orchestrating fraud intelligence and risk assessment.

    Guarantees:
    - Purely investigative signal: NEVER authorizes approvals or rejections directly.
    - Transparent, deterministic evaluation.
    - Full telemetry and provenance tracking without PII leakage.
    """

    def __init__(
        self,
        *,
        scorer: FraudRiskScoringProtocol | None = None,
        external_database: ExternalFraudIntelligenceProtocol | None = None,
        external_db: ExternalFraudIntelligenceProtocol | None = None,
        cache: dict[str, FraudRiskResult] | None = None,
    ) -> None:
        self._scorer = scorer or DeterministicFraudScorer()
        self._external_db = external_database or external_db or FakeExternalFraudDatabase()
        self._cache = cache if cache is not None else {}

    @property
    def scorer(self) -> FraudRiskScoringProtocol:
        return self._scorer

    @property
    def external_database(self) -> ExternalFraudIntelligenceProtocol:
        return self._external_db

    def assess_claim(self, request: FraudRiskRequest) -> FraudRiskResult:
        """
        Execute comprehensive fraud risk assessment for a claim.

        Pipeline:
        1. Extract PII-safe external database query hashes.
        2. Query external fraud intelligence database (if enabled).
        3. Extract normalized features and indicators.
        4. Score features via local heuristic / ML scoring engine.
        5. Aggregate findings, classify risk category, and assemble structured result.
        """
        start_time = time.perf_counter()
        warnings: list[str] = []
        external_matches: list[ExternalFraudMatch] = []

        # 1. External Database Lookup (PII-safe)
        if request.external_intelligence and self._external_db is not None:
            pol_hash = hash_identifier(request.policy_id) if request.policy_id else None

            # Look for VIN in policy details or description
            p_details = request.policy_details or {}
            vin_val = p_details.get("vin") or p_details.get("vehicle_vin")
            vin_hash = hash_identifier(str(vin_val)) if vin_val else None

            # Look for body shop identifier in repair validation
            r_details = request.repair_cost_validation or {}
            shop_id = r_details.get("body_shop_id") or r_details.get("repair_facility_id")

            query = FraudDatabaseQuery(
                claim_id=request.claim_id,
                policy_number_hash=pol_hash,
                vin_or_vehicle_ref_hash=vin_hash,
                body_shop_ref=str(shop_id) if shop_id else None,
            )

            try:
                db_resp = self._external_db.query_database(query)
                if db_resp.status in ("TIMEOUT", "UNAVAILABLE"):
                    warnings.append(
                        f"External fraud intelligence query warning: {db_resp.error_message or db_resp.status}"
                    )
                else:
                    external_matches.extend(db_resp.matches)
            except Exception as exc:
                warnings.append(f"External fraud database query exception: {exc}")

        # 2. Feature Extraction
        features, indicators, evidence = extract_features(
            claim_amount=request.claim_amount,
            incident_date=request.incident_date,
            description=request.description,
            documents=request.documents,
            policy_details=request.policy_details,
            claim_history=request.claim_history,
            repair_cost_validation=request.repair_cost_validation,
            external_matches_count=len(external_matches),
        )

        # 3. Add indicators for external matches
        for m in external_matches:
            indicators.append(
                FraudIndicator(
                    indicator_type="EXTERNAL_INTELLIGENCE_MATCH",
                    name=f"Match in {m.source_database}",
                    description=f"{m.matched_entity_masked}: {m.match_reason}",
                    severity=(
                        IndicatorSeverity.HIGH if m.confidence >= 0.85 else IndicatorSeverity.MEDIUM
                    ),
                    weight=0.35 if m.confidence >= 0.85 else 0.20,
                    evidence_refs=m.evidence_refs,
                )
            )

        # 4. Score Features
        try:
            risk_score, confidence, risk_factors = self._scorer.score_features(features)
            is_insufficient = features.is_insufficient_data
            risk_cat = classify_risk_score(risk_score, confidence, is_insufficient)
            scoring_failed = False
        except Exception as exc:
            warnings.append(f"Scoring engine failure: {exc}")
            risk_score = 0.50
            confidence = 0.0
            from casefile.fraud.contracts import FraudRiskFactors

            risk_factors = FraudRiskFactors()
            risk_cat = RiskCategory.INSUFFICIENT_DATA
            is_insufficient = True
            scoring_failed = True

        # 5. Risk Category Classification & Status
        has_provider_failure = scoring_failed or any(
            "exception" in w.lower() or "timeout" in w.lower() or "unavailable" in w.lower()
            for w in warnings
        )
        if scoring_failed:
            status = FraudAssessmentStatus.FAILED
        elif is_insufficient or has_provider_failure:
            status = FraudAssessmentStatus.INSUFFICIENT_DATA
        else:
            status = FraudAssessmentStatus.COMPLETED

        # 6. Assemble Investigative Summary
        findings = self._compose_findings_summary(
            risk_category=risk_cat,
            risk_score=risk_score,
            confidence=confidence,
            indicators=indicators,
            external_matches=external_matches,
            is_insufficient=is_insufficient,
        )
        if warnings:
            findings = f"{findings} Warnings: {'; '.join(warnings)}."

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0

        prov = getattr(self._scorer, "model_provider", "deterministic")
        m_prov = prov if isinstance(prov, str) else "deterministic"
        ver = getattr(self._scorer, "model_version", "1.0.0")
        m_ver = ver if isinstance(ver, str) else "1.0.0"

        metadata = FraudAssessmentMetadata(
            model_provider=m_prov,
            model_version=m_ver,
            assessment_version="1.0.0",
            duration_ms=round(elapsed_ms, 2),
            features_extracted_count=features.total_features_count,
            external_matches_count=len(external_matches),
            warnings=warnings,
        )

        # Claims with HIGH or CRITICAL risk, provider failures, or critical indicators require explicit scrutiny
        requires_scrutiny = (
            has_provider_failure
            or risk_cat
            in (RiskCategory.HIGH, RiskCategory.CRITICAL, RiskCategory.INSUFFICIENT_DATA)
            or risk_score >= 0.55
            or any(
                ind.severity in (IndicatorSeverity.HIGH, IndicatorSeverity.CRITICAL)
                for ind in indicators
            )
        )

        result = FraudRiskResult(
            assessment_id=uuid4(),
            claim_id=request.claim_id,
            workflow_run_id=request.workflow_run_id,
            risk_score=risk_score,
            risk_category=risk_cat,
            confidence_score=confidence,
            status=status,
            indicators=indicators,
            risk_factors=risk_factors,
            external_matches=external_matches,
            metadata=metadata,
            findings_summary=findings,
            requires_human_scrutiny=requires_scrutiny,
        )

        return result

    def _compose_findings_summary(
        self,
        *,
        risk_category: RiskCategory,
        risk_score: float,
        confidence: float,
        indicators: list[FraudIndicator],
        external_matches: list[ExternalFraudMatch],
        is_insufficient: bool,
    ) -> str:
        """Compose professional, objective investigative summary (never accuses of crimes)."""
        if is_insufficient or risk_category == RiskCategory.INSUFFICIENT_DATA:
            return (
                f"Fraud assessment concluded INSUFFICIENT_DATA (confidence: {confidence:.2f}). "
                "Essential claim records or policy details were unavailable to compute reliable risk metrics."
            )

        indicator_names = [ind.name for ind in indicators]
        ind_str = ", ".join(indicator_names) if indicator_names else "no significant anomalies"

        ext_str = ""
        if external_matches:
            ext_str = (
                f" Flagged in {len(external_matches)} external fraud intelligence database(s)."
            )

        return (
            f"Risk Category: {risk_category.value} (score: {risk_score:.4f}, confidence: {confidence:.2f}). "
            f"Investigative signals identified: {ind_str}.{ext_str}"
        )

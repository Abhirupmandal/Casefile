"""
Phase 12.3 Unit Tests: Fraud Intelligence, Feature Engineering & Risk Scoring.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest

from casefile.fraud.contracts import (
    FraudDatabaseQuery,
    FraudRiskRequest,
    IndicatorSeverity,
    RiskCategory,
)
from casefile.fraud.external import (
    FakeExternalFraudDatabase,
    hash_identifier,
)
from casefile.fraud.features import (
    extract_features,
)
from casefile.fraud.scoring import (
    DeterministicFraudScorer,
    ModelScoringAdapter,
    classify_risk_score,
)
from casefile.fraud.service import FraudAssessmentService
from casefile.observability.spans import (
    SPAN_FRAUD_ASSESSMENT,
    fraud_assessment_attributes,
)


class TestFraudFeatureEngineering:
    def test_clean_low_risk_claim_features(self) -> None:
        d_inc = date.today() - timedelta(days=5)
        d_rep = date.today() - timedelta(days=4)
        pol_inc = date.today() - timedelta(days=365)

        features, indicators, _ = extract_features(
            claim_amount=Decimal("1250.75"),
            incident_date=d_inc.isoformat(),
            report_date=d_rep.isoformat(),
            description="Minor scrape on parking bollard while reversing.",
            documents=["report.pdf"],
            policy_details={"inception_date": pol_inc.isoformat()},
            claim_history={"prior_claims_count": 0},
            repair_cost_validation={"market_rate": 1300.0},
        )

        assert features.claim_amount == 1250.75
        assert not features.has_timing_anomaly
        assert not features.has_round_amount
        assert not features.has_repair_cost_discrepancy
        assert not features.has_prior_claims_cluster
        assert features.missing_evidence_count == 0
        assert not features.is_insufficient_data
        assert len(indicators) == 0

    def test_timing_anomaly_pre_inception(self) -> None:
        d_inc = date(2026, 4, 1)
        pol_inc = date(2026, 4, 10)  # Incident precedes inception

        features, indicators, _ = extract_features(
            claim_amount=Decimal("5000.00"),
            incident_date=d_inc.isoformat(),
            policy_details={"inception_date": pol_inc.isoformat()},
        )

        assert features.has_timing_anomaly
        assert features.policy_age_days == -9
        crit_ind = [i for i in indicators if i.severity == IndicatorSeverity.CRITICAL]
        assert len(crit_ind) >= 1
        assert "pre-inception" in crit_ind[0].name.lower()

    def test_timing_anomaly_loss_immediately_after_inception(self) -> None:
        pol_inc = date(2026, 5, 1)
        d_inc = date(2026, 5, 3)  # 2 days after inception

        features, indicators, _ = extract_features(
            claim_amount=Decimal("8000.00"),
            incident_date=d_inc.isoformat(),
            policy_details={"inception_date": pol_inc.isoformat()},
        )

        assert features.has_timing_anomaly
        assert features.policy_age_days == 2
        crit_ind = [i for i in indicators if i.severity == IndicatorSeverity.CRITICAL]
        assert len(crit_ind) >= 1
        assert "inception" in crit_ind[0].name.lower()

    def test_round_amount_and_high_value_anomalies(self) -> None:
        features, indicators, _ = extract_features(
            claim_amount=Decimal("30000.00"),
            incident_date="2026-06-01",
        )

        assert features.has_round_amount
        ind_types = [i.indicator_type for i in indicators]
        assert "AMOUNT_ANOMALY" in ind_types

    def test_frequent_prior_claims_cluster(self) -> None:
        features, indicators, _ = extract_features(
            claim_amount=Decimal("2200.00"),
            incident_date="2026-06-01",
            claim_history={"prior_claims_count": 5},
        )

        assert features.has_prior_claims_cluster
        assert features.prior_claims_count == 5
        ind = next(i for i in indicators if i.indicator_type == "HISTORY_ANOMALY")
        assert ind.severity == IndicatorSeverity.HIGH

    def test_inflated_repair_cost_variance(self) -> None:
        features, indicators, _ = extract_features(
            claim_amount=Decimal("6000.00"),
            incident_date="2026-06-01",
            repair_cost_validation={"market_rate": 2000.0},  # 3.0x variance
        )

        assert features.has_repair_cost_discrepancy
        assert features.repair_cost_ratio == 3.0
        ind = next(i for i in indicators if i.indicator_type == "REPAIR_VARIANCE")
        assert ind.severity == IndicatorSeverity.HIGH

    def test_suspicious_keyphrase_detection(self) -> None:
        features, indicators, _ = extract_features(
            claim_amount=Decimal("3500.00"),
            incident_date="2026-06-01",
            description="Vehicle was parked unattended overnight and suffered hit and run with no witnesses.",
        )

        assert features.has_suspicious_phrases
        ind = next(i for i in indicators if i.indicator_type == "SUSPICIOUS_DESCRIPTION")
        assert "parked unattended" in ind.description.lower()

    def test_missing_data_explicitly_represented_without_invention(self) -> None:
        features, _, _ = extract_features(
            claim_amount=None,
            incident_date=None,
            policy_details=None,
            claim_history=None,
            repair_cost_validation=None,
            documents=[],
        )

        assert features.is_claim_amount_missing
        assert features.is_policy_age_missing
        assert features.is_prior_claims_missing
        assert features.is_repair_cost_ratio_missing
        assert features.missing_evidence_count >= 4
        assert features.is_insufficient_data


class TestDeterministicFraudScorer:
    @pytest.fixture
    def scorer(self) -> DeterministicFraudScorer:
        return DeterministicFraudScorer()

    def test_low_risk_scoring(self, scorer: DeterministicFraudScorer) -> None:
        d_inc = date.today() - timedelta(days=2)
        pol_inc = date.today() - timedelta(days=200)

        features, _, _ = extract_features(
            claim_amount=Decimal("850.50"),
            incident_date=d_inc.isoformat(),
            policy_details={"inception_date": pol_inc.isoformat()},
            claim_history={"prior_claims_count": 0},
            repair_cost_validation={"market_rate": 900.0},
            documents=["doc.pdf"],
        )

        score, conf, factors = scorer.score_features(features)
        assert score < 0.25
        assert conf >= 0.80
        assert classify_risk_score(score, conf, features.is_insufficient_data) == RiskCategory.LOW

    def test_compound_high_risk_scoring(self, scorer: DeterministicFraudScorer) -> None:
        # Multi-factor fraud pattern: inception timing anomaly + prior claims + inflated repair
        pol_inc = date(2026, 4, 1)
        d_inc = date(2026, 4, 3)

        features, _, _ = extract_features(
            claim_amount=Decimal("15000.00"),
            incident_date=d_inc.isoformat(),
            policy_details={"inception_date": pol_inc.isoformat()},
            claim_history={"prior_claims_count": 4},
            repair_cost_validation={"market_rate": 6000.0},
            documents=["invoice.pdf"],
            external_matches_count=1,
        )

        score, conf, factors = scorer.score_features(features)
        assert score >= 0.55
        cat = classify_risk_score(score, conf, features.is_insufficient_data)
        assert cat in (RiskCategory.HIGH, RiskCategory.CRITICAL)

    def test_insufficient_data_classification(self, scorer: DeterministicFraudScorer) -> None:
        # Empty claim input should result in INSUFFICIENT_DATA, not high fraud
        features, _, _ = extract_features(
            claim_amount=None,
            incident_date=None,
            documents=[],
        )

        score, conf, factors = scorer.score_features(features)
        cat = classify_risk_score(score, conf, features.is_insufficient_data)
        assert cat == RiskCategory.INSUFFICIENT_DATA


class TestModelScoringAdapter:
    def test_adapter_valid_prediction(self) -> None:
        def mock_predictor(payload: Any) -> dict[str, float]:
            return {
                "risk_score": 0.42,
                "confidence": 0.88,
                "timing_score": 0.1,
                "amount_score": 0.2,
                "history_score": 0.0,
                "inconsistency_score": 0.1,
                "external_score": 0.0,
            }

        adapter = ModelScoringAdapter(
            predictor_fn=mock_predictor,
            model_provider="cloud-automl-fraud",
            model_version="v2.1",
        )

        features, _, _ = extract_features(
            claim_amount=Decimal("1500.00"),
            incident_date="2026-05-01",
        )

        score, conf, factors = adapter.score_features(features)
        assert score == 0.42
        assert conf == 0.88
        assert adapter.model_provider == "cloud-automl-fraud"
        assert adapter.model_version == "v2.1"

    def test_adapter_out_of_range_prediction_falls_back_safe(self) -> None:
        def bad_predictor(payload: Any) -> dict[str, float]:
            return {"risk_score": 9.99}  # Invalid range

        adapter = ModelScoringAdapter(predictor_fn=bad_predictor)
        features, _, _ = extract_features(
            claim_amount=Decimal("1000.00"),
            incident_date="2026-05-01",
        )

        score, conf, factors = adapter.score_features(features)
        # Should gracefully fall back to deterministic scorer without raising
        assert 0.0 <= score <= 1.0


class TestExternalFraudDatabase:
    def test_query_database_known_suspect_vin(self) -> None:
        db = FakeExternalFraudDatabase()
        suspect_vin = "VIN-SUSPECT-999"
        query = FraudDatabaseQuery(
            claim_id=uuid4(),
            vin_or_vehicle_ref_hash=hash_identifier(suspect_vin),
        )

        resp = db.query_database(query)
        assert resp.status == "SUCCESS"
        assert len(resp.matches) >= 1
        m = resp.matches[0]
        assert "total-loss" in m.match_reason.lower()
        assert m.confidence >= 0.90
        assert m.is_unverified is True
        assert suspect_vin not in m.matched_entity_masked  # Raw VIN masked

    def test_query_database_clean_record(self) -> None:
        db = FakeExternalFraudDatabase()
        query = FraudDatabaseQuery(
            claim_id=uuid4(),
            vin_or_vehicle_ref_hash=hash_identifier("VIN-CLEAN-55555"),
            policy_number_hash=hash_identifier("POL-CLEAN-77777"),
        )

        resp = db.query_database(query)
        assert resp.status == "NO_MATCH"
        assert len(resp.matches) == 0

    def test_fault_injection_timeout_and_unavailable(self) -> None:
        db_timeout = FakeExternalFraudDatabase(fail_with_timeout=True)
        resp = db_timeout.query_database(FraudDatabaseQuery(claim_id=uuid4()))
        assert resp.status == "TIMEOUT"
        assert resp.error_message is not None and "timed out" in resp.error_message.lower()

        db_unavail = FakeExternalFraudDatabase(fail_with_unavailable=True)
        resp_un = db_unavail.query_database(FraudDatabaseQuery(claim_id=uuid4()))
        assert resp_un.status == "UNAVAILABLE"
        assert resp_un.error_message is not None and "503" in resp_un.error_message


class TestFraudAssessmentService:
    def test_complete_fraud_assessment_nominal_claim(self) -> None:
        svc = FraudAssessmentService()
        claim_id = uuid4()
        req = FraudRiskRequest(
            claim_id=claim_id,
            policy_id="POL-NORMAL-101",
            claimant_name="Alice Smith",
            claim_amount=Decimal("1450.00"),
            incident_date="2026-05-10",
            description="Fender bender at traffic light.",
            documents=["estimate.pdf"],
            policy_details={"inception_date": "2025-01-01"},
            claim_history={"prior_claims_count": 0},
            repair_cost_validation={"market_rate": 1500.0},
        )

        res = svc.assess_claim(req)
        assert res.claim_id == claim_id
        assert res.risk_category == RiskCategory.LOW
        assert res.risk_score < 0.25
        assert not res.requires_human_scrutiny
        assert res.metadata.features_extracted_count > 0
        assert "Investigative signals identified" in res.findings_summary

    def test_complete_fraud_assessment_with_external_match(self) -> None:
        svc = FraudAssessmentService()
        claim_id = uuid4()
        req = FraudRiskRequest(
            claim_id=claim_id,
            policy_id="POL-FRAUD-001",  # Known flagged policy in fake DB
            claimant_name="Bob Suspicious",
            claim_amount=Decimal("18000.00"),
            incident_date="2026-05-10",
            description="Collision with concrete barrier.",
            policy_details={"inception_date": "2026-05-08"},  # 2 days post-inception
            claim_history={"prior_claims_count": 4},
        )

        res = svc.assess_claim(req)
        assert res.risk_category in (RiskCategory.HIGH, RiskCategory.CRITICAL)
        assert res.risk_score >= 0.55
        assert res.requires_human_scrutiny is True
        assert len(res.external_matches) >= 1
        assert "Flagged in 1 external fraud intelligence" in res.findings_summary

    def test_assessment_telemetry_attributes_sanitized(self) -> None:
        attrs = fraud_assessment_attributes(
            model_provider="casefile-heuristic-scorer",
            model_version="v1.2",
            risk_category="HIGH",
            risk_score=0.72,
            confidence_score=0.85,
            duration_ms=12.5,
            features_count=7,
            external_matches_count=1,
        )

        assert attrs["casefile.fraud.risk_category"] == "HIGH"
        assert attrs["casefile.fraud.risk_score"] == 0.72
        # Ensure zero document or PII keys in attributes
        assert "document" not in str(attrs).lower()
        assert "claimant" not in str(attrs).lower()
        assert SPAN_FRAUD_ASSESSMENT == "casefile.fraud_assessment"

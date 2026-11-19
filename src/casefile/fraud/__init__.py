"""
Phase 12.3: Fraud Intelligence and ML Risk Scoring Subsystem.
"""

from casefile.fraud.contracts import (
    ExternalFraudMatch,
    FraudAssessmentMetadata,
    FraudAssessmentStatus,
    FraudDatabaseQuery,
    FraudDatabaseResponse,
    FraudEvidence,
    FraudIndicator,
    FraudRiskFactors,
    FraudRiskRequest,
    FraudRiskResult,
    IndicatorSeverity,
    RiskCategory,
)
from casefile.fraud.external import FakeExternalFraudDatabase, hash_identifier
from casefile.fraud.features import ClaimFeatures, extract_features
from casefile.fraud.protocols import (
    ExternalFraudIntelligenceProtocol,
    FraudRiskScoringProtocol,
)
from casefile.fraud.scoring import (
    DeterministicFraudScorer,
    ModelScoringAdapter,
    classify_risk_score,
)
from casefile.fraud.service import FraudAssessmentService

__all__ = [
    "ClaimFeatures",
    "DeterministicFraudScorer",
    "ExternalFraudIntelligenceProtocol",
    "ExternalFraudMatch",
    "FakeExternalFraudDatabase",
    "FraudAssessmentMetadata",
    "FraudAssessmentService",
    "FraudAssessmentStatus",
    "FraudDatabaseQuery",
    "FraudDatabaseResponse",
    "FraudEvidence",
    "FraudIndicator",
    "FraudRiskFactors",
    "FraudRiskRequest",
    "FraudRiskResult",
    "FraudRiskScoringProtocol",
    "IndicatorSeverity",
    "ModelScoringAdapter",
    "RiskCategory",
    "classify_risk_score",
    "extract_features",
    "hash_identifier",
]

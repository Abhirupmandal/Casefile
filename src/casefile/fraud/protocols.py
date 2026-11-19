"""
Phase 12.3: Fraud Intelligence Protocols.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from casefile.fraud.contracts import (
    FraudDatabaseQuery,
    FraudDatabaseResponse,
    FraudRiskFactors,
)

if TYPE_CHECKING:
    from casefile.fraud.features import ClaimFeatures


class FraudRiskScoringProtocol(Protocol):
    """Protocol for local or ML-backed fraud scoring engines."""

    @property
    def model_provider(self) -> str:
        """Name of the scoring provider/engine."""
        ...

    @property
    def model_version(self) -> str:
        """Version of the model or scoring heuristic."""
        ...

    def score_features(self, features: ClaimFeatures) -> tuple[float, float, FraudRiskFactors]:
        """
        Score extracted features.

        Returns:
            (risk_score, confidence_score, risk_factors)
        """
        ...


class ExternalFraudIntelligenceProtocol(Protocol):
    """Protocol for external fraud intelligence databases (NICB, industry blacklists)."""

    def query_database(self, query: FraudDatabaseQuery) -> FraudDatabaseResponse:
        """Query external database using PII-safe hashed/masked parameters."""
        ...

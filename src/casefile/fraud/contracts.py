"""
Phase 12.3: Fraud Domain Contracts.

Strongly typed Pydantic v2 domain models for fraud detection, ML risk scoring,
and external fraud intelligence integration.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RiskCategory(str, Enum):
    """Categorical risk classification. INSUFFICIENT_DATA is distinct from low risk."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class FraudAssessmentStatus(str, Enum):
    """Operational status of fraud assessment lifecycle."""

    COMPLETED = "COMPLETED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    FAILED = "FAILED"


class IndicatorSeverity(str, Enum):
    """Severity tier of individual fraud indicator."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class FraudEvidence(BaseModel):
    """Structured evidence backing a fraud indicator."""

    model_config = {"frozen": True}

    evidence_id: str = Field(default_factory=lambda: uuid4().hex)
    evidence_type: str
    source_ref: str
    summary: str
    is_verified: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class FraudIndicator(BaseModel):
    """Specific suspicious pattern or anomaly detected during assessment."""

    model_config = {"frozen": True}

    indicator_id: str = Field(default_factory=lambda: uuid4().hex)
    indicator_type: str
    name: str
    description: str
    severity: IndicatorSeverity
    weight: float = Field(default=0.1, ge=0.0, le=1.0)
    evidence_refs: list[str] = Field(default_factory=list)
    detected_at: datetime = Field(default_factory=_utcnow)

    @field_validator("name", "description")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()


class ExternalFraudMatch(BaseModel):
    """Result returned from external fraud intelligence lookup (NICB, blacklist, etc.)."""

    model_config = {"frozen": True}

    match_id: str = Field(default_factory=lambda: uuid4().hex)
    source_database: str
    matched_entity_type: str
    matched_entity_masked: str
    confidence: float = Field(ge=0.0, le=1.0)
    match_reason: str
    first_seen: str | None = None
    last_seen: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    is_unverified: bool = True


class FraudDatabaseQuery(BaseModel):
    """PII-safe query parameters for external fraud intelligence queries."""

    model_config = {"frozen": True}

    query_id: str = Field(default_factory=lambda: uuid4().hex)
    claim_id: UUID
    policy_number_hash: str | None = None
    vin_or_vehicle_ref_hash: str | None = None
    claimant_id_hash: str | None = None
    body_shop_ref: str | None = None


class FraudDatabaseResponse(BaseModel):
    """Standardized response from external fraud intelligence service."""

    model_config = {"frozen": True}

    query_id: str
    status: str
    matches: list[ExternalFraudMatch] = Field(default_factory=list)
    response_ms: float = Field(default=0.0, ge=0.0)
    error_message: str | None = None


class FraudRiskFactors(BaseModel):
    """Decomposed risk factor scores (0.0 to 1.0)."""

    model_config = {"frozen": True}

    timing_anomaly_score: float = Field(default=0.0, ge=0.0, le=1.0)
    amount_anomaly_score: float = Field(default=0.0, ge=0.0, le=1.0)
    history_frequency_score: float = Field(default=0.0, ge=0.0, le=1.0)
    inconsistency_score: float = Field(default=0.0, ge=0.0, le=1.0)
    external_intelligence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    missing_evidence_penalty: float = Field(default=0.0, ge=0.0, le=1.0)
    raw_factors: dict[str, float] = Field(default_factory=dict)


class FraudAssessmentMetadata(BaseModel):
    """Audit and provenance metadata for a fraud risk assessment."""

    model_config = {"frozen": True}

    model_provider: str
    model_version: str
    assessment_version: str = "1.0.0"
    duration_ms: float = Field(default=0.0, ge=0.0)
    features_extracted_count: int = Field(default=0, ge=0)
    external_matches_count: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)


class FraudRiskRequest(BaseModel):
    """Input parameters for a fraud risk assessment."""

    model_config = {"frozen": True}

    claim_id: UUID
    workflow_run_id: UUID | None = None
    policy_id: str | None = None
    claimant_name: str | None = None
    claim_amount: Decimal | None = None
    incident_date: str | None = None
    description: str | None = None
    documents: list[str] = Field(default_factory=list)
    policy_details: dict[str, Any] = Field(default_factory=dict)
    claim_history: dict[str, Any] = Field(default_factory=dict)
    repair_cost_validation: dict[str, Any] = Field(default_factory=dict)
    external_intelligence: bool = True


class FraudRiskResult(BaseModel):
    """
    Authoritative fraud assessment output.

    IMPORTANT SAFETY GUARANTEE:
    This assessment is an investigative signal and recommendation guide only.
    It NEVER automatically approves, rejects, or executes payment on any claim.
    """

    model_config = {"frozen": True}

    assessment_id: UUID = Field(default_factory=uuid4)
    claim_id: UUID
    workflow_run_id: UUID | None = None
    risk_score: float = Field(ge=0.0, le=1.0)
    risk_category: RiskCategory
    confidence_score: float = Field(ge=0.0, le=1.0)
    status: FraudAssessmentStatus = FraudAssessmentStatus.COMPLETED
    indicators: list[FraudIndicator] = Field(default_factory=list)
    risk_factors: FraudRiskFactors = Field(default_factory=FraudRiskFactors)
    external_matches: list[ExternalFraudMatch] = Field(default_factory=list)
    metadata: FraudAssessmentMetadata
    findings_summary: str
    requires_human_scrutiny: bool = False
    assessed_at: datetime = Field(default_factory=_utcnow)

    @field_validator("findings_summary")
    @classmethod
    def _validate_summary(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("findings_summary cannot be empty")
        return v.strip()

"""
Fraud-signal lookup tool: deterministic indicator assessment.

Scores synthetic fraud profiles with explicit severity weights and level
thresholds. Reports signals only — never decides claims.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from casefile.models.domain import FraudIndicator, FraudSeverity, RiskLevel
from casefile.models.versioning import SchemaVersion
from casefile.tools.contracts import ToolContext
from casefile.tools.fixtures import DEFAULT_STORE, FixtureStore
from casefile.tools.registry import Tool

SEVERITY_WEIGHTS: dict[FraudSeverity, float] = {
    FraudSeverity.LOW: 0.1,
    FraudSeverity.MEDIUM: 0.3,
    FraudSeverity.HIGH: 0.5,
}


class FraudSignalInput(BaseModel):
    """Fraud assessment request for one claim scope."""

    model_config = {"frozen": True}

    claim_ref: str
    policy_number: str = ""

    @field_validator("claim_ref")
    @classmethod
    def _check_ref(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("claim_ref cannot be empty")
        if len(stripped) > 64:
            raise ValueError("claim_ref exceeds 64 characters")
        return stripped


class FraudSignalOutput(BaseModel):
    """Risk score, level, and typed indicators."""

    model_config = {"frozen": True}

    claim_ref: str
    risk_score: float
    risk_level: RiskLevel
    indicators: tuple[FraudIndicator, ...] = ()
    requires_manual_review: bool = False
    schema_version: SchemaVersion = "1.0.0"


class FraudSignalLookupTool(Tool[FraudSignalInput, FraudSignalOutput]):
    """Assess fraud signals for a claim from its synthetic profile."""

    name = "fraud_signal_lookup"
    version = "1.0.0"
    description = "Assess fraud risk signals for a claim"
    timeout_seconds = 5.0

    input_model = FraudSignalInput
    output_model = FraudSignalOutput

    def __init__(self, store: FixtureStore | None = None) -> None:
        self._store = store or DEFAULT_STORE

    def run(self, tool_input: FraudSignalInput, ctx: ToolContext) -> FraudSignalOutput:
        """Score the claim's fraud profile with documented weights."""
        profile = self._store.fraud_profile(tool_input.claim_ref)
        raw = profile.indicators if profile is not None else ()
        indicators = tuple(
            FraudIndicator(indicator_type=kind, description=text, severity=severity)
            for kind, text, severity in raw
        )
        score = min(1.0, sum(SEVERITY_WEIGHTS[severity] for _, _, severity in raw))
        level = (
            RiskLevel.LOW
            if score < 0.2
            else (
                RiskLevel.MEDIUM
                if score < 0.5
                else RiskLevel.HIGH if score <= 0.8 else RiskLevel.CRITICAL
            )
        )
        return FraudSignalOutput(
            claim_ref=tool_input.claim_ref,
            risk_score=round(score, 3),
            risk_level=level,
            indicators=indicators,
            requires_manual_review=score > 0.7,
        )

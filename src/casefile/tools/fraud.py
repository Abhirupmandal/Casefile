"""
Fraud-signal lookup tool: deterministic indicator assessment.

Scores synthetic fraud profiles with explicit severity weights and level
thresholds. Reports signals only — never decides claims.
"""

from __future__ import annotations

from typing import Any

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
    cache_ttl = 1800

    input_model = FraudSignalInput
    output_model = FraudSignalOutput

    def __init__(
        self,
        store: FixtureStore | None = None,
        client: Any = None,
        fraud_service: Any = None,
    ) -> None:
        self._store = store or DEFAULT_STORE
        self._client = client
        self._fraud_service = fraud_service

    def run(self, tool_input: FraudSignalInput, ctx: ToolContext) -> FraudSignalOutput:
        """Score the claim's fraud profile with documented weights."""
        if self._client is not None:
            from casefile.tools.adapters.errors import ExternalServiceError
            from casefile.tools.adapters.mapping import map_external_error_to_tool_failure

            try:
                data = self._client.get_fraud_signals(tool_input.claim_ref)
                if data is None:
                    return FraudSignalOutput(
                        claim_ref=tool_input.claim_ref,
                        risk_score=0.0,
                        risk_level=RiskLevel.LOW,
                        indicators=(),
                        requires_manual_review=False,
                    )

                raw_score = float(data.get("fraud_score", 0.0))
                score = min(1.0, max(0.0, raw_score))
                level_str = str(data.get("risk_level", "LOW")).upper()
                try:
                    level = RiskLevel(level_str)
                except ValueError:
                    level = RiskLevel.LOW

                reasons = data.get("reasons", [])
                indicators = tuple(
                    FraudIndicator(
                        indicator_type="EXTERNAL_SIGNAL",
                        description=str(r),
                        severity=FraudSeverity.MEDIUM if score >= 0.5 else FraudSeverity.LOW,
                    )
                    for r in reasons
                )

                return FraudSignalOutput(
                    claim_ref=tool_input.claim_ref,
                    risk_score=round(score, 3),
                    risk_level=level,
                    indicators=indicators,
                    requires_manual_review=score > 0.7
                    or level in (RiskLevel.HIGH, RiskLevel.CRITICAL),
                )
            except ExternalServiceError as exc:
                raise map_external_error_to_tool_failure(exc, self.name) from exc
            except Exception as exc:
                from casefile.tools.contracts import ToolError, ToolErrorCode, ToolFailureError

                raise ToolFailureError(
                    ToolError(
                        code=ToolErrorCode.VALIDATION_FAILURE,
                        message=f"Failed to parse fraud scoring data: {exc}",
                        retryable=False,
                    )
                ) from exc

        if self._fraud_service is not None:
            from uuid import UUID

            from casefile.fraud.contracts import FraudRiskRequest

            try:
                c_uuid = UUID(tool_input.claim_ref)
            except ValueError:
                c_uuid = UUID("00000000-0000-0000-0000-000000000000")

            req = FraudRiskRequest(
                claim_id=c_uuid,
                policy_id=tool_input.policy_number or None,
            )
            res = self._fraud_service.assess_claim(req)
            risk_lvl_str = res.risk_category.value
            if risk_lvl_str == "INSUFFICIENT_DATA":
                lvl = RiskLevel.LOW
            else:
                try:
                    lvl = RiskLevel(risk_lvl_str)
                except ValueError:
                    lvl = RiskLevel.LOW

            domain_inds = tuple(
                FraudIndicator(
                    indicator_type=ind.indicator_type,
                    description=ind.description,
                    severity=(
                        FraudSeverity(ind.severity.value)
                        if ind.severity.value in ("LOW", "MEDIUM", "HIGH")
                        else FraudSeverity.HIGH
                    ),
                )
                for ind in res.indicators
            )
            return FraudSignalOutput(
                claim_ref=tool_input.claim_ref,
                risk_score=round(res.risk_score, 3),
                risk_level=lvl,
                indicators=domain_inds,
                requires_manual_review=res.requires_human_scrutiny,
            )

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

"""
Deterministic payload recipes and claim fixtures for evaluation scenarios.

Every recipe produces a dict keyed for one agent contract; workflow_id /
claim identity is stamped at resolve time so scripts stay reusable across
runs. No randomness, no wall-clock reads, no live providers.
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from casefile.models.contracts import ClaimInput
from casefile.models.versioning import SchemaVersion

UNTRUSTED_MARKER = "UNTRUSTED-MARKER-EVAL-DO-NOT-TRUST"


class PayloadRecipe(str, Enum):
    """Named deterministic payloads the scripted provider can serve."""

    EXTRACTION_OK = "EXTRACTION_OK"
    EXTRACTION_SCHEMA_INVALID = "EXTRACTION_SCHEMA_INVALID"
    EXTRACTION_WRONG_WORKFLOW = "EXTRACTION_WRONG_WORKFLOW"
    EXTRACTION_INCOMPLETE = "EXTRACTION_INCOMPLETE"
    INVESTIGATION_OK = "INVESTIGATION_OK"
    INVESTIGATION_EMPTY_FINDINGS = "INVESTIGATION_EMPTY_FINDINGS"
    REVIEW_APPROVE = "REVIEW_APPROVE"
    REVIEW_REJECT = "REVIEW_REJECT"
    REVIEW_REWORK = "REVIEW_REWORK"
    REVIEW_APPROVE_FRAUD = "REVIEW_APPROVE_FRAUD"


class ClaimSpec(BaseModel):
    """Initial claim content for one scenario. Frozen."""

    model_config = {"frozen": True}

    policy_id: str = "POL-SYN-001"
    claimant_name: str = "Evaluation Claimant"
    incident_date: str = "2026-09-20"
    claim_amount: Decimal = Field(default=Decimal("1500.00"))
    description: str = "Rear collision at Main St; evaluation scenario claim."
    documents: tuple[str, ...] = ("SYN-DOC-N1",)
    untrusted_marker: bool = False
    schema_version: SchemaVersion = "1.0.0"

    def to_claim_input(self, claim_id: UUID | None = None) -> ClaimInput:
        """Build a ClaimInput; optionally pin the claim identity."""
        description = self.description
        if self.untrusted_marker:
            description = f"{description} {UNTRUSTED_MARKER}"
        kwargs: dict[str, object] = {}
        if claim_id is not None:
            kwargs["claim_id"] = claim_id
        return ClaimInput(
            policy_id=self.policy_id,
            claimant_name=self.claimant_name,
            incident_date=self.incident_date,
            claim_amount=self.claim_amount,
            description=description,
            documents=list(self.documents),
            **kwargs,  # type: ignore[arg-type]
        )


def resolve_payload(
    recipe: PayloadRecipe,
    *,
    workflow_id: UUID,
    claim_amount: Decimal = Decimal("1500.00"),
    policy_id: str = "POL-SYN-001",
    claimant_name: str = "Evaluation Claimant",
    incident_date: str = "2026-09-20",
    description: str = "Evaluation claim",
) -> dict[str, object]:
    """Materialize one recipe as a JSON-ready contract payload."""
    if recipe == PayloadRecipe.EXTRACTION_OK:
        return {
            "workflow_id": str(workflow_id),
            "claimant_name": claimant_name,
            "policy_id": policy_id,
            "incident_date": incident_date,
            "incident_location": "Main St",
            "incident_description": description,
            "claim_amount": str(claim_amount),
            "requested_coverage_type": "COLLISION",
            "extraction_confidence": 0.95,
        }
    if recipe == PayloadRecipe.EXTRACTION_SCHEMA_INVALID:
        return {"workflow_id": str(workflow_id), "claimant_name": 12345}
    if recipe == PayloadRecipe.EXTRACTION_WRONG_WORKFLOW:
        payload = resolve_payload(
            PayloadRecipe.EXTRACTION_OK,
            workflow_id=workflow_id,
            claim_amount=claim_amount,
            policy_id=policy_id,
            claimant_name=claimant_name,
            incident_date=incident_date,
            description=description,
        )
        payload["workflow_id"] = "00000000-0000-0000-0000-000000000001"
        return payload
    if recipe == PayloadRecipe.EXTRACTION_INCOMPLETE:
        return {
            "workflow_id": str(workflow_id),
            "claimant_name": claimant_name,
            "policy_id": policy_id,
            "incident_date": incident_date,
            "incident_location": "",
            "incident_description": description,
            "claim_amount": str(claim_amount),
            "requested_coverage_type": "COLLISION",
            "extraction_confidence": 0.40,
            "missing_fields": ["incident_location"],
            "is_complete": False,
        }
    if recipe == PayloadRecipe.INVESTIGATION_OK:
        return {
            "workflow_id": str(workflow_id),
            "policy_details": {"status": "active"},
            "claim_history": {"prior": 0},
            "repair_cost_validation": {"within_range": True},
            "fraud_signals": {"risk": "low"},
            "supporting_documents": ["SYN-DOC-N1"],
            "findings_summary": "Policy active; costs reasonable",
            "evidence_strength": 0.85,
            "tool_calls_made": ["policy_lookup"],
            "investigation_duration_seconds": 3,
            "tools_succeeded": 1,
            "tools_failed": 0,
        }
    if recipe == PayloadRecipe.INVESTIGATION_EMPTY_FINDINGS:
        return {
            "workflow_id": str(workflow_id),
            "findings_summary": "",
            "evidence_strength": 0.0,
            "tool_calls_made": [],
            "tools_succeeded": 0,
            "tools_failed": 0,
        }
    return _review_payload(recipe, workflow_id)


def _review_payload(recipe: PayloadRecipe, workflow_id: UUID) -> dict[str, object]:
    base: dict[str, object] = {
        "workflow_id": str(workflow_id),
        "reasoning": "Evidence complete and consistent",
        "confidence_score": 0.93,
        "evidence_completeness": 1.0,
        "identified_gaps": [],
        "rework_feedback": None,
        "fraud_risk_level": "LOW",
        "fraud_signals_detected": False,
    }
    if recipe == PayloadRecipe.REVIEW_APPROVE:
        base["decision"] = "APPROVE"
        return base
    if recipe == PayloadRecipe.REVIEW_REJECT:
        base["decision"] = "REJECT"
        base["reasoning"] = "Policy exclusion applies"
        return base
    if recipe == PayloadRecipe.REVIEW_REWORK:
        base["decision"] = "REWORK"
        base["rework_feedback"] = "Investigate repair cost variance further"
        base["identified_gaps"] = ["repair_cost_validation"]
        return base
    if recipe == PayloadRecipe.REVIEW_APPROVE_FRAUD:
        base["decision"] = "APPROVE"
        base["fraud_signals_detected"] = True
        base["fraud_risk_level"] = "HIGH"
        return base
    raise ValueError(f"Unknown review recipe {recipe!r}")


class BrokenExporter:
    """Telemetry exporter that always fails (invariant P). Records errors."""

    def __init__(self) -> None:
        self.error_count = 0
        self.exported: list[object] = []

    def export(self, spans: object) -> object:
        """Fail every export while counting the attempts."""
        self.error_count += 1
        from opentelemetry.sdk.trace.export import SpanExportResult

        return SpanExportResult.FAILURE

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        """No-op flush."""
        _ = timeout_millis
        return False

    def shutdown(self) -> None:
        """No-op shutdown."""


class FailingSink:
    """Event sink that raises on emit (telemetry failure injection)."""

    def __init__(self) -> None:
        self.error_count = 0

    def emit(self, payload: object) -> None:
        """Count and raise — the runner wraps this so the run still completes."""
        _ = payload
        self.error_count += 1
        raise RuntimeError("injected telemetry sink failure")

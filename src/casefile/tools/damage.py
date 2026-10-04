"""
Damage-estimate tool: retrieval plus explicit sanity checking.

Reconciles raw estimate components (the validated DamageEstimate model
cannot even represent an inconsistent estimate) and reports findings as
structured data. Never makes payout decisions — evidence only.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, field_validator

from casefile.models.domain import DamageEstimate, Money
from casefile.models.versioning import SchemaVersion
from casefile.tools.contracts import ToolContext, ToolError, ToolErrorCode, ToolFailureError
from casefile.tools.fixtures import DEFAULT_STORE, FixtureStore
from casefile.tools.registry import Tool


class DamageEstimateInput(BaseModel):
    """Estimate retrieval by reference within claim scope."""

    model_config = {"frozen": True}

    estimate_ref: str
    claim_ref: str

    @field_validator("estimate_ref", "claim_ref")
    @classmethod
    def _check_ref(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reference cannot be empty")
        if len(stripped) > 64:
            raise ValueError("reference exceeds 64 characters")
        return stripped


class DamageEstimateOutput(BaseModel):
    """Reconciled estimate (when consistent) plus explicit sanity findings."""

    model_config = {"frozen": True}

    found: bool
    estimate: DamageEstimate | None = None
    declared_total: Money | None = None
    computed_total: Money | None = None
    consistent: bool = False
    findings: tuple[str, ...] = ()
    schema_version: SchemaVersion = "1.0.0"


class DamageEstimateTool(Tool[DamageEstimateInput, DamageEstimateOutput]):
    """Retrieve a damage estimate and sanity-check its reconciliation."""

    name = "repair_cost_lookup"
    version = "1.0.0"
    description = "Retrieve a damage estimate and report reconciliation findings"
    timeout_seconds = 5.0

    input_model = DamageEstimateInput
    output_model = DamageEstimateOutput

    def __init__(self, store: FixtureStore | None = None) -> None:
        self._store = store or DEFAULT_STORE

    def run(self, tool_input: DamageEstimateInput, ctx: ToolContext) -> DamageEstimateOutput:
        """Fetch raw components, recompute totals, report consistency."""
        estimate_ref = tool_input.estimate_ref
        claim_ref = tool_input.claim_ref
        parts = self._store.get_estimate(estimate_ref)
        if parts is None:
            return DamageEstimateOutput(found=False)
        if parts.claim_ref != claim_ref:
            raise ToolFailureError(
                ToolError(
                    code=ToolErrorCode.SCOPE_VIOLATION,
                    message=f"Estimate {estimate_ref!r} is not in claim scope {claim_ref!r}",
                )
            )
        computed = sum((item.total_cost.amount for item in parts.line_items), Decimal("0.00"))
        computed_total = Money(amount=computed, currency=parts.declared_total.currency)
        findings: list[str] = []
        for item in parts.line_items:
            expected = item.unit_cost.amount * item.quantity
            if item.total_cost.amount != expected:
                findings.append(f"line item {item.description!r} total does not reconcile")
        if computed != parts.declared_total.amount:
            findings.append(f"declared total {parts.declared_total.amount} != computed {computed}")
        consistent = not findings
        estimate: DamageEstimate | None = None
        if consistent:
            estimate = DamageEstimate(
                source=parts.source,
                estimate_date=parts.estimate_date,
                line_items=list(parts.line_items),
                labor_hours=parts.labor_hours,
                total_cost=parts.declared_total,
            )
        return DamageEstimateOutput(
            found=True,
            estimate=estimate,
            declared_total=parts.declared_total,
            computed_total=computed_total,
            consistent=consistent,
            findings=tuple(findings),
        )

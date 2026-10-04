"""
Prior-claim lookup tool: scoped, bounded, deterministically ordered history.

Queries one customer scope only, caps result counts, sorts newest-first,
and returns domain PriorClaim objects. No SQL from agents, no connections
exposed, no cross-customer leakage.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from casefile.models.domain import PriorClaim
from casefile.models.versioning import SchemaVersion
from casefile.tools.contracts import ToolContext
from casefile.tools.fixtures import DEFAULT_STORE, FixtureStore
from casefile.tools.registry import Tool

MAX_HISTORY_RESULTS = 50


class PriorClaimLookupInput(BaseModel):
    """Customer-scoped history query with bounded result count."""

    model_config = {"frozen": True}

    customer_id: str
    limit: int = Field(default=10, ge=1, le=MAX_HISTORY_RESULTS)
    include_closed: bool = True

    @field_validator("customer_id")
    @classmethod
    def _normalize(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("customer_id cannot be empty")
        if len(stripped) > 100:
            raise ValueError("customer_id exceeds 100 characters")
        return stripped


class PriorClaimLookupOutput(BaseModel):
    """Deterministically ordered prior claims plus totals."""

    model_config = {"frozen": True}

    customer_id: str
    prior_claims: tuple[PriorClaim, ...] = ()
    total_count: int = Field(default=0, ge=0)
    schema_version: SchemaVersion = "1.0.0"


class PriorClaimLookupTool(Tool[PriorClaimLookupInput, PriorClaimLookupOutput]):
    """Look up prior claims for one customer scope."""

    name = "claim_history_lookup"
    version = "1.0.0"
    description = "Look up previous claims history for a customer scope"
    timeout_seconds = 5.0

    input_model = PriorClaimLookupInput
    output_model = PriorClaimLookupOutput

    def __init__(self, store: FixtureStore | None = None) -> None:
        self._store = store or DEFAULT_STORE

    def run(self, tool_input: PriorClaimLookupInput, ctx: ToolContext) -> PriorClaimLookupOutput:
        """Return newest-first priors for the requested customer only."""
        rows = self._store.priors_for_customer(tool_input.customer_id)
        if not tool_input.include_closed:
            rows = [row for row in rows if row.status.upper() != "CLOSED"]
        selected = rows[: tool_input.limit]
        claims = tuple(
            PriorClaim(
                claim_reference=row.claim_reference,
                claim_date=row.claim_date,
                claim_type=row.claim_type,
                amount=row.amount,
                status=row.status,
                at_fault=row.at_fault,
            )
            for row in selected
        )
        return PriorClaimLookupOutput(
            customer_id=tool_input.customer_id,
            prior_claims=claims,
            total_count=len(rows),
        )

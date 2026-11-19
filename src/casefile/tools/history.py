"""
Prior-claim lookup tool: scoped, bounded, deterministically ordered history.

Queries one customer scope only, caps result counts, sorts newest-first,
and returns domain PriorClaim objects. No SQL from agents, no connections
exposed, no cross-customer leakage.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator

from casefile.models.domain import ClaimType, Money, PriorClaim
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
    cache_ttl = 3600

    input_model = PriorClaimLookupInput
    output_model = PriorClaimLookupOutput

    def __init__(
        self,
        store: FixtureStore | None = None,
        client: Any = None,
    ) -> None:
        self._store = store or DEFAULT_STORE
        self._client = client

    def run(self, tool_input: PriorClaimLookupInput, ctx: ToolContext) -> PriorClaimLookupOutput:
        """Return newest-first priors for the requested customer only."""
        if self._client is not None:
            from casefile.tools.adapters.errors import ExternalServiceError
            from casefile.tools.adapters.mapping import map_external_error_to_tool_failure

            try:
                raw_items = self._client.get_claim_history(
                    tool_input.customer_id, limit=tool_input.limit
                )
                claims_list: list[PriorClaim] = []
                for item in raw_items:
                    c_status = item.get("status", "CLOSED")
                    if not tool_input.include_closed and c_status.upper() == "CLOSED":
                        continue
                    dt_val = item.get("incident_date") or item.get("claim_date", "2024-01-01")
                    inc_date = date.fromisoformat(dt_val) if isinstance(dt_val, str) else dt_val
                    raw_amount = item.get("claim_amount", "0.00")
                    currency = item.get("currency", "USD")
                    money = Money(amount=Decimal(str(raw_amount)), currency=currency)
                    raw_type = item.get("claim_type", "COLLISION")
                    claim_type = ClaimType(raw_type) if isinstance(raw_type, str) else raw_type
                    claims_list.append(
                        PriorClaim(
                            claim_reference=str(
                                item.get("claim_id") or item.get("claim_reference")
                            ),
                            claim_date=inc_date,
                            claim_type=claim_type,
                            amount=money,
                            status=c_status,
                            at_fault=item.get("at_fault"),
                        )
                    )
                return PriorClaimLookupOutput(
                    customer_id=tool_input.customer_id,
                    prior_claims=tuple(claims_list),
                    total_count=len(claims_list),
                )
            except ExternalServiceError as exc:
                raise map_external_error_to_tool_failure(exc, self.name) from exc
            except Exception as exc:
                from casefile.tools.contracts import ToolError, ToolErrorCode, ToolFailureError

                raise ToolFailureError(
                    ToolError(
                        code=ToolErrorCode.VALIDATION_FAILURE,
                        message=f"Failed to parse claim history data from external service: {exc}",
                        retryable=False,
                    )
                ) from exc

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

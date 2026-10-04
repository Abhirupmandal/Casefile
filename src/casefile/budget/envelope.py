"""
Budget envelope: the immutable per-run limit contract (Phase 8 §2).

A run captures its envelope at start; later global config changes never
alter it. All limits are explicit and finite — negatives rejected, zero
means "none allowed". Money uses Decimal. Versioned for audit.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from casefile.config import BudgetConfig
from casefile.models.versioning import SchemaVersion

BUDGET_ENVELOPE_VERSION = "1.0.0"


class BudgetEnvelope(BaseModel):
    """Immutable budget limits captured at run start. Frozen."""

    model_config = {"frozen": True}

    max_steps: int = Field(default=50, ge=0)
    max_agent_steps: int = Field(default=50, ge=0)
    max_tool_calls: int = Field(default=100, ge=0)
    max_rework_cycles: int = Field(default=3, ge=0)
    max_agent_retries: int = Field(default=2, ge=0)
    max_wall_clock_seconds: int = Field(default=1800, ge=0)
    max_input_tokens: int = Field(default=100_000, ge=0)
    max_output_tokens: int = Field(default=20_000, ge=0)
    max_total_tokens: int = Field(default=150_000, ge=0)
    max_cost_usd: Decimal = Field(default=Decimal("5.00"), ge=Decimal("0.00"))
    pricing_version: str = Field(default="test-v1")
    schema_version: SchemaVersion = "1.0.0"

    @field_validator("max_cost_usd")
    @classmethod
    def _validate_cost(cls, value: Decimal) -> Decimal:
        if value.is_nan():
            raise ValueError("max_cost_usd cannot be NaN")
        return value

    @classmethod
    def from_app_config(cls, config: BudgetConfig) -> BudgetEnvelope:
        """Build from the app BudgetConfig (trusted control-plane only)."""
        return cls(
            max_input_tokens=config.max_input_tokens,
            max_output_tokens=config.max_output_tokens,
            max_total_tokens=config.max_total_tokens,
            max_cost_usd=config.max_cost_usd,
            max_steps=config.max_steps,
            max_agent_steps=config.max_steps,
            max_tool_calls=100,
            max_rework_cycles=config.max_rework_cycles,
            max_agent_retries=2,
            max_wall_clock_seconds=config.max_execution_time_seconds,
        )

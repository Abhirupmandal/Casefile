"""
Budget usage: durable counters for one run (Phase 8 §3).

All counters non-negative; money as Decimal. Usage restores from the
database on restart/resume — never from process memory alone.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from casefile.models.versioning import SchemaVersion


class BudgetUsage(BaseModel):
    """Consumed budget for a run. Mutable counters, validated bounds."""

    model_config = {"validate_assignment": True}

    steps: int = Field(default=0, ge=0)
    agent_steps: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    rework_cycles: int = Field(default=0, ge=0)
    agent_retries: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cost_usd: Decimal = Field(default=Decimal("0.00"), ge=Decimal("0.00"))
    tool_latency_ms: int = Field(default=0, ge=0)
    wall_start: datetime = Field(default_factory=lambda: datetime.now(UTC))
    schema_version: SchemaVersion = "1.0.0"

    def elapsed_seconds(self, now: datetime) -> int:
        """Wall-clock seconds consumed since the durable start."""
        delta = (now - self.wall_start).total_seconds()
        return max(0, int(delta))

    def totalize_tokens(self) -> BudgetUsage:
        """Recompute total_tokens from input+output (returns self)."""
        self.total_tokens = self.input_tokens + self.output_tokens
        return self

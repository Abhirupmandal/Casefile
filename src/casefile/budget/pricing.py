"""
Provider-agnostic cost accounting (Phase 8 §5).

Structured model usage (provider/model/tokens) times a versioned pricing
table yields exact Decimal cost. Test/deterministic providers use
explicitly declared pricing — including zero-cost — never invented
real-world rates.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from casefile.models.versioning import SchemaVersion

TEST_PRICING_VERSION = "test-v1"


class ModelPricing(BaseModel):
    """Per-1K-token rates for one provider/model. Frozen."""

    model_config = {"frozen": True}

    provider: str
    model: str
    input_per_1k_usd: Decimal = Field(ge=Decimal("0.00"))
    output_per_1k_usd: Decimal = Field(ge=Decimal("0.00"))
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")


class ModelUsage(BaseModel):
    """Structured usage for one model call. Frozen."""

    model_config = {"frozen": True}

    provider: str
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class PricingTable(BaseModel):
    """Versioned pricing registry. Unknown models raise, never default."""

    pricing_version: str = TEST_PRICING_VERSION
    entries: dict[str, ModelPricing] = Field(default_factory=dict)
    schema_version: SchemaVersion = "1.0.0"

    def rate_for(self, provider: str, model: str) -> ModelPricing:
        """Look up exact pricing or raise a clear error."""
        key = f"{provider}/{model}"
        try:
            return self.entries[key]
        except KeyError:
            raise ValueError(
                f"No pricing declared for {key!r} in {self.pricing_version!r}"
            ) from None


def calculate_cost(usage: ModelUsage, pricing: ModelPricing) -> Decimal:
    """Exact Decimal cost for one model call."""
    if (usage.provider, usage.model) != (pricing.provider, pricing.model):
        raise ValueError("Pricing does not match the recorded model usage")
    total = (
        Decimal(usage.input_tokens) * pricing.input_per_1k_usd
        + Decimal(usage.output_tokens) * pricing.output_per_1k_usd
    ) / Decimal(1000)
    return total.quantize(Decimal("0.0001"))


def zero_pricing(provider: str, model: str) -> ModelPricing:
    """Explicit zero-cost pricing for deterministic test providers."""
    return ModelPricing(
        provider=provider,
        model=model,
        input_per_1k_usd=Decimal("0"),
        output_per_1k_usd=Decimal("0"),
    )


class CostRecord(BaseModel):
    """Typed cost calculation result for a model call."""

    model_config = {"frozen": True}

    provider: str
    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    cost_usd: Decimal = Field(ge=Decimal("0.00"))
    currency: str = "USD"
    pricing_version: str


class CostCalculator:
    """Typed cost calculation abstraction backed by a PricingTable."""

    def __init__(self, pricing_table: PricingTable | None = None) -> None:
        self.pricing_table = pricing_table or PricingTable()

    def calculate(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> CostRecord:
        pricing = self.pricing_table.rate_for(provider, model)
        usage = ModelUsage(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        cost = calculate_cost(usage, pricing)
        return CostRecord(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            cost_usd=cost,
            currency=pricing.currency,
            pricing_version=self.pricing_table.pricing_version,
        )

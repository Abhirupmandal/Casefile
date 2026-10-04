"""
Unit Tests: Budget envelope, usage, termination vocabulary, pricing.
Matrix: A (validation), B (finite defaults), P (zero/negative),
Q (Decimal precision), AC (pricing metadata), AB (deterministic accounting).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from casefile.budget.envelope import BUDGET_ENVELOPE_VERSION, BudgetEnvelope
from casefile.budget.pricing import (
    TEST_PRICING_VERSION,
    ModelPricing,
    ModelUsage,
    PricingTable,
    calculate_cost,
    zero_pricing,
)
from casefile.budget.termination import BudgetTermination, to_workflow_state
from casefile.budget.usage import BudgetUsage
from casefile.config import BudgetConfig
from casefile.models.contracts import WorkflowState


@pytest.mark.unit
class TestBudgetEnvelope:
    def test_finite_defaults(self) -> None:
        envelope = BudgetEnvelope()
        assert envelope.max_steps == 50
        assert envelope.max_agent_steps == 50
        assert envelope.max_tool_calls == 100
        assert envelope.max_rework_cycles == 3
        assert envelope.max_agent_retries == 2
        assert envelope.max_wall_clock_seconds == 1800
        assert envelope.max_input_tokens == 100_000
        assert envelope.max_output_tokens == 20_000
        assert envelope.max_total_tokens == 150_000
        assert envelope.max_cost_usd == Decimal("5.00")
        assert envelope.schema_version == "1.0.0"
        assert BUDGET_ENVELOPE_VERSION == "1.0.0"

    def test_zero_means_none_allowed(self) -> None:
        envelope = BudgetEnvelope(max_steps=0, max_cost_usd=Decimal("0.00"))
        assert envelope.max_steps == 0

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BudgetEnvelope(max_steps=-1)
        with pytest.raises(ValidationError):
            BudgetEnvelope(max_cost_usd=Decimal("-0.01"))
        with pytest.raises(ValidationError):
            BudgetEnvelope(max_agent_retries=-5)

    def test_immutable(self) -> None:
        envelope = BudgetEnvelope()
        with pytest.raises(ValidationError):
            envelope.max_steps = 999

    def test_from_app_config(self) -> None:
        envelope = BudgetEnvelope.from_app_config(BudgetConfig())
        assert envelope.max_steps == 50
        assert envelope.max_rework_cycles == 3
        assert envelope.max_wall_clock_seconds == 1800
        assert envelope.max_cost_usd == Decimal("5.00")

    def test_json_round_trip(self) -> None:
        envelope = BudgetEnvelope(max_steps=7)
        assert BudgetEnvelope.model_validate_json(envelope.model_dump_json()) == envelope


@pytest.mark.unit
class TestBudgetUsage:
    def test_zeroed_defaults(self) -> None:
        usage = BudgetUsage()
        assert usage.steps == 0
        assert usage.cost_usd == Decimal("0.00")

    def test_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            BudgetUsage(steps=-1)
        with pytest.raises(ValidationError):
            BudgetUsage(cost_usd=Decimal("-1.00"))

    def test_elapsed_from_durable_start(self) -> None:
        start = datetime.now(UTC) - timedelta(seconds=90)
        usage = BudgetUsage(wall_start=start)
        assert usage.elapsed_seconds(datetime.now(UTC)) >= 89

    def test_totalize(self) -> None:
        usage = BudgetUsage(input_tokens=30, output_tokens=12)
        usage.totalize_tokens()
        assert usage.total_tokens == 42


@pytest.mark.unit
class TestTerminationVocabulary:
    def test_all_ten_reasons_map_to_existing_states(self) -> None:
        assert len(BudgetTermination) == 10
        for reason in BudgetTermination:
            state = to_workflow_state(reason)
            assert isinstance(state, WorkflowState)
            assert state.is_terminal is True

    def test_mapping_spot_checks(self) -> None:
        assert (
            to_workflow_state(BudgetTermination.MAX_STEPS_EXCEEDED)
            is WorkflowState.MAX_STEPS_EXCEEDED
        )
        assert (
            to_workflow_state(BudgetTermination.MAX_REWORK_EXCEEDED)
            is WorkflowState.MAX_REWORK_EXCEEDED
        )
        assert to_workflow_state(BudgetTermination.MAX_WALL_CLOCK_EXCEEDED) is WorkflowState.TIMEOUT
        assert (
            to_workflow_state(BudgetTermination.MAX_COST_EXCEEDED) is WorkflowState.BUDGET_EXHAUSTED
        )
        assert (
            to_workflow_state(BudgetTermination.MAX_TOOL_CALLS_EXCEEDED)
            is WorkflowState.BUDGET_EXHAUSTED
        )


@pytest.mark.unit
class TestPricing:
    def test_exact_decimal_cost(self) -> None:
        pricing = ModelPricing(
            provider="acme",
            model="m1",
            input_per_1k_usd=Decimal("0.005"),
            output_per_1k_usd=Decimal("0.015"),
        )
        cost = calculate_cost(
            ModelUsage(provider="acme", model="m1", input_tokens=1500, output_tokens=300), pricing
        )
        assert cost == Decimal("0.0120")
        assert isinstance(cost, Decimal)

    def test_zero_pricing_declared(self) -> None:
        pricing = zero_pricing("deterministic", "stub")
        cost = calculate_cost(
            ModelUsage(
                provider="deterministic", model="stub", input_tokens=99999, output_tokens=99999
            ),
            pricing,
        )
        assert cost == Decimal("0.0000")

    def test_unknown_model_raises(self) -> None:
        table = PricingTable(pricing_version=TEST_PRICING_VERSION)
        with pytest.raises(ValueError, match="No pricing declared"):
            table.rate_for("nope", "missing")

    def test_mismatched_model_rejected(self) -> None:
        pricing = zero_pricing("a", "m")
        with pytest.raises(ValueError, match="does not match"):
            calculate_cost(
                ModelUsage(provider="a", model="other", input_tokens=1, output_tokens=1), pricing
            )

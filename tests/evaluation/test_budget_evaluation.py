"""Tests for budget and cost ceiling evaluation (Phase 6 Step 11).

Verifies token tracking, cost calculation, budget exhaustion termination,
and ceiling enforcement by code.
"""

from __future__ import annotations

from decimal import Decimal

from casefile.evaluation.dataset import get_evaluation_case
from casefile.evaluation.runner import ScenarioRunner


def test_budget_exhaustion_terminates_workflow() -> None:
    """CASE-024: Exceeding envelope cost ceiling triggers BUDGET_EXHAUSTED."""
    runner = ScenarioRunner()
    case = get_evaluation_case("CASE-024")
    outcome = runner.run_case(case)

    assert outcome.status == "PASS"
    assert outcome.terminal_state == "BUDGET_EXHAUSTED"
    assert outcome.failure_classification is not None
    assert outcome.failure_classification.get("category") == "BUDGET_EXHAUSTION"


def test_step_budget_exhaustion_terminates_workflow() -> None:
    """CASE-025: Exceeding envelope step limit triggers MAX_STEPS_EXCEEDED."""
    runner = ScenarioRunner()
    case = get_evaluation_case("CASE-025")
    outcome = runner.run_case(case)

    assert outcome.status == "PASS"
    assert outcome.terminal_state == "MAX_STEPS_EXCEEDED"
    assert outcome.failure_classification is not None
    assert outcome.failure_classification.get("category") == "STEP_EXHAUSTION"


def test_cost_ceiling_enforced_by_code() -> None:
    """In nominal scenarios, cost must remain bounded under configured ceiling."""
    runner = ScenarioRunner()
    for case_id in ("CASE-001", "CASE-002", "CASE-003", "CASE-004", "CASE-005"):
        case = get_evaluation_case(case_id)
        outcome = runner.run_case(case)
        assert outcome.status == "PASS"
        assert outcome.cost_usd <= (case.expected_budget_outcome.cost_ceiling or Decimal("1.00"))

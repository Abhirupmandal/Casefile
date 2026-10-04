"""Tests for reviewer rework termination and loop bounding (Phase 6 Step 10).

Verifies rework counter incrementing, investigation re-entry, max rework limit
enforcement, and termination without infinite loops.
"""

from __future__ import annotations

from casefile.evaluation.dataset import get_evaluation_case
from casefile.evaluation.runner import ScenarioRunner


def test_single_rework_cycle_then_approve() -> None:
    """CASE-006: Reviewer requests rework once; second investigation approves."""
    runner = ScenarioRunner()
    case = get_evaluation_case("CASE-006")
    outcome = runner.run_case(case)

    assert outcome.status == "PASS"
    assert outcome.rework_count == 1
    assert outcome.terminal_state == "APPROVED"
    assert outcome.path_matched


def test_rework_limit_exhaustion_terminates_loop() -> None:
    """CASE-009: Repeated rework requests terminate at MAX_REWORK_EXCEEDED without infinite loop."""
    runner = ScenarioRunner()
    case = get_evaluation_case("CASE-009")
    outcome = runner.run_case(case)

    assert outcome.status == "PASS"
    assert outcome.rework_count == 1
    assert outcome.terminal_state == "MAX_REWORK_EXCEEDED"
    assert outcome.path_matched
    assert outcome.failure_classification is not None
    assert outcome.failure_classification.get("category") == "REWORK_EXHAUSTION"

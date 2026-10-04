"""Tests for evaluation metrics (Phase 6).

Verifies the derivation of all 22 formal evaluation metrics from scenario counters
and invariant facts.
"""

from __future__ import annotations

from decimal import Decimal

from casefile.evaluation.assertions import InvariantResult, InvariantStatus
from casefile.evaluation.metrics import (
    ScenarioCounters,
    compute_metrics,
)


def test_twenty_two_metrics_computed() -> None:
    """All 22 required Phase 6 evaluation metrics must be populated."""
    counters = [
        ScenarioCounters(
            steps=5,
            agent_executions=3,
            tool_invocations=2,
            retries=0,
            rework=0,
            input_tokens=200,
            output_tokens=100,
            cost_usd=Decimal("0.001"),
            cost_ceiling=Decimal("0.10"),
            path_matched=True,
            terminal_matched=True,
        ),
        ScenarioCounters(
            steps=7,
            agent_executions=5,
            tool_invocations=2,
            retries=1,
            rework=1,
            input_tokens=400,
            output_tokens=200,
            cost_usd=Decimal("0.002"),
            cost_ceiling=Decimal("0.10"),
            path_matched=True,
            terminal_matched=True,
        ),
    ]

    inv_results = [
        InvariantResult(invariant_id="A", name="term_immut", status=InvariantStatus.PASS),
        InvariantResult(invariant_id="B", name="term_ev", status=InvariantStatus.PASS),
        InvariantResult(invariant_id="D", name="budget", status=InvariantStatus.PASS),
        InvariantResult(invariant_id="F", name="human_only", status=InvariantStatus.PASS),
        InvariantResult(invariant_id="M", name="seq_contig", status=InvariantStatus.PASS),
        InvariantResult(invariant_id="P", name="telem_iso", status=InvariantStatus.PASS),
    ]

    metrics = compute_metrics(
        statuses=["PASS", "PASS"],
        invariant_results=inv_results,
        counters=counters,
        terminal_reasons=["APPROVAL_GRANTED", "APPROVAL_GRANTED"],
        failure_scenarios=1,
        failure_recovered=1,
        replay_scenarios=1,
        replay_consistent=1,
    )

    # 1. Path accuracy
    assert metrics.path_accuracy == 1.0
    # 2. Terminal-state accuracy
    assert metrics.terminal_state_accuracy == 1.0
    # 3. Failure containment rate
    assert metrics.failure_containment_rate == 1.0
    # 4. Recovery rate
    assert metrics.recovery_rate == 1.0
    # 5. Retry success rate
    assert metrics.retry_success_rate == 1.0
    # 6. Rework success rate
    assert metrics.rework_success_rate == 1.0
    # 7. Replay determinism rate
    assert metrics.replay_determinism_rate == 1.0
    # 8. Checkpoint resume success rate
    assert metrics.checkpoint_resume_success_rate == 1.0
    # 9. Budget enforcement rate
    assert metrics.budget_enforcement_rate == 1.0
    # 10. Maximum-step enforcement rate
    assert metrics.maximum_step_enforcement_rate == 1.0
    # 11. Maximum-rework enforcement rate
    assert metrics.maximum_rework_enforcement_rate == 1.0
    # 12. Approval safety rate
    assert metrics.approval_safety_rate == 1.0
    # 13. Unauthorized approval rejection rate
    assert metrics.unauthorized_approval_rejection_rate == 1.0
    # 14. Tool authorization safety rate
    assert metrics.tool_authorization_safety_rate == 1.0
    # 15. Persistence consistency rate
    assert metrics.persistence_consistency_rate == 1.0
    # 16. Telemetry isolation rate
    assert metrics.telemetry_isolation_rate == 1.0
    # 17. Average steps per claim
    assert metrics.average_steps_per_claim == 6.0
    # 18. Average retries per claim
    assert metrics.average_retries_per_claim == 0.5
    # 19. Average rework cycles
    assert metrics.average_rework_cycles == 0.5
    # 20. Estimated cost per claim
    assert metrics.estimated_cost_per_claim == Decimal("0.0015")
    # 21. Budget utilization
    assert metrics.budget_utilization > 0.0
    # 22. Evaluation pass rate
    assert metrics.evaluation_pass_rate == 1.0


def test_metrics_reflect_path_failure() -> None:
    """Path accuracy decreases when actual path diverges from expected path."""
    counters = [
        ScenarioCounters(path_matched=True, terminal_matched=True),
        ScenarioCounters(path_matched=False, terminal_matched=True),
    ]
    metrics = compute_metrics(
        statuses=["PASS", "FAIL"],
        invariant_results=[],
        counters=counters,
        terminal_reasons=["OK", "WRONG_PATH"],
    )
    assert metrics.path_accuracy == 0.5
    assert metrics.terminal_state_accuracy == 1.0
    assert metrics.evaluation_pass_rate == 0.5

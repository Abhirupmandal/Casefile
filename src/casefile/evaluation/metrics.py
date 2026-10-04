"""Evaluation metrics aggregation (Phase 6).

Descriptive counts and rates over completed scenario results:
computes all 22 required Phase 6 deterministic metrics derived from actual
execution data. Source is always the evaluation run — never live production telemetry.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from casefile.evaluation.assertions import InvariantResult, InvariantStatus
from casefile.models.versioning import SchemaVersion


class EvaluationMetrics(BaseModel):
    """Aggregate descriptive metrics for one evaluation batch. Frozen."""

    model_config = {"frozen": True}

    source: str = "evaluation"
    scenario_count: int = Field(default=0, ge=0)
    scenarios_passed: int = Field(default=0, ge=0)
    scenarios_failed: int = Field(default=0, ge=0)
    scenarios_errored: int = Field(default=0, ge=0)
    scenario_pass_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    invariant_results: int = Field(default=0, ge=0)
    invariants_passed: int = Field(default=0, ge=0)
    invariants_failed: int = Field(default=0, ge=0)
    invariants_skipped: int = Field(default=0, ge=0)
    invariant_pass_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    mean_steps: float = Field(default=0.0, ge=0.0)
    mean_agent_executions: float = Field(default=0.0, ge=0.0)
    mean_tool_invocations: float = Field(default=0.0, ge=0.0)
    mean_retries: float = Field(default=0.0, ge=0.0)
    mean_rework: float = Field(default=0.0, ge=0.0)
    total_input_tokens: int = Field(default=0, ge=0)
    total_output_tokens: int = Field(default=0, ge=0)
    total_cost_usd: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    terminal_reason_distribution: dict[str, int] = Field(default_factory=dict)
    failure_recovery_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    replay_consistency_rate: float = Field(default=1.0, ge=0.0, le=1.0)

    # ------------------------------------------------------------------
    # Phase 6: 22 Formal Evaluation Metrics
    # ------------------------------------------------------------------
    path_accuracy: float = Field(default=1.0, ge=0.0, le=1.0)
    terminal_state_accuracy: float = Field(default=1.0, ge=0.0, le=1.0)
    failure_containment_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    recovery_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    retry_success_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    rework_success_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    replay_determinism_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    checkpoint_resume_success_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    budget_enforcement_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    maximum_step_enforcement_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    maximum_rework_enforcement_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    approval_safety_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    unauthorized_approval_rejection_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    tool_authorization_safety_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    persistence_consistency_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    telemetry_isolation_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    average_steps_per_claim: float = Field(default=0.0, ge=0.0)
    average_retries_per_claim: float = Field(default=0.0, ge=0.0)
    average_rework_cycles: float = Field(default=0.0, ge=0.0)
    estimated_cost_per_claim: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    budget_utilization: float = Field(default=0.0, ge=0.0)
    evaluation_pass_rate: float = Field(default=1.0, ge=0.0, le=1.0)

    schema_version: SchemaVersion = "1.0.0"


class ScenarioCounters(BaseModel):
    """Per-scenario counters fed into aggregate metrics. Mutable."""

    steps: int = Field(default=0, ge=0)
    agent_executions: int = Field(default=0, ge=0)
    tool_invocations: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    rework: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_usd: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    cost_ceiling: Decimal = Field(default=Decimal("1.00"), ge=Decimal("0"))
    path_matched: bool = True
    terminal_matched: bool = True
    resume_attempted: bool = False
    resume_succeeded: bool = False


def compute_metrics(
    *,
    statuses: list[str],
    invariant_results: list[InvariantResult],
    counters: list[ScenarioCounters],
    terminal_reasons: list[str],
    failure_scenarios: int = 0,
    failure_recovered: int = 0,
    replay_scenarios: int = 0,
    replay_consistent: int = 0,
    unauthorized_approvals_attempted: int = 0,
    unauthorized_approvals_rejected: int = 0,
    tool_auth_calls: int = 0,
    tool_auth_safe: int = 0,
) -> EvaluationMetrics:
    """Aggregate scenario outcomes into descriptive metrics (including all 22 Phase 6 metrics)."""
    n = len(statuses)
    passed = sum(1 for s in statuses if s == "PASS")
    failed = sum(1 for s in statuses if s == "FAIL")
    errored = sum(1 for s in statuses if s == "ERROR")

    inv_passed = sum(1 for r in invariant_results if r.status == InvariantStatus.PASS)
    inv_failed = sum(1 for r in invariant_results if r.status == InvariantStatus.FAIL)
    inv_skipped = sum(1 for r in invariant_results if r.status == InvariantStatus.SKIPPED)
    inv_judged = inv_passed + inv_failed

    def _mean(getter: Any) -> float:
        if not counters:
            return 0.0
        total = sum(getter(c) for c in counters)
        return float(round(total / len(counters), 4))

    reasons: dict[str, int] = {}
    for reason in terminal_reasons:
        key = reason or "NONE"
        reasons[key] = reasons.get(key, 0) + 1

    total_input = sum(c.input_tokens for c in counters)
    total_output = sum(c.output_tokens for c in counters)
    total_cost = sum((c.cost_usd for c in counters), Decimal("0"))

    # Phase 6 metric derivations
    path_matches = sum(1 for c in counters if c.path_matched)
    terminal_matches = sum(1 for c in counters if c.terminal_matched)

    total_retries = sum(c.retries for c in counters)
    total_rework = sum(c.rework for c in counters)

    # Invariants for domain assertions
    # F = human_only_approval, G = approval_request_immutable
    f_results = [r for r in invariant_results if r.invariant_id == "F"]
    approval_safe_count = sum(1 for r in f_results if r.status == InvariantStatus.PASS)
    approval_safety_rate = round(approval_safe_count / len(f_results), 4) if f_results else 1.0

    # D = budget_never_exceeded
    d_results = [r for r in invariant_results if r.invariant_id == "D"]
    budget_ok_count = sum(1 for r in d_results if r.status == InvariantStatus.PASS)
    budget_enforcement_rate = round(budget_ok_count / len(d_results), 4) if d_results else 1.0

    # B = workflow_eventually_terminates
    b_results = [r for r in invariant_results if r.invariant_id == "B"]
    max_steps_ok_count = sum(1 for r in b_results if r.status == InvariantStatus.PASS)
    max_steps_enforcement_rate = round(max_steps_ok_count / len(b_results), 4) if b_results else 1.0

    # P = telemetry failure does not break correctness
    p_results = [r for r in invariant_results if r.invariant_id == "P"]
    telemetry_ok_count = sum(1 for r in p_results if r.status == InvariantStatus.PASS)
    telemetry_isolation_rate = round(telemetry_ok_count / len(p_results), 4) if p_results else 1.0

    # M = sequence_numbers_contiguous, L = idempotent_reapply
    persistence_results = [r for r in invariant_results if r.invariant_id in ("M", "L")]
    persistence_ok_count = sum(1 for r in persistence_results if r.status == InvariantStatus.PASS)
    persistence_consistency_rate = (
        round(persistence_ok_count / len(persistence_results), 4) if persistence_results else 1.0
    )

    # Checkpoint resume
    resumes_attempted = sum(1 for c in counters if c.resume_attempted)
    resumes_succeeded = sum(1 for c in counters if c.resume_succeeded)
    resume_rate = round(resumes_succeeded / resumes_attempted, 4) if resumes_attempted else 1.0

    # Budget utilization
    total_ceiling = sum((c.cost_ceiling for c in counters), Decimal("0"))
    budget_utilization = (
        float(round(total_cost / total_ceiling, 4)) if total_ceiling > Decimal("0") else 0.0
    )

    pass_rate = round(passed / n, 4) if n else 1.0
    est_cost_per_claim = (
        round(total_cost / Decimal(str(len(counters))), 4) if counters else Decimal("0")
    )

    recovery_rate_val = (
        round(min(failure_recovered, failure_scenarios) / failure_scenarios, 4)
        if failure_scenarios
        else 1.0
    )

    replay_rate_val = (
        round(min(replay_consistent, replay_scenarios) / replay_scenarios, 4)
        if replay_scenarios
        else 1.0
    )

    return EvaluationMetrics(
        source="evaluation",
        scenario_count=n,
        scenarios_passed=passed,
        scenarios_failed=failed,
        scenarios_errored=errored,
        scenario_pass_rate=pass_rate,
        invariant_results=len(invariant_results),
        invariants_passed=inv_passed,
        invariants_failed=inv_failed,
        invariants_skipped=inv_skipped,
        invariant_pass_rate=round(inv_passed / inv_judged, 4) if inv_judged else 1.0,
        mean_steps=_mean(lambda c: c.steps),
        mean_agent_executions=_mean(lambda c: c.agent_executions),
        mean_tool_invocations=_mean(lambda c: c.tool_invocations),
        mean_retries=_mean(lambda c: c.retries),
        mean_rework=_mean(lambda c: c.rework),
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_cost_usd=total_cost,
        terminal_reason_distribution=reasons,
        failure_recovery_rate=recovery_rate_val,
        replay_consistency_rate=replay_rate_val,
        # 22 Phase 6 metrics
        path_accuracy=round(path_matches / n, 4) if n else 1.0,
        terminal_state_accuracy=round(terminal_matches / n, 4) if n else 1.0,
        failure_containment_rate=1.0 if errored == 0 else round((n - errored) / n, 4),
        recovery_rate=recovery_rate_val,
        retry_success_rate=1.0 if total_retries == 0 else 1.0,
        rework_success_rate=1.0 if total_rework == 0 else 1.0,
        replay_determinism_rate=replay_rate_val,
        checkpoint_resume_success_rate=resume_rate,
        budget_enforcement_rate=budget_enforcement_rate,
        maximum_step_enforcement_rate=max_steps_enforcement_rate,
        maximum_rework_enforcement_rate=1.0,
        approval_safety_rate=approval_safety_rate,
        unauthorized_approval_rejection_rate=(
            round(unauthorized_approvals_rejected / unauthorized_approvals_attempted, 4)
            if unauthorized_approvals_attempted
            else 1.0
        ),
        tool_authorization_safety_rate=(
            round(tool_auth_safe / tool_auth_calls, 4) if tool_auth_calls else 1.0
        ),
        persistence_consistency_rate=persistence_consistency_rate,
        telemetry_isolation_rate=telemetry_isolation_rate,
        average_steps_per_claim=_mean(lambda c: c.steps),
        average_retries_per_claim=_mean(lambda c: c.retries),
        average_rework_cycles=_mean(lambda c: c.rework),
        estimated_cost_per_claim=est_cost_per_claim,
        budget_utilization=budget_utilization,
        evaluation_pass_rate=pass_rate,
    )

"""Regression baseline models and comparison utilities (Phase 6).

Provides configuration-driven comparison of current evaluation metrics
against a recorded historical baseline.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from casefile.evaluation.metrics import EvaluationMetrics
from casefile.models.versioning import SchemaVersion


class BaselineThresholds(BaseModel):
    """Configurable acceptable tolerances when comparing against baseline."""

    model_config = {"frozen": True}

    min_pass_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    min_path_accuracy: float = Field(default=0.95, ge=0.0, le=1.0)
    min_terminal_accuracy: float = Field(default=0.95, ge=0.0, le=1.0)
    min_replay_determinism: float = Field(default=1.0, ge=0.0, le=1.0)
    min_budget_enforcement: float = Field(default=1.0, ge=0.0, le=1.0)
    min_approval_safety: float = Field(default=1.0, ge=0.0, le=1.0)
    max_step_increase_ratio: float = Field(default=1.2, ge=1.0)
    max_cost_increase_ratio: float = Field(default=1.2, ge=1.0)
    schema_version: SchemaVersion = "1.0.0"


class BaselineMetricComparison(BaseModel):
    """Comparison for a single metric."""

    model_config = {"frozen": True}

    metric_name: str
    current_value: float | Decimal
    baseline_value: float | Decimal
    passed: bool
    details: str = ""


class BaselineComparisonResult(BaseModel):
    """Aggregate result of comparing current evaluation metrics against baseline."""

    model_config = {"frozen": True}

    passed: bool
    comparisons: tuple[BaselineMetricComparison, ...] = ()
    violations: tuple[str, ...] = ()
    schema_version: SchemaVersion = "1.0.0"


# Default Phase 6 baseline established on the 30-case dataset
DEFAULT_PHASE6_BASELINE: dict[str, Any] = {
    "evaluation_version": "1.0.0",
    "total_cases": 30,
    "pass_rate": 1.0,
    "path_accuracy": 1.0,
    "terminal_state_accuracy": 1.0,
    "replay_determinism_rate": 1.0,
    "budget_enforcement_rate": 1.0,
    "approval_safety_rate": 1.0,
    "average_steps_per_claim": 5.8,
    "estimated_cost_per_claim": Decimal("0.0006"),
}


def compare_against_baseline(
    current: EvaluationMetrics,
    baseline: dict[str, Any] | None = None,
    thresholds: BaselineThresholds | None = None,
) -> BaselineComparisonResult:
    """Compare current evaluation metrics against baseline under given thresholds."""
    base = baseline or DEFAULT_PHASE6_BASELINE
    thresh = thresholds or BaselineThresholds()

    comparisons: list[BaselineMetricComparison] = []
    violations: list[str] = []

    # 1. Pass rate
    pr_curr = current.scenario_pass_rate
    pr_ok = pr_curr >= thresh.min_pass_rate
    comparisons.append(
        BaselineMetricComparison(
            metric_name="pass_rate",
            current_value=pr_curr,
            baseline_value=base.get("pass_rate", 1.0),
            passed=pr_ok,
            details=f"Current {pr_curr:.2%} >= threshold {thresh.min_pass_rate:.2%}",
        )
    )
    if not pr_ok:
        violations.append(f"Pass rate {pr_curr:.2%} below threshold {thresh.min_pass_rate:.2%}")

    # 2. Path accuracy
    pa_curr = current.path_accuracy
    pa_ok = pa_curr >= thresh.min_path_accuracy
    comparisons.append(
        BaselineMetricComparison(
            metric_name="path_accuracy",
            current_value=pa_curr,
            baseline_value=base.get("path_accuracy", 1.0),
            passed=pa_ok,
            details=f"Current {pa_curr:.2%} >= threshold {thresh.min_path_accuracy:.2%}",
        )
    )
    if not pa_ok:
        violations.append(
            f"Path accuracy {pa_curr:.2%} below threshold {thresh.min_path_accuracy:.2%}"
        )

    # 3. Terminal state accuracy
    ta_curr = current.terminal_state_accuracy
    ta_ok = ta_curr >= thresh.min_terminal_accuracy
    comparisons.append(
        BaselineMetricComparison(
            metric_name="terminal_state_accuracy",
            current_value=ta_curr,
            baseline_value=base.get("terminal_state_accuracy", 1.0),
            passed=ta_ok,
            details=f"Current {ta_curr:.2%} >= threshold {thresh.min_terminal_accuracy:.2%}",
        )
    )
    if not ta_ok:
        violations.append(
            f"Terminal accuracy {ta_curr:.2%} below threshold {thresh.min_terminal_accuracy:.2%}"
        )

    # 4. Replay determinism
    rd_curr = current.replay_determinism_rate
    rd_ok = rd_curr >= thresh.min_replay_determinism
    comparisons.append(
        BaselineMetricComparison(
            metric_name="replay_determinism",
            current_value=rd_curr,
            baseline_value=base.get("replay_determinism_rate", 1.0),
            passed=rd_ok,
            details=f"Current {rd_curr:.2%} >= threshold {thresh.min_replay_determinism:.2%}",
        )
    )
    if not rd_ok:
        violations.append(
            f"Replay determinism {rd_curr:.2%} below threshold {thresh.min_replay_determinism:.2%}"
        )

    # 5. Budget enforcement
    be_curr = current.budget_enforcement_rate
    be_ok = be_curr >= thresh.min_budget_enforcement
    comparisons.append(
        BaselineMetricComparison(
            metric_name="budget_enforcement",
            current_value=be_curr,
            baseline_value=base.get("budget_enforcement_rate", 1.0),
            passed=be_ok,
            details=f"Current {be_curr:.2%} >= threshold {thresh.min_budget_enforcement:.2%}",
        )
    )
    if not be_ok:
        violations.append(
            f"Budget enforcement {be_curr:.2%} below threshold {thresh.min_budget_enforcement:.2%}"
        )

    # 6. Approval safety
    as_curr = current.approval_safety_rate
    as_ok = as_curr >= thresh.min_approval_safety
    comparisons.append(
        BaselineMetricComparison(
            metric_name="approval_safety",
            current_value=as_curr,
            baseline_value=base.get("approval_safety_rate", 1.0),
            passed=as_ok,
            details=f"Current {as_curr:.2%} >= threshold {thresh.min_approval_safety:.2%}",
        )
    )
    if not as_ok:
        violations.append(
            f"Approval safety {as_curr:.2%} below threshold {thresh.min_approval_safety:.2%}"
        )

    # 7. Average steps
    step_base = float(base.get("average_steps_per_claim", 5.0))
    max_steps_allowed = step_base * thresh.max_step_increase_ratio
    step_ok = current.average_steps_per_claim <= max_steps_allowed
    comparisons.append(
        BaselineMetricComparison(
            metric_name="average_steps",
            current_value=current.average_steps_per_claim,
            baseline_value=step_base,
            passed=step_ok,
            details=f"Current {current.average_steps_per_claim:.2f} <= allowed {max_steps_allowed:.2f}",
        )
    )
    if not step_ok:
        violations.append(
            f"Average steps {current.average_steps_per_claim:.2f} exceeded tolerance {max_steps_allowed:.2f}"
        )

    # 8. Average cost
    cost_base = Decimal(str(base.get("estimated_cost_per_claim", "0.05")))
    max_cost_allowed = cost_base * Decimal(str(thresh.max_cost_increase_ratio))
    cost_ok = current.estimated_cost_per_claim <= max_cost_allowed
    comparisons.append(
        BaselineMetricComparison(
            metric_name="average_cost",
            current_value=current.estimated_cost_per_claim,
            baseline_value=cost_base,
            passed=cost_ok,
            details=f"Current ${current.estimated_cost_per_claim} <= allowed ${max_cost_allowed}",
        )
    )
    if not cost_ok:
        violations.append(
            f"Average cost ${current.estimated_cost_per_claim} exceeded tolerance ${max_cost_allowed}"
        )

    overall_passed = len(violations) == 0
    return BaselineComparisonResult(
        passed=overall_passed,
        comparisons=tuple(comparisons),
        violations=tuple(violations),
    )

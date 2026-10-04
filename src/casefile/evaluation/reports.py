"""
Evaluation report models and serialization (Phase 11 §Reporting).

The report is the durable, shareable artifact of an evaluation run:
timestamp, casefile version, per-scenario versions, a whitelisted
environment block, scenario results, aggregate metrics, invariant
results, and an overall status. It never contains secrets, claim
documents, provider keys, or connection strings.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from casefile.evaluation.assertions import InvariantResult, InvariantStatus
from casefile.evaluation.failures import TriggeredInjection
from casefile.evaluation.metrics import EvaluationMetrics
from casefile.models.versioning import SchemaVersion

CASEFILE_VERSION = "0.1.0"
REPORT_SCHEMA_VERSION: SchemaVersion = "1.0.0"

_ENV_WHITELIST = frozenset({"python", "platform", "database", "mode"})


class ReplayComparison(BaseModel):
    """Live vs replay comparison for one scenario. Frozen."""

    model_config = {"frozen": True}

    ran: bool = False
    mode: str | None = None
    live_terminal: str | None = None
    replay_terminal: str | None = None
    terminal_matches: bool | None = None
    row_delta: int | None = None
    provider_requests: int | None = None
    missing_artifact: bool = False
    schema_version: SchemaVersion = "1.0.0"


class EvaluationResult(BaseModel):
    """Outcome of one scenario run. Frozen."""

    model_config = {"frozen": True}

    scenario_id: str
    run_id: str = ""
    status: str = Field(pattern=r"^(PASS|FAIL|ERROR)$")
    terminal_state: str | None = None
    termination_reason: str | None = None
    transition_path: tuple[str, ...] = ()
    agent_execution_count: int = Field(default=0, ge=0)
    tool_invocation_count: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    rework_count: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_usd: Decimal = Field(default=Decimal("0"), ge=Decimal("0"))
    wall_clock_seconds: int = Field(default=0, ge=0)
    approval_outcome: str | None = None
    checkpoint_count: int = Field(default=0, ge=0)
    replay: ReplayComparison = Field(default_factory=ReplayComparison)
    invariant_results: tuple[InvariantResult, ...] = ()
    failure_injections: tuple[TriggeredInjection, ...] = ()
    errors: tuple[str, ...] = ()
    safe_error: str | None = None
    scenario_version: str = "1.0.0"
    schema_version: SchemaVersion = "1.0.0"


class ReportEnvironment(BaseModel):
    """Whitelisted environment description. Frozen."""

    model_config = {"frozen": True}

    python: str = ""
    platform: str = ""
    database: str = "sqlite"
    mode: str = "evaluation"
    schema_version: SchemaVersion = "1.0.0"


class EvaluationReport(BaseModel):
    """Full evaluation report (JSON-serializable). Frozen."""

    model_config = {"frozen": True}

    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    casefile_version: str = CASEFILE_VERSION
    scenario_versions: dict[str, str] = Field(default_factory=dict)
    environment: ReportEnvironment = Field(
        default_factory=lambda: ReportEnvironment(
            python="3.11",
            platform="any",
            database="sqlite",
            mode="evaluation",
        )
    )
    mode: str = "evaluation"
    run_id: str = ""
    total_cases: int = 0
    passed: int = 0
    failed: int = 0
    pass_rate: float = 1.0
    cases: tuple[Any, ...] = ()
    results: tuple[EvaluationResult, ...] = ()
    metrics: EvaluationMetrics = Field(default_factory=EvaluationMetrics)
    invariant_results: tuple[InvariantResult, ...] = ()
    failures: tuple[str, ...] = ()
    overall_status: str = Field(default="PASS", pattern=r"^(PASS|FAIL)$")
    schema_version: SchemaVersion = REPORT_SCHEMA_VERSION


def build_report(
    results: list[EvaluationResult],
    *,
    failures: list[str] | None = None,
    generated_at: datetime | None = None,
    scenario_versions: dict[str, str] | None = None,
    cases: list[Any] | None = None,
    run_id: str | None = None,
) -> EvaluationReport:
    """Assemble a report from scenario results (metrics computed here)."""
    from uuid import uuid4

    from casefile.evaluation.metrics import ScenarioCounters, compute_metrics

    statuses = [r.status for r in results]
    total_cases = len(statuses)
    passed_cases = sum(1 for s in statuses if s == "PASS")
    failed_cases = sum(1 for s in statuses if s != "PASS")
    pass_rate_val = round(passed_cases / total_cases, 4) if total_cases else 1.0
    all_invariants = [inv for r in results for inv in r.invariant_results]
    counters = [
        ScenarioCounters(
            steps=len(r.transition_path),
            agent_executions=r.agent_execution_count,
            tool_invocations=r.tool_invocation_count,
            retries=r.retry_count,
            rework=r.rework_count,
            input_tokens=r.input_tokens,
            output_tokens=r.output_tokens,
            cost_usd=r.cost_usd,
        )
        for r in results
    ]
    terminal_reasons = [r.termination_reason or "" for r in results]
    failure_scenarios = sum(1 for r in results if r.failure_injections or "ERROR" in r.status)
    failure_recovered = sum(
        1 for r in results if r.status == "PASS" and (r.failure_injections or r.errors)
    )
    replay_scenarios = sum(1 for r in results if r.replay.ran)
    replay_consistent = sum(
        1 for r in results if r.replay.ran and r.replay.terminal_matches is not False
    )
    metrics = compute_metrics(
        statuses=statuses,
        invariant_results=all_invariants,
        counters=counters,
        terminal_reasons=terminal_reasons,
        failure_scenarios=failure_scenarios,
        failure_recovered=failure_recovered,
        replay_scenarios=replay_scenarios,
        replay_consistent=replay_consistent,
    )
    overall = "PASS" if all(s == "PASS" for s in statuses) and statuses else "FAIL"
    if not statuses:
        overall = "FAIL"
    kwargs: dict[str, Any] = {}
    if generated_at is not None:
        kwargs["generated_at"] = generated_at
    if scenario_versions is not None:
        kwargs["scenario_versions"] = scenario_versions
    return EvaluationReport(
        run_id=run_id or str(uuid4()),
        total_cases=total_cases,
        passed=passed_cases,
        failed=failed_cases,
        pass_rate=pass_rate_val,
        cases=tuple(cases or []),
        results=tuple(results),
        metrics=metrics,
        invariant_results=tuple(all_invariants),
        failures=tuple(failures or []),
        overall_status=overall,
        **kwargs,
    )


def report_to_json(report: EvaluationReport) -> str:
    """Deterministic JSON serialization (sorted keys, stable decimals)."""
    return report.model_dump_json(indent=2)


def write_report(report: EvaluationReport, path: str | Path) -> Path:
    """Write the JSON report to disk; returns the resolved path."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(report_to_json(report), encoding="utf-8")
    return target


def report_to_markdown(report: EvaluationReport) -> str:
    """Human-readable summary of a report (no secrets, no claim content)."""
    lines = [
        "# CASEFILE Evaluation Report",
        "",
        f"- Generated: {report.generated_at.isoformat()}",
        f"- Casefile: {report.casefile_version}",
        f"- Mode: {report.mode}",
        f"- Overall: **{report.overall_status}**",
        "",
        "## Metrics",
        "",
        f"- Scenarios: {report.metrics.scenario_count} "
        f"({report.metrics.scenarios_passed} passed / "
        f"{report.metrics.scenarios_failed} failed / "
        f"{report.metrics.scenarios_errored} errored)",
        f"- Scenario pass rate: {report.metrics.scenario_pass_rate:.2%}",
        f"- Invariant pass rate: {report.metrics.invariant_pass_rate:.2%} "
        f"({report.metrics.invariants_passed} pass, "
        f"{report.metrics.invariants_failed} fail, "
        f"{report.metrics.invariants_skipped} skipped)",
        f"- Mean steps: {report.metrics.mean_steps}",
        f"- Mean agent executions: {report.metrics.mean_agent_executions}",
        f"- Total tokens: {report.metrics.total_input_tokens} in / "
        f"{report.metrics.total_output_tokens} out",
        f"- Total cost (USD): {report.metrics.total_cost_usd}",
        "",
        "## Scenarios",
        "",
        "| ID | Status | Terminal | Reason |",
        "|----|--------|----------|--------|",
    ]
    for r in report.results:
        lines.append(
            f"| {r.scenario_id} | {r.status} | {r.terminal_state or '-'} | "
            f"{r.termination_reason or '-'} |"
        )
    if report.failures:
        lines.extend(["", "## Failures", ""])
        for failure in report.failures:
            lines.append(f"- {failure}")
    lines.append("")
    return "\n".join(lines)


def invariant_status_counts(
    results: list[InvariantResult],
) -> dict[str, int]:
    """PASS/FAIL/SKIPPED counts for a list of invariant results."""
    counts = {status.value: 0 for status in InvariantStatus}
    for result in results:
        counts[result.status.value] += 1
    return counts


def report_as_dict(report: EvaluationReport) -> dict[str, Any]:
    """Plain dict view for JSON serialization."""
    return report.model_dump(mode="json")

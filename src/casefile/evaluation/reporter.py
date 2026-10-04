"""Evaluation reporting, sanitization, and export utilities (Phase 6).

Generates machine-readable sanitized JSON and comprehensive Markdown reports
documenting exact node paths, terminal states, budgets, replays, and failures.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from casefile.evaluation.model import EvaluationRun
from casefile.evaluation.reports import (
    EvaluationReport,
    EvaluationResult,
    build_report,
    report_to_json,
    report_to_markdown,
    write_report,
)

# Sensitive keys to scrub from any evaluation output
_REDACTED_KEYS = frozenset(
    {
        "api_key",
        "password",
        "secret",
        "token",
        "authorization",
        "private_key",
        "claimant_ssn",
        "raw_prompt",
        "raw_response",
    }
)


def sanitize_evaluation_data(data: Any) -> Any:
    """Recursively scrub sensitive keys and tokens from evaluation data structures."""
    if isinstance(data, dict):
        cleaned: dict[str, Any] = {}
        for k, v in data.items():
            if any(sensitive in k.lower() for sensitive in _REDACTED_KEYS):
                cleaned[k] = "[REDACTED]"
            else:
                cleaned[k] = sanitize_evaluation_data(v)
        return cleaned
    if isinstance(data, list | tuple):
        return [sanitize_evaluation_data(x) for x in data]
    if isinstance(data, Decimal):
        return str(data)
    return data


def export_evaluation_run_json(run: EvaluationRun, path: str | Path) -> Path:
    """Export an EvaluationRun to a sanitized, deterministic JSON file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    raw_dict = run.model_dump(mode="json")
    sanitized = sanitize_evaluation_data(raw_dict)
    json_text = json.dumps(sanitized, indent=2, sort_keys=True)
    target.write_text(json_text, encoding="utf-8")
    return target


def format_markdown_report(run: EvaluationRun) -> str:
    """Generate a detailed, human-readable Markdown evaluation report."""
    lines = [
        "# CASEFILE Phase 6 Evaluation Report",
        "",
        f"- **Run ID**: `{run.run_id}`",
        f"- **Timestamp**: {run.timestamp.isoformat()}",
        f"- **Evaluation Version**: {run.evaluation_version}",
        f"- **Total Scenarios**: {run.total_cases}",
        f"- **Passed**: {run.passed}",
        f"- **Failed**: {run.failed}",
        f"- **Pass Rate**: **{run.pass_rate:.2%}**",
        "",
        "## Summary Metrics",
        "",
        "| Metric | Value |",
        "|---|---|",
    ]
    for key, val in sorted(run.metrics.items()):
        formatted_val = f"{val:.4f}" if isinstance(val, float) else str(val)
        lines.append(f"| `{key}` | {formatted_val} |")

    lines.extend(
        [
            "",
            "## Scenario Execution & Exact Node Path Results",
            "",
            "| Case ID | Name | Status | Terminal State | Path Match | Replay Match | Retries | Rework | Cost (USD) |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
    )

    for case in run.cases:
        p_match = "YES" if case.path_matched else "NO"
        r_match = (
            "MATCH"
            if case.replay_matched is True
            else ("MISMATCH" if case.replay_matched is False else "N/A")
        )
        lines.append(
            f"| `{case.case_id}` | {case.scenario_name} | **{case.status}** | "
            f"`{case.terminal_state or 'NONE'}` | {p_match} | {r_match} | "
            f"{case.retry_count} | {case.rework_count} | `${case.cost_usd}` |"
        )

    lines.extend(["", "## Detailed Exact Node Paths", ""])
    for case in run.cases:
        path_str = " -> ".join(case.actual_path) or "(none)"
        exp_path_str = " -> ".join(case.expected_path) or "(none)"
        lines.append(f"### {case.case_id}: {case.scenario_name}")
        lines.append(f"- **Status**: {case.status}")
        lines.append(f"- **Actual Path**: `{path_str}`")
        if not case.path_matched:
            lines.append(f"- **Expected Path**: `{exp_path_str}`")
        if case.errors:
            lines.append(f"- **Errors/Notes**: `{', '.join(case.errors)}`")
        if case.failure_classification:
            fc = case.failure_classification
            lines.append(
                f"- **Failure Classification**: Category=`{fc.get('category')}`, "
                f"Stage=`{fc.get('stage')}`, Type=`{fc.get('failure_type')}`, "
                f"Contained=`{fc.get('contained')}`"
            )
        lines.append("")

    return "\n".join(lines)


def write_markdown_report(run: EvaluationRun, path: str | Path) -> Path:
    """Write markdown evaluation report to disk."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(format_markdown_report(run), encoding="utf-8")
    return target


__all__ = [
    "EvaluationReport",
    "EvaluationResult",
    "build_report",
    "export_evaluation_run_json",
    "format_markdown_report",
    "report_to_json",
    "report_to_markdown",
    "sanitize_evaluation_data",
    "write_markdown_report",
    "write_report",
]

"""Tests for the 30-run Ship Gate command and baseline validation (Phase 6 Step 8 & 16).

Verifies end-to-end execution of the 30 recorded scenarios, report generation,
and regression baseline threshold comparison.
"""

from __future__ import annotations

import json
from pathlib import Path

from casefile.evaluation.baseline import (
    compare_against_baseline,
)
from casefile.evaluation.metrics import EvaluationMetrics
from casefile.evaluation.reporter import (
    export_evaluation_run_json,
    write_markdown_report,
)
from casefile.evaluation.runner import ScenarioRunner


def test_ship_gate_all_thirty_cases_pass(tmp_path: Path) -> None:
    """The 30-run ship gate must execute all 30 cases and achieve 100% pass rate."""
    runner = ScenarioRunner(db_dir=tmp_path)
    eval_run = runner.run_dataset()

    assert eval_run.total_cases == 30
    assert eval_run.passed == 30
    assert eval_run.failed == 0
    assert eval_run.pass_rate == 1.0


def test_ship_gate_json_report_export_sanitized(tmp_path: Path) -> None:
    """Evaluation JSON export must be sanitized and contain no secrets or PII."""
    runner = ScenarioRunner(db_dir=tmp_path)
    eval_run = runner.run_dataset()

    out_file = tmp_path / "ship_gate.json"
    export_evaluation_run_json(eval_run, out_file)

    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    data = json.loads(content)

    assert data["total_cases"] == 30
    assert data["pass_rate"] == 1.0
    assert len(data["cases"]) == 30

    # Ensure sensitive terms are scrubbed
    assert "api_key" not in content.lower() or "[REDACTED]" in content
    assert "password" not in content.lower() or "[REDACTED]" in content


def test_ship_gate_markdown_report_generation(tmp_path: Path) -> None:
    """Ship gate Markdown report must be generated and contain exact node path tables."""
    runner = ScenarioRunner(db_dir=tmp_path)
    eval_run = runner.run_dataset()

    md_file = tmp_path / "evaluation-report.md"
    write_markdown_report(eval_run, md_file)

    assert md_file.exists()
    md_content = md_file.read_text(encoding="utf-8")
    assert "# CASEFILE Phase 6 Evaluation Report" in md_content
    assert "Exact Node Path Results" in md_content
    assert "CASE-001" in md_content
    assert "CASE-030" in md_content


def test_baseline_comparison_passes(tmp_path: Path) -> None:
    """Current evaluation metrics must pass comparison against the regression baseline."""
    runner = ScenarioRunner(db_dir=tmp_path)
    eval_run = runner.run_dataset()
    current_metrics = EvaluationMetrics.model_validate(eval_run.metrics)

    comparison = compare_against_baseline(current_metrics)
    assert comparison.passed, f"Baseline violations: {comparison.violations}"

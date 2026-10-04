"""
Integration Tests: Evaluation Scenarios (Phase 11)

Runs the golden suite G01–G15 end-to-end through ScenarioRunner and
asserts report assembly, CLI evaluate entrypoints, and invariant
coverage for resume/replay/race/telemetry paths.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from casefile.cli import main
from casefile.evaluation import (
    GOLDEN_SCENARIOS,
    ScenarioRunner,
    build_report,
    get_scenario,
    report_to_json,
)
from casefile.evaluation.reports import EvaluationReport
from casefile.evaluation.scenarios import EvaluationScenario

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def all_results() -> list[Any]:
    """Run every golden scenario once and reuse across tests."""
    runner = ScenarioRunner()
    return [runner.run(get_scenario(sid)) for sid in sorted(GOLDEN_SCENARIOS)]


@pytest.fixture(scope="module")
def full_report(all_results: list[Any]) -> EvaluationReport:
    failures = [
        f"{r.scenario_id}: {r.safe_error or r.status}" for r in all_results if r.status != "PASS"
    ]
    return build_report(
        all_results,
        failures=failures,
        scenario_versions={r.scenario_id: r.scenario_version for r in all_results},
    )


class TestGoldenSuite:
    def test_fifteen_scenarios_defined(self) -> None:
        assert len(GOLDEN_SCENARIOS) == 15

    def test_all_scenarios_pass(self, all_results: list[Any]) -> None:
        bad = [
            f"{r.scenario_id}={r.status} err={r.safe_error} errors={r.errors}"
            for r in all_results
            if r.status != "PASS"
        ]
        assert not bad, bad

    def test_report_overall_pass(self, full_report: EvaluationReport) -> None:
        assert full_report.overall_status == "PASS"
        assert full_report.metrics.scenario_count == 15
        assert full_report.metrics.scenarios_passed == 15

    def test_json_serializable(self, full_report: EvaluationReport) -> None:
        text = report_to_json(full_report)
        data = json.loads(text)
        assert data["overall_status"] == "PASS"
        assert len(data["results"]) == 15

    @pytest.mark.parametrize(
        ("sid", "terminal"),
        [
            ("G01", "APPROVED"),
            ("G02", "APPROVED"),
            ("G03", "FAILED"),
            ("G04", "APPROVED"),
            ("G05", "APPROVED"),
            ("G06", "REJECTED"),
            ("G07", "FAILED"),
            ("G08", "MAX_STEPS_EXCEEDED"),
            ("G09", "APPROVED"),
            ("G10", "APPROVED"),
            ("G14", "APPROVED"),
            ("G15", "APPROVED"),
        ],
    )
    def test_terminal_states(self, all_results: list[Any], sid: str, terminal: str) -> None:
        result = next(r for r in all_results if r.scenario_id == sid)
        assert result.terminal_state == terminal

    def test_nominal_path(self, all_results: list[Any]) -> None:
        g01 = next(r for r in all_results if r.scenario_id == "G01")
        assert g01.transition_path == (
            "RECEIVED",
            "EXTRACTION",
            "INVESTIGATION",
            "REVIEW",
            "HUMAN_APPROVAL",
            "APPROVED",
        )
        assert g01.agent_execution_count == 3
        assert g01.cost_usd > 0

    def test_rework_path(self, all_results: list[Any]) -> None:
        g04 = next(r for r in all_results if r.scenario_id == "G04")
        assert g04.rework_count == 1
        assert g04.agent_execution_count == 5
        assert "REWORK_LOOP" in g04.transition_path

    def test_tool_failure_fires_injection(self, all_results: list[Any]) -> None:
        g03 = next(r for r in all_results if r.scenario_id == "G03")
        assert g03.failure_injections
        assert any(inj.target_component == "tool:policy_lookup" for inj in g03.failure_injections)

    def test_provider_retry(self, all_results: list[Any]) -> None:
        g02 = next(r for r in all_results if r.scenario_id == "G02")
        assert g02.retry_count >= 1

    def test_agent_retry_exhaustion(self, all_results: list[Any]) -> None:
        g07 = next(r for r in all_results if r.scenario_id == "G07")
        assert "MAX_AGENT_RETRIES_EXCEEDED" in g07.errors

    def test_corrupt_checkpoint_rejected(self, all_results: list[Any]) -> None:
        g11 = next(r for r in all_results if r.scenario_id == "G11")
        assert "CheckpointCorruptError" in g11.errors

    def test_replay_no_mutation(self, all_results: list[Any]) -> None:
        g10 = next(r for r in all_results if r.scenario_id == "G10")
        assert g10.replay.ran
        assert g10.replay.row_delta == 0
        assert g10.replay.provider_requests == 0
        assert g10.replay.terminal_matches is True

    def test_missing_artifact_fails_loudly(self, all_results: list[Any]) -> None:
        g14 = next(r for r in all_results if r.scenario_id == "G14")
        assert g14.replay.missing_artifact
        assert "replay-missing-artifact" in g14.errors

    def test_budget_race_one_winner(self, all_results: list[Any]) -> None:
        g12 = next(r for r in all_results if r.scenario_id == "G12")
        assert g12.status == "PASS"

    def test_approval_race_one_winner(self, all_results: list[Any]) -> None:
        g13 = next(r for r in all_results if r.scenario_id == "G13")
        assert g13.approval_outcome == "race_one_winner"
        outcomes = [e for e in g13.errors if e.startswith("outcome:")]
        assert outcomes.count("outcome:DECIDED") == 1
        assert outcomes.count("outcome:CONFLICT") == 5

    def test_broken_telemetry_still_passes(self, all_results: list[Any]) -> None:
        g15 = next(r for r in all_results if r.scenario_id == "G15")
        assert g15.status == "PASS"
        assert g15.terminal_state == "APPROVED"

    def test_resume_no_duplicates(self, all_results: list[Any]) -> None:
        g09 = next(r for r in all_results if r.scenario_id == "G09")
        assert g09.agent_execution_count == 3
        assert g09.terminal_state == "APPROVED"

    def test_invariants_present_on_each_result(self, all_results: list[Any]) -> None:
        for r in all_results:
            assert r.invariant_results, r.scenario_id
            fails = [i for i in r.invariant_results if i.status.value == "FAIL"]
            assert not fails, (r.scenario_id, fails)

    def test_deterministic_rerun_same_path(self) -> None:
        runner = ScenarioRunner()
        a = runner.run(get_scenario("G01"))
        b = runner.run(get_scenario("G01"))
        assert a.transition_path == b.transition_path
        assert a.terminal_state == b.terminal_state
        assert a.agent_execution_count == b.agent_execution_count


class TestCLIEvaluate:
    def test_evaluate_command_pass(self, tmp_path: Path) -> None:
        out = tmp_path / "eval.json"
        md = tmp_path / "eval.md"
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["evaluate", "-o", str(out), "--markdown", str(md)],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        assert out.exists()
        report = EvaluationReport.model_validate_json(out.read_text(encoding="utf-8"))
        assert report.overall_status == "PASS"
        assert md.exists()
        assert "CASEFILE Evaluation Report" in md.read_text(encoding="utf-8")

    def test_evaluate_single_scenario(self, tmp_path: Path) -> None:
        out = tmp_path / "one.json"
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["evaluate", "-o", str(out), "-s", "G01"],
            catch_exceptions=False,
        )
        assert result.exit_code == 0, result.output
        report = EvaluationReport.model_validate_json(out.read_text(encoding="utf-8"))
        assert report.metrics.scenario_count == 1
        assert report.results[0].scenario_id == "G01"

    def test_evaluate_unknown_scenario_fails(self, tmp_path: Path) -> None:
        out = tmp_path / "bad.json"
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["evaluate", "-o", str(out), "-s", "G99"],
        )
        assert result.exit_code != 0

    def test_run_evaluation_stub_unchanged(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["run-evaluation"])
        assert result.exit_code == 0
        assert "Phase 5 deliverable" in result.output


class TestModuleEntrypoint:
    def test_python_m_casefile_evaluation(self, tmp_path: Path) -> None:
        import subprocess
        import sys

        out = tmp_path / "report.json"
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "casefile.evaluation",
                "-o",
                str(out),
                "-s",
                "G01",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert proc.returncode == 0, proc.stderr + proc.stdout
        assert out.exists()
        report = EvaluationReport.model_validate_json(out.read_text(encoding="utf-8"))
        assert report.overall_status == "PASS"


class TestScenarioContracts:
    @pytest.mark.parametrize("sid", sorted(GOLDEN_SCENARIOS))
    def test_scenario_ids_pattern(self, sid: str) -> None:
        scenario: EvaluationScenario = GOLDEN_SCENARIOS[sid]
        assert scenario.scenario_id == sid
        assert scenario.version

    @pytest.mark.parametrize("sid", sorted(GOLDEN_SCENARIOS))
    def test_scenarios_declare_invariants(self, sid: str) -> None:
        scenario = GOLDEN_SCENARIOS[sid]
        assert scenario.expected.require_invariants, sid

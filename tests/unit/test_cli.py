"""
Unit Tests: CLI Entrypoint Commands

Validates CLI behavior for version, healthcheck, and unstarted phase placeholders.
"""

import pytest
from click.testing import CliRunner

from casefile.cli import main


@pytest.mark.unit
def test_cli_version() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["version"])
    assert result.exit_code == 0
    assert "CASEFILE v0.1.0" in result.output
    assert "Phase 1: Repository Foundation & Core Infrastructure" in result.output


@pytest.mark.unit
def test_cli_unimplemented_commands() -> None:
    runner = CliRunner()

    res_process = runner.invoke(main, ["process-claim", "CLM-101"])
    assert res_process.exit_code == 0
    assert "Phase 2 deliverable" in res_process.output

    res_eval = runner.invoke(main, ["run-evaluation"])
    assert res_eval.exit_code == 0
    assert "Phase 5 deliverable" in res_eval.output

    res_replay = runner.invoke(main, ["replay", "chk-123"])
    assert res_replay.exit_code == 0
    assert "Phase 4 deliverable" in res_replay.output


@pytest.mark.unit
def test_cli_healthcheck_mocked() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["healthcheck", "--env", "test"])
    # In test environment without running services, command reports unhealthy and exits with ClickException
    assert "Running CASEFILE health checks for environment: test" in result.output
    assert "POSTGRESQL" in result.output
    assert "REDIS" in result.output
    assert "JAEGER" in result.output

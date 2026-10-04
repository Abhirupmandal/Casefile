"""Tests for replay determinism and replay mismatch detection (Phase 6 Step 9).

Verifies that replay reproduces terminal states and paths without live side effects,
and that corrupted checkpoints or missing artifacts produce deterministic mismatches.
"""

from __future__ import annotations

from casefile.evaluation.dataset import get_evaluation_case
from casefile.evaluation.runner import ScenarioRunner


def test_positive_replay_match() -> None:
    """CASE-029: Replay from checkpoint matches live terminal state and path without mutation."""
    runner = ScenarioRunner()
    case = get_evaluation_case("CASE-029")
    outcome = runner.run_case(case)

    assert outcome.status == "PASS"
    assert outcome.path_matched
    assert outcome.terminal_matched
    assert outcome.terminal_state == "APPROVED"
    assert outcome.replay_matched is True


def test_negative_replay_mismatch_corrupted_checkpoint() -> None:
    """CASE-030: Tampered checkpoint fails integrity verification and fails closed."""
    runner = ScenarioRunner()
    case = get_evaluation_case("CASE-030")
    outcome = runner.run_case(case)

    # In CASE-030, corrupted resume is detected, CheckpointCorruptError is raised and caught,
    # and invariant J verifies fail-closed rejection.
    assert outcome.status == "PASS"
    assert "CheckpointCorruptError" in outcome.errors
    assert outcome.failure_classification is not None
    assert outcome.failure_classification.get("category") == "CHECKPOINT_FAILURE"


def test_replay_missing_artifact_refuses_live_calls() -> None:
    """Replay without recorded provider artifacts fails loudly rather than falling back to live calls."""
    from casefile.evaluation.scenarios import (
        EvaluationScenario,
        ExpectedBehavior,
        ExpectedReplayBehavior,
        ScenarioInitial,
        _approve_scripts,
    )
    from casefile.models.contracts import WorkflowState

    runner = ScenarioRunner()
    sc = EvaluationScenario(
        scenario_id="CASE-029",
        name="Missing replay artifact",
        initial=ScenarioInitial(
            scripts=_approve_scripts(),
            omit_replay_artifacts=True,
        ),
        expected=ExpectedBehavior(
            terminal_state=WorkflowState.APPROVED,
            replay=ExpectedReplayBehavior(
                runs=True,
                mode="REPLAY",
                expect_missing_artifact=True,
                expect_no_mutation=True,
            ),
            require_invariants=("H", "I"),
        ),
    )
    res = runner.run(sc)
    assert res.status == "PASS"
    assert res.replay.missing_artifact is True

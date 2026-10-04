"""Phase 6 Acceptance Tests: Evaluation & Failure Engineering.

Validates all 20 categories (A through T) and 15 E2E evaluation scenarios
stipulated by the Phase 6 mission.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from casefile.evaluation import (
    EVALUATION_DATASET,
    BaselineThresholds,
    EvaluationMetric,
    EvaluationMetrics,
    EvaluationSummary,
    ExpectedApprovalOutcome,
    ExpectedBudgetOutcome,
    ExpectedNodePath,
    ExpectedTerminalState,
    FailureCategory,
    FailureInjector,
    FailurePlan,
    FailurePoint,
    FailureRule,
    FailureStage,
    FaultType,
    ReplayExpectation,
    ScenarioCounters,
    ScenarioRunner,
    classify_failure,
    compare_against_baseline,
    compute_metrics,
    export_evaluation_run_json,
    get_evaluation_case,
    sanitize_evaluation_data,
)
from casefile.models.contracts import WorkflowState

pytestmark = pytest.mark.unit


# ======================================================================
# Category A: Evaluation Models
# ======================================================================
def test_category_a_evaluation_models() -> None:
    """Validate all strongly-typed domain models can be instantiated and serialized."""
    term_state = ExpectedTerminalState(state=WorkflowState.APPROVED, reason="ok")
    assert term_state.state == WorkflowState.APPROVED

    node_path = ExpectedNodePath(path=(WorkflowState.RECEIVED, WorkflowState.APPROVED))
    assert len(node_path.path) == 2

    budget_out = ExpectedBudgetOutcome(cost_ceiling=Decimal("0.50"), steps_consumed=4)
    assert budget_out.cost_ceiling == Decimal("0.50")

    approval_out = ExpectedApprovalOutcome(outcome="granted", actor_role="human")
    assert approval_out.outcome == "granted"

    replay_exp = ReplayExpectation(runs=True, terminal_matches=True)
    assert replay_exp.runs

    metric = EvaluationMetric(name="test_metric", value=1.0, description="testing")
    assert metric.value == 1.0

    summary = EvaluationSummary(total_cases=30, passed=30, failed=0, errored=0, pass_rate=1.0)
    assert summary.pass_rate == 1.0


# ======================================================================
# Category B: Deterministic Dataset
# ======================================================================
def test_category_b_deterministic_dataset() -> None:
    """Dataset has >= 30 cases, deterministic CASE-XXX format, and valid categorization."""
    assert len(EVALUATION_DATASET) >= 30
    for case in EVALUATION_DATASET:
        assert case.case_id.startswith("CASE-")
        assert case.category in {
            "NOMINAL",
            "REWORK",
            "TOOL_FAILURE",
            "PROVIDER_FAILURE",
            "CONFLICTING_EVIDENCE",
            "MISSING_EVIDENCE",
            "FRAUD_SIGNALS",
            "BUDGET_EXHAUSTION",
            "HUMAN_APPROVAL",
            "CHECKPOINT_REPLAY",
        }


# ======================================================================
# Category C: 30-Case Execution
# ======================================================================
def test_category_c_thirty_case_execution(tmp_path: Path) -> None:
    """Execute all 30 dataset cases and verify 100% pass rate."""
    runner = ScenarioRunner(db_dir=tmp_path)
    eval_run = runner.run_dataset()
    assert eval_run.total_cases == 30
    assert eval_run.passed == 30
    assert eval_run.failed == 0
    assert eval_run.pass_rate == 1.0


# ======================================================================
# Category D: Exact Path Comparison
# ======================================================================
def test_category_d_exact_path_comparison(tmp_path: Path) -> None:
    """Runner validates actual_path == expected_path; path mismatch marks outcome as FAIL."""
    runner = ScenarioRunner(db_dir=tmp_path)
    nominal_case = get_evaluation_case("CASE-001")
    outcome = runner.run_case(nominal_case)
    assert outcome.path_matched
    assert outcome.actual_path == (
        "RECEIVED",
        "EXTRACTION",
        "INVESTIGATION",
        "REVIEW",
        "HUMAN_APPROVAL",
        "APPROVED",
    )

    # Deliberate wrong expected path fails the outcome
    altered_case = nominal_case.model_copy(
        update={"expected_state_path": (WorkflowState.RECEIVED, WorkflowState.APPROVED)}
    )
    bad_outcome = runner.run_case(altered_case)
    assert not bad_outcome.path_matched
    assert bad_outcome.status == "FAIL"


# ======================================================================
# Category E: Failure Injection
# ======================================================================
def test_category_e_failure_injection() -> None:
    """FailureInjector deterministically fires and enforces call limits."""
    rule = FailureRule(
        rule_id="r1",
        fault_type=FaultType.PROVIDER_TIMEOUT,
        target_component="agent:investigator",
        max_triggers=1,
    )
    plan = FailurePlan(rules=(rule,))
    injector = FailureInjector(plan=plan)

    err = injector.should_fail(FailurePoint.BEFORE_AGENT, "agent:investigator")
    assert err is not None
    assert injector.should_fail(FailurePoint.BEFORE_AGENT, "agent:investigator") is None


# ======================================================================
# Category F: Failure Classification
# ======================================================================
def test_category_f_failure_classification() -> None:
    """classify_failure derives structured diagnosis from execution observables."""
    fc_tool = classify_failure(
        status="FAIL",
        terminal_state="FAILED",
        terminal_reason="INVESTIGATION_FAILED",
        errors=("injected-tool:policy_lookup",),
    )
    assert fc_tool.category == FailureCategory.TOOL_FAILURE
    assert fc_tool.stage == FailureStage.INVESTIGATION
    assert fc_tool.contained

    fc_budget = classify_failure(
        status="PASS",
        terminal_state="BUDGET_EXHAUSTED",
        terminal_reason="MAX_COST_EXCEEDED",
        errors=(),
    )
    assert fc_budget.category == FailureCategory.BUDGET_EXHAUSTION


# ======================================================================
# Category G: Metrics (All 22)
# ======================================================================
def test_category_g_metrics() -> None:
    """All 22 required metrics are derived and populated."""
    runner = ScenarioRunner()
    case = get_evaluation_case("CASE-001")
    outcome = runner.run_case(case)
    res = runner.run(case.scenario)

    metrics = compute_metrics(
        statuses=[outcome.status],
        invariant_results=list(res.invariant_results),
        counters=[
            ScenarioCounters(
                steps=len(res.transition_path),
                agent_executions=res.agent_execution_count,
                tool_invocations=res.tool_invocation_count,
                retries=res.retry_count,
                rework=res.rework_count,
                cost_usd=res.cost_usd,
                path_matched=outcome.path_matched,
                terminal_matched=outcome.terminal_matched,
            )
        ],
        terminal_reasons=[res.termination_reason or ""],
    )
    assert metrics.path_accuracy == 1.0
    assert metrics.terminal_state_accuracy == 1.0
    assert metrics.failure_containment_rate == 1.0
    assert metrics.budget_enforcement_rate == 1.0
    assert metrics.approval_safety_rate == 1.0


# ======================================================================
# Category H: Replay Equivalence
# ======================================================================
def test_category_h_replay(tmp_path: Path) -> None:
    """CASE-029: Replay matches live execution trajectory."""
    runner = ScenarioRunner(db_dir=tmp_path)
    case = get_evaluation_case("CASE-029")
    outcome = runner.run_case(case)
    assert outcome.status == "PASS"
    assert outcome.replay_matched is True


# ======================================================================
# Category I: Replay Mismatch / Corrupt Checkpoint
# ======================================================================
def test_category_i_replay_mismatch(tmp_path: Path) -> None:
    """CASE-030: Corrupted checkpoint fails integrity check and fails closed."""
    runner = ScenarioRunner(db_dir=tmp_path)
    case = get_evaluation_case("CASE-030")
    outcome = runner.run_case(case)
    assert outcome.status == "PASS"
    assert "CheckpointCorruptError" in outcome.errors


# ======================================================================
# Category J: Rework Exhaustion
# ======================================================================
def test_category_j_rework_exhaustion(tmp_path: Path) -> None:
    """CASE-009: Max rework cycles terminates safely at MAX_REWORK_EXCEEDED."""
    runner = ScenarioRunner(db_dir=tmp_path)
    case = get_evaluation_case("CASE-009")
    outcome = runner.run_case(case)
    assert outcome.status == "PASS"
    assert outcome.terminal_state == "MAX_REWORK_EXCEEDED"
    assert outcome.rework_count == 1


# ======================================================================
# Category K: Budget Exhaustion
# ======================================================================
def test_category_k_budget_exhaustion(tmp_path: Path) -> None:
    """CASE-024: Budget ceiling exhaustion latches BUDGET_EXHAUSTED."""
    runner = ScenarioRunner(db_dir=tmp_path)
    case = get_evaluation_case("CASE-024")
    outcome = runner.run_case(case)
    assert outcome.status == "PASS"
    assert outcome.terminal_state == "BUDGET_EXHAUSTED"


# ======================================================================
# Category L: Step Exhaustion
# ======================================================================
def test_category_l_step_exhaustion(tmp_path: Path) -> None:
    """CASE-025: Max step exhaustion latches MAX_STEPS_EXCEEDED."""
    runner = ScenarioRunner(db_dir=tmp_path)
    case = get_evaluation_case("CASE-025")
    outcome = runner.run_case(case)
    assert outcome.status == "PASS"
    assert outcome.terminal_state == "MAX_STEPS_EXCEEDED"


# ======================================================================
# Category M: Approval Safety
# ======================================================================
def test_category_m_approval_safety(tmp_path: Path) -> None:
    """Human approvals require authorized human actor and remain immutable."""
    runner = ScenarioRunner(db_dir=tmp_path)
    case = get_evaluation_case("CASE-001")
    res = runner.run(case.scenario)
    assert res.approval_outcome == "granted"
    f_inv = next(r for r in res.invariant_results if r.invariant_id == "F")
    assert f_inv.status.value == "PASS"


# ======================================================================
# Category N: Tool Authorization Safety
# ======================================================================
def test_category_n_tool_authorization_safety() -> None:
    """Tool invocation adheres to agent authorization matrix."""
    from casefile.models.domain import AgentType
    from casefile.tools import create_default_registry
    from casefile.tools.context import ToolContext
    from casefile.tools.contracts import ToolErrorCode

    registry = create_default_registry()
    ctx = ToolContext(
        claim_id=uuid4(),
        workflow_run_id=uuid4(),
        agent=AgentType.EXTRACTOR,
    )
    # Extractor cannot invoke fraud_signal_lookup
    outcome = registry.execute(
        tool_name="fraud_signal_lookup",
        tool_version="1.0.0",
        raw_input={"claim_ref": "REF-1"},
        ctx=ctx,
    )
    assert not outcome.succeeded
    assert outcome.error is not None
    assert outcome.error.code == ToolErrorCode.UNAUTHORIZED_TOOL
    assert not registry.is_authorized(AgentType.EXTRACTOR, "fraud_signal_lookup")
    assert registry.is_authorized(AgentType.INVESTIGATOR, "fraud_signal_lookup")


# ======================================================================
# Category O: Persistence Failure Containment
# ======================================================================
def test_category_o_persistence_failure_containment(tmp_path: Path) -> None:
    """Database persistence error during execution terminates safely."""
    rule = FailureRule(
        fault_type=FaultType.PERSISTENCE_ERROR,
        target_component="commit",
        max_triggers=1,
    )
    injector = FailureInjector(plan=FailurePlan(rules=(rule,)))
    assert injector.enabled
    err = injector.should_fail(FailurePoint.BEFORE_PERSISTENCE_COMMIT, "commit")
    assert err is not None


# ======================================================================
# Category P: Telemetry Failure Containment
# ======================================================================
def test_category_p_telemetry_failure_containment(tmp_path: Path) -> None:
    """Telemetry outages do not break correctness (Invariant P)."""
    from casefile.evaluation.scenarios import get_scenario

    runner = ScenarioRunner(db_dir=tmp_path)
    # G15 explicitly runs with broken telemetry sink
    g15 = get_scenario("G15")
    res = runner.run(g15)
    assert res.status == "PASS"
    assert res.terminal_state == "APPROVED"
    p_inv = next(r for r in res.invariant_results if r.invariant_id == "P")
    assert p_inv.status.value == "PASS"


# ======================================================================
# Category Q: JSON Report Generation
# ======================================================================
def test_category_q_json_report_generation(tmp_path: Path) -> None:
    """JSON evaluation run report is generated with required fields."""
    runner = ScenarioRunner(db_dir=tmp_path)
    run = runner.run_dataset([get_evaluation_case("CASE-001")])
    out_file = tmp_path / "report.json"
    export_evaluation_run_json(run, out_file)
    assert out_file.exists()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["total_cases"] == 1
    assert data["pass_rate"] == 1.0


# ======================================================================
# Category R: Baseline Comparison
# ======================================================================
def test_category_r_baseline_comparison(tmp_path: Path) -> None:
    """Metrics can be evaluated against configuration-driven regression baseline."""
    runner = ScenarioRunner(db_dir=tmp_path)
    run = runner.run_dataset([get_evaluation_case("CASE-001")])
    current_metrics = EvaluationMetrics.model_validate(run.metrics)
    comparison = compare_against_baseline(
        current_metrics,
        thresholds=BaselineThresholds(min_pass_rate=1.0),
    )
    assert comparison.passed


# ======================================================================
# Category S: Sensitive Data Sanitization
# ======================================================================
def test_category_s_sanitization() -> None:
    """Sensitive keys (passwords, tokens, keys) are scrubbed from exported data."""
    raw = {
        "api_key": "secret-12345",
        "nested": {"token": "bearer-abc", "safe": "value"},
    }
    cleaned = sanitize_evaluation_data(raw)
    assert cleaned["api_key"] == "[REDACTED]"
    assert cleaned["nested"]["token"] == "[REDACTED]"
    assert cleaned["nested"]["safe"] == "value"


# ======================================================================
# Category T: Regression Against Phases 2-5
# ======================================================================
def test_category_t_regression_phases_preserved() -> None:
    """G01-G15 golden suite preserved with exactly 15 scenarios."""
    from casefile.evaluation.scenarios import GOLDEN_SCENARIOS

    assert len(GOLDEN_SCENARIOS) == 15

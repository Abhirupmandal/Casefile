"""
Unit Tests: Evaluation Core (Phase 11)

Covers invariant engine A–P, failure injection framework, metrics,
reports, fixtures/recipes, scenario models, and the scenario runner
surface (without full end-to-end drives — those live in integration).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from casefile.budget.envelope import BudgetEnvelope
from casefile.budget.termination import BudgetTermination
from casefile.evaluation import (
    GOLDEN_SCENARIOS,
    FailureInjection,
    FailureInjector,
    FailurePoint,
    FailureType,
    InvariantContext,
    InvariantStatus,
    RecoveryBehavior,
    ScenarioRunner,
    build_report,
    evaluate_invariants,
    get_scenario,
    invariant_pass_rate,
    report_to_json,
    report_to_markdown,
)
from casefile.evaluation.assertions import (
    INVARIANT_LETTERS,
    ApprovalFact,
    BudgetFact,
    TransitionFact,
    agent_type_is_known,
    budget_reason_is_known,
    summarize_budget,
    trigger_is_enum,
)
from casefile.evaluation.fixtures import (
    UNTRUSTED_MARKER,
    ClaimSpec,
    PayloadRecipe,
    resolve_payload,
)
from casefile.evaluation.metrics import EvaluationMetrics, ScenarioCounters, compute_metrics
from casefile.evaluation.reports import (
    EvaluationReport,
    EvaluationResult,
    ReplayComparison,
    invariant_status_counts,
    write_report,
)
from casefile.evaluation.scenarios import (
    AgentScript,
    EvaluationScenario,
    ExpectedBehavior,
    ScenarioInitial,
    ScriptedStep,
    ScriptKind,
)
from casefile.models.contracts import WorkflowState
from casefile.models.domain import AgentType

pytestmark = pytest.mark.unit


def _clean_ctx(**kwargs: object) -> InvariantContext:
    """Context that satisfies every invariant by construction."""
    defaults: dict[str, object] = {
        "terminal_state": WorkflowState.APPROVED,
        "transition_path": (
            WorkflowState.RECEIVED,
            WorkflowState.EXTRACTION,
            WorkflowState.INVESTIGATION,
            WorkflowState.REVIEW,
            WorkflowState.HUMAN_APPROVAL,
            WorkflowState.APPROVED,
        ),
        "transitions": (
            TransitionFact(
                sequence_no=1,
                execution_id=str(uuid4()),
                idempotency_key="k1",
                trigger="CLAIM_VALIDATED",
                actor=AgentType.SUPERVISOR.value,
                source=WorkflowState.RECEIVED.value,
                destination=WorkflowState.EXTRACTION.value,
            ),
            TransitionFact(
                sequence_no=2,
                execution_id=str(uuid4()),
                idempotency_key="k2",
                trigger="EXTRACTION_COMPLETE",
                actor=AgentType.SUPERVISOR.value,
                source=WorkflowState.EXTRACTION.value,
                destination=WorkflowState.INVESTIGATION.value,
            ),
        ),
        "reached_terminal": True,
        "ops_after_terminal": 0,
        "budget": summarize_budget(steps=5, cost_usd=Decimal("0.01")),
        "envelope": BudgetEnvelope(),
        "approval": ApprovalFact(
            status="APPROVED",
            approver_role="HUMAN",
            recommendation_json="{}",
            reviewer_summary="ok",
            request_version=1,
        ),
        "approval_immutable_ok": True,
        "checkpoint_integrity_ok": True,
        "postgres_absent": True,
        "sqlite_backend": True,
        "triggers_valid_enum": True,
        "per_agent_run_counts": {
            AgentType.EXTRACTOR.value: 1,
            AgentType.INVESTIGATOR.value: 1,
            AgentType.REVIEWER.value: 1,
        },
        "duplicate_applied": True,
        "untrusted_marker": UNTRUSTED_MARKER,
        "marker_in_triggers_or_reasons": False,
        "replay_row_delta": 0,
        "replay_provider_requests": 0,
        "replay_terminal_matches": True,
        "broken_telemetry_enabled": True,
        "telemetry_errors": 1,
    }
    defaults.update(kwargs)
    return InvariantContext(**defaults)  # type: ignore[arg-type]


class TestInvariantLetters:
    def test_letters_are_a_to_p(self) -> None:
        assert tuple("ABCDEFGHIJKLMNOP") == INVARIANT_LETTERS

    def test_unknown_letter_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown invariant"):
            evaluate_invariants(_clean_ctx(), ("Z",))

    def test_all_invariants_pass_on_clean_context(self) -> None:
        results = evaluate_invariants(_clean_ctx())
        assert len(results) == 16
        by_id = {r.invariant_id: r for r in results}
        for letter in INVARIANT_LETTERS:
            assert (
                by_id[letter].status is InvariantStatus.PASS
            ), f"{letter}: {by_id[letter].message}"

    def test_pass_rate_excludes_skipped(self) -> None:
        ctx = InvariantContext(terminal_state=None, reached_terminal=False)
        results = evaluate_invariants(ctx, ("A", "B"))
        # A skips (no terminal), B skips (no terminal, no error)
        assert all(r.status is InvariantStatus.SKIPPED for r in results)
        assert invariant_pass_rate(results) == 1.0


class TestInvariantFailures:
    def test_a_fails_on_ops_after_terminal(self) -> None:
        ctx = _clean_ctx(ops_after_terminal=2)
        results = {r.invariant_id: r for r in evaluate_invariants(ctx, ("A", "C"))}
        assert results["A"].status is InvariantStatus.FAIL
        assert results["C"].status is InvariantStatus.FAIL

    def test_b_fails_when_not_terminal(self) -> None:
        ctx = _clean_ctx(terminal_state=WorkflowState.INVESTIGATION, reached_terminal=False)
        results = {r.invariant_id: r for r in evaluate_invariants(ctx, ("B",))}
        assert results["B"].status is InvariantStatus.FAIL

    def test_d_fails_on_budget_breach_without_latch(self) -> None:
        ctx = _clean_ctx(
            budget=summarize_budget(steps=100),
            envelope=BudgetEnvelope(max_steps=5),
        )
        results = {r.invariant_id: r for r in evaluate_invariants(ctx, ("D",))}
        assert results["D"].status is InvariantStatus.FAIL

    def test_e_fails_on_negative_counter(self) -> None:
        # BudgetFact validates ge=0; E is defense-in-depth against injected state.
        ctx = _clean_ctx(
            budget=BudgetFact.model_construct(steps=1, input_tokens=-1),
        )
        results = {r.invariant_id: r for r in evaluate_invariants(ctx, ("E",))}
        assert results["E"].status is InvariantStatus.FAIL

    def test_f_fails_when_approver_not_human(self) -> None:
        ctx = _clean_ctx(
            approval=ApprovalFact(status="APPROVED", approver_role="AGENT"),
        )
        results = {r.invariant_id: r for r in evaluate_invariants(ctx, ("F",))}
        assert results["F"].status is InvariantStatus.FAIL

    def test_f_skips_when_pending_without_role(self) -> None:
        ctx = _clean_ctx(approval=ApprovalFact(status="PENDING", approver_role=None))
        results = {r.invariant_id: r for r in evaluate_invariants(ctx, ("F",))}
        assert results["F"].status is InvariantStatus.PASS

    def test_g_fails_when_immutable_flag_false(self) -> None:
        ctx = _clean_ctx(approval_immutable_ok=False)
        results = {r.invariant_id: r for r in evaluate_invariants(ctx, ("G",))}
        assert results["G"].status is InvariantStatus.FAIL

    def test_h_fails_on_replay_mutation(self) -> None:
        ctx = _clean_ctx(replay_row_delta=3)
        results = {r.invariant_id: r for r in evaluate_invariants(ctx, ("H",))}
        assert results["H"].status is InvariantStatus.FAIL

    def test_i_fails_on_live_call_during_replay(self) -> None:
        ctx = _clean_ctx(replay_provider_requests=1)
        results = {r.invariant_id: r for r in evaluate_invariants(ctx, ("I",))}
        assert results["I"].status is InvariantStatus.FAIL

    def test_j_fails_when_corrupt_not_rejected(self) -> None:
        ctx = _clean_ctx(checkpoint_integrity_ok=False, checkpoint_rejected=None)
        results = {r.invariant_id: r for r in evaluate_invariants(ctx, ("J",))}
        assert results["J"].status is InvariantStatus.FAIL

    def test_j_passes_when_rejected(self) -> None:
        ctx = _clean_ctx(checkpoint_integrity_ok=False, checkpoint_rejected=True)
        results = {r.invariant_id: r for r in evaluate_invariants(ctx, ("J",))}
        assert results["J"].status is InvariantStatus.PASS

    def test_k_skips_without_agent_counts(self) -> None:
        ctx = _clean_ctx(per_agent_run_counts={})
        results = {r.invariant_id: r for r in evaluate_invariants(ctx, ("K",))}
        assert results["K"].status is InvariantStatus.SKIPPED

    def test_k_fails_when_extractor_runs_twice(self) -> None:
        ctx = _clean_ctx(per_agent_run_counts={"extractor": 2})
        results = {r.invariant_id: r for r in evaluate_invariants(ctx, ("K",))}
        assert results["K"].status is InvariantStatus.FAIL

    def test_l_passes_when_duplicate_reported_true(self) -> None:
        ctx = _clean_ctx(duplicate_applied=True)
        results = {r.invariant_id: r for r in evaluate_invariants(ctx, ("L",))}
        assert results["L"].status is InvariantStatus.PASS

    def test_l_fails_when_duplicate_not_reported(self) -> None:
        ctx = _clean_ctx(duplicate_applied=False)
        results = {r.invariant_id: r for r in evaluate_invariants(ctx, ("L",))}
        assert results["L"].status is InvariantStatus.FAIL

    def test_m_fails_on_non_contiguous_sequence(self) -> None:
        exec_id = str(uuid4())
        ctx = _clean_ctx(
            transitions=(
                TransitionFact(
                    sequence_no=1,
                    execution_id=exec_id,
                    idempotency_key="k1",
                    trigger="CLAIM_VALIDATED",
                    actor=AgentType.SUPERVISOR.value,
                    source="RECEIVED",
                    destination="EXTRACTION",
                ),
                TransitionFact(
                    sequence_no=5,
                    execution_id=exec_id,
                    idempotency_key="k2",
                    trigger="EXTRACTION_COMPLETE",
                    actor=AgentType.SUPERVISOR.value,
                    source="EXTRACTION",
                    destination="INVESTIGATION",
                ),
            )
        )
        results = {r.invariant_id: r for r in evaluate_invariants(ctx, ("M",))}
        assert results["M"].status is InvariantStatus.FAIL

    def test_n_fails_when_marker_drives_control(self) -> None:
        ctx = _clean_ctx(marker_in_triggers_or_reasons=True)
        results = {r.invariant_id: r for r in evaluate_invariants(ctx, ("N",))}
        assert results["N"].status is InvariantStatus.FAIL

    def test_o_fails_when_postgres_present(self) -> None:
        ctx = _clean_ctx(postgres_absent=False)
        results = {r.invariant_id: r for r in evaluate_invariants(ctx, ("O",))}
        assert results["O"].status is InvariantStatus.FAIL

    def test_p_passes_when_telemetry_fails_but_run_ok(self) -> None:
        ctx = _clean_ctx(broken_telemetry_enabled=True, telemetry_errors=3)
        results = {r.invariant_id: r for r in evaluate_invariants(ctx, ("P",))}
        assert results["P"].status is InvariantStatus.PASS

    def test_p_fails_when_telemetry_errors_missing(self) -> None:
        ctx = _clean_ctx(broken_telemetry_enabled=True, telemetry_errors=0)
        results = {r.invariant_id: r for r in evaluate_invariants(ctx, ("P",))}
        assert results["P"].status is InvariantStatus.FAIL


class TestHelpers:
    def test_trigger_is_enum(self) -> None:
        assert trigger_is_enum("CLAIM_VALIDATED")
        assert not trigger_is_enum("NOT_A_TRIGGER")

    def test_budget_reason_is_known(self) -> None:
        assert budget_reason_is_known(None)
        assert budget_reason_is_known(BudgetTermination.MAX_STEPS_EXCEEDED.value)
        assert not budget_reason_is_known("INVENTED")

    def test_agent_type_is_known(self) -> None:
        assert agent_type_is_known(AgentType.EXTRACTOR.value)
        assert not agent_type_is_known("WIZARD")

    def test_summarize_budget_totalizes_tokens(self) -> None:
        fact = summarize_budget(input_tokens=10, output_tokens=5)
        assert fact.total_tokens == 15


class TestFailureInjector:
    def test_disabled_when_empty(self) -> None:
        injector = FailureInjector([])
        assert not injector.enabled
        assert injector.should_fail(FailurePoint.BEFORE_TOOL, "tool:policy_lookup") is None

    def test_fires_on_point_and_target(self) -> None:
        rule = FailureInjection(
            failure_id="F1",
            point=FailurePoint.BEFORE_TOOL,
            type=FailureType.UNAVAILABLE_DEPENDENCY,
            target_component="tool:policy_lookup",
            max_triggers=1,
            expected_recovery_behavior=RecoveryBehavior.TERMINATE_NODE,
        )
        injector = FailureInjector([rule])
        assert injector.enabled
        err = injector.should_fail(FailurePoint.BEFORE_TOOL, "tool:policy_lookup")
        assert err is not None
        assert err.injection.failure_id == "F1"
        # max_triggers=1 exhausted
        assert injector.should_fail(FailurePoint.BEFORE_TOOL, "tool:policy_lookup") is None
        assert injector.fire_count("F1") == 1
        assert len(injector.triggered) == 1

    def test_point_mismatch_does_not_fire(self) -> None:
        rule = FailureInjection(
            failure_id="F2",
            point=FailurePoint.BEFORE_TOOL,
            type=FailureType.TIMEOUT,
            target_component="*",
        )
        injector = FailureInjector([rule])
        assert injector.should_fail(FailurePoint.AFTER_AGENT, "agent:extractor") is None

    def test_wildcard_target_matches(self) -> None:
        rule = FailureInjection(
            failure_id="F3",
            point=FailurePoint.BEFORE_AGENT,
            type=FailureType.EXCEPTION,
            target_component="*",
            max_triggers=1,
        )
        injector = FailureInjector([rule])
        assert injector.should_fail(FailurePoint.BEFORE_AGENT, "agent:reviewer") is not None

    def test_target_kind_suffix_match(self) -> None:
        rule = FailureInjection(
            failure_id="F4",
            point=FailurePoint.BEFORE_TOOL,
            type=FailureType.EXCEPTION,
            target_component="tool:fraud_signal_lookup",
            max_triggers=1,
        )
        injector = FailureInjector([rule])
        assert (
            injector.should_fail(FailurePoint.BEFORE_TOOL, "tool:fraud_signal_lookup") is not None
        )

    def test_maybe_raise_raises(self) -> None:
        from casefile.evaluation.failures import InjectedFailureError

        rule = FailureInjection(
            failure_id="F5",
            point=FailurePoint.BEFORE_CHECKPOINT_CREATE,
            type=FailureType.EXCEPTION,
            target_component="*",
            max_triggers=1,
        )
        injector = FailureInjector([rule])
        with pytest.raises(InjectedFailureError):
            injector.maybe_raise(FailurePoint.BEFORE_CHECKPOINT_CREATE, "checkpoint")

    @pytest.mark.parametrize(
        ("point", "ftype"),
        [
            (FailurePoint.BEFORE_AGENT, FailureType.EXCEPTION),
            (FailurePoint.AFTER_AGENT, FailureType.TIMEOUT),
            (FailurePoint.BEFORE_TOOL, FailureType.UNAVAILABLE_DEPENDENCY),
            (FailurePoint.AFTER_TOOL, FailureType.MALFORMED_RESULT),
            (FailurePoint.BEFORE_WORKFLOW_TRANSITION, FailureType.EXCEPTION),
            (FailurePoint.AFTER_WORKFLOW_TRANSITION, FailureType.CONCURRENCY_CONFLICT),
            (FailurePoint.BEFORE_CHECKPOINT_CREATE, FailureType.EXCEPTION),
            (FailurePoint.AFTER_CHECKPOINT_CREATE, FailureType.INTEGRITY_FAILURE),
            (FailurePoint.BEFORE_BUDGET_RESERVE, FailureType.EXCEPTION),
            (FailurePoint.AFTER_BUDGET_RESERVE, FailureType.TIMEOUT),
            (FailurePoint.BEFORE_APPROVAL_DECIDE, FailureType.EXCEPTION),
            (FailurePoint.AFTER_APPROVAL_DECIDE, FailureType.CONCURRENCY_CONFLICT),
            (FailurePoint.BEFORE_PERSISTENCE_COMMIT, FailureType.EXCEPTION),
            (FailurePoint.AFTER_PERSISTENCE_COMMIT, FailureType.INTEGRITY_FAILURE),
            (FailurePoint.DURING_REPLAY_ARTIFACT_RETRIEVAL, FailureType.MISSING_ARTIFACT),
        ],
    )
    def test_all_failure_points_fire(self, point: FailurePoint, ftype: FailureType) -> None:
        rule = FailureInjection(
            failure_id=f"P-{point.value}",
            point=point,
            type=ftype,
            target_component="*",
            max_triggers=1,
        )
        injector = FailureInjector([rule])
        assert injector.should_fail(point, "component") is not None

    @pytest.mark.parametrize("ftype", list(FailureType))
    def test_all_failure_types_construct(self, ftype: FailureType) -> None:
        rule = FailureInjection(
            failure_id=f"T-{ftype.value}",
            point=FailurePoint.BEFORE_AGENT,
            type=ftype,
        )
        assert rule.type is ftype


class TestFixtures:
    def test_claim_spec_to_claim_input(self) -> None:
        spec = ClaimSpec()
        claim = spec.to_claim_input(uuid4())
        assert claim.policy_id == "POL-SYN-001"
        assert claim.claim_amount == Decimal("1500.00")

    def test_untrusted_marker_appended(self) -> None:
        spec = ClaimSpec(untrusted_marker=True)
        claim = spec.to_claim_input()
        assert UNTRUSTED_MARKER in claim.description

    @pytest.mark.parametrize("recipe", list(PayloadRecipe))
    def test_all_recipes_resolve(self, recipe: PayloadRecipe) -> None:
        payload = resolve_payload(recipe, workflow_id=uuid4())
        assert isinstance(payload, dict)
        assert "workflow_id" in payload

    def test_review_recipes_have_decision(self) -> None:
        for recipe in (
            PayloadRecipe.REVIEW_APPROVE,
            PayloadRecipe.REVIEW_REJECT,
            PayloadRecipe.REVIEW_REWORK,
        ):
            payload = resolve_payload(recipe, workflow_id=uuid4())
            assert payload["decision"] in ("APPROVE", "REJECT", "REWORK")


class TestScenarios:
    def test_golden_has_fifteen(self) -> None:
        assert len(GOLDEN_SCENARIOS) == 15
        assert set(GOLDEN_SCENARIOS) == {f"G{i:02d}" for i in range(1, 16)}

    def test_get_scenario_known(self) -> None:
        s = get_scenario("G01")
        assert s.scenario_id == "G01"
        assert s.expected.terminal_state == WorkflowState.APPROVED

    def test_get_scenario_unknown(self) -> None:
        with pytest.raises(KeyError, match="Unknown scenario"):
            get_scenario("G99")

    def test_scenarios_are_frozen_models(self) -> None:
        s = get_scenario("G01")
        with pytest.raises(ValidationError):
            s.scenario_id = "G02"

    def test_script_kinds(self) -> None:
        step = ScriptedStep(kind=ScriptKind.ERROR, error_retryable=True, input_tokens=0)
        assert step.kind is ScriptKind.ERROR
        assert step.input_tokens == 0

    def test_agent_script_defaults(self) -> None:
        script = AgentScript(agent="extractor", steps=(ScriptedStep(),))
        assert script.agent == "extractor"
        assert len(script.steps) == 1

    def test_scenario_pattern_requires_gnn(self) -> None:
        with pytest.raises(ValidationError):
            EvaluationScenario(
                scenario_id="X1",
                name="bad",
                initial=ScenarioInitial(),
                expected=ExpectedBehavior(),
            )


class TestMetrics:
    def test_compute_metrics_all_pass(self) -> None:
        inv = evaluate_invariants(_clean_ctx())
        metrics = compute_metrics(
            statuses=["PASS", "PASS"],
            invariant_results=inv,
            counters=[
                ScenarioCounters(steps=5, cost_usd=Decimal("0.01")),
                ScenarioCounters(steps=3, cost_usd=Decimal("0.02")),
            ],
            terminal_reasons=["APPROVAL_GRANTED", "APPROVAL_GRANTED"],
        )
        assert isinstance(metrics, EvaluationMetrics)
        assert metrics.scenario_count == 2
        assert metrics.scenarios_passed == 2
        assert metrics.scenario_pass_rate == 1.0
        assert metrics.invariant_pass_rate == 1.0
        assert metrics.total_cost_usd == Decimal("0.03")
        assert metrics.mean_steps == 4.0

    def test_compute_metrics_empty(self) -> None:
        metrics = compute_metrics(
            statuses=[],
            invariant_results=[],
            counters=[],
            terminal_reasons=[],
        )
        assert metrics.scenario_count == 0
        assert metrics.scenario_pass_rate == 1.0

    def test_pass_rate_with_failures(self) -> None:
        metrics = compute_metrics(
            statuses=["PASS", "FAIL", "ERROR"],
            invariant_results=[],
            counters=[ScenarioCounters(), ScenarioCounters(), ScenarioCounters()],
            terminal_reasons=["", "FAILED", ""],
        )
        assert metrics.scenarios_passed == 1
        assert metrics.scenarios_failed == 1
        assert metrics.scenarios_errored == 1
        assert metrics.scenario_pass_rate == pytest.approx(1 / 3, abs=1e-4)

    def test_terminal_reason_distribution(self) -> None:
        metrics = compute_metrics(
            statuses=["PASS", "PASS"],
            invariant_results=[],
            counters=[ScenarioCounters(), ScenarioCounters()],
            terminal_reasons=["APPROVAL_GRANTED", "REJECTED"],
        )
        assert metrics.terminal_reason_distribution == {
            "APPROVAL_GRANTED": 1,
            "REJECTED": 1,
        }


class TestReports:
    def _result(self, status: str = "PASS") -> EvaluationResult:
        return EvaluationResult(
            scenario_id="G01",
            run_id=str(uuid4()),
            status=status,
            terminal_state="APPROVED",
            transition_path=("RECEIVED", "APPROVED"),
            agent_execution_count=3,
            cost_usd=Decimal("0.01"),
            invariant_results=tuple(evaluate_invariants(_clean_ctx())),
        )

    def test_build_report_pass(self) -> None:
        report = build_report([self._result(), self._result()])
        assert report.overall_status == "PASS"
        assert report.metrics.scenario_count == 2

    def test_build_report_fail_on_non_pass(self) -> None:
        report = build_report([self._result(), self._result("FAIL")], failures=["x"])
        assert report.overall_status == "FAIL"

    def test_build_report_empty_is_fail(self) -> None:
        report = build_report([])
        assert report.overall_status == "FAIL"

    def test_json_roundtrip(self) -> None:
        report = build_report([self._result()])
        text = report_to_json(report)
        assert '"overall_status"' in text
        parsed = EvaluationReport.model_validate_json(text)
        assert parsed.overall_status == "PASS"

    def test_write_report(self, tmp_path: Path) -> None:
        report = build_report([self._result()])
        path = write_report(report, tmp_path / "nested" / "report.json")
        assert path.exists()
        assert EvaluationReport.model_validate_json(path.read_text(encoding="utf-8"))

    def test_markdown_summary(self) -> None:
        report = build_report([self._result()])
        md = report_to_markdown(report)
        assert "# CASEFILE Evaluation Report" in md
        assert "G01" in md
        assert "PASS" in md

    def test_invariant_status_counts(self) -> None:
        counts = invariant_status_counts(list(evaluate_invariants(_clean_ctx())))
        assert counts["PASS"] == 16
        assert counts["FAIL"] == 0

    def test_replay_comparison_defaults(self) -> None:
        rc = ReplayComparison()
        assert rc.ran is False
        assert rc.terminal_matches is None


class TestRunnerSurface:
    def test_runner_constructs(self) -> None:
        runner = ScenarioRunner()
        assert runner is not None

    def test_budget_trigger_mapping(self) -> None:
        from casefile.evaluation.runner import BudgetTrigger
        from casefile.workflow.triggers import Trigger

        assert (
            BudgetTrigger.for_reason(BudgetTermination.MAX_STEPS_EXCEEDED)
            is Trigger.STEPS_EXHAUSTED
        )
        assert (
            BudgetTrigger.for_reason(BudgetTermination.MAX_AGENT_STEPS_EXCEEDED)
            is Trigger.STEPS_EXHAUSTED
        )
        assert (
            BudgetTrigger.for_reason(BudgetTermination.MAX_WALL_CLOCK_EXCEEDED)
            is Trigger.WORKFLOW_TIMED_OUT
        )
        assert BudgetTrigger.for_reason(BudgetTermination.MAX_COST_EXCEEDED) is (
            Trigger.BUDGET_EXHAUSTED
        )

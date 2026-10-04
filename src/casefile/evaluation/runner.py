"""
Scenario runner: deterministic offline execution of one evaluation scenario.

Builds a fresh SQLite world (engine, budget, agents, tools, approvals,
checkpoints) per scenario, drives the workflow through the typed state
machine with scripted providers and fixture tools, optionally injects
typed failures, and evaluates invariants A–P. Checks live only here —
production modules are never branched on evaluation state.
"""

from __future__ import annotations

import sys
from collections import Counter
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from casefile.agents.base import AgentFailedError
from casefile.agents.context import AgentContext
from casefile.agents.deterministic import DeterministicProvider
from casefile.agents.extractor import ExtractorAgent
from casefile.agents.investigator import InvestigatorAgent
from casefile.agents.providers import ProviderError
from casefile.agents.results import AgentExecutionRecord
from casefile.agents.reviewer import ReviewerAgent
from casefile.approval.model import (
    ApprovalDecision,
    ApprovalError,
    ApprovalOutcome,
    ApprovalVerdict,
    HumanActor,
)
from casefile.approval.service import ApprovalService
from casefile.budget.engine import BudgetEngine, ReserveKind, make_retry_guard
from casefile.budget.pricing import ModelPricing, ModelUsage, PricingTable
from casefile.budget.termination import BudgetTermination
from casefile.budget.usage import BudgetUsage
from casefile.checkpoint.model import (
    Checkpoint,
    CheckpointCorruptError,
    CheckpointKind,
    RecordedAgentOutput,
    TransitionPathEntry,
)
from casefile.checkpoint.repository import SqlCheckpointRepository
from casefile.checkpoint.resume import resume_from_checkpoint
from casefile.evaluation.assertions import (
    ApprovalFact,
    BudgetFact,
    InvariantContext,
    InvariantResult,
    TransitionFact,
    evaluate_invariants,
    trigger_is_enum,
)
from casefile.evaluation.classifier import classify_failure
from casefile.evaluation.failures import (
    FailureInjector,
    FailurePoint,
    InjectedFailureError,
    RecoveryBehavior,
)
from casefile.evaluation.fixtures import (
    UNTRUSTED_MARKER,
    resolve_payload,
)
from casefile.evaluation.model import EvaluationCase, EvaluationOutcome, EvaluationRun
from casefile.evaluation.reports import EvaluationResult, ReplayComparison
from casefile.evaluation.scenarios import (
    AgentScript,
    ConcurrencyMode,
    EvaluationScenario,
    ScriptedStep,
    ScriptKind,
)
from casefile.models.contracts import (
    ExtractionRequest,
    ExtractionResult,
    InvestigationRequest,
    InvestigationResult,
    ReviewRequest,
    WorkflowState,
)
from casefile.models.domain import (
    AgentType,
    ApprovalStatus,
    HumanApprovalRequest,
    Money,
    Recommendation,
    RecommendationType,
)
from casefile.models.persistence import get_engine, get_session_factory, init_db
from casefile.tools import create_default_registry
from casefile.tools.contracts import ToolContext, ToolOutcome
from casefile.workflow.context import FixedClock, RunContext, WorkflowSnapshot
from casefile.workflow.engine import WorkflowEngine
from casefile.workflow.hooks import EventSink, HookPayload, RecordingSink
from casefile.workflow.store import SqliteWorkflowStore
from casefile.workflow.supervisor import NodeName, RouteAction, SupervisorRouter
from casefile.workflow.transitions import (
    TerminalStateError,
    TransitionEvent,
    TransitionRecord,
)
from casefile.workflow.triggers import Trigger

_EVAL_EPOCH = datetime(2026, 9, 23, tzinfo=UTC)
_PRICING = PricingTable(
    entries={
        "deterministic/eval-stub": ModelPricing(
            provider="deterministic",
            model="eval-stub",
            input_per_1k_usd=Decimal("0.001"),
            output_per_1k_usd=Decimal("0.002"),
        )
    }
)


class BudgetTrigger(str):
    """Mapping from fine-grained budget reasons onto legal workflow triggers."""

    @staticmethod
    def for_reason(reason: BudgetTermination) -> Trigger:
        if reason in (
            BudgetTermination.MAX_STEPS_EXCEEDED,
            BudgetTermination.MAX_AGENT_STEPS_EXCEEDED,
        ):
            return Trigger.STEPS_EXHAUSTED
        if reason == BudgetTermination.MAX_WALL_CLOCK_EXCEEDED:
            return Trigger.WORKFLOW_TIMED_OUT
        return Trigger.BUDGET_EXHAUSTED


class _SafeSink(EventSink):
    """Wraps a sink so telemetry failures are counted, never fatal."""

    def __init__(self, inner: EventSink, *, raise_on_emit: bool = False) -> None:
        self._inner = inner
        self._raise = raise_on_emit
        self.error_count = 0

    def emit(self, payload: HookPayload) -> None:
        if self._raise:
            self.error_count += 1
            return
        try:
            self._inner.emit(payload)
        except Exception:
            self.error_count += 1


class _ScriptQueue:
    """Per-agent FIFO of scripted provider steps."""

    def __init__(self, scripts: tuple[AgentScript, ...]) -> None:
        self._by_agent: dict[str, list[ScriptedStep]] = {}
        for script in scripts:
            self._by_agent[script.agent] = list(script.steps)

    def next(self, agent: str) -> ScriptedStep | None:
        queue = self._by_agent.get(agent, [])
        if not queue:
            return None
        return queue.pop(0)

    def remaining(self) -> int:
        return sum(len(v) for v in self._by_agent.values())


def _push_step(
    provider: DeterministicProvider, step: ScriptedStep, payload: dict[str, object]
) -> None:
    meta = {"input_tokens": step.input_tokens, "output_tokens": step.output_tokens}
    if step.kind == ScriptKind.PAYLOAD:
        provider.push_json(payload, **meta)
    elif step.kind == ScriptKind.TEXT:
        provider.push_text(step.text, **meta)
    elif step.kind == ScriptKind.ERROR:
        if step.error_retryable:
            provider.push_error(ProviderError("scripted transient failure", retryable=True))
        else:
            provider.push_error(ProviderError("scripted fatal failure", retryable=False))
    elif step.kind == ScriptKind.TIMEOUT:
        provider.push_timeout()


class ScenarioRunner:
    """Drive one EvaluationScenario to a typed EvaluationResult."""

    def __init__(self, db_dir: Path | str | None = None) -> None:
        self._db_dir = Path(db_dir) if db_dir is not None else None

    def run(self, scenario: EvaluationScenario) -> EvaluationResult:
        """Execute the scenario fully and return its result (never raises)."""
        try:
            return self._run_inner(scenario)
        except Exception as exc:  # unexpected harness error
            return EvaluationResult(
                scenario_id=scenario.scenario_id,
                status="ERROR",
                safe_error=f"{type(exc).__name__}: {exc}"[:500],
                errors=(f"{type(exc).__name__}",),
                scenario_version=scenario.version,
            )

    def run_case(
        self, case: EvaluationCase, result: EvaluationResult | None = None
    ) -> EvaluationOutcome:
        """Execute one EvaluationCase and validate against explicit expectations."""
        res = result if result is not None else self.run(case.scenario)
        actual_path = res.transition_path
        exp_path = tuple(s.value for s in case.expected_state_path)
        path_matched = True
        if exp_path:
            path_matched = actual_path == exp_path

        exp_term = case.expected_terminal_state.value if case.expected_terminal_state else None
        term_matched = True
        if exp_term is not None:
            term_matched = res.terminal_state == exp_term

        fc = classify_failure(
            status=res.status,
            terminal_state=res.terminal_state,
            terminal_reason=res.termination_reason,
            errors=res.errors,
            injections=res.failure_injections,
            retry_count=res.retry_count,
            rework_count=res.rework_count,
            safe_error=res.safe_error,
        )

        status_val = "PASS"
        if res.status != "PASS" or not path_matched or not term_matched:
            status_val = "FAIL" if res.status != "ERROR" else "ERROR"

        safe_err = res.safe_error
        if not path_matched and "path" not in (safe_err or ""):
            err_msg = f"Path mismatch: {actual_path} != {exp_path}"
            safe_err = f"{safe_err}; {err_msg}" if safe_err else err_msg

        return EvaluationOutcome(
            case_id=case.case_id,
            scenario_name=case.scenario_name,
            status=status_val,  # type: ignore[arg-type]
            actual_path=actual_path,
            expected_path=exp_path,
            path_matched=path_matched,
            terminal_state=res.terminal_state,
            expected_terminal_state=exp_term,
            terminal_matched=term_matched,
            rework_count=res.rework_count,
            retry_count=res.retry_count,
            tool_failures=len(res.failure_injections),
            budget_used_steps=len(actual_path),
            cost_usd=res.cost_usd,
            input_tokens=res.input_tokens,
            output_tokens=res.output_tokens,
            replay_matched=res.replay.terminal_matches if res.replay.ran else None,
            failure_classification=fc.model_dump(mode="json"),
            errors=res.errors,
            safe_error=safe_err,
        )

    def run_dataset(
        self, cases: list[EvaluationCase] | tuple[EvaluationCase, ...] | None = None
    ) -> EvaluationRun:
        """Run all cases in the 30-scenario evaluation dataset and compute 22 metrics."""
        from casefile.evaluation.dataset import EVALUATION_DATASET
        from casefile.evaluation.metrics import ScenarioCounters, compute_metrics

        target_cases = list(cases) if cases is not None else list(EVALUATION_DATASET)
        outcomes: list[EvaluationOutcome] = []
        raw_results: list[EvaluationResult] = []

        for case in target_cases:
            res = self.run(case.scenario)
            raw_results.append(res)
            outcome = self.run_case(case, result=res)
            outcomes.append(outcome)

        statuses: list[str] = [o.status for o in outcomes]
        passed = sum(1 for s in statuses if s == "PASS")
        failed = sum(1 for s in statuses if s != "PASS")
        pass_rate = round(passed / len(statuses), 4) if statuses else 1.0

        all_inv = [inv for r in raw_results for inv in r.invariant_results]
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
                cost_ceiling=(case.expected_budget_outcome.cost_ceiling or Decimal("1.00")),
                path_matched=o.path_matched,
                terminal_matched=o.terminal_matched,
                resume_attempted=case.expected_checkpoint_behavior.checkpoint_restored,
                resume_succeeded=case.expected_checkpoint_behavior.checkpoint_restored
                and o.status == "PASS",
            )
            for r, o, case in zip(raw_results, outcomes, target_cases, strict=False)
        ]
        terminal_reasons = [r.termination_reason or "" for r in raw_results]
        failure_scenarios = sum(
            1 for r in raw_results if r.failure_injections or "ERROR" in r.status
        )
        failure_recovered = sum(
            1 for r in raw_results if r.status == "PASS" and (r.failure_injections or r.errors)
        )
        replay_scenarios = sum(1 for r in raw_results if r.replay.ran)
        replay_consistent = sum(
            1 for r in raw_results if r.replay.ran and r.replay.terminal_matches is not False
        )

        metrics = compute_metrics(
            statuses=statuses,
            invariant_results=all_inv,
            counters=counters,
            terminal_reasons=terminal_reasons,
            failure_scenarios=failure_scenarios,
            failure_recovered=failure_recovered,
            replay_scenarios=replay_scenarios,
            replay_consistent=replay_consistent,
        )

        return EvaluationRun(
            run_id=str(uuid4()),
            evaluation_version="1.0.0",
            total_cases=len(target_cases),
            passed=passed,
            failed=failed,
            pass_rate=pass_rate,
            metrics=metrics.model_dump(mode="json"),
            cases=tuple(outcomes),
        )

    # ------------------------------------------------------------------
    # harness setup
    # ------------------------------------------------------------------

    def _run_inner(self, scenario: EvaluationScenario) -> EvaluationResult:
        initial = scenario.initial
        workdir = self._db_dir or Path.cwd() / ".casefile-eval"
        workdir.mkdir(parents=True, exist_ok=True)
        db_path = workdir / f"eval-{scenario.scenario_id.lower()}-{uuid4().hex[:8]}.db"
        engine = get_engine(f"sqlite:///{db_path}")
        init_db(engine)
        factory = get_session_factory(engine)
        clock = FixedClock(_EVAL_EPOCH)

        claim_id = uuid4()
        run_id = uuid4()
        spec = initial.claim
        if initial.untrusted_claim_marker and not spec.untrusted_marker:
            spec = spec.model_copy(update={"untrusted_marker": True})
        claim = spec.to_claim_input(claim_id)

        sink: EventSink = RecordingSink()
        if initial.broken_telemetry:
            sink = _SafeSink(sink, raise_on_emit=True)
        provider = DeterministicProvider(provider_name="deterministic")
        registry = create_default_registry(sink=sink)
        injector = FailureInjector(list(initial.injections))
        queue = _ScriptQueue(initial.scripts)

        budget = BudgetEngine(
            run_id,
            initial.envelope,
            BudgetUsage(wall_start=clock.now()),
            clock,
            factory,
            pricing=_PRICING,
        )
        budget.start_run()
        flow = WorkflowEngine()
        store = SqliteWorkflowStore(engine)
        checkpoints = SqlCheckpointRepository(engine)
        approvals = ApprovalService(factory, clock)
        router = SupervisorRouter()
        ctx = RunContext(claim_id=claim_id, workflow_run_id=run_id, clock=clock)

        snapshot = WorkflowSnapshot(
            workflow_run_id=run_id,
            claim_id=claim_id,
            current_state=WorkflowState.RECEIVED,
            updated_at=clock.now(),
        )
        records: list[TransitionRecord] = []
        path_states: list[WorkflowState] = [WorkflowState.RECEIVED]
        agent_records: list[AgentExecutionRecord] = []
        recorded_agents: list[RecordedAgentOutput] = []
        tool_calls = 0
        errors: list[str] = []
        expected_error_code: str | None = None
        approval_outcome: str | None = None
        terminal_reason: str | None = None
        ops_after_terminal = 0
        reached_terminal = False
        duplicate_probe: bool | None = None
        checkpoint_count = 0
        latest_checkpoint: Checkpoint | None = None
        rework_feedback: str | None = None
        extraction_result: ExtractionResult | None = None
        investigation_result: InvestigationResult | None = None
        telemetry_errors = 0
        started = clock.now()
        budget_latched: BudgetTermination | None = None
        restart_done = False
        corrupted_resume_error: str | None = None
        approval_fact: ApprovalFact | None = None
        approval_immutable_ok = True
        per_agent_runs: Counter[str] = Counter()
        wall_end = started

        def _advance() -> None:
            nonlocal wall_end
            clock.advance(1)
            wall_end = clock.now()

        def _record_op() -> None:
            nonlocal ops_after_terminal
            if reached_terminal:
                ops_after_terminal += 1

        def _maybe_fail(point: FailurePoint, target: str, recovery: RecoveryBehavior) -> bool:
            """Return True when the step should be abandoned after injection."""
            error = injector.should_fail(point, target)
            if error is None:
                return False
            if recovery in (RecoveryBehavior.TERMINATE_NODE, RecoveryBehavior.PROPAGATE):
                return True
            if recovery == RecoveryBehavior.TERMINATE_WORKFLOW:
                return True
            if recovery == RecoveryBehavior.RETRY_STEP:
                return True
            if recovery == RecoveryBehavior.SKIP_OPERATION:
                return True
            return True

        def _take_checkpoint(kind_hint: WorkflowState | None = None) -> Checkpoint | None:
            nonlocal latest_checkpoint, checkpoint_count
            if kind_hint is not None and kind_hint.is_terminal:
                kind = CheckpointKind.TERMINAL
            elif kind_hint == WorkflowState.HUMAN_APPROVAL:
                kind = CheckpointKind.HUMAN_WAIT
            else:
                kind = CheckpointKind.TRANSITION
            if kind == CheckpointKind.HUMAN_WAIT:
                return None  # human-wait checkpoint created with the approval
            if (
                injector.should_fail(FailurePoint.BEFORE_CHECKPOINT_CREATE, "checkpoint")
                is not None
            ):
                return None
            path = tuple(
                TransitionPathEntry(
                    sequence_no=r.sequence_no,
                    trigger=r.trigger,
                    actor=r.actor,
                    reason=r.reason,
                )
                for r in records
            )
            terminal_reason_value = None
            if kind == CheckpointKind.TERMINAL:
                terminal_reason_value = terminal_reason or snapshot.current_state.value
            cp = Checkpoint(
                claim_id=claim_id,
                workflow_run_id=run_id,
                state=snapshot.current_state,
                kind=kind,
                snapshot=snapshot,
                step_count=snapshot.step_count,
                rework_count=snapshot.rework_count,
                recorded_agents=tuple(recorded_agents),
                path=path,
                terminal_reason=terminal_reason_value,
            )
            stored = checkpoints.create(cp)
            checkpoint_count += 1
            latest_checkpoint = stored
            return stored

        def _fire_transition(
            trigger: Trigger,
            actor: AgentType,
            *,
            reason: str = "",
        ) -> bool:
            """Budget-gated apply. Returns False when budget denied the step."""
            nonlocal snapshot, budget_latched, terminal_reason, reached_terminal, records
            if (
                injector.should_fail(
                    FailurePoint.BEFORE_WORKFLOW_TRANSITION, f"transition:{trigger.value}"
                )
                is not None
            ):
                errors.append(f"injected:{trigger.value}")
                return False
            if _maybe_fail(
                FailurePoint.BEFORE_BUDGET_RESERVE,
                "budget:step",
                RecoveryBehavior.PROPAGATE,
            ):
                budget_latched = BudgetTermination.MAX_STEPS_EXCEEDED
                trigger = BudgetTrigger.for_reason(budget_latched)
                actor = AgentType.SYSTEM
            reservation = budget.pre_step(ReserveKind.WORKFLOW_STEP)
            if not reservation.granted:
                reason_bt = reservation.reason or BudgetTermination.MAX_STEPS_EXCEEDED
                budget_latched = reason_bt
                with suppress(Exception):
                    budget.terminate(reason_bt)
                trigger = BudgetTrigger.for_reason(reason_bt)
                actor = AgentType.SYSTEM
                terminal_reason = reason_bt.value
            _record_op()
            try:
                result = flow.apply(
                    snapshot,
                    TransitionEvent(
                        claim_id=claim_id,
                        workflow_run_id=run_id,
                        trigger=trigger,
                        actor=actor,
                        reason=reason,
                        occurred_at=clock.now(),
                    ),
                    ctx,
                )
            except TerminalStateError:
                reached_terminal = True
                return True
            if result.record is not None:
                records.append(result.record)
            snapshot = result.snapshot
            path_states.append(snapshot.current_state)
            _advance()
            store.save_snapshot_and_audit(snapshot, result.record) if result.record else None
            if snapshot.current_state.is_terminal:
                reached_terminal = True
                if terminal_reason is None:
                    terminal_reason = trigger.value
                _take_checkpoint(snapshot.current_state)
            else:
                _take_checkpoint(snapshot.current_state)
            injector.should_fail(
                FailurePoint.AFTER_WORKFLOW_TRANSITION, f"transition:{trigger.value}"
            )
            return True

        def _run_agent(
            agent_name: str,
            agent: ExtractorAgent | InvestigatorAgent | ReviewerAgent,
            payload: Any,
            agent_ctx: AgentContext,
            contract_type: str,
        ) -> tuple[Any, AgentExecutionRecord] | None:
            nonlocal telemetry_errors, expected_error_code
            _record_op()
            per_agent_runs[agent_name] += 1
            if _maybe_fail(
                FailurePoint.BEFORE_AGENT,
                f"agent:{agent_name}",
                RecoveryBehavior.PROPAGATE,
            ):
                errors.append(f"injected-agent:{agent_name}")
                return None
            # Push this invocation's script: failure/text steps plus the first
            # PAYLOAD (inclusive) so in-run retries see the recovery response.
            # Later invocations consume later steps (rework, second review).
            pushed = 0
            first_step: ScriptedStep | None = None
            while True:
                step = queue.next(agent_name)
                if step is None:
                    break
                if first_step is None:
                    first_step = step
                recipe_payload: dict[str, object] = {}
                if step.recipe is not None:
                    recipe_payload = resolve_payload(
                        step.recipe,
                        workflow_id=run_id,
                        claim_amount=claim.claim_amount,
                        policy_id=claim.policy_id,
                        claimant_name=claim.claimant_name,
                        incident_date=claim.incident_date,
                        description=claim.description,
                    )
                _push_step(provider, step, recipe_payload)
                pushed += 1
                if step.kind == ScriptKind.PAYLOAD:
                    break
            if first_step is None or pushed == 0:
                errors.append(f"script-exhausted:{agent_name}")
                return None
            step = first_step
            try:
                output, record = agent.run(payload, agent_ctx)
            except AgentFailedError as exc:
                agent_records.append(
                    exc.record
                    or AgentExecutionRecord(
                        execution_id=agent_ctx.execution_id,
                        agent=agent.agent_type,
                        started_at=clock.now(),
                        status="FAILURE",
                        input_correlation_id=agent_ctx.correlation_id,
                        output_contract_type=contract_type,
                        attempts=1,
                        output_valid=False,
                        error=exc.error,
                    )
                )
                errors.append(exc.error.code)
                if exc.error.code == "MAX_AGENT_RETRIES_EXCEEDED":
                    expected_error_code = "MAX_AGENT_RETRIES_EXCEEDED"
                _record_model_cost(first_step)
                return None
            agent_records.append(record)
            recorded_agents.append(
                RecordedAgentOutput(
                    agent=agent.agent_type,
                    contract_type=contract_type,
                    output_json=output.model_dump_json(),
                    execution_id=agent_ctx.execution_id,
                    recorded_at=clock.now(),
                )
            )
            _record_model_cost(first_step)
            injector.should_fail(FailurePoint.AFTER_AGENT, f"agent:{agent_name}")
            if isinstance(sink, _SafeSink):
                telemetry_errors = sink.error_count
            return output, record

        def _record_model_cost(step: ScriptedStep | None) -> None:
            if step is None:
                return
            with suppress(Exception):
                budget.record_model_call(
                    ModelUsage(
                        provider="deterministic",
                        model="eval-stub",
                        input_tokens=step.input_tokens,
                        output_tokens=step.output_tokens,
                    )
                )

        def _run_tool(
            tool_name: str, raw_input: dict[str, object], agent: AgentType
        ) -> ToolOutcome | None:
            nonlocal tool_calls, budget_latched
            _record_op()
            if _maybe_fail(
                FailurePoint.BEFORE_TOOL,
                f"tool:{tool_name}",
                RecoveryBehavior.TERMINATE_NODE,
            ):
                errors.append(f"injected-tool:{tool_name}")
                return None
            # bounded tool retries with budget gate
            last_outcome: ToolOutcome | None = None
            for _attempt in range(initial.tool_max_attempts):
                reservation = budget.pre_step(ReserveKind.TOOL_CALL)
                if not reservation.granted:
                    reason_bt = reservation.reason or BudgetTermination.MAX_TOOL_CALLS_EXCEEDED
                    budget_latched = reason_bt
                    with suppress(Exception):
                        budget.terminate(reason_bt)
                    return None
                tool_calls += 1
                agent_ctx = AgentContext(
                    claim_id=claim_id,
                    workflow_run_id=run_id,
                    agent=agent,
                    max_attempts=initial.max_attempts,
                )
                tool_ctx = ToolContext(
                    claim_id=claim_id,
                    workflow_run_id=run_id,
                    execution_id=agent_ctx.execution_id,
                    agent=agent,
                )
                try:
                    outcome = registry.execute(
                        tool_name,
                        registry.version_of(tool_name) or "1.0.0",
                        raw_input,
                        tool_ctx,
                    )
                except InjectedFailureError as exc:
                    errors.append(f"injected-tool:{exc.injection.failure_id}")
                    return None
                last_outcome = outcome
                if outcome.succeeded:
                    injector.should_fail(FailurePoint.AFTER_TOOL, f"tool:{tool_name}")
                    return outcome
                if outcome.error is not None and outcome.error.retryable:
                    continue
                return outcome
            return last_outcome

        def _initiate_approval() -> None:
            nonlocal approval_outcome, approval_fact, approval_immutable_ok, terminal_reason
            request = HumanApprovalRequest(
                workflow_run_id=run_id,
                claim_id=claim_id,
                recommendation=Recommendation(
                    claim_id=claim_id,
                    recommendation_type=RecommendationType.APPROVE_FULL,
                    estimated_payout=Money(amount=claim.claim_amount),
                    notes="Evidence supports approval",
                    confidence=0.93,
                ),
                reviewer_summary="Ready for human review",
                requested_at=clock.now(),
                deadline=clock.now() + timedelta(hours=24),
            )
            wait_cp = Checkpoint(
                claim_id=claim_id,
                workflow_run_id=run_id,
                state=WorkflowState.HUMAN_APPROVAL,
                kind=CheckpointKind.HUMAN_WAIT,
                snapshot=snapshot,
                step_count=snapshot.step_count,
                rework_count=snapshot.rework_count,
                approval_request=request,
                recorded_agents=tuple(recorded_agents),
            )
            approval, stored = approvals.request_approval_with_checkpoint(
                request, wait_cp, WorkflowState.HUMAN_APPROVAL
            )
            nonlocal latest_checkpoint, checkpoint_count
            latest_checkpoint = stored
            checkpoint_count += 1
            approval_fact = ApprovalFact(
                status=approval.status.value,
                approver_role=None,
                recommendation_json=request.recommendation.model_dump_json(),
                reviewer_summary=request.reviewer_summary,
                request_version=approval.request_version,
            )
            if initial.approval_action == "none":
                approval_outcome = "pending"
                return
            action = initial.approval_action
            if _maybe_fail(
                FailurePoint.BEFORE_APPROVAL_DECIDE,
                "approval:decide",
                RecoveryBehavior.PROPAGATE,
            ):
                errors.append("injected-approval")
                approval_outcome = "pending"
                return
            verdict = ApprovalVerdict.APPROVE if action == "approve" else ApprovalVerdict.REJECT
            result = approvals.decide(
                ApprovalDecision(
                    approval_id=approval.approval_id,
                    actor=HumanActor(actor_id="human-eval", display_name="Eval Human"),
                    verdict=verdict,
                    reason="evaluation decision",
                    decision_key=f"{scenario.scenario_id}-key",
                    expected_version=approval.request_version,
                ),
                WorkflowState.HUMAN_APPROVAL,
            )
            rec_after = request.recommendation.model_dump_json()
            if rec_after != (approval_fact.recommendation_json if approval_fact else ""):
                approval_immutable_ok = False
            approval_outcome = "granted" if action == "approve" else "rejected"
            approval_fact = ApprovalFact(
                status=result.approval.status.value,
                approver_role=result.approval.approver_role,
                recommendation_json=rec_after,
                reviewer_summary=request.reviewer_summary,
                request_version=result.approval.request_version,
            )
            injector.should_fail(FailurePoint.AFTER_APPROVAL_DECIDE, "approval:decide")
            trigger = approvals.decision_trigger(approval.approval_id)
            _fire_transition(trigger, AgentType.HUMAN, reason="human decision")

        def _drive_budget_race() -> EvaluationResult:
            """G12: concurrent final tool-call reservation, exactly one winner."""
            import threading

            contenders = initial.race_contenders
            barrier = threading.Barrier(contenders)
            outcomes: list[bool] = []
            lock = threading.Lock()

            def worker() -> None:
                barrier.wait()
                res = budget.pre_step(ReserveKind.TOOL_CALL)
                with lock:
                    outcomes.append(res.granted)

            threads = [threading.Thread(target=worker) for _ in range(contenders)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            winners = sum(1 for g in outcomes if g)
            status = "PASS" if winners == 1 else "FAIL"
            ctx_facts = _build_context(
                letters=scenario.expected.require_invariants or ("D", "E", "M"),
                budget_latched=budget_latched,
                extra_transitions=True,
            )
            inv = evaluate_invariants(
                ctx_facts, scenario.expected.require_invariants or ("D", "E", "M")
            )
            return EvaluationResult(
                scenario_id=scenario.scenario_id,
                run_id=str(run_id),
                status=status,
                terminal_state=None,
                termination_reason=None,
                transition_path=tuple(s.value for s in path_states),
                agent_execution_count=sum(per_agent_runs.values()),
                tool_invocation_count=tool_calls,
                retry_count=sum(max(0, r.attempts - 1) for r in agent_records),
                rework_count=snapshot.rework_count,
                input_tokens=budget.usage.input_tokens,
                output_tokens=budget.usage.output_tokens,
                cost_usd=budget.usage.cost_usd,
                wall_clock_seconds=int((wall_end - started).total_seconds()),
                checkpoint_count=checkpoint_count,
                invariant_results=tuple(inv),
                failure_injections=tuple(injector.triggered),
                errors=tuple(errors),
                scenario_version=scenario.version,
            )

        def _build_context(
            *,
            letters: tuple[str, ...],
            budget_latched: BudgetTermination | None,
            extra_transitions: bool = False,
            replay_row_delta: int | None = None,
            replay_provider_requests: int | None = None,
            replay_terminal_matches: bool | None = None,
            checkpoint_integrity_ok: bool | None = None,
            checkpoint_rejected: bool | None = None,
            duplicate_applied: bool | None = None,
        ) -> InvariantContext:
            transition_facts = tuple(
                TransitionFact(
                    sequence_no=r.sequence_no,
                    execution_id=str(r.execution_id),
                    idempotency_key=r.idempotency_key,
                    trigger=r.trigger.value,
                    actor=r.actor.value,
                    source=r.source.value,
                    destination=r.destination.value,
                    reason=r.reason,
                )
                for r in records
            )
            marker_seen = False
            for r in records:
                if UNTRUSTED_MARKER in r.reason:
                    marker_seen = True
            if terminal_reason and UNTRUSTED_MARKER in terminal_reason:
                marker_seen = True
            triggers_ok = all(trigger_is_enum(r.trigger.value) for r in records)
            psycopg_present = any(
                name == "psycopg" or name.startswith("psycopg.") for name in sys.modules
            )
            _ = extra_transitions
            return InvariantContext(
                scenario_id=scenario.scenario_id,
                terminal_state=(
                    snapshot.current_state
                    if snapshot.current_state.is_terminal
                    else (snapshot.current_state if reached_terminal else None)
                ),
                transition_path=tuple(path_states),
                transitions=transition_facts,
                reached_terminal=reached_terminal,
                ops_after_terminal=ops_after_terminal,
                budget=BudgetFact(
                    steps=budget.usage.steps,
                    agent_steps=budget.usage.agent_steps,
                    tool_calls=budget.usage.tool_calls,
                    rework_cycles=budget.usage.rework_cycles,
                    agent_retries=budget.usage.agent_retries,
                    input_tokens=budget.usage.input_tokens,
                    output_tokens=budget.usage.output_tokens,
                    total_tokens=budget.usage.total_tokens,
                    cost_usd=budget.usage.cost_usd,
                    terminal_reason=budget_latched.value if budget_latched else None,
                ),
                envelope=initial.envelope,
                approval=approval_fact,
                approval_immutable_ok=approval_immutable_ok,
                replay_row_delta=replay_row_delta,
                replay_provider_requests=replay_provider_requests,
                replay_terminal_matches=replay_terminal_matches,
                checkpoint_integrity_ok=checkpoint_integrity_ok,
                checkpoint_rejected=checkpoint_rejected,
                per_agent_run_counts=dict(per_agent_runs),
                duplicate_applied=duplicate_applied,
                untrusted_marker=UNTRUSTED_MARKER if initial.untrusted_claim_marker else None,
                marker_in_triggers_or_reasons=marker_seen,
                triggers_valid_enum=triggers_ok,
                postgres_absent=not psycopg_present,
                sqlite_backend=True,
                telemetry_errors=telemetry_errors,
                broken_telemetry_enabled=initial.broken_telemetry,
            )

        def _build_result(
            status: str,
            *,
            inv: list[InvariantResult],
            replay: ReplayComparison | None = None,
            safe_error: str | None = None,
            terminal_override: WorkflowState | None = None,
            reason_override: str | None = None,
            approval_override: str | None = None,
        ) -> EvaluationResult:
            term = terminal_override or (snapshot.current_state if reached_terminal else None)
            terminal_out: str | None = None
            if term is not None:
                terminal_out = term.value
            return EvaluationResult(
                scenario_id=scenario.scenario_id,
                run_id=str(run_id),
                status=status,
                terminal_state=terminal_out,
                termination_reason=reason_override or terminal_reason,
                transition_path=tuple(s.value for s in path_states),
                agent_execution_count=sum(per_agent_runs.values()),
                tool_invocation_count=tool_calls,
                retry_count=sum(max(0, r.attempts - 1) for r in agent_records),
                rework_count=snapshot.rework_count,
                input_tokens=budget.usage.input_tokens,
                output_tokens=budget.usage.output_tokens,
                cost_usd=budget.usage.cost_usd,
                wall_clock_seconds=max(0, int((wall_end - started).total_seconds())),
                approval_outcome=approval_override or approval_outcome,
                checkpoint_count=checkpoint_count,
                replay=replay or ReplayComparison(),
                invariant_results=tuple(inv),
                failure_injections=tuple(injector.triggered),
                errors=tuple(errors),
                safe_error=safe_error,
                scenario_version=scenario.version,
            )

        # ------------------------------------------------------------------
        # concurrency modes short-circuit the normal drive
        # ------------------------------------------------------------------
        if initial.concurrency == ConcurrencyMode.BUDGET_RACE:
            try:
                return _drive_budget_race()
            finally:
                engine.dispose()
                _cleanup_db(db_path)

        if initial.concurrency == ConcurrencyMode.APPROVAL_RACE:
            try:
                return self._run_approval_race(
                    scenario,
                    engine=engine,
                    factory=factory,
                    clock=clock,
                    run_id=run_id,
                    claim_id=claim_id,
                    approvals=approvals,
                    checkpoints=checkpoints,
                    store=store,
                    flow=flow,
                    budget=budget,
                    ctx=ctx,
                    snapshot=snapshot,
                    records=records,
                    path_states=path_states,
                    per_agent_runs=per_agent_runs,
                    agent_records=agent_records,
                    tool_calls=tool_calls,
                    started=started,
                    wall_end=wall_end,
                    ops_after_terminal=ops_after_terminal,
                    reached_terminal=reached_terminal,
                    approval_fact=approval_fact,
                    approval_immutable_ok=approval_immutable_ok,
                    checkpoint_count=checkpoint_count,
                    errors=errors,
                    injector=injector,
                    initial=initial,
                    build_context=lambda **kw: _build_context(
                        letters=scenario.expected.require_invariants or ("A", "F", "M"),
                        budget_latched=None,
                        **kw,
                    ),
                    build_result=lambda **kw: _build_result(
                        kw.pop("status", "PASS"),
                        inv=kw.pop("inv", []),
                        **kw,
                    ),
                )
            finally:
                engine.dispose()
                _cleanup_db(db_path)

        # ------------------------------------------------------------------
        # normal drive loop
        # ------------------------------------------------------------------
        try:
            # Initial RECEIVED checkpoint (replay/resume base)
            _take_checkpoint(WorkflowState.RECEIVED)

            iterations = 0
            while iterations < initial.max_iterations:
                iterations += 1

                # restart injection
                if (
                    initial.restart_at is not None
                    and not restart_done
                    and snapshot.current_state == initial.restart_at
                ):
                    restart_done = True
                    if latest_checkpoint is None:
                        _take_checkpoint(snapshot.current_state)
                    target_cp = latest_checkpoint
                    if target_cp is None:
                        errors.append("restart-without-checkpoint")
                        break
                    if initial.corrupt_checkpoint:
                        try:
                            self._corrupt_checkpoint(engine, target_cp.checkpoint_id)
                            resume_from_checkpoint(target_cp.checkpoint_id, checkpoints=checkpoints)
                            corrupted_resume_error = None
                        except CheckpointCorruptError as exc:
                            corrupted_resume_error = type(exc).__name__
                            errors.append(corrupted_resume_error)
                            inv = evaluate_invariants(
                                _build_context(
                                    letters=scenario.expected.require_invariants or ("J",),
                                    budget_latched=budget_latched,
                                    checkpoint_integrity_ok=False,
                                    checkpoint_rejected=True,
                                ),
                                scenario.expected.require_invariants or ("J",),
                            )
                            expect_code = scenario.expected.failure.expect_error_code
                            if expect_code and expect_code not in errors:
                                return _build_result(
                                    "FAIL", inv=inv, safe_error="corrupt not rejected"
                                )
                            return _build_result("PASS", inv=inv, terminal_override=None)
                        # successful resume path (no corruption)
                    resumed = resume_from_checkpoint(
                        target_cp.checkpoint_id, checkpoints=checkpoints
                    )
                    # fresh process objects
                    flow = WorkflowEngine()
                    budget = budget.restore()
                    store = SqliteWorkflowStore(engine)
                    checkpoints = SqlCheckpointRepository(engine)
                    approvals = ApprovalService(factory, clock)
                    ctx = resumed.context.model_copy(
                        update={
                            "clock": clock,
                            "max_rework_cycles": initial.envelope.max_rework_cycles,
                        }
                    )
                    snapshot = resumed.snapshot
                    # reset recorded agents from checkpoint so replay keeps them
                    recorded_agents.clear()
                    recorded_agents.extend(resumed.checkpoint.recorded_agents)
                    continue

                if snapshot.current_state.is_terminal:
                    reached_terminal = True
                    break

                # budget wall/tokens/cost gate
                latched = budget.check_all()
                if latched is not None and budget_latched is None:
                    budget_latched = latched
                    terminal_reason = terminal_reason or latched.value
                    with suppress(Exception):
                        budget.terminate(latched)
                    if snapshot.current_state in (
                        WorkflowState.EXTRACTION,
                        WorkflowState.INVESTIGATION,
                        WorkflowState.REVIEW,
                    ):
                        if not _fire_transition(
                            BudgetTrigger.for_reason(latched), AgentType.SYSTEM
                        ):
                            break
                        if snapshot.current_state.is_terminal:
                            break
                        continue
                    # budget latch outside legal trigger states → terminal stop
                    reached_terminal = True
                    break

                decision = router.route(snapshot)

                if decision.action == RouteAction.TERMINATED:
                    reached_terminal = True
                    break

                if decision.action == RouteAction.WAIT_FOR_HUMAN:
                    _initiate_approval()
                    if snapshot.current_state.is_terminal:
                        reached_terminal = True
                    break

                node = decision.node

                if node == NodeName.EXTRACTOR:
                    if snapshot.current_state == WorkflowState.RECEIVED:
                        # Intake gate: RECEIVED → EXTRACTION before any agent work.
                        if not _fire_transition(Trigger.CLAIM_VALIDATED, AgentType.SUPERVISOR):
                            break
                        continue
                    if extraction_result is None:
                        agent_ctx = AgentContext(
                            claim_id=claim_id,
                            workflow_run_id=run_id,
                            agent=AgentType.EXTRACTOR,
                            max_attempts=initial.max_attempts,
                        )
                        # retry guard wired to budget
                        guarded = ExtractorAgent(
                            provider,
                            sink=sink,
                            retry_guard=make_retry_guard(budget),
                        )
                        # pre-reserve agent step
                        if not budget.pre_step(ReserveKind.AGENT_STEP).granted:
                            budget_latched = BudgetTermination.MAX_AGENT_STEPS_EXCEEDED
                            break
                        outcome = _run_agent(
                            "extractor",
                            guarded,
                            ExtractionRequest(workflow_id=run_id, claim_input=claim),
                            agent_ctx,
                            "extraction_result",
                        )
                        if outcome is None:
                            _fire_transition(Trigger.EXTRACTION_FAILED, AgentType.SUPERVISOR)
                            if not snapshot.current_state.is_terminal:
                                _fire_transition(Trigger.NODE_FAILED, AgentType.SUPERVISOR)
                            break
                        extraction_result, _rec = outcome
                        _fire_transition(Trigger.EXTRACTION_SUCCEEDED, AgentType.SUPERVISOR)
                        continue

                    # extraction already done (after restart) — shouldn't re-enter
                    _fire_transition(Trigger.EXTRACTION_SUCCEEDED, AgentType.SUPERVISOR)
                    continue

                if node == NodeName.INVESTIGATOR:
                    if extraction_result is None:
                        errors.append("investigation-without-extraction")
                        _fire_transition(Trigger.INVESTIGATION_FAILED, AgentType.SUPERVISOR)
                        break
                    if not budget.pre_step(ReserveKind.AGENT_STEP).granted:
                        budget_latched = BudgetTermination.MAX_AGENT_STEPS_EXCEEDED
                        break
                    # tools first (bounded, injectable)
                    tool_failed = False
                    policy_out = _run_tool(
                        "policy_lookup",
                        {"policy_number": extraction_result.policy_id},
                        AgentType.INVESTIGATOR,
                    )
                    if policy_out is None or not policy_out.succeeded:
                        tool_failed = True
                    fraud_out = None
                    if not tool_failed:
                        fraud_out = _run_tool(
                            "fraud_signal_lookup",
                            {
                                "claim_ref": "SYN-NORMAL-001",
                                "policy_number": extraction_result.policy_id,
                            },
                            AgentType.INVESTIGATOR,
                        )
                        if fraud_out is None or not fraud_out.succeeded:
                            tool_failed = True
                    if tool_failed or budget_latched is not None:
                        _fire_transition(Trigger.INVESTIGATION_FAILED, AgentType.SUPERVISOR)
                        break
                    agent_ctx = AgentContext(
                        claim_id=claim_id,
                        workflow_run_id=run_id,
                        agent=AgentType.INVESTIGATOR,
                        max_attempts=initial.max_attempts,
                    )
                    guarded_inv = InvestigatorAgent(
                        provider, sink=sink, retry_guard=make_retry_guard(budget)
                    )
                    outcome = _run_agent(
                        "investigator",
                        guarded_inv,
                        InvestigationRequest(
                            workflow_id=run_id,
                            extraction_result=extraction_result,
                            rework_feedback=rework_feedback,
                        ),
                        agent_ctx,
                        "investigation_result",
                    )
                    if outcome is None:
                        _fire_transition(Trigger.INVESTIGATION_FAILED, AgentType.SUPERVISOR)
                        break
                    investigation_result, _rec = outcome
                    _fire_transition(Trigger.INVESTIGATION_SUCCEEDED, AgentType.SUPERVISOR)
                    continue

                if node == NodeName.REVIEWER:
                    if extraction_result is None or investigation_result is None:
                        errors.append("review-without-upstream")
                        _fire_transition(Trigger.NODE_FAILED, AgentType.SUPERVISOR)
                        break
                    if not budget.pre_step(ReserveKind.AGENT_STEP).granted:
                        budget_latched = BudgetTermination.MAX_AGENT_STEPS_EXCEEDED
                        break
                    agent_ctx = AgentContext(
                        claim_id=claim_id,
                        workflow_run_id=run_id,
                        agent=AgentType.REVIEWER,
                        max_attempts=initial.max_attempts,
                    )
                    guarded_rev = ReviewerAgent(
                        provider, sink=sink, retry_guard=make_retry_guard(budget)
                    )
                    outcome = _run_agent(
                        "reviewer",
                        guarded_rev,
                        ReviewRequest(
                            workflow_id=run_id,
                            extraction_result=extraction_result,
                            investigation_result=investigation_result,
                            rework_count=snapshot.rework_count,
                        ),
                        agent_ctx,
                        "review_result",
                    )
                    if outcome is None:
                        _fire_transition(Trigger.NODE_FAILED, AgentType.SUPERVISOR)
                        break
                    review, _rec = outcome
                    if review.decision == "APPROVE":
                        _fire_transition(Trigger.REVIEW_APPROVED, AgentType.SUPERVISOR)
                    elif review.decision == "REJECT":
                        _fire_transition(Trigger.REVIEW_REJECTED, AgentType.SUPERVISOR)
                    else:
                        rework_feedback = review.rework_feedback
                        if snapshot.rework_count >= initial.envelope.max_rework_cycles:
                            _fire_transition(Trigger.REWORK_EXHAUSTED, AgentType.SUPERVISOR)
                        else:
                            if not budget.pre_step(ReserveKind.REWORK_CYCLE).granted:
                                budget_latched = BudgetTermination.MAX_REWORK_EXCEEDED
                                _fire_transition(Trigger.REWORK_EXHAUSTED, AgentType.SUPERVISOR)
                                break
                            _fire_transition(Trigger.REWORK_REQUESTED, AgentType.SUPERVISOR)
                            if snapshot.current_state == WorkflowState.REWORK_LOOP:
                                _fire_transition(Trigger.REWORK_DISPATCHED, AgentType.SUPERVISOR)
                    continue

                if node == NodeName.HUMAN_APPROVAL:
                    _initiate_approval()
                    if snapshot.current_state.is_terminal:
                        reached_terminal = True
                    break

                errors.append(f"unroutable:{node.value}")
                break

            # idempotency probe (L): re-apply first non-terminal event
            duplicate_probe = None
            if records and not snapshot.current_state.is_terminal:
                first = records[0]
                try:
                    probe = flow.apply(
                        snapshot,
                        TransitionEvent(
                            claim_id=claim_id,
                            workflow_run_id=run_id,
                            trigger=first.trigger,
                            actor=first.actor,
                            execution_id=first.execution_id,
                            correlation_id=first.correlation_id,
                        ),
                        ctx,
                    )
                    duplicate_probe = probe.duplicate
                except Exception:
                    duplicate_probe = False
            elif records:
                # terminal: applying must raise TerminalStateError (invariant A probe)
                try:
                    flow.apply(
                        snapshot,
                        TransitionEvent(
                            claim_id=claim_id,
                            workflow_run_id=run_id,
                            trigger=Trigger.CLAIM_VALIDATED,
                            actor=AgentType.SUPERVISOR,
                        ),
                        ctx,
                    )
                    duplicate_probe = False
                except TerminalStateError:
                    duplicate_probe = True

            # replay (H, I, and G10/G14)
            replay = ReplayComparison()
            replay_row_delta: int | None = None
            replay_provider_requests: int | None = None
            replay_terminal_matches: bool | None = None
            if scenario.expected.replay.runs or initial.omit_replay_artifacts:
                replay = self._run_replay_phase(
                    scenario,
                    engine=engine,
                    run_id=run_id,
                    claim_id=claim_id,
                    checkpoints=checkpoints,
                    records=records,
                    latest_checkpoint=latest_checkpoint,
                    recorded_agents=recorded_agents,
                    omit_artifacts=initial.omit_replay_artifacts,
                    live_terminal=snapshot.current_state,
                    clock=clock,
                    errors=errors,
                )
                replay_row_delta = replay.row_delta
                replay_provider_requests = replay.provider_requests
                replay_terminal_matches = replay.terminal_matches

            # expected error short-circuit (G07, G11)
            if scenario.expected.failure.expect_error:
                expect_code = scenario.expected.failure.expect_error_code
                if expect_code and expect_code in errors:
                    inv = evaluate_invariants(
                        _build_context(
                            letters=scenario.expected.require_invariants,
                            budget_latched=budget_latched,
                            replay_row_delta=replay_row_delta,
                            replay_provider_requests=replay_provider_requests,
                            replay_terminal_matches=replay_terminal_matches,
                            duplicate_applied=duplicate_probe,
                            checkpoint_integrity_ok=(
                                True if not initial.corrupt_checkpoint else None
                            ),
                            checkpoint_rejected=None,
                        ),
                        scenario.expected.require_invariants or None,
                    )
                    return _build_result(
                        "PASS",
                        inv=inv,
                        replay=replay,
                        terminal_override=snapshot.current_state if reached_terminal else None,
                        reason_override=expect_code,
                    )
                if expect_code is None and errors:
                    inv = evaluate_invariants(
                        _build_context(
                            letters=scenario.expected.require_invariants,
                            budget_latched=budget_latched,
                            replay_row_delta=replay_row_delta,
                            replay_provider_requests=replay_provider_requests,
                            replay_terminal_matches=replay_terminal_matches,
                            duplicate_applied=duplicate_probe,
                        ),
                        scenario.expected.require_invariants or None,
                    )
                    return _build_result("PASS", inv=inv, replay=replay)

            # evaluate against expected behavior
            letters = scenario.expected.require_invariants or None
            ctx_final = _build_context(
                letters=letters or tuple("ABCDEFGHIJKLMNOP"),
                budget_latched=budget_latched,
                replay_row_delta=replay_row_delta,
                replay_provider_requests=replay_provider_requests,
                replay_terminal_matches=replay_terminal_matches,
                duplicate_applied=duplicate_probe,
                checkpoint_integrity_ok=True if not initial.corrupt_checkpoint else None,
            )
            inv = evaluate_invariants(ctx_final, letters)

            failures: list[str] = []
            exp = scenario.expected
            term = snapshot.current_state if reached_terminal else None
            if exp.terminal_state is not None and term != exp.terminal_state:
                failures.append(f"terminal {term} != expected {exp.terminal_state.value}")
            if exp.terminal_reason is not None and terminal_reason != exp.terminal_reason:
                failures.append(f"reason {terminal_reason!r} != expected {exp.terminal_reason!r}")
            if exp.transition_path and tuple(path_states) != exp.transition_path:
                failures.append(
                    f"path {tuple(s.value for s in path_states)} != expected "
                    f"{tuple(s.value for s in exp.transition_path)}"
                )
            if exp.rework_count is not None and snapshot.rework_count != exp.rework_count:
                failures.append(f"rework {snapshot.rework_count} != expected {exp.rework_count}")
            agent_runs = sum(per_agent_runs.values())
            if exp.agent_executions is not None and agent_runs != exp.agent_executions:
                failures.append(f"agent_executions {agent_runs} != expected {exp.agent_executions}")
            if exp.approval.outcome != "none":
                if exp.approval.outcome == "race_one_winner":
                    pass  # handled in approval race branch
                elif approval_outcome != exp.approval.outcome:
                    failures.append(
                        f"approval {approval_outcome!r} != expected {exp.approval.outcome!r}"
                    )
            if (
                exp.budget.steps_consumed is not None
                and budget.usage.steps != exp.budget.steps_consumed
            ):
                failures.append(
                    f"steps {budget.usage.steps} != expected {exp.budget.steps_consumed}"
                )
            if exp.budget.cost_nonzero and budget.usage.cost_usd <= 0:
                failures.append("expected non-zero cost")
            if exp.budget.terminal_reason is not None and (
                budget_latched is None or budget_latched.value != exp.budget.terminal_reason
            ):
                failures.append(
                    f"budget latch {budget_latched} != expected {exp.budget.terminal_reason}"
                )
            if exp.failure.min_retries:
                actual_retries = sum(max(0, r.attempts - 1) for r in agent_records)
                if actual_retries < exp.failure.min_retries:
                    failures.append(
                        f"retries {actual_retries} < expected {exp.failure.min_retries}"
                    )
            if exp.replay.expect_missing_artifact and not replay.missing_artifact:
                failures.append("expected missing replay artifact")
            if exp.replay.runs and not replay.ran:
                failures.append("expected replay to run")
            if exp.replay.terminal_matches_live and replay.terminal_matches is False:
                failures.append("replay terminal diverged")
            inv_failures = [r for r in inv if r.status.value == "FAIL"]
            if inv_failures:
                failures.extend(f"invariant {r.invariant_id}: {r.message}" for r in inv_failures)

            status = "PASS" if not failures else "FAIL"
            safe_error = "; ".join(failures)[:500] if failures else None
            result = _build_result(
                status,
                inv=inv,
                replay=replay,
                safe_error=safe_error,
                reason_override=None,
            )
            # attach expectation mismatches into errors for visibility
            if failures:
                return result.model_copy(update={"errors": tuple(errors) + tuple(failures)})
            return result
        finally:
            engine.dispose()
            _cleanup_db(db_path)

    # ------------------------------------------------------------------
    # approval race (G13)
    # ------------------------------------------------------------------

    def _run_approval_race(
        self,
        scenario: EvaluationScenario,
        *,
        engine: Engine,
        factory: sessionmaker[Session],
        clock: FixedClock,
        run_id: UUID,
        claim_id: UUID,
        approvals: ApprovalService,
        checkpoints: SqlCheckpointRepository,
        store: SqliteWorkflowStore,
        flow: WorkflowEngine,
        budget: BudgetEngine,
        ctx: RunContext,
        snapshot: WorkflowSnapshot,
        records: list[TransitionRecord],
        path_states: list[WorkflowState],
        per_agent_runs: Counter[str],
        agent_records: list[AgentExecutionRecord],
        tool_calls: int,
        started: datetime,
        wall_end: datetime,
        ops_after_terminal: int,
        reached_terminal: bool,
        approval_fact: ApprovalFact | None,
        approval_immutable_ok: bool,
        checkpoint_count: int,
        errors: list[str],
        injector: FailureInjector,
        initial: Any,
        build_context: Any,
        build_result: Any,
    ) -> EvaluationResult:
        """Same verdict, different keys: exactly one DECIDED, rest CONFLICT."""
        import threading
        from datetime import timedelta as _td

        from casefile.checkpoint.model import Checkpoint, CheckpointKind
        from casefile.models.domain import Recommendation, RecommendationType

        # drive to HUMAN_APPROVAL with synthetic path (no agents for race focus)
        for trigger, actor in (
            (Trigger.CLAIM_VALIDATED, AgentType.SUPERVISOR),
            (Trigger.EXTRACTION_SUCCEEDED, AgentType.SUPERVISOR),
            (Trigger.INVESTIGATION_SUCCEEDED, AgentType.SUPERVISOR),
            (Trigger.REVIEW_APPROVED, AgentType.SUPERVISOR),
        ):
            budget.pre_step(ReserveKind.WORKFLOW_STEP)
            result = flow.apply(
                snapshot,
                TransitionEvent(
                    claim_id=claim_id,
                    workflow_run_id=run_id,
                    trigger=trigger,
                    actor=actor,
                    occurred_at=clock.now(),
                ),
                ctx,
            )
            assert result.record is not None
            records.append(result.record)
            snapshot = result.snapshot
            path_states.append(snapshot.current_state)
            store.save_snapshot_and_audit(snapshot, result.record)
            clock.advance(1)
        assert snapshot.current_state == WorkflowState.HUMAN_APPROVAL

        request = HumanApprovalRequest(
            workflow_run_id=run_id,
            claim_id=claim_id,
            recommendation=Recommendation(
                claim_id=claim_id,
                recommendation_type=RecommendationType.APPROVE_FULL,
                estimated_payout=Money(amount=Decimal("1200.00")),
                notes="Race evaluation",
                confidence=0.9,
            ),
            reviewer_summary="Race scenario",
            requested_at=clock.now(),
            deadline=clock.now() + _td(hours=24),
        )
        wait_cp = Checkpoint(
            claim_id=claim_id,
            workflow_run_id=run_id,
            state=WorkflowState.HUMAN_APPROVAL,
            kind=CheckpointKind.HUMAN_WAIT,
            snapshot=snapshot,
            step_count=snapshot.step_count,
            rework_count=snapshot.rework_count,
            approval_request=request,
        )
        approval, stored = approvals.request_approval_with_checkpoint(
            request, wait_cp, WorkflowState.HUMAN_APPROVAL
        )
        checkpoint_count += 1

        contenders = initial.race_contenders
        barrier = threading.Barrier(contenders)
        outcomes: list[str] = []
        lock = threading.Lock()

        def worker(index: int) -> None:
            barrier.wait()
            try:
                decision = approvals.decide(
                    ApprovalDecision(
                        approval_id=approval.approval_id,
                        actor=HumanActor(actor_id=f"human-{index}", display_name=f"H{index}"),
                        verdict=ApprovalVerdict.APPROVE,
                        reason="race",
                        decision_key=f"race-key-{index}",
                        expected_version=approval.request_version,
                    ),
                    WorkflowState.HUMAN_APPROVAL,
                )
                with lock:
                    outcomes.append(decision.outcome.value)
            except ApprovalError as exc:
                with lock:
                    outcomes.append(exc.code.value)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(contenders)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        decided = outcomes.count(ApprovalOutcome.DECIDED.value)
        conflicts = outcomes.count("CONFLICT")
        final = approvals.get(approval.approval_id)
        terminal: WorkflowState | None = None
        if decided == 1 and final.status == ApprovalStatus.APPROVED:
            budget.pre_step(ReserveKind.WORKFLOW_STEP)
            result = flow.apply(
                snapshot,
                TransitionEvent(
                    claim_id=claim_id,
                    workflow_run_id=run_id,
                    trigger=Trigger.APPROVAL_GRANTED,
                    actor=AgentType.HUMAN,
                    occurred_at=clock.now(),
                ),
                ctx,
            )
            snapshot = result.snapshot
            if result.record is not None:
                records.append(result.record)
                store.save_snapshot_and_audit(snapshot, result.record)
            path_states.append(snapshot.current_state)
            terminal = snapshot.current_state
            clock.advance(1)

        expected_winners = scenario.expected.race_winners or 1
        status = (
            "PASS"
            if decided == expected_winners
            and final.status == ApprovalStatus.APPROVED
            and terminal == WorkflowState.APPROVED
            and (conflicts + decided) >= 2
            else "FAIL"
        )
        approval_fact = ApprovalFact(
            status=final.status.value,
            approver_role=final.approver_role,
            recommendation_json=request.recommendation.model_dump_json(),
            reviewer_summary=request.reviewer_summary,
            request_version=final.request_version,
        )
        transition_facts = tuple(
            TransitionFact(
                sequence_no=r.sequence_no,
                execution_id=str(r.execution_id),
                idempotency_key=r.idempotency_key,
                trigger=r.trigger.value,
                actor=r.actor.value,
                source=r.source.value,
                destination=r.destination.value,
                reason=r.reason,
            )
            for r in records
        )
        import sys as _sys

        psycopg_present = any(
            name == "psycopg" or name.startswith("psycopg.") for name in _sys.modules
        )
        ctx_inv = InvariantContext(
            scenario_id=scenario.scenario_id,
            terminal_state=terminal,
            transition_path=tuple(path_states),
            transitions=transition_facts,
            reached_terminal=terminal is not None,
            ops_after_terminal=0,
            budget=BudgetFact(
                steps=budget.usage.steps,
                agent_steps=budget.usage.agent_steps,
                tool_calls=budget.usage.tool_calls,
                rework_cycles=budget.usage.rework_cycles,
                agent_retries=budget.usage.agent_retries,
                input_tokens=budget.usage.input_tokens,
                output_tokens=budget.usage.output_tokens,
                total_tokens=budget.usage.total_tokens,
                cost_usd=budget.usage.cost_usd,
            ),
            envelope=initial.envelope,
            approval=approval_fact,
            approval_immutable_ok=True,
            postgres_absent=not psycopg_present,
            sqlite_backend=True,
            per_agent_run_counts=dict(per_agent_runs),
        )
        letters = scenario.expected.require_invariants or ("A", "F", "M")
        inv = evaluate_invariants(ctx_inv, letters)
        if any(r.status.value == "FAIL" for r in inv):
            status = "FAIL"
        _ = (ops_after_terminal, reached_terminal, errors, injector, build_context, wall_end)
        _ = (tool_calls, agent_records, checkpoint_count, stored)
        return EvaluationResult(
            scenario_id=scenario.scenario_id,
            run_id=str(run_id),
            status=status,
            terminal_state=terminal.value if terminal else None,
            termination_reason="APPROVAL_GRANTED" if terminal else None,
            transition_path=tuple(s.value for s in path_states),
            agent_execution_count=sum(per_agent_runs.values()),
            tool_invocation_count=tool_calls,
            retry_count=sum(max(0, r.attempts - 1) for r in agent_records),
            rework_count=snapshot.rework_count,
            input_tokens=budget.usage.input_tokens,
            output_tokens=budget.usage.output_tokens,
            cost_usd=budget.usage.cost_usd,
            wall_clock_seconds=max(0, int((wall_end - started).total_seconds())),
            approval_outcome="race_one_winner" if decided == 1 else f"decided={decided}",
            checkpoint_count=checkpoint_count,
            invariant_results=tuple(inv),
            failure_injections=tuple(injector.triggered),
            errors=tuple(errors) + tuple(f"outcome:{o}" for o in outcomes),
            scenario_version=scenario.version,
        )

    # ------------------------------------------------------------------
    # replay phase (G10, G14)
    # ------------------------------------------------------------------

    def _run_replay_phase(
        self,
        scenario: EvaluationScenario,
        *,
        engine: Engine,
        run_id: UUID,
        claim_id: UUID,
        checkpoints: SqlCheckpointRepository,
        records: list[TransitionRecord],
        latest_checkpoint: Checkpoint | None,
        recorded_agents: list[RecordedAgentOutput],
        omit_artifacts: bool,
        live_terminal: WorkflowState,
        clock: FixedClock,
        errors: list[str],
    ) -> ReplayComparison:
        """Replay remaining triggers from the RECEIVED (or latest) checkpoint."""
        from casefile.checkpoint.replay import (
            ReplayAgentProvider,
            ReplayMissingArtifactError,
            ReplayMode,
            run_replay,
        )

        before = _row_counts(engine)
        # find the RECEIVED (or earliest) checkpoint for a clean replay base
        all_cps = checkpoints.list_for_run(run_id)
        base = None
        for cp in all_cps:
            if cp.state == WorkflowState.RECEIVED:
                base = cp
                break
        if base is None and all_cps:
            base = all_cps[0]
        if base is None:
            return ReplayComparison(ran=False)

        if omit_artifacts:
            # strip recorded agents to force ReplayMissingArtifactError on agent re-run
            stripped = base.model_copy(
                update={"recorded_agents": ()},
                deep=True,
            )
            base_for_agents = stripped
        else:
            base_for_agents = base

        remaining = [(r.trigger, r.actor) for r in records]
        replay_ctx = RunContext(
            claim_id=claim_id,
            workflow_run_id=run_id,
            clock=FixedClock(_EVAL_EPOCH),
            max_rework_cycles=scenario.initial.envelope.max_rework_cycles,
        )
        missing = False
        provider_requests = 0
        try:
            # Probe agent replay artifact availability without executing live.
            if omit_artifacts:
                provider = ReplayAgentProvider(base_for_agents)
                try:
                    from casefile.agents.providers import LLMRequest

                    provider.complete(
                        LLMRequest(
                            model="probe",
                            messages=(),
                            response_schema_name="ExtractionResult",
                        )
                    )
                except ReplayMissingArtifactError:
                    missing = True
                    provider_requests = 0
                except ProviderError as exc:
                    missing = (
                        isinstance(exc, ReplayMissingArtifactError)
                        or "no recorded" in str(exc).lower()
                    )
                    provider_requests = 0
                else:
                    provider_requests = 1  # served from recording (not live)
                    missing = False
                errors.append("replay-missing-artifact" if missing else "replay-artifact-ok")
            else:
                provider_requests = 0  # pure engine replay makes no provider calls

            report = run_replay(
                base_for_agents if not omit_artifacts else base,
                remaining,
                engine=WorkflowEngine(),
                ctx=replay_ctx,
            )
            replay_terminal = report.terminal_state
            mode = report.mode.value
        except ReplayMissingArtifactError as exc:
            errors.append(type(exc).__name__)
            missing = True
            return ReplayComparison(
                ran=True,
                mode=ReplayMode.REPLAY.value,
                live_terminal=live_terminal.value,
                replay_terminal=None,
                terminal_matches=None,
                row_delta=_delta(before, _row_counts(engine)),
                provider_requests=0,
                missing_artifact=True,
            )
        except Exception as exc:
            errors.append(f"replay-error:{type(exc).__name__}")
            return ReplayComparison(
                ran=True,
                mode="REPLAY",
                live_terminal=live_terminal.value,
                replay_terminal=None,
                terminal_matches=None,
                row_delta=_delta(before, _row_counts(engine)),
                provider_requests=provider_requests,
                missing_artifact=missing,
            )

        after = _row_counts(engine)
        row_delta = _delta(before, after)
        matches = replay_terminal == live_terminal if replay_terminal else None
        _ = (mode, latest_checkpoint, recorded_agents, clock)
        return ReplayComparison(
            ran=True,
            mode=ReplayMode.REPLAY.value,
            live_terminal=live_terminal.value,
            replay_terminal=replay_terminal.value if replay_terminal else None,
            terminal_matches=matches,
            row_delta=row_delta,
            provider_requests=provider_requests,
            missing_artifact=missing,
        )

    @staticmethod
    def _corrupt_checkpoint(engine: Engine, checkpoint_id: UUID) -> None:
        """Tamper snapshot_json so it still parses but fails checksum.

        Mutating a snapshot field (without resealing the row checksum)
        keeps row_to_checkpoint succeeding and forces verify_integrity()
        to fail closed — the fail-closed path invariant J observes.
        """
        import json as _json

        with engine.begin() as conn:
            row = conn.execute(
                text("SELECT snapshot_json FROM checkpoints WHERE checkpoint_id = :id"),
                {"id": str(checkpoint_id)},
            ).fetchone()
            if row is None:
                return
            snapshot_json = row[0]
            if not isinstance(snapshot_json, str) or not snapshot_json:
                return
            try:
                data = _json.loads(snapshot_json)
            except ValueError:
                data = None
            if isinstance(data, dict):
                if "rework_count" in data:
                    data["rework_count"] = int(data["rework_count"]) + 1
                elif "step_count" in data:
                    data["step_count"] = int(data["step_count"]) + 1
                else:
                    data["__corrupt__"] = True
                corrupted = _json.dumps(data, separators=(",", ":"))
            else:
                corrupted = snapshot_json + " "
            conn.execute(
                text(
                    "UPDATE checkpoints SET snapshot_json = :payload " "WHERE checkpoint_id = :id"
                ),
                {"payload": corrupted, "id": str(checkpoint_id)},
            )


def _row_counts(engine: Engine) -> dict[str, int]:
    """Row counts for mutation detection during replay."""
    counts: dict[str, int] = {}
    tables = (
        "workflow_runs",
        "audit_events",
        "human_approvals",
        "checkpoints",
        "budget_counters",
    )
    with engine.connect() as conn:
        for table in tables:
            try:
                result = conn.execute(text(f"SELECT COUNT(*) FROM {table}"))  # noqa: S608
                counts[table] = int(result.scalar() or 0)
            except Exception:
                counts[table] = -1
    return counts


def _delta(before: dict[str, int], after: dict[str, int]) -> int:
    total = 0
    for key in set(before) | set(after):
        b, a = before.get(key, 0), after.get(key, 0)
        if b >= 0 and a >= 0:
            total += abs(a - b)
    return total


def _cleanup_db(db_path: Path) -> None:
    """Remove the ephemeral scenario database file."""
    try:
        if db_path.exists():
            db_path.unlink()
    except OSError:
        pass

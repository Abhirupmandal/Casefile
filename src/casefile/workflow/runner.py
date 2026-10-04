"""
CASEFILE deterministic multi-agent workflow runner (Phase 2, Phase 3, Phase 4).

Orchestrates the complete CASEFILE lifecycle:
RECEIVED -> EXTRACTION -> INVESTIGATION -> REVIEW -> HUMAN_APPROVAL / TERMINAL.

Coordinates Supervisor routing, specialist agents (Extractor, Investigator,
Reviewer), bounded retries, bounded reviewer rework, typed handoffs via
ContractEnvelope, terminal state safety, durable checkpointing, and hard budget enforcement.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from casefile.agents.base import AgentFailedError
from casefile.agents.context import AgentContext
from casefile.agents.extractor import ExtractorAgent
from casefile.agents.investigator import InvestigatorAgent
from casefile.agents.reviewer import ReviewerAgent
from casefile.approval.model import (
    ApprovalAuthorizer,
    ApprovalDecision,
    ApprovalVerdict,
)
from casefile.budget.engine import BudgetEngine, ReserveKind
from casefile.budget.pricing import ModelUsage
from casefile.budget.termination import to_workflow_state
from casefile.checkpoint.model import (
    Checkpoint,
    CheckpointKind,
    RecordedAgentOutput,
    RecordedToolOutput,
    TransitionPathEntry,
)
from casefile.checkpoint.policy import should_checkpoint
from casefile.checkpoint.repository import CheckpointRepository
from casefile.checkpoint.resume import resume_from_checkpoint
from casefile.models.contracts import (
    ClaimInput,
    ExtractionRequest,
    ExtractionResult,
    InvestigationRequest,
    InvestigationResult,
    ReviewRequest,
    ReviewResult,
    WorkflowState,
)
from casefile.models.domain import (
    AgentType,
    HumanApprovalRequest,
    Money,
    Recommendation,
    RecommendationType,
)
from casefile.models.envelope import ContractEnvelope
from casefile.workflow.context import (
    Clock,
    RunContext,
    SystemClock,
    WorkflowContext,
    WorkflowSnapshot,
)
from casefile.workflow.engine import WorkflowEngine
from casefile.workflow.hooks import (
    EventSink,
    HookPayload,
    NullSink,
    WorkflowHookEvent,
)
from casefile.workflow.states import is_terminal
from casefile.workflow.store import record_to_event, snapshot_to_run
from casefile.workflow.supervisor import NodeName, RouteAction, SupervisorRouter
from casefile.workflow.transitions import TransitionEvent
from casefile.workflow.triggers import Trigger

if TYPE_CHECKING:
    from casefile.observability.provider import Observability
    from casefile.storage.unit_of_work import UnitOfWork
    from casefile.tools.registry import ToolRegistry


class WorkflowRunner:
    """
    Deterministic multi-agent workflow orchestrator.

    Executes specialist agents under Supervisor routing and engine state transitions.
    Enforces terminal state immutability, bounded retries, bounded rework loops,
    step exhaustion ceilings, and budget enforcement.
    """

    def __init__(
        self,
        extractor: ExtractorAgent,
        investigator: InvestigatorAgent,
        reviewer: ReviewerAgent,
        *,
        engine: WorkflowEngine | None = None,
        router: SupervisorRouter | None = None,
        clock: Clock | None = None,
        sink: EventSink | None = None,
        max_agent_retries: int = 3,
        max_rework_cycles: int = 3,
        max_steps: int = 50,
        uow_factory: Callable[[], UnitOfWork] | None = None,
        tool_registry: ToolRegistry | None = None,
        checkpoint_repo: CheckpointRepository | None = None,
        budget_engine: BudgetEngine | None = None,
        approval_service: Any = None,
        obs: Observability | None = None,
    ) -> None:
        self._extractor = extractor
        self._investigator = investigator
        self._reviewer = reviewer
        self._engine = engine or WorkflowEngine()
        self._router = router or SupervisorRouter()
        self._clock = clock or SystemClock()
        self._sink = sink or NullSink()
        self._max_agent_retries = max_agent_retries
        self._max_rework_cycles = max_rework_cycles
        self._max_steps = max_steps
        self._uow_factory = uow_factory
        self._tool_registry = tool_registry
        self._checkpoint_repo = checkpoint_repo
        self._budget_engine = budget_engine
        self._approval_service = approval_service
        self._obs = obs
        self.active_run_id: UUID | None = None

        for agent in (extractor, investigator, reviewer):
            prov = getattr(agent, "_provider", None)
            if prov is not None and hasattr(prov, "_runner_holder"):
                prov._runner_holder.clear()
                prov._runner_holder.append(self)

    @property
    def engine(self) -> WorkflowEngine:
        return self._engine

    @property
    def router(self) -> SupervisorRouter:
        return self._router

    @property
    def checkpoint_repo(self) -> CheckpointRepository | None:
        return self._checkpoint_repo

    @property
    def budget_engine(self) -> BudgetEngine | None:
        return self._budget_engine

    @property
    def approval_service(self) -> Any:
        return self._approval_service

    @property
    def obs(self) -> Observability | None:
        return self._obs

    @property
    def max_steps(self) -> int:
        return self._max_steps

    def _collect_recorded_agents(self, wf_ctx: WorkflowContext) -> tuple[RecordedAgentOutput, ...]:
        outputs: list[RecordedAgentOutput] = []
        if wf_ctx.extraction_result is not None:
            outputs.append(
                RecordedAgentOutput(
                    agent=AgentType.EXTRACTOR,
                    contract_type="extraction_result",
                    output_json=wf_ctx.extraction_result.model_dump_json(),
                    execution_id=uuid4(),
                )
            )
        if wf_ctx.investigation_result is not None:
            outputs.append(
                RecordedAgentOutput(
                    agent=AgentType.INVESTIGATOR,
                    contract_type="investigation_result",
                    output_json=wf_ctx.investigation_result.model_dump_json(),
                    execution_id=uuid4(),
                )
            )
        if wf_ctx.review_result is not None:
            outputs.append(
                RecordedAgentOutput(
                    agent=AgentType.REVIEWER,
                    contract_type="review_result",
                    output_json=wf_ctx.review_result.model_dump_json(),
                    execution_id=uuid4(),
                )
            )
        return tuple(outputs)

    def _collect_recorded_tools(self, wf_ctx: WorkflowContext) -> tuple[RecordedToolOutput, ...]:
        outputs: list[RecordedToolOutput] = []
        for call in wf_ctx.tool_calls:
            output_json = ""
            if hasattr(call, "output"):
                out = call.output
                if hasattr(out, "model_dump_json"):
                    output_json = out.model_dump_json()
                elif isinstance(out, str):
                    output_json = out
                else:
                    output_json = json.dumps(out, default=str)
            outputs.append(
                RecordedToolOutput(
                    tool_name=getattr(call, "tool_name", "unknown"),
                    invocation_id=getattr(call, "invocation_id", uuid4()),
                    input_hash="",
                    output_json=output_json,
                    idempotency_key=str(getattr(call, "idempotency_key", uuid4())),
                )
            )
        return tuple(outputs)

    def _apply_and_persist(
        self,
        snapshot: WorkflowSnapshot,
        event: TransitionEvent,
        run_ctx: RunContext,
        wf_ctx: WorkflowContext,
    ) -> WorkflowSnapshot:
        apply_res = self._engine.apply(snapshot, event, run_ctx)
        wf_ctx.current_snapshot = apply_res.snapshot

        latest_cp: Checkpoint | None = None
        if self._checkpoint_repo is not None:
            latest_cp = self._checkpoint_repo.get_latest(wf_ctx.workflow_run_id)

        checkpoint_to_save: Checkpoint | None = None
        approval_req: HumanApprovalRequest | None = None
        if self._checkpoint_repo is not None and apply_res.record is not None:
            take_cp, cp_kind = should_checkpoint(apply_res.record, latest_cp)
            if take_cp:
                rec_agents = self._collect_recorded_agents(wf_ctx)
                rec_tools = self._collect_recorded_tools(wf_ctx)
                if latest_cp is not None:
                    path_entries = latest_cp.path + (
                        TransitionPathEntry(
                            sequence_no=apply_res.record.sequence_no,
                            trigger=apply_res.record.trigger,
                            actor=apply_res.record.actor,
                            reason=apply_res.record.reason,
                        ),
                    )
                else:
                    path_entries = (
                        TransitionPathEntry(
                            sequence_no=apply_res.record.sequence_no,
                            trigger=apply_res.record.trigger,
                            actor=apply_res.record.actor,
                            reason=apply_res.record.reason,
                        ),
                    )

                approval_req = None
                if cp_kind == CheckpointKind.HUMAN_WAIT:
                    now = datetime.now(UTC)
                    claim_amount = Decimal("100.00")
                    if (
                        wf_ctx.claim is not None
                        and getattr(wf_ctx.claim, "claim_amount", None) is not None
                    ):
                        claim_amount = Decimal(str(wf_ctx.claim.claim_amount))
                    summary = "Awaiting human approval"
                    conf = 0.95
                    if wf_ctx.review_result is not None:
                        summary = wf_ctx.review_result.reasoning
                        conf = float(wf_ctx.review_result.confidence_score)
                    approval_req = HumanApprovalRequest(
                        workflow_run_id=wf_ctx.workflow_run_id,
                        claim_id=wf_ctx.claim_id,
                        recommendation=Recommendation(
                            claim_id=wf_ctx.claim_id,
                            recommendation_type=RecommendationType.APPROVE_FULL,
                            estimated_payout=Money(amount=claim_amount),
                            notes=summary,
                            confidence=conf,
                        ),
                        reviewer_summary=summary,
                        deadline=now + timedelta(hours=24),
                    )

                checkpoint_to_save = Checkpoint(
                    claim_id=wf_ctx.claim_id,
                    workflow_run_id=wf_ctx.workflow_run_id,
                    state=apply_res.snapshot.current_state,
                    kind=cp_kind,
                    snapshot=apply_res.snapshot,
                    step_count=apply_res.snapshot.step_count,
                    rework_count=apply_res.snapshot.rework_count,
                    recorded_tools=rec_tools,
                    recorded_agents=rec_agents,
                    path=path_entries,
                    approval_request=approval_req,
                    terminal_reason=(
                        apply_res.record.reason
                        if apply_res.snapshot.current_state.is_terminal
                        else None
                    ),
                    correlation_id=wf_ctx.correlation_id,
                    parent_checkpoint_id=latest_cp.checkpoint_id if latest_cp else None,
                ).sealed()

        if self._uow_factory is not None:
            with self._uow_factory() as uow:
                uow.runs.save(snapshot_to_run(apply_res.snapshot))
                if apply_res.record is not None:
                    uow.workflow_events.append(record_to_event(apply_res.record))
                if checkpoint_to_save is not None:
                    uow.checkpoints.create(checkpoint_to_save)
                    self._emit(
                        WorkflowHookEvent.CHECKPOINT_CREATED,
                        wf_ctx,
                        f"Checkpoint {checkpoint_to_save.checkpoint_id} persisted",
                    )
        elif checkpoint_to_save is not None and self._checkpoint_repo is not None:
            self._checkpoint_repo.create(checkpoint_to_save)
            self._emit(
                WorkflowHookEvent.CHECKPOINT_CREATED,
                wf_ctx,
                f"Checkpoint {checkpoint_to_save.checkpoint_id} persisted",
            )

        if approval_req is not None and self._approval_service is not None:
            with suppress(Exception):
                self._approval_service.request_approval(approval_req)

        return apply_res.snapshot

    def _persist_agent_execution(
        self,
        record: Any,
        operation: str,
        wf_ctx: WorkflowContext,
    ) -> None:
        if self._uow_factory is None or record is None:
            return
        with self._uow_factory() as uow:
            uow.agent_executions.save(
                record,
                operation=operation,
                claim_id=wf_ctx.claim_id,
                workflow_run_id=wf_ctx.workflow_run_id,
            )

    def _persist_tool_calls(self, wf_ctx: WorkflowContext) -> None:
        if self._uow_factory is None or self._tool_registry is None:
            return
        with self._uow_factory() as uow:
            for call in self._tool_registry.recorded_calls:
                uow.tool_invocations.save(call)
                wf_ctx.record_tool_call(call)

    def run(
        self,
        claim: ClaimInput,
        *,
        workflow_run_id: UUID | None = None,
        correlation_id: UUID | None = None,
    ) -> WorkflowContext:
        """
        Run the complete claim adjudication workflow from intake to decision/terminal.
        """
        run_id = workflow_run_id or uuid4()
        corr_id = correlation_id or uuid4()
        self.active_run_id = run_id

        run_ctx = RunContext(
            claim_id=claim.claim_id,
            workflow_run_id=run_id,
            correlation_id=corr_id,
            actor=AgentType.SUPERVISOR,
            clock=self._clock,
            max_rework_cycles=self._max_rework_cycles,
        )

        snapshot = WorkflowSnapshot(
            workflow_run_id=run_id,
            claim_id=claim.claim_id,
            current_state=WorkflowState.RECEIVED,
        )

        wf_ctx = WorkflowContext(
            workflow_run_id=run_id,
            claim_id=claim.claim_id,
            correlation_id=corr_id,
            claim=claim,
            documents=list(claim.documents),
            current_snapshot=snapshot,
        )

        self._emit(WorkflowHookEvent.WORKFLOW_STARTED, wf_ctx, "Workflow run started")
        if self._uow_factory is not None:
            with self._uow_factory() as uow:
                uow.runs.save(snapshot_to_run(snapshot))

        # Check pre-flight limits before intake transition
        if snapshot.step_count >= self._max_steps:
            init_event = TransitionEvent(
                claim_id=claim.claim_id,
                workflow_run_id=run_id,
                trigger=Trigger.STEPS_EXHAUSTED,
                actor=AgentType.SUPERVISOR,
                correlation_id=corr_id,
                reason=f"Maximum workflow steps reached at intake ({self._max_steps})",
            )
            snapshot = self._apply_and_persist(snapshot, init_event, run_ctx, wf_ctx)
            wf_ctx.current_snapshot = snapshot
            return wf_ctx

        if self._budget_engine is not None:
            term_reason = self._budget_engine.check_all()
            if term_reason is not None:
                dest = to_workflow_state(term_reason)
                trigger = (
                    Trigger.BUDGET_EXHAUSTED
                    if dest == WorkflowState.BUDGET_EXHAUSTED
                    else (
                        Trigger.STEPS_EXHAUSTED
                        if dest == WorkflowState.MAX_STEPS_EXCEEDED
                        else Trigger.WORKFLOW_TIMED_OUT
                    )
                )
                init_event = TransitionEvent(
                    claim_id=claim.claim_id,
                    workflow_run_id=run_id,
                    trigger=trigger,
                    actor=AgentType.SUPERVISOR,
                    correlation_id=corr_id,
                    reason=f"Budget exhausted before intake: {term_reason.value}",
                )
                snapshot = self._apply_and_persist(snapshot, init_event, run_ctx, wf_ctx)
                wf_ctx.current_snapshot = snapshot
                return wf_ctx

            res = self._budget_engine.pre_step(ReserveKind.WORKFLOW_STEP)
            if not res.granted and res.reason is not None:
                dest = to_workflow_state(res.reason)
                trigger = (
                    Trigger.BUDGET_EXHAUSTED
                    if dest == WorkflowState.BUDGET_EXHAUSTED
                    else (
                        Trigger.STEPS_EXHAUSTED
                        if dest == WorkflowState.MAX_STEPS_EXCEEDED
                        else Trigger.WORKFLOW_TIMED_OUT
                    )
                )
                init_event = TransitionEvent(
                    claim_id=claim.claim_id,
                    workflow_run_id=run_id,
                    trigger=trigger,
                    actor=AgentType.SUPERVISOR,
                    correlation_id=corr_id,
                    reason=f"Budget pre-step denied at intake: {res.reason.value}",
                )
                snapshot = self._apply_and_persist(snapshot, init_event, run_ctx, wf_ctx)
                wf_ctx.current_snapshot = snapshot
                return wf_ctx

        # Initial intake transition: RECEIVED -> EXTRACTION
        init_event = TransitionEvent(
            claim_id=claim.claim_id,
            workflow_run_id=run_id,
            trigger=Trigger.CLAIM_VALIDATED,
            actor=AgentType.SUPERVISOR,
            correlation_id=corr_id,
            reason="Claim validated at intake",
        )
        snapshot = self._apply_and_persist(snapshot, init_event, run_ctx, wf_ctx)
        self._emit(
            WorkflowHookEvent.STATE_TRANSITION,
            wf_ctx,
            f"RECEIVED -> {snapshot.current_state.value}",
        )

        # Main orchestration loop
        while not is_terminal(snapshot.current_state):
            # Check step limit
            if snapshot.step_count >= self._max_steps:
                event = TransitionEvent(
                    claim_id=wf_ctx.claim_id,
                    workflow_run_id=run_id,
                    trigger=Trigger.STEPS_EXHAUSTED,
                    actor=AgentType.SUPERVISOR,
                    correlation_id=corr_id,
                    reason=f"Maximum workflow steps exceeded ({self._max_steps})",
                )
                snapshot = self._apply_and_persist(snapshot, event, run_ctx, wf_ctx)
                self._emit(
                    WorkflowHookEvent.STEP_LIMIT_REACHED,
                    wf_ctx,
                    f"Step limit {self._max_steps} reached",
                )
                break

            if self._budget_engine is not None:
                term_reason = self._budget_engine.check_all()
                if term_reason is not None:
                    dest = to_workflow_state(term_reason)
                    trigger = (
                        Trigger.BUDGET_EXHAUSTED
                        if dest == WorkflowState.BUDGET_EXHAUSTED
                        else (
                            Trigger.STEPS_EXHAUSTED
                            if dest == WorkflowState.MAX_STEPS_EXCEEDED
                            else Trigger.WORKFLOW_TIMED_OUT
                        )
                    )
                    event = TransitionEvent(
                        claim_id=wf_ctx.claim_id,
                        workflow_run_id=run_id,
                        trigger=trigger,
                        actor=AgentType.SUPERVISOR,
                        correlation_id=corr_id,
                        reason=f"Budget exhaustion: {term_reason.value}",
                    )
                    snapshot = self._apply_and_persist(snapshot, event, run_ctx, wf_ctx)
                    self._emit(
                        WorkflowHookEvent.BUDGET_EXCEEDED,
                        wf_ctx,
                        f"Budget exceeded: {term_reason.value}",
                    )
                    break

                res = self._budget_engine.pre_step(ReserveKind.WORKFLOW_STEP)
                if not res.granted and res.reason is not None:
                    dest = to_workflow_state(res.reason)
                    trigger = (
                        Trigger.BUDGET_EXHAUSTED
                        if dest == WorkflowState.BUDGET_EXHAUSTED
                        else (
                            Trigger.STEPS_EXHAUSTED
                            if dest == WorkflowState.MAX_STEPS_EXCEEDED
                            else Trigger.WORKFLOW_TIMED_OUT
                        )
                    )
                    event = TransitionEvent(
                        claim_id=wf_ctx.claim_id,
                        workflow_run_id=run_id,
                        trigger=trigger,
                        actor=AgentType.SUPERVISOR,
                        correlation_id=corr_id,
                        reason=f"Budget pre-step denied: {res.reason.value}",
                    )
                    snapshot = self._apply_and_persist(snapshot, event, run_ctx, wf_ctx)
                    self._emit(
                        WorkflowHookEvent.BUDGET_EXCEEDED,
                        wf_ctx,
                        f"Budget pre-step denied: {res.reason.value}",
                    )
                    break

            decision = self._router.route(snapshot)

            if decision.action == RouteAction.TERMINATED:
                self._emit(
                    WorkflowHookEvent.WORKFLOW_TERMINATED,
                    wf_ctx,
                    f"Workflow terminated at state {snapshot.current_state.value}",
                )
                break

            if decision.action == RouteAction.WAIT_FOR_HUMAN:
                self._emit(
                    WorkflowHookEvent.APPROVAL_REQUESTED,
                    wf_ctx,
                    "Workflow paused at human approval gate",
                )
                break

            if decision.node == NodeName.EXTRACTOR:
                snapshot = self._step_extractor(wf_ctx, run_ctx, snapshot)
            elif decision.node == NodeName.INVESTIGATOR:
                snapshot = self._step_investigator(wf_ctx, run_ctx, snapshot)
            elif decision.node == NodeName.REVIEWER:
                snapshot = self._step_reviewer(wf_ctx, run_ctx, snapshot)
            else:
                break

        if is_terminal(snapshot.current_state):
            self._emit(
                WorkflowHookEvent.WORKFLOW_TERMINATED,
                wf_ctx,
                f"Workflow terminated at state {snapshot.current_state.value}",
            )

        wf_ctx.current_snapshot = snapshot
        return wf_ctx

    def resume(
        self,
        checkpoint_id: UUID,
        *,
        claim: ClaimInput | None = None,
    ) -> WorkflowContext:
        """
        Resume workflow execution from a stored checkpoint.

        Verifies checkpoint integrity and compatibility.
        If terminal state, preserves terminal immutability.
        Restores recorded outputs and continues from the checkpointed state.
        """
        if self._checkpoint_repo is None:
            raise ValueError("WorkflowRunner requires checkpoint_repo to resume")

        resume_res = resume_from_checkpoint(
            checkpoint_id,
            checkpoints=self._checkpoint_repo,
            max_rework_cycles=self._max_rework_cycles,
        )
        cp = resume_res.checkpoint
        snapshot = resume_res.snapshot
        run_ctx = resume_res.context
        self.active_run_id = cp.workflow_run_id

        # Build restored WorkflowContext
        wf_ctx = WorkflowContext(
            workflow_run_id=cp.workflow_run_id,
            claim_id=cp.claim_id,
            correlation_id=cp.correlation_id,
            claim=claim,
            current_snapshot=snapshot,
        )
        if claim is not None:
            wf_ctx.documents = list(claim.documents)

        # Restore agent recordings
        for rec in cp.recorded_agents:
            if rec.contract_type == "extraction_result":
                wf_ctx.set_extraction_result(ExtractionResult.model_validate_json(rec.output_json))
            elif rec.contract_type == "investigation_result":
                wf_ctx.set_investigation_result(
                    InvestigationResult.model_validate_json(rec.output_json)
                )
            elif rec.contract_type == "review_result":
                wf_ctx.set_review_result(ReviewResult.model_validate_json(rec.output_json))

        # Restore tool recordings
        for tool_rec in cp.recorded_tools:
            wf_ctx.record_tool_call(tool_rec)

        self._emit(
            WorkflowHookEvent.CHECKPOINT_LOADED, wf_ctx, f"Checkpoint {checkpoint_id} loaded"
        )
        self._emit(
            WorkflowHookEvent.CHECKPOINT_RESTORED,
            wf_ctx,
            f"Checkpoint {checkpoint_id} restored to {snapshot.current_state.value}",
        )

        # Terminal state immutability: do not continue
        if snapshot.current_state.is_terminal:
            return wf_ctx

        # Resume loop from current state
        while not is_terminal(snapshot.current_state):
            # Check step limit
            if snapshot.step_count >= self._max_steps:
                event = TransitionEvent(
                    claim_id=wf_ctx.claim_id,
                    workflow_run_id=wf_ctx.workflow_run_id,
                    trigger=Trigger.STEPS_EXHAUSTED,
                    actor=AgentType.SUPERVISOR,
                    correlation_id=wf_ctx.correlation_id,
                    reason=f"Maximum workflow steps exceeded ({self._max_steps})",
                )
                snapshot = self._apply_and_persist(snapshot, event, run_ctx, wf_ctx)
                self._emit(
                    WorkflowHookEvent.STEP_LIMIT_REACHED,
                    wf_ctx,
                    f"Step limit {self._max_steps} reached",
                )
                break

            if self._budget_engine is not None:
                term_reason = self._budget_engine.check_all()
                if term_reason is not None:
                    dest = to_workflow_state(term_reason)
                    trigger = (
                        Trigger.BUDGET_EXHAUSTED
                        if dest == WorkflowState.BUDGET_EXHAUSTED
                        else (
                            Trigger.STEPS_EXHAUSTED
                            if dest == WorkflowState.MAX_STEPS_EXCEEDED
                            else Trigger.WORKFLOW_TIMED_OUT
                        )
                    )
                    event = TransitionEvent(
                        claim_id=wf_ctx.claim_id,
                        workflow_run_id=wf_ctx.workflow_run_id,
                        trigger=trigger,
                        actor=AgentType.SUPERVISOR,
                        correlation_id=wf_ctx.correlation_id,
                        reason=f"Budget exhaustion: {term_reason.value}",
                    )
                    snapshot = self._apply_and_persist(snapshot, event, run_ctx, wf_ctx)
                    self._emit(
                        WorkflowHookEvent.BUDGET_EXCEEDED,
                        wf_ctx,
                        f"Budget exceeded: {term_reason.value}",
                    )
                    break

                res = self._budget_engine.pre_step(ReserveKind.WORKFLOW_STEP)
                if not res.granted and res.reason is not None:
                    dest = to_workflow_state(res.reason)
                    trigger = (
                        Trigger.BUDGET_EXHAUSTED
                        if dest == WorkflowState.BUDGET_EXHAUSTED
                        else (
                            Trigger.STEPS_EXHAUSTED
                            if dest == WorkflowState.MAX_STEPS_EXCEEDED
                            else Trigger.WORKFLOW_TIMED_OUT
                        )
                    )
                    event = TransitionEvent(
                        claim_id=wf_ctx.claim_id,
                        workflow_run_id=wf_ctx.workflow_run_id,
                        trigger=trigger,
                        actor=AgentType.SUPERVISOR,
                        correlation_id=wf_ctx.correlation_id,
                        reason=f"Budget pre-step denied: {res.reason.value}",
                    )
                    snapshot = self._apply_and_persist(snapshot, event, run_ctx, wf_ctx)
                    self._emit(
                        WorkflowHookEvent.BUDGET_EXCEEDED,
                        wf_ctx,
                        f"Budget pre-step denied: {res.reason.value}",
                    )
                    break

            decision = self._router.route(snapshot)

            if decision.action == RouteAction.TERMINATED:
                self._emit(
                    WorkflowHookEvent.WORKFLOW_TERMINATED,
                    wf_ctx,
                    f"Workflow terminated at state {snapshot.current_state.value}",
                )
                break

            if decision.action == RouteAction.WAIT_FOR_HUMAN:
                self._emit(
                    WorkflowHookEvent.APPROVAL_REQUESTED,
                    wf_ctx,
                    "Workflow paused at human approval gate",
                )
                break

            if decision.node == NodeName.EXTRACTOR:
                snapshot = self._step_extractor(wf_ctx, run_ctx, snapshot)
            elif decision.node == NodeName.INVESTIGATOR:
                snapshot = self._step_investigator(wf_ctx, run_ctx, snapshot)
            elif decision.node == NodeName.REVIEWER:
                snapshot = self._step_reviewer(wf_ctx, run_ctx, snapshot)
            else:
                break

        if is_terminal(snapshot.current_state):
            self._emit(
                WorkflowHookEvent.WORKFLOW_TERMINATED,
                wf_ctx,
                f"Workflow terminated at state {snapshot.current_state.value}",
            )

        wf_ctx.current_snapshot = snapshot
        return wf_ctx

    def apply_human_decision(
        self,
        wf_ctx: WorkflowContext,
        decision: ApprovalDecision,
    ) -> WorkflowContext:
        """
        Apply an authorized human approval or rejection to a parked workflow.

        Transitions HUMAN_APPROVAL -> APPROVED (if verdict == APPROVED)
        or HUMAN_APPROVAL -> REJECTED (if verdict == REJECTED).
        Rejects decisions attempted by agents, supervisor, or unauthorized roles.
        """
        if (
            wf_ctx.current_snapshot is None
            or wf_ctx.current_snapshot.current_state != WorkflowState.HUMAN_APPROVAL
        ):
            curr = (
                wf_ctx.current_snapshot.current_state
                if wf_ctx.current_snapshot is not None
                else "UNINITIALIZED"
            )
            raise ValueError(f"Workflow is not in HUMAN_APPROVAL state (current: {curr})")

        if decision.actor is not None:
            ApprovalAuthorizer.check_authorization(decision.actor)

        verdict = decision.verdict or decision.decision
        trigger = (
            Trigger.APPROVAL_GRANTED
            if verdict == ApprovalVerdict.APPROVED
            else Trigger.APPROVAL_REJECTED
        )
        event = TransitionEvent(
            claim_id=wf_ctx.claim_id,
            workflow_run_id=wf_ctx.workflow_run_id,
            trigger=trigger,
            actor=AgentType.HUMAN,
            correlation_id=decision.correlation_id or wf_ctx.correlation_id,
            reason=decision.reason or f"Human decision: {verdict.value if verdict else 'DECIDED'}",
        )
        run_ctx = RunContext(
            workflow_run_id=wf_ctx.workflow_run_id,
            claim_id=wf_ctx.claim_id,
            max_rework_cycles=self._max_rework_cycles,
        )
        snapshot = self._apply_and_persist(wf_ctx.current_snapshot, event, run_ctx, wf_ctx)
        wf_ctx.current_snapshot = snapshot
        if is_terminal(snapshot.current_state):
            self._emit(
                WorkflowHookEvent.WORKFLOW_TERMINATED,
                wf_ctx,
                f"Workflow terminated at state {snapshot.current_state.value}",
            )
        return wf_ctx

    def handle_approval_timeout(
        self,
        wf_ctx: WorkflowContext,
        reason: str = "Approval expired",
    ) -> WorkflowContext:
        """Handle approval deadline expiration (transitions HUMAN_APPROVAL -> ESCALATION)."""
        if (
            wf_ctx.current_snapshot is None
            or wf_ctx.current_snapshot.current_state != WorkflowState.HUMAN_APPROVAL
        ):
            curr = (
                wf_ctx.current_snapshot.current_state
                if wf_ctx.current_snapshot is not None
                else "UNINITIALIZED"
            )
            raise ValueError(f"Workflow is not in HUMAN_APPROVAL state (current: {curr})")
        event = TransitionEvent(
            claim_id=wf_ctx.claim_id,
            workflow_run_id=wf_ctx.workflow_run_id,
            trigger=Trigger.APPROVAL_TIMED_OUT,
            actor=AgentType.SUPERVISOR,
            correlation_id=wf_ctx.correlation_id,
            reason=reason,
        )
        run_ctx = RunContext(
            workflow_run_id=wf_ctx.workflow_run_id,
            claim_id=wf_ctx.claim_id,
            max_rework_cycles=self._max_rework_cycles,
        )
        snapshot = self._apply_and_persist(wf_ctx.current_snapshot, event, run_ctx, wf_ctx)
        wf_ctx.current_snapshot = snapshot
        if is_terminal(snapshot.current_state):
            self._emit(
                WorkflowHookEvent.WORKFLOW_TERMINATED,
                wf_ctx,
                f"Workflow terminated at state {snapshot.current_state.value}",
            )
        return wf_ctx

    def _step_extractor(
        self,
        wf_ctx: WorkflowContext,
        run_ctx: RunContext,
        snapshot: WorkflowSnapshot,
    ) -> WorkflowSnapshot:
        if self._budget_engine is not None:
            res = self._budget_engine.pre_step(ReserveKind.AGENT_STEP)
            if not res.granted and res.reason is not None:
                dest = to_workflow_state(res.reason)
                trigger = (
                    Trigger.BUDGET_EXHAUSTED
                    if dest == WorkflowState.BUDGET_EXHAUSTED
                    else (
                        Trigger.STEPS_EXHAUSTED
                        if dest == WorkflowState.MAX_STEPS_EXCEEDED
                        else Trigger.WORKFLOW_TIMED_OUT
                    )
                )
                event = TransitionEvent(
                    claim_id=wf_ctx.claim_id,
                    workflow_run_id=wf_ctx.workflow_run_id,
                    trigger=trigger,
                    actor=AgentType.SUPERVISOR,
                    correlation_id=wf_ctx.correlation_id,
                    reason=f"Budget agent step denied: {res.reason.value}",
                )
                return self._apply_and_persist(snapshot, event, run_ctx, wf_ctx)

        assert wf_ctx.claim is not None
        if isinstance(wf_ctx.claim, ClaimInput):
            claim_input = wf_ctx.claim
        elif wf_ctx.extraction_result is not None:
            claim_input = ClaimInput(
                claim_id=wf_ctx.claim_id,
                policy_id=wf_ctx.extraction_result.policy_id,
                claimant_name=wf_ctx.extraction_result.claimant_name,
                incident_date=wf_ctx.extraction_result.incident_date,
                claim_amount=wf_ctx.extraction_result.claim_amount,
                description=wf_ctx.extraction_result.incident_description,
                documents=[str(d) for d in wf_ctx.documents],
            )
        else:
            raise ValueError("ClaimInput required for extraction step")

        req = ExtractionRequest(workflow_id=wf_ctx.workflow_run_id, claim_input=claim_input)
        envelope = ContractEnvelope.wrap(
            "extraction_request",
            req,
            claim_id=wf_ctx.claim_id,
            workflow_run_id=wf_ctx.workflow_run_id,
        )
        unwrapped = envelope.unwrap(ExtractionRequest)

        agent_ctx = AgentContext(
            workflow_run_id=wf_ctx.workflow_run_id,
            claim_id=wf_ctx.claim_id,
            correlation_id=wf_ctx.correlation_id,
            agent=AgentType.EXTRACTOR,
            max_attempts=self._max_agent_retries,
        )

        self._emit(WorkflowHookEvent.NODE_STARTED, wf_ctx, "Extractor agent started")
        try:
            result, record = self._extractor.run(unwrapped, agent_ctx)
            result_envelope = ContractEnvelope.wrap(
                "extraction_result",
                result,
                claim_id=wf_ctx.claim_id,
                workflow_run_id=wf_ctx.workflow_run_id,
            )
            validated_result = result_envelope.unwrap(ExtractionResult)
            wf_ctx.set_extraction_result(validated_result)
            wf_ctx.record_execution(record)
            self._persist_agent_execution(record, "extraction", wf_ctx)
            if (
                self._budget_engine is not None
                and record is not None
                and (
                    getattr(record, "input_tokens", 0) > 0
                    or getattr(record, "output_tokens", 0) > 0
                )
            ):
                provider = getattr(record, "provider_name", "deterministic") or "deterministic"
                model = getattr(record, "model_name", "test") or "test"
                with suppress(Exception):
                    self._budget_engine.record_model_call(
                        ModelUsage(
                            provider=provider,
                            model=model,
                            input_tokens=record.input_tokens,
                            output_tokens=record.output_tokens,
                        )
                    )
            self._emit(WorkflowHookEvent.NODE_COMPLETED, wf_ctx, "Extractor agent completed")

            event = TransitionEvent(
                claim_id=wf_ctx.claim_id,
                workflow_run_id=wf_ctx.workflow_run_id,
                trigger=Trigger.EXTRACTION_SUCCEEDED,
                actor=AgentType.SUPERVISOR,
                correlation_id=wf_ctx.correlation_id,
                reason="Extraction succeeded",
            )
            return self._apply_and_persist(snapshot, event, run_ctx, wf_ctx)
        except AgentFailedError as exc:
            if exc.record:
                wf_ctx.record_execution(exc.record)
                self._persist_agent_execution(exc.record, "extraction", wf_ctx)
            self._emit(WorkflowHookEvent.NODE_FAILED, wf_ctx, f"Extractor failed: {exc}")
            event = TransitionEvent(
                claim_id=wf_ctx.claim_id,
                workflow_run_id=wf_ctx.workflow_run_id,
                trigger=Trigger.EXTRACTION_FAILED,
                actor=AgentType.SUPERVISOR,
                correlation_id=wf_ctx.correlation_id,
                reason=f"Extraction failed: {exc}",
            )
            return self._apply_and_persist(snapshot, event, run_ctx, wf_ctx)

    def _step_investigator(
        self,
        wf_ctx: WorkflowContext,
        run_ctx: RunContext,
        snapshot: WorkflowSnapshot,
    ) -> WorkflowSnapshot:
        if self._budget_engine is not None:
            res = self._budget_engine.pre_step(ReserveKind.AGENT_STEP)
            if not res.granted and res.reason is not None:
                dest = to_workflow_state(res.reason)
                trigger = (
                    Trigger.BUDGET_EXHAUSTED
                    if dest == WorkflowState.BUDGET_EXHAUSTED
                    else (
                        Trigger.STEPS_EXHAUSTED
                        if dest == WorkflowState.MAX_STEPS_EXCEEDED
                        else Trigger.WORKFLOW_TIMED_OUT
                    )
                )
                event = TransitionEvent(
                    claim_id=wf_ctx.claim_id,
                    workflow_run_id=wf_ctx.workflow_run_id,
                    trigger=trigger,
                    actor=AgentType.SUPERVISOR,
                    correlation_id=wf_ctx.correlation_id,
                    reason=f"Budget agent step denied: {res.reason.value}",
                )
                return self._apply_and_persist(snapshot, event, run_ctx, wf_ctx)

        if wf_ctx.extraction_result is None:
            raise ValueError("ExtractionResult required before investigation")

        rework_feedback = None
        if wf_ctx.review_result and wf_ctx.review_result.decision == "REWORK":
            rework_feedback = wf_ctx.review_result.rework_feedback

        req = InvestigationRequest(
            workflow_id=wf_ctx.workflow_run_id,
            extraction_result=wf_ctx.extraction_result,
            rework_feedback=rework_feedback,
        )
        envelope = ContractEnvelope.wrap(
            "investigation_request",
            req,
            claim_id=wf_ctx.claim_id,
            workflow_run_id=wf_ctx.workflow_run_id,
        )
        unwrapped = envelope.unwrap(InvestigationRequest)

        agent_ctx = AgentContext(
            workflow_run_id=wf_ctx.workflow_run_id,
            claim_id=wf_ctx.claim_id,
            correlation_id=wf_ctx.correlation_id,
            agent=AgentType.INVESTIGATOR,
            max_attempts=self._max_agent_retries,
        )

        self._emit(WorkflowHookEvent.NODE_STARTED, wf_ctx, "Investigator agent started")
        try:
            if self._tool_registry is not None:
                lookups: list[tuple[str, dict[str, object]]] = [
                    ("policy_lookup", {"policy_number": wf_ctx.extraction_result.policy_id}),
                    ("evidence_lookup", {"claim_ref": str(wf_ctx.claim_id)}),
                    (
                        "claim_history_lookup",
                        {"customer_id": wf_ctx.extraction_result.claimant_name},
                    ),
                    (
                        "repair_cost_lookup",
                        {
                            "estimate_ref": f"EST-{wf_ctx.extraction_result.policy_id}",
                            "claim_ref": str(wf_ctx.claim_id),
                        },
                    ),
                    ("fraud_signal_lookup", {"claim_ref": str(wf_ctx.claim_id)}),
                ]
                for tool_name, tool_input in lookups:
                    if self._budget_engine is not None:
                        tool_res = self._budget_engine.pre_step(ReserveKind.TOOL_CALL)
                        if not tool_res.granted:
                            break
                    with suppress(Exception):
                        self._investigator.lookup(
                            tool_name,
                            tool_input,
                            agent_ctx=agent_ctx,
                            registry=self._tool_registry,
                        )
                self._persist_tool_calls(wf_ctx)

            result, record = self._investigator.run(unwrapped, agent_ctx)
            result_envelope = ContractEnvelope.wrap(
                "investigation_result",
                result,
                claim_id=wf_ctx.claim_id,
                workflow_run_id=wf_ctx.workflow_run_id,
            )
            validated_result = result_envelope.unwrap(InvestigationResult)
            wf_ctx.set_investigation_result(validated_result)
            wf_ctx.record_execution(record)
            self._persist_agent_execution(record, "investigation", wf_ctx)
            if (
                self._budget_engine is not None
                and record is not None
                and (
                    getattr(record, "input_tokens", 0) > 0
                    or getattr(record, "output_tokens", 0) > 0
                )
            ):
                provider = getattr(record, "provider_name", "deterministic") or "deterministic"
                model = getattr(record, "model_name", "test") or "test"
                with suppress(Exception):
                    self._budget_engine.record_model_call(
                        ModelUsage(
                            provider=provider,
                            model=model,
                            input_tokens=record.input_tokens,
                            output_tokens=record.output_tokens,
                        )
                    )
            self._emit(WorkflowHookEvent.NODE_COMPLETED, wf_ctx, "Investigator agent completed")

            event = TransitionEvent(
                claim_id=wf_ctx.claim_id,
                workflow_run_id=wf_ctx.workflow_run_id,
                trigger=Trigger.INVESTIGATION_SUCCEEDED,
                actor=AgentType.SUPERVISOR,
                correlation_id=wf_ctx.correlation_id,
                reason="Investigation succeeded",
            )
            return self._apply_and_persist(snapshot, event, run_ctx, wf_ctx)
        except AgentFailedError as exc:
            if exc.record:
                wf_ctx.record_execution(exc.record)
                self._persist_agent_execution(exc.record, "investigation", wf_ctx)
            self._emit(WorkflowHookEvent.NODE_FAILED, wf_ctx, f"Investigator failed: {exc}")
            event = TransitionEvent(
                claim_id=wf_ctx.claim_id,
                workflow_run_id=wf_ctx.workflow_run_id,
                trigger=Trigger.INVESTIGATION_FAILED,
                actor=AgentType.SUPERVISOR,
                correlation_id=wf_ctx.correlation_id,
                reason=f"Investigation failed: {exc}",
            )
            return self._apply_and_persist(snapshot, event, run_ctx, wf_ctx)

    def _step_reviewer(
        self,
        wf_ctx: WorkflowContext,
        run_ctx: RunContext,
        snapshot: WorkflowSnapshot,
    ) -> WorkflowSnapshot:
        if self._budget_engine is not None:
            res = self._budget_engine.pre_step(ReserveKind.AGENT_STEP)
            if not res.granted and res.reason is not None:
                dest = to_workflow_state(res.reason)
                trigger = (
                    Trigger.BUDGET_EXHAUSTED
                    if dest == WorkflowState.BUDGET_EXHAUSTED
                    else (
                        Trigger.STEPS_EXHAUSTED
                        if dest == WorkflowState.MAX_STEPS_EXCEEDED
                        else Trigger.WORKFLOW_TIMED_OUT
                    )
                )
                event = TransitionEvent(
                    claim_id=wf_ctx.claim_id,
                    workflow_run_id=wf_ctx.workflow_run_id,
                    trigger=trigger,
                    actor=AgentType.SUPERVISOR,
                    correlation_id=wf_ctx.correlation_id,
                    reason=f"Budget agent step denied: {res.reason.value}",
                )
                return self._apply_and_persist(snapshot, event, run_ctx, wf_ctx)

        if wf_ctx.extraction_result is None or wf_ctx.investigation_result is None:
            raise ValueError("ExtractionResult and InvestigationResult required before review")

        req = ReviewRequest(
            workflow_id=wf_ctx.workflow_run_id,
            extraction_result=wf_ctx.extraction_result,
            investigation_result=wf_ctx.investigation_result,
            rework_count=snapshot.rework_count,
        )
        envelope = ContractEnvelope.wrap(
            "review_request",
            req,
            claim_id=wf_ctx.claim_id,
            workflow_run_id=wf_ctx.workflow_run_id,
        )
        unwrapped = envelope.unwrap(ReviewRequest)

        agent_ctx = AgentContext(
            workflow_run_id=wf_ctx.workflow_run_id,
            claim_id=wf_ctx.claim_id,
            correlation_id=wf_ctx.correlation_id,
            agent=AgentType.REVIEWER,
            max_attempts=self._max_agent_retries,
        )

        self._emit(WorkflowHookEvent.NODE_STARTED, wf_ctx, "Reviewer agent started")
        try:
            result, record = self._reviewer.run(unwrapped, agent_ctx)
            result_envelope = ContractEnvelope.wrap(
                "review_result",
                result,
                claim_id=wf_ctx.claim_id,
                workflow_run_id=wf_ctx.workflow_run_id,
            )
            validated_result = result_envelope.unwrap(ReviewResult)
            wf_ctx.set_review_result(validated_result)
            wf_ctx.record_execution(record)
            self._persist_agent_execution(record, "review", wf_ctx)
            if (
                self._budget_engine is not None
                and record is not None
                and (
                    getattr(record, "input_tokens", 0) > 0
                    or getattr(record, "output_tokens", 0) > 0
                )
            ):
                provider = getattr(record, "provider_name", "deterministic") or "deterministic"
                model = getattr(record, "model_name", "test") or "test"
                with suppress(Exception):
                    self._budget_engine.record_model_call(
                        ModelUsage(
                            provider=provider,
                            model=model,
                            input_tokens=record.input_tokens,
                            output_tokens=record.output_tokens,
                        )
                    )
            self._emit(WorkflowHookEvent.NODE_COMPLETED, wf_ctx, "Reviewer agent completed")

            if result.decision == "APPROVE":
                event = TransitionEvent(
                    claim_id=wf_ctx.claim_id,
                    workflow_run_id=wf_ctx.workflow_run_id,
                    trigger=Trigger.REVIEW_APPROVED,
                    actor=AgentType.SUPERVISOR,
                    correlation_id=wf_ctx.correlation_id,
                    reason="Review recommended approval",
                )
                return self._apply_and_persist(snapshot, event, run_ctx, wf_ctx)
            elif result.decision == "REWORK":
                if self._budget_engine is not None:
                    rework_res = self._budget_engine.pre_step(ReserveKind.REWORK_CYCLE)
                    if not rework_res.granted:
                        event = TransitionEvent(
                            claim_id=wf_ctx.claim_id,
                            workflow_run_id=wf_ctx.workflow_run_id,
                            trigger=Trigger.REWORK_EXHAUSTED,
                            actor=AgentType.SUPERVISOR,
                            correlation_id=wf_ctx.correlation_id,
                            reason="Reviewer rework budget exhausted",
                        )
                        return self._apply_and_persist(snapshot, event, run_ctx, wf_ctx)

                if snapshot.rework_count >= run_ctx.max_rework_cycles:
                    event = TransitionEvent(
                        claim_id=wf_ctx.claim_id,
                        workflow_run_id=wf_ctx.workflow_run_id,
                        trigger=Trigger.REWORK_EXHAUSTED,
                        actor=AgentType.SUPERVISOR,
                        correlation_id=wf_ctx.correlation_id,
                        reason="Reviewer rework cycles exhausted",
                    )
                    return self._apply_and_persist(snapshot, event, run_ctx, wf_ctx)

                # Transition to REWORK_LOOP then to INVESTIGATION
                event_rework = TransitionEvent(
                    claim_id=wf_ctx.claim_id,
                    workflow_run_id=wf_ctx.workflow_run_id,
                    trigger=Trigger.REWORK_REQUESTED,
                    actor=AgentType.SUPERVISOR,
                    correlation_id=wf_ctx.correlation_id,
                    reason=result.rework_feedback or "Review requested rework",
                )
                snap_rework = self._apply_and_persist(snapshot, event_rework, run_ctx, wf_ctx)
                self._emit(
                    WorkflowHookEvent.REWORK_REQUESTED,
                    wf_ctx,
                    result.rework_feedback or "Rework requested",
                )

                event_dispatch = TransitionEvent(
                    claim_id=wf_ctx.claim_id,
                    workflow_run_id=wf_ctx.workflow_run_id,
                    trigger=Trigger.REWORK_DISPATCHED,
                    actor=AgentType.SUPERVISOR,
                    correlation_id=wf_ctx.correlation_id,
                    reason="Rework dispatched to investigation",
                )
                return self._apply_and_persist(snap_rework, event_dispatch, run_ctx, wf_ctx)
            elif result.decision == "REJECT":
                event = TransitionEvent(
                    claim_id=wf_ctx.claim_id,
                    workflow_run_id=wf_ctx.workflow_run_id,
                    trigger=Trigger.REVIEW_REJECTED,
                    actor=AgentType.SUPERVISOR,
                    correlation_id=wf_ctx.correlation_id,
                    reason="Review recommended rejection",
                )
                return self._apply_and_persist(snapshot, event, run_ctx, wf_ctx)
            else:
                raise ValueError(f"Unknown reviewer decision {result.decision}")
        except AgentFailedError as exc:
            if exc.record:
                wf_ctx.record_execution(exc.record)
                self._persist_agent_execution(exc.record, "review", wf_ctx)
            self._emit(WorkflowHookEvent.NODE_FAILED, wf_ctx, f"Reviewer failed: {exc}")
            event = TransitionEvent(
                claim_id=wf_ctx.claim_id,
                workflow_run_id=wf_ctx.workflow_run_id,
                trigger=Trigger.NODE_FAILED,
                actor=AgentType.SUPERVISOR,
                correlation_id=wf_ctx.correlation_id,
                reason=f"Reviewer failed: {exc}",
            )
            return self._apply_and_persist(snapshot, event, run_ctx, wf_ctx)

    def _emit(self, event: WorkflowHookEvent, wf_ctx: WorkflowContext, detail: str) -> None:
        self._sink.emit(
            HookPayload(
                event=event,
                workflow_run_id=wf_ctx.workflow_run_id,
                claim_id=wf_ctx.claim_id,
                correlation_id=wf_ctx.correlation_id,
                detail=detail,
            )
        )

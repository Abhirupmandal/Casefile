"""
Deterministic replay (Phase 7 §9–§12).

ReplayMode is explicit and visible in every report. Replay serves agent
outputs from checkpoint recordings and tool outputs from recorded
results — a missing recording fails replay loudly instead of calling
live providers or networks.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, Field

from casefile.agents.deterministic import DeterministicProvider
from casefile.agents.providers import LLMRequest, LLMResponse, ProviderError
from casefile.checkpoint.model import Checkpoint, CheckpointKind
from casefile.checkpoint.repository import CheckpointRepository
from casefile.checkpoint.resume import ResumeResult, resume_from_checkpoint
from casefile.models.contracts import WorkflowState
from casefile.models.domain import AgentType
from casefile.tools.contracts import (
    ToolContext,
    ToolError,
    ToolErrorCode,
    ToolFailureError,
    ToolOutcome,
    ToolStatus,
)
from casefile.tools.registry import ToolRegistry
from casefile.tools.registry import idempotency_key_for as tool_key_for
from casefile.workflow.context import RunContext, WorkflowSnapshot
from casefile.workflow.engine import WorkflowEngine
from casefile.workflow.hooks import EventSink, NullSink, WorkflowHookEvent
from casefile.workflow.transitions import TransitionEvent
from casefile.workflow.triggers import Trigger

if TYPE_CHECKING:
    from casefile.observability.provider import Observability


class ReplayMode(str, Enum):
    """Explicit execution mode, visible in reports and metadata."""

    LIVE = "LIVE"
    REPLAY = "REPLAY"


class ReplayMissingArtifactError(ProviderError):
    """A required recording is absent: replay fails, never calls live."""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


class ReplayAgentProvider(DeterministicProvider):
    """Agent outputs served strictly from checkpoint recordings.

    Recordings match by requested contract type
    (`response_schema_name`). A missing or exhausted contract fails
    loudly — the live provider is never consulted.
    """

    def __init__(self, checkpoint: Checkpoint) -> None:
        super().__init__(provider_name="replay")
        self._by_contract: dict[str, list[dict[str, object]]] = {}
        for recorded in checkpoint.recorded_agents:
            payload = json.loads(recorded.output_json)
            if not isinstance(payload, dict):
                raise ReplayMissingArtifactError("Recorded agent output is not a JSON object")
            self._by_contract.setdefault(recorded.contract_type, []).append(payload)

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Serve the next recording for the requested contract, else fail."""
        from casefile.models.envelope import HANDOFF_ROUTES

        wanted = {
            contract
            for contract, route in HANDOFF_ROUTES.items()
            if route.payload_model.__name__ == request.response_schema_name
        }
        wanted.add(request.response_schema_name)
        for contract in sorted(wanted):
            queue = self._by_contract.get(contract, [])
            if queue:
                payload = queue.pop(0)
                return LLMResponse(
                    request_id=request.request_id,
                    content=json.dumps(payload, sort_keys=True),
                    model=request.model,
                    provider=self.name,
                )
        raise ReplayMissingArtifactError(
            f"Replay has no recorded output for {request.response_schema_name!r}; "
            "refusing live call"
        )


class ReplayToolRegistry(ToolRegistry):
    """Tool outputs served strictly from checkpoint recordings.

    Lookups key on the same stable idempotency key the live path used.
    A miss raises a typed replay-missing error instead of executing.
    """

    def __init__(self, recordings: dict[str, str], sink: EventSink | None = None) -> None:
        super().__init__(sink=sink or NullSink())
        self._recordings = dict(recordings)

    def execute(
        self,
        tool_name: str,
        tool_version: str,
        raw_input: dict[str, object],
        ctx: ToolContext,
        obs: Observability | None = None,
    ) -> ToolOutcome:
        """Serve a recorded result or fail without executing."""
        key = tool_key_for(
            ctx.workflow_run_id,
            ctx.execution_id,
            tool_name,
            json.dumps(raw_input, sort_keys=True, default=str),
        )
        recorded = self._recordings.get(key)
        if recorded is None:
            raise ToolFailureError(
                ToolError(
                    code=ToolErrorCode.REPLAY_ARTIFACT_MISSING,
                    message=f"No recorded output for {tool_name!r} in replay",
                )
            )
        tool = self._resolve(tool_name)
        data = json.loads(recorded)
        if not isinstance(data, dict):
            raise ToolFailureError(
                ToolError(
                    code=ToolErrorCode.REPLAY_ARTIFACT_MISSING,
                    message=f"Recorded output for {tool_name!r} is corrupt",
                )
            )
        output = tool.output_model.model_validate(data)
        self._emit(WorkflowHookEvent.TOOL_COMPLETED, ctx, tool_name, "replay")
        return ToolOutcome(
            invocation_id=ctx.execution_id,
            tool_name=tool_name,
            tool_version=tool.version,
            status=ToolStatus.SUCCESS,
            output=output,
            idempotency_key=key,
        )


class ReplayReport(BaseModel):
    """Outcome of one replay run: path, terminal, and mode."""

    mode: ReplayMode = ReplayMode.REPLAY
    workflow_run_id: UUID
    claim_id: UUID
    states: list[WorkflowState] = Field(default_factory=list)
    terminal_state: WorkflowState | None = None
    steps: int = 0
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def run_replay(
    checkpoint: Checkpoint,
    remaining: list[tuple[Trigger, AgentType]],
    *,
    engine: WorkflowEngine,
    ctx: RunContext,
    obs: Observability | None = None,
) -> ReplayReport:
    """Deterministically re-apply remaining triggers from a checkpoint.

    Pure engine replay: same triggers in the same order from the same
    snapshot reproduce the same state path. Agent/tool recordings are
    consumed by the caller through ReplayAgentProvider/ReplayToolRegistry.
    """
    from casefile.observability.context import ExecMode, TraceContext
    from casefile.observability.spans import SPAN_REPLAY_RUN
    from casefile.observability.tracer import maybe_span

    trace = TraceContext(
        workflow_run_id=checkpoint.workflow_run_id,
        claim_id=checkpoint.claim_id,
        checkpoint_id=checkpoint.checkpoint_id,
        correlation_id=checkpoint.correlation_id,
        mode=ExecMode.REPLAY,
    )
    tracer = obs.tracer if obs is not None else None
    with maybe_span(tracer, SPAN_REPLAY_RUN, {"casefile.mode": "REPLAY"}, trace) as span:
        try:
            report = _replay_inner(checkpoint, remaining, engine, ctx)
        except Exception as exc:
            span.set_status_error(type(exc).__name__)
            raise
        span.set_attribute("casefile.success", True)
        if report.terminal_state is not None:
            span.set_attribute("casefile.terminal_state", report.terminal_state.value)
        if obs is not None:
            obs.meters.record_counter("casefile.workflow.runs", 1.0, {"outcome": "replayed"})
        return report


def _replay_inner(
    checkpoint: Checkpoint,
    remaining: list[tuple[Trigger, AgentType]],
    engine: WorkflowEngine,
    ctx: RunContext,
) -> ReplayReport:
    """Uninstrumented replay body (logic unchanged)."""
    snapshot = checkpoint.snapshot
    states = [snapshot.current_state]
    for trigger, actor in remaining:
        result = engine.apply(
            snapshot,
            TransitionEvent(
                claim_id=checkpoint.claim_id,
                workflow_run_id=checkpoint.workflow_run_id,
                trigger=trigger,
                actor=actor,
            ),
            ctx,
        )
        snapshot = result.snapshot
        states.append(snapshot.current_state)
    terminal = snapshot.current_state if snapshot.current_state.is_terminal else None
    return ReplayReport(
        workflow_run_id=checkpoint.workflow_run_id,
        claim_id=checkpoint.claim_id,
        states=states,
        terminal_state=terminal,
        steps=len(remaining),
    )


def resume_for_replay(
    checkpoint_id: UUID,
    *,
    checkpoints: CheckpointRepository,
    max_rework_cycles: int = 3,
) -> ResumeResult:
    """Resume helper bound to REPLAY mode (same guarantees as live resume)."""
    return resume_from_checkpoint(
        checkpoint_id, checkpoints=checkpoints, max_rework_cycles=max_rework_cycles
    )


class ReplayMismatch(BaseModel):
    """Explicit structured result when replay diverges from original run."""

    first_divergence_index: int = Field(ge=0)
    step_name: str
    expected_value: str
    actual_value: str
    message: str


class ReplayResult(BaseModel):
    """Full outcome of replay comparison against original execution."""

    original_workflow_run_id: UUID
    replay_run_id: UUID
    original_terminal_state: WorkflowState | None = None
    replay_terminal_state: WorkflowState | None = None
    original_state_sequence: list[WorkflowState] = Field(default_factory=list)
    replay_state_sequence: list[WorkflowState] = Field(default_factory=list)
    original_transition_sequence: list[str] = Field(default_factory=list)
    replay_transition_sequence: list[str] = Field(default_factory=list)
    matching_status: bool
    mismatch_details: ReplayMismatch | None = None


def compare_replay_runs(
    *,
    original_workflow_run_id: UUID,
    replay_run_id: UUID,
    original_states: list[WorkflowState],
    replay_states: list[WorkflowState],
    original_transitions: list[str],
    replay_transitions: list[str],
    original_terminal: WorkflowState | None,
    replay_terminal: WorkflowState | None,
) -> ReplayResult:
    """Compare an original run against a replay run and detect any divergence."""
    # Compare state sequence
    for idx, (orig, rep) in enumerate(zip(original_states, replay_states, strict=False)):
        if orig != rep:
            return ReplayResult(
                original_workflow_run_id=original_workflow_run_id,
                replay_run_id=replay_run_id,
                original_terminal_state=original_terminal,
                replay_terminal_state=replay_terminal,
                original_state_sequence=original_states,
                replay_state_sequence=replay_states,
                original_transition_sequence=original_transitions,
                replay_transition_sequence=replay_transitions,
                matching_status=False,
                mismatch_details=ReplayMismatch(
                    first_divergence_index=idx,
                    step_name=f"state_step_{idx}",
                    expected_value=orig.value,
                    actual_value=rep.value,
                    message=f"State divergence at step {idx}: expected {orig.value}, got {rep.value}",
                ),
            )

    if len(original_states) != len(replay_states):
        idx = min(len(original_states), len(replay_states))
        orig_val = original_states[idx].value if idx < len(original_states) else "<none>"
        rep_val = replay_states[idx].value if idx < len(replay_states) else "<none>"
        return ReplayResult(
            original_workflow_run_id=original_workflow_run_id,
            replay_run_id=replay_run_id,
            original_terminal_state=original_terminal,
            replay_terminal_state=replay_terminal,
            original_state_sequence=original_states,
            replay_state_sequence=replay_states,
            original_transition_sequence=original_transitions,
            replay_transition_sequence=replay_transitions,
            matching_status=False,
            mismatch_details=ReplayMismatch(
                first_divergence_index=idx,
                step_name="state_sequence_length",
                expected_value=orig_val,
                actual_value=rep_val,
                message=f"State sequence length mismatch: expected {len(original_states)}, got {len(replay_states)}",
            ),
        )

    # Compare transition sequence
    for idx, (orig_t, rep_t) in enumerate(
        zip(original_transitions, replay_transitions, strict=False)
    ):
        if orig_t != rep_t:
            return ReplayResult(
                original_workflow_run_id=original_workflow_run_id,
                replay_run_id=replay_run_id,
                original_terminal_state=original_terminal,
                replay_terminal_state=replay_terminal,
                original_state_sequence=original_states,
                replay_state_sequence=replay_states,
                original_transition_sequence=original_transitions,
                replay_transition_sequence=replay_transitions,
                matching_status=False,
                mismatch_details=ReplayMismatch(
                    first_divergence_index=idx,
                    step_name=f"transition_step_{idx}",
                    expected_value=orig_t,
                    actual_value=rep_t,
                    message=f"Transition divergence at step {idx}: expected {orig_t}, got {rep_t}",
                ),
            )

    if len(original_transitions) != len(replay_transitions):
        idx = min(len(original_transitions), len(replay_transitions))
        orig_val = original_transitions[idx] if idx < len(original_transitions) else "<none>"
        rep_val = replay_transitions[idx] if idx < len(replay_transitions) else "<none>"
        return ReplayResult(
            original_workflow_run_id=original_workflow_run_id,
            replay_run_id=replay_run_id,
            original_terminal_state=original_terminal,
            replay_terminal_state=replay_terminal,
            original_state_sequence=original_states,
            replay_state_sequence=replay_states,
            original_transition_sequence=original_transitions,
            replay_transition_sequence=replay_transitions,
            matching_status=False,
            mismatch_details=ReplayMismatch(
                first_divergence_index=idx,
                step_name="transition_sequence_length",
                expected_value=orig_val,
                actual_value=rep_val,
                message=f"Transition sequence length mismatch: expected {len(original_transitions)}, got {len(replay_transitions)}",
            ),
        )

    # Terminal state check
    if original_terminal != replay_terminal:
        return ReplayResult(
            original_workflow_run_id=original_workflow_run_id,
            replay_run_id=replay_run_id,
            original_terminal_state=original_terminal,
            replay_terminal_state=replay_terminal,
            original_state_sequence=original_states,
            replay_state_sequence=replay_states,
            original_transition_sequence=original_transitions,
            replay_transition_sequence=replay_transitions,
            matching_status=False,
            mismatch_details=ReplayMismatch(
                first_divergence_index=len(original_states) - 1,
                step_name="terminal_state",
                expected_value=str(original_terminal),
                actual_value=str(replay_terminal),
                message=f"Terminal state mismatch: expected {original_terminal}, got {replay_terminal}",
            ),
        )

    return ReplayResult(
        original_workflow_run_id=original_workflow_run_id,
        replay_run_id=replay_run_id,
        original_terminal_state=original_terminal,
        replay_terminal_state=replay_terminal,
        original_state_sequence=original_states,
        replay_state_sequence=replay_states,
        original_transition_sequence=original_transitions,
        replay_transition_sequence=replay_transitions,
        matching_status=True,
        mismatch_details=None,
    )


def replay_workflow(
    workflow_run_id: UUID,
    *,
    checkpoints: CheckpointRepository,
    engine: WorkflowEngine | None = None,
    divergent_trigger: Trigger | None = None,
) -> ReplayResult:
    """Replay a stored workflow run from checkpoints and verify matching.

    Replay re-applies every path entry from the latest checkpoint starting
    from the implicit initial RECEIVED state.  The checkpoint path records
    ALL transitions from the very first one (e.g. CLAIM_VALIDATED:
    RECEIVED → EXTRACTION), so we must start from RECEIVED rather than
    from the first checkpoint's snapshot (which is already post-transition).
    """
    from uuid import uuid4

    from casefile.checkpoint.model import CheckpointNotFoundError

    run_checkpoints = checkpoints.list_for_run(workflow_run_id)
    if not run_checkpoints:
        raise CheckpointNotFoundError(f"No checkpoints found for run {workflow_run_id}")

    first_cp = run_checkpoints[0]
    latest_cp = run_checkpoints[-1]

    # Checkpoints are created after transitions (never at RECEIVED), so the
    # original state sequence does not include the implicit starting state.
    # Prepend RECEIVED so original_states matches the replay sequence which
    # starts from RECEIVED before any transition is applied.
    original_states = [WorkflowState.RECEIVED] + [cp.state for cp in run_checkpoints]
    original_transitions = [entry.trigger.value for entry in latest_cp.path]
    original_terminal = latest_cp.state if latest_cp.state.is_terminal else None

    replay_run_id = uuid4()
    replay_engine = engine or WorkflowEngine()
    replay_ctx = RunContext(
        claim_id=first_cp.claim_id,
        workflow_run_id=first_cp.workflow_run_id,
        correlation_id=first_cp.correlation_id,
    )

    # The checkpoint path records ALL transitions including the very first one
    # (CLAIM_VALIDATED: RECEIVED -> EXTRACTION).  We must start replay from
    # the implicit RECEIVED state so that every path entry is valid.
    initial_snapshot = WorkflowSnapshot(
        workflow_run_id=first_cp.workflow_run_id,
        claim_id=first_cp.claim_id,
        current_state=WorkflowState.RECEIVED,
        step_count=0,
        rework_count=0,
    )
    # Construct a synthetic initial checkpoint at RECEIVED as the starting point.
    initial_cp = Checkpoint(
        claim_id=first_cp.claim_id,
        workflow_run_id=first_cp.workflow_run_id,
        execution_id=first_cp.execution_id,
        sequence_no=0,
        state=WorkflowState.RECEIVED,
        kind=CheckpointKind.TRANSITION,
        snapshot=initial_snapshot,
        step_count=0,
        rework_count=0,
        recorded_tools=first_cp.recorded_tools,
        recorded_agents=first_cp.recorded_agents,
        correlation_id=first_cp.correlation_id,
        schema_version=first_cp.schema_version,
        application_version=first_cp.application_version,
    )

    remaining_triggers: list[tuple[Trigger, AgentType]] = [
        (entry.trigger, entry.actor) for entry in latest_cp.path
    ]
    if divergent_trigger is not None and remaining_triggers:
        remaining_triggers[-1] = (divergent_trigger, remaining_triggers[-1][1])

    report = run_replay(initial_cp, remaining_triggers, engine=replay_engine, ctx=replay_ctx)

    replay_states = report.states
    replay_transitions = [t[0].value for t in remaining_triggers]
    replay_terminal = report.terminal_state

    return compare_replay_runs(
        original_workflow_run_id=workflow_run_id,
        replay_run_id=replay_run_id,
        original_states=original_states,
        replay_states=replay_states,
        original_transitions=original_transitions,
        replay_transitions=replay_transitions,
        original_terminal=original_terminal,
        replay_terminal=replay_terminal,
    )


replay_from_checkpoints = replay_workflow

"""
Budget engine: durable, concurrency-safe enforcement (Phase 8).

Owns one run's envelope + usage. Discrete units (steps, agent steps,
tool calls, rework cycles, retries) are consumed through atomic
single-statement counter claims — exactly one concurrent worker wins.
Tokens are reserved pre-call and reconciled post-call; cost derives from
versioned pricing. Wall-clock derives from the durable start timestamp,
so restarts inherit consumed time. Terminal reasons latch monotonically:
once set, every gate refuses further work, including stale workers and
resumes.

Fail-closed: anything that cannot be authoritatively decided denies
execution. Reservations are non-refundable consumption claims keyed by
idempotency key — a retry with the same key replays the prior verdict
instead of consuming again (see IdempotencyLedger integration in the
orchestration layer); a crash between reserve and ledger-record costs at
most one unit and never leaks unboundedly.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker

from casefile.budget.envelope import BudgetEnvelope
from casefile.budget.pricing import ModelUsage, PricingTable, calculate_cost
from casefile.budget.termination import BudgetTermination, to_workflow_state
from casefile.budget.usage import BudgetUsage
from casefile.models.contracts import BudgetState, WorkflowState
from casefile.models.versioning import SchemaVersion
from casefile.storage.errors import PersistenceError, PersistenceErrorCode
from casefile.storage.unit_of_work import UnitOfWork

if TYPE_CHECKING:
    from casefile.observability.provider import Observability
from casefile.workflow.context import Clock


class ReserveKind(str):
    """Discrete reservable unit (string constants, validated at use)."""

    WORKFLOW_STEP = "steps"
    AGENT_STEP = "agent_steps"
    TOOL_CALL = "tool_calls"
    REWORK_CYCLE = "rework_cycles"
    AGENT_RETRY = "agent_retries"


_RESERVE_LIMITS = {
    ReserveKind.WORKFLOW_STEP: "max_steps",
    ReserveKind.AGENT_STEP: "max_agent_steps",
    ReserveKind.TOOL_CALL: "max_tool_calls",
    ReserveKind.REWORK_CYCLE: "max_rework_cycles",
    ReserveKind.AGENT_RETRY: "max_agent_retries",
}

_RESERVE_REASONS = {
    ReserveKind.WORKFLOW_STEP: BudgetTermination.MAX_STEPS_EXCEEDED,
    ReserveKind.AGENT_STEP: BudgetTermination.MAX_AGENT_STEPS_EXCEEDED,
    ReserveKind.TOOL_CALL: BudgetTermination.MAX_TOOL_CALLS_EXCEEDED,
    ReserveKind.REWORK_CYCLE: BudgetTermination.MAX_REWORK_EXCEEDED,
    ReserveKind.AGENT_RETRY: BudgetTermination.MAX_AGENT_RETRIES_EXCEEDED,
}


class Reservation(BaseModel):
    """Outcome of one atomic budget claim. Frozen."""

    model_config = {"frozen": True}

    granted: bool
    reason: BudgetTermination | None = None
    workflow_state: WorkflowState | None = None
    remaining_after: int = Field(default=0, ge=0)
    schema_version: SchemaVersion = "1.0.0"


class BudgetExhaustedError(Exception):
    """Raised when a gate denies work. Carries the structured reason."""

    def __init__(self, reason: BudgetTermination) -> None:
        super().__init__(f"Budget exhausted: {reason.value}")
        self.reason = reason
        self.workflow_state = to_workflow_state(reason)


class BudgetEngine:
    """Durable enforcement for one workflow run."""

    def __init__(
        self,
        run_id: UUID,
        envelope: BudgetEnvelope,
        usage: BudgetUsage,
        clock: Clock,
        sessions: sessionmaker[Session],
        pricing: PricingTable | None = None,
    ) -> None:
        self._run_id = run_id
        self._envelope = envelope
        self._usage = usage
        self._clock = clock
        self._sessions = sessions
        self._pricing = pricing or PricingTable()

    @property
    def run_id(self) -> UUID:
        """Workflow run under enforcement."""
        return self._run_id

    @property
    def envelope(self) -> BudgetEnvelope:
        """Immutable run envelope."""
        return self._envelope

    @property
    def usage(self) -> BudgetUsage:
        """Live usage counters."""
        return self._usage

    def start_run(self) -> None:
        """Persist envelope + zeroed counters. Fails if the run exists."""
        with UnitOfWork(self._sessions) as uow:
            try:
                uow.run_budgets.get_envelope(self._run_id)
                raise PersistenceError(
                    PersistenceErrorCode.INTEGRITY_VIOLATION,
                    f"Run {self._run_id} already has a budget envelope",
                )
            except PersistenceError as exc:
                if exc.code != PersistenceErrorCode.NOT_FOUND:
                    raise
            uow.run_budgets.save_envelope(self._run_id, self._envelope.model_dump_json())
            uow.counters.init_counters(self._run_id, self._usage.wall_start)

    def restore(self) -> BudgetEngine:
        """Reload envelope + usage from durable state (restart/resume)."""
        with UnitOfWork(self._sessions) as uow:
            envelope = BudgetEnvelope.model_validate_json(
                uow.run_budgets.get_envelope(self._run_id)
            )
            counts = uow.counters.get_counts(self._run_id)
            cost, wall_start = uow.counters.get_cost_and_start(self._run_id)
        usage = BudgetUsage(
            steps=counts.get("steps", 0),
            agent_steps=counts.get("agent_steps", 0),
            tool_calls=counts.get("tool_calls", 0),
            rework_cycles=counts.get("rework_cycles", 0),
            agent_retries=counts.get("agent_retries", 0),
            input_tokens=counts.get("input_tokens", 0),
            output_tokens=counts.get("output_tokens", 0),
            total_tokens=counts.get("total_tokens", 0),
            cost_usd=cost,
            wall_start=wall_start or self._usage.wall_start,
        )
        return BudgetEngine(
            run_id=self._run_id,
            envelope=envelope,
            usage=usage,
            clock=self._clock,
            sessions=self._sessions,
            pricing=self._pricing,
        )

    def is_terminated(self) -> BudgetTermination | None:
        """Latched terminal reason, or None while the run is live."""
        with UnitOfWork(self._sessions) as uow:
            reason = uow.run_budgets.get_terminal(self._run_id)
        if reason is None:
            return None
        return BudgetTermination(reason)

    def terminate(
        self, reason: BudgetTermination, obs: Observability | None = None
    ) -> WorkflowState:
        """Latch a terminal reason monotonically. Second call fails."""
        from casefile.observability.spans import SPAN_BUDGET_CHECK, budget_attributes
        from casefile.observability.tracer import maybe_span

        tracer = obs.tracer if obs is not None else None
        attributes = budget_attributes(termination_reason=reason.value)
        with maybe_span(tracer, SPAN_BUDGET_CHECK, attributes) as span:
            with UnitOfWork(self._sessions) as uow:
                uow.run_budgets.set_terminal(self._run_id, reason.value)
            state = to_workflow_state(reason)
            span.set_attribute("casefile.terminal_state", state.value)
            if obs is not None:
                obs.meters.record_counter(
                    "casefile.budget.exhaustions", 1.0, {"reason": reason.value}
                )
            return state

    def check_all(self) -> BudgetTermination | None:
        """Evaluate every dimension: terminal latch, wall-clock, tokens, cost."""
        latched = self.is_terminated()
        if latched is not None:
            return latched
        elapsed = self._usage.elapsed_seconds(self._clock.now())
        if elapsed >= self._envelope.max_wall_clock_seconds:
            return BudgetTermination.MAX_WALL_CLOCK_EXCEEDED
        usage, envelope = self._usage, self._envelope
        if usage.input_tokens >= envelope.max_input_tokens:
            return BudgetTermination.MAX_INPUT_TOKENS_EXCEEDED
        if usage.output_tokens >= envelope.max_output_tokens:
            return BudgetTermination.MAX_OUTPUT_TOKENS_EXCEEDED
        if usage.total_tokens >= envelope.max_total_tokens:
            return BudgetTermination.MAX_TOTAL_TOKENS_EXCEEDED
        if usage.cost_usd >= envelope.max_cost_usd:
            return BudgetTermination.MAX_COST_EXCEEDED
        return None

    def pre_step(self, kind: str, obs: Observability | None = None) -> Reservation:
        """Gate one discrete unit: terminal latch, limit check, atomic claim."""
        from casefile.observability.spans import SPAN_BUDGET_CHECK
        from casefile.observability.tracer import maybe_span

        tracer = obs.tracer if obs is not None else None
        with maybe_span(tracer, SPAN_BUDGET_CHECK, {"casefile.budget_kind": kind}) as span:
            reservation = self._pre_step_inner(kind)
            span.set_attribute("casefile.reservation_granted", reservation.granted)
            if reservation.reason is not None:
                span.set_attribute("casefile.termination_reason", reservation.reason.value)
            if obs is not None and not reservation.granted and reservation.reason is not None:
                obs.meters.record_counter(
                    "casefile.budget.exhaustions",
                    1.0,
                    {"reason": reservation.reason.value},
                )
            return reservation

    def _pre_step_inner(self, kind: str) -> Reservation:
        """Uninstrumented gate body (logic unchanged)."""
        latched = self.is_terminated()
        if latched is not None:
            return Reservation(
                granted=False, reason=latched, workflow_state=to_workflow_state(latched)
            )
        if kind not in _RESERVE_LIMITS:
            raise PersistenceError(
                PersistenceErrorCode.VALIDATION_FAILED, f"Unknown reserve kind {kind!r}"
            )
        limit = getattr(self._envelope, _RESERVE_LIMITS[kind])
        with UnitOfWork(self._sessions) as uow:
            granted = uow.counters.reserve(self._run_id, kind, limit)
            counts = uow.counters.get_counts(self._run_id)
        if not granted:
            reason = _RESERVE_REASONS[kind]
            return Reservation(
                granted=False,
                reason=reason,
                workflow_state=to_workflow_state(reason),
                remaining_after=0,
            )
        self._bump(kind, 1)
        used = counts.get(kind, 1)
        return Reservation(granted=True, remaining_after=max(0, limit - used))

    def reserve_tokens(self, input_tokens: int, output_tokens: int) -> Reservation:
        """Atomically reserve token usage pre-call (fail-closed on race)."""
        latched = self.is_terminated()
        if latched is not None:
            return Reservation(
                granted=False, reason=latched, workflow_state=to_workflow_state(latched)
            )
        if input_tokens < 0 or output_tokens < 0:
            raise PersistenceError(
                PersistenceErrorCode.VALIDATION_FAILED, "Token reservation cannot be negative"
            )
        with UnitOfWork(self._sessions) as uow:
            granted = uow.counters.reserve_tokens(
                self._run_id, input_tokens, output_tokens, self._envelope.max_total_tokens
            )
            counts = uow.counters.get_counts(self._run_id)
        if not granted:
            return Reservation(
                granted=False,
                reason=BudgetTermination.MAX_TOTAL_TOKENS_EXCEEDED,
                workflow_state=to_workflow_state(BudgetTermination.MAX_TOTAL_TOKENS_EXCEEDED),
                remaining_after=0,
            )
        self._usage.input_tokens += input_tokens
        self._usage.output_tokens += output_tokens
        self._usage.totalize_tokens()
        remaining = max(0, self._envelope.max_total_tokens - counts.get("total_tokens", 0))
        return Reservation(granted=True, remaining_after=remaining)

    def record_model_call(self, usage: ModelUsage, obs: Observability | None = None) -> Decimal:
        """Price one model call from the versioned table and persist cost."""
        pricing = self._pricing.rate_for(usage.provider, usage.model)
        cost = calculate_cost(usage, pricing)
        self._usage.input_tokens += usage.input_tokens
        self._usage.output_tokens += usage.output_tokens
        self._usage.totalize_tokens()
        self._usage.cost_usd += cost
        with UnitOfWork(self._sessions) as uow:
            uow.counters.add_cost(self._run_id, cost)
        if obs is not None:
            labels = {"provider": usage.provider, "model": usage.model, "mode": "LIVE"}
            obs.meters.record_histogram(
                "casefile.budget.tokens",
                float(usage.input_tokens + usage.output_tokens),
                dict(labels),
            )
            obs.meters.record_histogram("casefile.budget.cost", float(cost), dict(labels))
        return cost

    def record_tool_latency(self, latency_ms: int) -> None:
        """Accumulate tool latency metadata for Phase 8 enforcement input."""
        if latency_ms < 0:
            raise PersistenceError(
                PersistenceErrorCode.VALIDATION_FAILED, "Latency cannot be negative"
            )
        self._usage.tool_latency_ms += latency_ms

    def snapshot_usage(self) -> None:
        """Persist current usage counters for restart/resume restore."""
        with UnitOfWork(self._sessions) as uow:
            uow.budgets.save_snapshot(_usage_to_state(self._usage), self._run_id)

    def checkpoint_payload(self) -> dict[str, str]:
        """Envelope + usage JSON for checkpoint pinning."""
        return {
            "envelope_json": self._envelope.model_dump_json(),
            "usage_json": self._usage.model_dump_json(),
        }

    @staticmethod
    def usage_from_checkpoint_payload(usage_json: str) -> BudgetUsage:
        """Restore usage from a checkpoint payload (resume/replay)."""
        return BudgetUsage.model_validate_json(usage_json)

    def summary(self) -> dict[str, object]:
        """Structured observability snapshot for Phase 10 consumers."""
        latched = self.is_terminated()
        envelope_dump = self._envelope.model_dump(mode="json")
        usage_dump = self._usage.model_dump(mode="json")
        return {
            "run_id": str(self._run_id),
            "configured": envelope_dump,
            "consumed": usage_dump,
            "remaining_steps": max(0, self._envelope.max_steps - self._usage.steps),
            "remaining_tool_calls": max(0, self._envelope.max_tool_calls - self._usage.tool_calls),
            "remaining_total_tokens": max(
                0, self._envelope.max_total_tokens - self._usage.total_tokens
            ),
            "terminal_reason": latched.value if latched else None,
        }

    def _bump(self, kind: str, units: int) -> None:
        attr = {
            ReserveKind.WORKFLOW_STEP: "steps",
            ReserveKind.AGENT_STEP: "agent_steps",
            ReserveKind.TOOL_CALL: "tool_calls",
            ReserveKind.REWORK_CYCLE: "rework_cycles",
            ReserveKind.AGENT_RETRY: "agent_retries",
        }[kind]
        setattr(self._usage, attr, getattr(self._usage, attr) + units)


def _usage_to_state(usage: BudgetUsage) -> BudgetState:
    return BudgetState(
        input_tokens_used=usage.input_tokens,
        output_tokens_used=usage.output_tokens,
        total_tokens_used=usage.total_tokens,
        estimated_cost_usd=usage.cost_usd,
        steps_completed=usage.steps,
        tool_calls_made=usage.tool_calls,
        rework_count=usage.rework_cycles,
    )


def should_retry(attempt: int, usage: BudgetUsage, envelope: BudgetEnvelope) -> bool:
    """Retry guard for orchestration layers: bounded attempts + retry budget."""
    if attempt < 1:
        return False
    if attempt > envelope.max_agent_retries:
        return False
    return usage.agent_retries < envelope.max_agent_retries


def make_retry_guard(engine: BudgetEngine) -> Callable[[int], bool]:
    """Build an agent retry-guard backed by live budget usage.

    Reserves one retry unit per permitted attempt so retries consume
    durable budget; denials stop the agent with a structured error.
    """

    def _guard(next_attempt: int) -> bool:
        if not should_retry(next_attempt, engine.usage, engine.envelope):
            return False
        reservation = engine.pre_step(ReserveKind.AGENT_RETRY)
        return reservation.granted

    return _guard

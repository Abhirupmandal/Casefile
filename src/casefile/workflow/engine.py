"""
CASEFILE deterministic workflow engine (Phase 3 §3, §13, §14).

`WorkflowEngine.apply()` is a pure function of (snapshot, event, context):
same inputs ⇒ same outcome. No randomness, no wall-clock reads (timestamps
come from the injected clock), no LLM or external calls.

Guarantees:
- Terminal states are immutable (any event rejected)
- Unknown (state, trigger) pairs rejected with legal-trigger guidance
- Actor authorization enforced per transition rule
- Rework budget enforced against context.max_rework_cycles
- Snapshot schema versions validated under the Phase 2 policy
- Duplicate (event, execution identity) applications return the original
  record instead of advancing state twice (in-process; the distributed
  ledger arrives in Phase 7)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from casefile.models.versioning import validate_schema_version
from casefile.observability.context import TraceContext
from casefile.observability.spans import SPAN_TRANSITION, transition_attributes
from casefile.observability.tracer import maybe_span
from casefile.workflow.context import RunContext, WorkflowSnapshot
from casefile.workflow.states import is_terminal
from casefile.workflow.transitions import (
    ActorNotAllowedError,
    InvalidTransitionError,
    ReworkLimitError,
    TerminalStateError,
    TransitionEvent,
    TransitionRecord,
    rule_for,
)

if TYPE_CHECKING:
    from casefile.observability.provider import Observability


class ApplyResult(BaseModel):
    """Outcome of one engine application."""

    model_config = {"arbitrary_types_allowed": True}

    snapshot: WorkflowSnapshot
    record: TransitionRecord | None = None
    duplicate: bool = False
    terminated: bool = False
    steps_advanced: int = Field(default=0, ge=0)


def idempotency_key_for(event: TransitionEvent) -> str:
    """Deterministic key: same event + same execution identity ⇒ same key."""
    return f"{event.workflow_run_id}:{event.execution_id}:{event.trigger.value}"


class WorkflowEngine:
    """Deterministic state machine executor with rework and idempotency bounds."""

    def __init__(self) -> None:
        self._seen: dict[str, TransitionRecord] = {}
        self._sequence: int = 0

    @property
    def applied_count(self) -> int:
        """Number of non-duplicate transitions applied."""
        return self._sequence

    def apply(
        self,
        snapshot: WorkflowSnapshot,
        event: TransitionEvent,
        ctx: RunContext,
        obs: Observability | None = None,
    ) -> ApplyResult:
        """Apply one typed event; instrumented wrapper around _apply_inner."""
        trace = TraceContext(
            workflow_run_id=snapshot.workflow_run_id,
            execution_id=event.execution_id,
            claim_id=snapshot.claim_id,
            correlation_id=event.correlation_id,
        )
        attributes = transition_attributes(
            source_state=snapshot.current_state.value,
            destination_state="",
            trigger=event.trigger.value,
            actor=event.actor.value,
        )
        tracer = obs.tracer if obs is not None else None
        with maybe_span(tracer, SPAN_TRANSITION, attributes, trace) as span:
            try:
                result = self._apply_inner(snapshot, event, ctx)
            except Exception as exc:
                span.set_status_error(type(exc).__name__)
                span.set_attribute("casefile.outcome", "failure")
                raise
            span.set_attribute("casefile.destination_state", result.snapshot.current_state.value)
            span.set_attribute("casefile.outcome", "duplicate" if result.duplicate else "success")
            return result

    def _apply_inner(
        self, snapshot: WorkflowSnapshot, event: TransitionEvent, ctx: RunContext
    ) -> ApplyResult:
        """Deterministic transition logic (no telemetry inside)."""
        validate_schema_version(snapshot.schema_version, artifact_type="workflow_state")
        validate_schema_version(event.schema_version, artifact_type="workflow_state")
        self._check_identities(snapshot, event, ctx)

        if is_terminal(snapshot.current_state):
            raise TerminalStateError(
                f"Workflow {snapshot.workflow_run_id} is terminal "
                f"({snapshot.current_state.value}); event {event.trigger.value} rejected"
            )

        key = idempotency_key_for(event)
        if key in self._seen:
            original = self._seen[key]
            return ApplyResult(
                snapshot=snapshot,
                record=original,
                duplicate=True,
                terminated=is_terminal(original.destination),
                steps_advanced=0,
            )

        rule = rule_for(snapshot.current_state, event.trigger)
        if event.actor not in rule.allowed_actors:
            allowed = sorted(a.value for a in rule.allowed_actors)
            raise ActorNotAllowedError(
                f"Actor {event.actor.value} may not fire "
                f"{snapshot.current_state.value}+{event.trigger.value}; allowed: {allowed}"
            )

        rework_count = snapshot.rework_count
        if rule.increments_rework:
            if snapshot.rework_count >= ctx.max_rework_cycles:
                raise ReworkLimitError(
                    f"Rework budget exhausted ({snapshot.rework_count}/"
                    f"{ctx.max_rework_cycles}); fire REWORK_EXHAUSTED instead"
                )
            rework_count += 1

        self._sequence += 1
        now = ctx.clock.now()
        next_snapshot = WorkflowSnapshot(
            workflow_run_id=snapshot.workflow_run_id,
            claim_id=snapshot.claim_id,
            current_state=rule.destination,
            step_count=snapshot.step_count + 1,
            rework_count=rework_count,
            updated_at=now,
            schema_version=snapshot.schema_version,
        )
        record = TransitionRecord(
            transition_id=event.transition_id,
            sequence_no=self._sequence,
            claim_id=event.claim_id,
            workflow_run_id=event.workflow_run_id,
            execution_id=event.execution_id,
            source=snapshot.current_state,
            destination=rule.destination,
            trigger=event.trigger,
            actor=event.actor,
            reason=event.reason,
            idempotency_key=key,
            applied_at=now,
            correlation_id=event.correlation_id,
            schema_version=event.schema_version,
        )
        self._seen[key] = record
        return ApplyResult(
            snapshot=next_snapshot,
            record=record,
            duplicate=False,
            terminated=rule.terminates,
            steps_advanced=1,
        )

    @staticmethod
    def _check_identities(
        snapshot: WorkflowSnapshot, event: TransitionEvent, ctx: RunContext
    ) -> None:
        mismatches = []
        if event.workflow_run_id != snapshot.workflow_run_id:
            mismatches.append("workflow_run_id")
        if event.claim_id != snapshot.claim_id:
            mismatches.append("claim_id")
        if ctx.workflow_run_id != snapshot.workflow_run_id:
            mismatches.append("ctx.workflow_run_id")
        if ctx.claim_id != snapshot.claim_id:
            mismatches.append("ctx.claim_id")
        if mismatches:
            raise InvalidTransitionError(
                f"Identity mismatch on {', '.join(mismatches)}: event/context "
                "must reference the snapshot workflow"
            )

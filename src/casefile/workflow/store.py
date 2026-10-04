"""
CASEFILE workflow persistence boundary (Phase 3 §16, Phase 6 §19).

The engine stays persistence-agnostic: it works on snapshots and records.
This module defines the storage interface plus a SQLite implementation
over the Phase 6 unit of work. Snapshot + transition appends commit
atomically. Full checkpoint/replay belongs to Phase 7.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from sqlalchemy import Engine

from casefile.models.contracts import WorkflowState
from casefile.models.domain import (
    ActorType,
    AgentType,
    AuditEvent,
    AuditEventType,
    AuditResult,
    WorkflowRun,
)
from casefile.models.persistence import get_session_factory
from casefile.storage.unit_of_work import UnitOfWork
from casefile.workflow.context import WorkflowSnapshot
from casefile.workflow.transitions import TransitionRecord
from casefile.workflow.triggers import Trigger


class WorkflowStore(Protocol):
    """Storage interface for workflow snapshots and transition history."""

    def save_snapshot(self, snapshot: WorkflowSnapshot) -> None:
        """Upsert the current snapshot for a workflow run."""
        ...

    def load_snapshot(self, workflow_run_id: UUID) -> WorkflowSnapshot | None:
        """Load the current snapshot, or None when unknown."""
        ...

    def record_transition(self, record: TransitionRecord) -> None:
        """Append an applied transition to durable history."""
        ...

    def list_transitions(self, workflow_run_id: UUID) -> list[TransitionRecord]:
        """History for a run in application order."""
        ...


class SqliteWorkflowStore:
    """SQLite-backed WorkflowStore over the Phase 6 unit of work."""

    def __init__(self, engine: Engine) -> None:
        self._sessions = get_session_factory(engine)

    def _unit(self) -> UnitOfWork:
        return UnitOfWork(self._sessions)

    def save_snapshot(self, snapshot: WorkflowSnapshot) -> None:
        """Upsert the current snapshot for a workflow run."""
        with self._unit() as uow:
            uow.runs.save(_snapshot_to_run(snapshot))

    def load_snapshot(self, workflow_run_id: UUID) -> WorkflowSnapshot | None:
        """Load the current snapshot, or None when unknown."""
        from casefile.storage.errors import PersistenceError

        with self._unit() as uow:
            try:
                run = uow.runs.get(workflow_run_id)
            except PersistenceError:
                return None
            return WorkflowSnapshot(
                workflow_run_id=run.workflow_run_id,
                claim_id=run.claim_id,
                current_state=run.current_state,
                step_count=run.step_count,
                rework_count=run.rework_count,
                updated_at=run.updated_at,
            )

    def record_transition(self, record: TransitionRecord) -> None:
        """Append an applied transition as an audit event."""
        with self._unit() as uow:
            uow.audit.append(_record_to_event(record))

    def save_snapshot_and_audit(self, snapshot: WorkflowSnapshot, record: TransitionRecord) -> None:
        """Persist snapshot + transition atomically in one transaction."""
        with self._unit() as uow:
            uow.runs.save(_snapshot_to_run(snapshot))
            uow.audit.append(_record_to_event(record))

    def list_transitions(self, workflow_run_id: UUID) -> list[TransitionRecord]:
        """History for a run in application order."""
        with self._unit() as uow:
            events = uow.audit.list_for_run(workflow_run_id)
            records = []
            for event in events:
                if event.event_type != AuditEventType.STATE_TRANSITION:
                    continue
                records.append(
                    TransitionRecord(
                        transition_id=event.event_id,
                        sequence_no=int(event.attributes.get("sequence", "0")),
                        claim_id=event.claim_id,
                        workflow_run_id=event.workflow_run_id,
                        execution_id=UUID(
                            event.attributes.get("execution_id", str(event.correlation_id))
                        ),
                        source=WorkflowState(event.from_state or ""),
                        destination=WorkflowState(event.to_state or ""),
                        trigger=Trigger(event.attributes.get("trigger", "")),
                        actor=_actor_from_event(event),
                        reason=event.summary,
                        idempotency_key=event.attributes.get("idempotency_key", ""),
                        applied_at=event.timestamp,
                        correlation_id=event.correlation_id,
                    )
                )
            return records


def _actor_from_event(event: AuditEvent) -> AgentType:
    try:
        return AgentType(event.actor_id)
    except ValueError:
        return AgentType.SYSTEM


def _snapshot_to_run(snapshot: WorkflowSnapshot) -> WorkflowRun:
    terminal = snapshot.current_state if snapshot.current_state.is_terminal else None
    return WorkflowRun(
        workflow_run_id=snapshot.workflow_run_id,
        claim_id=snapshot.claim_id,
        current_state=snapshot.current_state,
        step_count=snapshot.step_count,
        rework_count=snapshot.rework_count,
        started_at=snapshot.updated_at,
        updated_at=snapshot.updated_at,
        completed_at=snapshot.updated_at if terminal else None,
        terminal_state=terminal,
        termination_reason=snapshot.current_state.value if terminal else None,
    )


def _record_to_event(record: TransitionRecord) -> AuditEvent:
    actor_type = {
        AgentType.SUPERVISOR: ActorType.AGENT,
        AgentType.EXTRACTOR: ActorType.AGENT,
        AgentType.INVESTIGATOR: ActorType.AGENT,
        AgentType.REVIEWER: ActorType.AGENT,
        AgentType.HUMAN: ActorType.HUMAN,
        AgentType.SYSTEM: ActorType.SYSTEM,
    }[record.actor]
    return AuditEvent(
        event_id=record.transition_id,
        workflow_run_id=record.workflow_run_id,
        claim_id=record.claim_id,
        trace_id=record.workflow_run_id.hex,
        event_type=AuditEventType.STATE_TRANSITION,
        timestamp=record.applied_at,
        actor_type=actor_type,
        actor_id=record.actor.value,
        action=f"{record.source.value}->{record.destination.value}",
        summary=record.reason or record.trigger.value,
        attributes={
            "trigger": record.trigger.value,
            "sequence": str(record.sequence_no),
            "idempotency_key": record.idempotency_key,
            "execution_id": str(record.execution_id),
        },
        result=AuditResult.SUCCESS,
        from_state=record.source,
        to_state=record.destination,
        correlation_id=record.correlation_id,
    )


snapshot_to_run = _snapshot_to_run
record_to_event = _record_to_event

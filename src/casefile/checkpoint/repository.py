"""
Checkpoint repository: append-only durable storage (Phase 7 §3, §14, §20).

- create: verifies checksum, assigns the next per-run sequence atomically,
  rejects duplicates/gaps/cross-run contamination (UNIQUE + explicit check)
- get / get_latest / list_for_run: ordered reads
- verify: integrity + version compatibility
- prune: keep the latest N per run (basic retention; archival is future work)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol
from uuid import UUID

from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from casefile.checkpoint.model import (
    Checkpoint,
    CheckpointNotFoundError,
    CheckpointSequenceError,
)
from casefile.models.persistence import CheckpointRow, get_session_factory
from casefile.storage.mappings import checkpoint_to_row, row_to_checkpoint

if TYPE_CHECKING:
    from casefile.observability.context import TraceContext
    from casefile.observability.provider import Observability


class CheckpointRepository(Protocol):
    """Append-only checkpoint storage boundary."""

    def create(self, checkpoint: Checkpoint) -> Checkpoint: ...
    def get(self, checkpoint_id: UUID) -> Checkpoint: ...
    def get_latest(self, workflow_run_id: UUID) -> Checkpoint | None: ...
    def list_for_run(self, workflow_run_id: UUID) -> list[Checkpoint]: ...
    def list_by_workflow_run_id(self, workflow_run_id: UUID) -> list[Checkpoint]: ...
    def verify(self, checkpoint: Checkpoint) -> bool: ...
    def verify_integrity(self, checkpoint: Checkpoint) -> bool: ...
    def prune(self, workflow_run_id: UUID, keep_last_n: int) -> int: ...


class SqlCheckpointRepository:
    """SQLite-backed CheckpointRepository."""

    def __init__(self, engine: Engine) -> None:
        self._sessions = get_session_factory(engine)

    def create(self, checkpoint: Checkpoint, obs: Observability | None = None) -> Checkpoint:
        """Seal (if needed), assign next sequence, and persist atomically."""
        from casefile.observability.spans import SPAN_CHECKPOINT_CREATE
        from casefile.observability.tracer import maybe_span

        trace = _trace_for(checkpoint)
        tracer = obs.tracer if obs is not None else None
        with maybe_span(
            tracer, SPAN_CHECKPOINT_CREATE, {"casefile.kind": checkpoint.kind.value}, trace
        ) as span:
            stored = self._create_inner(checkpoint)
            span.set_attribute("casefile.sequence_no", stored.sequence_no)
            span.set_attribute("casefile.success", True)
            if obs is not None:
                obs.meters.record_counter("casefile.workflow.runs", 1.0, {"outcome": "checkpoint"})
            return stored

    def _create_inner(self, checkpoint: Checkpoint) -> Checkpoint:
        """Uninstrumented creation body (logic unchanged)."""
        with self._sessions() as session:
            stored = self._prepare(session, checkpoint)
            try:
                session.add(checkpoint_to_row(stored))
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise CheckpointSequenceError(
                    f"Duplicate checkpoint sequence for run {checkpoint.workflow_run_id}: {exc}"
                ) from exc
            return stored

    @staticmethod
    def create_in(
        session: Session, checkpoint: Checkpoint, obs: Observability | None = None
    ) -> Checkpoint:
        """Prepare + stage a checkpoint inside the caller's transaction.

        The caller owns commit/rollback, so approval rows and checkpoint
        rows commit atomically together. Flushes but never commits.
        """
        from casefile.observability.spans import SPAN_CHECKPOINT_CREATE
        from casefile.observability.tracer import maybe_span

        tracer = obs.tracer if obs is not None else None
        with maybe_span(
            tracer, SPAN_CHECKPOINT_CREATE, {"casefile.kind": checkpoint.kind.value}
        ) as span:
            stored = SqlCheckpointRepository._prepare(session, checkpoint)
            try:
                session.add(checkpoint_to_row(stored))
                session.flush()
            except IntegrityError as exc:
                span.set_status_error(type(exc).__name__)
                raise CheckpointSequenceError(
                    f"Duplicate checkpoint sequence for run {checkpoint.workflow_run_id}: {exc}"
                ) from exc
            span.set_attribute("casefile.sequence_no", stored.sequence_no)
            span.set_attribute("casefile.success", True)
            return stored

    @staticmethod
    def _prepare(session: Session, checkpoint: Checkpoint) -> Checkpoint:
        sealed = checkpoint if checkpoint.verify_integrity() else checkpoint.sealed()
        last = SqlCheckpointRepository._max_sequence(session, sealed.workflow_run_id)
        expected = (last or 0) + 1
        if sealed.sequence_no not in (0, expected):
            raise CheckpointSequenceError(
                f"Checkpoint for run {sealed.workflow_run_id} must carry "
                f"sequence {expected}, got {sealed.sequence_no}"
            )
        stored = sealed.model_copy(update={"sequence_no": expected})
        if not stored.verify_integrity():
            stored = stored.sealed()
        return stored

    def get(self, checkpoint_id: UUID, obs: Observability | None = None) -> Checkpoint:
        """Load one checkpoint by id or raise not-found."""
        from casefile.observability.spans import SPAN_CHECKPOINT_LOAD
        from casefile.observability.tracer import maybe_span

        tracer = obs.tracer if obs is not None else None
        with maybe_span(tracer, SPAN_CHECKPOINT_LOAD, {}, None) as span:
            try:
                with self._sessions() as session:
                    row = session.get(CheckpointRow, str(checkpoint_id))
                    if row is None:
                        raise CheckpointNotFoundError(f"Checkpoint {checkpoint_id} not found")
                    loaded = row_to_checkpoint(row)
                span.set_attribute("casefile.success", True)
                return loaded
            except Exception as exc:
                span.set_status_error(type(exc).__name__)
                raise

    def get_latest(self, workflow_run_id: UUID) -> Checkpoint | None:
        """Newest checkpoint for a run, or None when the run has none."""
        with self._sessions() as session:
            row = (
                session.query(CheckpointRow)
                .filter_by(workflow_run_id=str(workflow_run_id))
                .order_by(CheckpointRow.sequence_no.desc())
                .first()
            )
            if row is None:
                return None
            return row_to_checkpoint(row)

    def list_for_run(self, workflow_run_id: UUID) -> list[Checkpoint]:
        """All checkpoints for a run in sequence order."""
        with self._sessions() as session:
            rows = (
                session.query(CheckpointRow)
                .filter_by(workflow_run_id=str(workflow_run_id))
                .order_by(CheckpointRow.sequence_no)
                .all()
            )
            return [row_to_checkpoint(row) for row in rows]

    def list_by_workflow_run_id(self, workflow_run_id: UUID) -> list[Checkpoint]:
        """Alias for list_for_run."""
        return self.list_for_run(workflow_run_id)

    def verify_integrity(self, checkpoint: Checkpoint) -> bool:
        """Alias for verify."""
        return self.verify(checkpoint)

    def verify(self, checkpoint: Checkpoint) -> bool:
        """Integrity plus version compatibility (no exceptions on failure)."""
        try:
            if not checkpoint.verify_integrity():
                return False
            checkpoint.check_compatible()
            return True
        except Exception:
            return False

    def prune(self, workflow_run_id: UUID, keep_last_n: int) -> int:
        """Delete older checkpoints, keeping the latest N. Returns the count."""
        if keep_last_n < 1:
            raise ValueError("keep_last_n must be >= 1")
        with self._sessions() as session:
            sequences = (
                session.query(CheckpointRow.sequence_no)
                .filter_by(workflow_run_id=str(workflow_run_id))
                .order_by(CheckpointRow.sequence_no.desc())
                .all()
            )
            drop = [seq for (seq,) in sequences][keep_last_n:]
            if not drop:
                return 0
            deleted = (
                session.query(CheckpointRow)
                .filter_by(workflow_run_id=str(workflow_run_id))
                .filter(CheckpointRow.sequence_no.in_(drop))
                .delete(synchronize_session=False)
            )
            session.commit()
            return int(deleted)

    @staticmethod
    def _max_sequence(session: Session, workflow_run_id: UUID) -> int | None:
        value = session.execute(
            select(func.max(CheckpointRow.sequence_no)).where(
                CheckpointRow.workflow_run_id == str(workflow_run_id)
            )
        ).scalar()
        return int(value) if value is not None else None


class SqlCheckpointSessionRepository:
    """Session-bound checkpoint repository for atomic transactions within a UnitOfWork."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def create(self, checkpoint: Checkpoint) -> Checkpoint:
        stored = SqlCheckpointRepository._prepare(self._session, checkpoint)
        try:
            self._session.add(checkpoint_to_row(stored))
            self._session.flush()
        except IntegrityError as exc:
            raise CheckpointSequenceError(
                f"Duplicate checkpoint sequence for run {checkpoint.workflow_run_id}: {exc}"
            ) from exc
        return stored

    def get(self, checkpoint_id: UUID) -> Checkpoint:
        row = self._session.get(CheckpointRow, str(checkpoint_id))
        if row is None:
            raise CheckpointNotFoundError(f"Checkpoint {checkpoint_id} not found")
        return row_to_checkpoint(row)

    def get_latest(self, workflow_run_id: UUID) -> Checkpoint | None:
        row = (
            self._session.query(CheckpointRow)
            .filter_by(workflow_run_id=str(workflow_run_id))
            .order_by(CheckpointRow.sequence_no.desc())
            .first()
        )
        if row is None:
            return None
        return row_to_checkpoint(row)

    def list_for_run(self, workflow_run_id: UUID) -> list[Checkpoint]:
        rows = (
            self._session.query(CheckpointRow)
            .filter_by(workflow_run_id=str(workflow_run_id))
            .order_by(CheckpointRow.sequence_no)
            .all()
        )
        return [row_to_checkpoint(row) for row in rows]

    def list_by_workflow_run_id(self, workflow_run_id: UUID) -> list[Checkpoint]:
        return self.list_for_run(workflow_run_id)

    def verify(self, checkpoint: Checkpoint) -> bool:
        try:
            if not checkpoint.verify_integrity():
                return False
            checkpoint.check_compatible()
            return True
        except Exception:
            return False

    def verify_integrity(self, checkpoint: Checkpoint) -> bool:
        return self.verify(checkpoint)

    def prune(self, workflow_run_id: UUID, keep_last_n: int) -> int:
        if keep_last_n < 1:
            raise ValueError("keep_last_n must be >= 1")
        sequences = (
            self._session.query(CheckpointRow.sequence_no)
            .filter_by(workflow_run_id=str(workflow_run_id))
            .order_by(CheckpointRow.sequence_no.desc())
            .all()
        )
        drop = [seq for (seq,) in sequences][keep_last_n:]
        if not drop:
            return 0
        deleted = (
            self._session.query(CheckpointRow)
            .filter_by(workflow_run_id=str(workflow_run_id))
            .filter(CheckpointRow.sequence_no.in_(drop))
            .delete(synchronize_session=False)
        )
        return int(deleted)


def _trace_for(checkpoint: Checkpoint) -> TraceContext:
    """Trace context enriched from checkpoint identities."""
    from casefile.observability.context import TraceContext

    return TraceContext(
        workflow_run_id=checkpoint.workflow_run_id,
        execution_id=checkpoint.execution_id,
        claim_id=checkpoint.claim_id,
        checkpoint_id=checkpoint.checkpoint_id,
        correlation_id=checkpoint.correlation_id,
    )

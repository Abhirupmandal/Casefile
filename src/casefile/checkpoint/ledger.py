"""
Durable idempotency boundary across restarts (Phase 7 §15, §22).

Claim-before-apply: the first worker to insert an idempotency key owns
the transition; losers get IntegrityError and must treat their attempt
as a duplicate. No duplicate state transitions, no duplicate side
effects. Local SQLite transactions only — no distributed consensus.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError

from casefile.models.persistence import IdempotencyRecord, get_session_factory


class DuplicateClaimError(Exception):
    """The idempotency key was already claimed by another attempt."""


class IdempotencyLedger:
    """Atomic claim/check ledger over the idempotency_ledger table."""

    def __init__(self, engine: Engine) -> None:
        self._sessions = get_session_factory(engine)

    def claim(
        self,
        *,
        idempotency_key: str,
        workflow_run_id: UUID,
        execution_id: UUID,
        sequence_no: int,
        result_kind: str,
        result_ref: str,
    ) -> bool:
        """Atomically claim a key. True when first; False when duplicate.

        Never raises for races: IntegrityError means another worker won.
        """
        try:
            with self._sessions() as session:
                session.add(
                    IdempotencyRecord(
                        idempotency_key=idempotency_key,
                        workflow_run_id=str(workflow_run_id),
                        execution_id=str(execution_id),
                        sequence_no=sequence_no,
                        result_kind=result_kind,
                        result_ref=result_ref,
                        created_at=datetime.now(UTC),
                    )
                )
                session.commit()
            return True
        except IntegrityError:
            return False

    def is_claimed(self, idempotency_key: str) -> bool:
        """True when the key already has an owner."""
        with self._sessions() as session:
            return session.get(IdempotencyRecord, idempotency_key) is not None

    def lookup(self, idempotency_key: str) -> dict[str, str] | None:
        """Owner metadata for a claimed key, or None when free."""
        with self._sessions() as session:
            row = session.get(IdempotencyRecord, idempotency_key)
            if row is None:
                return None
            return {
                "workflow_run_id": row.workflow_run_id,
                "execution_id": row.execution_id,
                "result_kind": row.result_kind,
                "result_ref": row.result_ref,
            }

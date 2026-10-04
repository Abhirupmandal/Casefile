"""
Unit of work / transaction boundary (Phase 6 §4).

One Session per unit. All repository writes flush into it; exiting the
context commits everything atomically or rolls everything back. This is
how `workflow state update + audit event` commit consistently: both go
through one UnitOfWork. SQLite transactions only — no distributed
transactions. SQLAlchemy errors surface as typed PersistenceErrors.
"""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session, sessionmaker

from casefile.checkpoint.repository import SqlCheckpointSessionRepository
from casefile.storage.errors import (
    PersistenceError,
    PersistenceErrorCode,
    map_transaction_failure,
    map_unavailable,
)
from casefile.storage.repositories import (
    SqlAgentExecutionRepository,
    SqlApprovalRepository,
    SqlAuditRepository,
    SqlBudgetCounterRepository,
    SqlBudgetRepository,
    SqlCheckpointBudgetRepository,
    SqlClaimRepository,
    SqlDamageEstimateRepository,
    SqlDocumentRepository,
    SqlEvidenceRepository,
    SqlFraudRepository,
    SqlPolicyRepository,
    SqlPriorClaimRepository,
    SqlRunBudgetRepository,
    SqlToolInvocationRepository,
    SqlWorkflowRunRepository,
)


class UnitOfWork:
    """Atomic work scope exposing all repositories over one Session."""

    _REPOSITORIES = (
        "claims",
        "documents",
        "policies",
        "priors",
        "estimates",
        "evidence",
        "fraud",
        "runs",
        "executions",
        "agent_executions",
        "audit",
        "events",
        "workflow_events",
        "tool_invocations",
        "approvals",
        "budgets",
        "run_budgets",
        "counters",
        "checkpoint_budgets",
        "checkpoints",
    )

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._factory = session_factory
        self._session: Session | None = None
        self._committed = False

    def __getattr__(self, name: str) -> object:
        """Repositories exist only inside an open scope."""
        if name in UnitOfWork._REPOSITORIES:
            raise PersistenceError(
                PersistenceErrorCode.TRANSACTION_FAILED, "UnitOfWork is not open"
            )
        raise AttributeError(f"{type(self).__name__} has no attribute {name!r}")

    @property
    def session(self) -> Session:
        """The underlying session (storage-internal use only)."""
        if self._session is None:
            raise PersistenceError(
                PersistenceErrorCode.TRANSACTION_FAILED, "UnitOfWork is not open"
            )
        return self._session

    def __enter__(self) -> UnitOfWork:
        self._session = self._factory()
        self._bind_repositories()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if exc_type is None:
                try:
                    self.session.commit()
                    self._committed = True
                except IntegrityError as commit_exc:
                    self.session.rollback()
                    raise PersistenceError(
                        PersistenceErrorCode.INTEGRITY_VIOLATION,
                        f"Commit failed integrity: {commit_exc}",
                    ) from commit_exc
                except OperationalError as commit_exc:
                    self.session.rollback()
                    raise map_unavailable(f"Commit failed: {commit_exc}") from commit_exc
            else:
                self.session.rollback()
        finally:
            if self._session is not None:
                self._session.close()
                self._session = None
            for attr in UnitOfWork._REPOSITORIES:
                self.__dict__.pop(attr, None)

    @property
    def committed(self) -> bool:
        """True after a successful commit on exit."""
        return self._committed

    def commit(self) -> None:
        """Commit mid-scope (still inside the context)."""
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise PersistenceError(
                PersistenceErrorCode.INTEGRITY_VIOLATION, f"Commit failed integrity: {exc}"
            ) from exc
        except OperationalError as exc:
            self.session.rollback()
            raise map_unavailable(f"Commit failed: {exc}") from exc
        except Exception as exc:
            self.session.rollback()
            raise map_transaction_failure(f"Commit failed: {exc}") from exc

    def rollback(self) -> None:
        """Discard all pending changes in this scope."""
        self.session.rollback()

    def _bind_repositories(self) -> None:
        session = self.session
        self.claims = SqlClaimRepository(session)
        self.documents = SqlDocumentRepository(session)
        self.policies = SqlPolicyRepository(session)
        self.priors = SqlPriorClaimRepository(session)
        self.estimates = SqlDamageEstimateRepository(session)
        self.evidence = SqlEvidenceRepository(session)
        self.fraud = SqlFraudRepository(session)
        self.runs = SqlWorkflowRunRepository(session)
        self.executions = SqlAgentExecutionRepository(session)
        self.agent_executions = self.executions
        self.audit = SqlAuditRepository(session)
        self.events = self.audit
        self.workflow_events = self.audit
        self.tool_invocations = SqlToolInvocationRepository(session)
        self.approvals = SqlApprovalRepository(session)
        self.budgets = SqlBudgetRepository(session)
        self.run_budgets = SqlRunBudgetRepository(session)
        self.counters = SqlBudgetCounterRepository(session)
        self.checkpoint_budgets = SqlCheckpointBudgetRepository(session)
        self.checkpoints = SqlCheckpointSessionRepository(session)

    claims: SqlClaimRepository
    documents: SqlDocumentRepository
    policies: SqlPolicyRepository
    priors: SqlPriorClaimRepository
    estimates: SqlDamageEstimateRepository
    evidence: SqlEvidenceRepository
    fraud: SqlFraudRepository
    runs: SqlWorkflowRunRepository
    executions: SqlAgentExecutionRepository
    agent_executions: SqlAgentExecutionRepository
    audit: SqlAuditRepository
    events: SqlAuditRepository
    workflow_events: SqlAuditRepository
    tool_invocations: SqlToolInvocationRepository
    approvals: SqlApprovalRepository
    budgets: SqlBudgetRepository
    run_budgets: SqlRunBudgetRepository
    counters: SqlBudgetCounterRepository
    checkpoint_budgets: SqlCheckpointBudgetRepository
    checkpoints: SqlCheckpointSessionRepository

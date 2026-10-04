"""
Integration Tests: SQLite-backed persistence and tool execution (Phase 6).

Seed dataset, repository-backed tool runs, workflow restart across store
instances, migration upgrades, index verification, scope isolation, and
agent/storage trust-boundary scans. Deterministic; no network; serverless
local database only.
"""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session, sessionmaker

from casefile.models.contracts import WorkflowState
from casefile.models.domain import AgentType
from casefile.models.persistence import get_engine, get_session_factory, init_db
from casefile.storage.seed import seed_synthetic_dataset
from casefile.storage.unit_of_work import UnitOfWork
from casefile.tools.contracts import ToolContext
from casefile.tools.registry import ToolRegistry
from casefile.tools.sqlite_source import RepositoryDataSource
from casefile.workflow.context import FixedClock, RunContext, WorkflowSnapshot
from casefile.workflow.engine import WorkflowEngine
from casefile.workflow.store import SqliteWorkflowStore
from casefile.workflow.transitions import TransitionEvent
from casefile.workflow.triggers import Trigger


def _seeded(tmp_path: Path, name: str = "phase6.db") -> sessionmaker[Session]:
    engine = get_engine(f"sqlite:///{tmp_path / name}")
    init_db(engine)
    factory = get_session_factory(engine)
    with UnitOfWork(factory) as uow:
        seed_synthetic_dataset(uow)
    return factory


def _tool_ctx(agent: AgentType = AgentType.INVESTIGATOR) -> ToolContext:
    return ToolContext(claim_id=uuid4(), workflow_run_id=uuid4(), agent=agent)


@pytest.mark.integration
class TestSeedDataset:
    def test_all_scenarios_seeded(self, tmp_path: Path) -> None:
        factory = _seeded(tmp_path)
        source = RepositoryDataSource(factory)
        assert source.get_policy("POL-SYN-001") is not None
        assert len(source.priors_for_customer("CUST-SYN-HIST")) == 3
        assert source.get_estimate("SYN-EST-NORMAL") is not None
        assert len(source.documents_for_claim("SYN-NORMAL-001")) == 2
        assert source.fraud_profile("SYN-FRAUD-001") is not None
        assert source.scenario("SYN-REWORK-001") is not None
        assert source.get_policy("POL-NOPE") is None

    def test_seed_is_idempotent(self, tmp_path: Path) -> None:
        from casefile.storage.seed import seed_synthetic_dataset as seed

        factory = _seeded(tmp_path)
        with UnitOfWork(factory) as uow:
            counts = seed(uow)
        assert counts["policies"] == 4
        assert counts["claims"] == 7


@pytest.mark.integration
class TestSqliteBackedTools:
    def _registry(self, tmp_path: Path) -> ToolRegistry:
        from casefile.tools import (
            DamageEstimateTool,
            DocumentRetrievalTool,
            EvidenceLookupTool,
            FraudSignalLookupTool,
            PolicyLookupTool,
            PriorClaimLookupTool,
        )
        from casefile.tools.registry import ToolRegistry

        factory = _seeded(tmp_path)
        source = RepositoryDataSource(factory)
        registry = ToolRegistry()
        registry.register(DocumentRetrievalTool(store=source))
        registry.register(EvidenceLookupTool(store=source))
        registry.register(PriorClaimLookupTool(store=source))
        registry.register(PolicyLookupTool(store=source))
        registry.register(DamageEstimateTool(store=source))
        registry.register(FraudSignalLookupTool(store=source))
        return registry

    def test_policy_history_damage_from_sqlite(self, tmp_path: Path) -> None:
        registry = self._registry(tmp_path)
        ctx = _tool_ctx()
        policy = registry.execute("policy_lookup", "1.0.0", {"policy_number": "POL-SYN-001"}, ctx)
        assert policy.succeeded is True
        history = registry.execute(
            "claim_history_lookup", "1.0.0", {"customer_id": "CUST-SYN-HIST"}, ctx
        )
        assert history.succeeded is True
        damage = registry.execute(
            "repair_cost_lookup",
            "1.0.0",
            {"estimate_ref": "SYN-EST-NORMAL", "claim_ref": "SYN-NORMAL-001"},
            ctx,
        )
        assert damage.succeeded is True
        assert damage.output is not None

    def test_document_and_evidence_from_sqlite(self, tmp_path: Path) -> None:
        from casefile.tools.sqlite_source import RepositoryDataSource as Source

        factory = _seeded(tmp_path, "docs.db")
        source = Source(factory)
        docs = source.documents_for_claim("SYN-NORMAL-001")
        assert len(docs) == 2
        registry = self._registry(tmp_path)
        ctx = _tool_ctx(AgentType.EXTRACTOR)
        first_ref = docs[0].document_ref
        outcome = registry.execute(
            "document_retrieval",
            "1.0.0",
            {"document_ref": first_ref, "claim_ref": "SYN-NORMAL-001"},
            ctx,
        )
        assert outcome.succeeded is True
        evidence = registry.execute(
            "evidence_lookup", "1.0.0", {"claim_ref": "SYN-NORMAL-001"}, _tool_ctx()
        )
        assert evidence.succeeded is True

    def test_broken_estimate_flagged_from_sqlite(self, tmp_path: Path) -> None:
        registry = self._registry(tmp_path)
        outcome = registry.execute(
            "repair_cost_lookup",
            "1.0.0",
            {"estimate_ref": "SYN-EST-BROKEN", "claim_ref": "SYN-ESTIMATE-001"},
            _tool_ctx(),
        )
        assert outcome.succeeded is True
        assert outcome.output is not None
        assert outcome.output.consistent is False  # type: ignore[attr-defined]


@pytest.mark.integration
class TestWorkflowRestart:
    def test_restart_preserves_and_continues(self, tmp_path: Path) -> None:
        db_path = tmp_path / "restart.db"
        engine = get_engine(f"sqlite:///{db_path}")
        init_db(engine)
        run_id, claim_id = uuid4(), uuid4()
        clock = FixedClock(datetime.now(UTC))

        # Process 1: create run, drive two transitions, persist
        store_one = SqliteWorkflowStore(engine)
        flow_one = WorkflowEngine()
        ctx_one = RunContext(claim_id=claim_id, workflow_run_id=run_id, clock=clock)
        snap = WorkflowSnapshot(
            workflow_run_id=run_id, claim_id=claim_id, current_state=WorkflowState.RECEIVED
        )
        for trigger in (Trigger.CLAIM_VALIDATED, Trigger.EXTRACTION_SUCCEEDED):
            event = TransitionEvent(claim_id=claim_id, workflow_run_id=run_id, trigger=trigger)
            result = flow_one.apply(snap, event, ctx_one)
            assert result.record is not None
            store_one.save_snapshot_and_audit(result.snapshot, result.record)
            snap = result.snapshot
        assert snap.current_state == WorkflowState.INVESTIGATION
        engine.dispose()

        # Process 2: fresh engine + store on the same file, reload, continue
        engine_two = get_engine(f"sqlite:///{db_path}")
        store_two = SqliteWorkflowStore(engine_two)
        reloaded = store_two.load_snapshot(run_id)
        assert reloaded is not None
        assert reloaded.current_state == WorkflowState.INVESTIGATION
        assert reloaded.step_count == 2
        assert len(store_two.list_transitions(run_id)) == 2
        flow_two = WorkflowEngine()
        ctx_two = RunContext(claim_id=claim_id, workflow_run_id=run_id, clock=clock)
        continued = flow_two.apply(
            reloaded,
            TransitionEvent(
                claim_id=claim_id, workflow_run_id=run_id, trigger=Trigger.INVESTIGATION_SUCCEEDED
            ),
            ctx_two,
        )
        assert continued.snapshot.current_state == WorkflowState.REVIEW
        assert continued.record is not None
        store_two.save_snapshot_and_audit(continued.snapshot, continued.record)
        assert store_two.load_snapshot(run_id) is not None
        engine_two.dispose()

    def test_terminal_survives_reload(self, tmp_path: Path) -> None:
        engine = get_engine(f"sqlite:///{tmp_path / 'terminal.db'}")
        init_db(engine)
        store = SqliteWorkflowStore(engine)
        run_id, claim_id = uuid4(), uuid4()
        flow = WorkflowEngine()
        ctx = RunContext(
            claim_id=claim_id,
            workflow_run_id=run_id,
            clock=FixedClock(datetime.now(UTC)),
        )
        snap = WorkflowSnapshot(
            workflow_run_id=run_id, claim_id=claim_id, current_state=WorkflowState.REVIEW
        )
        result = flow.apply(
            snap,
            TransitionEvent(
                claim_id=claim_id, workflow_run_id=run_id, trigger=Trigger.REVIEW_REJECTED
            ),
            ctx,
        )
        assert result.record is not None
        store.save_snapshot_and_audit(result.snapshot, result.record)
        engine.dispose()
        engine_two = get_engine(f"sqlite:///{tmp_path / 'terminal.db'}")
        store_two = SqliteWorkflowStore(engine_two)
        reloaded = store_two.load_snapshot(run_id)
        assert reloaded is not None
        assert reloaded.current_state == WorkflowState.REJECTED
        assert reloaded.current_state.is_terminal is True
        engine_two.dispose()


@pytest.mark.integration
class TestMigrationsAndIndexes:
    def test_upgrade_head_fresh_db(self, tmp_path: Path) -> None:
        import os
        from unittest import mock

        from alembic import command
        from alembic.config import Config

        url = f"sqlite:///{tmp_path / 'mig.db'}"
        repo_root = Path(__file__).resolve().parents[2]
        cfg = Config(str(repo_root / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", url)
        with mock.patch.dict(os.environ, {"CASEFILE_DATABASE_URL": url}):
            command.upgrade(cfg, "head")
        engine = get_engine(url)
        tables = set(inspect(engine).get_table_names())
        for expected in (
            "claims",
            "workflow_runs",
            "audit_events",
            "human_approvals",
            "budget_snapshots",
            "policies",
            "prior_claims",
            "damage_estimates",
            "damage_line_items",
            "claim_documents",
            "evidence_items",
            "agent_executions",
            "fraud_indicators",
            "alembic_version",
        ):
            assert expected in tables, expected
        engine.dispose()

    def test_lookup_indexes_exist(self, tmp_path: Path) -> None:
        engine = get_engine(f"sqlite:///{tmp_path / 'idx.db'}")
        init_db(engine)
        inspector = inspect(engine)
        index_columns = {
            table: {col for index in inspector.get_indexes(table) for col in index["column_names"]}
            for table in (
                "workflow_runs",
                "audit_events",
                "prior_claims",
                "damage_estimates",
                "claim_documents",
                "evidence_items",
                "agent_executions",
                "fraud_indicators",
            )
        }
        assert "claim_id" in index_columns["workflow_runs"]
        assert "workflow_run_id" in index_columns["audit_events"]
        assert "customer_id" in index_columns["prior_claims"]
        assert "claim_ref" in index_columns["damage_estimates"]
        assert "claim_ref" in index_columns["claim_documents"]
        assert "claim_ref" in index_columns["evidence_items"]
        assert "workflow_run_id" in index_columns["agent_executions"]
        assert "claim_ref" in index_columns["fraud_indicators"]
        engine.dispose()


@pytest.mark.integration
class TestTrustBoundaries:
    def test_agents_never_import_storage(self) -> None:
        from pathlib import Path as FsPath

        package = FsPath(__file__).resolve().parents[2] / "src" / "casefile" / "agents"
        hits = [
            path.name
            for path in sorted(package.glob("*.py"))
            if "storage" in path.read_text(encoding="utf-8")
        ]
        assert hits == []

    def test_claim_scope_isolation(self, tmp_path: Path) -> None:
        factory = _seeded(tmp_path)
        source = RepositoryDataSource(factory)
        other_docs = source.documents_for_claim("SYN-FRAUD-001")
        refs = {doc.document_ref for doc in source.documents_for_claim("SYN-NORMAL-001")}
        assert all(doc.document_ref not in refs for doc in other_docs)
        assert source.priors_for_customer("CUST-SYN-001") == []

    def test_no_secrets_persisted(self, tmp_path: Path) -> None:
        import sqlite3

        db_path = tmp_path / "secrets.db"
        engine = get_engine(f"sqlite:///{db_path}")
        init_db(engine)
        factory = get_session_factory(engine)
        with UnitOfWork(factory) as uow:
            seed_synthetic_dataset(uow)
        conn = sqlite3.connect(db_path)
        try:
            dump = "\n".join(
                str(row)
                for table in (
                    "claims",
                    "policies",
                    "prior_claims",
                    "agent_executions",
                    "human_approvals",
                )
                for row in conn.execute(f"SELECT * FROM {table}")
            ).lower()
        finally:
            conn.close()
        for token in ("sk-", "api_key", "apikey", "password", "secret"):
            assert token not in dump, token

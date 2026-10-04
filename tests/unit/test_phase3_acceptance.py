"""
Phase 3 Acceptance Test Matrix (Items A through AZ) and End-to-End Scenarios 1–6.

Validates the complete Phase 3 scope:
- A-F: Tool contracts (typed input/output, versions, descriptions, schemas)
- G-J: Tool registry (explicit registration, duplicates rejected, closed failure)
- K-N: Tool authorization & security boundaries (matrix, role restrictions)
- O-R: Tool error taxonomy & failure modes (timeouts, validation, execution)
- S-V: Tool provenance metadata (stability, source tracking, audit trail)
- W-Z: SQLite durable persistence (tables, indices, JSON serialization)
- AA-AD: Repositories (typed domain mapping, no ORM leakage, error handling)
- AE-AH: Unit of Work (atomic commit, rollback, boundary isolation)
- AI-AL: Database migrations (Alembic upgrade head, schema parity)
- AM-AP: Agent to tool integration (Investigator, Extractor, Reviewer boundaries)
- AQ-AT: Architecture boundaries (no ORM in agents, layered separation)
- AU-AZ: End-to-End Scenarios 1 through 6
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import BaseModel
from sqlalchemy import inspect
from sqlalchemy.orm import Session, sessionmaker

from casefile.agents.base import AgentFailedError
from casefile.agents.context import AgentContext
from casefile.agents.deterministic import DeterministicProvider
from casefile.agents.extractor import ExtractorAgent
from casefile.agents.investigator import InvestigatorAgent
from casefile.agents.reviewer import ReviewerAgent
from casefile.agents.supervisor_agent import SupervisorAgent
from casefile.models.contracts import (
    ClaimInput,
    WorkflowState,
)
from casefile.models.domain import (
    AgentType,
    AuditEvent,
    AuditEventType,
    AuditResult,
    Claim,
    ClaimStatus,
    ClaimType,
    EvidenceItem,
    EvidenceSourceType,
    Money,
    WorkflowRun,
)
from casefile.models.persistence import (
    get_engine,
    get_session_factory,
    init_db,
)
from casefile.storage.errors import PersistenceError, PersistenceErrorCode
from casefile.storage.unit_of_work import UnitOfWork
from casefile.tools import (
    AUTHORIZED_TOOLS,
    DamageEstimateTool,
    DocumentRetrievalTool,
    EvidenceLookupTool,
    FraudSignalLookupTool,
    PolicyLookupInput,
    PolicyLookupOutput,
    PolicyLookupTool,
    PriorClaimLookupTool,
    ToolAuthorizationError,
    ToolCall,
    ToolContext,
    ToolContractError,
    ToolErrorCode,
    ToolExecutionError,
    ToolNotFoundError,
    ToolRegistry,
    ToolResultMetadata,
    ToolStatus,
    ToolTimeoutError,
    ToolUnavailableError,
    ToolValidationError,
    create_default_registry,
)
from casefile.tools.errors import ToolError as ToolException
from casefile.workflow.runner import WorkflowRunner

# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------


def make_test_db(tmp_path: Path, name: str = "phase3_test.db") -> sessionmaker[Session]:
    db_file = tmp_path / name
    engine = get_engine(f"sqlite:///{db_file}")
    init_db(engine)
    return get_session_factory(engine)


def make_test_claim_input(
    claim_id: UUID | None = None,
    policy_id: str = "POL-SYN-001",
) -> ClaimInput:
    return ClaimInput(
        claim_id=claim_id or uuid4(),
        policy_id=policy_id,
        claimant_name="Jane Doe",
        incident_date="2026-09-20",
        claim_amount=Decimal("4500.00"),
        description="Collision with concrete pillar in underground parking",
        documents=["DOC-001", "DOC-002"],
    )


def make_tool_context(
    agent: AgentType = AgentType.INVESTIGATOR,
    claim_id: UUID | None = None,
    run_id: UUID | None = None,
) -> ToolContext:
    return ToolContext(
        claim_id=claim_id or uuid4(),
        workflow_run_id=run_id or uuid4(),
        agent=agent,
        correlation_id=uuid4(),
        execution_id=uuid4(),
    )


# ===========================================================================
# Categories A-F: Tool Contracts
# ===========================================================================


class TestToolContracts:
    def test_item_a_generic_tool_interface(self) -> None:
        """Item A: Tools define typed generic input and output models (not dict)."""
        tool = PolicyLookupTool()
        assert issubclass(tool.input_model, BaseModel)
        assert issubclass(tool.output_model, BaseModel)
        assert tool.input_model is PolicyLookupInput
        assert tool.output_model is PolicyLookupOutput

    def test_item_b_stable_metadata_declarations(self) -> None:
        """Item B: Tools declare stable name, version, description, schema_version."""
        for tool_cls in (
            PolicyLookupTool,
            PriorClaimLookupTool,
            DamageEstimateTool,
            FraudSignalLookupTool,
            DocumentRetrievalTool,
            EvidenceLookupTool,
        ):
            instance = tool_cls()
            assert instance.name
            assert instance.version == "1.0.0"
            assert instance.description
            assert instance.schema_version == "1.0.0"
            assert instance.timeout_seconds > 0

    def test_item_c_input_validation(self) -> None:
        """Item C: Malformed input fails validation before execution."""
        registry = create_default_registry()
        ctx = make_tool_context()
        outcome = registry.execute("policy_lookup", "1.0.0", {"invalid_field": 123}, ctx)
        assert outcome.succeeded is False
        assert outcome.status == ToolStatus.FAILURE
        assert outcome.error is not None
        assert outcome.error.code == ToolErrorCode.INVALID_INPUT

    def test_item_d_output_contract_validation(self) -> None:
        """Item D: Valid tool execution produces validated output model."""
        registry = create_default_registry()
        ctx = make_tool_context()
        outcome = registry.execute("policy_lookup", "1.0.0", {"policy_number": "POL-SYN-001"}, ctx)
        assert outcome.succeeded is True
        assert isinstance(outcome.output, PolicyLookupOutput)
        assert outcome.output.found is True
        assert outcome.output.policy is not None
        assert outcome.output.policy.policy_number == "POL-SYN-001"

    def test_item_e_provenance_metadata_attached(self) -> None:
        """Item E: Every ToolOutcome carries structured ToolResultMetadata."""
        registry = create_default_registry()
        ctx = make_tool_context()
        outcome = registry.execute("policy_lookup", "1.0.0", {"policy_number": "POL-SYN-001"}, ctx)
        assert outcome.provenance is not None
        assert isinstance(outcome.provenance, ToolResultMetadata)
        assert outcome.provenance.tool_name == "policy_lookup"
        assert outcome.provenance.tool_version == "1.0.0"
        assert outcome.provenance.source in ("fixture", "repository")
        assert outcome.provenance.retrieved_at is not None
        assert outcome.provenance.correlation_id == ctx.correlation_id

    def test_item_f_execution_modes_and_costs(self) -> None:
        """Item F: Tools declare cost units, max results, and timeout."""
        tool = DamageEstimateTool()
        assert tool.cost_units >= 1
        assert tool.max_results >= 1
        assert tool.timeout_seconds >= 1.0


# ===========================================================================
# Categories G-J: Tool Registry
# ===========================================================================


class TestToolRegistry:
    def test_item_g_explicit_registration_and_lookup(self) -> None:
        """Item G: Explicit registration and lookup by name."""
        registry = ToolRegistry()
        tool = PolicyLookupTool()
        registry.register(tool)
        assert "policy_lookup" in registry.list_tools()
        meta = registry.metadata("policy_lookup")
        assert meta["name"] == "policy_lookup"
        assert meta["version"] == "1.0.0"

    def test_item_h_duplicate_registration_rejected(self) -> None:
        """Item H: Duplicate registration raises ValueError."""
        registry = ToolRegistry()
        registry.register(PolicyLookupTool())
        with pytest.raises(ValueError, match="already registered"):
            registry.register(PolicyLookupTool())

    def test_item_i_unknown_tool_fails_closed(self) -> None:
        """Item I: Unknown tool lookup produces UNKNOWN_TOOL error."""
        registry = ToolRegistry()
        ctx = make_tool_context()
        outcome = registry.execute("non_existent_tool", "1.0.0", {}, ctx)
        assert outcome.succeeded is False
        assert outcome.error is not None
        assert outcome.error.code == ToolErrorCode.UNKNOWN_TOOL

    def test_item_j_version_mismatch_rejected(self) -> None:
        """Item J: Version mismatch produces VERSION_MISMATCH error."""
        registry = create_default_registry()
        ctx = make_tool_context()
        outcome = registry.execute("policy_lookup", "9.9.9", {"policy_number": "POL-SYN-001"}, ctx)
        assert outcome.succeeded is False
        assert outcome.error is not None
        assert outcome.error.code == ToolErrorCode.VERSION_MISMATCH


# ===========================================================================
# Categories K-N: Authorization & Security Boundaries
# ===========================================================================


class TestToolAuthorizationAndSecurity:
    def test_item_k_authorization_matrix_enforcement(self) -> None:
        """Item K: Extractor has 1 tool, Investigator has 6 tools, Reviewer has 4 tools."""
        assert AUTHORIZED_TOOLS[AgentType.EXTRACTOR] == frozenset({"document_retrieval"})
        assert AUTHORIZED_TOOLS[AgentType.INVESTIGATOR] == frozenset(
            {
                "policy_lookup",
                "claim_history_lookup",
                "repair_cost_lookup",
                "fraud_signal_lookup",
                "document_retrieval",
                "evidence_lookup",
            }
        )
        assert AUTHORIZED_TOOLS[AgentType.REVIEWER] == frozenset(
            {"document_retrieval", "evidence_lookup", "policy_lookup", "repair_cost_lookup"}
        )

    def test_item_l_supervisor_denied_domain_tools(self) -> None:
        """Item L: Supervisor has zero authorized tools."""
        assert AUTHORIZED_TOOLS[AgentType.SUPERVISOR] == frozenset()
        registry = create_default_registry()
        ctx = make_tool_context(AgentType.SUPERVISOR)
        outcome = registry.execute("policy_lookup", "1.0.0", {"policy_number": "POL-SYN-001"}, ctx)
        assert outcome.succeeded is False
        assert outcome.status == ToolStatus.DENIED
        assert outcome.error is not None
        assert outcome.error.code == ToolErrorCode.UNAUTHORIZED_TOOL

    def test_item_m_cross_agent_unauthorized_call_denied(self) -> None:
        """Item M: Extractor attempting fraud or policy lookup is denied."""
        registry = create_default_registry()
        ctx = make_tool_context(AgentType.EXTRACTOR)
        outcome = registry.execute(
            "fraud_signal_lookup", "1.0.0", {"claim_ref": "SYN-FRAUD-001"}, ctx
        )
        assert outcome.succeeded is False
        assert outcome.status == ToolStatus.DENIED
        assert outcome.error is not None
        assert outcome.error.code == ToolErrorCode.UNAUTHORIZED_TOOL

    def test_item_n_reviewer_denied_fraud_tool(self) -> None:
        """Item N: Reviewer cannot call fraud signal lookup."""
        registry = create_default_registry()
        ctx = make_tool_context(AgentType.REVIEWER)
        outcome = registry.execute(
            "fraud_signal_lookup", "1.0.0", {"claim_ref": "SYN-FRAUD-001"}, ctx
        )
        assert outcome.succeeded is False
        assert outcome.status == ToolStatus.DENIED


# ===========================================================================
# Categories O-R: Tool Failures & Error Taxonomy
# ===========================================================================


class TestToolErrorTaxonomy:
    def test_item_o_error_hierarchy(self) -> None:
        """Item O: Typed exception taxonomy."""
        assert issubclass(ToolNotFoundError, ToolException)
        assert issubclass(ToolAuthorizationError, ToolException)
        assert issubclass(ToolValidationError, ToolException)
        assert issubclass(ToolExecutionError, ToolException)
        assert issubclass(ToolTimeoutError, ToolException)
        assert issubclass(ToolUnavailableError, ToolException)
        assert issubclass(ToolContractError, ToolException)

    def test_item_p_tool_execution_error_recorded(self) -> None:
        """Item P: Failure in tool code recorded without unhandled crash."""

        class ExplodingPolicyTool(PolicyLookupTool):
            def run(self, input_data: PolicyLookupInput, ctx: ToolContext) -> PolicyLookupOutput:
                raise RuntimeError("Boom in tool code")

        registry = ToolRegistry()
        registry.register(ExplodingPolicyTool())
        ctx = make_tool_context(AgentType.INVESTIGATOR)
        outcome = registry.execute("policy_lookup", "1.0.0", {"policy_number": "POL-123"}, ctx)
        assert outcome.succeeded is False
        assert outcome.status == ToolStatus.FAILURE
        assert outcome.error is not None
        assert outcome.error.code == ToolErrorCode.INTERNAL_FAILURE

    def test_item_q_tool_timeout_handling(self) -> None:
        """Item Q: Tool exceeding timeout fails with TIMEOUT status."""
        import time

        class SlowPolicyTool(PolicyLookupTool):
            timeout_seconds = 0.05

            def run(self, input_data: PolicyLookupInput, ctx: ToolContext) -> PolicyLookupOutput:
                time.sleep(0.2)
                return PolicyLookupOutput(found=False)

        registry = ToolRegistry()
        registry.register(SlowPolicyTool())
        ctx = make_tool_context(AgentType.INVESTIGATOR)
        ctx.timeout_seconds = 0.05
        outcome = registry.execute("policy_lookup", "1.0.0", {"policy_number": "POL-123"}, ctx)
        assert outcome.succeeded is False
        assert outcome.status == ToolStatus.TIMEOUT
        assert outcome.error is not None
        assert outcome.error.code == ToolErrorCode.TIMEOUT

    def test_item_r_missing_source_handled(self) -> None:
        """Item R: Querying non-existent entity returns clean factual output (not crash)."""
        registry = create_default_registry()
        ctx = make_tool_context(AgentType.INVESTIGATOR)
        outcome = registry.execute(
            "policy_lookup", "1.0.0", {"policy_number": "POL-DOES-NOT-EXIST"}, ctx
        )
        assert outcome.succeeded is True
        assert outcome.output is not None
        assert outcome.output.found is False
        assert outcome.output.policy is None


# ===========================================================================
# Categories S-V: Provenance
# ===========================================================================


class TestToolProvenance:
    def test_item_s_provenance_fields_complete(self) -> None:
        """Item S: Provenance includes all audit fields."""
        registry = create_default_registry()
        ctx = make_tool_context()
        outcome = registry.execute(
            "repair_cost_lookup",
            "1.0.0",
            {"estimate_ref": "SYN-EST-NORMAL", "claim_ref": "SYN-NORMAL-001"},
            ctx,
        )
        assert outcome.provenance is not None
        prov = outcome.provenance
        assert prov.tool_name == "repair_cost_lookup"
        assert prov.tool_version == "1.0.0"
        assert prov.source_version == "1.0.0"
        assert prov.correlation_id == ctx.correlation_id
        assert prov.retrieved_at is not None

    def test_item_t_fixture_id_tracking(self) -> None:
        """Item T: Fixture identifier captured in provenance."""
        registry = create_default_registry()
        ctx = make_tool_context()
        outcome = registry.execute("policy_lookup", "1.0.0", {"policy_number": "POL-SYN-001"}, ctx)
        assert outcome.provenance is not None
        assert outcome.provenance.fixture_id == "POL-SYN-001"

    def test_item_u_no_secrets_in_provenance(self) -> None:
        """Item U: Provenance contains no sensitive keys."""
        registry = create_default_registry()
        ctx = make_tool_context()
        outcome = registry.execute("evidence_lookup", "1.0.0", {"claim_ref": "SYN-NORMAL-001"}, ctx)
        dump = json.dumps(outcome.provenance.model_dump(), default=str).lower()
        for token in ("sk-", "secret", "password", "apikey", "api_key"):
            assert token not in dump

    def test_item_v_provenance_serialization_round_trip(self) -> None:
        """Item V: Provenance round-trips cleanly through JSON."""
        meta = ToolResultMetadata(
            source="fixture",
            source_version="1.0.0",
            retrieved_at=datetime.now(UTC),
            fixture_id="FIX-001",
            tool_name="policy_lookup",
            tool_version="1.0.0",
            correlation_id=uuid4(),
        )
        data = meta.model_dump_json()
        restored = ToolResultMetadata.model_validate_json(data)
        assert restored.tool_name == meta.tool_name
        assert restored.fixture_id == meta.fixture_id
        assert restored.correlation_id == meta.correlation_id


# ===========================================================================
# Categories W-Z: SQLite Persistence
# ===========================================================================


class TestSqlitePersistence:
    def test_item_w_sqlite_schema_initialization(self, tmp_path: Path) -> None:
        """Item W: Fresh SQLite DB initializes all required tables."""
        factory = make_test_db(tmp_path, "schema.db")
        with factory() as session:
            engine = session.get_bind()
            tables = set(inspect(engine).get_table_names())
            for expected in (
                "claims",
                "workflow_runs",
                "audit_events",
                "agent_executions",
                "tool_invocations",
                "evidence_items",
                "policies",
            ):
                assert expected in tables, f"Missing table {expected}"

    def test_item_x_stable_identifiers_persisted(self, tmp_path: Path) -> None:
        """Item X: UUID identifiers remain stable across persistence."""
        factory = make_test_db(tmp_path, "ids.db")
        run_id = uuid4()
        claim_id = uuid4()
        with UnitOfWork(factory) as uow:
            run = WorkflowRun(
                workflow_run_id=run_id,
                claim_id=claim_id,
                current_state=WorkflowState.RECEIVED,
            )
            uow.runs.save(run)

        with UnitOfWork(factory) as uow:
            loaded = uow.runs.get(run_id)
            assert loaded.workflow_run_id == run_id
            assert loaded.claim_id == claim_id

    def test_item_y_tool_invocation_table_persisted(self, tmp_path: Path) -> None:
        """Item Y: ToolInvocationRecord stores complete execution details."""
        factory = make_test_db(tmp_path, "tool_inv.db")
        inv_id = uuid4()
        run_id = uuid4()
        claim_id = uuid4()
        call = ToolCall(
            invocation_id=inv_id,
            tool_name="policy_lookup",
            tool_version="1.0.0",
            claim_id=claim_id,
            workflow_run_id=run_id,
            execution_id=uuid4(),
            correlation_id=uuid4(),
            requesting_agent=AgentType.INVESTIGATOR,
            status=ToolStatus.SUCCESS,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            duration_ms=12,
            input_json=json.dumps({"policy_number": "POL-1"}),
            output_json=json.dumps({"status": "active"}),
        )
        with UnitOfWork(factory) as uow:
            uow.tool_invocations.save(call)

        with UnitOfWork(factory) as uow:
            loaded = uow.tool_invocations.get(inv_id)
            assert loaded.invocation_id == inv_id
            assert loaded.tool_name == "policy_lookup"
            assert loaded.status == ToolStatus.SUCCESS

    def test_item_z_json_fields_round_trip(self, tmp_path: Path) -> None:
        """Item Z: JSON fields serialize and deserialize accurately."""
        factory = make_test_db(tmp_path, "json.db")
        event_id = uuid4()
        run_id = uuid4()
        claim_id = uuid4()
        event = AuditEvent(
            event_id=event_id,
            workflow_run_id=run_id,
            claim_id=claim_id,
            trace_id=run_id.hex,
            event_type=AuditEventType.STATE_TRANSITION,
            timestamp=datetime.now(UTC),
            actor_type="AGENT",
            actor_id="SUPERVISOR",
            action="RECEIVED->EXTRACTION",
            summary="Intake transition",
            attributes={"trigger": "CLAIM_VALIDATED", "step": "1"},
            result=AuditResult.SUCCESS,
            from_state=WorkflowState.RECEIVED,
            to_state=WorkflowState.EXTRACTION,
            correlation_id=uuid4(),
        )
        with UnitOfWork(factory) as uow:
            uow.workflow_events.append(event)

        with UnitOfWork(factory) as uow:
            events = uow.workflow_events.list_for_run(run_id)
            assert len(events) == 1
            assert events[0].attributes["trigger"] == "CLAIM_VALIDATED"
            assert events[0].attributes["step"] == "1"


# ===========================================================================
# Categories AA-AD: Repositories
# ===========================================================================


class TestRepositories:
    def test_item_aa_claim_repository_crud(self, tmp_path: Path) -> None:
        """Item AA: ClaimRepository supports save, get, and exists."""
        factory = make_test_db(tmp_path, "claim_repo.db")
        claim_id = uuid4()
        claim = Claim(
            claim_id=claim_id,
            external_claim_id="EXT-100",
            policy_number="POL-100",
            customer_id="CUST-100",
            claim_type=ClaimType.COLLISION,
            status=ClaimStatus.SUBMITTED,
            date_of_loss=date(2026, 9, 1),
            date_reported=date(2026, 9, 2),
            description="Vehicle accident",
            claim_amount=Money(amount=Decimal("3000.00"), currency="USD"),
        )
        with UnitOfWork(factory) as uow:
            assert not uow.claims.exists(claim_id)
            uow.claims.save(claim)
            assert uow.claims.exists(claim_id)
            fetched = uow.claims.get(claim_id)
            assert fetched.external_claim_id == "EXT-100"
            assert fetched.claim_amount.amount == Decimal("3000.00")

    def test_item_ab_no_orm_objects_leaked(self, tmp_path: Path) -> None:
        """Item AB: Repositories return Pydantic domain models, not SQLAlchemy rows."""
        factory = make_test_db(tmp_path, "no_leak.db")
        claim_id = uuid4()
        claim = Claim(
            claim_id=claim_id,
            external_claim_id="EXT-101",
            policy_number="POL-101",
            customer_id="CUST-101",
            claim_type=ClaimType.COLLISION,
            status=ClaimStatus.SUBMITTED,
            date_of_loss=date(2026, 9, 1),
            date_reported=date(2026, 9, 2),
            description="Vehicle accident",
            claim_amount=Money(amount=Decimal("1200.00"), currency="USD"),
        )
        with UnitOfWork(factory) as uow:
            uow.claims.save(claim)
            result = uow.claims.get(claim_id)
            assert type(result) is Claim
            assert not hasattr(result, "_sa_instance_state")

    def test_item_ac_missing_record_raises_persistence_error(self, tmp_path: Path) -> None:
        """Item AC: Missing entity raises typed PersistenceError with NOT_FOUND."""
        factory = make_test_db(tmp_path, "missing.db")
        with UnitOfWork(factory) as uow:
            with pytest.raises(PersistenceError) as exc_info:
                uow.claims.get(uuid4())
            assert exc_info.value.code == PersistenceErrorCode.NOT_FOUND

    def test_item_ad_evidence_repository(self, tmp_path: Path) -> None:
        """Item AD: EvidenceRepository saves and lists items scoped to claim."""
        factory = make_test_db(tmp_path, "evidence_repo.db")
        ev_id = uuid4()
        item = EvidenceItem(
            evidence_id=ev_id,
            claim_ref="CLM-999",
            source="repair_estimate",
            source_type=EvidenceSourceType.ESTIMATE_RECORD,
            confidence=0.95,
            value="Front bumper damage: moderate severity",
            provenance="EST-001 line items",
        )
        with UnitOfWork(factory) as uow:
            uow.evidence.save(item)
            items = uow.evidence.list_for_claim("CLM-999")
            assert len(items) == 1
            assert items[0].evidence_id == ev_id
            assert "bumper" in items[0].value


# ===========================================================================
# Categories AE-AH: Unit of Work
# ===========================================================================


class TestUnitOfWork:
    def test_item_ae_atomic_commit(self, tmp_path: Path) -> None:
        """Item AE: Operations within UnitOfWork commit atomically on clean exit."""
        factory = make_test_db(tmp_path, "commit.db")
        run_id = uuid4()
        with UnitOfWork(factory) as uow:
            uow.runs.save(
                WorkflowRun(
                    workflow_run_id=run_id,
                    claim_id=uuid4(),
                    current_state=WorkflowState.RECEIVED,
                )
            )

        with UnitOfWork(factory) as uow:
            assert uow.runs.exists(run_id)

    def test_item_af_atomic_rollback_on_exception(self, tmp_path: Path) -> None:
        """Item AF: Exception inside UnitOfWork rolls back all changes."""
        factory = make_test_db(tmp_path, "rollback.db")
        run_id = uuid4()
        with pytest.raises(RuntimeError, match="Simulated crash"), UnitOfWork(factory) as uow:
            uow.runs.save(
                WorkflowRun(
                    workflow_run_id=run_id,
                    claim_id=uuid4(),
                    current_state=WorkflowState.RECEIVED,
                )
            )
            raise RuntimeError("Simulated crash")

        with UnitOfWork(factory) as uow:
            assert not uow.runs.exists(run_id)

    def test_item_ag_isolated_sessions(self, tmp_path: Path) -> None:
        """Item AG: Separate sessions do not see uncommitted work."""
        factory = make_test_db(tmp_path, "isolation.db")
        run_id = uuid4()
        uow1 = UnitOfWork(factory)
        uow1.__enter__()
        uow1.runs.save(
            WorkflowRun(
                workflow_run_id=run_id,
                claim_id=uuid4(),
                current_state=WorkflowState.RECEIVED,
            )
        )

        with UnitOfWork(factory) as uow2:
            assert not uow2.runs.exists(run_id)

        uow1.__exit__(None, None, None)

        with UnitOfWork(factory) as uow3:
            assert uow3.runs.exists(run_id)

    def test_item_ah_use_outside_context_rejected(self, tmp_path: Path) -> None:
        """Item AH: Accessing repositories outside with context raises error."""
        factory = make_test_db(tmp_path, "outside.db")
        uow = UnitOfWork(factory)
        with pytest.raises(PersistenceError, match="UnitOfWork is not open"):
            _ = uow.claims


# ===========================================================================
# Categories AI-AL: Migrations
# ===========================================================================


class TestAlembicMigrations:
    def test_item_ai_migration_upgrade_head_fresh_db(self, tmp_path: Path) -> None:
        """Item AI: Alembic migration applies cleanly to fresh SQLite database."""
        import os
        from unittest import mock

        from alembic import command
        from alembic.config import Config

        url = f"sqlite:///{tmp_path / 'alembic_fresh.db'}"
        repo_root = Path(__file__).resolve().parents[2]
        cfg = Config(str(repo_root / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", url)
        with mock.patch.dict(os.environ, {"CASEFILE_DATABASE_URL": url}):
            command.upgrade(cfg, "head")

        engine = get_engine(url)
        tables = set(inspect(engine).get_table_names())
        assert "tool_invocations" in tables
        assert "workflow_runs" in tables
        assert "claims" in tables
        assert "alembic_version" in tables
        engine.dispose()

    def test_item_aj_tool_invocations_columns(self, tmp_path: Path) -> None:
        """Item AJ: tool_invocations table has all required columns."""
        factory = make_test_db(tmp_path, "cols.db")
        with factory() as session:
            engine = session.get_bind()
            cols = {c["name"] for c in inspect(engine).get_columns("tool_invocations")}
            for expected in (
                "tool_invocation_id",
                "tool_name",
                "tool_version",
                "claim_id",
                "workflow_run_id",
                "execution_id",
                "correlation_id",
                "requesting_agent",
                "status",
                "started_at",
                "duration_ms",
                "input_json",
                "output_json",
            ):
                assert expected in cols, f"Missing column {expected}"

    def test_item_ak_tool_invocations_indices(self, tmp_path: Path) -> None:
        """Item AK: Indices exist for efficient lookup by run and correlation."""
        factory = make_test_db(tmp_path, "tool_idx.db")
        with factory() as session:
            engine = session.get_bind()
            indices = {
                idx["name"]: idx["column_names"]
                for idx in inspect(engine).get_indexes("tool_invocations")
            }
            indexed_cols = {col for cols in indices.values() for col in cols}
            assert "workflow_run_id" in indexed_cols
            assert "claim_id" in indexed_cols
            assert "execution_id" in indexed_cols

    def test_item_al_migration_downgrade_and_upgrade(self, tmp_path: Path) -> None:
        """Item AL: Alembic migration can downgrade and re-upgrade."""
        import os
        from unittest import mock

        from alembic import command
        from alembic.config import Config

        url = f"sqlite:///{tmp_path / 'alembic_cycle.db'}"
        repo_root = Path(__file__).resolve().parents[2]
        cfg = Config(str(repo_root / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", url)
        with mock.patch.dict(os.environ, {"CASEFILE_DATABASE_URL": url}):
            command.upgrade(cfg, "head")
            command.downgrade(cfg, "-1")
            command.upgrade(cfg, "head")
        engine = get_engine(url)
        assert "tool_invocations" in inspect(engine).get_table_names()
        engine.dispose()


# ===========================================================================
# Categories AM-AP: Agent to Tool Integration
# ===========================================================================


class TestAgentToolIntegration:
    def test_item_am_investigator_authorized_lookups(self) -> None:
        """Item AM: Investigator executes authorized tools through registry."""
        registry = create_default_registry()
        investigator = InvestigatorAgent(DeterministicProvider())
        agent_ctx = AgentContext(
            workflow_run_id=uuid4(),
            claim_id=uuid4(),
            correlation_id=uuid4(),
            agent=AgentType.INVESTIGATOR,
        )

        outcome = investigator.lookup(
            "policy_lookup",
            {"policy_number": "POL-SYN-001"},
            agent_ctx=agent_ctx,
            registry=registry,
        )
        assert outcome.succeeded is True
        assert outcome.output is not None

    def test_item_an_extractor_document_lookup(self) -> None:
        registry = create_default_registry()
        _ = ExtractorAgent(DeterministicProvider())
        agent_ctx = AgentContext(
            workflow_run_id=uuid4(),
            claim_id=uuid4(),
            correlation_id=uuid4(),
            agent=AgentType.EXTRACTOR,
        )
        outcome = registry.execute(
            "document_retrieval",
            "1.0.0",
            {"document_ref": "SYN-DOC-001", "claim_ref": "SYN-NORMAL-001"},
            ToolContext(
                claim_id=agent_ctx.claim_id,
                workflow_run_id=agent_ctx.workflow_run_id,
                agent=AgentType.EXTRACTOR,
            ),
        )
        assert outcome.succeeded is True

    def test_item_ao_reviewer_no_fraud_lookup(self) -> None:
        """Item AO: Reviewer attempting fraud lookup fails authorization."""
        registry = create_default_registry()
        reviewer = ReviewerAgent(DeterministicProvider())
        agent_ctx = AgentContext(
            workflow_run_id=uuid4(),
            claim_id=uuid4(),
            correlation_id=uuid4(),
            agent=AgentType.REVIEWER,
        )
        with pytest.raises(AgentFailedError) as exc_info:
            reviewer.lookup(
                "fraud_signal_lookup",
                {"claim_ref": "SYN-FRAUD-001"},
                agent_ctx=agent_ctx,
                registry=registry,
            )
        assert "may not call 'fraud_signal_lookup'" in str(exc_info.value)

    def test_item_ap_supervisor_has_no_tools(self) -> None:
        """Item AP: Supervisor agent defines empty allowed_tools."""
        supervisor = SupervisorAgent()
        assert supervisor.allowed_tools == frozenset()


# ===========================================================================
# Categories AQ-AT: Architecture Boundaries
# ===========================================================================


class TestArchitectureBoundaries:
    def test_item_aq_agents_never_import_sqlalchemy(self) -> None:
        """Item AQ: Agent package files never import SQLAlchemy."""
        agents_dir = Path(__file__).resolve().parents[2] / "src" / "casefile" / "agents"
        violations = []
        for py_file in sorted(agents_dir.glob("*.py")):
            text = py_file.read_text(encoding="utf-8")
            if "sqlalchemy" in text.lower():
                violations.append(py_file.name)
        assert violations == []

    def test_item_ar_tools_never_mutate_workflow_control_state(self) -> None:
        """Item AR: Tools package files never import or touch WorkflowRunner or WorkflowEngine."""
        tools_dir = Path(__file__).resolve().parents[2] / "src" / "casefile" / "tools"
        violations = []
        for py_file in sorted(tools_dir.glob("*.py")):
            text = py_file.read_text(encoding="utf-8")
            for forbidden in ("WorkflowEngine", "WorkflowRunner", "apply_transition"):
                if forbidden in text:
                    violations.append(f"{py_file.name}:{forbidden}")
        assert violations == []

    def test_item_as_workflow_package_never_imports_llm_providers(self) -> None:
        """Item AS: Workflow package never imports LLM SDKs directly."""
        wf_dir = Path(__file__).resolve().parents[2] / "src" / "casefile" / "workflow"
        violations = []
        for py_file in sorted(wf_dir.glob("*.py")):
            text = py_file.read_text(encoding="utf-8").lower()
            for token in ("openai", "anthropic", "gemini", "langchain", "llm."):
                if token in text:
                    violations.append(f"{py_file.name}:{token}")
        assert violations == []

    def test_item_at_tools_never_import_storage_at_top_level(self) -> None:
        """Item AT: Tool package does not import storage models directly at module level."""
        import casefile.tools as t

        assert not hasattr(t, "Session")
        assert not hasattr(t, "Base")


# ===========================================================================
# Categories AU-AZ: End-to-End Scenarios 1–6
# ===========================================================================


class TestEndToEndScenarios:
    def _make_runner(
        self,
        factory: sessionmaker[Session],
        registry: ToolRegistry | None = None,
        extraction_data: dict[str, Any] | None = None,
        investigation_data: dict[str, Any] | None = None,
        review_data: dict[str, Any] | None = None,
    ) -> tuple[WorkflowRunner, DeterministicProvider, DeterministicProvider, DeterministicProvider]:
        ext_prov = DeterministicProvider()
        inv_prov = DeterministicProvider()
        rev_prov = DeterministicProvider()

        if extraction_data:
            ext_prov.push_json(extraction_data)
        if investigation_data:
            inv_prov.push_json(investigation_data)
        if review_data:
            rev_prov.push_json(review_data)

        extractor = ExtractorAgent(ext_prov)
        investigator = InvestigatorAgent(inv_prov)
        reviewer = ReviewerAgent(rev_prov)

        runner = WorkflowRunner(
            extractor,
            investigator,
            reviewer,
            uow_factory=lambda: UnitOfWork(factory),
            tool_registry=registry,
        )
        return runner, ext_prov, inv_prov, rev_prov

    def test_scenario_1_nominal_persisted_claim(self, tmp_path: Path) -> None:
        """Scenario 1: Nominal claim intake -> Extractor -> Investigator -> Reviewer -> HUMAN_APPROVAL."""
        factory = make_test_db(tmp_path, "scen1.db")
        run_id = uuid4()
        claim_id = uuid4()
        claim = make_test_claim_input(claim_id)
        registry = create_default_registry()

        ext_data = {
            "workflow_id": str(run_id),
            "claimant_name": claim.claimant_name,
            "policy_id": claim.policy_id,
            "incident_date": claim.incident_date,
            "incident_location": "Underground Garage",
            "incident_description": claim.description,
            "claim_amount": "4500.00",
            "requested_coverage_type": "collision",
            "documents_processed": list(claim.documents),
            "extraction_confidence": 0.95,
            "missing_fields": [],
            "is_complete": True,
        }
        inv_data = {
            "workflow_id": str(run_id),
            "policy_details": {"policy_number": claim.policy_id, "status": "active"},
            "claim_history": {"priors": 0},
            "repair_cost_validation": {"estimate_ref": "EST-001", "valid": True},
            "fraud_signals": {"risk_level": "LOW"},
            "supporting_documents": ["SYN-DOC-001"],
            "findings_summary": "Policy active, estimate reasonable, no fraud signals detected.",
            "evidence_strength": 0.92,
            "tool_calls_made": [
                "policy_lookup",
                "evidence_lookup",
                "claim_history_lookup",
                "repair_cost_lookup",
                "fraud_signal_lookup",
            ],
            "investigation_duration_seconds": 2,
            "tools_succeeded": 5,
            "tools_failed": 0,
        }
        rev_data = {
            "workflow_id": str(run_id),
            "decision": "APPROVE",
            "reasoning": "Complete documentation and strong evidence supporting coverage.",
            "confidence_score": 0.96,
            "evidence_completeness": 0.95,
            "identified_gaps": [],
            "fraud_risk_level": "LOW",
            "fraud_signals_detected": False,
        }

        runner, _, _, _ = self._make_runner(factory, registry, ext_data, inv_data, rev_data)
        ctx = runner.run(claim, workflow_run_id=run_id)

        assert ctx.current_snapshot is not None
        assert ctx.current_snapshot.current_state == WorkflowState.HUMAN_APPROVAL

        # Verify durable SQLite records
        with UnitOfWork(factory) as uow:
            run = uow.runs.get(run_id)
            assert run.current_state == WorkflowState.HUMAN_APPROVAL
            events = uow.workflow_events.list_for_run(run_id)
            assert (
                len(events) >= 4
            )  # RECEIVED->EXTRACTION, EXTRACTION->INVESTIGATION, INVESTIGATION->REVIEW, REVIEW->HUMAN_APPROVAL
            executions = uow.agent_executions.list_for_run(run_id)
            assert len(executions) == 3  # Extractor, Investigator, Reviewer
            tool_invs = uow.tool_invocations.list_for_run(run_id)
            assert len(tool_invs) >= 1

    def test_scenario_2_unauthorized_tool_attempt(self, tmp_path: Path) -> None:
        """Scenario 2: Agent attempts unauthorized tool, rejected cleanly, recorded in audit."""
        factory = make_test_db(tmp_path, "scen2.db")
        registry = create_default_registry()
        run_id = uuid4()
        claim_id = uuid4()
        tool_ctx = ToolContext(
            claim_id=claim_id,
            workflow_run_id=run_id,
            agent=AgentType.SUPERVISOR,
        )

        outcome = registry.execute(
            "policy_lookup", "1.0.0", {"policy_number": "POL-SYN-001"}, tool_ctx
        )
        assert outcome.succeeded is False
        assert outcome.status == ToolStatus.DENIED

        with UnitOfWork(factory) as uow:
            if outcome.call is not None:
                uow.tool_invocations.save(outcome.call)

        with UnitOfWork(factory) as uow:
            invs = uow.tool_invocations.list_for_run(run_id)
            assert len(invs) == 1
            assert invs[0].status == ToolStatus.DENIED
            assert invs[0].requesting_agent == AgentType.SUPERVISOR

    def test_scenario_3_deterministic_tool_failure(self, tmp_path: Path) -> None:
        """Scenario 3: Tool failure produces ToolExecutionError and is recorded without state corruption."""
        factory = make_test_db(tmp_path, "scen3.db")
        run_id = uuid4()
        claim_id = uuid4()

        class FailingPolicyTool(PolicyLookupTool):
            def run(self, input_data: PolicyLookupInput, ctx: ToolContext) -> PolicyLookupOutput:
                raise RuntimeError("Database connection timeout")

        registry = ToolRegistry()
        registry.register(FailingPolicyTool())
        ctx = ToolContext(
            claim_id=claim_id,
            workflow_run_id=run_id,
            agent=AgentType.INVESTIGATOR,
        )
        outcome = registry.execute("policy_lookup", "1.0.0", {"policy_number": "POL-SYN-001"}, ctx)
        assert outcome.succeeded is False
        assert outcome.status == ToolStatus.FAILURE

        with UnitOfWork(factory) as uow:
            if outcome.call is not None:
                uow.tool_invocations.save(outcome.call)

        with UnitOfWork(factory) as uow:
            invs = uow.tool_invocations.list_for_run(run_id)
            assert len(invs) == 1
            assert invs[0].status == ToolStatus.FAILURE
            assert invs[0].error is not None
            assert invs[0].error.code == ToolErrorCode.INTERNAL_FAILURE

    def test_scenario_4_conflicting_evidence_preserved(self, tmp_path: Path) -> None:
        """Scenario 4: Conflicting evidence from tools is preserved in structured investigation."""
        factory = make_test_db(tmp_path, "scen4.db")
        run_id = uuid4()
        claim_id = uuid4()
        claim = make_test_claim_input(claim_id)
        registry = create_default_registry()

        ext_data = {
            "workflow_id": str(run_id),
            "claimant_name": claim.claimant_name,
            "policy_id": claim.policy_id,
            "incident_date": claim.incident_date,
            "incident_location": "Main St",
            "incident_description": claim.description,
            "claim_amount": "4500.00",
            "requested_coverage_type": "collision",
            "documents_processed": list(claim.documents),
            "extraction_confidence": 0.95,
            "missing_fields": [],
            "is_complete": True,
        }
        # Conflicting evidence: police report says parked, damage says rollover
        inv_data = {
            "workflow_id": str(run_id),
            "policy_details": {"policy_number": claim.policy_id, "status": "active"},
            "claim_history": {"priors": 1},
            "repair_cost_validation": {"estimate_ref": "SYN-EST-BROKEN", "consistent": False},
            "fraud_signals": {"risk_level": "MEDIUM", "anomaly": "conflicting_damage_narrative"},
            "supporting_documents": ["SYN-DOC-001", "SYN-DOC-002"],
            "findings_summary": "CONFLICT: Police report states stationary rear-end collision, but repair estimate cites rollover structural damage.",
            "evidence_strength": 0.45,
            "tool_calls_made": [
                "policy_lookup",
                "evidence_lookup",
                "repair_cost_lookup",
                "fraud_signal_lookup",
            ],
            "investigation_duration_seconds": 3,
            "tools_succeeded": 4,
            "tools_failed": 0,
        }
        # Reviewer identifies conflict and triggers rework or flags gaps
        rev_data = {
            "workflow_id": str(run_id),
            "decision": "REJECT",
            "reasoning": "Unresolvable contradictions between police report and vehicle physical inspection.",
            "confidence_score": 0.88,
            "evidence_completeness": 0.70,
            "identified_gaps": ["Independent damage assessment required to resolve conflict"],
            "fraud_risk_level": "HIGH",
            "fraud_signals_detected": True,
        }

        runner, _, _, _ = self._make_runner(factory, registry, ext_data, inv_data, rev_data)
        ctx = runner.run(claim, workflow_run_id=run_id)

        assert ctx.current_snapshot is not None
        assert ctx.current_snapshot.current_state == WorkflowState.REJECTED
        assert ctx.investigation_result is not None
        assert "CONFLICT" in ctx.investigation_result.findings_summary

        # Verify durable history shows rejection
        with UnitOfWork(factory) as uow:
            run = uow.runs.get(run_id)
            assert run.current_state == WorkflowState.REJECTED

    def test_scenario_5_persistence_rollback(self, tmp_path: Path) -> None:
        """Scenario 5: Forced failure during multi-entity write triggers complete rollback."""
        factory = make_test_db(tmp_path, "scen5.db")
        run_id = uuid4()
        claim_id = uuid4()

        with (
            pytest.raises(ValueError, match="Database disk full simulation"),
            UnitOfWork(factory) as uow,
        ):
            uow.runs.save(
                WorkflowRun(
                    workflow_run_id=run_id,
                    claim_id=claim_id,
                    current_state=WorkflowState.RECEIVED,
                )
            )
            uow.workflow_events.append(
                AuditEvent(
                    event_id=uuid4(),
                    workflow_run_id=run_id,
                    claim_id=claim_id,
                    trace_id=run_id.hex,
                    event_type=AuditEventType.STATE_TRANSITION,
                    timestamp=datetime.now(UTC),
                    actor_type="AGENT",
                    actor_id="SUPERVISOR",
                    action="RECEIVED->EXTRACTION",
                    summary="Intake transition",
                    attributes={"trigger": "CLAIM_VALIDATED"},
                    result=AuditResult.SUCCESS,
                    from_state=WorkflowState.RECEIVED,
                    to_state=WorkflowState.EXTRACTION,
                    correlation_id=uuid4(),
                )
            )
            raise ValueError("Database disk full simulation")

        with UnitOfWork(factory) as uow:
            assert not uow.runs.exists(run_id)
            events = uow.workflow_events.list_for_run(run_id)
            assert len(events) == 0

    def test_scenario_6_restart_durable_history(self, tmp_path: Path) -> None:
        """Scenario 6: Close session, open fresh session, verify history and state preserved."""
        db_path = tmp_path / "scen6.db"
        engine1 = get_engine(f"sqlite:///{db_path}")
        init_db(engine1)
        factory1 = get_session_factory(engine1)

        run_id = uuid4()
        claim_id = uuid4()
        inv_id = uuid4()

        # Step 1: Write state in factory1
        with UnitOfWork(factory1) as uow:
            uow.runs.save(
                WorkflowRun(
                    workflow_run_id=run_id,
                    claim_id=claim_id,
                    current_state=WorkflowState.INVESTIGATION,
                    step_count=2,
                )
            )
            uow.workflow_events.append(
                AuditEvent(
                    event_id=uuid4(),
                    workflow_run_id=run_id,
                    claim_id=claim_id,
                    trace_id=run_id.hex,
                    event_type=AuditEventType.STATE_TRANSITION,
                    timestamp=datetime.now(UTC),
                    actor_type="AGENT",
                    actor_id="SUPERVISOR",
                    action="RECEIVED->EXTRACTION",
                    summary="Intake transition",
                    attributes={"trigger": "CLAIM_VALIDATED"},
                    result=AuditResult.SUCCESS,
                    from_state=WorkflowState.RECEIVED,
                    to_state=WorkflowState.EXTRACTION,
                    correlation_id=uuid4(),
                )
            )
            uow.tool_invocations.save(
                ToolCall(
                    invocation_id=inv_id,
                    tool_name="policy_lookup",
                    tool_version="1.0.0",
                    claim_id=claim_id,
                    workflow_run_id=run_id,
                    execution_id=uuid4(),
                    correlation_id=uuid4(),
                    requesting_agent=AgentType.INVESTIGATOR,
                    status=ToolStatus.SUCCESS,
                    started_at=datetime.now(UTC),
                    finished_at=datetime.now(UTC),
                    duration_ms=25,
                    input_json=json.dumps({"policy_number": "POL-1"}),
                    output_json=json.dumps({"status": "active"}),
                )
            )

        engine1.dispose()

        # Step 2: Open completely fresh engine and session on same SQLite file
        engine2 = get_engine(f"sqlite:///{db_path}")
        factory2 = get_session_factory(engine2)

        with UnitOfWork(factory2) as uow:
            run = uow.runs.get(run_id)
            assert run.workflow_run_id == run_id
            assert run.current_state == WorkflowState.INVESTIGATION
            assert run.step_count == 2

            events = uow.workflow_events.list_for_run(run_id)
            assert len(events) == 1
            assert events[0].action == "RECEIVED->EXTRACTION"

            tools = uow.tool_invocations.list_for_run(run_id)
            assert len(tools) == 1
            assert tools[0].invocation_id == inv_id
            assert tools[0].tool_name == "policy_lookup"

        engine2.dispose()

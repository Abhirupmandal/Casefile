"""
Integration Tests: Phase 8 end-to-end acceptance (7 scenarios).

Nominal, max-steps, max-cost, rework loop, restart, replay, concurrency —
each proving budget accounting, termination, and durability together.
Deterministic providers only; no network; no live LLM calls.
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from casefile.agents.context import AgentContext
from casefile.agents.deterministic import DeterministicProvider
from casefile.agents.extractor import ExtractorAgent
from casefile.budget.engine import BudgetEngine, ReserveKind, make_retry_guard
from casefile.budget.envelope import BudgetEnvelope
from casefile.budget.pricing import ModelUsage, PricingTable, zero_pricing
from casefile.budget.termination import BudgetTermination
from casefile.budget.usage import BudgetUsage
from casefile.models.contracts import (
    ClaimInput,
    ExtractionRequest,
    WorkflowState,
)
from casefile.models.domain import AgentType
from casefile.models.persistence import get_engine, get_session_factory, init_db
from casefile.storage.unit_of_work import UnitOfWork
from casefile.workflow.context import FixedClock, RunContext, WorkflowSnapshot
from casefile.workflow.engine import WorkflowEngine
from casefile.workflow.store import SqliteWorkflowStore
from casefile.workflow.transitions import TransitionEvent
from casefile.workflow.triggers import Trigger


def _setup(
    tmp_path: Path,
    name: str,
    envelope: BudgetEnvelope | None = None,
    pricing: PricingTable | None = None,
) -> tuple[Engine, sessionmaker[Session], FixedClock, UUID, BudgetEngine]:
    engine = get_engine(f"sqlite:///{tmp_path / name}")
    init_db(engine)
    factory: sessionmaker[Session] = get_session_factory(engine)
    clock = FixedClock(datetime.now(UTC))
    run_id = uuid4()
    default_pricing = PricingTable(
        entries={"deterministic/stub": zero_pricing("deterministic", "stub")}
    )
    budget = BudgetEngine(
        run_id,
        envelope or BudgetEnvelope(),
        BudgetUsage(wall_start=clock.now()),
        clock,
        factory,
        pricing=pricing or default_pricing,
    )
    budget.start_run()
    return engine, factory, clock, run_id, budget


def _claim() -> ClaimInput:
    return ClaimInput(
        policy_id="POL-ACC",
        claimant_name="Acceptance Tester",
        incident_date="2026-09-20",
        claim_amount=Decimal("1500.00"),
        description="Acceptance claim",
        documents=["SYN-DOC-N1"],
    )


def _extraction_payload(run_id: UUID) -> dict[str, object]:
    return {
        "workflow_id": str(run_id),
        "claimant_name": "Acceptance Tester",
        "policy_id": "POL-ACC",
        "incident_date": "2026-09-20",
        "incident_location": "Main St",
        "incident_description": "Acceptance claim",
        "claim_amount": "1500.00",
        "requested_coverage_type": "COLLISION",
        "extraction_confidence": 0.95,
    }


def _review_payload(run_id: UUID, decision: str = "APPROVE") -> dict[str, object]:
    return {
        "workflow_id": str(run_id),
        "decision": decision,
        "reasoning": "Evidence complete",
        "confidence_score": 0.93,
        "evidence_completeness": 1.0,
        "identified_gaps": [],
        "rework_feedback": None,
        "fraud_risk_level": "LOW",
        "fraud_signals_detected": False,
    }


@pytest.mark.integration
class TestScenario1Nominal:
    def test_full_path_with_budget(self, tmp_path: Path) -> None:
        engine, factory, clock, run_id, budget = _setup(tmp_path, "s1.db")
        claim = _claim()
        flow = WorkflowEngine()
        ctx = RunContext(claim_id=claim.claim_id, workflow_run_id=run_id, clock=clock)
        store = SqliteWorkflowStore(engine)

        provider = DeterministicProvider()
        provider.push_json(_extraction_payload(run_id))
        agent = ExtractorAgent(provider)
        agent_ctx = AgentContext(
            claim_id=claim.claim_id, workflow_run_id=run_id, agent=AgentType.EXTRACTOR
        )
        assert budget.pre_step(ReserveKind.AGENT_STEP).granted is True
        extraction, record = agent.run(
            ExtractionRequest(workflow_id=run_id, claim_input=claim), agent_ctx
        )
        assert record.status == "SUCCESS"
        budget.record_model_call(
            ModelUsage(provider="deterministic", model="stub", input_tokens=500, output_tokens=100)
        )

        snap = WorkflowSnapshot(
            workflow_run_id=run_id, claim_id=claim.claim_id, current_state=WorkflowState.RECEIVED
        )
        path = [
            (Trigger.CLAIM_VALIDATED, AgentType.SUPERVISOR),
            (Trigger.EXTRACTION_SUCCEEDED, AgentType.SUPERVISOR),
            (Trigger.INVESTIGATION_SUCCEEDED, AgentType.SUPERVISOR),
            (Trigger.REVIEW_APPROVED, AgentType.SUPERVISOR),
            (Trigger.APPROVAL_GRANTED, AgentType.HUMAN),
        ]
        for trigger, actor in path:
            assert budget.pre_step(ReserveKind.WORKFLOW_STEP).granted is True
            result = flow.apply(
                snap,
                TransitionEvent(
                    claim_id=claim.claim_id, workflow_run_id=run_id, trigger=trigger, actor=actor
                ),
                ctx,
            )
            assert result.record is not None
            store.save_snapshot_and_audit(result.snapshot, result.record)
            snap = result.snapshot
        assert snap.current_state == WorkflowState.APPROVED
        assert budget.usage.steps == 5
        assert budget.usage.agent_steps == 1
        assert budget.usage.total_tokens == 600
        remaining = budget.summary()["remaining_steps"]
        assert remaining == 45
        payload = budget.checkpoint_payload()
        with UnitOfWork(factory) as uow:
            uow.checkpoint_budgets.save(
                uuid4(), run_id, payload["envelope_json"], payload["usage_json"]
            )
        engine.dispose()


@pytest.mark.integration
class TestScenario2MaxSteps:
    def test_stops_exactly_at_boundary(self, tmp_path: Path) -> None:
        engine, _, clock, run_id, budget = _setup(tmp_path, "s2.db", BudgetEnvelope(max_steps=2))
        claim = _claim()
        flow = WorkflowEngine()
        ctx = RunContext(claim_id=claim.claim_id, workflow_run_id=run_id, clock=clock)
        snap = WorkflowSnapshot(
            workflow_run_id=run_id, claim_id=claim.claim_id, current_state=WorkflowState.RECEIVED
        )
        applied = 0
        for trigger in (Trigger.CLAIM_VALIDATED, Trigger.EXTRACTION_SUCCEEDED):
            assert budget.pre_step(ReserveKind.WORKFLOW_STEP).granted is True
            snap = flow.apply(
                snap,
                TransitionEvent(claim_id=claim.claim_id, workflow_run_id=run_id, trigger=trigger),
                ctx,
            ).snapshot
            applied += 1
        assert applied == 2
        denied = budget.pre_step(ReserveKind.WORKFLOW_STEP)
        assert denied.granted is False
        assert denied.reason is not None
        state = budget.terminate(denied.reason)
        assert state == WorkflowState.MAX_STEPS_EXCEEDED
        assert budget.is_terminated() == BudgetTermination.MAX_STEPS_EXCEEDED
        engine.dispose()


@pytest.mark.integration
class TestScenario3MaxCost:
    def test_cost_boundary_persisted(self, tmp_path: Path) -> None:
        pricing = PricingTable(
            entries={
                "det/stub": zero_pricing("det", "stub").model_copy(
                    update={"input_per_1k_usd": Decimal("1.00")}
                )
            }
        )
        engine, _, clock, run_id, budget = _setup(
            tmp_path, "s3.db", BudgetEnvelope(max_cost_usd=Decimal("0.05")), pricing=pricing
        )
        cost = budget.record_model_call(
            ModelUsage(provider="det", model="stub", input_tokens=100, output_tokens=0)
        )
        assert cost == Decimal("0.1000")
        assert budget.check_all() == BudgetTermination.MAX_COST_EXCEEDED
        budget.terminate(BudgetTermination.MAX_COST_EXCEEDED)
        clock2 = FixedClock(datetime.now(UTC))
        restored = BudgetEngine(
            run_id, budget.envelope, BudgetUsage(), clock2, get_session_factory(engine)
        ).restore()
        assert restored.check_all() == BudgetTermination.MAX_COST_EXCEEDED
        engine.dispose()


@pytest.mark.integration
class TestScenario4ReworkLoop:
    def test_bounded_rework_with_exact_count(self, tmp_path: Path) -> None:
        engine, factory, clock, run_id, budget = _setup(
            tmp_path, "s4.db", BudgetEnvelope(max_rework_cycles=2, max_steps=99)
        )
        claim = _claim()
        flow = WorkflowEngine()
        ctx = RunContext(claim_id=claim.claim_id, workflow_run_id=run_id, clock=clock)
        snap = WorkflowSnapshot(
            workflow_run_id=run_id, claim_id=claim.claim_id, current_state=WorkflowState.RECEIVED
        )
        for trigger in (
            Trigger.CLAIM_VALIDATED,
            Trigger.EXTRACTION_SUCCEEDED,
            Trigger.INVESTIGATION_SUCCEEDED,
        ):
            assert budget.pre_step(ReserveKind.WORKFLOW_STEP).granted is True
            snap = flow.apply(
                snap,
                TransitionEvent(claim_id=claim.claim_id, workflow_run_id=run_id, trigger=trigger),
                ctx,
            ).snapshot
        assert snap.current_state == WorkflowState.REVIEW
        for _ in range(2):
            assert budget.pre_step(ReserveKind.WORKFLOW_STEP).granted is True
            snap = flow.apply(
                snap,
                TransitionEvent(
                    claim_id=claim.claim_id,
                    workflow_run_id=run_id,
                    trigger=Trigger.REWORK_REQUESTED,
                ),
                ctx,
            ).snapshot
            assert budget.pre_step(ReserveKind.REWORK_CYCLE).granted is True
            assert budget.pre_step(ReserveKind.WORKFLOW_STEP).granted is True
            snap = flow.apply(
                snap,
                TransitionEvent(
                    claim_id=claim.claim_id,
                    workflow_run_id=run_id,
                    trigger=Trigger.REWORK_DISPATCHED,
                ),
                ctx,
            ).snapshot
            assert budget.pre_step(ReserveKind.WORKFLOW_STEP).granted is True
            snap = flow.apply(
                snap,
                TransitionEvent(
                    claim_id=claim.claim_id,
                    workflow_run_id=run_id,
                    trigger=Trigger.INVESTIGATION_SUCCEEDED,
                ),
                ctx,
            ).snapshot
        assert budget.usage.rework_cycles == 2
        denied = budget.pre_step(ReserveKind.REWORK_CYCLE)
        assert denied.reason == BudgetTermination.MAX_REWORK_EXCEEDED
        assert denied.reason is not None
        budget.terminate(denied.reason)
        with UnitOfWork(factory) as uow:
            assert uow.counters.get_counts(run_id)["rework_cycles"] == 2
        engine.dispose()


@pytest.mark.integration
class TestScenario5Restart:
    def test_usage_survives_restart(self, tmp_path: Path) -> None:
        db_path = tmp_path / "s5.db"
        engine = get_engine(f"sqlite:///{db_path}")
        init_db(engine)
        factory = get_session_factory(engine)
        clock = FixedClock(datetime.now(UTC))
        run_id = uuid4()
        budget = BudgetEngine(
            run_id,
            BudgetEnvelope(max_steps=10),
            BudgetUsage(wall_start=clock.now()),
            clock,
            factory,
        )
        budget.start_run()
        assert budget.pre_step(ReserveKind.WORKFLOW_STEP).granted is True
        assert budget.pre_step(ReserveKind.TOOL_CALL).granted is True
        assert budget.reserve_tokens(25, 25).granted is True
        budget.snapshot_usage()
        engine.dispose()
        # Fresh process objects on the same file
        engine_two = get_engine(f"sqlite:///{db_path}")
        factory_two = get_session_factory(engine_two)
        clock_two = FixedClock(datetime.now(UTC))
        restored = BudgetEngine(
            run_id, budget.envelope, BudgetUsage(), clock_two, factory_two
        ).restore()
        assert restored.usage.steps == 1
        assert restored.usage.tool_calls == 1
        assert restored.usage.total_tokens == 50
        assert restored.usage.wall_start == budget.usage.wall_start
        assert restored.check_all() is None
        engine_two.dispose()


@pytest.mark.integration
class TestScenario6Replay:
    def test_replay_reproduces_budget_outcome(self, tmp_path: Path) -> None:
        engine, factory, clock, run_id, budget = _setup(
            tmp_path, "s6.db", BudgetEnvelope(max_steps=10)
        )
        claim = _claim()
        flow = WorkflowEngine()
        ctx = RunContext(claim_id=claim.claim_id, workflow_run_id=run_id, clock=clock)
        snap = WorkflowSnapshot(
            workflow_run_id=run_id, claim_id=claim.claim_id, current_state=WorkflowState.RECEIVED
        )
        path = [
            (Trigger.CLAIM_VALIDATED, AgentType.SUPERVISOR),
            (Trigger.EXTRACTION_SUCCEEDED, AgentType.SUPERVISOR),
            (Trigger.INVESTIGATION_SUCCEEDED, AgentType.SUPERVISOR),
            (Trigger.REVIEW_APPROVED, AgentType.SUPERVISOR),
            (Trigger.APPROVAL_GRANTED, AgentType.HUMAN),
        ]
        live_states = [snap.current_state]
        for trigger, actor in path:
            assert budget.pre_step(ReserveKind.WORKFLOW_STEP).granted is True
            snap = flow.apply(
                snap,
                TransitionEvent(
                    claim_id=claim.claim_id, workflow_run_id=run_id, trigger=trigger, actor=actor
                ),
                ctx,
            ).snapshot
            live_states.append(snap.current_state)
        assert snap.current_state == WorkflowState.APPROVED
        payload = budget.checkpoint_payload()
        checkpoint_id = uuid4()
        with UnitOfWork(factory) as uow:
            uow.checkpoint_budgets.save(
                checkpoint_id, run_id, payload["envelope_json"], payload["usage_json"]
            )
        # Replay: fresh engine objects, recorded usage, no live calls
        replay_usage = BudgetEngine.usage_from_checkpoint_payload(payload["usage_json"])
        replay_envelope = BudgetEnvelope.model_validate_json(payload["envelope_json"])
        assert replay_usage.steps == 5
        replay_flow = WorkflowEngine()
        replay_ctx = RunContext(
            claim_id=claim.claim_id, workflow_run_id=uuid4(), clock=FixedClock(datetime.now(UTC))
        )
        replay_snap = WorkflowSnapshot(
            workflow_run_id=replay_ctx.workflow_run_id,
            claim_id=claim.claim_id,
            current_state=WorkflowState.RECEIVED,
        )
        replay_states = [replay_snap.current_state]
        for trigger, actor in path:
            replay_snap = replay_flow.apply(
                replay_snap,
                TransitionEvent(
                    claim_id=claim.claim_id,
                    workflow_run_id=replay_ctx.workflow_run_id,
                    trigger=trigger,
                    actor=actor,
                ),
                replay_ctx,
            ).snapshot
            replay_states.append(replay_snap.current_state)
        assert replay_states == live_states
        assert replay_snap.current_state == WorkflowState.APPROVED
        assert replay_envelope == budget.envelope
        # Production usage rows untouched by replay (replay ran a different run id)
        with UnitOfWork(factory) as uow:
            assert uow.counters.get_counts(run_id)["steps"] == 5
        engine.dispose()


@pytest.mark.integration
class TestScenario7Concurrency:
    def test_final_unit_race(self, tmp_path: Path) -> None:
        import threading

        engine = get_engine(f"sqlite:///{tmp_path / 's7.db'}")
        init_db(engine)
        factory = get_session_factory(engine)
        clock = FixedClock(datetime.now(UTC))
        run_id = uuid4()
        seed = BudgetEngine(
            run_id,
            BudgetEnvelope(max_tool_calls=1),
            BudgetUsage(wall_start=clock.now()),
            clock,
            factory,
        )
        seed.start_run()
        envelope = seed.envelope
        results: list[bool] = []

        def worker() -> None:
            contender = BudgetEngine(
                run_id, envelope, BudgetUsage(), FixedClock(datetime.now(UTC)), factory
            )
            results.append(contender.pre_step(ReserveKind.TOOL_CALL).granted)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(results) == [False] * 5 + [True]
        with UnitOfWork(factory) as uow:
            counts = uow.counters.get_counts(run_id)
            assert counts["tool_calls"] == 1
            total = counts["tool_calls"]
            assert total >= 0
        engine.dispose()


@pytest.mark.integration
class TestAgentRetryGuard:
    def test_retry_budget_stops_agent(self, tmp_path: Path) -> None:

        engine, _, _, run_id, budget = _setup(
            tmp_path, "retry.db", BudgetEnvelope(max_agent_retries=2)
        )
        claim = _claim()
        provider = DeterministicProvider()
        provider.push_text("not json")
        provider.push_text("still not json")
        agent = ExtractorAgent(provider, retry_guard=make_retry_guard(budget))
        agent_ctx = AgentContext(
            claim_id=claim.claim_id,
            workflow_run_id=run_id,
            agent=AgentType.EXTRACTOR,
            max_attempts=3,
        )
        from casefile.agents.base import AgentFailedError

        with pytest.raises(AgentFailedError) as exc_info:
            agent.run(ExtractionRequest(workflow_id=run_id, claim_input=claim), agent_ctx)
        assert exc_info.value.error.code == "MAX_AGENT_RETRIES_EXCEEDED"
        assert budget.usage.agent_retries == 1
        engine.dispose()

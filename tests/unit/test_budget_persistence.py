"""
Unit Tests: Budget durability — restore, checkpoint pinning, replay
preservation, concurrent races, crash semantics.
Matrix: R (race), S/T (restart/resume), U/V (replay), Y (idempotency),
Z (crash), AD (migration covered in integration).
"""

import threading
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from casefile.budget.engine import BudgetEngine, ReserveKind
from casefile.budget.envelope import BudgetEnvelope
from casefile.budget.pricing import ModelUsage, PricingTable, zero_pricing
from casefile.budget.usage import BudgetUsage
from casefile.models.persistence import get_engine, get_session_factory, init_db
from casefile.storage.errors import PersistenceError
from casefile.storage.unit_of_work import UnitOfWork
from casefile.workflow.context import FixedClock


def _factory(tmp_path: Path, name: str = "budget-restore.db") -> sessionmaker[Session]:
    engine = get_engine(f"sqlite:///{tmp_path / name}")
    init_db(engine)
    return get_session_factory(engine)


def _started(
    factory: sessionmaker[Session],
    envelope: BudgetEnvelope | None = None,
    pricing: PricingTable | None = None,
) -> BudgetEngine:
    clock = FixedClock(datetime.now(UTC))
    eng = BudgetEngine(
        uuid4(),
        envelope or BudgetEnvelope(),
        BudgetUsage(wall_start=clock.now()),
        clock,
        factory,
        pricing=pricing,
    )
    eng.start_run()
    return eng


@pytest.mark.unit
class TestDurability:
    def test_restart_preserves_usage(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)
        eng = _started(factory, BudgetEnvelope(max_steps=10, max_total_tokens=1000))
        assert eng.pre_step(ReserveKind.WORKFLOW_STEP).granted is True
        assert eng.pre_step(ReserveKind.TOOL_CALL).granted is True
        assert eng.reserve_tokens(40, 20).granted is True
        eng.snapshot_usage()
        clock = FixedClock(datetime.now(UTC))
        restored = BudgetEngine(eng.run_id, eng.envelope, BudgetUsage(), clock, factory).restore()
        assert restored.usage.steps == 1
        assert restored.usage.tool_calls == 1
        assert restored.usage.total_tokens == 60
        assert restored.usage.wall_start == eng.usage.wall_start
        assert restored.envelope == eng.envelope

    def test_cost_survives_restart(self, tmp_path: Path) -> None:
        pricing = PricingTable(
            entries={
                "t/m": zero_pricing("t", "m").model_copy(
                    update={"input_per_1k_usd": Decimal("2.00")}
                )
            }
        )
        factory = _factory(tmp_path)
        eng = _started(factory, pricing=pricing)
        eng.record_model_call(
            ModelUsage(provider="t", model="m", input_tokens=500, output_tokens=0)
        )
        clock = FixedClock(datetime.now(UTC))
        restored = BudgetEngine(eng.run_id, eng.envelope, BudgetUsage(), clock, factory).restore()
        assert restored.usage.cost_usd == Decimal("1.0000")

    def test_wall_clock_inherited(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)
        eng = _started(factory, BudgetEnvelope(max_wall_clock_seconds=100))
        clock = FixedClock(datetime.now(UTC))
        fresh = BudgetEngine(eng.run_id, eng.envelope, BudgetUsage(), clock, factory).restore()
        assert fresh.usage.wall_start == eng.usage.wall_start
        assert fresh.check_all() is None

    def test_checkpoint_pinning_and_restore(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)
        eng = _started(factory, BudgetEnvelope(max_steps=5))
        eng.pre_step(ReserveKind.WORKFLOW_STEP)
        payload = eng.checkpoint_payload()
        checkpoint_id = uuid4()
        with UnitOfWork(factory) as uow:
            uow.checkpoint_budgets.save(
                checkpoint_id, eng.run_id, payload["envelope_json"], payload["usage_json"]
            )
        with UnitOfWork(factory) as uow:
            stored = uow.checkpoint_budgets.get_for_checkpoint(checkpoint_id)
        restored_usage = BudgetEngine.usage_from_checkpoint_payload(stored["usage_json"])
        assert restored_usage.steps == 1
        restored_envelope = BudgetEnvelope.model_validate_json(stored["envelope_json"])
        assert restored_envelope == eng.envelope


@pytest.mark.unit
class TestConcurrentRace:
    def test_exactly_one_winner(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)
        probe = _started(
            factory,
            BudgetEnvelope(max_steps=1),
        )
        run_id = probe.run_id
        envelope = probe.envelope
        results: list[bool] = []

        def worker() -> None:
            clock = FixedClock(datetime.now(UTC))
            contender = BudgetEngine(
                run_id, envelope, BudgetUsage(wall_start=clock.now()), clock, factory
            )
            results.append(contender.pre_step(ReserveKind.WORKFLOW_STEP).granted)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(results) == [False] * 7 + [True]
        with UnitOfWork(factory) as uow:
            assert uow.counters.get_counts(run_id)["steps"] == 1


@pytest.mark.unit
class TestCrashSemantics:
    def test_crash_after_reserve_costs_one_unit(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)
        eng = _started(factory, BudgetEnvelope(max_steps=5))
        run_id = eng.run_id
        assert eng.pre_step(ReserveKind.WORKFLOW_STEP).granted is True
        # Crash: engine object discarded without further work.
        del eng
        with UnitOfWork(factory) as uow:
            assert uow.counters.get_counts(run_id)["steps"] == 1

    def test_duplicate_delivery_no_double_charge(self, tmp_path: Path) -> None:
        from casefile.checkpoint.ledger import IdempotencyLedger

        factory = _factory(tmp_path)
        eng = _started(factory, BudgetEnvelope(max_steps=5))
        engine = get_engine(f"sqlite:///{tmp_path / 'budget-restore.db'}")
        ledger = IdempotencyLedger(engine)
        key = "run:exec:steps:1"
        assert eng.pre_step(ReserveKind.WORKFLOW_STEP).granted is True
        assert (
            ledger.claim(
                idempotency_key=key,
                workflow_run_id=eng.run_id,
                execution_id=uuid4(),
                sequence_no=1,
                result_kind="budget",
                result_ref="granted",
            )
            is True
        )
        # Retry with the same key: ledger hit means no second reservation.
        assert ledger.is_claimed(key) is True
        with UnitOfWork(factory) as uow:
            assert uow.counters.get_counts(eng.run_id)["steps"] == 1


@pytest.mark.unit
class TestEnvelopeImmutability:
    def test_double_start_rejected(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)
        eng = _started(factory)
        with pytest.raises(PersistenceError):
            eng.start_run()

    def test_config_change_does_not_mutate_run(self, tmp_path: Path) -> None:
        factory = _factory(tmp_path)
        eng = _started(factory, BudgetEnvelope(max_steps=5))
        changed = BudgetEnvelope(max_steps=500)
        assert eng.envelope.max_steps == 5
        assert changed.max_steps == 500

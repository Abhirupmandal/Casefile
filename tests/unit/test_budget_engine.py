"""
Unit Tests: BudgetEngine — reservation, exhaustion per dimension,
exact/over-boundary behavior, monotonic latch, wall-clock, retry/rework
accounting, idempotent duplicates, crash semantics.
Matrix: D–O, W, X, Y, Z, AA.
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from casefile.budget.engine import BudgetEngine, BudgetExhaustedError, ReserveKind, should_retry
from casefile.budget.envelope import BudgetEnvelope
from casefile.budget.pricing import ModelUsage, PricingTable, zero_pricing
from casefile.budget.termination import BudgetTermination
from casefile.budget.usage import BudgetUsage
from casefile.models.contracts import WorkflowState
from casefile.models.persistence import get_engine, get_session_factory, init_db
from casefile.storage.errors import PersistenceError
from casefile.workflow.context import FixedClock


def _engine(
    tmp_path: Path,
    envelope: BudgetEnvelope | None = None,
    usage: BudgetUsage | None = None,
    name: str = "budget.db",
    pricing: PricingTable | None = None,
) -> tuple[BudgetEngine, FixedClock]:
    engine = get_engine(f"sqlite:///{tmp_path / name}")
    init_db(engine)
    factory: sessionmaker[Session] = get_session_factory(engine)
    clock = FixedClock(datetime.now(UTC))
    budget_engine = BudgetEngine(
        uuid4(),
        envelope or BudgetEnvelope(),
        usage or BudgetUsage(wall_start=clock.now()),
        clock,
        factory,
        pricing=pricing,
    )
    budget_engine.start_run()
    return budget_engine, clock


def kind_to_limit(kind: str) -> str:
    """Map a reserve kind to its envelope limit field."""
    return {
        ReserveKind.AGENT_STEP: "max_agent_steps",
        ReserveKind.TOOL_CALL: "max_tool_calls",
        ReserveKind.REWORK_CYCLE: "max_rework_cycles",
        ReserveKind.AGENT_RETRY: "max_agent_retries",
    }[kind]


@pytest.mark.unit
class TestReservation:
    def test_grant_then_deny_at_boundary(self, tmp_path: Path) -> None:
        eng, _ = _engine(tmp_path, BudgetEnvelope(max_steps=2))
        first = eng.pre_step(ReserveKind.WORKFLOW_STEP)
        assert first.granted is True
        assert first.remaining_after == 1
        assert eng.pre_step(ReserveKind.WORKFLOW_STEP).granted is True
        denied = eng.pre_step(ReserveKind.WORKFLOW_STEP)
        assert denied.granted is False
        assert denied.reason == BudgetTermination.MAX_STEPS_EXCEEDED
        assert denied.workflow_state == WorkflowState.MAX_STEPS_EXCEEDED

    def test_zero_budget_denies_immediately(self, tmp_path: Path) -> None:
        eng, _ = _engine(tmp_path, BudgetEnvelope(max_tool_calls=0))
        denied = eng.pre_step(ReserveKind.TOOL_CALL)
        assert denied.granted is False
        assert denied.reason == BudgetTermination.MAX_TOOL_CALLS_EXCEEDED

    def test_unknown_kind_rejected(self, tmp_path: Path) -> None:
        eng, _ = _engine(tmp_path)
        with pytest.raises(PersistenceError):
            eng.pre_step("frobnicate")

    def test_each_dimension_maps_reason(self, tmp_path: Path) -> None:
        cases = [
            (ReserveKind.AGENT_STEP, BudgetTermination.MAX_AGENT_STEPS_EXCEEDED),
            (ReserveKind.TOOL_CALL, BudgetTermination.MAX_TOOL_CALLS_EXCEEDED),
            (ReserveKind.REWORK_CYCLE, BudgetTermination.MAX_REWORK_EXCEEDED),
            (ReserveKind.AGENT_RETRY, BudgetTermination.MAX_AGENT_RETRIES_EXCEEDED),
        ]
        for kind, reason in cases:
            env = BudgetEnvelope(max_steps=99).model_copy(update={kind_to_limit(kind): 1})
            eng, _ = _engine(tmp_path, env, name=f"{kind}.db")
            assert eng.pre_step(kind).granted is True
            denied = eng.pre_step(kind)
            assert denied.reason == reason


@pytest.mark.unit
class TestTokenCostWallClock:
    def test_token_reservation_and_denial(self, tmp_path: Path) -> None:
        eng, _ = _engine(tmp_path, BudgetEnvelope(max_total_tokens=100))
        assert eng.reserve_tokens(60, 30).granted is True
        denied = eng.reserve_tokens(60, 30)
        assert denied.granted is False
        assert denied.reason == BudgetTermination.MAX_TOTAL_TOKENS_EXCEEDED

    def test_negative_token_rejected(self, tmp_path: Path) -> None:
        eng, _ = _engine(tmp_path)
        with pytest.raises(PersistenceError):
            eng.reserve_tokens(-1, 0)

    def test_cost_accumulates_and_exhausts(self, tmp_path: Path) -> None:
        pricing = PricingTable(
            entries={
                "acme/m1": zero_pricing("acme", "m1").model_copy(
                    update={"input_per_1k_usd": Decimal("1.00")}
                )
            }
        )
        eng, _ = _engine(tmp_path, BudgetEnvelope(max_cost_usd=Decimal("0.05")), pricing=pricing)
        cost = eng.record_model_call(
            ModelUsage(provider="acme", model="m1", input_tokens=100, output_tokens=0)
        )
        assert cost == Decimal("0.1000")
        assert eng.check_all() == BudgetTermination.MAX_COST_EXCEEDED

    def test_wall_clock_exhaustion(self, tmp_path: Path) -> None:
        eng, clock = _engine(tmp_path, BudgetEnvelope(max_wall_clock_seconds=60))
        assert eng.check_all() is None
        clock.advance(61)
        assert eng.check_all() == BudgetTermination.MAX_WALL_CLOCK_EXCEEDED

    def test_input_output_token_checks(self, tmp_path: Path) -> None:
        eng, _ = _engine(
            tmp_path,
            BudgetEnvelope(max_input_tokens=10, max_output_tokens=10, max_total_tokens=1000),
        )
        eng.usage.input_tokens = 10
        assert eng.check_all() == BudgetTermination.MAX_INPUT_TOKENS_EXCEEDED
        eng.usage.input_tokens = 0
        eng.usage.output_tokens = 10
        assert eng.check_all() == BudgetTermination.MAX_OUTPUT_TOKENS_EXCEEDED


@pytest.mark.unit
class TestMonotonicTermination:
    def test_latch_and_stale_rejection(self, tmp_path: Path) -> None:
        eng, _ = _engine(tmp_path, BudgetEnvelope(max_steps=1))
        assert eng.pre_step(ReserveKind.WORKFLOW_STEP).granted is True
        denied = eng.pre_step(ReserveKind.WORKFLOW_STEP)
        assert denied.reason == BudgetTermination.MAX_STEPS_EXCEEDED
        state = eng.terminate(denied.reason)
        assert state == WorkflowState.MAX_STEPS_EXCEEDED
        assert eng.is_terminated() == BudgetTermination.MAX_STEPS_EXCEEDED
        # Stale worker: everything refuses after latch
        assert eng.pre_step(ReserveKind.WORKFLOW_STEP).granted is False
        assert eng.reserve_tokens(1, 1).granted is False
        assert eng.check_all() == BudgetTermination.MAX_STEPS_EXCEEDED

    def test_double_terminate_fails(self, tmp_path: Path) -> None:
        eng, _ = _engine(tmp_path)
        eng.terminate(BudgetTermination.MAX_COST_EXCEEDED)
        with pytest.raises(PersistenceError):
            eng.terminate(BudgetTermination.MAX_COST_EXCEEDED)

    def test_exhausted_error_carries_state(self) -> None:
        error = BudgetExhaustedError(BudgetTermination.MAX_REWORK_EXCEEDED)
        assert error.reason == BudgetTermination.MAX_REWORK_EXCEEDED
        assert error.workflow_state == WorkflowState.MAX_REWORK_EXCEEDED

    def test_no_live_calls_after_exhaustion(self, tmp_path: Path) -> None:
        import socket
        from unittest import mock

        eng, _ = _engine(tmp_path, BudgetEnvelope(max_steps=0))
        with mock.patch.object(socket.socket, "connect", autospec=True) as connect_mock:
            connect_mock.side_effect = AssertionError("network must not be used")
            denied = eng.pre_step(ReserveKind.WORKFLOW_STEP)
            assert denied.granted is False
        connect_mock.assert_not_called()


@pytest.mark.unit
class TestRetryReworkAccounting:
    def test_should_retry_bounds(self) -> None:
        envelope = BudgetEnvelope(max_agent_retries=2)
        usage = BudgetUsage(agent_retries=0)
        assert should_retry(1, usage, envelope) is True
        assert should_retry(2, usage, envelope) is True
        assert should_retry(3, usage, envelope) is False
        assert should_retry(0, usage, envelope) is False
        spent = BudgetUsage(agent_retries=2)
        assert should_retry(1, spent, envelope) is False

    def test_retry_consumes_durable_budget(self, tmp_path: Path) -> None:
        from casefile.budget.engine import make_retry_guard

        eng, _ = _engine(tmp_path, BudgetEnvelope(max_agent_retries=1))
        guard = make_retry_guard(eng)
        assert guard(1) is True
        assert guard(1) is False
        assert eng.usage.agent_retries == 1

    def test_rework_consumes_durable_budget(self, tmp_path: Path) -> None:
        eng, _ = _engine(tmp_path, BudgetEnvelope(max_rework_cycles=3, max_steps=99))
        for _ in range(3):
            assert eng.pre_step(ReserveKind.REWORK_CYCLE).granted is True
        assert eng.usage.rework_cycles == 3
        denied = eng.pre_step(ReserveKind.REWORK_CYCLE)
        assert denied.reason == BudgetTermination.MAX_REWORK_EXCEEDED

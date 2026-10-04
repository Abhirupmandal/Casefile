"""
Shared pytest fixtures for workflow unit tests.

The root netguard lives in tests/conftest.py and applies to all tests.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from casefile.workflow.context import FixedClock, RunContext
from casefile.workflow.engine import WorkflowEngine


@pytest.fixture
def claim_id() -> UUID:
    return uuid4()


@pytest.fixture
def workflow_run_id() -> UUID:
    return uuid4()


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(datetime(2026, 9, 22, tzinfo=UTC))


@pytest.fixture
def ctx(claim_id: UUID, workflow_run_id: UUID, clock: FixedClock) -> RunContext:
    return RunContext(
        claim_id=claim_id,
        workflow_run_id=workflow_run_id,
        clock=clock,
        max_rework_cycles=3,
    )


@pytest.fixture
def engine() -> WorkflowEngine:
    return WorkflowEngine()

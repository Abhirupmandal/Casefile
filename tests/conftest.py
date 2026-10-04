"""
Shared pytest fixtures for all CASEFILE tests.

Includes the root netguard: every unit and integration test runs with
non-localhost socket connections blocked so accidental network use fails
loudly (localhost/127.0.0.1/::1 remain allowed for SQLite/Redis/Jaeger).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest import mock
from uuid import UUID, uuid4

import pytest

from casefile.workflow.context import FixedClock, RunContext
from casefile.workflow.engine import WorkflowEngine

_ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", ""})


@pytest.fixture(autouse=True)
def _netguard(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Block non-localhost outbound sockets for every test."""
    import socket

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _guarded_connect(self: socket.socket, address: Any) -> None:
        host = address[0] if isinstance(address, tuple) else str(address)
        if isinstance(host, bytes):
            host = host.decode("ascii", "replace")
        if host not in _ALLOWED_HOSTS:
            raise AssertionError(f"netguard: blocked connect to {host!r}")
        return real_connect(self, address)

    def _guarded_connect_ex(self: socket.socket, address: Any) -> int:
        host = address[0] if isinstance(address, tuple) else str(address)
        if isinstance(host, bytes):
            host = host.decode("ascii", "replace")
        if host not in _ALLOWED_HOSTS:
            raise AssertionError(f"netguard: blocked connect_ex to {host!r}")
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _guarded_connect_ex)
    with mock.patch.object(
        socket,
        "create_connection",
        side_effect=AssertionError("netguard: blocked create_connection"),
    ):
        yield


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

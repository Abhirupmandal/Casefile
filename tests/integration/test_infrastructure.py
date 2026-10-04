"""
Integration Tests: Local Infrastructure Services

Validates the Phase 1 persistence architecture against real services:
- SQLite test database file can be opened and queried (no server required)
- Live Redis container if running (ephemeral coordination)
- Live Jaeger collector if running (observability)

Marked with @pytest.mark.integration. Redis/Jaeger tests skip if offline;
the SQLite test always runs because it needs no external service.
"""

import os
from unittest import mock

import pytest

from casefile.config import load_config
from casefile.health import check_database, check_jaeger, check_redis


@pytest.mark.integration
def test_sqlite_test_database_file() -> None:
    """Validate the configured test SQLite database opens and answers SELECT 1."""
    with mock.patch.dict(os.environ):
        os.environ.pop("CASEFILE_DATABASE_URL", None)
        config = load_config("test")
    assert config.database.url.startswith("sqlite://")
    status = check_database(config.database, timeout=5.0)
    assert status.status == "healthy", f"SQLite test database unhealthy: {status.error}"
    assert status.service == "sqlite"


@pytest.mark.integration
def test_live_redis_connection() -> None:
    """Validate connection to live Redis if running."""
    config = load_config("development")
    status = check_redis(config.redis, timeout=2.0)
    if status.status != "healthy":
        pytest.skip(f"Redis service not accessible: {status.error}")
    assert status.status == "healthy"


@pytest.mark.integration
def test_live_jaeger_connection() -> None:
    """Validate connection to live Jaeger collector if running."""
    config = load_config("development")
    status = check_jaeger(config.observability.tracing, timeout=2.0)
    if status.status != "healthy":
        pytest.skip(f"Jaeger collector not accessible: {status.error}")
    assert status.status == "healthy"

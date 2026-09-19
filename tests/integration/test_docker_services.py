"""
Integration Tests: Docker Development Services

Validates connectivity to live PostgreSQL, Redis, and Jaeger containers.
Marked with @pytest.mark.integration and designed to skip if services are offline.
"""

import pytest

from casefile.config import load_config
from casefile.health import check_database, check_jaeger, check_redis


@pytest.mark.integration
def test_live_postgresql_connection() -> None:
    """Validate connection to live PostgreSQL if running."""
    config = load_config("development")
    status = check_database(config.database, timeout=2.0)
    if status.status != "healthy":
        pytest.skip(f"PostgreSQL service not accessible: {status.error}")
    assert status.status == "healthy"


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

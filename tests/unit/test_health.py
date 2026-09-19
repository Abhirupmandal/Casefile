"""
Unit Tests: Health Check System

Validates health check functions for PostgreSQL, Redis, and Jaeger
using both successful and failure test vectors.
"""

from unittest import mock

import pytest

from casefile.config import DatabaseConfig, RedisConfig, TracingConfig, load_config
from casefile.health import (
    check_database,
    check_jaeger,
    check_redis,
    check_system_health,
)


@pytest.mark.unit
class TestHealthChecks:
    """Validate isolated health checks with mocks."""

    def test_database_health_success(self) -> None:
        db_cfg = DatabaseConfig(name="testdb", user="testuser")
        mock_conn = mock.MagicMock()
        mock_cursor = mock.MagicMock()
        mock_conn.cursor.return_value.__enter__.return_value = mock_cursor

        with mock.patch("psycopg2.connect", return_value=mock_conn):
            status = check_database(db_cfg, timeout=1.0)
            assert status.service == "postgresql"
            assert status.status == "healthy"
            assert status.latency_ms is not None
            assert status.error is None

    def test_database_health_failure(self) -> None:
        db_cfg = DatabaseConfig(name="testdb", user="testuser")
        with mock.patch("psycopg2.connect", side_effect=Exception("Connection refused")):
            status = check_database(db_cfg, timeout=1.0)
            assert status.service == "postgresql"
            assert status.status == "unhealthy"
            assert "Connection refused" in (status.error or "")

    def test_redis_health_success(self) -> None:
        redis_cfg = RedisConfig(host="localhost", port=6379)
        mock_client = mock.MagicMock()
        mock_client.ping.return_value = True

        with mock.patch("redis.Redis", return_value=mock_client):
            status = check_redis(redis_cfg, timeout=1.0)
            assert status.service == "redis"
            assert status.status == "healthy"
            assert status.error is None

    def test_redis_health_failure(self) -> None:
        redis_cfg = RedisConfig(host="localhost", port=6379)
        with mock.patch("redis.Redis", side_effect=Exception("Redis unavailable")):
            status = check_redis(redis_cfg, timeout=1.0)
            assert status.service == "redis"
            assert status.status == "unhealthy"
            assert "Redis unavailable" in (status.error or "")

    def test_jaeger_disabled(self) -> None:
        tracing_cfg = TracingConfig(enabled=False)
        status = check_jaeger(tracing_cfg, timeout=1.0)
        assert status.service == "jaeger"
        assert status.status == "disabled"

    def test_jaeger_health_success(self) -> None:
        tracing_cfg = TracingConfig(enabled=True, endpoint="http://localhost:14268/api/traces")
        mock_resp = mock.MagicMock(status_code=200)

        with mock.patch("httpx.get", return_value=mock_resp):
            status = check_jaeger(tracing_cfg, timeout=1.0)
            assert status.service == "jaeger"
            assert status.status == "healthy"

    def test_overall_system_health_healthy(self) -> None:
        config = load_config("test")
        with (
            mock.patch("casefile.health.check_database") as mock_db,
            mock.patch("casefile.health.check_redis") as mock_redis,
            mock.patch("casefile.health.check_jaeger") as mock_jaeger,
        ):

            from casefile.health import HealthStatus

            mock_db.return_value = HealthStatus(service="postgresql", status="healthy")
            mock_redis.return_value = HealthStatus(service="redis", status="healthy")
            mock_jaeger.return_value = HealthStatus(service="jaeger", status="disabled")

            result = check_system_health(config)
            assert result.overall_status == "healthy"
            assert result.is_healthy is True

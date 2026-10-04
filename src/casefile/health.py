"""
CASEFILE Health Check Module

Provides concrete health checks for Phase 1 infrastructure dependencies:
- SQLite local persistence store
- Redis caching and rate limiting
- Jaeger / OpenTelemetry trace collector
"""

from __future__ import annotations

import socket
import time
from typing import Literal
from urllib.parse import urlparse

import httpx
import redis
from pydantic import BaseModel, Field

from casefile.config import AppConfig, DatabaseConfig, RedisConfig, TracingConfig


class HealthStatus(BaseModel):
    """Health check outcome for an individual service."""

    service: str
    status: Literal["healthy", "unhealthy", "disabled"]
    latency_ms: float | None = None
    details: str | None = None
    error: str | None = None


class SystemHealth(BaseModel):
    """Overall system health evaluation across all infrastructure dependencies."""

    environment: str
    overall_status: Literal["healthy", "degraded", "unhealthy"]
    services: dict[str, HealthStatus] = Field(default_factory=dict)

    @property
    def is_healthy(self) -> bool:
        """Return True only if all active services are healthy."""
        return self.overall_status == "healthy"


def check_database(config: DatabaseConfig, timeout: float = 3.0) -> HealthStatus:
    """Check SQLite database connectivity with a real open + trivial query."""
    start_time = time.perf_counter()
    try:
        import sqlite3

        db_path = config.path
    except ValueError as exc:
        latency = round((time.perf_counter() - start_time) * 1000, 2)
        return HealthStatus(
            service="sqlite",
            status="unhealthy",
            latency_ms=latency,
            error=str(exc),
            details=f"Unsupported database URL: {config.url}",
        )
    try:
        conn = sqlite3.connect(db_path, timeout=timeout)
        try:
            cur = conn.execute("SELECT 1;")
            cur.fetchone()
        finally:
            conn.close()

        latency = round((time.perf_counter() - start_time) * 1000, 2)
        return HealthStatus(
            service="sqlite",
            status="healthy",
            latency_ms=latency,
            details=f"Opened SQLite database at {db_path}",
        )
    except Exception as exc:
        latency = round((time.perf_counter() - start_time) * 1000, 2)
        return HealthStatus(
            service="sqlite",
            status="unhealthy",
            latency_ms=latency,
            error=str(exc),
            details=f"Failed to open SQLite database at {db_path}",
        )


def check_redis(config: RedisConfig, timeout: float = 3.0) -> HealthStatus:
    """Check Redis cache and rate limiter connectivity."""
    start_time = time.perf_counter()
    try:
        client = redis.Redis(
            host=config.host,
            port=config.port,
            db=config.db,
            socket_timeout=timeout,
            socket_connect_timeout=timeout,
        )
        ping_ok = client.ping()
        client.close()

        latency = round((time.perf_counter() - start_time) * 1000, 2)
        if ping_ok:
            return HealthStatus(
                service="redis",
                status="healthy",
                latency_ms=latency,
                details=f"Connected to Redis DB {config.db} at {config.host}:{config.port}",
            )
        return HealthStatus(
            service="redis",
            status="unhealthy",
            latency_ms=latency,
            error="PING command did not return True",
        )
    except Exception as exc:
        latency = round((time.perf_counter() - start_time) * 1000, 2)
        return HealthStatus(
            service="redis",
            status="unhealthy",
            latency_ms=latency,
            error=str(exc),
            details=f"Failed to connect to {config.host}:{config.port}",
        )


def check_jaeger(config: TracingConfig, timeout: float = 3.0) -> HealthStatus:
    """Check Jaeger / OTLP collector accessibility."""
    if not config.enabled:
        return HealthStatus(
            service="jaeger",
            status="disabled",
            details="Tracing is disabled in configuration",
        )

    start_time = time.perf_counter()
    parsed = urlparse(config.endpoint)
    host = parsed.hostname or "localhost"
    port = parsed.port or 14268

    try:
        # First attempt HTTP ping if HTTP endpoint
        if parsed.scheme in ("http", "https"):
            try:
                resp = httpx.get(f"{parsed.scheme}://{host}:{port}/", timeout=timeout)
                latency = round((time.perf_counter() - start_time) * 1000, 2)
                return HealthStatus(
                    service="jaeger",
                    status="healthy",
                    latency_ms=latency,
                    details=f"Jaeger endpoint responded (HTTP {resp.status_code}) at {host}:{port}",
                )
            except (httpx.ConnectError, httpx.HTTPError):
                pass  # Fall back to socket check

        # Socket level connectivity check
        with socket.create_connection((host, port), timeout=timeout):
            latency = round((time.perf_counter() - start_time) * 1000, 2)
            return HealthStatus(
                service="jaeger",
                status="healthy",
                latency_ms=latency,
                details=f"TCP port {port} open on {host}",
            )
    except Exception as exc:
        latency = round((time.perf_counter() - start_time) * 1000, 2)
        return HealthStatus(
            service="jaeger",
            status="unhealthy",
            latency_ms=latency,
            error=str(exc),
            details=f"Cannot reach Jaeger collector at {host}:{port}",
        )


def check_system_health(config: AppConfig) -> SystemHealth:
    """Perform health checks across all enabled infrastructure services."""
    services: dict[str, HealthStatus] = {}

    db_status = check_database(config.database)
    services["sqlite"] = db_status

    redis_status = check_redis(config.redis)
    services["redis"] = redis_status

    jaeger_status = check_jaeger(config.observability.tracing)
    services["jaeger"] = jaeger_status

    active_statuses = [s.status for s in services.values() if s.status != "disabled"]

    overall: Literal["healthy", "degraded", "unhealthy"] = "unhealthy"
    if all(s == "healthy" for s in active_statuses):
        overall = "healthy"
    elif any(s == "healthy" for s in active_statuses):
        overall = "degraded"

    return SystemHealth(
        environment=config.environment,
        overall_status=overall,
        services=services,
    )

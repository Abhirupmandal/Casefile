"""
Health and Readiness endpoints (Phase 7 §18).

- GET /health: Application liveness probe (public).
- GET /ready: System readiness probe (public), verifies primary SQLite persistence.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from casefile.api.dependencies import get_config
from casefile.api.schemas.models import HealthResponse, ReadinessResponse
from casefile.config import AppConfig
from casefile.health import check_database

router = APIRouter(tags=["Health"])


@router.get("/health", response_model=HealthResponse)
def health_check(
    config: Annotated[AppConfig, Depends(get_config)],
) -> HealthResponse:
    """Liveness probe indicating the HTTP process is running."""
    return HealthResponse(
        status="healthy",
        service="casefile-api",
        environment=config.environment,
        version="0.1.0",
    )


@router.get("/ready", response_model=ReadinessResponse)
def readiness_check(
    config: Annotated[AppConfig, Depends(get_config)],
) -> ReadinessResponse:
    """
    Readiness probe validating critical dependencies.
    Fails (HTTP 503) if SQLite database is unreachable or corrupt.
    """
    db_status = check_database(config.database)
    is_ready = db_status.status == "healthy"

    services_detail = {
        "sqlite": {
            "status": db_status.status,
            "latency_ms": db_status.latency_ms,
            "details": db_status.details,
            "error": db_status.error,
        }
    }

    if not is_ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="System is not ready: primary persistence store unavailable",
        )

    return ReadinessResponse(
        ready=True,
        status="ready",
        services=services_detail,
    )

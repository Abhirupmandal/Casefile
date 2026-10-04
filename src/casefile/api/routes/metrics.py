"""
Operational metrics endpoints (Phase 7 §22).

- GET /api/v1/metrics: Real-time operational dashboard metrics derived from the database.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from casefile.api.auth import Principal, Role, require_roles
from casefile.api.dependencies import ApiService, get_api_service
from casefile.api.schemas.models import OperationalMetricsResponse

router = APIRouter(prefix="/api/v1/metrics", tags=["Metrics"])

VIEWER_AND_ABOVE = (
    Role.VIEWER,
    Role.OPERATOR,
    Role.CLAIM_REVIEWER,
    Role.SENIOR_REVIEWER,
    Role.CLAIM_SUPERVISOR,
    Role.ADMIN,
)


@router.get("", response_model=OperationalMetricsResponse)
def get_operational_metrics(
    principal: Annotated[Principal, Depends(require_roles(*VIEWER_AND_ABOVE))],
    service: Annotated[ApiService, Depends(get_api_service)],
) -> OperationalMetricsResponse:
    """Retrieve operational dashboard metrics aggregated from workflow and approval state."""
    return service.get_operational_metrics()

"""
Evaluation results endpoints (Phase 7 §26).

- GET /api/v1/evaluations: Retrieve Phase 6 benchmark results and 22 derived metrics.
- GET /api/v1/evaluations/cases: List evaluation cases.
- GET /api/v1/evaluations/cases/{case_id}: Inspect single scenario details.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from casefile.api.auth import Principal, Role, require_roles
from casefile.api.dependencies import ApiService, get_api_service
from casefile.api.schemas.models import EvaluationCaseSummary, EvaluationSummaryResponse

router = APIRouter(prefix="/api/v1/evaluations", tags=["Evaluations"])

VIEWER_AND_ABOVE = (
    Role.VIEWER,
    Role.OPERATOR,
    Role.CLAIM_REVIEWER,
    Role.SENIOR_REVIEWER,
    Role.CLAIM_SUPERVISOR,
    Role.ADMIN,
)


@router.get("", response_model=EvaluationSummaryResponse)
def get_evaluation_summary(
    principal: Annotated[Principal, Depends(require_roles(*VIEWER_AND_ABOVE))],
    service: Annotated[ApiService, Depends(get_api_service)],
) -> EvaluationSummaryResponse:
    """Retrieve full evaluation benchmark summary and all 22 metrics."""
    return service.get_evaluation_summary()


@router.get("/cases", response_model=list[EvaluationCaseSummary])
def list_evaluation_cases(
    principal: Annotated[Principal, Depends(require_roles(*VIEWER_AND_ABOVE))],
    service: Annotated[ApiService, Depends(get_api_service)],
) -> list[EvaluationCaseSummary]:
    """List all 30 evaluation cases."""
    return service.get_evaluation_summary().cases


@router.get("/cases/{case_id}", response_model=EvaluationCaseSummary)
def get_evaluation_case_detail(
    case_id: str,
    principal: Annotated[Principal, Depends(require_roles(*VIEWER_AND_ABOVE))],
    service: Annotated[ApiService, Depends(get_api_service)],
) -> EvaluationCaseSummary:
    """Retrieve details for a specific evaluation case."""
    return service.get_evaluation_case(case_id)

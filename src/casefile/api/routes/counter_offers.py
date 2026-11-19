"""Counter-Offers and Negotiation endpoints (Phase 12.4).

- POST /api/v1/claims/{claim_id}/counter-offers: Reviewer creates a new counter-offer.
- GET  /api/v1/claims/{claim_id}/counter-offers: List negotiation history.
- GET  /api/v1/claims/{claim_id}/counter-offers/active: Retrieve active pending offer.
- GET  /api/v1/claims/{claim_id}/counter-offers/{offer_id}: Inspect specific offer.
- POST /api/v1/claims/{claim_id}/counter-offers/{offer_id}/respond: Authenticated claimant response.
- POST /api/v1/claims/{claim_id}/counter-offers/{offer_id}/withdraw: Reviewer withdraws offer.
- POST /api/v1/claims/{claim_id}/counter-offers/{offer_id}/settle: Reviewer authorizes settlement.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status

from casefile.api.auth import Principal, Role, require_roles
from casefile.api.dependencies import (
    ApiService,
    IdempotencyStore,
    get_api_service,
    get_idempotency_store,
)
from casefile.api.rate_limit import rate_limit
from casefile.api.schemas.models import (
    CounterOfferItemResponse,
    CounterOfferListResponse,
    CounterOfferResponseRequest,
    CounterOfferSettlementResponse,
    CreateCounterOfferRequest,
)
from casefile.models.persistence import ClaimRecord

router = APIRouter(prefix="/api/v1/claims/{claim_id}/counter-offers", tags=["Counter-Offers"])

REVIEWER_ROLES = (
    Role.CLAIM_REVIEWER,
    Role.SENIOR_REVIEWER,
    Role.CLAIM_SUPERVISOR,
    Role.ADMIN,
)

READ_ROLES = (
    Role.VIEWER,
    Role.CLAIMANT,
    Role.OPERATOR,
    Role.CLAIM_REVIEWER,
    Role.SENIOR_REVIEWER,
    Role.CLAIM_SUPERVISOR,
    Role.ADMIN,
)

RESPOND_ROLES = (
    Role.CLAIMANT,
    Role.OPERATOR,
    Role.CLAIM_SUPERVISOR,
    Role.ADMIN,
)


def _verify_claimant_access(claim_id: UUID, principal: Principal, service: ApiService) -> None:
    """Ensure a claimant principal can only view or respond to their own claims."""
    if principal.role == Role.CLAIMANT:
        with service._uow() as uow:
            row = uow.session.get(ClaimRecord, str(claim_id))
            if row is not None and row.customer_id != principal.principal_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Claimant '{principal.principal_id}' is not authorized to access claim owned by '{row.customer_id}'",
                )


@router.post(
    "",
    response_model=CounterOfferItemResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60.0))],
)
def create_counter_offer(
    claim_id: UUID,
    req: CreateCounterOfferRequest,
    principal: Annotated[Principal, Depends(require_roles(*REVIEWER_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
    idempotency_store: Annotated[IdempotencyStore, Depends(get_idempotency_store)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> CounterOfferItemResponse:
    """Create a new structured counter-offer for an open claim."""
    scope_key = f"counter_offer_create:{claim_id}"
    if idempotency_key:
        cached = idempotency_store.check(scope_key, idempotency_key, req)
        if cached is not None:
            return CounterOfferItemResponse(**cached.response_data)

    response = service.create_counter_offer(
        claim_id=claim_id,
        principal=principal,
        req=req,
    )

    if idempotency_key:
        idempotency_store.record(
            scope_key,
            idempotency_key,
            req,
            status_code=status.HTTP_201_CREATED,
            response_data=response.model_dump(),
        )

    return response


@router.get(
    "",
    response_model=CounterOfferListResponse,
)
def list_counter_offers(
    claim_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(*READ_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
) -> CounterOfferListResponse:
    """Retrieve full immutable counter-offer negotiation history for a claim."""
    _verify_claimant_access(claim_id, principal, service)
    return service.list_counter_offers(claim_id)


@router.get(
    "/active",
    response_model=CounterOfferItemResponse | None,
)
def get_active_counter_offer(
    claim_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(*READ_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
) -> CounterOfferItemResponse | None:
    """Retrieve currently active pending counter-offer, or None if none is active."""
    _verify_claimant_access(claim_id, principal, service)
    return service.get_active_counter_offer(claim_id)


@router.get(
    "/{offer_id}",
    response_model=CounterOfferItemResponse,
)
def get_counter_offer(
    claim_id: UUID,
    offer_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(*READ_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
) -> CounterOfferItemResponse:
    """Retrieve a specific counter-offer by ID."""
    _verify_claimant_access(claim_id, principal, service)
    return service.get_counter_offer(claim_id, offer_id)


@router.post(
    "/{offer_id}/respond",
    response_model=CounterOfferItemResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(rate_limit(max_requests=60, window_seconds=60.0))],
)
def respond_to_counter_offer(
    claim_id: UUID,
    offer_id: UUID,
    req: CounterOfferResponseRequest,
    principal: Annotated[Principal, Depends(require_roles(*RESPOND_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
    idempotency_store: Annotated[IdempotencyStore, Depends(get_idempotency_store)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> CounterOfferItemResponse:
    """Submit an authenticated claimant response (ACCEPT, REJECT, or REQUEST_REVISION)."""
    _verify_claimant_access(claim_id, principal, service)

    scope_key = f"counter_offer_respond:{offer_id}"
    if idempotency_key:
        cached = idempotency_store.check(scope_key, idempotency_key, req)
        if cached is not None:
            return CounterOfferItemResponse(**cached.response_data)

    response = service.respond_to_counter_offer(
        claim_id=claim_id,
        offer_id=offer_id,
        principal=principal,
        req=req,
    )

    if idempotency_key:
        idempotency_store.record(
            scope_key,
            idempotency_key,
            req,
            status_code=status.HTTP_200_OK,
            response_data=response.model_dump(),
        )

    return response


@router.post(
    "/{offer_id}/withdraw",
    response_model=CounterOfferItemResponse,
    status_code=status.HTTP_200_OK,
)
def withdraw_counter_offer(
    claim_id: UUID,
    offer_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(*REVIEWER_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
    reason: str = "",
) -> CounterOfferItemResponse:
    """Insurer reviewer withdraws a pending counter-offer."""
    return service.withdraw_counter_offer(
        claim_id=claim_id,
        offer_id=offer_id,
        principal=principal,
        reason=reason,
    )


@router.post(
    "/{offer_id}/settle",
    response_model=CounterOfferSettlementResponse,
    status_code=status.HTTP_200_OK,
)
def settle_counter_offer(
    claim_id: UUID,
    offer_id: UUID,
    principal: Annotated[Principal, Depends(require_roles(*REVIEWER_ROLES))],
    service: Annotated[ApiService, Depends(get_api_service)],
    reason: str = "",
) -> CounterOfferSettlementResponse:
    """Insurer reviewer authorizes settlement for an accepted counter-offer."""
    return service.settle_counter_offer(
        claim_id=claim_id,
        offer_id=offer_id,
        principal=principal,
        reason=reason,
    )

"""
Structured API Error Handling (Phase 7 §15).

Formats all API errors consistently as:
{
  "error": {
    "code": "...",
    "message": "...",
    "request_id": "...",
    "correlation_id": "...",
    "details": {}
  }
}

Guarantees:
- Never leaks Python tracebacks, filesystem paths, SQL statements, or secrets.
- Maps domain and persistence exceptions to stable machine-readable codes.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from casefile.approval.model import ApprovalError, ApprovalErrorCode
from casefile.checkpoint.model import CheckpointCorruptError, CheckpointNotFoundError
from casefile.storage.errors import PersistenceError, PersistenceErrorCode

logger = logging.getLogger(__name__)


class ErrorDetail(BaseModel):
    """Structured error payload."""

    code: str = Field(..., description="Stable machine-readable error code")
    message: str = Field(..., description="Safe human-readable explanation")
    request_id: str = Field(default_factory=lambda: uuid4().hex, description="Request identifier")
    correlation_id: str = Field(
        default_factory=lambda: uuid4().hex, description="Trace correlation identifier"
    )
    details: dict[str, Any] = Field(default_factory=dict, description="Structured failure context")


class ErrorResponse(BaseModel):
    """Standard API error envelope."""

    error: ErrorDetail


class APIError(Exception):
    """Base application exception for explicit API error responses."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


def _get_request_ids(request: Request) -> tuple[str, str]:
    """Extract request_id and correlation_id from request state or headers."""
    req_id = (
        getattr(request.state, "request_id", None)
        or request.headers.get("X-Request-ID")
        or uuid4().hex
    )
    corr_id = (
        getattr(request.state, "correlation_id", None)
        or request.headers.get("X-Correlation-ID")
        or uuid4().hex
    )
    return str(req_id), str(corr_id)


def build_error_response(
    code: str,
    message: str,
    status_code: int,
    request: Request,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    """Format and return a structured JSONResponse."""
    req_id, corr_id = _get_request_ids(request)
    payload = {
        "error": {
            "code": code,
            "message": message,
            "request_id": req_id,
            "correlation_id": corr_id,
            "details": details or {},
        }
    }
    return JSONResponse(status_code=status_code, content=payload)


APPROVAL_ERROR_STATUS_MAP: dict[ApprovalErrorCode, int] = {
    ApprovalErrorCode.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ApprovalErrorCode.FORBIDDEN_ACTOR: status.HTTP_403_FORBIDDEN,
    ApprovalErrorCode.UNAUTHORIZED: status.HTTP_403_FORBIDDEN,
    ApprovalErrorCode.NOT_PENDING: status.HTTP_409_CONFLICT,
    ApprovalErrorCode.STALE_VERSION: status.HTTP_409_CONFLICT,
    ApprovalErrorCode.TERMINAL_RUN: status.HTTP_409_CONFLICT,
    ApprovalErrorCode.EXPIRED_REQUEST: status.HTTP_409_CONFLICT,
    ApprovalErrorCode.DUPLICATE_REQUEST: status.HTTP_409_CONFLICT,
    ApprovalErrorCode.CONFLICT: status.HTTP_409_CONFLICT,
}


def register_error_handlers(app: FastAPI) -> None:
    """Register uniform structured error handlers on the FastAPI application."""

    @app.exception_handler(APIError)
    async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
        return build_error_response(
            code=exc.code,
            message=exc.message,
            status_code=exc.status_code,
            request=request,
            details=exc.details,
        )

    @app.exception_handler(ApprovalError)
    async def approval_error_handler(request: Request, exc: ApprovalError) -> JSONResponse:
        code_str = exc.code.value if hasattr(exc.code, "value") else str(exc.code)
        status_code = APPROVAL_ERROR_STATUS_MAP.get(exc.code, status.HTTP_400_BAD_REQUEST)
        return build_error_response(
            code=f"APPROVAL_{code_str}",
            message=str(exc),
            status_code=status_code,
            request=request,
            details={"approval_error_code": code_str},
        )

    @app.exception_handler(PersistenceError)
    async def persistence_error_handler(request: Request, exc: PersistenceError) -> JSONResponse:
        if exc.code == PersistenceErrorCode.NOT_FOUND:
            st = status.HTTP_404_NOT_FOUND
        elif exc.code == PersistenceErrorCode.INTEGRITY_VIOLATION:
            st = status.HTTP_409_CONFLICT
        elif exc.code == PersistenceErrorCode.UNAVAILABLE:
            st = status.HTTP_503_SERVICE_UNAVAILABLE
        else:
            st = status.HTTP_500_INTERNAL_SERVER_ERROR

        return build_error_response(
            code=f"PERSISTENCE_{exc.code.value}",
            message=str(exc),
            status_code=st,
            request=request,
            details={"persistence_code": exc.code.value},
        )

    @app.exception_handler(CheckpointNotFoundError)
    async def checkpoint_not_found_handler(
        request: Request, exc: CheckpointNotFoundError
    ) -> JSONResponse:
        return build_error_response(
            code="CHECKPOINT_NOT_FOUND",
            message=str(exc),
            status_code=status.HTTP_404_NOT_FOUND,
            request=request,
        )

    @app.exception_handler(CheckpointCorruptError)
    async def checkpoint_corrupt_handler(
        request: Request, exc: CheckpointCorruptError
    ) -> JSONResponse:
        return build_error_response(
            code="CHECKPOINT_CORRUPT",
            message="Checkpoint integrity verification failed",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            request=request,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # Sanitize validation errors to not leak sensitive values
        sanitized_errors = []
        for err in exc.errors():
            sanitized_errors.append(
                {
                    "loc": [str(x) for x in err.get("loc", [])],
                    "msg": err.get("msg", ""),
                    "type": err.get("type", ""),
                }
            )
        return build_error_response(
            code="VALIDATION_ERROR",
            message="Request body or query parameter validation failed",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            request=request,
            details={"validation_errors": sanitized_errors},
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        code = "HTTP_ERROR"
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            code = "UNAUTHENTICATED"
        elif exc.status_code == status.HTTP_403_FORBIDDEN:
            code = "PERMISSION_DENIED"
        elif exc.status_code == status.HTTP_404_NOT_FOUND:
            code = "RESOURCE_NOT_FOUND"
        elif exc.status_code == status.HTTP_409_CONFLICT:
            code = "CONFLICT"
        elif exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            code = "RATE_LIMIT_EXCEEDED"

        headers = getattr(exc, "headers", None)
        resp = build_error_response(
            code=code,
            message=str(exc.detail),
            status_code=exc.status_code,
            request=request,
        )
        if headers:
            for k, v in headers.items():
                resp.headers[k] = v
        return resp

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        req_id, corr_id = _get_request_ids(request)
        logger.error(
            "Unhandled server error on %s %s: %s (req_id=%s, corr_id=%s)",
            request.method,
            request.url.path,
            exc,
            req_id,
            corr_id,
            exc_info=True,
        )
        return build_error_response(
            code="INTERNAL_SERVER_ERROR",
            message="An unexpected internal server error occurred. Please contact system operations.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            request=request,
        )

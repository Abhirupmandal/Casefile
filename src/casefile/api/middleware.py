"""
API Observability & Telemetry Middleware (Phase 7 §19).

Captures request/response metrics and distributed tracing spans.
Enforces privacy and security boundaries:
- Drops Authorization, passwords, tokens, API keys, documents, prompts
- Sanitizes all span attributes using Phase 5 sanitize_attributes
- Injects standard X-Request-ID and X-Correlation-ID response headers
"""

from __future__ import annotations

import logging
import time
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from casefile.observability.sanitize import sanitize_attributes

logger = logging.getLogger(__name__)


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """Instruments incoming HTTP requests with tracing, request correlation, and safe timing."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        start_time = time.perf_counter()

        # Extract or generate identifiers
        request_id = request.headers.get("X-Request-ID") or uuid4().hex
        correlation_id = request.headers.get("X-Correlation-ID") or uuid4().hex

        # Attach to request state for downstream handlers
        request.state.request_id = request_id
        request.state.correlation_id = correlation_id

        # Safely extract principal role if available
        role_header = request.headers.get("X-Principal-Role")
        client_role = role_header.strip() if role_header else "unknown"

        status_code = 500
        error_code: str | None = None

        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        except Exception as exc:
            error_code = type(exc).__name__
            raise
        finally:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

            # Build and sanitize telemetry attributes
            raw_attributes = {
                "http.method": request.method,
                "http.route": request.url.path,
                "http.status_code": status_code,
                "http.duration_ms": duration_ms,
                "casefile.request_id": request_id,
                "casefile.correlation_id": correlation_id,
                "casefile.principal_role": client_role,
            }
            if error_code:
                raw_attributes["casefile.error_code"] = error_code

            # Sanitize attributes to ensure no leaked sensitive substrings or credentials
            sanitized = sanitize_attributes(raw_attributes)

            # Record log
            logger.info(
                "API Request: %s %s -> %d in %0.2fms (req_id=%s)",
                request.method,
                request.url.path,
                status_code,
                duration_ms,
                request_id,
                extra={"telemetry": sanitized},
            )

            # Ensure response headers are set (if response was created)
            if "response" in locals() and response is not None:
                response.headers["X-Request-ID"] = request_id
                response.headers["X-Correlation-ID"] = correlation_id


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Injects defense-in-depth HTTP security headers into all responses."""

    def __init__(
        self,
        app: object,
        content_security_policy: str = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline' fonts.googleapis.com; "
            "font-src 'self' fonts.gstatic.com; "
            "img-src 'self' data:; "
            "connect-src 'self';"
        ),
        strict_transport_security: bool = False,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.csp = content_security_policy
        self.hsts = strict_transport_security

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = self.csp
        if self.hsts:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Enforces maximum incoming request body size to protect API boundaries from DoS."""

    def __init__(self, app: object, max_bytes: int = 2 * 1024 * 1024) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                cl_val = int(content_length)
                if cl_val > self.max_bytes:
                    from starlette.responses import JSONResponse

                    return JSONResponse(
                        status_code=413,
                        content={
                            "error": {
                                "category": "VALIDATION",
                                "code": "REQUEST_TOO_LARGE",
                                "message": f"Request body exceeds maximum allowed size of {self.max_bytes} bytes",
                            }
                        },
                    )
            except ValueError:
                pass
        return await call_next(request)

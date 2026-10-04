"""
FastAPI Application Entry Point & Factory (Phase 7 §2, §3, §20).

Creates and configures the CASEFILE REST API:
- Explicit OpenAPI documentation with tags and security requirements
- Observability and tracing middleware
- Uniform structured error handlers (no leaked secrets or tracebacks)
- Versioned routing (/api/v1/...)
- Health and readiness probes
- Safe Operations Console static asset mounting (if present)
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import Engine

from casefile.api.dependencies import set_api_service, set_config
from casefile.api.errors import register_error_handlers
from casefile.api.middleware import ObservabilityMiddleware
from casefile.api.routes import (
    agents_router,
    approvals_router,
    audit_router,
    checkpoints_router,
    claims_router,
    evaluations_router,
    health_router,
    metrics_router,
    replay_router,
    tools_router,
    workflows_router,
)
from casefile.api.service import ApiService
from casefile.config import AppConfig, load_config

OPENAPI_TAGS = [
    {"name": "Health", "description": "Liveness and readiness probes."},
    {"name": "Claims", "description": "Claim submission and intake."},
    {"name": "Workflows", "description": "Workflow execution status and audit history."},
    {
        "name": "Approvals",
        "description": "Human-in-the-loop approval queue and adjudication decisions.",
    },
    {"name": "Checkpoints", "description": "Durable checkpoint metadata and integrity status."},
    {"name": "Replay", "description": "Deterministic simulation replay control."},
    {"name": "Agents", "description": "Sanitized agent execution telemetry and outputs."},
    {"name": "Tools", "description": "Sanitized external tool invocations and provenance."},
    {
        "name": "Evaluations",
        "description": "Deterministic evaluation scenarios and benchmark metrics.",
    },
    {"name": "Audit", "description": "Immutable system audit trail."},
    {"name": "Metrics", "description": "Real-time operational dashboard metrics."},
]


def create_app(
    engine: Engine | None = None,
    config: AppConfig | None = None,
    database_url: str | None = None,
) -> FastAPI:
    """Create and configure the production FastAPI application instance."""
    app_config = config or load_config()
    set_config(app_config)

    # Initialize Service and register with dependencies
    db_url = database_url or app_config.database.url
    service = ApiService(engine=engine, database_url=db_url)
    set_api_service(service)

    from collections.abc import AsyncIterator
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def app_lifespan(app_instance: FastAPI) -> AsyncIterator[None]:
        yield
        # Graceful shutdown: cleanup database connection pools and flush telemetry
        from casefile.api.dependencies import get_api_service

        svc = get_api_service()
        if svc is not None:
            svc.close()

    app = FastAPI(
        title="CASEFILE Adjudication Platform API",
        description=(
            "Operational REST API for CASEFILE Multi-Agent Insurance Claim Orchestration. "
            "Exposes workflow status, deterministic replay, human-in-the-loop approvals, "
            "agent/tool visibility, and system health."
        ),
        version="0.1.0",
        openapi_tags=OPENAPI_TAGS,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=app_lifespan,
    )

    # Set authentication provider based on environment/config
    from casefile.api.auth import (
        DevAuthProvider,
        ProductionAuthProvider,
        set_auth_provider,
    )

    if app_config.environment == "production" or app_config.security.auth_mode == "production":
        set_auth_provider(ProductionAuthProvider())
    else:
        set_auth_provider(DevAuthProvider())

    # Observability & Tracing Middleware (inner)
    app.add_middleware(ObservabilityMiddleware)

    # Security Headers Middleware
    if app_config.security.enable_security_headers:
        from casefile.api.middleware import SecurityHeadersMiddleware

        app.add_middleware(
            SecurityHeadersMiddleware,
            content_security_policy=app_config.security.content_security_policy,
            strict_transport_security=app_config.security.strict_transport_security,
        )

    # Request Size Limit Middleware (protects from oversized payloads)
    from casefile.api.middleware import RequestSizeLimitMiddleware

    app.add_middleware(
        RequestSizeLimitMiddleware,
        max_bytes=app_config.security.max_request_body_bytes,
    )

    # CORS support with explicit origin allowlist
    app.add_middleware(
        CORSMiddleware,
        allow_origins=app_config.security.cors_allowed_origins,
        allow_credentials=app_config.security.cors_allow_credentials,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"],
        allow_headers=["*"],
    )

    # Structured Error Handling
    register_error_handlers(app)

    # Health & Readiness (root routes)
    app.include_router(health_router)

    # Versioned API routes (/api/v1/...)
    app.include_router(claims_router)
    app.include_router(workflows_router)
    app.include_router(approvals_router)
    app.include_router(checkpoints_router)
    app.include_router(replay_router)
    app.include_router(agents_router)
    app.include_router(tools_router)
    app.include_router(evaluations_router)
    app.include_router(audit_router)
    app.include_router(metrics_router)

    # Mount UI static files if built
    ui_dist = Path("ui/dist")
    if ui_dist.exists() and (ui_dist / "index.html").exists():
        app.mount("/console", StaticFiles(directory=str(ui_dist), html=True), name="console")

    return app


# Default app instance for ASGI servers (uvicorn casefile.api.app:app)
app = create_app()

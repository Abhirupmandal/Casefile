"""
FastAPI Dependencies for CASEFILE API (Phase 7).

Provides dependency injection for:
- ApiService instance
- AppConfig instance
- Authentication and authorization helpers
- Idempotency store
- Rate limiter
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Header

from casefile.api.auth import (
    Principal,
    Role,
    get_current_principal,
    require_permission,
    require_roles,
)
from casefile.api.idempotency import IdempotencyStore, get_idempotency_store
from casefile.api.rate_limit import RateLimiter, get_rate_limiter
from casefile.api.service import ApiService
from casefile.config import AppConfig, load_config

_GLOBAL_SERVICE: ApiService | None = None
_GLOBAL_CONFIG: AppConfig | None = None


def get_config() -> AppConfig:
    global _GLOBAL_CONFIG
    if _GLOBAL_CONFIG is None:
        _GLOBAL_CONFIG = load_config()
    return _GLOBAL_CONFIG


def set_config(config: AppConfig) -> None:
    global _GLOBAL_CONFIG
    _GLOBAL_CONFIG = config


def get_api_service() -> ApiService:
    global _GLOBAL_SERVICE
    if _GLOBAL_SERVICE is None:
        cfg = get_config()
        _GLOBAL_SERVICE = ApiService(database_url=cfg.database.url)
    return _GLOBAL_SERVICE


def set_api_service(service: ApiService) -> None:
    global _GLOBAL_SERVICE
    _GLOBAL_SERVICE = service


def get_idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str | None:
    return idempotency_key


__all__ = [
    "ApiService",
    "AppConfig",
    "IdempotencyStore",
    "Principal",
    "RateLimiter",
    "Role",
    "get_api_service",
    "get_config",
    "get_current_principal",
    "get_idempotency_key",
    "get_idempotency_store",
    "get_rate_limiter",
    "require_permission",
    "require_roles",
    "set_api_service",
    "set_config",
]

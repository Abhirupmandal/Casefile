"""
Rate limiting abstraction and in-memory implementation (Phase 7 §17).

Provides:
- Abstract RateLimiter protocol enabling future distributed implementations
- Thread-safe sliding window InMemoryRateLimiter
- FastAPI rate limiting dependency protecting sensitive mutation endpoints
- Never corrupts state upon rate limit exhaustion; returns HTTP 429 with Retry-After header.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Coroutine
from typing import Any, Protocol

from fastapi import HTTPException, Request, status


class RateLimiter(Protocol):
    """Abstract interface for rate limiting."""

    def acquire(
        self, key: str, max_requests: int, window_seconds: float
    ) -> tuple[bool, int, float]:
        """
        Attempt to acquire an action slot.
        Returns:
            (allowed: bool, remaining: int, retry_after_seconds: float)
        """
        ...


class InMemoryRateLimiter:
    """Thread-safe sliding-window rate limiter."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # key -> list of float timestamps
        self._history: dict[str, list[float]] = {}

    def acquire(
        self, key: str, max_requests: int, window_seconds: float
    ) -> tuple[bool, int, float]:
        now = time.monotonic()
        cutoff = now - window_seconds

        with self._lock:
            # Purge entries older than window
            timestamps = [ts for ts in self._history.get(key, []) if ts > cutoff]

            if len(timestamps) >= max_requests:
                # Rate limited
                oldest = timestamps[0]
                retry_after = max(0.1, round(window_seconds - (now - oldest), 2))
                self._history[key] = timestamps
                return False, 0, retry_after

            # Slot acquired
            timestamps.append(now)
            self._history[key] = timestamps
            remaining = max_requests - len(timestamps)
            return True, remaining, 0.0

    def reset(self) -> None:
        """Clear all rate limit histories (used in test fixtures)."""
        with self._lock:
            self._history.clear()


_GLOBAL_RATE_LIMITER: RateLimiter = InMemoryRateLimiter()


def get_rate_limiter() -> RateLimiter:
    return _GLOBAL_RATE_LIMITER


def set_rate_limiter(limiter: RateLimiter) -> None:
    global _GLOBAL_RATE_LIMITER
    _GLOBAL_RATE_LIMITER = limiter


def rate_limit(
    max_requests: int, window_seconds: float = 60.0
) -> Callable[[Request], Coroutine[Any, Any, None]]:
    """
    FastAPI dependency factory to enforce rate limiting on an endpoint.
    Keyed by principal_id or client IP address + endpoint route.
    """

    async def _rate_limit_dependency(request: Request) -> None:
        # Determine client key
        principal_id = getattr(request.state, "principal_id", None)
        if not principal_id:
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                tok = auth_header[7:].strip()
                from casefile.api.auth import DEV_TOKENS

                if tok in DEV_TOKENS:
                    principal_id = DEV_TOKENS[tok][0]
        if not principal_id:
            principal_id = request.headers.get("X-Principal-Id")
        if not principal_id:
            principal_id = request.client.host if request.client else "unknown-client"
        rate_key = f"{request.method}:{request.url.path}:{principal_id}"
        limiter = get_rate_limiter()

        allowed, remaining, retry_after = limiter.acquire(rate_key, max_requests, window_seconds)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Maximum {max_requests} requests per {window_seconds}s. Try again in {retry_after} seconds.",
                headers={
                    "Retry-After": str(int(retry_after) + 1),
                    "X-RateLimit-Limit": str(max_requests),
                    "X-RateLimit-Remaining": "0",
                },
            )

    return _rate_limit_dependency

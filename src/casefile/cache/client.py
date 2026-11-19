"""
Distributed and tiered caching abstraction (Phase 9 §C).

Provides:
- CacheClient protocol for type-safe caching
- InMemoryCache for isolated unit testing and local fallback
- RedisCache for distributed caching backed by Redis with fail-safe error handling
"""

from __future__ import annotations

import threading
import time
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)


class CacheClient(Protocol):
    """Abstract interface for key-value caching."""

    def get(self, key: str) -> str | None:
        """Retrieve a cached string value by key, or None if expired/missing."""
        ...

    def set(self, key: str, value: str, ttl_seconds: int) -> bool:
        """Store a string value with a time-to-live in seconds. Returns True if stored."""
        ...

    def delete(self, key: str) -> bool:
        """Remove a cached key. Returns True if removed."""
        ...

    def clear(self) -> None:
        """Clear all cached entries."""
        ...

    def is_available(self) -> bool:
        """Check if the cache backend is operational."""
        ...


class InMemoryCache:
    """Thread-safe in-memory cache with TTL support."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # key -> (value, expires_at_monotonic)
        self._entries: dict[str, tuple[str, float | None]] = {}

    def get(self, key: str) -> str | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            val, expires_at = entry
            if expires_at is not None and now > expires_at:
                del self._entries[key]
                return None
            return val

    def set(self, key: str, value: str, ttl_seconds: int) -> bool:
        expires_at = time.monotonic() + ttl_seconds if ttl_seconds > 0 else None
        with self._lock:
            self._entries[key] = (value, expires_at)
        return True

    def delete(self, key: str) -> bool:
        with self._lock:
            if key in self._entries:
                del self._entries[key]
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def is_available(self) -> bool:
        return True

    def size(self) -> int:
        """Return count of unexpired keys."""
        now = time.monotonic()
        with self._lock:
            # Purge expired
            expired = [k for k, (_, exp) in self._entries.items() if exp is not None and now > exp]
            for k in expired:
                del self._entries[k]
            return len(self._entries)


class RedisCache:
    """
    Redis-backed cache with fail-safe error handling.

    If Redis is down or unreachable, operations log a warning and return
    gracefully (get -> None, set -> False) so business workflows are never blocked.
    """

    def __init__(
        self,
        redis_client: Any = None,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        socket_timeout: float = 2.0,
        key_prefix: str = "casefile:cache:",
    ) -> None:
        self._key_prefix = key_prefix
        if redis_client is not None:
            self._client = redis_client
        else:
            import redis

            self._client = redis.Redis(
                host=host,
                port=port,
                db=db,
                socket_timeout=socket_timeout,
                socket_connect_timeout=socket_timeout,
                decode_responses=True,
            )

    def _prefixed(self, key: str) -> str:
        return f"{self._key_prefix}{key}" if not key.startswith(self._key_prefix) else key

    def get(self, key: str) -> str | None:
        try:
            val = self._client.get(self._prefixed(key))
            return str(val) if val is not None else None
        except Exception as exc:
            logger.warning("redis_cache_get_failed", key=key, error=str(exc))
            return None

    def set(self, key: str, value: str, ttl_seconds: int) -> bool:
        try:
            self._client.set(self._prefixed(key), value, ex=ttl_seconds)
            return True
        except Exception as exc:
            logger.warning("redis_cache_set_failed", key=key, error=str(exc))
            return False

    def delete(self, key: str) -> bool:
        try:
            res = self._client.delete(self._prefixed(key))
            return bool(res > 0)
        except Exception as exc:
            logger.warning("redis_cache_delete_failed", key=key, error=str(exc))
            return False

    def clear(self) -> None:
        try:
            keys = self._client.keys(f"{self._key_prefix}*")
            if keys:
                self._client.delete(*keys)
        except Exception as exc:
            logger.warning("redis_cache_clear_failed", error=str(exc))

    def is_available(self) -> bool:
        try:
            return bool(self._client.ping())
        except Exception:
            return False


_GLOBAL_CACHE: CacheClient = InMemoryCache()


def get_cache_client() -> CacheClient:
    """Return the currently configured global cache client."""
    return _GLOBAL_CACHE


def set_cache_client(client: CacheClient) -> None:
    """Set the global cache client."""
    global _GLOBAL_CACHE
    _GLOBAL_CACHE = client

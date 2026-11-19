"""
Cache module exports (Phase 9).
"""

from casefile.cache.client import (
    CacheClient,
    InMemoryCache,
    RedisCache,
    get_cache_client,
    set_cache_client,
)

__all__ = [
    "CacheClient",
    "InMemoryCache",
    "RedisCache",
    "get_cache_client",
    "set_cache_client",
]

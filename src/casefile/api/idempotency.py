"""
Idempotency framework for mutation endpoints (Phase 7 §16).

Guarantees:
- Re-executing an operation with an identical idempotency key returns the stored response.
- Reusing an existing idempotency key with a DIFFERENT request payload raises an IDEMPOTENCY_CONFLICT (HTTP 409).
- Thread-safe storage with in-memory implementation for Phase 7.
"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import Any

from fastapi import HTTPException, status
from pydantic import BaseModel


class CachedResponse(BaseModel):
    """Cached response for an idempotent operation."""

    status_code: int
    response_data: dict[str, Any]
    request_hash: str


def validate_idempotency_key(key: str | None) -> None:
    """Validate idempotency key length and character set."""
    if key is None:
        return
    if not (1 <= len(key) <= 256):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Idempotency-Key: length must be between 1 and 256 characters.",
        )
    if not key.isascii() or any(c in key for c in "\r\n\t"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Idempotency-Key: must contain only valid printable ASCII characters.",
        )


class IdempotencyStore:
    """Thread-safe in-memory idempotency store."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, CachedResponse] = {}

    @staticmethod
    def compute_hash(payload: Any) -> str:
        """Compute deterministic SHA-256 hash of the request payload."""
        if payload is None:
            return "empty"
        if isinstance(payload, BaseModel):
            normalized = payload.model_dump_json(exclude_none=True)
        elif isinstance(payload, dict):
            normalized = json.dumps(payload, sort_keys=True, default=str)
        elif isinstance(payload, str | bytes):
            normalized = str(payload)
        else:
            normalized = repr(payload)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def check(self, scope: str, key: str, payload: Any) -> CachedResponse | None:
        """
        Check if an idempotency key has been executed.
        - If not seen: returns None.
        - If seen with matching payload: returns the CachedResponse.
        - If seen with conflicting payload: raises HTTP 409 IDEMPOTENCY_CONFLICT.
        """
        validate_idempotency_key(key)
        store_key = f"{scope}:{key}"
        current_hash = self.compute_hash(payload)

        with self._lock:
            cached = self._records.get(store_key)
            if cached is None:
                return None

            if cached.request_hash != current_hash:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Idempotency conflict: key '{key}' was previously executed with a different request payload.",
                )
            return cached

    def record(
        self, scope: str, key: str, payload: Any, status_code: int, response_data: dict[str, Any]
    ) -> None:
        """Store the outcome of a successful idempotent execution."""
        validate_idempotency_key(key)
        store_key = f"{scope}:{key}"
        current_hash = self.compute_hash(payload)
        with self._lock:
            self._records[store_key] = CachedResponse(
                status_code=status_code,
                response_data=response_data,
                request_hash=current_hash,
            )

    def reset(self) -> None:
        with self._lock:
            self._records.clear()


_GLOBAL_IDEMPOTENCY_STORE = IdempotencyStore()


def get_idempotency_store() -> IdempotencyStore:
    return _GLOBAL_IDEMPOTENCY_STORE


def set_idempotency_store(store: IdempotencyStore) -> None:
    global _GLOBAL_IDEMPOTENCY_STORE
    _GLOBAL_IDEMPOTENCY_STORE = store

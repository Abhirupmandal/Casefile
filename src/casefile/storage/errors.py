"""
Typed persistence errors (Phase 6 §24).

Repository failures surface as these categories, never as silent empty
successes and never as raw SQLAlchemy exceptions outside this package.
"""

from __future__ import annotations

from enum import Enum


class PersistenceErrorCode(str, Enum):
    """Repository failure categories."""

    NOT_FOUND = "NOT_FOUND"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    INTEGRITY_VIOLATION = "INTEGRITY_VIOLATION"
    TRANSACTION_FAILED = "TRANSACTION_FAILED"
    UNAVAILABLE = "UNAVAILABLE"
    MAPPING_FAILED = "MAPPING_FAILED"


class PersistenceError(Exception):
    """Typed repository failure with a stable category."""

    def __init__(self, code: PersistenceErrorCode, message: str) -> None:
        super().__init__(f"{code.value}: {message}")
        self.code = code


def map_integrity_error(message: str) -> PersistenceError:
    """Wrap a database integrity violation (duplicates, FK breaks)."""
    return PersistenceError(PersistenceErrorCode.INTEGRITY_VIOLATION, message)


def map_unavailable(message: str) -> PersistenceError:
    """Wrap database unavailability (locked file, missing database)."""
    return PersistenceError(PersistenceErrorCode.UNAVAILABLE, message)


def map_transaction_failure(message: str) -> PersistenceError:
    """Wrap commit/rollback failures."""
    return PersistenceError(PersistenceErrorCode.TRANSACTION_FAILED, message)


def map_mapping_failure(message: str) -> PersistenceError:
    """Wrap domain/database mapping failures."""
    return PersistenceError(PersistenceErrorCode.MAPPING_FAILED, message)

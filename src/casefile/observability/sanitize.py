"""
Attribute sanitization and safe error serialization (Phase 10 §19, §13).

Span/metric attributes carry IDs, enums, counts, and durations only.
Anything resembling secrets, credentials, documents, reasoning, or PII
is dropped; strings are length-capped. Error telemetry carries stable
codes and categories — raw exception text only when proven safe.
"""

from __future__ import annotations

import re
from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel

MAX_ATTRIBUTE_LENGTH = 256

_DENY_SUBSTRINGS = (
    "password",
    "passwd",
    "secret",
    "api_key",
    "apikey",
    "auth_token",
    "authtoken",
    "authorization",
    "bearer",
    "token",
    "credential",
    "private_key",
    "privatekey",
    "document",
    "raw_document",
    "content",
    "reasoning",
    "chain_of_thought",
    "chain-of-thought",
    "prompt_text",
    "prompt",
    "completion",
    "llm_prompt",
    "llm_output",
    "statement",
    "claimant_statement",
    "financial",
    "account_number",
    "credit_card",
    "ssn",
    "social_security",
)

_SECRET_PATTERNS = [
    re.compile(r"sk-[a-zA-Z0-9_\-]{8,}", re.IGNORECASE),
    re.compile(r"bearer\s+[a-zA-Z0-9_\-\.]{8,}", re.IGNORECASE),
    re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b"),  # credit cards
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # SSN
]


def _is_denied(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in _DENY_SUBSTRINGS)


def sanitize_value(value: object) -> str | int | float | bool:
    """Coerce one value to a safe OTel attribute primitive."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return str(value.value)
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    if len(text) > MAX_ATTRIBUTE_LENGTH:
        text = text[:MAX_ATTRIBUTE_LENGTH]
    return text


def sanitize_attributes(attributes: dict[str, object]) -> dict[str, str | int | float | bool]:
    """Drop denied keys, coerce and cap the rest. Never raises."""
    try:
        clean: dict[str, str | int | float | bool] = {}
        for key, value in attributes.items():
            if not isinstance(key, str) or _is_denied(key):
                continue
            clean[key] = sanitize_value(value)
        return clean
    except Exception:
        return {}


class SafeErrorModel(BaseModel):
    """Structured, leakage-free error telemetry."""

    model_config = {"frozen": True}

    category: str
    code: str
    component: str
    retryable: bool = False
    workflow_state: str = ""
    terminal: bool = False
    trace_id: str = ""
    span_id: str = ""


def safe_error(
    *,
    category: str,
    code: str,
    component: str,
    retryable: bool = False,
    workflow_state: str = "",
    terminal: bool = False,
    trace_id: str = "",
    span_id: str = "",
) -> SafeErrorModel:
    """Build error telemetry from stable fields only — never raw text."""
    return SafeErrorModel(
        category=category,
        code=code,
        component=component,
        retryable=retryable,
        workflow_state=workflow_state,
        terminal=terminal,
        trace_id=trace_id,
        span_id=span_id,
    )


def sanitize_log_message(msg: str) -> str:
    """Sanitize a log or audit message to prevent CRLF log injection and mask secrets."""
    if not isinstance(msg, str):
        msg = str(msg)
    cleaned = msg.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub("[REDACTED]", cleaned)
    return cleaned

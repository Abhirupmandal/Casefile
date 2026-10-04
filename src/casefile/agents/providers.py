"""
CASEFILE provider-agnostic LLM abstraction (AGENTS.md providers, ADR-005).

Agents depend only on the LLMProvider protocol and structured request /
response types. No OpenAI / Anthropic / Gemini SDK object ever crosses an
agent boundary. Real network adapters are optional, key-gated, and never
used in tests (see adapters.py).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from casefile.models.versioning import SchemaVersion


class LLMMessage(BaseModel):
    """One message in provider-agnostic form."""

    model_config = {"frozen": True}

    role: Literal["system", "user"] = "user"
    content: str


class LLMRequest(BaseModel):
    """Structured generation request. No provider-specific fields."""

    model_config = {"frozen": True}

    request_id: UUID = Field(default_factory=uuid4)
    correlation_id: UUID = Field(default_factory=uuid4)
    model: str
    messages: tuple[LLMMessage, ...]
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=1024, ge=1)
    response_schema_name: str = ""
    schema_version: SchemaVersion = "1.0.0"


class LLMResponse(BaseModel):
    """Provider answer in neutral form. Raw text only; parsing is separate."""

    model_config = {"frozen": True}

    request_id: UUID
    content: str
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    model: str = ""
    provider: str = ""
    completed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProviderError(Exception):
    """Provider failure (transport, auth, rate limit, server error)."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class ProviderTimeoutError(ProviderError):
    """Provider call exceeded its deadline. Always retryable."""

    def __init__(self, message: str = "Provider call timed out") -> None:
        super().__init__(message, retryable=True)


class LLMProvider(Protocol):
    """Neutral generation interface implemented by all providers."""

    @property
    def name(self) -> str:
        """Stable provider identifier (e.g. 'deterministic', 'openai')."""
        ...

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Produce one response or raise ProviderError / ProviderTimeoutError."""
        ...

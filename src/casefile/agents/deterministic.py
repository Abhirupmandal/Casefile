"""
Deterministic test provider: scripted, network-free LLM stand-in.

Queues scripted outcomes (JSON payloads, raw text, provider errors,
timeouts) and replays them in order while recording every request.
Empty queue ⇒ explicit ProviderError, never hidden randomness or I/O.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from casefile.agents.providers import (
    LLMRequest,
    LLMResponse,
    ProviderError,
    ProviderTimeoutError,
)


class DeterministicProvider:
    """Scripted LLMProvider for tests. No keys, no network, no clock reads."""

    def __init__(self, provider_name: str = "deterministic") -> None:
        self._provider_name = provider_name
        self._script: list[object] = []
        self.requests: list[LLMRequest] = []

    @property
    def name(self) -> str:
        """Stable provider identifier."""
        return self._provider_name

    def push_json(self, payload: dict[str, object], **metadata: object) -> None:
        """Queue a structured JSON response."""
        self._script.append({"kind": "json", "payload": payload, "metadata": metadata})

    def push_text(self, text: str, **metadata: object) -> None:
        """Queue a raw-text (unparseable) response."""
        self._script.append({"kind": "text", "text": text, "metadata": metadata})

    def push_error(self, error: ProviderError) -> None:
        """Queue a provider failure."""
        self._script.append({"kind": "error", "error": error})

    def push_timeout(self) -> None:
        """Queue a provider timeout."""
        self._script.append({"kind": "timeout"})

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Replay the next scripted outcome for a recorded request."""
        self.requests.append(request)
        if not self._script:
            raise ProviderError("DeterministicProvider has no scripted outcome left")
        outcome = self._script.pop(0)
        if not isinstance(outcome, dict):
            raise ProviderError("DeterministicProvider received a malformed script entry")
        kind = outcome.get("kind")
        if kind == "timeout":
            raise ProviderTimeoutError()
        if kind == "error":
            error = outcome.get("error")
            if not isinstance(error, ProviderError):
                raise ProviderError("DeterministicProvider received a malformed error entry")
            raise error
        metadata = outcome.get("metadata")
        meta: dict[str, object] = metadata if isinstance(metadata, dict) else {}
        if kind == "json":
            payload = outcome.get("payload", {})
            content = json.dumps(payload, sort_keys=True)
        elif kind == "text":
            content = str(outcome.get("text", ""))
        else:
            raise ProviderError(f"DeterministicProvider received unknown outcome {kind!r}")
        return LLMResponse(
            request_id=request.request_id,
            content=content,
            input_tokens=_as_int(meta.get("input_tokens")),
            output_tokens=_as_int(meta.get("output_tokens")),
            latency_ms=_as_int(meta.get("latency_ms")),
            model=request.model,
            provider=self._provider_name,
        )

    def remaining(self) -> int:
        """Scripted outcomes still queued."""
        return len(self._script)


def _as_int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def scripted_provider(payloads: Sequence[dict[str, object]]) -> DeterministicProvider:
    """Build a provider pre-loaded with JSON responses in order."""
    provider = DeterministicProvider()
    for payload in payloads:
        provider.push_json(payload)
    return provider

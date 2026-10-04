"""
Optional LangChain-backed provider adapters (AGENTS.md provider assignments).

Constructed only with an explicit API key; never instantiated in tests.
Lazy SDK imports keep provider packages out of the agent import graph —
agents see the LLMProvider protocol only.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import SecretStr

from casefile.agents.providers import (
    LLMMessage,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    ProviderError,
)


class LangChainProviderAdapter(LLMProvider):
    """Optional real adapter for OpenAI / Anthropic via LangChain.

    Requires an explicit api_key; performs real network I/O, so it is
    excluded from unit tests by construction (no key ⇒ no instance).
    """

    def __init__(
        self,
        provider: Literal["openai", "anthropic"],
        model: str,
        api_key: str,
        temperature: float = 0.0,
    ) -> None:
        if not api_key.strip():
            raise ValueError("LangChainProviderAdapter requires a non-empty api_key")
        self._provider = provider
        self._model = model
        self._api_key = api_key
        self._temperature = temperature

    @property
    def name(self) -> str:
        """Stable provider identifier."""
        return self._provider

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Call the provider chat model and return the neutral response."""
        started = time.perf_counter()
        try:
            text, usage = self._invoke(request)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"{self._provider} call failed: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)
        return LLMResponse(
            request_id=request.request_id,
            content=text,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            latency_ms=latency_ms,
            model=request.model or self._model,
            provider=self._provider,
        )

    def _invoke(self, request: LLMRequest) -> tuple[str, dict[str, int]]:
        if self._provider == "openai":
            return self._invoke_openai(request)
        return self._invoke_anthropic(request)

    def _invoke_openai(self, request: LLMRequest) -> tuple[str, dict[str, int]]:
        from langchain_openai import ChatOpenAI

        chat = ChatOpenAI(
            model=self._model,
            api_key=SecretStr(self._api_key),
            temperature=self._temperature,
        )
        return self._run_chat(chat, request)

    def _invoke_anthropic(self, request: LLMRequest) -> tuple[str, dict[str, int]]:
        from langchain_anthropic import ChatAnthropic

        chat = ChatAnthropic(
            model_name=self._model,
            api_key=SecretStr(self._api_key),
            temperature=self._temperature,
            timeout=60.0,
            stop=None,
        )
        return self._run_chat(chat, request)

    def _run_chat(self, chat: Any, request: LLMRequest) -> tuple[str, dict[str, int]]:
        # Any is necessary here: the two foreign SDK chat classes expose
        # incompatible static signatures for invoke(); all access below is
        # validated at runtime and covered by adapter-level error mapping.
        prompt = "\n\n".join(_format_message(message) for message in request.messages)
        try:
            result = chat.invoke(prompt)
        except Exception as exc:
            message = str(exc).lower()
            if "timeout" in message or "timed out" in message:
                from casefile.agents.providers import ProviderTimeoutError

                raise ProviderTimeoutError(str(exc)) from exc
            raise ProviderError(str(exc)) from exc
        usage_raw = result.response_metadata.get("token_usage", {}) or {}
        usage = {
            "input_tokens": int(usage_raw.get("input_tokens", 0) or 0),
            "output_tokens": int(usage_raw.get("output_tokens", 0) or 0),
        }
        text = result.content if isinstance(result.content, str) else str(result.content)
        return text, usage


def _format_message(message: LLMMessage) -> str:
    label = "System instructions" if message.role == "system" else "Task input"
    return f"{label}:\n{message.content}"

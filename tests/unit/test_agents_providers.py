"""
Unit Tests: Provider abstraction and deterministic test provider.

No API keys, no network: every case runs against scripted outcomes.
"""

import pytest

from casefile.agents.deterministic import DeterministicProvider, scripted_provider
from casefile.agents.providers import (
    LLMRequest,
    ProviderError,
)


def _request() -> LLMRequest:
    from casefile.agents.providers import LLMMessage

    return LLMRequest(
        model="test-model",
        messages=(LLMMessage(role="user", content="hello"),),
    )


@pytest.mark.unit
class TestDeterministicProvider:
    def test_successful_json_response(self) -> None:
        provider = DeterministicProvider()
        provider.push_json({"a": 1}, input_tokens=10, output_tokens=5, latency_ms=3)
        response = provider.complete(_request())
        assert response.content == '{"a": 1}'
        assert response.input_tokens == 10
        assert response.output_tokens == 5
        assert response.latency_ms == 3
        assert response.provider == "deterministic"
        assert len(provider.requests) == 1
        assert provider.remaining() == 0

    def test_malformed_text_response(self) -> None:
        provider = DeterministicProvider()
        provider.push_text("not json at all {{{")
        response = provider.complete(_request())
        assert response.content == "not json at all {{{"

    def test_provider_error(self) -> None:
        provider = DeterministicProvider()
        provider.push_error(ProviderError("boom", retryable=False))
        with pytest.raises(ProviderError, match="boom"):
            provider.complete(_request())

    def test_timeout(self) -> None:
        from casefile.agents.providers import ProviderTimeoutError

        provider = DeterministicProvider()
        provider.push_timeout()
        with pytest.raises(ProviderTimeoutError):
            provider.complete(_request())

    def test_empty_queue_is_explicit(self) -> None:
        with pytest.raises(ProviderError, match="no scripted outcome"):
            DeterministicProvider().complete(_request())

    def test_outcomes_replay_in_order(self) -> None:
        provider = scripted_provider([{"n": 1}, {"n": 2}])
        first = provider.complete(_request())
        second = provider.complete(_request())
        assert '"n": 1' in first.content
        assert '"n": 2' in second.content

    def test_no_network_usage(self) -> None:
        import socket
        from unittest import mock

        provider = scripted_provider([{"ok": True}])
        with mock.patch.object(socket.socket, "connect", autospec=True) as connect_mock:
            connect_mock.side_effect = AssertionError("network must not be used")
            provider.complete(_request())
        connect_mock.assert_not_called()

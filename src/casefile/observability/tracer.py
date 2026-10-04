"""
OTel tracer wrapper with graceful degradation (Phase 10 §4–§9, §17).

All SDK interaction is guarded: exporter/endpoint failures, missing
packages, and sampling decisions can never raise into business logic.
Tests use the SDK InMemorySpanExporter; production uses OTLP when the
collector is reachable, else silent NoOp spans.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, nullcontext, suppress
from typing import Any, Protocol

from casefile.observability.context import TraceContext
from casefile.observability.sanitize import sanitize_attributes


class SpanHandle(Protocol):
    """Minimal span surface used by instrumented code."""

    def set_attribute(self, key: str, value: object) -> None: ...
    def add_event(self, name: str, attributes: dict[str, object] | None = None) -> None: ...
    def record_exception(self, error: BaseException) -> None: ...
    def set_status_error(self, description: str = "") -> None: ...


class _SdkSpan:
    """Adapter presenting the stable SpanHandle surface over an SDK span."""

    def __init__(self, span: Any) -> None:
        self._span = span

    def set_attribute(self, key: str, value: object) -> None:
        cleaned = sanitize_attributes({key: value})
        if key in cleaned:
            self._span.set_attribute(key, cleaned[key])

    def add_event(self, name: str, attributes: dict[str, object] | None = None) -> None:
        self._span.add_event(name, sanitize_attributes(attributes or {}))

    def record_exception(self, error: BaseException) -> None:
        with suppress(Exception):
            self._span.record_exception(error)

    def set_status_error(self, description: str = "") -> None:
        with suppress(Exception):
            from opentelemetry.trace import Status, StatusCode

            self._span.set_status(Status(StatusCode.ERROR, description))


class _NullSpan:
    """No-op span: safe when tracing is disabled or degraded."""

    def set_attribute(self, key: str, value: object) -> None:
        _ = (key, value)

    def add_event(self, name: str, attributes: dict[str, object] | None = None) -> None:
        _ = (name, attributes)

    def record_exception(self, error: BaseException) -> None:
        _ = error

    def set_status_error(self, description: str = "") -> None:
        _ = description


class OtelTracer:
    """Guarded tracer: real spans when configured, null spans otherwise."""

    def __init__(
        self,
        tracer: Any | None = None,
        *,
        enabled: bool = True,
        service_name: str = "casefile",
    ) -> None:
        self._tracer = tracer if enabled else None
        self._enabled = enabled
        self._service_name = service_name

    @property
    def enabled(self) -> bool:
        """Whether real spans are produced."""
        return self._enabled and self._tracer is not None

    @contextmanager
    def span(
        self,
        name: str,
        attributes: dict[str, object] | None = None,
        context: TraceContext | None = None,
    ) -> Iterator[SpanHandle]:
        """Open a child span; setup failures degrade to a null span.

        Once the SDK span is open, exceptions from the caller's block
        propagate (so `with` blocks behave normally) while the SDK records
        them on the span. Catching after ``yield`` would re-enter the
        contextmanager and raise ``generator didn't stop after throw()``.
        """
        if not self.enabled or self._tracer is None:
            yield _NullSpan()
            return
        try:
            merged: dict[str, object] = {}
            if context is not None:
                merged.update(context.attribute_dict())
            if attributes:
                merged.update(attributes)
            clean = sanitize_attributes(merged)
            span_cm = self._tracer.start_as_current_span(name, attributes=clean)
        except Exception:
            yield _NullSpan()
            return
        with span_cm as span:
            yield _SdkSpan(span)


def maybe_span(
    tracer: OtelTracer | None,
    name: str,
    attributes: dict[str, object] | None = None,
    context: TraceContext | None = None,
) -> Any:
    """Span context manager that tolerates a missing/disabled tracer."""
    if tracer is None:
        return nullcontext(_NullSpan())
    return tracer.span(name, attributes, context)

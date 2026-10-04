"""
Observability provider: SDK wiring from configuration (Phase 10 §17–§18).

Builds TracerProvider + MeterProvider with OTLP export when the
collector is configured and reachable; degrades to silent NoOp
otherwise. Shutdown is best-effort. Tests bypass the network entirely
(InMemorySpanExporter, in-memory meters).
"""

from __future__ import annotations

from typing import Any

from casefile.config import ObservabilityConfig
from casefile.observability.meters import CasefileMeters
from casefile.observability.tracer import OtelTracer


class Observability:
    """Configured tracer + meters handle with graceful shutdown."""

    def __init__(
        self,
        tracer: OtelTracer | None = None,
        meters: CasefileMeters | None = None,
        *,
        enabled: bool = True,
        service_name: str = "casefile",
    ) -> None:
        self.tracer = tracer or OtelTracer(enabled=False)
        self.meters = meters or CasefileMeters()
        self.enabled = enabled
        self.service_name = service_name
        self._shutdown_callbacks: list[object] = []

    def on_shutdown(self, callback: object) -> None:
        """Register a best-effort shutdown callback."""
        self._shutdown_callbacks.append(callback)

    def shutdown(self) -> None:
        """Best-effort shutdown; failures are swallowed by design."""
        for callback in self._shutdown_callbacks:
            try:
                if callable(callback):
                    callback()
                    continue
                provider_shutdown = getattr(callback, "shutdown", None)
                if provider_shutdown is not None:
                    provider_shutdown()
            except Exception:
                pass

    @staticmethod
    def disabled(service_name: str = "casefile") -> Observability:
        """Fully local observability: no export, in-memory meters only."""
        return Observability(
            tracer=OtelTracer(enabled=False),
            meters=CasefileMeters(),
            enabled=False,
            service_name=service_name,
        )


def _otlp_endpoint(config: ObservabilityConfig) -> str:
    endpoint = getattr(config.tracing, "otlp_endpoint", "") or ""
    if endpoint.strip():
        return endpoint.strip()
    return "http://localhost:4318"


def configure_observability(config: ObservabilityConfig) -> Observability:
    """Build tracing + metrics from app config, degrading gracefully.

    Export is attempted only when enabled in config; any failure
    (missing package, unreachable collector) yields silent NoOp
    telemetry. Business logic must never depend on the result.
    """
    service_name = config.service_name or "casefile"
    tracing_enabled = bool(config.tracing.enabled)
    metrics_enabled = bool(config.metrics.enabled)
    if not tracing_enabled and not metrics_enabled:
        return Observability.disabled(service_name=service_name)

    tracer: OtelTracer | None = None
    meters: CasefileMeters | None = None
    shutdown_callbacks: list[Any] = []
    try:
        from opentelemetry import trace as trace_api
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)
        if tracing_enabled:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )

                exporter = OTLPSpanExporter(endpoint=_otlp_endpoint(config))
                provider.add_span_processor(BatchSpanProcessor(exporter))
            except Exception:
                pass
        trace_api.set_tracer_provider(provider)
        sdk_tracer = provider.get_tracer(service_name)
        tracer = OtelTracer(tracer=sdk_tracer, enabled=tracing_enabled, service_name=service_name)
        shutdown_callbacks.append(provider)
    except Exception:
        tracer = OtelTracer(enabled=False)
    try:
        from opentelemetry import metrics as metrics_api
        from opentelemetry.sdk.metrics import MeterProvider

        meter_provider = MeterProvider()
        metrics_api.set_meter_provider(meter_provider)
        sdk_meter = meter_provider.get_meter(service_name)
        meters = CasefileMeters(meter=sdk_meter if metrics_enabled else None)
        shutdown_callbacks.append(meter_provider)
    except Exception:
        meters = CasefileMeters()
    observability = Observability(
        tracer=tracer,
        meters=meters,
        enabled=bool(tracing_enabled or metrics_enabled),
        service_name=service_name,
    )
    for callback in shutdown_callbacks:
        observability.on_shutdown(callback)
    return observability


def test_observability(
    service_name: str = "casefile-test",
) -> tuple[Observability, Any]:
    """In-memory observability for tests: span exporter + local meters."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    sdk_tracer = provider.get_tracer(service_name)
    observability = Observability(
        tracer=OtelTracer(tracer=sdk_tracer, enabled=True, service_name=service_name),
        meters=CasefileMeters(),
        enabled=True,
        service_name=service_name,
    )
    observability.on_shutdown(provider)
    return observability, exporter

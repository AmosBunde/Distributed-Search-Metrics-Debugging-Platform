"""OpenTelemetry bootstrap, identical for every service (see ADR-0004).

Trace context crosses HTTP automatically once a service is instrumented, but it
does *not* cross Kafka on its own. The inject/extract helpers here are what keep
a trace intact from the collector, through the broker, into the metrics engine —
so they live in one place rather than being re-implemented per service.
"""

from __future__ import annotations

from typing import Any

from opentelemetry import propagate, trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

from .settings import Settings

KafkaHeaders = list[tuple[str, bytes]]


def configure_tracing(service: str, settings: Settings) -> trace.Tracer:
    """Install a tracer provider exporting OTLP, and return this service's tracer.

    With `otel_sdk_disabled` set — the default in unit tests — no exporter is
    installed and spans become cheap no-ops, so nothing tries to reach a
    collector that is not running.
    """
    if settings.otel_sdk_disabled:
        return trace.get_tracer(service)

    resource = Resource.create(
        {
            "service.name": service,
            "service.namespace": "search-metrics",
            "deployment.environment": settings.environment,
        }
    )
    provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(root=TraceIdRatioBased(settings.otel_traces_sampler_arg)),
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint))
    )
    trace.set_tracer_provider(provider)
    return trace.get_tracer(service)


def instrument_fastapi(app: Any) -> None:
    """Instrument a FastAPI app if the optional dependency is installed."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except ImportError:  # pragma: no cover - optional extra
        return
    FastAPIInstrumentor.instrument_app(app, excluded_urls="/health,/metrics")


def inject_trace_context(headers: KafkaHeaders | None = None) -> KafkaHeaders:
    """Return Kafka headers carrying the active trace context."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    injected = [(key, value.encode("utf-8")) for key, value in carrier.items()]
    return [*(headers or []), *injected]


def extract_trace_context(headers: KafkaHeaders | None) -> Any:
    """Rebuild the producing service's trace context from Kafka headers."""
    carrier = {
        key: value.decode("utf-8", errors="replace")
        for key, value in (headers or [])
        if value is not None
    }
    return propagate.extract(carrier)


def current_trace_id() -> str | None:
    """Hex trace id of the active span, or None outside a trace."""
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None

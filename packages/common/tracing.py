"""OpenTelemetry tracing setup — exports spans to Jaeger for distributed tracing.

Architecture:
  - Every incoming HTTP request gets a trace span.
  - Outgoing HTTP calls (LLM, embedding, reranker) propagate trace context via
    W3C TraceContext headers.
  - Spans are exported to the OTLP collector (Jaeger) over gRPC.
  - Trace IDs are injected into structlog so every log line is correlated.

Usage:
    from packages.common.tracing import setup_tracing
    setup_tracing(service_name="aegisrag-api")

Environment:
  OTEL_EXPORTER_OTLP_ENDPOINT  – Jaeger OTLP endpoint (default: http://localhost:4317)
  OTEL_SERVICE_NAME            – service name in traces
"""

from __future__ import annotations

import os

OTEL_AVAILABLE = True
try:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.trace.sampling import ALWAYS_ON
except ImportError:  # pragma: no cover – OTEL is optional; graceful degradation
    OTEL_AVAILABLE = False


def setup_tracing(
    *,
    service_name: str = "aegisrag-api",
    endpoint: str | None = None,
) -> bool:
    """Initialise OpenTelemetry tracing. Returns True on success."""
    if not OTEL_AVAILABLE:
        return False

    otlp_endpoint = endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

    resource = Resource(attributes={SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource, sampler=ALWAYS_ON)

    try:
        exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
    except Exception:
        # Exporter unreachable — traces are dropped, service continues.
        return False

    trace.set_tracer_provider(provider)
    return True


def instrument_app(app, *, service_name: str = "aegisrag-api") -> None:
    """Attach OpenTelemetry auto-instrumentation to a FastAPI app.

    Must be called AFTER setup_tracing().
    """
    if not OTEL_AVAILABLE:
        return
    try:
        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=trace.get_tracer_provider(),
            server_request_hook=_server_request_hook,
        )
        HTTPXClientInstrumentor().instrument()
        RedisInstrumentor().instrument()
        SQLAlchemyInstrumentor().instrument()
    except Exception:
        pass  # Instrumentation is best-effort


def _server_request_hook(span, scope: dict) -> None:  # noqa: ARG001
    """Attach route pattern as span name for cleaner traces."""
    if scope.get("route"):
        span.update_name(f"{scope['method']} {scope['route'].path}")

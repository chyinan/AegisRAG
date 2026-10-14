"""Isolate optional framework callbacks while retaining application context.

Do not copy the caller's entire Context: framework tracers, collectors and
Runnable configuration can capture private prompts independently of env flags.
Only the application's explicit RLS and observability contexts are propagated.
"""

from __future__ import annotations

from contextvars import Context

from opentelemetry import context as otel_context
from structlog.contextvars import bind_contextvars, get_contextvars

from packages.common.tenant_context import current_tenant_id, set_tenant_id


def isolated_framework_context() -> Context:
    context = Context()
    tenant_id = current_tenant_id()
    if tenant_id is not None:
        context.run(set_tenant_id, tenant_id)
    context.run(otel_context.attach, otel_context.get_current())
    log_context = get_contextvars()
    correlation = {
        key: log_context[key]
        for key in ("request_id", "trace_id", "session_id")
        if key in log_context
    }
    context.run(bind_contextvars, **correlation)
    return context

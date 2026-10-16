"""Privacy boundary for request-scoped, outward-facing framework adapters.

This module does not import optional frameworks until an adapter is invoked. It
never changes process-wide tracing/debug settings. Caller-owned chains may still
observe their own inputs and outputs; their tracing policy belongs to the caller.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable
from contextvars import Context
from typing import Never, TypeVar

from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError
from packages.common.framework_context import isolated_framework_context
from packages.common.tenant_context import current_tenant_id, tenant_scope

T = TypeVar("T")


class FrameworkExportError(DomainError):
    """Safe configuration failure before invoking an application service."""

    def __init__(self, *, code: str, message: str) -> None:
        super().__init__(code=code, message=message, status_code=400)


def export_context(context: AuthenticatedRequestContext) -> Context:
    """Capture correlation state, rejecting a conflicting active tenant scope."""
    tenant = current_tenant_id()
    if tenant is not None and tenant != context.auth.tenant_id:
        raise FrameworkExportError(
            code="FRAMEWORK_EXPORT_TENANT_MISMATCH",
            message="Framework export does not match the active tenant scope.",
        )
    return isolated_framework_context()


async def call_export(
    operation: Callable[[], Awaitable[T]],
    *,
    request_context: AuthenticatedRequestContext,
    framework_context: Context | None = None,
    cleanup: bool = False,
) -> T:
    """Keep service exceptions/cancellation intact and exclude ambient callbacks."""
    from langchain_core.globals import get_debug
    from langsmith import tracing_context

    execution_context = (
        framework_context if framework_context is not None else export_context(request_context)
    )

    async def execute() -> T:
        if get_debug() and not cleanup:
            raise FrameworkExportError(
                code="FRAMEWORK_EXPORT_DEBUG_DISABLED",
                message="Framework debug logging must be disabled for controlled exports.",
            )
        with tenant_scope(request_context.auth.tenant_id), tracing_context(enabled=False):
            return await operation()

    return await asyncio.create_task(execute(), context=execution_context)


async def stream_export(
    iterator_factory: Callable[[], AsyncIterator[T]],
    *,
    request_context: AuthenticatedRequestContext,
) -> AsyncGenerator[T, None]:
    """Keep open/next/close in one isolated Task with strict pull backpressure.

    A generator may hold task-affine resources such as asyncio.timeout across a
    yield. A fresh Task for each anext breaks those resources even if the Context
    is reused. The producer therefore lives until completion or consumer close,
    and advances exactly once per consumer request, never prefetching a terminal
    success or triggering additional provider work while a consumer is paused.
    """
    from langchain_core.globals import get_debug
    from langsmith import tracing_context

    context = export_context(request_context)
    demand = asyncio.Event()
    available = asyncio.Event()
    values: asyncio.Queue[T] = asyncio.Queue(maxsize=1)

    async def produce() -> None:
        iterator: AsyncIterator[T] | None = None
        failed = False
        with tenant_scope(request_context.auth.tenant_id), tracing_context(enabled=False):
            try:
                while True:
                    await demand.wait()
                    demand.clear()
                    if get_debug():
                        raise FrameworkExportError(
                            code="FRAMEWORK_EXPORT_DEBUG_DISABLED",
                            message=(
                                "Framework debug logging must be disabled for controlled exports."
                            ),
                        )
                    if iterator is None:
                        iterator = iterator_factory().__aiter__()
                    try:
                        item = await anext(iterator)
                    except StopAsyncIteration:
                        return
                    values.put_nowait(item)
                    available.set()
            except BaseException:
                failed = True
                raise
            finally:
                try:
                    close = getattr(iterator, "aclose", None)
                    if close is not None:
                        try:
                            async with asyncio.timeout(1.0):
                                await close()
                        except Exception:
                            # Never replace source errors or cancellation with cleanup failures.
                            if not failed:
                                raise
                finally:
                    available.set()

    producer = asyncio.create_task(produce(), context=context)
    failed = False
    cancelled_producer = False
    try:
        while True:
            demand.set()
            while values.empty() and not producer.done():
                await available.wait()
                available.clear()
            if values.empty():
                # Preserve the original application exception, including timeout/cancellation.
                await producer
                return
            yield values.get_nowait()
    except BaseException:
        failed = True
        raise
    finally:
        if not producer.done():
            cancelled_producer = True
            producer.cancel()
        try:
            async with asyncio.timeout(1.0):
                await producer
        except asyncio.CancelledError:
            if not cancelled_producer and not failed:
                raise
        except Exception:
            if not failed:
                raise


def async_only() -> Never:
    raise NotImplementedError(
        "Controlled AegisRAG exports are async-only; use ainvoke, abatch, or astream."
    )

"""Application-lifespan resources, never request/session or user state.

A ContextVar scopes pools to one application and event loop. CLI/RQ jobs that
own another loop keep their existing per-operation resource ownership.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Protocol, TypeVar, cast


class AsyncCloseable(Protocol):
    async def aclose(self) -> None: ...


T = TypeVar("T", bound=AsyncCloseable)


class ApplicationResources:
    def __init__(self) -> None:
        self._items: dict[tuple[object, ...], AsyncCloseable] = {}
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    def get_or_create(self, key: tuple[object, ...], build: Callable[[], T]) -> T:
        if self._closed:
            raise RuntimeError("Application resources are closed")
        if key not in self._items:
            self._items[key] = build()
        return cast(T, self._items[key])

    async def aclose(self) -> None:
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close_resources())
            self._close_task.add_done_callback(_consume_close_result)
        # Shutdown remains owned if one caller is cancelled during cleanup.
        await asyncio.shield(self._close_task)

    async def _close_resources(self) -> None:
        items, self._items = tuple(self._items.values()), {}
        # Close all pools even if an earlier resource fails cleanup.
        errors: list[Exception] = []
        for item in reversed(items):
            try:
                await item.aclose()
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ExceptionGroup("Application resource cleanup failed", errors)


_current: ContextVar[ApplicationResources | None] = ContextVar(
    "application_resources", default=None,
)


def current_resources() -> ApplicationResources | None:
    return _current.get()


@asynccontextmanager
async def application_resources() -> AsyncIterator[ApplicationResources]:
    resources = ApplicationResources()
    token = _current.set(resources)
    try:
        yield resources
    finally:
        try:
            await resources.aclose()
        finally:
            _current.reset(token)


@contextmanager
def resource_scope(resources: ApplicationResources | None) -> Iterator[None]:
    token = _current.set(resources)
    try:
        yield
    finally:
        _current.reset(token)


def _consume_close_result(task: asyncio.Task[None]) -> None:
    if not task.cancelled():
        task.exception()

"""Explicit iterator ownership across stream-formatting wrappers."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import TypeVar

T = TypeVar("T")


@asynccontextmanager
async def closing_async_iterator(iterator: AsyncIterator[T]) -> AsyncIterator[AsyncIterator[T]]:
    try:
        yield iterator
    finally:
        close = getattr(iterator, "aclose", None)
        if callable(close):
            # Cleanup cannot replace an existing terminal/domain error.
            # Cancellation still propagates; provider transports own cleanup.
            with suppress(Exception):
                await close()

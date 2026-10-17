"""Bound local inference by unfinished work, including abandoned threads."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import CancelledError as FutureCancelledError
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import copy_context
from threading import Event, Lock
from typing import Any, TypeVar

_T = TypeVar("_T")


class InferenceCapacityError(RuntimeError):
    """Inference is closing or its bounded admission queue is full."""


class BoundedInferenceExecutor:
    """A request cancellation cannot release capacity held by a running thread.

    The underlying executor has an unbounded queue, so admission is bounded
    *before* submission. Pending futures own their capacity until they actually
    finish. An abandoned queued job retains its budget until a worker actually
    removes it from the executor queue, then skips the user's operation.
    """

    def __init__(self, *, max_concurrency: int = 1, max_queue_size: int = 32) -> None:
        if (
            isinstance(max_concurrency, bool)
            or not isinstance(max_concurrency, int)
            or max_concurrency < 1
        ):
            raise ValueError("max_concurrency must be positive")
        if (
            isinstance(max_queue_size, bool)
            or not isinstance(max_queue_size, int)
            or max_queue_size < 0
        ):
            raise ValueError("max_queue_size must not be negative")
        self._capacity = max_concurrency + max_queue_size
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency, thread_name_prefix="bge_rerank"
        )
        self._pending: set[Future[Any]] = set()
        self._lock = Lock()
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    async def run(self, operation: Callable[[], _T]) -> _T:
        context = copy_context()
        abandoned = Event()

        def execute() -> _T:
            if abandoned.is_set():
                raise FutureCancelledError
            return context.run(operation)

        with self._lock:
            if self._closed:
                raise InferenceCapacityError("Local inference is closing")
            if len(self._pending) >= self._capacity:
                raise InferenceCapacityError("Local inference queue is full")
            future = self._executor.submit(execute)
            self._pending.add(future)
        # This may execute immediately for a completed future: never register
        # the callback while holding the non-reentrant pending-work lock.
        future.add_done_callback(self._finished)
        wrapped = asyncio.wrap_future(future)
        wrapped.add_done_callback(_consume_result)
        try:
            return await asyncio.shield(wrapped)
        except asyncio.CancelledError:
            # Future.cancel() leaves its WorkItem in ThreadPoolExecutor's
            # unbounded queue. Releasing admission at that point lets a cancel
            # storm retain unbounded contexts/documents. Instead, the worker
            # consumes and skips abandoned work before its budget is released.
            # A running Python thread cannot be forcibly stopped either.
            abandoned.set()
            raise

    def _finished(self, future: Future[Any]) -> None:
        with self._lock:
            self._pending.discard(future)

    async def aclose(self) -> None:
        with self._lock:
            self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(
                asyncio.to_thread(self._executor.shutdown, wait=True, cancel_futures=True)
            )
            self._close_task.add_done_callback(_consume_result)
        # Closing and joining the executor must never block the event loop.
        # Cancellation of one closer does not abandon the owned shutdown.
        await asyncio.shield(self._close_task)


def _consume_result(future: asyncio.Future[Any]) -> None:
    if not future.cancelled():
        future.exception()

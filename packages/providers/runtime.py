"""Per-event-loop HTTP resources and bounded admission for native providers.

The budget is process-local, not a distributed rate limit or tenant scheduler.
A logical call owns one slot throughout its retries so backoff cannot become an
unbounded second queue. Injected HTTP clients always retain caller ownership.
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from collections import deque
from collections.abc import Awaitable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import TypeVar

import httpx

T = TypeVar("T")
logger = logging.getLogger(__name__)


class ProviderCapacityError(Exception):
    """The finite admission queue is full or its wait budget expired."""


class ProviderRuntimeClosedError(Exception):
    """The resource owner has shut down this runtime."""


def positive_seconds(value: float, name: str) -> float:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


class ProviderDeadline:
    def __init__(self, timeout_seconds: float) -> None:
        self.expires_at = time.monotonic() + positive_seconds(timeout_seconds, "total timeout")

    def remaining(self) -> float:
        remaining = self.expires_at - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Provider deadline exceeded")
        return remaining

    async def wait(self, operation: Awaitable[T]) -> T:
        # wait_for must own the operation even when the deadline has just passed.
        # Closing a newly created coroutine prevents an unawaited-coroutine leak.
        try:
            remaining = self.remaining()
        except TimeoutError:
            if asyncio.iscoroutine(operation):
                operation.close()
            raise
        return await asyncio.wait_for(operation, timeout=remaining)


class ProviderLease:
    def __init__(self, runtime: ProviderRuntime) -> None:
        self._runtime = runtime
        self._released = False

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._runtime._release()


class ProviderRuntime:
    def __init__(
        self,
        *,
        max_in_flight: int = 8,
        max_waiters: int = 32,
        queue_timeout_seconds: float = 1.0,
        request_timeout_seconds: float | None = None,
        retry_backoff_seconds: float = 0.1,
        retry_max_backoff_seconds: float = 2.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if type(max_in_flight) is not int or max_in_flight < 1:
            raise ValueError("max_in_flight must be positive")
        if type(max_waiters) is not int or max_waiters < 0:
            raise ValueError("max_waiters must be nonnegative")
        self.max_in_flight = max_in_flight
        self.max_waiters = max_waiters
        self.queue_timeout_seconds = positive_seconds(queue_timeout_seconds, "queue timeout")
        self.request_timeout_seconds = (
            positive_seconds(request_timeout_seconds, "request timeout")
            if request_timeout_seconds is not None
            else None
        )
        if not math.isfinite(retry_backoff_seconds) or retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must be finite and nonnegative")
        self.retry_backoff_seconds = retry_backoff_seconds
        self.retry_max_backoff_seconds = positive_seconds(
            retry_max_backoff_seconds, "retry_max_backoff_seconds"
        )
        if retry_backoff_seconds > retry_max_backoff_seconds:
            raise ValueError("retry_backoff_seconds must not exceed retry_max_backoff_seconds")
        self._client = client
        self._owns_client = client is None
        self._closed = False
        self._in_flight = 0
        self._waiters: deque[asyncio.Future[ProviderLease]] = deque()
        self._streams: set[ProviderStreamGuard] = set()
        self._client_closes: set[asyncio.Task[None]] = set()
        self._close_task: asyncio.Task[None] | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        self.ensure_open()
        if self._client is None:
            self._client = httpx.AsyncClient(
                limits=httpx.Limits(
                    max_connections=self.max_in_flight,
                    max_keepalive_connections=self.max_in_flight,
                )
            )
        return self._client

    def ensure_open(self) -> None:
        if self._closed:
            raise ProviderRuntimeClosedError

    @property
    def in_flight(self) -> int:
        return self._in_flight

    @property
    def waiting(self) -> int:
        return len(self._waiters)

    def deadline(
        self, *, timeout_seconds: float, retry_budget: int, total_timeout_seconds: float | None
    ) -> ProviderDeadline:
        # Existing DTO timeout remains per HTTP attempt. Without an explicit
        # total budget retain room for all original attempts plus bounded waits.
        total = total_timeout_seconds
        if total is None:
            total = self.request_timeout_seconds
        if total is None:
            total = (
                positive_seconds(timeout_seconds, "HTTP timeout") * (retry_budget + 1)
                + self.queue_timeout_seconds
                + self.retry_max_backoff_seconds * retry_budget
            )
        return ProviderDeadline(total)

    async def acquire(self, deadline: ProviderDeadline) -> ProviderLease:
        if self._closed:
            raise ProviderRuntimeClosedError
        deadline.remaining()
        if self._in_flight < self.max_in_flight:
            self._in_flight += 1
            return ProviderLease(self)
        if len(self._waiters) >= self.max_waiters:
            raise ProviderCapacityError("Provider admission queue is full")
        future: asyncio.Future[ProviderLease] = asyncio.get_running_loop().create_future()
        self._waiters.append(future)
        try:
            lease = await asyncio.wait_for(
                asyncio.shield(future),
                timeout=min(self.queue_timeout_seconds, deadline.remaining()),
            )
            self.ensure_open()
            deadline.remaining()
            return lease
        except TimeoutError as exc:
            self._cancel_waiter(future)
            # A total deadline is different from local queue backpressure.
            deadline.remaining()
            raise ProviderCapacityError("Provider admission wait timed out") from exc
        except BaseException:
            self._cancel_waiter(future)
            raise

    def _cancel_waiter(self, future: asyncio.Future[ProviderLease]) -> None:
        if future.done():
            if not future.cancelled() and future.exception() is None:
                # Handles cancellation racing with a direct slot hand-off.
                future.result().release()
        else:
            future.cancel()
        try:
            self._waiters.remove(future)
        except ValueError:
            pass

    def _release(self) -> None:
        while self._waiters and not self._closed:
            future = self._waiters.popleft()
            if not future.done():
                future.set_result(ProviderLease(self))
                return
        self._in_flight -= 1

    async def backoff(
        self, *, attempt: int, deadline: ProviderDeadline, response: httpx.Response | None
    ) -> None:
        self.ensure_open()
        # Full jitter, bounded exponential growth; Retry-After is a minimum and
        # is never shortened to the local backoff cap. The total deadline wins.
        cap = self.retry_backoff_seconds
        if cap:
            cap *= 2 ** min(attempt, 30)
        delay = random.uniform(0.0, min(cap, self.retry_max_backoff_seconds))
        retry_after = _retry_after(response)
        if retry_after is not None:
            delay = max(delay, retry_after)
        await deadline.wait(asyncio.sleep(delay))
        self.ensure_open()

    def guard_stream(
        self,
        *,
        response: httpx.Response,
        lease: ProviderLease,
        deadline: ProviderDeadline,
        owned_client: httpx.AsyncClient | None = None,
    ) -> ProviderStreamGuard:
        guard = ProviderStreamGuard(
            runtime=self,
            response=response,
            lease=lease,
            deadline=deadline,
            owned_client=owned_client,
        )
        self._streams.add(guard)
        return guard

    async def close_owned_client(self, client: httpx.AsyncClient) -> None:
        """Retain cleanup ownership when a direct caller is repeatedly cancelled."""
        task = asyncio.create_task(client.aclose(), name="provider-client-cleanup")
        self._client_closes.add(task)
        task.add_done_callback(self._client_close_finished)
        await asyncio.shield(task)

    def _client_close_finished(self, task: asyncio.Task[None]) -> None:
        self._client_closes.discard(task)
        _observe_cleanup(task, "provider.client_cleanup")

    async def aclose(self) -> None:
        if self._close_task is None:
            self._closed = True
            while self._waiters:
                future = self._waiters.popleft()
                if not future.done():
                    future.set_exception(ProviderRuntimeClosedError())
            # Shutdown itself has an owner independent of any cancelled caller.
            self._close_task = asyncio.create_task(
                self._shutdown(), name="provider-runtime-cleanup"
            )
            self._close_task.add_done_callback(
                lambda task: _observe_cleanup(task, "provider.runtime_cleanup")
            )
        await asyncio.shield(self._close_task)

    async def _shutdown(self) -> None:
        outcomes = await asyncio.gather(
            *(guard.shutdown() for guard in tuple(self._streams)), return_exceptions=True
        )
        outcomes.extend(await asyncio.gather(*tuple(self._client_closes), return_exceptions=True))
        try:
            if self._owns_client and self._client is not None:
                await self._client.aclose()
        finally:
            for outcome in outcomes:
                if isinstance(outcome, BaseException):
                    raise outcome


class ProviderStreamGuard:
    """Own transport cleanup separately from callers and deadline timers."""

    def __init__(
        self,
        *,
        runtime: ProviderRuntime,
        response: httpx.Response,
        lease: ProviderLease,
        deadline: ProviderDeadline,
        owned_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._runtime = runtime
        self._response = response
        self._lease = lease
        self._deadline = deadline
        self._owned_client = owned_client
        self._release_on_close = False
        self._close_task: asyncio.Task[None] | None = None
        self._timer = asyncio.create_task(self._expire(), name="provider-stream-deadline")

    async def _expire(self) -> None:
        await asyncio.sleep(max(0.0, self._deadline.expires_at - time.monotonic()))
        # Logical admission expires immediately, while a separately owned task
        # retains actual transport cleanup until it has really finished.
        self._release_on_close = True
        self._lease.release()
        self._start_close()

    def _start_close(self) -> asyncio.Task[None]:
        if self._close_task is None:
            if asyncio.current_task() is not self._timer:
                self._timer.cancel()
            self._close_task = asyncio.create_task(
                self._close_resources(), name="provider-stream-cleanup"
            )
            self._close_task.add_done_callback(self._close_finished)
        return self._close_task

    async def _close_resources(self) -> None:
        try:
            await self._response.aclose()
        finally:
            if self._owned_client is not None:
                await self._owned_client.aclose()

    def _close_finished(self, task: asyncio.Task[None]) -> None:
        # HTTPX marks response.is_closed before transport.aclose finishes. Only
        # the owned task's completion is sufficient to stop tracking cleanup.
        if self._release_on_close:
            self._lease.release()
        self._runtime._streams.discard(self)
        _observe_cleanup(task, "provider.stream_cleanup")

    async def aclose(self, *, release: bool = True) -> None:
        if release:
            self._release_on_close = True
            self._lease.release()
        already_closing = self._close_task is not None
        task = self._start_close()
        if not already_closing:
            # A second cancellation (including AnyIO level cancellation) may
            # cancel this wait, but cannot reach the actual transport cleanup.
            await asyncio.shield(task)

    async def shutdown(self) -> None:
        self._release_on_close = True
        self._lease.release()
        await asyncio.shield(self._start_close())


def _observe_cleanup(task: asyncio.Task[None], event: str) -> None:
    if task.cancelled():
        logger.warning("%s_cancelled", event)
    elif task.exception() is not None:
        # No exception text: it may contain prompts, URLs or credentials.
        logger.warning("%s_failed", event)


def _retry_after(response: httpx.Response | None) -> float | None:
    if response is None:
        return None
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        try:
            date = parsedate_to_datetime(raw)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            seconds = (date - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None

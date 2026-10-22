"""Streaming cleanup cannot replace application errors or consumer cancellation."""

# ruff: noqa: E402 - installed optional dependencies must import successfully
from __future__ import annotations

import asyncio
import importlib.metadata
import os
from collections.abc import AsyncIterator
from importlib import import_module
from typing import Any, Literal

import pytest

try:
    importlib.metadata.version("langchain-core")
except importlib.metadata.PackageNotFoundError as exc:
    if os.environ.get("AEGISRAG_REQUIRE_FRAMEWORKS") == "1":
        raise pytest.UsageError(
            "Required framework distribution is absent: langchain-core"
        ) from exc
    pytest.skip(
        "Optional framework distribution is absent: langchain-core", allow_module_level=True
    )

# Fail on a broken installed framework before importing legacy test fixtures.
import_module("langchain_core.runnables")

from packages.common.errors import DomainError
from packages.integrations.langchain_boundary import stream_export
from tests.unit.framework_exports.test_langchain_exports import context


class _FailingCloseIterator(AsyncIterator[int]):
    def __init__(self, ending: Literal["complete", "error", "blocked"]) -> None:
        self.ending = ending
        self.source_error = DomainError(code="SOURCE_FAILED", message="Application failure")
        self.cleanup_error = RuntimeError("cleanup failure")
        self.started = asyncio.Event()
        self.closed = asyncio.Event()
        self.pulls = 0
        self.close_calls = 0
        self.producer: asyncio.Task[Any] | None = None
        self.cleanup_task: asyncio.Task[Any] | None = None

    async def __anext__(self) -> int:
        self.producer = asyncio.current_task()
        self.pulls += 1
        if self.pulls == 1:
            return 1
        if self.ending == "error":
            raise self.source_error
        if self.ending == "blocked":
            self.started.set()
            await asyncio.Event().wait()
            raise AssertionError("Cancelled source must not resume")
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.cleanup_task = asyncio.current_task()
        self.close_calls += 1
        self.closed.set()
        raise self.cleanup_error

    def assert_closed_in_producer(self) -> None:
        assert self.closed.is_set()
        assert self.close_calls == 1
        assert self.producer is not None and self.producer.done()
        assert self.cleanup_task is self.producer


async def test_cleanup_failure_after_normal_exhaustion_remains_observable() -> None:
    source = _FailingCloseIterator("complete")
    exported = stream_export(lambda: source, request_context=context())
    assert await anext(exported) == 1
    with pytest.raises(RuntimeError) as caught:
        await anext(exported)
    assert caught.value is source.cleanup_error
    source.assert_closed_in_producer()
    await exported.aclose()
    assert source.close_calls == 1


async def test_cleanup_failure_preserves_original_application_error_identity() -> None:
    source = _FailingCloseIterator("error")
    exported = stream_export(lambda: source, request_context=context())
    assert await anext(exported) == 1
    with pytest.raises(DomainError) as caught:
        await anext(exported)
    assert caught.value is source.source_error
    source.assert_closed_in_producer()


@pytest.mark.parametrize("cancel_pending_pull", [False, True], ids=["close-paused", "cancel-pull"])
async def test_cleanup_failure_does_not_replace_consumer_close_or_cancellation(
    cancel_pending_pull: bool,
) -> None:
    source = _FailingCloseIterator("blocked")
    exported = stream_export(lambda: source, request_context=context())
    assert await anext(exported) == 1
    if cancel_pending_pull:
        pending = asyncio.create_task(anext(exported))
        await asyncio.wait_for(source.started.wait(), timeout=1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    else:
        await exported.aclose()
    source.assert_closed_in_producer()
    await exported.aclose()
    assert source.close_calls == 1


async def test_valid_async_iterator_without_optional_close_exhausts_normally() -> None:
    class Iterator(AsyncIterator[int]):
        def __init__(self) -> None:
            self.values = iter([1, 2])

        async def __anext__(self) -> int:
            try:
                return next(self.values)
            except StopIteration:
                raise StopAsyncIteration from None

    assert [item async for item in stream_export(Iterator, request_context=context())] == [1, 2]

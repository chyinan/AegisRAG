"""Regression for yielding DONE while the adapter unwinds cancellation/close.

These cases reproduce the original adapter's finally-yield bug independently
of the new bounded audit decorator. They use the legacy in-memory audit port.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import suppress
from typing import cast

import pytest

from packages.common.audit import InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.rag.dto import QueryCommand
from packages.rag.service_token import (
    OpenAIChatCompletionRequest,
    OpenAIChatMessage,
    ServiceTokenChatAdapter,
)
from packages.rag.streaming import RagStreamEvent, token_event
from tests.unit.rag.test_service_token_adapter import StubChatService, _context


class PausedChatService(StubChatService):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def stream_chat(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        session_id: str | None,
    ) -> AsyncIterator[RagStreamEvent]:
        yield token_event(
            request_id=context.request_id, trace_id=context.trace_id, index=0, delta="answer",
        )
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


def _stream(service: StubChatService) -> AsyncGenerator[str, None]:
    adapter = ServiceTokenChatAdapter(
        chat_service=service, model_id="fake", owned_by="local", audit=InMemoryAuditPort(),
    )
    return cast(AsyncGenerator[str, None], adapter.stream_chat_completion(
        context=_context(),
        request=OpenAIChatCompletionRequest(
            model="fake", messages=(OpenAIChatMessage(role="user", content="question"),),
            stream=True,
        ),
    ))


async def test_adapter_close_after_token_does_not_yield_done() -> None:
    stream = _stream(StubChatService())
    assert "answer" in await anext(stream)
    try:
        await stream.aclose()
    finally:
        # Baseline failure must not leave its suspended finally block alive.
        with suppress(BaseException):
            await stream.aclose()


async def test_adapter_upstream_cancellation_propagates_before_done() -> None:
    service = PausedChatService()
    stream = _stream(service)
    assert "answer" in await anext(stream)
    pending = asyncio.create_task(anext(stream))
    try:
        await asyncio.wait_for(service.entered.wait(), timeout=1)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert service.cancelled.is_set()
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        with suppress(BaseException):
            await stream.aclose()


async def test_adapter_close_after_error_chunk_does_not_yield_done() -> None:
    stream = _stream(StubChatService(stream_error=RuntimeError("synthetic failure")))
    assert "OPENWEBUI_STREAM_FAILED" in await anext(stream)
    try:
        await stream.aclose()
    finally:
        with suppress(BaseException):
            await stream.aclose()

from __future__ import annotations

import asyncio
import inspect
import logging
import sys
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from typing import Any, Literal, cast

import httpx
import pytest

from packages.embeddings.adapters.openai_compatible import OpenAICompatibleEmbeddingProvider
from packages.embeddings.dto import EmbeddingRequest
from packages.embeddings.exceptions import EMBEDDING_PROVIDER_TIMEOUT, EmbeddingProviderError
from packages.llm.adapters.openai_compatible import OpenAICompatibleChatProvider
from packages.llm.dto import GenerateChunk, GenerateRequest, LLMMessage
from packages.llm.exceptions import (
    LLM_PROVIDER_RATE_LIMITED,
    LLM_PROVIDER_TIMEOUT,
    LLM_STREAM_FAILED,
    LLMProviderError,
)
from packages.providers.runtime import ProviderDeadline, ProviderRuntime, _retry_after


@dataclass
class _Clock:
    """Advance provider time without changing asyncio's event-loop clock."""

    seconds: float = 0.0

    def monotonic(self) -> float:
        return self.seconds


def _request(*, retries: int = 0) -> GenerateRequest:
    return GenerateRequest(
        messages=(LLMMessage(role="user", content="private prompt"),),
        provider="openai_compatible",
        model="test-model",
        timeout_seconds=1,
        retry_budget=retries,
        request_id="req",
        trace_id="trace",
        tenant_id="tenant",
        user_id="user",
    )


def _chat_provider(
    *, client: httpx.AsyncClient | None = None, runtime: ProviderRuntime | None = None
) -> OpenAICompatibleChatProvider:
    return OpenAICompatibleChatProvider(
        provider="openai_compatible",
        model="test-model",
        base_url="https://models.test/v1",
        api_key="private-key",
        client=client,
        runtime=runtime,
    )


@pytest.mark.asyncio
async def test_expired_deadline_closes_unstarted_coroutine() -> None:
    invoked = False

    async def operation() -> None:
        nonlocal invoked
        invoked = True

    deadline = ProviderDeadline(1)
    deadline.expires_at = 0
    pending = operation()
    with pytest.raises(TimeoutError, match="Provider deadline exceeded"):
        await deadline.wait(pending)
    assert not invoked
    assert inspect.getcoroutinestate(pending) == inspect.CORO_CLOSED


@pytest.mark.asyncio
async def test_explicit_guard_close_releases_once_and_preserves_borrowed_client() -> None:
    transport = httpx.MockTransport(lambda _: httpx.Response(200))
    async with httpx.AsyncClient(transport=transport) as client:
        runtime = ProviderRuntime(client=client)
        response = httpx.Response(200)
        lease = await runtime.acquire(ProviderDeadline(1))
        guard = runtime.guard_stream(response=response, lease=lease, deadline=ProviderDeadline(1))
        try:
            await guard.aclose()
            await guard.aclose()
            assert response.is_closed
            assert runtime.in_flight == 0
            assert not runtime._streams
            assert not client.is_closed
        finally:
            await runtime.aclose()
        assert not client.is_closed


@pytest.mark.asyncio
async def test_shutdown_reports_stream_cleanup_failure_and_still_closes_owned_clients(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = RuntimeError("private prompt and private-key")
    original_client = httpx.AsyncClient

    def factory(**kwargs: Any) -> httpx.AsyncClient:
        transport = httpx.MockTransport(lambda _: httpx.Response(200))
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr("packages.providers.runtime.httpx.AsyncClient", factory)

    class FailingStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b""

        async def aclose(self) -> None:
            raise failure

    runtime = ProviderRuntime()
    pooled_client = runtime.client
    stream_client = httpx.AsyncClient()
    lease = await runtime.acquire(ProviderDeadline(1))
    runtime.guard_stream(
        response=httpx.Response(200, stream=FailingStream()),
        lease=lease,
        deadline=ProviderDeadline(1),
        owned_client=stream_client,
    )
    with caplog.at_level(logging.WARNING, logger="packages.providers.runtime"):
        with pytest.raises(RuntimeError) as raised:
            await runtime.aclose()
    assert raised.value is failure
    assert pooled_client.is_closed and stream_client.is_closed
    assert runtime.in_flight == 0 and not runtime._streams
    assert "provider.stream_cleanup_failed" in caplog.messages
    assert "provider.runtime_cleanup_failed" in caplog.messages
    assert "private prompt" not in caplog.text and "private-key" not in caplog.text


@pytest.mark.asyncio
async def test_cancelled_client_cleanup_is_observed_without_logging_secrets(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class CancelledCloseClient(httpx.AsyncClient):
        async def aclose(self) -> None:
            await super().aclose()
            raise asyncio.CancelledError("private-key")

    client = CancelledCloseClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    runtime = ProviderRuntime()
    with caplog.at_level(logging.WARNING, logger="packages.providers.runtime"):
        with pytest.raises(asyncio.CancelledError):
            await runtime.close_owned_client(client)
    assert client.is_closed
    assert not runtime._client_closes
    assert "provider.client_cleanup_cancelled" in caplog.messages
    assert "private-key" not in caplog.text
    await runtime.aclose()


def test_retry_after_date_without_timezone_is_interpreted_as_utc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: tzinfo | None = None) -> FrozenDatetime:
            assert tz is UTC
            return cls(2025, 1, 1, tzinfo=UTC)

    monkeypatch.setattr("packages.providers.runtime.datetime", FrozenDatetime)
    response = httpx.Response(429, headers={"Retry-After": "Wed, 01 Jan 2025 00:00:30 -0000"})
    assert _retry_after(response) == 30.0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["llm", "embedding"])
async def test_deadline_includes_successful_response_parsing(
    kind: Literal["llm", "embedding"], monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _Clock()
    monkeypatch.setattr("packages.providers.runtime.time", clock)
    calls = 0

    class SlowJsonResponse(httpx.Response):
        def json(self, **kwargs: Any) -> Any:
            body = super().json(**kwargs)
            clock.seconds = 2.0
            return body

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = (
            {"choices": [{"message": {"content": "ok"}}]}
            if kind == "llm"
            else {"data": [{"index": 0, "embedding": [0.1, 0.2]}]}
        )
        return SlowJsonResponse(200, json=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        runtime = ProviderRuntime(client=client, request_timeout_seconds=1)
        try:
            with pytest.raises((LLMProviderError, EmbeddingProviderError)) as raised:
                if kind == "llm":
                    chat = _chat_provider(client=client, runtime=runtime)
                    await chat.generate(_request(retries=2))
                else:
                    provider = OpenAICompatibleEmbeddingProvider(
                        provider="openai_compatible",
                        model="test-model",
                        base_url="https://models.test/v1",
                        client=client,
                        runtime=runtime,
                    )
                    await provider.embed_texts(
                        EmbeddingRequest(
                            texts=["private prompt"],
                            provider="openai_compatible",
                            model="test-model",
                            timeout_seconds=1,
                            retry_budget=2,
                        )
                    )
            expected = LLM_PROVIDER_TIMEOUT if kind == "llm" else EMBEDDING_PROVIDER_TIMEOUT
            assert raised.value.code == expected
            assert isinstance(raised.value.__cause__, TimeoutError)
            assert calls == 1 and runtime.in_flight == 0
            assert not client.is_closed
        finally:
            await runtime.aclose()


@pytest.mark.asyncio
async def test_stream_eof_and_finish_only_event_produce_one_final_chunk() -> None:
    response = httpx.Response(
        200, content=b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
        runtime = ProviderRuntime(client=client)
        try:
            chunks = [chunk async for chunk in _chat_provider(runtime=runtime).stream(_request())]
            assert len(chunks) == 1 and chunks[0].is_final
            assert chunks[0].delta == "" and chunks[0].index == 0
            result = chunks[0].response
            assert result is not None and result.text == "" and result.finish_reason == "stop"
            assert response.is_closed and runtime.in_flight == 0
        finally:
            await runtime.aclose()


@pytest.mark.asyncio
async def test_stream_oversized_json_integer_is_sanitized_and_not_retried() -> None:
    digit_limit = sys.get_int_max_str_digits()
    if digit_limit == 0:
        pytest.skip("Python's integer-decoding safety limit is disabled")
    payload = b'data: {"usage":{"prompt_tokens":' + b"9" * (digit_limit + 1) + b"}}\n\n"
    calls = 0
    response = httpx.Response(200, content=payload)

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        runtime = ProviderRuntime(client=client, retry_backoff_seconds=0)
        try:
            with pytest.raises(LLMProviderError) as raised:
                _ = [
                    chunk
                    async for chunk in _chat_provider(runtime=runtime).stream(_request(retries=2))
                ]
            assert raised.value.code == LLM_STREAM_FAILED
            assert isinstance(raised.value.__cause__, ValueError)
            assert "9" * 20 not in str(raised.value)
            assert calls == 1 and response.is_closed and runtime.in_flight == 0
        finally:
            await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("closed", [False, True])
async def test_stream_capacity_failure_does_not_send_http_request(closed: bool) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        pytest.fail("a rejected stream must not reach HTTP transport")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        runtime = ProviderRuntime(client=client, max_in_flight=1, max_waiters=0)
        lease = await runtime.acquire(ProviderDeadline(1))
        if closed:
            await runtime.aclose()
        try:
            with pytest.raises(LLMProviderError) as raised:
                await anext(_chat_provider(runtime=runtime).stream(_request()))
            assert raised.value.code == LLM_PROVIDER_RATE_LIMITED
            assert raised.value.status_code == 503 and raised.value.retryable
            assert runtime.in_flight == 1 and runtime.waiting == 0
        finally:
            lease.release()
            await runtime.aclose()
        assert runtime.in_flight == 0 and not client.is_closed


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 503])
async def test_stream_deadline_includes_cleanup_before_final_chunk_or_retry(
    status: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = _Clock()
    monkeypatch.setattr("packages.providers.runtime.time", clock)
    calls = 0

    class SlowCloseResponse(httpx.Response):
        async def aclose(self) -> None:
            await super().aclose()
            clock.seconds = 2.0

    response = SlowCloseResponse(status, content=b"data: [DONE]\n\n")

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        runtime = ProviderRuntime(client=client, request_timeout_seconds=1, retry_backoff_seconds=0)
        try:
            with pytest.raises(LLMProviderError) as raised:
                await anext(_chat_provider(runtime=runtime).stream(_request(retries=2)))
            assert raised.value.code == LLM_PROVIDER_TIMEOUT
            assert isinstance(raised.value.__cause__, TimeoutError)
            assert calls == 1 and response.is_closed and runtime.in_flight == 0
            assert not runtime._streams and not client.is_closed
        finally:
            await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancel", [False, True])
async def test_owned_stream_client_is_closed_when_opening_fails_or_is_cancelled(
    cancel: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_client = httpx.AsyncClient
    created: list[httpx.AsyncClient] = []
    started = asyncio.Event()
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        started.set()
        if cancel:
            await asyncio.Event().wait()
        raise httpx.ConnectError("private-key and private prompt", request=request)

    def factory() -> httpx.AsyncClient:
        client = original_client(transport=httpx.MockTransport(handler))
        created.append(client)
        return client

    monkeypatch.setattr("packages.llm.adapters.openai_compatible.httpx.AsyncClient", factory)
    provider = _chat_provider()
    result = cast(AsyncGenerator[GenerateChunk, None], provider.stream(_request(retries=1)))

    async def first_chunk() -> GenerateChunk:
        return await anext(result)

    pending = asyncio.create_task(first_chunk())
    try:
        await asyncio.wait_for(started.wait(), 1)
        if cancel:
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            with pytest.raises(LLMProviderError) as raised:
                await pending
            assert raised.value.code == LLM_STREAM_FAILED
            assert isinstance(raised.value.__cause__, httpx.ConnectError)
            assert "private-key" not in str(raised.value)
            assert "private prompt" not in str(raised.value)
        assert calls == (1 if cancel else 2)
        assert len(created) == calls and all(client.is_closed for client in created)
        assert provider._runtime.in_flight == 0
        assert not provider._runtime._client_closes
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        await result.aclose()
        await provider.aclose()

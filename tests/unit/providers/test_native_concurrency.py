from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Literal

import anyio
import httpx
import pytest

from packages.embeddings.adapters.openai_compatible import OpenAICompatibleEmbeddingProvider
from packages.embeddings.dto import EmbeddingRequest
from packages.embeddings.exceptions import EMBEDDING_PROVIDER_TIMEOUT, EmbeddingProviderError
from packages.llm.adapters.openai_compatible import OpenAICompatibleChatProvider
from packages.llm.dto import GenerateRequest, LLMMessage
from packages.llm.exceptions import LLM_PROVIDER_TIMEOUT, LLM_STREAM_FAILED, LLMProviderError
from packages.providers.runtime import ProviderDeadline, ProviderRuntime

Kind = Literal["llm", "embedding"]
Provider = OpenAICompatibleChatProvider | OpenAICompatibleEmbeddingProvider
TOKEN = b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
DONE = b"data: [DONE]\n\n"


def _request(*, retries: int = 0) -> GenerateRequest:
    return GenerateRequest(
        messages=[LLMMessage(role="user", content="private prompt")],
        provider="openai_compatible",
        model="test-model",
        timeout_seconds=1,
        retry_budget=retries,
        request_id="req",
        trace_id="trace",
        tenant_id="tenant",
        user_id="user",
    )


def _provider(
    kind: Kind,
    *,
    client: httpx.AsyncClient | None,
    runtime: ProviderRuntime | None = None,
    total_timeout_seconds: float | None = None,
) -> Provider:
    cls = OpenAICompatibleChatProvider if kind == "llm" else OpenAICompatibleEmbeddingProvider
    return cls(
        provider="openai_compatible",
        model="test-model",
        base_url="https://models.test/v1",
        api_key="private-key",
        client=client,
        runtime=runtime,
        total_timeout_seconds=total_timeout_seconds,
    )


async def _call(provider: Provider, *, retries: int = 0) -> None:
    if isinstance(provider, OpenAICompatibleChatProvider):
        await provider.generate(_request(retries=retries))
    else:
        await provider.embed_texts(
            EmbeddingRequest(
                texts=["private prompt"],
                provider="openai_compatible",
                model="test-model",
                timeout_seconds=1,
                retry_budget=retries,
            )
        )


def _ok(kind: Kind) -> httpx.Response:
    if kind == "llm":
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})


async def _until(predicate: Callable[[], bool]) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), timeout=1)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["llm", "embedding"])
async def test_native_admission_has_bounded_in_flight_and_queue(kind: Kind) -> None:
    release = asyncio.Event()
    active = peak = calls = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal active, peak, calls
        active += 1
        calls += 1
        peak = max(peak, active)
        try:
            await release.wait()
            await asyncio.sleep(0)
            return _ok(kind)
        finally:
            active -= 1

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        runtime = ProviderRuntime(max_in_flight=2, max_waiters=2)
        provider = _provider(kind, client=client, runtime=runtime)
        jobs = [asyncio.create_task(_call(provider)) for _ in range(4)]
        await _until(lambda: runtime.waiting == 2 and calls == 2)
        with pytest.raises((LLMProviderError, EmbeddingProviderError)) as overloaded:
            await _call(provider)
        assert overloaded.value.status_code == 503
        assert overloaded.value.retryable
        assert calls == 2
        release.set()
        await asyncio.gather(*jobs)
        assert calls == 4 and peak == 2
        assert (runtime.in_flight, runtime.waiting) == (0, 0)
        await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["llm", "embedding"])
async def test_cancelled_native_call_releases_slot_to_waiter(kind: Kind) -> None:
    calls = 0
    hold = asyncio.Event()

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            await hold.wait()
        return _ok(kind)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        runtime = ProviderRuntime(max_in_flight=1, max_waiters=1)
        provider = _provider(kind, client=client, runtime=runtime)
        first = asyncio.create_task(_call(provider))
        await _until(lambda: calls == 1)
        second = asyncio.create_task(_call(provider))
        await _until(lambda: runtime.waiting == 1)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        await asyncio.wait_for(second, 1)
        assert calls == 2
        assert (runtime.in_flight, runtime.waiting) == (0, 0)
        await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["llm", "embedding"])
async def test_native_retry_after_obeys_total_deadline_and_no_retry_storm(kind: Kind) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, headers={"Retry-After": "3600"}, json={"error": "secret"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        runtime = ProviderRuntime(request_timeout_seconds=0.02)
        provider = _provider(kind, client=client, runtime=runtime)
        with pytest.raises((LLMProviderError, EmbeddingProviderError)) as timed_out:
            await asyncio.wait_for(_call(provider, retries=10), 0.5)
        assert timed_out.value.code in {LLM_PROVIDER_TIMEOUT, EMBEDDING_PROVIDER_TIMEOUT}
        assert calls == 1
        assert runtime.in_flight == 0
        assert "secret" not in str(timed_out.value)
        await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["llm", "embedding"])
@pytest.mark.parametrize("status,expected_calls", [(401, 1), (500, 3)])
async def test_native_retry_budget_and_non_retryable_status(
    kind: Kind,
    status: int,
    expected_calls: int,
) -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        runtime = ProviderRuntime(retry_backoff_seconds=0)
        provider = _provider(kind, client=client, runtime=runtime)
        with pytest.raises((LLMProviderError, EmbeddingProviderError)):
            await _call(provider, retries=2)
        assert calls == expected_calls
        assert runtime.in_flight == 0
        await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["llm", "embedding"])
async def test_native_retryable_http_error_recovers(kind: Kind) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("secret network detail", request=request)
        if calls == 2:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return _ok(kind)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        runtime = ProviderRuntime(retry_backoff_seconds=0)
        await _call(_provider(kind, client=client, runtime=runtime), retries=2)
        assert calls == 3 and runtime.in_flight == 0
        await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["llm", "embedding"])
async def test_total_deadline_includes_admission_wait(kind: Kind) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        pytest.fail("an expired queued call must not reach the provider")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        runtime = ProviderRuntime(max_in_flight=1, request_timeout_seconds=0.01)
        active = await runtime.acquire(ProviderDeadline(1))
        with pytest.raises((LLMProviderError, EmbeddingProviderError)) as timed_out:
            await _call(_provider(kind, client=client, runtime=runtime))
        assert timed_out.value.code in {LLM_PROVIDER_TIMEOUT, EMBEDDING_PROVIDER_TIMEOUT}
        assert runtime.waiting == 0 and runtime.in_flight == 1
        active.release()
        await runtime.aclose()


class _Stream(httpx.AsyncByteStream):
    def __init__(self, *, token: bool = True, fail: bool = False, wait: bool = False) -> None:
        self.token = token
        self.fail = fail
        self.wait = wait
        self.closed = asyncio.Event()
        self.started = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self.started.set()
        if self.token:
            yield TOKEN
        if self.wait:
            await asyncio.Event().wait()
        if self.fail:
            raise httpx.ReadError("secret transport detail")
        yield DONE

    async def aclose(self) -> None:
        self.closed.set()


@pytest.mark.asyncio
async def test_stream_deadline_closes_idle_consumer_without_cancelling_caller() -> None:
    stream = _Stream(wait=True)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
    ) as client:
        runtime = ProviderRuntime(request_timeout_seconds=0.02, max_in_flight=1)
        provider = _provider("llm", client=client, runtime=runtime)
        assert isinstance(provider, OpenAICompatibleChatProvider)
        result = provider.stream(_request(retries=3))
        assert (await anext(result)).delta == "hello"
        await asyncio.wait_for(stream.closed.wait(), 0.5)
        assert runtime.in_flight == 0
        assert not client.is_closed
        # This await belongs to the caller and must not inherit a timer cancel.
        await asyncio.sleep(0)
        with pytest.raises(LLMProviderError) as timed_out:
            await anext(result)
        assert timed_out.value.code == LLM_PROVIDER_TIMEOUT
        assert runtime.in_flight == 0
        await result.aclose()
        await runtime.aclose()


@pytest.mark.asyncio
async def test_stream_transport_retries_only_before_first_emitted_token() -> None:
    calls = 0
    streams = [_Stream(token=False, fail=True), _Stream()]

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        stream = streams[min(calls, 1)]
        calls += 1
        return httpx.Response(200, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        runtime = ProviderRuntime(retry_backoff_seconds=0)
        provider = _provider("llm", client=client, runtime=runtime)
        assert isinstance(provider, OpenAICompatibleChatProvider)
        chunks = [chunk async for chunk in provider.stream(_request(retries=2))]
        assert [chunk.delta for chunk in chunks] == ["hello", ""]
        assert calls == 2
        assert all(stream.closed.is_set() for stream in streams)
        assert runtime.in_flight == 0
        await runtime.aclose()


@pytest.mark.asyncio
async def test_stream_partial_output_is_never_replayed_on_failure() -> None:
    calls = 0
    stream = _Stream(fail=True)

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, stream=stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        runtime = ProviderRuntime(retry_backoff_seconds=0)
        provider = _provider("llm", client=client, runtime=runtime)
        assert isinstance(provider, OpenAICompatibleChatProvider)
        result = provider.stream(_request(retries=3))
        assert (await anext(result)).delta == "hello"
        with pytest.raises(LLMProviderError) as failed:
            await anext(result)
        assert failed.value.code == LLM_STREAM_FAILED
        assert calls == 1 and stream.closed.is_set() and runtime.in_flight == 0
        await runtime.aclose()


@pytest.mark.asyncio
async def test_stream_http_retry_holds_one_admission_slot() -> None:
    calls = 0
    retry_started = asyncio.Event()
    finish_retry = asyncio.Event()
    runtime = ProviderRuntime(max_in_flight=1, max_waiters=0, retry_backoff_seconds=0)

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert runtime.in_flight == 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        retry_started.set()
        await finish_retry.wait()
        return httpx.Response(200, content=TOKEN + DONE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = _provider("llm", client=client, runtime=runtime)
        assert isinstance(provider, OpenAICompatibleChatProvider)

        async def consume() -> list[str]:
            return [chunk.delta async for chunk in provider.stream(_request(retries=2))]

        job = asyncio.create_task(consume())
        await asyncio.wait_for(retry_started.wait(), 1)
        with pytest.raises(LLMProviderError) as overloaded:
            await provider.generate(_request())
        assert overloaded.value.status_code == 503
        assert calls == 2 and runtime.in_flight == 1
        finish_retry.set()
        assert await job == ["hello", ""]
        assert calls == 2 and runtime.in_flight == 0
        await runtime.aclose()


@pytest.mark.asyncio
async def test_deadline_retains_slow_close_ownership_without_blocking_timeout() -> None:
    finish_close = asyncio.Event()
    close_finished = asyncio.Event()

    class SlowClose(_Stream):
        async def aclose(self) -> None:
            self.closed.set()
            await finish_close.wait()
            close_finished.set()

    stream = SlowClose(wait=True)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
    ) as client:
        runtime = ProviderRuntime(request_timeout_seconds=0.02, max_in_flight=1)
        provider = _provider("llm", client=client, runtime=runtime)
        assert isinstance(provider, OpenAICompatibleChatProvider)
        result = provider.stream(_request())
        await anext(result)
        await asyncio.wait_for(stream.closed.wait(), 0.5)
        assert runtime.in_flight == 0 and not close_finished.is_set()
        with pytest.raises(LLMProviderError) as timed_out:
            await asyncio.wait_for(anext(result), 0.1)
        assert timed_out.value.code == LLM_PROVIDER_TIMEOUT
        shutdown = asyncio.create_task(runtime.aclose())
        await asyncio.sleep(0)
        assert not shutdown.done()
        finish_close.set()
        await asyncio.wait_for(shutdown, 0.5)
        assert close_finished.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["explicit", "double_cancel", "anyio_level_cancel"])
async def test_repeated_cancellation_cannot_cancel_owned_stream_cleanup(mode: str) -> None:
    finish_close = asyncio.Event()
    close_finished = asyncio.Event()
    close_cancelled = False

    class SlowClose(_Stream):
        async def aclose(self) -> None:
            nonlocal close_cancelled
            self.closed.set()
            try:
                await finish_close.wait()
            except asyncio.CancelledError:
                close_cancelled = True
                raise
            close_finished.set()

    stream = SlowClose(wait=True)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))
    ) as client:
        runtime = ProviderRuntime(client=client, request_timeout_seconds=60)
        provider = _provider("llm", client=None, runtime=runtime)
        assert isinstance(provider, OpenAICompatibleChatProvider)
        result = provider.stream(_request())
        await anext(result)
        if mode == "anyio_level_cancel":
            with anyio.CancelScope() as cancel_scope:
                cancel_scope.cancel()
                await result.aclose()
            await asyncio.wait_for(stream.closed.wait(), 1)
        else:
            pending = asyncio.create_task(result.aclose() if mode == "explicit" else anext(result))
            if mode == "double_cancel":
                await asyncio.sleep(0)
                pending.cancel()  # cancellation starts the response cleanup
            await asyncio.wait_for(stream.closed.wait(), 1)
            pending.cancel()  # cancellation during the slow transport close
            await asyncio.gather(pending, return_exceptions=True)
        assert not close_cancelled and not close_finished.is_set()
        assert len(runtime._streams) == 1
        # Cancelling a shutdown waiter must not cancel shutdown or its children.
        shutdown = asyncio.create_task(runtime.aclose())
        await asyncio.sleep(0)
        shutdown.cancel()
        await asyncio.gather(shutdown, return_exceptions=True)
        assert len(runtime._streams) == 1 and not close_cancelled
        finish_close.set()
        await asyncio.wait_for(runtime.aclose(), 1)
        assert close_finished.is_set() and not close_cancelled
        assert len(runtime._streams) == 0 and runtime.in_flight == 0
        assert not client.is_closed


@pytest.mark.asyncio
async def test_stream_cancel_and_explicit_close_release_response_and_slot() -> None:
    for early_close in (False, True):
        stream = _Stream(token=early_close, wait=True)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _, value=stream: httpx.Response(200, stream=value))
        ) as client:
            runtime = ProviderRuntime()
            provider = _provider("llm", client=client, runtime=runtime)
            assert isinstance(provider, OpenAICompatibleChatProvider)
            result = provider.stream(_request())
            if early_close:
                await anext(result)
                await result.aclose()
            else:
                pending = asyncio.create_task(anext(result))
                await stream.started.wait()
                pending.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await pending
            assert stream.closed.is_set() and runtime.in_flight == 0
            await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["llm", "embedding"])
async def test_client_ownership_and_pool_reuse(
    kind: Kind,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = httpx.AsyncClient
    created: list[httpx.AsyncClient] = []

    def factory(**kwargs: object) -> httpx.AsyncClient:
        client = original(transport=httpx.MockTransport(lambda _: _ok(kind)), **kwargs)
        created.append(client)
        return client

    monkeypatch.setattr("packages.providers.runtime.httpx.AsyncClient", factory)
    standalone = _provider(kind, client=None)
    await _call(standalone)
    await _call(standalone)
    assert len(created) == 2 and all(client.is_closed for client in created)
    await standalone.aclose()

    runtime = ProviderRuntime()
    pooled = _provider(kind, client=None, runtime=runtime)
    await _call(pooled)
    await _call(pooled)
    assert len(created) == 3 and not created[-1].is_closed
    await pooled.aclose()
    assert not created[-1].is_closed  # externally owned runtime still lives
    await runtime.aclose()
    assert created[-1].is_closed

    external = original(transport=httpx.MockTransport(lambda _: _ok(kind)))
    other_runtime = ProviderRuntime()
    injected = _provider(kind, client=external, runtime=other_runtime)
    await _call(injected)
    await injected.aclose()
    await other_runtime.aclose()
    assert not external.is_closed and len(created) == 3
    await external.aclose()


@pytest.mark.asyncio
async def test_default_stream_deadline_also_closes_its_owned_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = httpx.AsyncClient
    created: list[httpx.AsyncClient] = []
    stream = _Stream(wait=True)

    def factory(**kwargs: object) -> httpx.AsyncClient:
        client = original(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream)), **kwargs
        )
        created.append(client)
        return client

    monkeypatch.setattr("packages.providers.runtime.httpx.AsyncClient", factory)
    provider = _provider("llm", client=None, total_timeout_seconds=0.02)
    assert isinstance(provider, OpenAICompatibleChatProvider)
    result = provider.stream(_request())
    await anext(result)
    await asyncio.wait_for(stream.closed.wait(), 0.5)
    await _until(lambda: created[0].is_closed)
    with pytest.raises(LLMProviderError):
        await anext(result)
    await provider.aclose()

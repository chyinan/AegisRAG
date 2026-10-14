"""Real LangChain SDK objects with a local-only mocked HTTP transport."""

# ruff: noqa: E402 - optional framework imports follow importorskip
from __future__ import annotations

import asyncio
import json
import traceback
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

langchain_openai = pytest.importorskip("langchain_openai")
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langsmith import get_tracing_context

from packages.llm.adapters.langchain import LangChainChatProvider
from packages.llm.adapters.openai_compatible import OpenAICompatibleChatProvider
from packages.llm.dto import GenerateRequest, LLMMessage
from packages.llm.exceptions import LLMProviderError


def _request(**updates: Any) -> GenerateRequest:
    values = {
        "messages": (
            LLMMessage(role="system", content="Use supplied evidence"),
            LLMMessage(role="user", content="private-user-prompt"),
        ),
        "provider": "openai_compatible",
        "model": "test-chat",
        "timeout_seconds": 1.0,
        "retry_budget": 0,
        "request_id": "request-1",
        "trace_id": "trace-1",
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "temperature": 0.2,
        "max_output_tokens": 42,
        "stream_options": {"include_usage": True},
    }
    values.update(updates)
    return GenerateRequest(**values)


def _provider(client: httpx.AsyncClient, kind: str = "langchain") -> Any:
    kwargs = {
        "provider": "openai_compatible",
        "model": "test-chat",
        "base_url": "https://models.test/v1",
        "api_key": "test-key",
        "version": "test-version",
    }
    if kind == "native":
        return OpenAICompatibleChatProvider(**kwargs, client=client)
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: pytest.fail("sync I/O"))
    ) as sync_client:
        model = langchain_openai.ChatOpenAI(
            model="test-chat",
            base_url="https://models.test/v1",
            api_key="test-key",
            http_async_client=client,
            http_client=sync_client,
            max_retries=0,
            stream_usage=True,
            use_responses_api=False,
            cache=False,
        )
    return LangChainChatProvider(**kwargs, chat_model=model)


def _body() -> dict[str, Any]:
    return {
        "id": "chat-1",
        "object": "chat.completion",
        "created": 1,
        "model": "test-chat",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Evidence answer"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
    }


def _sse() -> bytes:
    chunks = [
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant", "content": "Evidence "},
                    "finish_reason": None,
                }
            ]
        },
        {"choices": [{"index": 0, "delta": {"content": "answer"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
        {"choices": [], "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10}},
    ]
    return (
        "".join(
            "data: "
            + json.dumps(
                {
                    "id": "chat-1",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "test-chat",
                    **chunk,
                }
            )
            + "\n\n"
            for chunk in chunks
        )
        + "data: [DONE]\n\n"
    ).encode()


@pytest.mark.parametrize("kind", ["native", "langchain"])
async def test_supported_generation_contract_matches_native(kind: str) -> None:
    requests: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        requests.append(json.loads(request.content))
        return httpx.Response(200, json=_body())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        response = await _provider(client, kind).generate(_request())
    assert response.text == "Evidence answer"
    assert response.provider == "openai_compatible"
    assert response.model == "test-chat"
    assert response.version == "test-version"
    assert response.usage.model_dump() == {
        "input_tokens": 8,
        "output_tokens": 2,
        "total_tokens": 10,
    }
    assert response.finish_reason == "stop"
    assert response.request_id == response.metadata.request_id == "request-1"
    assert response.tenant_id == response.metadata.tenant_id == "tenant-1"
    assert response.trace_id == response.metadata.trace_id == "trace-1"
    assert response.user_id == response.metadata.user_id == "user-1"
    assert response.metadata.metadata["usage_unavailable_count"] == 0
    assert response.latency_ms >= 0
    sent = requests[0]
    assert sent["model"] == "test-chat"
    assert sent["messages"] == [
        {"role": item.role, "content": item.content} for item in _request().messages
    ]
    assert sent["temperature"] == 0.2
    assert sent.get("max_tokens", sent.get("max_completion_tokens")) == 42
    assert "tenant_id" not in sent and "metadata" not in sent


@pytest.mark.parametrize("kind", ["native", "langchain"])
async def test_supported_stream_contract_includes_final_usage(kind: str) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream_options"] == {"include_usage": True}
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_sse())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        chunks = [chunk async for chunk in _provider(client, kind).stream(_request())]
    assert [chunk.delta for chunk in chunks] == ["Evidence ", "answer", ""]
    assert [chunk.index for chunk in chunks] == [0, 1, 2]
    assert [chunk.is_final for chunk in chunks] == [False, False, True]
    final = chunks[-1].response
    assert final is not None and final.text == "Evidence answer"
    assert final.usage.total_tokens == 10
    assert final.metadata.chunk_count == 3
    assert final.finish_reason == "stop"
    assert all(chunk.metadata.tenant_id == "tenant-1" for chunk in chunks)


@pytest.mark.parametrize(
    "status,code,retryable",
    [
        (401, "LLM_PROVIDER_AUTH_FAILED", False),
        (403, "LLM_PROVIDER_AUTH_FAILED", False),
        (400, "LLM_GENERATION_INVALID_REQUEST", False),
        (429, "LLM_PROVIDER_RATE_LIMITED", True),
        (503, "LLM_PROVIDER_FAILED", True),
    ],
)
async def test_error_mapping_retry_budget_and_redaction(
    status: int, code: str, retryable: bool
) -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            status, json={"error": {"message": "private-user-prompt sk-secret-value"}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(LLMProviderError) as caught:
            await _provider(client).generate(_request(retry_budget=2))
    assert caught.value.code == code
    assert caught.value.retryable is retryable
    assert calls == (3 if retryable else 1)
    formatted = "".join(traceback.format_exception(caught.value))
    assert "private-user-prompt" not in formatted and "sk-secret-value" not in formatted
    assert "response" not in caught.value.details


async def test_missing_usage_is_explicit_not_estimated() -> None:
    body = _body()
    body.pop("usage")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    ) as client:
        result = await _provider(client).generate(_request())
    assert result.usage.total_tokens == 0
    assert result.metadata.metadata["usage_unavailable_count"] == 1


async def test_mismatched_configuration_rejected_without_http() -> None:
    def handle(_: httpx.Request) -> httpx.Response:
        pytest.fail("Mismatched model must not call HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(LLMProviderError, match="LLM_GENERATION_INVALID_REQUEST"):
            await _provider(client).generate(_request(model="different-model"))


class _ObservedModel(BaseChatModel):
    observed: list[bool | None] = []
    closed: list[bool] = []
    mode: str = "stream"

    @property
    def _llm_type(self) -> str:
        return "observed-test-only"

    def _generate(self, *args: Any, **kwargs: Any) -> ChatResult:
        raise AssertionError("Sync model must not run")

    async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
        self.observed.append(get_tracing_context()["enabled"])
        if self.mode == "slow":
            await asyncio.sleep(10)
        from langchain_core.outputs import ChatGeneration

        return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok"))])

    async def _astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        self.observed.append(get_tracing_context()["enabled"])
        try:
            yield ChatGenerationChunk(message=AIMessageChunk(content="one"))
            if self.mode == "error":
                raise httpx.ReadError("private-user-prompt sk-secret-value")
            await asyncio.sleep(10)
        finally:
            self.closed.append(True)


def _observed_provider(model: _ObservedModel) -> LangChainChatProvider:
    return LangChainChatProvider(
        provider="openai_compatible",
        model="test-chat",
        base_url="https://models.test/v1",
        api_key="test-key",
        chat_model=model,
    )


async def test_tracing_disabled_even_when_environment_enables_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    model = _ObservedModel()
    await _observed_provider(model).generate(_request())
    assert model.observed == [False]


async def test_request_timeout_is_bounded() -> None:
    model = _ObservedModel(mode="slow")
    with pytest.raises(LLMProviderError, match="LLM_PROVIDER_TIMEOUT"):
        await _observed_provider(model).generate(_request(timeout_seconds=0.01, retry_budget=1))
    assert len(model.observed) == 2


async def test_consumer_close_closes_underlying_stream() -> None:
    model = _ObservedModel()
    iterator = _observed_provider(model).stream(_request())
    assert (await anext(iterator)).delta == "one"
    await iterator.aclose()
    assert model.closed == [True]


async def test_cancellation_closes_underlying_stream() -> None:
    model = _ObservedModel()
    iterator = _observed_provider(model).stream(_request())
    await anext(iterator)
    pending = asyncio.create_task(anext(iterator))
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert model.closed == [True]


async def test_partial_stream_is_never_replayed_and_errors_are_redacted() -> None:
    model = _ObservedModel(mode="error")
    iterator = _observed_provider(model).stream(_request(retry_budget=3))
    await anext(iterator)
    with pytest.raises(LLMProviderError, match="LLM_STREAM_FAILED") as caught:
        await anext(iterator)
    assert model.observed == [False]
    assert model.closed == [True]
    assert "private-user-prompt" not in "".join(traceback.format_exception(caught.value))


@pytest.mark.parametrize(
    "message",
    [
        AIMessage(content=[{"type": "text", "text": "unsupported block"}]),
        AIMessage(content="", tool_calls=[{"name": "unsafe", "args": {}, "id": "call-1"}]),
        AIMessage(
            content="",
            invalid_tool_calls=[
                {"name": "unsafe", "args": "broken", "id": "call-1", "error": "invalid"}
            ],
        ),
        AIMessageChunk(
            content="",
            tool_call_chunks=[{"name": "unsafe", "args": "", "id": "call-1", "index": 0}],
        ),
        AIMessage(content="", additional_kwargs={"function_call": {"name": "unsafe"}}),
    ],
)
def test_nontext_and_tool_outputs_are_rejected(message: Any) -> None:
    with pytest.raises(LLMProviderError, match="LLM_PROVIDER_MALFORMED_RESPONSE"):
        _observed_provider(_ObservedModel())._parse_message(message, _request())


def test_injected_model_identity_and_retry_config_are_validated() -> None:
    sync_client = httpx.Client(transport=httpx.MockTransport(lambda _: pytest.fail("sync I/O")))
    async_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("HTTP I/O"))
    )
    model = langchain_openai.ChatOpenAI(
        model="wrong-model",
        api_key="test-key",
        max_retries=0,
        http_client=sync_client,
        http_async_client=async_client,
    )
    with pytest.raises(ValueError, match="model does not match"):
        LangChainChatProvider(
            provider="openai_compatible",
            model="test-chat",
            base_url="https://models.test/v1",
            api_key="test-key",
            chat_model=model,
        )
    model = langchain_openai.ChatOpenAI(
        model="test-chat",
        api_key="test-key",
        max_retries=2,
        http_client=sync_client,
        http_async_client=async_client,
    )
    with pytest.raises(ValueError, match="max_retries=0"):
        LangChainChatProvider(
            provider="openai_compatible",
            model="test-chat",
            base_url="https://models.test/v1",
            api_key="test-key",
            chat_model=model,
        )


async def test_retry_before_first_stream_delta_succeeds() -> None:
    calls = 0

    def handle(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"error": {"message": "transient"}})
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=_sse())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        chunks = [chunk async for chunk in _provider(client).stream(_request(retry_budget=1))]
    assert calls == 2
    assert chunks[-1].response.text == "Evidence answer"


async def test_stream_timeout_closes_and_does_not_retry_after_delta() -> None:
    model = _ObservedModel()
    iterator = _observed_provider(model).stream(_request(timeout_seconds=0.01, retry_budget=2))
    await anext(iterator)
    with pytest.raises(LLMProviderError, match="LLM_PROVIDER_TIMEOUT"):
        await anext(iterator)
    assert len(model.observed) == 1
    assert model.closed == [True]


async def test_ambient_collectors_and_callback_config_cannot_capture_prompts() -> None:
    from langchain_core.callbacks.base import BaseCallbackHandler
    from langchain_core.runnables.config import set_config_context
    from langchain_core.tracers.context import collect_runs

    seen: list[Any] = []

    class Capture(BaseCallbackHandler):
        def on_chat_model_start(self, serialized: Any, messages: Any, **kwargs: Any) -> None:
            seen.append(messages)

    model = _ObservedModel()
    provider = _observed_provider(model)
    with collect_runs() as collector:
        with set_config_context({"callbacks": [Capture()]}) as inherited:
            await asyncio.create_task(provider.generate(_request()), context=inherited)
            iterator = provider.stream(_request())
            assert (await asyncio.create_task(anext(iterator), context=inherited)).delta == "one"
            await asyncio.create_task(iterator.aclose(), context=inherited)
        assert collector.traced_runs == []
    assert seen == []
    assert model.closed == [True]


async def test_ambient_tracing_v2_cannot_capture_prompts(monkeypatch: pytest.MonkeyPatch) -> None:
    from unittest.mock import MagicMock

    from langchain_core.tracers.context import tracing_v2_callback_var, tracing_v2_enabled
    from langchain_core.tracers.langchain import LangChainTracer

    saved: list[Any] = []
    monkeypatch.setattr(LangChainTracer, "_persist_run", lambda self, run: saved.append(run))
    with tracing_v2_enabled(client=MagicMock()) as tracer:
        await _observed_provider(_ObservedModel()).generate(_request())
        assert tracing_v2_callback_var.get() is tracer
    assert saved == []


async def test_global_debug_fails_closed_without_prompt_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import langchain_core.globals as globals_module

    model = _ObservedModel()
    monkeypatch.setattr(globals_module, "_debug", True)
    with pytest.raises(LLMProviderError, match="LLM_PROVIDER_FAILED"):
        await _observed_provider(model).generate(_request())
    assert model.observed == []
    assert "private-user-prompt" not in capsys.readouterr().out


async def test_debug_enabled_midstream_still_closes_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    import langchain_core.globals as globals_module

    model = _ObservedModel()
    iterator = _observed_provider(model).stream(_request())
    await anext(iterator)
    monkeypatch.setattr(globals_module, "_debug", True)
    with pytest.raises(LLMProviderError, match="LLM_STREAM_FAILED"):
        await anext(iterator)
    assert model.closed == [True]


async def test_owned_clients_close_after_each_call_and_provider_can_be_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clients: list[Any] = []
    real_sync = httpx.Client
    real_async = httpx.AsyncClient

    class LocalSyncClient(real_sync):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(transport=httpx.MockTransport(lambda _: pytest.fail("sync HTTP")))
            clients.append(self)

    class LocalAsyncClient(real_async):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(
                transport=httpx.MockTransport(lambda _: httpx.Response(200, json=_body()))
            )
            clients.append(self)

    monkeypatch.setattr(httpx, "Client", LocalSyncClient)
    monkeypatch.setattr(httpx, "AsyncClient", LocalAsyncClient)
    provider = LangChainChatProvider(
        provider="openai_compatible",
        model="test-chat",
        base_url="https://models.test/v1",
        api_key="test-key",
    )
    for _ in range(2):
        assert (await provider.generate(_request())).text == "Evidence answer"
    assert len(clients) == 4
    assert all(client.is_closed for client in clients)


async def test_isolation_preserves_rls_otel_and_only_safe_log_correlation() -> None:
    from opentelemetry import context as otel_context
    from structlog.contextvars import bound_contextvars, get_contextvars

    from packages.common.tenant_context import current_tenant_id, tenant_scope

    seen: list[Any] = []

    class ContextModel(_ObservedModel):
        async def _agenerate(self, *args: Any, **kwargs: Any) -> Any:
            seen.append(
                (current_tenant_id(), otel_context.get_value("test-span"), get_contextvars())
            )
            return await super()._agenerate(*args, **kwargs)

    token = otel_context.attach(otel_context.set_value("test-span", "trace-context"))
    try:
        with tenant_scope("rls-tenant"):
            with bound_contextvars(
                request_id="request-context",
                trace_id="trace-context",
                session_id="session-context",
                prompt="must-not-propagate",
            ):
                await _observed_provider(ContextModel()).generate(_request())
                assert current_tenant_id() == "rls-tenant"
                assert get_contextvars()["prompt"] == "must-not-propagate"
    finally:
        otel_context.detach(token)
    assert seen == [
        (
            "rls-tenant",
            "trace-context",
            {
                "request_id": "request-context",
                "trace_id": "trace-context",
                "session_id": "session-context",
            },
        )
    ]


@pytest.mark.parametrize("ending", ["close", "cancel", "timeout"])
async def test_real_sdk_stream_releases_http_response(ending: str) -> None:
    class TrackedStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.waiting = asyncio.Event()
            self.closed = False

        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield _sse().split(b"\n\n")[0] + b"\n\n"
            self.waiting.set()
            await asyncio.sleep(10)

        async def aclose(self) -> None:
            self.closed = True

    stream = TrackedStream()
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=stream,
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        iterator = _provider(client).stream(_request(timeout_seconds=0.03, retry_budget=2))
        assert (await anext(iterator)).delta == "Evidence "
        if ending == "close":
            await iterator.aclose()
        elif ending == "cancel":
            pending = asyncio.create_task(anext(iterator))
            await stream.waiting.wait()
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            with pytest.raises(LLMProviderError, match="LLM_PROVIDER_TIMEOUT"):
                await anext(iterator)
        assert stream.closed
        assert not client.is_closed  # injected clients remain caller-owned


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("logger_name", ["openai._base_client", "openai._response"])
async def test_sdk_debug_fails_closed_without_network_or_private_log(
    stream: bool,
    logger_name: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    def handle(_: httpx.Request) -> httpx.Response:
        pytest.fail("SDK debug configuration must fail before HTTP")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = _provider(client)
        with caplog.at_level(logging.DEBUG, logger=logger_name):
            with pytest.raises(LLMProviderError):
                if stream:
                    await anext(provider.stream(_request()))
                else:
                    await provider.generate(_request())
    assert "private-user-prompt" not in caplog.text
    assert "test-key" not in caplog.text

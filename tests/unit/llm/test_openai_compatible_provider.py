from __future__ import annotations

import json

import httpx
import pytest

from packages.llm.adapters.openai_compatible import OpenAICompatibleChatProvider
from packages.llm.dto import GenerateRequest, LLMMessage
from packages.llm.exceptions import (
    LLM_GENERATION_INVALID_REQUEST,
    LLM_PROVIDER_AUTH_FAILED,
    LLM_PROVIDER_FAILED,
    LLM_PROVIDER_MALFORMED_RESPONSE,
    LLM_PROVIDER_RATE_LIMITED,
    LLM_PROVIDER_TIMEOUT,
    LLM_STREAM_FAILED,
    LLMProviderError,
)


@pytest.mark.asyncio
async def test_generate_posts_allowlisted_chat_completion_body_and_maps_usage() -> None:
    captured: list[dict[str, object]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        assert request.url == "https://llm.example/v1/chat/completions"
        assert request.headers["authorization"] == "Bearer test-secret"
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "answer from real provider"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 4,
                    "total_tokens": 14,
                },
            },
        )

    provider = _provider(handler)

    response = await provider.generate(_request())

    assert response.text == "answer from real provider"
    assert response.provider == "openai_compatible"
    assert response.model == "configured-model"
    assert response.usage.input_tokens == 10
    assert response.usage.output_tokens == 4
    assert response.usage.total_tokens == 14
    assert response.finish_reason == "stop"
    assert dict(response.metadata.metadata)["message_count"] == 2
    body = captured[0]
    assert body == {
        "model": "configured-model",
        "messages": [
            {"role": "system", "content": "Use only context."},
            {"role": "user", "content": "private prompt"},
        ],
        "temperature": 0.2,
        "max_tokens": 64,
        "stream": False,
    }
    forbidden_dump = json.dumps(body)
    for forbidden in ("request_id", "trace_id", "tenant_id", "user_id", "test-secret"):
        assert forbidden not in forbidden_dump


@pytest.mark.asyncio
async def test_generate_marks_usage_unavailable_without_fabricating_counts() -> None:
    provider = _provider(
        lambda _request: httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}]},
        )
    )

    response = await provider.generate(_request())

    assert response.usage.input_tokens == 0
    assert response.usage.output_tokens == 0
    assert response.usage.total_tokens == 0
    assert dict(response.metadata.metadata)["usage_unavailable_count"] == 1


@pytest.mark.asyncio
async def test_stream_yields_token_chunks_and_final_response_with_usage() -> None:
    provider = _provider(
        lambda _request: httpx.Response(
            200,
            content=_sse(
                {"choices": [{"delta": {"content": "alpha "}}]},
                {"choices": [{"delta": {"content": "beta"}, "finish_reason": "stop"}]},
                {
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 2,
                        "total_tokens": 7,
                    },
                },
            ),
        )
    )

    chunks = [chunk async for chunk in provider.stream(_request())]

    assert [chunk.delta for chunk in chunks] == ["alpha ", "beta", ""]
    assert [chunk.is_final for chunk in chunks] == [False, False, True]
    assert chunks[-1].response is not None
    assert chunks[-1].response.text == "alpha beta"
    assert chunks[-1].response.usage.total_tokens == 7
    assert chunks[-1].response.finish_reason == "stop"
    assert chunks[0].metadata is not None
    assert chunks[0].metadata.provider == "openai_compatible"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_code", "retryable"),
    [
        (400, LLM_GENERATION_INVALID_REQUEST, False),
        (401, LLM_PROVIDER_AUTH_FAILED, False),
        (403, LLM_PROVIDER_AUTH_FAILED, False),
        (429, LLM_PROVIDER_RATE_LIMITED, True),
        (500, LLM_PROVIDER_FAILED, True),
    ],
)
async def test_generate_maps_http_errors_to_safe_provider_errors(
    status_code: int,
    expected_code: str,
    retryable: bool,
) -> None:
    provider = _provider(lambda _request: httpx.Response(status_code, json={"error": "secret"}))

    with pytest.raises(LLMProviderError) as exc_info:
        await provider.generate(_request())

    assert exc_info.value.code == expected_code
    assert exc_info.value.retryable is retryable
    assert exc_info.value.details == {
        "request_id": "req-1",
        "trace_id": "trace-1",
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "provider": "openai_compatible",
        "model": "configured-model",
        "error_code": expected_code,
    }
    assert "secret" not in str(exc_info.value.details)


@pytest.mark.asyncio
async def test_generate_maps_timeout_and_malformed_response() -> None:
    timeout_provider = _provider(
        lambda request: (_ for _ in ()).throw(
            httpx.TimeoutException("secret", request=request)
        )
    )
    malformed_provider = _provider(lambda _request: httpx.Response(200, json={"choices": []}))

    with pytest.raises(LLMProviderError) as timeout:
        await timeout_provider.generate(_request())
    with pytest.raises(LLMProviderError) as malformed:
        await malformed_provider.generate(_request())

    assert timeout.value.code == LLM_PROVIDER_TIMEOUT
    assert timeout.value.retryable is True
    assert malformed.value.code == LLM_PROVIDER_MALFORMED_RESPONSE
    assert malformed.value.retryable is False


@pytest.mark.asyncio
async def test_stream_interruption_maps_to_safe_stream_error() -> None:
    provider = _provider(lambda _request: httpx.Response(200, content=b"data: {not-json}\n\n"))

    with pytest.raises(LLMProviderError) as exc_info:
        _ = [chunk async for chunk in provider.stream(_request())]

    assert exc_info.value.code == LLM_STREAM_FAILED
    assert exc_info.value.retryable is True
    assert "private prompt" not in str(exc_info.value.details)
    assert "test-secret" not in repr(exc_info.value)


def _provider(
    handler: httpx.MockTransport | httpx.SyncByteStream | object,
) -> OpenAICompatibleChatProvider:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    return OpenAICompatibleChatProvider(
        provider="openai_compatible",
        model="configured-model",
        base_url="https://llm.example/v1",
        api_key="test-secret",
        client=httpx.AsyncClient(transport=transport),
    )


def _request() -> GenerateRequest:
    return GenerateRequest(
        messages=(
            LLMMessage(role="system", name="system", content="Use only context."),
            LLMMessage(role="user", name="user", content="private prompt"),
        ),
        provider="openai_compatible",
        model="configured-model",
        timeout_seconds=1.0,
        retry_budget=0,
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-1",
        user_id="user-1",
        temperature=0.2,
        max_output_tokens=64,
    )


def _sse(*payloads: dict[str, object]) -> bytes:
    frames = [f"data: {json.dumps(payload)}\n\n" for payload in payloads]
    frames.append("data: [DONE]\n\n")
    return "".join(frames).encode()

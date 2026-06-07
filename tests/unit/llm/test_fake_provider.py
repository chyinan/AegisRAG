from __future__ import annotations

import pytest

from packages.llm.adapters.fake import FakeLLMProvider
from packages.llm.dto import GenerateRequest, LLMMessage
from packages.llm.exceptions import (
    LLM_PROVIDER_FAILED,
    LLM_PROVIDER_RATE_LIMITED,
    LLM_PROVIDER_TIMEOUT,
    LLM_STREAM_FAILED,
    LLMProviderError,
)


@pytest.mark.asyncio
async def test_fake_llm_provider_returns_deterministic_generation() -> None:
    provider = FakeLLMProvider(response_text="无法从给定上下文确认。")
    request = _request()

    first = await provider.generate(request)
    second = await provider.generate(request)

    assert first == second
    assert first.text == "无法从给定上下文确认。"
    assert first.provider == "fake"
    assert first.model == "fake-llm"
    assert first.version == "fake-v1"
    assert first.finish_reason == "stop"
    assert first.usage.input_tokens > 0
    assert first.usage.output_tokens > 0
    assert first.usage.total_tokens == first.usage.input_tokens + first.usage.output_tokens
    assert first.metadata.request_id == "req-1"
    assert first.metadata.tenant_id == "tenant-1"
    assert "private prompt" not in str(first.model_dump())


@pytest.mark.asyncio
async def test_fake_llm_provider_streams_tokens_then_final_chunk() -> None:
    provider = FakeLLMProvider(response_text="alpha beta")
    chunks = [chunk async for chunk in provider.stream(_request())]

    assert [chunk.delta for chunk in chunks] == ["alpha ", "beta", ""]
    assert "".join(chunk.delta for chunk in chunks if not chunk.is_final) == "alpha beta"
    assert [chunk.index for chunk in chunks] == [0, 1, 2]
    assert [chunk.is_final for chunk in chunks] == [False, False, True]
    assert chunks[-1].response is not None
    assert chunks[-1].response.text == "alpha beta"
    assert chunks[-1].metadata is not None
    assert chunks[-1].metadata.token_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure_mode", "expected_code", "retryable"),
    [
        ("timeout", LLM_PROVIDER_TIMEOUT, True),
        ("rate_limited", LLM_PROVIDER_RATE_LIMITED, True),
        ("failed", LLM_PROVIDER_FAILED, True),
    ],
)
async def test_fake_llm_provider_failure_modes_are_stable_and_safe(
    failure_mode: str,
    expected_code: str,
    retryable: bool,
) -> None:
    provider = FakeLLMProvider(failure_mode=failure_mode)  # type: ignore[arg-type]

    with pytest.raises(LLMProviderError) as exc_info:
        await provider.generate(_request())

    assert exc_info.value.code == expected_code
    assert exc_info.value.retryable is retryable
    assert exc_info.value.details == {
        "provider": "fake",
        "model": "fake-llm",
        "version": "fake-v1",
    }
    assert "private prompt" not in str(exc_info.value.details)


@pytest.mark.asyncio
async def test_fake_llm_provider_stream_failure_is_stable_and_safe() -> None:
    provider = FakeLLMProvider(failure_mode="stream_failed")

    with pytest.raises(LLMProviderError) as exc_info:
        _ = [chunk async for chunk in provider.stream(_request())]

    assert exc_info.value.code == LLM_STREAM_FAILED
    assert exc_info.value.retryable is True
    assert "private prompt" not in str(exc_info.value.details)


def _request() -> GenerateRequest:
    return GenerateRequest(
        messages=(
            LLMMessage(role="system", name="system", content="Use only provided context."),
            LLMMessage(role="user", name="question", content="private prompt with token"),
        ),
        provider="fake",
        model="fake-llm",
        timeout_seconds=1.0,
        retry_budget=1,
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-1",
        user_id="user-1",
        session_id="session-1",
        temperature=0.0,
        max_output_tokens=64,
    )

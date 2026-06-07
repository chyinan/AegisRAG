from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import ValidationError

from packages.llm.dto import (
    GenerateChunk,
    GenerateRequest,
    GenerateResponse,
    GenerationMetadata,
    LLMMessage,
    TokenUsage,
)


def test_generate_request_requires_typed_messages() -> None:
    with pytest.raises(ValidationError):
        GenerateRequest(
            messages=cast(tuple[LLMMessage, ...], ({"role": "user", "content": "hello"},)),
            provider="fake",
            model="fake-llm",
            timeout_seconds=1.0,
            retry_budget=0,
            request_id="req-1",
            trace_id="trace-1",
            tenant_id="tenant-1",
            user_id="user-1",
        )


def test_llm_message_rejects_blank_content_and_invalid_role() -> None:
    with pytest.raises(ValidationError):
        LLMMessage(role="user", content=" ")

    with pytest.raises(ValidationError):
        LLMMessage(role=cast(Any, "tool"), content="hello")


def test_generate_request_validates_timeout_retry_and_safe_metadata() -> None:
    with pytest.raises(ValidationError):
        _request(timeout_seconds=0)

    with pytest.raises(ValidationError):
        _request(retry_budget=-1)

    request = _request(
        metadata={
            "source_count": 2,
            "business_summary": "confidential customer details",
            "content": "secret prompt",
            "raw_response": {"body": "provider payload"},
            "api_key": "sk-secret",
        }
    )

    assert dict(request.metadata) == {"source_count": 2}
    assert "confidential customer details" not in str(request.metadata)
    assert "secret prompt" not in str(request.metadata)
    assert "provider payload" not in str(request.metadata)
    assert "sk-secret" not in str(request.metadata)


def test_usage_and_generation_metadata_are_safe_allowlists() -> None:
    usage = TokenUsage(
        input_tokens=3,
        output_tokens=4,
        total_tokens=7,
        content="secret",  # type: ignore[call-arg]
    )
    metadata = GenerationMetadata(
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-1",
        user_id="user-1",
        provider="fake",
        model="fake-llm",
        version="fake-v1",
        usage=usage,
        latency_ms=0.0,
        finish_reason="stop",
        error_code=None,
        token_count=4,
        metadata={
            "raw_response": "provider payload",
            "safe_count": 1,
            "business_summary": "confidential customer details",
        },
    )

    assert metadata.usage.model_dump() == {
        "input_tokens": 3,
        "output_tokens": 4,
        "total_tokens": 7,
    }
    assert dict(metadata.metadata) == {"safe_count": 1}
    dumped = str(metadata.model_dump())
    assert "secret" not in dumped
    assert "provider payload" not in dumped
    assert "confidential customer details" not in dumped


def test_generate_chunk_enforces_final_response_contract() -> None:
    response = _response()

    with pytest.raises(ValidationError):
        GenerateChunk(delta="", index=0, is_final=True)

    with pytest.raises(ValidationError):
        GenerateChunk(delta="partial", index=0, is_final=False, response=response)

    final = GenerateChunk(delta="", index=1, is_final=True, response=response)

    assert final.response == response


def _request(
    *,
    timeout_seconds: float = 1.0,
    retry_budget: int = 0,
    metadata: dict[str, object] | None = None,
) -> GenerateRequest:
    return GenerateRequest(
        messages=(LLMMessage(role="user", name="question", content="What is allowed?"),),
        provider="fake",
        model="fake-llm",
        timeout_seconds=timeout_seconds,
        retry_budget=retry_budget,
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-1",
        user_id="user-1",
        metadata=metadata or {},
    )


def _response() -> GenerateResponse:
    usage = TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2)
    metadata = GenerationMetadata(
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-1",
        user_id="user-1",
        provider="fake",
        model="fake-llm",
        version="fake-v1",
        usage=usage,
        latency_ms=0.0,
        finish_reason="stop",
        error_code=None,
        token_count=1,
    )
    return GenerateResponse(
        text="answer",
        provider="fake",
        model="fake-llm",
        version="fake-v1",
        usage=usage,
        latency_ms=0.0,
        finish_reason="stop",
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-1",
        user_id="user-1",
        error_code=None,
        metadata=metadata,
    )

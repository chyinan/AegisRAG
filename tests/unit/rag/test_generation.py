from __future__ import annotations

import pytest

from packages.auth.context import AuthContext
from packages.common.context import AuthenticatedRequestContext
from packages.llm.adapters.fake import FakeLLMProvider
from packages.llm.dto import GenerateRequest, GenerateResponse
from packages.rag import (
    RAG_GENERATION_FAILED,
    RAG_GENERATION_INVALID_REQUEST,
    PromptBuildResult,
    PromptBuildTrace,
    PromptMessage,
    RagGenerationError,
    RagGenerationService,
)


class RecordingProvider(FakeLLMProvider):
    def __init__(self) -> None:
        super().__init__(response_text="answer")
        self.request: GenerateRequest | None = None

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        self.request = request
        return await super().generate(request)


class MismatchedIdentityProvider(FakeLLMProvider):
    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        response = await super().generate(request)
        return response.model_copy(
            update={
                "tenant_id": "tenant-2",
                "metadata": response.metadata.model_copy(update={"tenant_id": "tenant-2"}),
            }
        )


@pytest.mark.asyncio
async def test_rag_generation_maps_prompt_to_provider_request_with_safe_metadata() -> None:
    provider = RecordingProvider()
    service = RagGenerationService(
        provider=provider,
        provider_name="fake",
        model="fake-llm",
        timeout_seconds=2.0,
        retry_budget=1,
    )

    result = await service.generate(prompt=_prompt(), context=_context())

    assert result.text == "answer"
    assert result.metadata.request_id == "req-1"
    assert result.metadata.trace_id == "trace-1"
    assert result.metadata.tenant_id == "tenant-1"
    assert result.metadata.user_id == "user-1"
    assert result.metadata.provider == "fake"
    assert result.metadata.model == "fake-llm"
    assert result.metadata.usage.total_tokens > 0
    assert result.metadata.error_code is None
    assert provider.request is not None
    assert [message.name for message in provider.request.messages] == ["system", "user_question"]
    assert provider.request.session_id == "session-1"
    assert provider.request.max_output_tokens == 256
    assert dict(provider.request.metadata) == {
        "citation_source_count": 1,
        "prompt_part_count": 2,
        "context_item_count": 1,
        "source_chunk_count": 1,
        "detected_risk_count": 0,
    }
    dumped = str(result.model_dump())
    assert "private question" not in dumped
    assert "authorized context" not in dumped


@pytest.mark.asyncio
async def test_rag_generation_ignores_bool_max_output_tokens() -> None:
    provider = RecordingProvider()
    service = RagGenerationService(provider=provider)

    await service.generate(
        prompt=_prompt(max_output_tokens=True),
        context=_context(),
    )

    assert provider.request is not None
    assert provider.request.max_output_tokens is None


@pytest.mark.asyncio
async def test_rag_generation_fails_closed_on_provider_response_identity_mismatch() -> None:
    service = RagGenerationService(provider=MismatchedIdentityProvider())

    with pytest.raises(RagGenerationError) as exc_info:
        await service.generate(prompt=_prompt(), context=_context())

    assert exc_info.value.code == RAG_GENERATION_FAILED
    assert exc_info.value.details["reason"] == "provider_response_identity_mismatch"
    assert exc_info.value.details["mismatched_fields"] == [
        "tenant_id",
        "metadata.tenant_id",
    ]


@pytest.mark.asyncio
async def test_rag_generation_fails_closed_on_prompt_context_identity_mismatch() -> None:
    service = RagGenerationService(provider=FakeLLMProvider())

    with pytest.raises(RagGenerationError) as exc_info:
        await service.generate(
            prompt=_prompt(trace_tenant_id="tenant-2"),
            context=_context(),
        )

    assert exc_info.value.code == RAG_GENERATION_INVALID_REQUEST
    assert exc_info.value.details["reason"] == "prompt_trace_context_mismatch"
    assert exc_info.value.details["mismatched_fields"] == ["tenant_id"]
    assert "private question" not in str(exc_info.value.details)


def _context() -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="req-1",
        trace_id="trace-1",
        session_id="session-1",
        auth=AuthContext(user_id="user-1", tenant_id="tenant-1"),
    )


def _prompt(
    *,
    trace_tenant_id: str = "tenant-1",
    max_output_tokens: object = 256,
) -> PromptBuildResult:
    return PromptBuildResult(
        messages=(
            PromptMessage(role="system", name="system", content="Use only context."),
            PromptMessage(role="user", name="user_question", content="private question"),
        ),
        trace=PromptBuildTrace(
            request_id="req-1",
            trace_id="trace-1",
            tenant_id=trace_tenant_id,
            user_id="user-1",
            context_item_count=1,
            source_chunk_count=1,
            input_char_count=32,
            prompt_part_count=2,
            detected_risk_count=0,
        ),
        citation_source_ids=("cite-1",),
        metadata={"max_output_tokens": max_output_tokens, "query": "private question"},
    )

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from typing import Literal

from packages.llm.dto import (
    GenerateChunk,
    GenerateChunkMetadata,
    GenerateRequest,
    GenerateResponse,
    GenerationMetadata,
    TokenUsage,
)
from packages.llm.exceptions import (
    LLM_PROVIDER_FAILED,
    LLM_PROVIDER_RATE_LIMITED,
    LLM_PROVIDER_TIMEOUT,
    LLM_STREAM_FAILED,
    LLMProviderError,
)

FailureMode = Literal["timeout", "rate_limited", "failed", "stream_failed"]


class FakeLLMProvider:
    def __init__(
        self,
        *,
        provider: str = "fake",
        model: str = "fake-llm",
        version: str = "fake-v1",
        response_text: str = "Fake LLM response.",
        failure_mode: FailureMode | None = None,
    ) -> None:
        if not response_text.strip():
            raise ValueError("response_text must not be blank")
        self._provider = provider
        self._model = model
        self._version = version
        self._response_text = response_text
        self._failure_mode = failure_mode

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        await asyncio.sleep(0)
        self._raise_if_configured_failure(request)
        return self._response(request)

    async def stream(self, request: GenerateRequest) -> AsyncIterator[GenerateChunk]:
        await asyncio.sleep(0)
        if self._failure_mode == "stream_failed":
            raise self._error(
                code=LLM_STREAM_FAILED,
                message="Fake LLM stream failed.",
                retryable=True,
                request=request,
            )
        self._raise_if_configured_failure(request)
        deltas = self._deltas()
        output_token_count = _token_count(self._response_text)
        for index, delta in enumerate(deltas):
            yield GenerateChunk(
                delta=delta,
                index=index,
                is_final=False,
                metadata=self._chunk_metadata(
                    request,
                    chunk_count=index + 1,
                    token_count=output_token_count,
                ),
            )
        yield GenerateChunk(
            delta="",
            index=len(deltas),
            is_final=True,
            response=self._response(request),
            metadata=self._chunk_metadata(
                request,
                chunk_count=len(deltas) + 1,
                token_count=output_token_count,
            ),
        )

    def _response(self, request: GenerateRequest) -> GenerateResponse:
        usage = TokenUsage(
            input_tokens=sum(_token_count(message.content) for message in request.messages),
            output_tokens=_token_count(self._response_text),
            total_tokens=sum(_token_count(message.content) for message in request.messages)
            + _token_count(self._response_text),
        )
        metadata = GenerationMetadata(
            request_id=request.request_id,
            trace_id=request.trace_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            provider=self._provider,
            model=self._model,
            version=self._version,
            usage=usage,
            latency_ms=0.0,
            finish_reason="stop",
            error_code=None,
            chunk_count=None,
            token_count=usage.output_tokens,
            metadata={
                "message_count": len(request.messages),
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
            },
        )
        return GenerateResponse(
            text=self._response_text,
            provider=self._provider,
            model=self._model,
            version=self._version,
            usage=usage,
            latency_ms=0.0,
            finish_reason="stop",
            request_id=request.request_id,
            trace_id=request.trace_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            error_code=None,
            metadata=metadata,
        )

    def _deltas(self) -> list[str]:
        return re.findall(r"\s+|\S+\s*", self._response_text)

    def _raise_if_configured_failure(self, request: GenerateRequest) -> None:
        if self._failure_mode == "timeout":
            raise self._error(
                code=LLM_PROVIDER_TIMEOUT,
                message="Fake LLM provider timeout.",
                retryable=True,
                request=request,
            )
        if self._failure_mode == "rate_limited":
            raise self._error(
                code=LLM_PROVIDER_RATE_LIMITED,
                message="Fake LLM provider rate limited.",
                retryable=True,
                request=request,
            )
        if self._failure_mode == "failed":
            raise self._error(
                code=LLM_PROVIDER_FAILED,
                message="Fake LLM provider failed.",
                retryable=True,
                request=request,
            )

    def _error(
        self,
        *,
        code: str,
        message: str,
        retryable: bool,
        request: GenerateRequest,
    ) -> LLMProviderError:
        return LLMProviderError(
            code=code,
            message=message,
            retryable=retryable,
            details={
                "provider": self._provider,
                "model": self._model,
                "version": self._version,
            },
        )

    def _chunk_metadata(
        self,
        request: GenerateRequest,
        *,
        chunk_count: int,
        token_count: int,
    ) -> GenerateChunkMetadata:
        return GenerateChunkMetadata(
            request_id=request.request_id,
            trace_id=request.trace_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            provider=self._provider,
            model=self._model,
            version=self._version,
            chunk_count=chunk_count,
            token_count=token_count,
            error_code=None,
        )


def _token_count(text: str) -> int:
    return len(text.split())

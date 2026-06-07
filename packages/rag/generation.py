from __future__ import annotations

from collections.abc import AsyncIterator

from pydantic import BaseModel, ConfigDict

from packages.common.context import AuthenticatedRequestContext
from packages.llm.dto import (
    GenerateChunk,
    GenerateRequest,
    GenerateResponse,
    GenerationMetadata,
    LLMMessage,
)
from packages.llm.ports import LLMProvider
from packages.rag.dto import PromptBuildResult
from packages.rag.exceptions import (
    RAG_GENERATION_FAILED,
    RAG_GENERATION_INVALID_REQUEST,
    RagGenerationError,
)


class RagGenerationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    metadata: GenerationMetadata


class RagGenerationService:
    def __init__(
        self,
        *,
        provider: LLMProvider,
        provider_name: str = "fake",
        model: str = "fake-llm",
        timeout_seconds: float = 10.0,
        retry_budget: int = 2,
        temperature: float | None = None,
    ) -> None:
        self._provider = provider
        self._provider_name = provider_name
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._retry_budget = retry_budget
        self._temperature = temperature

    async def generate(
        self,
        *,
        prompt: PromptBuildResult,
        context: AuthenticatedRequestContext,
    ) -> RagGenerationResult:
        request = self._request(prompt=prompt, context=context)
        response = await self._provider.generate(request)
        self._validate_response_identity(response=response, request=request)
        return RagGenerationResult(text=response.text, metadata=response.metadata)

    def stream(
        self,
        *,
        prompt: PromptBuildResult,
        context: AuthenticatedRequestContext,
    ) -> AsyncIterator[GenerateChunk]:
        return self._validated_stream(self._request(prompt=prompt, context=context))

    def provider_summary(self) -> dict[str, object]:
        return {
            "provider": self._provider_name,
            "model": self._model,
            "version": None,
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
            "latency_ms": None,
            "finish_reason": None,
            "error_code": None,
        }

    async def _validated_stream(self, request: GenerateRequest) -> AsyncIterator[GenerateChunk]:
        seen_final = False
        async for chunk in self._provider.stream(request):
            if seen_final:
                raise RagGenerationError(
                    code=RAG_GENERATION_FAILED,
                    message="RAG generation stream emitted chunks after final response.",
                    details={
                        "reason": "provider_stream_chunk_after_final",
                        "request_id": request.request_id,
                        "trace_id": request.trace_id,
                        "tenant_id": request.tenant_id,
                        "user_id": request.user_id,
                    },
                    status_code=502,
                )
            if chunk.is_final:
                seen_final = True
            self._validate_chunk_identity(chunk=chunk, request=request)
            yield chunk

    def _request(
        self,
        *,
        prompt: PromptBuildResult,
        context: AuthenticatedRequestContext,
    ) -> GenerateRequest:
        self._validate_identity(prompt=prompt, context=context)
        return GenerateRequest(
            messages=tuple(
                LLMMessage(role=message.role, name=message.name, content=message.content)
                for message in prompt.messages
            ),
            provider=self._provider_name,
            model=self._model,
            timeout_seconds=self._timeout_seconds,
            retry_budget=self._retry_budget,
            request_id=context.request_id,
            trace_id=context.trace_id,
            tenant_id=context.auth.tenant_id,
            user_id=context.auth.user_id,
            session_id=context.session_id,
            temperature=self._temperature,
            max_output_tokens=_max_output_tokens(prompt),
            metadata={
                "citation_source_count": len(prompt.citation_source_ids),
                "prompt_part_count": prompt.trace.prompt_part_count,
                "context_item_count": prompt.trace.context_item_count,
                "source_chunk_count": prompt.trace.source_chunk_count,
                "detected_risk_count": prompt.trace.detected_risk_count,
            },
        )

    def _validate_identity(
        self,
        *,
        prompt: PromptBuildResult,
        context: AuthenticatedRequestContext,
    ) -> None:
        expected = {
            "request_id": context.request_id,
            "trace_id": context.trace_id,
            "tenant_id": context.auth.tenant_id,
            "user_id": context.auth.user_id,
        }
        actual = {
            "request_id": prompt.trace.request_id,
            "trace_id": prompt.trace.trace_id,
            "tenant_id": prompt.trace.tenant_id,
            "user_id": prompt.trace.user_id,
        }
        mismatched = [
            key for key, expected_value in expected.items() if actual[key] != expected_value
        ]
        if mismatched:
            raise RagGenerationError(
                code=RAG_GENERATION_INVALID_REQUEST,
                message="RAG generation request is inconsistent with prompt trace.",
                details={
                    "reason": "prompt_trace_context_mismatch",
                    "mismatched_fields": mismatched,
                },
            )

    def _validate_response_identity(
        self,
        *,
        response: GenerateResponse,
        request: GenerateRequest,
    ) -> None:
        mismatched = _identity_mismatches(
            expected={
                "request_id": request.request_id,
                "trace_id": request.trace_id,
                "tenant_id": request.tenant_id,
                "user_id": request.user_id,
                "provider": request.provider,
                "model": request.model,
            },
            actual={
                "request_id": response.request_id,
                "trace_id": response.trace_id,
                "tenant_id": response.tenant_id,
                "user_id": response.user_id,
                "provider": response.provider,
                "model": response.model,
            },
        )
        mismatched.extend(
            f"metadata.{field}"
            for field in _identity_mismatches(
                expected={
                    "request_id": request.request_id,
                    "trace_id": request.trace_id,
                    "tenant_id": request.tenant_id,
                    "user_id": request.user_id,
                    "provider": request.provider,
                    "model": request.model,
                },
                actual={
                    "request_id": response.metadata.request_id,
                    "trace_id": response.metadata.trace_id,
                    "tenant_id": response.metadata.tenant_id,
                    "user_id": response.metadata.user_id,
                    "provider": response.metadata.provider,
                    "model": response.metadata.model,
                },
            )
        )
        if mismatched:
            _raise_provider_identity_mismatch(mismatched)

    def _validate_chunk_identity(
        self,
        *,
        chunk: GenerateChunk,
        request: GenerateRequest,
    ) -> None:
        mismatched: list[str] = []
        if chunk.metadata is not None:
            mismatched.extend(
                f"chunk.metadata.{field}"
                for field in _identity_mismatches(
                    expected={
                        "request_id": request.request_id,
                        "trace_id": request.trace_id,
                        "tenant_id": request.tenant_id,
                        "user_id": request.user_id,
                        "provider": request.provider,
                        "model": request.model,
                    },
                    actual={
                        "request_id": chunk.metadata.request_id,
                        "trace_id": chunk.metadata.trace_id,
                        "tenant_id": chunk.metadata.tenant_id,
                        "user_id": chunk.metadata.user_id,
                        "provider": chunk.metadata.provider,
                        "model": chunk.metadata.model,
                    },
                )
            )
        if chunk.response is not None:
            try:
                self._validate_response_identity(response=chunk.response, request=request)
            except RagGenerationError as exc:
                response_mismatches = exc.details.get("mismatched_fields", [])
                if isinstance(response_mismatches, list):
                    mismatched.extend(f"chunk.response.{field}" for field in response_mismatches)
                else:
                    mismatched.append("chunk.response")
        if mismatched:
            _raise_provider_identity_mismatch(mismatched)


def _max_output_tokens(prompt: PromptBuildResult) -> int | None:
    value = prompt.metadata.get("max_output_tokens")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _identity_mismatches(
    *,
    expected: dict[str, str],
    actual: dict[str, str],
) -> list[str]:
    return [field for field, expected_value in expected.items() if actual[field] != expected_value]


def _raise_provider_identity_mismatch(mismatched: list[str]) -> None:
    raise RagGenerationError(
        code=RAG_GENERATION_FAILED,
        message="LLM provider response identity does not match generation request.",
        details={
            "reason": "provider_response_identity_mismatch",
            "mismatched_fields": mismatched,
        },
        status_code=502,
    )

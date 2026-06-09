from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, cast
from urllib.parse import urljoin

import httpx

from packages.llm.dto import (
    GenerateChunk,
    GenerateChunkMetadata,
    GenerateRequest,
    GenerateResponse,
    GenerationMetadata,
    TokenUsage,
)
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


class OpenAICompatibleChatProvider:
    def __init__(
        self,
        *,
        provider: str,
        model: str,
        base_url: str,
        api_key: str,
        version: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._provider = _required_text(provider, "provider")
        self._model = _required_text(model, "model")
        self._base_url = _required_text(base_url, "base_url").rstrip("/") + "/"
        self._api_key = _required_text(api_key, "api_key")
        self._version = version.strip() if version and version.strip() else None
        self._client = client

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        started = time.perf_counter()
        payload = _request_body(request, stream=False)
        response = await self._post_with_retries(request=request, payload=payload)
        body = _json_response(response=response, request=request, stream=False)
        text, finish_reason, usage, usage_unavailable_count = _parse_generate_body(
            body=body,
            request=request,
        )
        return self._response(
            request=request,
            text=text,
            usage=usage,
            finish_reason=finish_reason,
            started=started,
            chunk_count=None,
            usage_unavailable_count=usage_unavailable_count,
        )

    async def stream(self, request: GenerateRequest) -> AsyncIterator[GenerateChunk]:
        started = time.perf_counter()
        payload = _request_body(request, stream=True)
        chunks: list[str] = []
        usage = TokenUsage()
        usage_unavailable_count = 1
        finish_reason = "unknown"
        chunk_index = 0
        try:
            async with self._stream_response(request=request, payload=payload) as response:
                _raise_for_status(response=response, request=request, stream=True)
                async for line in response.aiter_lines():
                    event_data = _sse_data(line)
                    if event_data is None:
                        continue
                    if event_data == "[DONE]":
                        break
                    body = _json_sse_payload(data=event_data, request=request)
                    body_usage = _usage_from_body(body)
                    if body_usage is not None:
                        usage = body_usage
                        usage_unavailable_count = 0
                    for choice in _choices(body):
                        delta = _choice_delta_content(choice)
                        choice_finish_reason = _choice_finish_reason(choice)
                        if choice_finish_reason:
                            finish_reason = choice_finish_reason
                        if not delta:
                            continue
                        chunks.append(delta)
                        yield GenerateChunk(
                            delta=delta,
                            index=chunk_index,
                            is_final=False,
                            metadata=self._chunk_metadata(
                                request,
                                chunk_count=chunk_index + 1,
                                token_count=sum(_token_count(part) for part in chunks),
                            ),
                        )
                        chunk_index += 1
        except LLMProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise self._error(
                request=request,
                code=LLM_PROVIDER_TIMEOUT,
                message="LLM provider timed out.",
                retryable=True,
                status_code=504,
            ) from exc
        except httpx.HTTPError as exc:
            raise self._error(
                request=request,
                code=LLM_STREAM_FAILED,
                message="LLM provider stream failed.",
                retryable=True,
                status_code=502,
            ) from exc
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise self._error(
                request=request,
                code=LLM_STREAM_FAILED,
                message="LLM provider stream failed.",
                retryable=True,
                status_code=502,
            ) from exc

        text = "".join(chunks)
        provider_response = self._response(
            request=request,
            text=text,
            usage=usage,
            finish_reason=finish_reason,
            started=started,
            chunk_count=chunk_index + 1,
            usage_unavailable_count=usage_unavailable_count,
        )
        yield GenerateChunk(
            delta="",
            index=chunk_index,
            is_final=True,
            response=provider_response,
            metadata=self._chunk_metadata(
                request,
                chunk_count=chunk_index + 1,
                token_count=usage.output_tokens,
            ),
        )

    async def _post_with_retries(
        self,
        *,
        request: GenerateRequest,
        payload: Mapping[str, object],
    ) -> httpx.Response:
        attempts = request.retry_budget + 1
        last_error: LLMProviderError | None = None
        for attempt in range(attempts):
            try:
                response = await self._post_once(request=request, payload=payload)
                _raise_for_status(response=response, request=request, stream=False)
                return response
            except LLMProviderError as exc:
                last_error = exc
                if not exc.retryable or attempt >= attempts - 1:
                    raise
            except httpx.TimeoutException as exc:
                last_error = self._error(
                    request=request,
                    code=LLM_PROVIDER_TIMEOUT,
                    message="LLM provider timed out.",
                    retryable=True,
                    status_code=504,
                )
                if attempt >= attempts - 1:
                    raise last_error from exc
            except httpx.HTTPError as exc:
                last_error = self._error(
                    request=request,
                    code=LLM_PROVIDER_FAILED,
                    message="LLM provider request failed.",
                    retryable=True,
                    status_code=502,
                )
                if attempt >= attempts - 1:
                    raise last_error from exc
        if last_error is not None:
            raise last_error
        raise self._error(
            request=request,
            code=LLM_PROVIDER_FAILED,
            message="LLM provider request failed.",
            retryable=True,
            status_code=502,
        )

    async def _post_once(
        self,
        *,
        request: GenerateRequest,
        payload: Mapping[str, object],
    ) -> httpx.Response:
        if self._client is not None:
            return await self._client.post(
                self._endpoint(),
                json=payload,
                headers=self._headers(),
                timeout=request.timeout_seconds,
            )
        async with httpx.AsyncClient(timeout=request.timeout_seconds) as client:
            return await client.post(
                self._endpoint(),
                json=payload,
                headers=self._headers(),
                timeout=request.timeout_seconds,
            )

    @asynccontextmanager
    async def _stream_response(
        self,
        *,
        request: GenerateRequest,
        payload: Mapping[str, object],
    ) -> AsyncIterator[httpx.Response]:
        if self._client is not None:
            async with self._client.stream(
                "POST",
                self._endpoint(),
                json=payload,
                headers=self._headers(),
                timeout=request.timeout_seconds,
            ) as response:
                yield response
            return
        async with httpx.AsyncClient(timeout=request.timeout_seconds) as client:
            async with client.stream(
                "POST",
                self._endpoint(),
                json=payload,
                headers=self._headers(),
                timeout=request.timeout_seconds,
            ) as response:
                yield response

    def _endpoint(self) -> str:
        return urljoin(self._base_url, "chat/completions")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    def _response(
        self,
        *,
        request: GenerateRequest,
        text: str,
        usage: TokenUsage,
        finish_reason: str,
        started: float,
        chunk_count: int | None,
        usage_unavailable_count: int,
    ) -> GenerateResponse:
        latency_ms = (time.perf_counter() - started) * 1000
        metadata = GenerationMetadata(
            request_id=request.request_id,
            trace_id=request.trace_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            provider=self._provider,
            model=self._model,
            version=self._version,
            usage=usage,
            latency_ms=latency_ms,
            finish_reason=finish_reason or "unknown",
            error_code=None,
            chunk_count=chunk_count,
            token_count=usage.output_tokens,
            metadata={
                "message_count": len(request.messages),
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
                "usage_unavailable_count": usage_unavailable_count,
            },
        )
        return GenerateResponse(
            text=text,
            provider=self._provider,
            model=self._model,
            version=self._version,
            usage=usage,
            latency_ms=latency_ms,
            finish_reason=finish_reason or "unknown",
            request_id=request.request_id,
            trace_id=request.trace_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            error_code=None,
            metadata=metadata,
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

    def _error(
        self,
        *,
        request: GenerateRequest,
        code: str,
        message: str,
        retryable: bool,
        status_code: int,
    ) -> LLMProviderError:
        return LLMProviderError(
            code=code,
            message=message,
            retryable=retryable,
            status_code=status_code,
            details={
                "request_id": request.request_id,
                "trace_id": request.trace_id,
                "tenant_id": request.tenant_id,
                "user_id": request.user_id,
                "provider": self._provider,
                "model": self._model,
                "version": self._version,
                "error_code": code,
            },
        )


def _request_body(request: GenerateRequest, *, stream: bool) -> dict[str, object]:
    body: dict[str, object] = {
        "model": request.model,
        "messages": [
            {"role": message.role, "content": message.content}
            for message in request.messages
        ],
        "stream": stream,
    }
    if request.temperature is not None:
        body["temperature"] = request.temperature
    if request.max_output_tokens is not None:
        body["max_tokens"] = request.max_output_tokens
    if stream and request.stream_options:
        body["stream_options"] = dict(request.stream_options)
    return body


def _json_response(
    *,
    response: httpx.Response,
    request: GenerateRequest,
    stream: bool,
) -> Mapping[str, object]:
    try:
        body = response.json()
    except json.JSONDecodeError as exc:
        raise _malformed_error(request=request, stream=stream) from exc
    if not isinstance(body, Mapping):
        raise _malformed_error(request=request, stream=stream)
    return cast(Mapping[str, object], body)


def _parse_generate_body(
    *,
    body: Mapping[str, object],
    request: GenerateRequest,
) -> tuple[str, str, TokenUsage, int]:
    choices = _choices(body)
    if not choices:
        raise _malformed_error(request=request, stream=False)
    first = choices[0]
    message = first.get("message")
    if not isinstance(message, Mapping):
        raise _malformed_error(request=request, stream=False)
    content = message.get("content")
    if content is None:
        text = ""
    elif isinstance(content, str):
        text = content
    else:
        raise _malformed_error(request=request, stream=False)
    finish_reason = _choice_finish_reason(first) or "unknown"
    usage = _usage_from_body(body)
    if usage is None:
        return text, finish_reason, TokenUsage(), 1
    return text, finish_reason, usage, 0


def _choices(body: Mapping[str, object]) -> list[Mapping[str, object]]:
    raw_choices = body.get("choices")
    if not isinstance(raw_choices, list):
        return []
    return [cast(Mapping[str, object], item) for item in raw_choices if isinstance(item, Mapping)]


def _choice_finish_reason(choice: Mapping[str, object]) -> str | None:
    value = choice.get("finish_reason")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _choice_delta_content(choice: Mapping[str, object]) -> str:
    delta = choice.get("delta")
    if not isinstance(delta, Mapping):
        return ""
    content = delta.get("content")
    if isinstance(content, str):
        return content
    return ""


def _usage_from_body(body: Mapping[str, object]) -> TokenUsage | None:
    usage = body.get("usage")
    if not isinstance(usage, Mapping):
        return None
    prompt_tokens = _safe_int(usage.get("prompt_tokens"))
    completion_tokens = _safe_int(usage.get("completion_tokens"))
    total_tokens = _safe_int(usage.get("total_tokens"))
    return TokenUsage(
        input_tokens=prompt_tokens,
        output_tokens=completion_tokens,
        total_tokens=total_tokens,
    )


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    return 0


def _raise_for_status(
    *,
    response: httpx.Response,
    request: GenerateRequest,
    stream: bool,
) -> None:
    status_code = response.status_code
    if status_code < 400:
        return
    if status_code == 429:
        code = LLM_PROVIDER_RATE_LIMITED
        retryable = True
        message = "LLM provider rate limited the request."
    elif status_code in {401, 403}:
        code = LLM_PROVIDER_AUTH_FAILED
        retryable = False
        message = "LLM provider authentication failed."
    elif status_code == 400:
        code = LLM_GENERATION_INVALID_REQUEST
        retryable = False
        message = "LLM provider rejected the generation request."
    else:
        code = LLM_STREAM_FAILED if stream else LLM_PROVIDER_FAILED
        retryable = status_code >= 500
        message = "LLM provider request failed."
    raise LLMProviderError(
        code=code,
        message=message,
        retryable=retryable,
        status_code=502 if status_code >= 500 else status_code,
        details={
            "request_id": request.request_id,
            "trace_id": request.trace_id,
            "tenant_id": request.tenant_id,
            "user_id": request.user_id,
            "provider": request.provider,
            "model": request.model,
            "error_code": code,
        },
    )


def _malformed_error(*, request: GenerateRequest, stream: bool) -> LLMProviderError:
    code = LLM_STREAM_FAILED if stream else LLM_PROVIDER_MALFORMED_RESPONSE
    return LLMProviderError(
        code=code,
        message="LLM provider returned a malformed response.",
        retryable=stream,
        status_code=502,
        details={
            "request_id": request.request_id,
            "trace_id": request.trace_id,
            "tenant_id": request.tenant_id,
            "user_id": request.user_id,
            "provider": request.provider,
            "model": request.model,
            "error_code": code,
        },
    )


def _sse_data(line: str) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith(":"):
        return None
    if not stripped.startswith("data:"):
        return None
    return stripped.removeprefix("data:").strip()


def _json_sse_payload(*, data: str, request: GenerateRequest) -> Mapping[str, object]:
    try:
        body = json.loads(data)
    except json.JSONDecodeError as exc:
        raise _malformed_error(request=request, stream=True) from exc
    if not isinstance(body, Mapping):
        raise _malformed_error(request=request, stream=True)
    return cast(Mapping[str, object], body)


def _required_text(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def _token_count(text: str) -> int:
    return len(text.split())

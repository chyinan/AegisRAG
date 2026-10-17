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
from packages.providers.runtime import (
    ProviderCapacityError,
    ProviderDeadline,
    ProviderLease,
    ProviderRuntime,
    ProviderRuntimeClosedError,
    positive_seconds,
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
        runtime: ProviderRuntime | None = None,
        total_timeout_seconds: float | None = None,
    ) -> None:
        self._provider = _required_text(provider, "provider")
        self._model = _required_text(model, "model")
        self._base_url = _required_text(base_url, "base_url").rstrip("/") + "/"
        self._api_key = _required_text(api_key, "api_key")
        self._version = version.strip() if version and version.strip() else None
        self._client = client
        self._runtime = runtime or ProviderRuntime(client=client)
        self._owns_runtime = runtime is None
        self._total_timeout_seconds = (
            positive_seconds(total_timeout_seconds, "total timeout")
            if total_timeout_seconds is not None
            else None
        )

    async def aclose(self) -> None:
        if self._owns_runtime:
            await self._runtime.aclose()

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        started = time.perf_counter()
        deadline = self._deadline(request)
        payload = _request_body(request, stream=False)
        response = await self._post_with_retries(
            request=request, payload=payload, deadline=deadline
        )
        body = _json_response(response=response, request=request, stream=False)
        text, finish_reason, usage, usage_unavailable_count = _parse_generate_body(
            body=body,
            request=request,
        )
        result = self._response(
            request=request,
            text=text,
            usage=usage,
            finish_reason=finish_reason,
            started=started,
            chunk_count=None,
            usage_unavailable_count=usage_unavailable_count,
        )
        try:
            deadline.remaining()
        except TimeoutError as exc:
            raise self._timeout_error(request) from exc
        return result

    async def stream(self, request: GenerateRequest) -> AsyncIterator[GenerateChunk]:
        started = time.perf_counter()
        payload = _request_body(request, stream=True)
        deadline = self._deadline(request)
        lease = None
        chunks: list[str] = []
        chunk_index = 0
        usage = TokenUsage()
        usage_unavailable_count = 1
        finish_reason = "unknown"
        try:
            lease = await self._runtime.acquire(deadline)
            for attempt in range(request.retry_budget + 1):
                response = None
                retryable = False
                try:
                    # Keep timeout scopes within an await, never across yield:
                    # cancelling an asyncio.timeout across yield cancels callers.
                    async with self._stream_response(
                        request=request, payload=payload, deadline=deadline, lease=lease
                    ) as response:
                        _raise_for_status(response=response, request=request, stream=True)
                        lines = response.aiter_lines()
                        while True:
                            try:
                                line = await deadline.wait(anext(lines))
                            except StopAsyncIteration:
                                break
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
                                deadline.remaining()
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
                                deadline.remaining()
                    break
                except LLMProviderError as exc:
                    error = exc
                    # Malformed SSE is not a transport retry. HTTP failures may
                    # be retried only before any token has escaped to the user.
                    retryable = (
                        exc.retryable and response is not None and response.status_code >= 400
                    )
                except (httpx.TimeoutException, TimeoutError) as exc:
                    error = self._timeout_error(request)
                    error.__cause__ = exc
                    retryable = True
                except httpx.HTTPError as exc:
                    error = self._error(
                        request=request,
                        code=LLM_STREAM_FAILED,
                        message="LLM provider stream failed.",
                        retryable=True,
                        status_code=502,
                    )
                    error.__cause__ = exc
                    retryable = True
                except (json.JSONDecodeError, ValueError, TypeError) as exc:
                    error = self._error(
                        request=request,
                        code=LLM_STREAM_FAILED,
                        message="LLM provider stream failed.",
                        retryable=True,
                        status_code=502,
                    )
                    error.__cause__ = exc
                if chunks or not retryable or attempt >= request.retry_budget:
                    raise error
                usage = TokenUsage()
                usage_unavailable_count = 1
                finish_reason = "unknown"
                await self._runtime.backoff(attempt=attempt, deadline=deadline, response=response)
        except (ProviderCapacityError, ProviderRuntimeClosedError) as exc:
            raise self._capacity_error(request) from exc
        except TimeoutError as exc:
            raise self._timeout_error(request) from exc
        finally:
            if lease is not None:
                lease.release()

        try:
            deadline.remaining()
        except TimeoutError as exc:
            raise self._timeout_error(request) from exc
        provider_response = self._response(
            request=request,
            text="".join(chunks),
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

    def _deadline(self, request: GenerateRequest) -> ProviderDeadline:
        return self._runtime.deadline(
            timeout_seconds=request.timeout_seconds,
            retry_budget=request.retry_budget,
            total_timeout_seconds=self._total_timeout_seconds,
        )

    async def _post_with_retries(
        self,
        *,
        request: GenerateRequest,
        payload: Mapping[str, object],
        deadline: ProviderDeadline,
    ) -> httpx.Response:
        lease = None
        try:
            lease = await self._runtime.acquire(deadline)
            attempt = 0
            while True:
                response = None
                try:
                    response = await deadline.wait(
                        self._post_once(request=request, payload=payload)
                    )
                    _raise_for_status(response=response, request=request, stream=False)
                    return response
                except LLMProviderError as exc:
                    error = exc
                    if not exc.retryable:
                        raise
                except (httpx.TimeoutException, TimeoutError) as exc:
                    error = self._timeout_error(request)
                    error.__cause__ = exc
                except httpx.HTTPError as exc:
                    error = self._error(
                        request=request,
                        code=LLM_PROVIDER_FAILED,
                        message="LLM provider request failed.",
                        retryable=True,
                        status_code=502,
                    )
                    error.__cause__ = exc
                if attempt >= request.retry_budget:
                    raise error
                await self._runtime.backoff(attempt=attempt, deadline=deadline, response=response)
                attempt += 1
        except (ProviderCapacityError, ProviderRuntimeClosedError) as exc:
            raise self._capacity_error(request) from exc
        except TimeoutError as exc:
            raise self._timeout_error(request) from exc
        finally:
            if lease is not None:
                lease.release()

    @asynccontextmanager
    async def _http_client(self) -> AsyncIterator[tuple[httpx.AsyncClient, bool]]:
        self._runtime.ensure_open()
        if self._client is not None:
            yield self._client, False
        elif not self._owns_runtime:
            yield self._runtime.client, False
        else:
            # Direct/worker callers historically have no lifecycle close hook.
            client = httpx.AsyncClient()
            try:
                yield client, True
            finally:
                await self._runtime.close_owned_client(client)

    async def _post_once(
        self,
        *,
        request: GenerateRequest,
        payload: Mapping[str, object],
    ) -> httpx.Response:
        async with self._http_client() as (client, _owned):
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
        deadline: ProviderDeadline,
        lease: ProviderLease,
    ) -> AsyncIterator[httpx.Response]:
        self._runtime.ensure_open()
        owned = self._client is None and self._owns_runtime
        if owned:
            client = httpx.AsyncClient()
        else:
            client = self._client if self._client is not None else self._runtime.client
        context = client.stream(
            "POST",
            self._endpoint(),
            json=payload,
            headers=self._headers(),
            timeout=request.timeout_seconds,
        )
        try:
            response = await deadline.wait(context.__aenter__())
        except BaseException:
            if owned:
                await self._runtime.close_owned_client(client)
            raise
        guard = self._runtime.guard_stream(
            response=response,
            lease=lease,
            deadline=deadline,
            owned_client=client if owned else None,
        )
        try:
            yield response
        finally:
            await guard.aclose(release=False)
            await context.__aexit__(None, None, None)

    def _timeout_error(self, request: GenerateRequest) -> LLMProviderError:
        return self._error(
            request=request,
            code=LLM_PROVIDER_TIMEOUT,
            message="LLM provider timed out.",
            retryable=True,
            status_code=504,
        )

    def _capacity_error(self, request: GenerateRequest) -> LLMProviderError:
        return self._error(
            request=request,
            code=LLM_PROVIDER_RATE_LIMITED,
            message="LLM provider capacity is unavailable.",
            retryable=True,
            status_code=503,
        )

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
            {"role": message.role, "content": message.content} for message in request.messages
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

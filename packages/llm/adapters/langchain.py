"""Optional LangChain boundary for AegisRAG's text-only generation contract.

Framework objects and callbacks never cross this boundary. The optional decision
port accepts one native tool call; generation remains strictly text-only. Tool
execution and the agent loop always belong to the application runtime.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from contextvars import Context
from types import SimpleNamespace
from typing import Any

import httpx
from pydantic import ValidationError

from packages.common.framework_context import isolated_framework_context
from packages.llm.decisions import (
    FINAL_ANSWER_TOOL,
    DecisionRequest,
    DecisionResponse,
)
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


class LangChainChatProvider:
    """Use ``ainvoke``/``astream`` without changing the application's DTOs.

    ``chat_model`` is an injection seam for a configured LangChain BaseChatModel.
    Its SDK retries must be disabled so only ``request.retry_budget`` is used.
    Each attempt is bounded by ``request.timeout_seconds``, matching native
    provider retry semantics. No global tracing, cache or environment is changed.
    """

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        base_url: str,
        api_key: str,
        version: str | None = None,
        chat_model: Any = None,
    ) -> None:
        self._provider = _required_text(provider, "provider")
        self._model = _required_text(model, "model")
        self._version = version.strip() if version and version.strip() else None
        base_url = _required_text(base_url, "base_url")
        api_key = _required_text(api_key, "api_key")
        try:
            from langchain_core.language_models.chat_models import BaseChatModel
            from langchain_openai import ChatOpenAI
            from langsmith import tracing_context
        except ImportError:
            raise RuntimeError(
                "LangChain chat adapter requires the optional framework dependencies; "
                "install the 'langchain' extra."
            ) from None
        self._tracing_context = tracing_context
        self._chat_model_type = ChatOpenAI
        self._chat_model = chat_model
        self._model_options: dict[str, Any] = {}
        if chat_model is None:
            self._model_options = {
                "model": self._model,
                "base_url": base_url,
                "api_key": api_key,
                "max_retries": 0,
                "timeout": 60.0,
                "cache": False,
                "callbacks": None,
                "verbose": False,
                "stream_usage": True,
                "use_responses_api": False,
            }
            return
        if not isinstance(chat_model, BaseChatModel):
            raise ValueError("chat_model must implement LangChain BaseChatModel")
        configured_model = getattr(chat_model, "model_name", getattr(chat_model, "model", None))
        if isinstance(configured_model, str) and configured_model != self._model:
            raise ValueError("chat_model model does not match configured model")
        if getattr(chat_model, "max_retries", 0) != 0:
            raise ValueError("chat_model must have max_retries=0")
        if getattr(chat_model, "callbacks", None):
            raise ValueError("chat_model must not have callbacks configured")
        if getattr(chat_model, "cache", None) not in (None, False):
            raise ValueError("chat_model must not have caching enabled")
        # A copy avoids inherited/global cache and verbose handlers without
        # mutating a caller-owned framework object.
        self._chat_model = chat_model.model_copy(update={"cache": False, "verbose": False})

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        self._validate_request(request)
        started = time.perf_counter()
        messages = self._messages(request)
        for attempt in range(request.retry_budget + 1):
            failure: LLMProviderError | None = None
            try:
                with self._tracing_context(enabled=False):
                    async with asyncio.timeout(request.timeout_seconds):
                        async with self._model_context() as model:
                            message = await self._call_untraced(
                                lambda: model.ainvoke(
                                    messages, config={"callbacks": []}, **self._options(request)
                                )
                            )
                text, finish_reason, usage, unavailable = self._parse_message(message, request)
                return self._response(
                    request, text, finish_reason, usage, unavailable, started, None
                )
            except Exception as exc:
                failure = self._translate_error(exc, request, stream=False)
            if not failure.retryable or attempt == request.retry_budget:
                raise failure from None
        raise AssertionError("unreachable retry state")

    async def decide(self, request: DecisionRequest) -> DecisionResponse:
        """Bind declarative schemas and return one decision without executing it."""
        self._validate_request(request)
        started = time.perf_counter()
        messages = self._messages(request)
        schemas = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in request.tools
        ]
        schemas.append(
            {
                "type": "function",
                "function": {
                    "name": FINAL_ANSWER_TOOL,
                    "description": "Submit the final answer and evidence citations for validation.",
                    "parameters": request.final_answer_schema,
                },
            }
        )
        for attempt in range(request.retry_budget + 1):
            failure: LLMProviderError | None = None
            try:
                with self._tracing_context(enabled=False):
                    async with asyncio.timeout(request.timeout_seconds):
                        async with self._model_context() as model:

                            async def invoke() -> Any:
                                # Binding itself is inside the private framework context.
                                decision_model = self._decision_model(model, request)
                                bound = decision_model.bind_tools(
                                    schemas, tool_choice="required", parallel_tool_calls=False
                                )
                                return await bound.ainvoke(
                                    messages, config={"callbacks": []}, **self._options(request)
                                )

                            message = await self._call_untraced(invoke)
                name, arguments = self._parse_decision(message, request)
                reason, usage, unavailable = self._parse_response_metadata(
                    message, request, allow_tool_calls=True
                )
                metadata = self._response(
                    request, "", reason, usage, unavailable, started, None
                ).metadata
                if name == FINAL_ANSWER_TOOL:
                    return DecisionResponse(
                        action="final_answer", final_answer=arguments, metadata=metadata
                    )
                return DecisionResponse(
                    action="tool_call", tool_name=name, arguments=arguments, metadata=metadata
                )
            except ValidationError:
                failure = self._malformed(request)
            except Exception as exc:
                failure = self._translate_error(exc, request, stream=False)
            if not failure.retryable or attempt == request.retry_budget:
                raise failure from None
        raise AssertionError("unreachable retry state")

    def _decision_model(self, model: Any, request: DecisionRequest) -> Any:
        if not isinstance(model, self._chat_model_type):
            # Other injected BaseChatModels already expose normalized calls.
            # Their provider implementation owns validation before normalization.
            return model
        if (
            model.use_responses_api is True
            or model.output_version == "responses/v1"
            or "response_format" in model.model_kwargs
        ):
            raise self._error(
                request,
                LLM_GENERATION_INVALID_REQUEST,
                "Native decisions require the chat-completions tool-call API.",
                False,
                400,
            )
        # ChatOpenAI normalizes argument JSON and discards raw tool_calls. A
        # request-local resource wrapper validates them before that lossy step.
        # Copying avoids changing the caller's model or the text generation path.
        return model.model_copy(
            update={
                "async_client": _DecisionAsyncClient(
                    model.async_client,
                    validate=lambda raw: self._validate_raw_decision(raw, request),
                    malformed=lambda: self._malformed(request),
                ),
                "use_responses_api": False,
                "streaming": False,
                "disable_streaming": True,
            }
        )

    def _validate_raw_decision(self, response: Any, request: DecisionRequest) -> None:
        try:
            body = (
                response if isinstance(response, Mapping) else response.model_dump(warnings=False)
            )
        except Exception:
            raise self._malformed(request) from None
        if not isinstance(body, Mapping):
            raise self._malformed(request)
        choices = body.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise self._malformed(request)
        choice = choices[0]
        if not isinstance(choice, Mapping):
            raise self._malformed(request)
        message = choice.get("message")
        if not isinstance(message, Mapping) or message.get("role") != "assistant":
            raise self._malformed(request)
        content = message.get("content")
        if content is not None and (not isinstance(content, str) or content.strip()):
            raise self._malformed(request)
        if any(
            value for key, value in message.items() if key not in {"role", "content", "tool_calls"}
        ):
            raise self._malformed(request)
        calls = message.get("tool_calls")
        if not isinstance(calls, list) or len(calls) != 1:
            raise self._malformed(request)
        call = calls[0]
        if not isinstance(call, Mapping) or call.get("type") != "function":
            raise self._malformed(request)
        function = call.get("function")
        if not isinstance(function, Mapping):
            raise self._malformed(request)
        name, call_id, raw_arguments = (
            function.get("name"),
            call.get("id"),
            function.get("arguments"),
        )
        if (
            not isinstance(name, str)
            or name not in {tool.name for tool in request.tools} | {FINAL_ANSWER_TOOL}
            or not isinstance(call_id, str)
            or not call_id.strip()
            or not isinstance(raw_arguments, str)
        ):
            raise self._malformed(request)
        try:
            arguments = json.loads(raw_arguments, object_pairs_hook=_unique_json_object)
            if not isinstance(arguments, dict):
                raise ValueError
            # Also reject JSON's nonstandard constants and exponent overflow.
            json.dumps(arguments, allow_nan=False)
        except (TypeError, ValueError):
            raise self._malformed(request) from None
        reason = choice.get("finish_reason")
        if reason is not None and not isinstance(reason, str):
            raise self._malformed(request)
        if reason in {"length", "content_filter"}:
            raise self._malformed(request)
        # Validate raw counts before typed SDK/framework models can coerce them.
        self._parse_response_metadata(
            SimpleNamespace(response_metadata={"token_usage": body.get("usage")}), request
        )

    def _parse_decision(self, message: Any, request: DecisionRequest) -> tuple[str, dict[str, Any]]:
        from langchain_core.messages import AIMessage, AIMessageChunk

        additional = getattr(message, "additional_kwargs", {})
        calls = getattr(message, "tool_calls", None)
        if (
            not isinstance(message, AIMessage)
            or isinstance(message, AIMessageChunk)
            or not isinstance(message.content, str)
            or message.content.strip()
            or not isinstance(additional, Mapping)
            or any(value for key, value in additional.items() if key != "tool_calls")
            or getattr(message, "invalid_tool_calls", None)
            or not isinstance(calls, list)
            or len(calls) != 1
        ):
            raise self._malformed(request)
        call = calls[0]
        if not isinstance(call, Mapping):
            raise self._malformed(request)
        name, arguments, call_id = call.get("name"), call.get("args"), call.get("id")
        allowed = {tool.name for tool in request.tools} | {FINAL_ANSWER_TOOL}
        if (
            not isinstance(name, str)
            or name not in allowed
            or not isinstance(arguments, dict)
            or not isinstance(call_id, str)
            or not call_id.strip()
        ):
            raise self._malformed(request)
        # Some SDK versions normalize permissive/partial JSON. If raw calls are
        # present, require an exact JSON object and agreement with the normalized call.
        raw_calls = additional.get("tool_calls")
        if raw_calls is not None:
            try:
                if not isinstance(raw_calls, list) or len(raw_calls) != 1:
                    raise ValueError
                raw = raw_calls[0]
                function = raw["function"]
                if (
                    raw["type"] != "function"
                    or raw["id"] != call_id
                    or function["name"] != name
                    or json.loads(function["arguments"], object_pairs_hook=_unique_json_object)
                    != arguments
                ):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                raise self._malformed(request) from None
        try:
            # Reject arbitrary objects and non-finite values before they reach
            # the domain boundary, even for caller-injected test models.
            json.dumps(arguments, allow_nan=False)
        except (TypeError, ValueError):
            raise self._malformed(request) from None
        metadata = getattr(message, "response_metadata", {})
        if not isinstance(metadata, Mapping):
            raise self._malformed(request)
        reason = metadata.get("finish_reason")
        if reason is not None and not isinstance(reason, str):
            raise self._malformed(request)
        if reason in {"length", "content_filter"}:
            raise self._malformed(request)
        return name, arguments

    async def stream(self, request: GenerateRequest) -> AsyncIterator[GenerateChunk]:
        self._validate_request(request)
        started = time.perf_counter()
        messages = self._messages(request)
        for attempt in range(request.retry_budget + 1):
            deltas: list[str] = []
            usage = TokenUsage()
            unavailable = 1
            finish_reason = "unknown"
            iterator: Any = None
            framework_context = isolated_framework_context()
            failure: LLMProviderError | None = None
            deadline = time.monotonic() + request.timeout_seconds
            try:
                async with self._model_context() as model:
                    try:
                        with self._tracing_context(enabled=False):
                            iterator = model.astream(
                                messages,
                                config={"callbacks": []},
                                **self._options(request, stream=True),
                            ).__aiter__()
                        while True:
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise TimeoutError
                            try:
                                with self._tracing_context(enabled=False):
                                    message = await asyncio.wait_for(
                                        self._call_untraced(iterator.__anext__, framework_context),
                                        timeout=remaining,
                                    )
                            except StopAsyncIteration:
                                break
                            delta, reason, chunk_usage, chunk_unavailable = self._parse_message(
                                message, request
                            )
                            if reason != "unknown":
                                finish_reason = reason
                            if not chunk_unavailable:
                                # LangChain UsageMetadata on streamed chunks is incremental.
                                usage = TokenUsage(
                                    input_tokens=usage.input_tokens + chunk_usage.input_tokens,
                                    output_tokens=usage.output_tokens + chunk_usage.output_tokens,
                                    total_tokens=usage.total_tokens + chunk_usage.total_tokens,
                                )
                                unavailable = 0
                            if delta:
                                deltas.append(delta)
                                yield GenerateChunk(
                                    delta=delta,
                                    index=len(deltas) - 1,
                                    metadata=self._chunk_metadata(
                                        request,
                                        len(deltas),
                                        sum(len(part.split()) for part in deltas),
                                    ),
                                )
                    finally:
                        if iterator is not None:
                            # Explicit close propagates cancellation/consumer aclose to the SDK.
                            try:
                                with self._tracing_context(enabled=False):
                                    async with asyncio.timeout(min(request.timeout_seconds, 1.0)):
                                        await self._call_untraced(
                                            iterator.aclose, framework_context, cleanup=True
                                        )
                            except Exception as exc:
                                # Cleanup failures are translated too; cancellation and
                                # GeneratorExit still propagate through this finally.
                                if failure is None:
                                    failure = self._translate_error(exc, request, stream=True)
            except Exception as exc:
                failure = self._translate_error(exc, request, stream=True)
            if failure is not None:
                # Replaying after a visible delta would duplicate user-visible output.
                if deltas or not failure.retryable or attempt == request.retry_budget:
                    raise failure from None
                continue
            response = self._response(
                request,
                "".join(deltas),
                finish_reason,
                usage,
                unavailable,
                started,
                len(deltas) + 1,
            )
            yield GenerateChunk(
                delta="",
                index=len(deltas),
                is_final=True,
                response=response,
                metadata=self._chunk_metadata(request, len(deltas) + 1, usage.output_tokens),
            )
            return

    async def _call_untraced(
        self,
        operation: Callable[[], Awaitable[Any]],
        context: Context | None = None,
        *,
        cleanup: bool = False,
    ) -> Any:
        async def run() -> Any:
            from langchain_core.globals import get_debug

            unsafe_logging = get_debug() or any(
                logging.getLogger(name).isEnabledFor(logging.DEBUG)
                for name in ("openai._base_client", "openai._response")
            )
            if unsafe_logging and not cleanup:
                raise RuntimeError("Framework and SDK debug logging must be disabled")
            # Fresh task context excludes ambient tracing_v2_enabled,
            # collect_runs and inherited Runnable callback managers.
            with self._tracing_context(enabled=False):
                return await operation()

        return await asyncio.create_task(
            run(), context=context if context is not None else isolated_framework_context()
        )

    @asynccontextmanager
    async def _model_context(self) -> AsyncIterator[Any]:
        if self._chat_model is not None:
            yield self._chat_model
            return
        # API factories are request-scoped and the provider port has no close
        # hook. Own our clients for exactly this operation, including streaming.
        with httpx.Client() as sync_client:
            async with httpx.AsyncClient() as async_client:
                model = self._chat_model_type(
                    **self._model_options,
                    http_client=sync_client,
                    http_async_client=async_client,
                )
                yield model

    def _validate_request(self, request: GenerateRequest) -> None:
        if (
            request.provider != self._provider
            or request.model != self._model
            or not math.isfinite(request.timeout_seconds)
        ):
            raise self._error(
                request,
                LLM_GENERATION_INVALID_REQUEST,
                "Generation request does not match the configured provider.",
                False,
                400,
            )

    def _messages(self, request: GenerateRequest) -> list[dict[str, str]]:
        # Role dictionaries are a public LangChain message input format.
        return [{"role": item.role, "content": item.content} for item in request.messages]

    def _options(self, request: GenerateRequest, *, stream: bool = False) -> dict[str, object]:
        options: dict[str, object] = {"timeout": request.timeout_seconds}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            options["max_tokens"] = request.max_output_tokens
        if stream:
            options["stream_options"] = {
                "include_usage": bool(request.stream_options.get("include_usage", True))
            }
        return options

    def _parse_message(
        self, message: Any, request: GenerateRequest
    ) -> tuple[str, str, TokenUsage, int]:
        content = getattr(message, "content", None)
        additional = getattr(message, "additional_kwargs", {})
        has_tool_output = any(
            getattr(message, name, None)
            for name in ("tool_calls", "invalid_tool_calls", "tool_call_chunks")
        ) or (
            isinstance(additional, Mapping)
            and any(additional.get(name) for name in ("tool_calls", "function_call"))
        )
        if not isinstance(content, str) or has_tool_output:
            raise self._error(
                request,
                LLM_PROVIDER_MALFORMED_RESPONSE,
                "LLM provider returned unsupported non-text output.",
                False,
                502,
            )
        return content, *self._parse_response_metadata(message, request)

    def _parse_response_metadata(
        self, message: Any, request: GenerateRequest, *, allow_tool_calls: bool = False
    ) -> tuple[str, TokenUsage, int]:
        metadata = getattr(message, "response_metadata", {})
        if not isinstance(metadata, Mapping):
            raise self._malformed(request)
        raw_reason = metadata.get("finish_reason")
        reason = (
            (
                raw_reason
                if raw_reason
                in (
                    {"stop", "length", "content_filter", "unknown"}
                    | ({"tool_calls"} if allow_tool_calls else set())
                )
                else "unknown"
            )
            if isinstance(raw_reason, str)
            else "unknown"
        )
        raw_usage = getattr(message, "usage_metadata", None)
        if raw_usage is None:
            raw_usage = metadata.get("token_usage")
        if raw_usage is None:
            return reason, TokenUsage(), 1
        if not isinstance(raw_usage, Mapping):
            raise self._malformed(request)
        values = [
            raw_usage.get("input_tokens", raw_usage.get("prompt_tokens", 0)),
            raw_usage.get("output_tokens", raw_usage.get("completion_tokens", 0)),
        ]
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values
        ):
            raise self._malformed(request)
        total = raw_usage.get("total_tokens", sum(values))
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise self._malformed(request)
        return (
            reason,
            TokenUsage(input_tokens=values[0], output_tokens=values[1], total_tokens=total),
            0,
        )

    def _response(
        self,
        request: GenerateRequest,
        text: str,
        finish_reason: str,
        usage: TokenUsage,
        unavailable: int,
        started: float,
        chunk_count: int | None,
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
            finish_reason=finish_reason,
            chunk_count=chunk_count,
            token_count=usage.output_tokens,
            metadata={
                "message_count": len(request.messages),
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
                "usage_unavailable_count": unavailable,
            },
        )
        return GenerateResponse(
            text=text,
            provider=self._provider,
            model=self._model,
            version=self._version,
            usage=usage,
            latency_ms=latency_ms,
            finish_reason=finish_reason,
            request_id=request.request_id,
            trace_id=request.trace_id,
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            metadata=metadata,
        )

    def _chunk_metadata(
        self, request: GenerateRequest, chunk_count: int, token_count: int
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
        )

    def _translate_error(
        self, exc: Exception, request: GenerateRequest, *, stream: bool
    ) -> LLMProviderError:
        if isinstance(exc, LLMProviderError):
            return exc
        status = getattr(exc, "status_code", None)
        if (
            isinstance(exc, TimeoutError | httpx.TimeoutException)
            or type(exc).__name__ == "APITimeoutError"
        ):
            return self._error(request, LLM_PROVIDER_TIMEOUT, "LLM provider timed out.", True, 504)
        if status == 429:
            return self._error(
                request,
                LLM_PROVIDER_RATE_LIMITED,
                "LLM provider rate limited the request.",
                True,
                429,
            )
        if status in (401, 403):
            return self._error(
                request,
                LLM_PROVIDER_AUTH_FAILED,
                "LLM provider authentication failed.",
                False,
                status,
            )
        if status == 400:
            return self._error(
                request,
                LLM_GENERATION_INVALID_REQUEST,
                "LLM provider rejected the generation request.",
                False,
                400,
            )
        retryable = (
            (isinstance(status, int) and status >= 500)
            or isinstance(exc, httpx.TransportError)
            or type(exc).__name__ == "APIConnectionError"
        )
        return self._error(
            request,
            LLM_STREAM_FAILED if stream else LLM_PROVIDER_FAILED,
            "LLM provider request failed.",
            retryable,
            502,
        )

    def _malformed(self, request: GenerateRequest) -> LLMProviderError:
        return self._error(
            request,
            LLM_PROVIDER_MALFORMED_RESPONSE,
            "LLM provider returned a malformed response.",
            False,
            502,
        )

    def _error(
        self, request: GenerateRequest, code: str, message: str, retryable: bool, status: int
    ) -> LLMProviderError:
        return LLMProviderError(
            code=code,
            message=message,
            retryable=retryable,
            status_code=status,
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


def _required_text(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{name} must not be blank")
    return normalized


class _DecisionAsyncClient:
    """Validate SDK chat results while keeping LangChain's native invocation."""

    def __init__(
        self,
        client: Any,
        *,
        validate: Callable[[Any], None],
        malformed: Callable[[], LLMProviderError],
    ) -> None:
        self._client = client
        self._validate = validate
        self._malformed = malformed

    @property
    def with_raw_response(self) -> _DecisionRawResponseClient:
        return _DecisionRawResponseClient(
            self._client.with_raw_response, validate=self._validate, malformed=self._malformed
        )

    async def create(self, **kwargs: Any) -> Any:
        response = await self._client.create(**kwargs)
        self._validate(response)
        return response


class _DecisionRawResponseClient:
    def __init__(
        self,
        client: Any,
        *,
        validate: Callable[[Any], None],
        malformed: Callable[[], LLMProviderError],
    ) -> None:
        self._client = client
        self._validate = validate
        self._malformed = malformed

    async def create(self, **kwargs: Any) -> Any:
        response = await self._client.create(**kwargs)
        try:
            http_response = getattr(response, "http_response", None)
            # The SDK exposes the original response here. Inspect it before
            # either SDK or framework normalization can discard envelope fields.
            parsed = (
                http_response.json(object_pairs_hook=_unique_json_object)
                if isinstance(http_response, httpx.Response)
                else response.parse()
            )
        except Exception:
            raise self._malformed() from None
        self._validate(parsed)
        return response


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member")
        result[key] = value
    return result

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from packages.auth.policies import FrozenDict
from packages.common.audit import AuditEvent, AuditPort, AuditResource, AuditStatus
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError
from packages.common.logging import REDACTED_VALUE, redact_mapping
from packages.rag.dto import ChatResponse, Citation, QueryCommand, UnsupportedClaim
from packages.rag.streaming import (
    ErrorEventPayload,
    FinalEventPayload,
    RagStreamEvent,
    TokenEventPayload,
)

OpenAIMessageRole = Literal["system", "developer", "user", "assistant", "tool"]
FORBIDDEN_METADATA_FILTER_KEYS = frozenset(
    {
        "tenant_id",
        "user_id",
        "acl",
        "permissions",
        "roles",
        "department",
        "created_by",
    }
)


class OpenWebUIChatService(Protocol):
    async def chat(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        session_id: str | None,
    ) -> ChatResponse: ...

    def stream_chat(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        session_id: str | None,
    ) -> AsyncIterator[RagStreamEvent]: ...


class OpenAIModel(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    object: Literal["model"] = "model"
    created: int
    owned_by: str


class OpenAIModelListResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    object: Literal["list"] = "list"
    data: tuple[OpenAIModel, ...]


class OpenAIChatMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: OpenAIMessageRole
    content: str = ""

    @field_validator("content", mode="before")
    @classmethod
    def _normalize_content(cls, value: object) -> str:
        return _message_content_text(value).strip()

    @model_validator(mode="after")
    def _user_content_must_not_be_blank(self) -> OpenAIChatMessage:
        if self.role == "user" and not self.content.strip():
            raise ValueError("user message content must not be blank")
        return self


class OpenAIChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    model: str
    messages: tuple[OpenAIChatMessage, ...]
    stream: bool = False
    session_id: str | None = None
    top_k: int = Field(default=10, ge=1, le=100)
    max_tokens: int | None = Field(default=None, gt=0, le=4096)
    max_completion_tokens: int | None = Field(default=None, gt=0, le=4096)
    metadata_filter: dict[str, object] = Field(default_factory=dict)

    @field_validator("model")
    @classmethod
    def _model_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("model must not be blank")
        return normalized

    @field_validator("session_id")
    @classmethod
    def _optional_session_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("metadata_filter", mode="before")
    @classmethod
    def _safe_metadata_filter(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("metadata_filter must be an object")
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("metadata_filter keys must be strings")
            normalized_key = key.strip()
            if not normalized_key:
                raise ValueError("metadata_filter keys must not be blank")
            if normalized_key.lower() in FORBIDDEN_METADATA_FILTER_KEYS:
                raise ValueError("metadata_filter cannot include authorization scope fields")
            normalized[normalized_key] = item
        QueryCommand(query="metadata filter validation", metadata_filter=normalized)
        return normalized

    @model_validator(mode="after")
    def _must_include_user_message(self) -> OpenAIChatCompletionRequest:
        if _latest_user_message(self.messages) is None:
            raise ValueError("messages must include at least one user message")
        return self


class OpenAIUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class OpenAIChatChoiceMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Literal["assistant"] = "assistant"
    content: str


class OpenAIChatChoice(BaseModel):
    model_config = ConfigDict(frozen=True)

    index: int
    message: OpenAIChatChoiceMessage
    finish_reason: Literal["stop"] = "stop"


class OpenAIChatCompletionResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: tuple[OpenAIChatChoice, ...]
    usage: OpenAIUsage
    request_id: str
    trace_id: str
    session_id: str
    citations: tuple[Citation, ...] = ()
    no_answer: bool = False
    unsupported_claims: tuple[UnsupportedClaim, ...] = ()
    metadata: Mapping[str, Any] = Field(default_factory=FrozenDict)

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return dict(value)


class OpenWebUIChatAdapter:
    def __init__(
        self,
        *,
        chat_service: OpenWebUIChatService,
        model_id: str,
        owned_by: str,
        audit: AuditPort | None = None,
        created: int | None = None,
    ) -> None:
        self._chat_service = chat_service
        self._model_id = _required_text(model_id, field_name="model_id")
        self._owned_by = _required_text(owned_by, field_name="owned_by")
        self._audit = audit
        self._created = created or int(time.time())

    def list_models(self) -> OpenAIModelListResponse:
        return OpenAIModelListResponse(
            data=(
                OpenAIModel(
                    id=self._model_id,
                    created=self._created,
                    owned_by=self._owned_by,
                ),
            )
        )

    async def chat_completion(
        self,
        *,
        context: AuthenticatedRequestContext,
        request: OpenAIChatCompletionRequest,
    ) -> OpenAIChatCompletionResponse:
        started = time.perf_counter()
        try:
            response = await self._chat_service.chat(
                context=context,
                command=_query_command(request),
                session_id=request.session_id,
            )
            completion = _completion_response(response=response, model=self._model_id)
            await self._record_audit(
                context=context,
                request=request,
                status=AuditStatus.SUCCESS,
                started=started,
                response=completion,
                stream=False,
                error_code=None,
            )
            return completion
        except DomainError as exc:
            await self._record_audit(
                context=context,
                request=request,
                status=AuditStatus.DENIED if exc.status_code == 403 else AuditStatus.FAILURE,
                started=started,
                response=None,
                stream=False,
                error_code=exc.code,
            )
            raise
        except Exception:
            await self._record_audit(
                context=context,
                request=request,
                status=AuditStatus.FAILURE,
                started=started,
                response=None,
                stream=False,
                error_code="OPENWEBUI_CHAT_FAILED",
            )
            raise

    async def stream_chat_completion(
        self,
        *,
        context: AuthenticatedRequestContext,
        request: OpenAIChatCompletionRequest,
    ) -> AsyncIterator[str]:
        started = time.perf_counter()
        final_payload: FinalEventPayload | None = None
        error_code: str | None = None
        status = AuditStatus.SUCCESS
        try:
            async for event in self._chat_service.stream_chat(
                context=context,
                command=_query_command(request),
                session_id=request.session_id,
            ):
                if isinstance(event.payload, FinalEventPayload):
                    final_payload = event.payload
                    if event.payload.status == "error":
                        status = AuditStatus.FAILURE
                        error_code = _metadata_error_code(event.payload.metadata)
                yield format_openai_stream_event(event=event, model=self._model_id)
        except DomainError as exc:
            status = AuditStatus.DENIED if exc.status_code == 403 else AuditStatus.FAILURE
            error_code = exc.code
            yield format_openai_error_chunk(
                request_id=context.request_id,
                trace_id=context.trace_id,
                model=self._model_id,
                code=exc.code,
                message=exc.message,
                details=exc.details,
            )
        except Exception:
            status = AuditStatus.FAILURE
            error_code = "OPENWEBUI_STREAM_FAILED"
            yield format_openai_error_chunk(
                request_id=context.request_id,
                trace_id=context.trace_id,
                model=self._model_id,
                code=error_code,
                message="OpenAI-compatible chat stream failed.",
                details={
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "error_code": error_code,
                },
            )
        finally:
            await self._record_stream_audit(
                context=context,
                request=request,
                started=started,
                final_payload=final_payload,
                status=status,
                error_code=error_code,
            )
            yield "data: [DONE]\n\n"

    async def _record_audit(
        self,
        *,
        context: AuthenticatedRequestContext,
        request: OpenAIChatCompletionRequest,
        status: AuditStatus,
        started: float,
        response: OpenAIChatCompletionResponse | None,
        stream: bool,
        error_code: str | None,
    ) -> None:
        if self._audit is None:
            return
        metadata = _adapter_audit_metadata(
            context=context,
            request=request,
            configured_model=self._model_id,
            stream=stream,
            session_id=response.session_id if response is not None else request.session_id,
            citation_count=len(response.citations) if response is not None else 0,
            usage=response.usage.model_dump() if response is not None else None,
            error_code=error_code,
        )
        await self._audit.record(
            AuditEvent(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                action="rag.openwebui.chat",
                resource=AuditResource(
                    type="openwebui_chat",
                    id=context.request_id,
                    metadata={"request_id": context.request_id, "trace_id": context.trace_id},
                ),
                status=status,
                latency_ms=max((time.perf_counter() - started) * 1000, 0.0),
                error_code=error_code,
                metadata=metadata,
                created_at=datetime.now(tz=UTC),
            )
        )

    async def _record_stream_audit(
        self,
        *,
        context: AuthenticatedRequestContext,
        request: OpenAIChatCompletionRequest,
        started: float,
        final_payload: FinalEventPayload | None,
        status: AuditStatus,
        error_code: str | None,
    ) -> None:
        if self._audit is None:
            return
        usage = None
        citation_count = 0
        session_id = request.session_id
        if final_payload is not None:
            usage = _usage_from_metadata(final_payload.metadata).model_dump()
            citation_count = len(final_payload.citations)
            session_id = final_payload.session_id
        metadata = _adapter_audit_metadata(
            context=context,
            request=request,
            configured_model=self._model_id,
            stream=True,
            session_id=session_id,
            citation_count=citation_count,
            usage=usage,
            error_code=error_code,
        )
        await self._audit.record(
            AuditEvent(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                action="rag.openwebui.chat.stream",
                resource=AuditResource(
                    type="openwebui_chat",
                    id=context.request_id,
                    metadata={"request_id": context.request_id, "trace_id": context.trace_id},
                ),
                status=status,
                latency_ms=max((time.perf_counter() - started) * 1000, 0.0),
                error_code=error_code,
                metadata=metadata,
                created_at=datetime.now(tz=UTC),
            )
        )


def format_openai_stream_event(*, event: RagStreamEvent, model: str) -> str:
    created = int(time.time())
    base: dict[str, object] = {
        "id": f"chatcmpl-{event.payload.request_id}",
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
    }
    if isinstance(event.payload, TokenEventPayload):
        base["choices"] = (
            {
                "index": 0,
                "delta": {"content": event.payload.delta},
                "finish_reason": None,
            },
        )
    elif isinstance(event.payload, FinalEventPayload):
        base.update(_final_extension_fields(event.payload))
        base["choices"] = (
            {
                "index": 0,
                "delta": {},
                "finish_reason": "stop" if event.payload.status == "success" else "error",
            },
        )
    elif isinstance(event.payload, ErrorEventPayload):
        base["error"] = {
            "code": event.payload.code,
            "message": event.payload.message,
            "details": dict(event.payload.details),
        }
        base["choices"] = (
            {
                "index": 0,
                "delta": {},
                "finish_reason": "error",
            },
        )
    else:
        base["choices"] = (
            {
                "index": 0,
                "delta": {},
                "finish_reason": None,
            },
        )
    return f"data: {json.dumps(base, ensure_ascii=False, separators=(',', ':'))}\n\n"


def format_openai_error_chunk(
    *,
    request_id: str,
    trace_id: str,
    model: str,
    code: str,
    message: str,
    details: Mapping[str, object],
) -> str:
    payload = {
        "id": f"chatcmpl-{request_id}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "request_id": request_id,
        "trace_id": trace_id,
        "error": {
            "code": code,
            "message": message,
            "details": _safe_response_metadata(details),
        },
        "choices": (
            {
                "index": 0,
                "delta": {},
                "finish_reason": "error",
            },
        ),
    }
    return f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _completion_response(*, response: ChatResponse, model: str) -> OpenAIChatCompletionResponse:
    return OpenAIChatCompletionResponse(
        id=f"chatcmpl-{response.request_id}",
        created=int(time.time()),
        model=model,
        choices=(
            OpenAIChatChoice(
                index=0,
                message=OpenAIChatChoiceMessage(content=response.answer),
            ),
        ),
        usage=_usage_from_metadata(response.metadata),
        request_id=response.request_id,
        trace_id=response.trace_id,
        session_id=response.session_id,
        citations=response.citations,
        no_answer=response.no_answer,
        unsupported_claims=response.unsupported_claims,
        metadata=_safe_response_metadata(response.metadata),
    )


def _query_command(request: OpenAIChatCompletionRequest) -> QueryCommand:
    query = _latest_user_message(request.messages)
    if query is None:
        raise ValueError("messages must include at least one user message")
    return QueryCommand(
        query=query,
        top_k=request.top_k,
        metadata_filter=dict(request.metadata_filter),
        max_output_tokens=request.max_completion_tokens or request.max_tokens,
    )


def _latest_user_message(messages: tuple[OpenAIChatMessage, ...]) -> str | None:
    for message in reversed(messages):
        if message.role == "user" and message.content.strip():
            return message.content
    return None


def _message_content_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return _content_part_text(value)
    if isinstance(value, list | tuple):
        parts = [_content_part_text(item) for item in value]
        return "\n".join(part for part in parts if part.strip())
    raise ValueError("message content must be a string, null, object, or content parts array")


def _content_part_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, Mapping):
        return ""
    part_type = value.get("type")
    if part_type not in {None, "text", "input_text"}:
        return ""
    text = value.get("text")
    if isinstance(text, str):
        return text
    return ""


def _usage_from_metadata(metadata: Mapping[str, object]) -> OpenAIUsage:
    generation = metadata.get("generation")
    token_usage: object = None
    if isinstance(generation, Mapping):
        token_usage = generation.get("token_usage")
    if not isinstance(token_usage, Mapping):
        return OpenAIUsage()
    return OpenAIUsage(
        prompt_tokens=_safe_int(token_usage.get("prompt_tokens")),
        completion_tokens=_safe_int(token_usage.get("completion_tokens")),
        total_tokens=_safe_int(token_usage.get("total_tokens")),
    )


def _final_extension_fields(payload: FinalEventPayload) -> dict[str, object]:
    return {
        "request_id": payload.request_id,
        "trace_id": payload.trace_id,
        "session_id": payload.session_id,
        "citations": [citation.model_dump(mode="json") for citation in payload.citations],
        "citation_count": len(payload.citations),
        "no_answer": payload.no_answer,
        "unsupported_claims": [
            unsupported.model_dump(mode="json") for unsupported in payload.unsupported_claims
        ],
        "metadata": _safe_response_metadata(payload.metadata),
    }


def _safe_response_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    redacted = redact_mapping(metadata)
    return cast(dict[str, object], _drop_redacted(redacted))


def _drop_redacted(value: object) -> object:
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            cleaned = _drop_redacted(item)
            if cleaned is REDACTED_VALUE or cleaned == REDACTED_VALUE:
                continue
            if cleaned == {}:
                continue
            result[str(key)] = cleaned
        return result
    if isinstance(value, list):
        return [item for item in (_drop_redacted(item) for item in value) if item != REDACTED_VALUE]
    return value


def _safe_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(value, 0)
    return 0


def _required_text(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def _metadata_error_code(metadata: Mapping[str, object]) -> str | None:
    value = metadata.get("error_code")
    if value is None:
        return None
    return str(value)


def _adapter_audit_metadata(
    *,
    context: AuthenticatedRequestContext,
    request: OpenAIChatCompletionRequest,
    configured_model: str,
    stream: bool,
    session_id: str | None,
    citation_count: int,
    usage: Mapping[str, object] | None,
    error_code: str | None,
) -> dict[str, object]:
    return {
        "request_id": context.request_id,
        "trace_id": context.trace_id,
        "tenant_id": context.auth.tenant_id,
        "user_id": context.auth.user_id,
        "model": configured_model,
        "requested_model": request.model,
        "stream": stream,
        "session_id": session_id,
        "top_k": request.top_k,
        "citation_count": citation_count,
        "token_usage": dict(usage or {}),
        "auth_method": context.auth_method,
        "role_count": len(context.auth.roles),
        "permission_count": len(context.auth.permissions),
        "message_count": len(request.messages),
        "error_code": error_code,
    }

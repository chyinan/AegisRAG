from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal, Protocol, cast
from urllib.parse import urlencode

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)

from packages.agent.service_token_bridge import (
    ServiceTokenToolBridgeCandidate,
    ServiceTokenToolBridgeExecution,
    ServiceTokenToolBridgePort,
    ServiceTokenToolChoice,
    ServiceTokenToolCitation,
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
    ToolCallEventPayload,
    ToolResultEventPayload,
    final_event,
    tool_call_event,
    tool_result_event,
)

OpenAIMessageRole = Literal["system", "developer", "user", "assistant", "tool"]
logger = logging.getLogger(__name__)
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
FORBIDDEN_TOOL_SCHEMA_FIELDS = frozenset(
    {
        "tenant_id",
        "user_id",
        "roles",
        "permissions",
        "acl",
        "token",
        "secret",
        "source_uri",
        "file_path",
        "prompt",
        "raw_output",
    }
)


class ServiceTokenChatService(Protocol):
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
    tools: tuple[dict[str, Any], ...] | None = None
    tool_choice: str | dict[str, Any] | None = None
    functions: tuple[dict[str, Any], ...] | None = None
    function_call: str | dict[str, Any] | None = None

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
        _ = self.normalized_tool_candidates
        _ = self.normalized_tool_choice
        return self

    @property
    def normalized_tool_candidates(self) -> tuple[ServiceTokenToolBridgeCandidate, ...]:
        return _normalize_tool_candidates(tools=self.tools, functions=self.functions)

    @property
    def normalized_tool_choice(self) -> ServiceTokenToolChoice:
        return _normalize_tool_choice(
            tool_choice=self.tool_choice,
            function_call=self.function_call,
            candidates=self.normalized_tool_candidates,
        )


class OpenAIUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ServiceTokenToolEventSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    event: Literal["tool_call", "tool_result"]
    agent_run_id: str | None = None
    tool_call_id: str
    tool_name: str
    status: str
    latency_ms: float | int | None = None
    error_code: str | None = None
    request_id: str
    trace_id: str
    next_step: str | None = None
    audit_ref: str | None = None
    review_ref: str | None = None


def _empty_evidence_query() -> Mapping[str, str | int]:
    return {}


class CitationEvidenceLink(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    citation_ref: str
    evidence_url: str
    evidence_query: Mapping[str, str | int] = Field(default_factory=_empty_evidence_query)
    document_id: str
    version_id: str
    chunk_id: str
    page_start: int | None = None
    page_end: int | None = None
    request_id: str
    trace_id: str
    source_display_name: str

    @field_serializer("evidence_query")
    def _serialize_evidence_query(self, value: Mapping[str, str | int]) -> dict[str, str | int]:
        return dict(value)


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
    evidence_links: tuple[CitationEvidenceLink, ...] = ()
    no_answer: bool = False
    unsupported_claims: tuple[UnsupportedClaim, ...] = ()
    metadata: Mapping[str, Any] = Field(default_factory=FrozenDict)

    @field_serializer("metadata")
    def _serialize_metadata(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return dict(value)


class ServiceTokenChatAdapter:
    def __init__(
        self,
        *,
        chat_service: ServiceTokenChatService,
        tool_bridge: ServiceTokenToolBridgePort | None = None,
        model_id: str,
        owned_by: str,
        audit: AuditPort | None = None,
        created: int | None = None,
    ) -> None:
        self._chat_service = chat_service
        self._tool_bridge = tool_bridge
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
            bridge_execution = await self._execute_tool_bridge_if_needed(
                context=context,
                request=request,
            )
            if bridge_execution is not None:
                completion = _completion_from_bridge(
                    response=bridge_execution,
                    model=self._model_id,
                )
                await self._record_audit(
                    context=context,
                    request=request,
                    status=(
                        AuditStatus.SUCCESS
                        if bridge_execution.status == "success"
                        else AuditStatus.FAILURE
                    ),
                    started=started,
                    response=completion,
                    stream=False,
                    error_code=bridge_execution.error_code,
                )
                return completion
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
        tool_summary = _new_tool_event_summary()
        try:
            bridge_execution = await self._execute_tool_bridge_if_needed(
                context=context,
                request=request,
            )
            if bridge_execution is not None:
                async for frame in self._stream_bridge_execution(
                    context=context,
                    request=request,
                    execution=bridge_execution,
                    tool_summary=tool_summary,
                ):
                    yield frame
                status = (
                    AuditStatus.SUCCESS
                    if bridge_execution.status == "success"
                    else AuditStatus.FAILURE
                )
                error_code = bridge_execution.error_code
                final_payload = _bridge_final_payload(context=context, execution=bridge_execution)
                return
            async for event in self._chat_service.stream_chat(
                context=context,
                command=_query_command(request),
                session_id=request.session_id,
            ):
                if _is_tool_event(event):
                    _record_tool_event_summary(tool_summary, event)
                if isinstance(event.payload, FinalEventPayload):
                    final_event = _event_with_tool_event_summary(
                        event=event,
                        summary=tool_summary,
                    )
                    final_payload = cast(FinalEventPayload, final_event.payload)
                    event = final_event
                    if final_payload.status == "error":
                        status = AuditStatus.FAILURE
                        error_code = _metadata_error_code(final_payload.metadata)
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
                tool_summary=tool_summary,
            )
            yield "data: [DONE]\n\n"

    async def _execute_tool_bridge_if_needed(
        self,
        *,
        context: AuthenticatedRequestContext,
        request: OpenAIChatCompletionRequest,
    ) -> ServiceTokenToolBridgeExecution | None:
        if self._tool_bridge is None:
            return None
        candidates = request.normalized_tool_candidates
        if not candidates:
            return None
        if request.normalized_tool_choice.mode == "none":
            return None
        latest_user_message = _latest_user_message(request.messages)
        if latest_user_message is None:
            return None
        return await self._tool_bridge.execute(
            context=context,
            latest_user_message=latest_user_message,
            session_id=request.session_id,
            candidates=candidates,
            tool_choice=request.normalized_tool_choice,
            requested_model=request.model,
        )

    async def _stream_bridge_execution(
        self,
        *,
        context: AuthenticatedRequestContext,
        request: OpenAIChatCompletionRequest,
        execution: ServiceTokenToolBridgeExecution,
        tool_summary: dict[str, object],
    ) -> AsyncIterator[str]:
        call_event = tool_call_event(
            request_id=context.request_id,
            trace_id=context.trace_id,
            tool_call_id=execution.tool_call_id,
            tool_name=execution.tool_name,
            metadata=execution.metadata,
        )
        _record_tool_event_summary(tool_summary, call_event)
        yield format_openai_stream_event(event=call_event, model=self._model_id)
        result_event = tool_result_event(
            request_id=context.request_id,
            trace_id=context.trace_id,
            tool_call_id=execution.tool_call_id,
            tool_name=execution.tool_name,
            status="success" if execution.status == "success" else "error",
            metadata=execution.metadata,
        )
        _record_tool_event_summary(tool_summary, result_event)
        yield format_openai_stream_event(event=result_event, model=self._model_id)
        final_payload = _bridge_final_payload(context=context, execution=execution)
        final_event_with_summary = _event_with_tool_event_summary(
            event=final_event(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                answer=final_payload.answer,
                citations=final_payload.citations,
                no_answer=final_payload.no_answer,
                unsupported_claims=final_payload.unsupported_claims,
                metadata=final_payload.metadata,
                status=final_payload.status,
                session_id=final_payload.session_id,
            ),
            summary=tool_summary,
        )
        yield format_openai_stream_event(event=final_event_with_summary, model=self._model_id)

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
            evidence_link_count=len(response.evidence_links) if response is not None else 0,
            usage=response.usage.model_dump() if response is not None else None,
            error_code=error_code,
        )
        try:
            await self._audit.record(
                AuditEvent(
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    tenant_id=context.auth.tenant_id,
                    user_id=context.auth.user_id,
                    action="rag.service_token.chat",
                    resource=AuditResource(
                        type="service_token_chat",
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
        except Exception as exc:
            logger.warning(
                "rag.service_token.audit_failed",
                extra={
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "stream": stream,
                    "audit_status": status.value,
                    "error_code": error_code,
                    "audit_error_type": type(exc).__name__,
                },
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
        tool_summary: dict[str, object],
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
        evidence_link_count = citation_count
        metadata = _adapter_audit_metadata(
            context=context,
            request=request,
            configured_model=self._model_id,
            stream=True,
            session_id=session_id,
            citation_count=citation_count,
            evidence_link_count=evidence_link_count,
            usage=usage,
            error_code=error_code,
            tool_summary=tool_summary,
        )
        try:
            await self._audit.record(
                AuditEvent(
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    tenant_id=context.auth.tenant_id,
                    user_id=context.auth.user_id,
                    action="rag.service_token.chat.stream",
                    resource=AuditResource(
                        type="service_token_chat",
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
        except Exception as exc:
            logger.warning(
                "rag.service_token.audit_failed",
                extra={
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "stream": True,
                    "audit_status": status.value,
                    "error_code": error_code,
                    "audit_error_type": type(exc).__name__,
                },
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
    elif isinstance(event.payload, ToolCallEventPayload | ToolResultEventPayload):
        summary = _tool_event_summary(event.payload)
        summary_payload = _tool_event_summary_payload(summary)
        base["request_id"] = event.payload.request_id
        base["trace_id"] = event.payload.trace_id
        base["tool_event"] = summary_payload
        base["metadata"] = {"tool_event": summary_payload}
        base["choices"] = (
            {
                "index": 0,
                "delta": {},
                "finish_reason": None,
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
    evidence_links = _evidence_links(
        citations=response.citations,
        request_id=response.request_id,
        trace_id=response.trace_id,
    )
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
        evidence_links=evidence_links,
        no_answer=response.no_answer,
        unsupported_claims=response.unsupported_claims,
        metadata=_safe_response_metadata(response.metadata),
    )


def _completion_from_bridge(
    *,
    response: ServiceTokenToolBridgeExecution,
    model: str,
) -> OpenAIChatCompletionResponse:
    citations = tuple(_bridge_citation_to_public(citation) for citation in response.citations)
    metadata = {
        **dict(response.metadata),
        "agent_run_id": response.agent_run_id,
        "tool_call_id": response.tool_call_id,
        "tool_name": response.tool_name,
        "status": response.status,
        "latency_ms": response.latency_ms,
        "error_code": response.error_code,
    }
    return OpenAIChatCompletionResponse(
        id=f"chatcmpl-{response.request_id}",
        created=int(time.time()),
        model=model,
        choices=(
            OpenAIChatChoice(
                index=0,
                message=OpenAIChatChoiceMessage(content=response.assistant_text),
            ),
        ),
        usage=OpenAIUsage(),
        request_id=response.request_id,
        trace_id=response.trace_id,
        session_id=response.session_id,
        citations=citations,
        evidence_links=_evidence_links(
            citations=citations,
            request_id=response.request_id,
            trace_id=response.trace_id,
        ),
        no_answer=False,
        unsupported_claims=(),
        metadata=_safe_response_metadata(metadata),
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


def _bridge_citation_to_public(citation: ServiceTokenToolCitation) -> Citation:
    return Citation(
        document_id=citation.document_id,
        version_id=citation.version_id,
        chunk_id=citation.chunk_id,
        source_display_name=citation.source_display_name,
        source_type=citation.source_type,
        page_start=citation.page_start,
        page_end=citation.page_end,
        title_path=citation.title_path or ("Untitled",),
        retrieval_method=citation.retrieval_method,
        score=citation.score,
    )


def _bridge_final_payload(
    *,
    context: AuthenticatedRequestContext,
    execution: ServiceTokenToolBridgeExecution,
) -> FinalEventPayload:
    return FinalEventPayload(
        request_id=context.request_id,
        trace_id=context.trace_id,
        tenant_id=context.auth.tenant_id,
        user_id=context.auth.user_id,
        session_id=execution.session_id,
        answer=execution.assistant_text,
        citations=tuple(_bridge_citation_to_public(citation) for citation in execution.citations),
        unsupported_claims=(),
        no_answer=False,
        metadata=execution.metadata,
        status="success" if execution.status == "success" else "error",
    )


def _latest_user_message(messages: tuple[OpenAIChatMessage, ...]) -> str | None:
    for message in reversed(messages):
        if message.role == "user" and message.content.strip():
            return message.content
    return None


def _normalize_tool_candidates(
    *,
    tools: tuple[dict[str, Any], ...] | None,
    functions: tuple[dict[str, Any], ...] | None,
) -> tuple[ServiceTokenToolBridgeCandidate, ...]:
    if tools and functions:
        raise ValueError("tools and functions cannot be mixed in the same request")
    candidates: list[ServiceTokenToolBridgeCandidate] = []
    seen_names: set[str] = set()
    for declaration_type, values in (
        ("modern", tools or ()),
        ("legacy", functions or ()),
    ):
        for item in values:
            candidate = _normalize_tool_candidate(
                item=item,
                declaration_type=cast(Literal["modern", "legacy"], declaration_type),
            )
            if candidate.name in seen_names:
                raise ValueError("duplicate tool declarations are not allowed")
            seen_names.add(candidate.name)
            candidates.append(candidate)
    return tuple(candidates)


def _normalize_tool_candidate(
    *,
    item: Mapping[str, object],
    declaration_type: Literal["modern", "legacy"],
) -> ServiceTokenToolBridgeCandidate:
    payload = item
    if declaration_type == "modern":
        if payload.get("type") != "function":
            raise ValueError("tools entries must use type=function")
        function = payload.get("function")
        if not isinstance(function, Mapping):
            raise ValueError("tools.function must be an object")
        payload = cast(Mapping[str, object], function)
    name = _required_tool_name(payload.get("name"))
    description = _required_tool_description(payload.get("description"))
    parameters = payload.get("parameters")
    schema_summary = _tool_schema_summary(parameters)
    return ServiceTokenToolBridgeCandidate(
        name=name,
        description=description,
        schema_summary=schema_summary,
        declaration_type=declaration_type,
    )


def _normalize_tool_choice(
    *,
    tool_choice: str | dict[str, Any] | None,
    function_call: str | dict[str, Any] | None,
    candidates: Sequence[ServiceTokenToolBridgeCandidate],
) -> ServiceTokenToolChoice:
    if tool_choice is not None and function_call is not None:
        raise ValueError("tool_choice and function_call cannot be mixed in the same request")
    raw_choice = tool_choice if tool_choice is not None else function_call
    if raw_choice is None:
        return ServiceTokenToolChoice(mode="auto")
    if isinstance(raw_choice, str):
        normalized = raw_choice.strip()
        if normalized in {"auto", "none", "required"}:
            return ServiceTokenToolChoice(
                mode=cast(Literal["auto", "none", "required"], normalized))
        raise ValueError("unsupported tool choice mode")
    if not isinstance(raw_choice, Mapping):
        raise ValueError("tool choice must be a string or object")
    if "function" in raw_choice:
        if raw_choice.get("type") != "function":
            raise ValueError("tool_choice.function entries must use type=function")
        function = raw_choice.get("function")
        if not isinstance(function, Mapping):
            raise ValueError("tool_choice.function must be an object")
        name = _required_tool_name(function.get("name"))
        if name not in {candidate.name for candidate in candidates}:
            raise ValueError("tool_choice must reference a declared tool")
        return ServiceTokenToolChoice(mode="tool", tool_name=name)
    name = _required_tool_name(raw_choice.get("name"))
    if name not in {candidate.name for candidate in candidates}:
        raise ValueError("function_call must reference a declared tool")
    return ServiceTokenToolChoice(mode="tool", tool_name=name)


def _required_tool_name(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("tool name must be a string")
    normalized = value.strip()
    if not normalized or normalized[0].isdigit() or normalized.lower() != normalized:
        raise ValueError("tool name must be lower snake_case")
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789_")
    if any(char not in allowed for char in normalized):
        raise ValueError("tool name must be lower snake_case")
    return normalized


def _required_tool_description(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("tool description must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError("tool description must not be blank")
    return normalized


def _tool_schema_summary(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError("tool parameters must be an object")
    if value.get("type") != "object":
        raise ValueError("tool parameters must use type=object")
    properties = value.get("properties", {})
    if not isinstance(properties, Mapping):
        raise ValueError("tool properties must be an object")
    property_names = []
    for key in properties:
        key_text = str(key).strip()
        if not key_text:
            raise ValueError("tool property names must not be blank")
        if key_text.lower() in FORBIDDEN_TOOL_SCHEMA_FIELDS:
            continue
        property_names.append(key_text)
    required = value.get("required", ())
    if required is None:
        required_names: tuple[str, ...] = ()
    elif isinstance(required, list | tuple):
        required_names = tuple(
            name
            for item in required
            if (
                isinstance(item, str)
                and (name := item.strip())
                and name.lower() not in FORBIDDEN_TOOL_SCHEMA_FIELDS
            )
        )
    else:
        raise ValueError("tool required fields must be an array")
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(encoded) > 4000:
        raise ValueError("tool schema is too large")
    return {
        "type": "object",
        "property_names": tuple(sorted(property_names)),
        "required": tuple(sorted(required_names)),
        "property_count": len(property_names),
    }


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
    evidence_links = _evidence_links(
        citations=payload.citations,
        request_id=payload.request_id,
        trace_id=payload.trace_id,
    )
    return {
        "request_id": payload.request_id,
        "trace_id": payload.trace_id,
        "session_id": payload.session_id,
        "citations": [citation.model_dump(mode="json") for citation in payload.citations],
        "evidence_links": [link.model_dump(mode="json") for link in evidence_links],
        "citation_count": len(payload.citations),
        "no_answer": payload.no_answer,
        "unsupported_claims": [
            unsupported.model_dump(mode="json") for unsupported in payload.unsupported_claims
        ],
        "metadata": _safe_response_metadata(payload.metadata),
    }


def _safe_response_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    normalized = _normalize_safe_response_metadata(metadata)
    redacted = redact_mapping(normalized)
    return cast(dict[str, object], _drop_redacted(redacted))


def _normalize_safe_response_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key, value in metadata.items():
        text_key = str(key)
        if text_key == "tool_event":
            summary = _tool_event_summary_from_mapping(value)
            if summary is not None:
                normalized[text_key] = summary
            continue
        if text_key == "tool_events":
            events = _safe_tool_events_from_value(value)
            if events:
                normalized[text_key] = events
            continue
        if text_key == "tool_event_summary":
            if isinstance(value, Mapping):
                normalized[text_key] = _safe_tool_event_count_summary(value)
            continue
        normalized[text_key] = value
    return normalized


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


def _evidence_links(
    *,
    citations: tuple[Citation, ...],
    request_id: str,
    trace_id: str,
) -> tuple[CitationEvidenceLink, ...]:
    return tuple(
        _evidence_link(citation=citation, index=index, request_id=request_id, trace_id=trace_id)
        for index, citation in enumerate(citations)
    )


def _evidence_link(
    *,
    citation: Citation,
    index: int,
    request_id: str,
    trace_id: str,
) -> CitationEvidenceLink:
    citation_ref = citation.source_ref or f"citation-{index + 1}"
    query: dict[str, str | int] = {
        "document_id": citation.document_id,
        "version_id": citation.version_id,
        "chunk_id": citation.chunk_id,
        "request_id": request_id,
        "citation_ref": citation_ref,
    }
    if citation.page_start is not None and citation.page_end is not None:
        query["page_start"] = citation.page_start
        query["page_end"] = citation.page_end
    evidence_url = f"/governance?{urlencode(query)}#source-evidence"
    return CitationEvidenceLink(
        citation_ref=citation_ref,
        evidence_url=evidence_url,
        evidence_query=query,
        document_id=citation.document_id,
        version_id=citation.version_id,
        chunk_id=citation.chunk_id,
        page_start=citation.page_start,
        page_end=citation.page_end,
        request_id=request_id,
        trace_id=trace_id,
        source_display_name=citation.source_display_name,
    )


def _adapter_audit_metadata(
    *,
    context: AuthenticatedRequestContext,
    request: OpenAIChatCompletionRequest,
    configured_model: str,
    stream: bool,
    session_id: str | None,
    citation_count: int,
    evidence_link_count: int,
    usage: Mapping[str, object] | None,
    error_code: str | None,
    tool_summary: Mapping[str, object] | None = None,
) -> dict[str, object]:
    safe_tool_summary = _safe_tool_event_count_summary(tool_summary or {})
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
        "evidence_link_count": evidence_link_count,
        "token_usage": dict(usage or {}),
        "auth_method": context.auth_method,
        "role_count": len(context.auth.roles),
        "permission_count": len(context.auth.permissions),
        "message_count": len(request.messages),
        "error_code": error_code,
        "tool_event_count": safe_tool_summary["tool_event_count"],
        "tool_call_count": safe_tool_summary["tool_call_count"],
        "tool_result_count": safe_tool_summary["tool_result_count"],
        "tool_error_count": safe_tool_summary["tool_error_count"],
        "agent_run_id": safe_tool_summary.get("agent_run_id"),
    }


def _is_tool_event(event: RagStreamEvent) -> bool:
    return isinstance(event.payload, ToolCallEventPayload | ToolResultEventPayload)


def _tool_event_summary(
    payload: ToolCallEventPayload | ToolResultEventPayload,
) -> ServiceTokenToolEventSummary:
    metadata = dict(payload.metadata)
    status = (
        str(metadata.get("status") or "started")
        if isinstance(payload, ToolCallEventPayload)
        else payload.status
    )
    return ServiceTokenToolEventSummary(
        event=payload.event,
        agent_run_id=_optional_str(metadata.get("agent_run_id")),
        tool_call_id=payload.tool_call_id,
        tool_name=payload.tool_name,
        status=status,
        latency_ms=_safe_number(metadata.get("latency_ms")),
        error_code=_optional_str(metadata.get("error_code")),
        request_id=payload.request_id,
        trace_id=payload.trace_id,
        next_step=_optional_str(metadata.get("next_step")),
        audit_ref=_optional_str(metadata.get("audit_ref")),
        review_ref=_optional_str(metadata.get("review_ref")),
    )


def _tool_event_summary_from_mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    event = value.get("event")
    tool_call_id = value.get("tool_call_id")
    tool_name = value.get("tool_name")
    status = value.get("status")
    request_id = value.get("request_id")
    trace_id = value.get("trace_id")
    if not (
        event in {"tool_call", "tool_result"}
        and isinstance(tool_call_id, str)
        and isinstance(tool_name, str)
        and isinstance(status, str)
        and isinstance(request_id, str)
        and isinstance(trace_id, str)
    ):
        return None
    summary = ServiceTokenToolEventSummary(
        event=event,
        agent_run_id=_optional_str(value.get("agent_run_id")),
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        status=status,
        latency_ms=_safe_number(value.get("latency_ms")),
        error_code=_optional_str(value.get("error_code")),
        request_id=request_id,
        trace_id=trace_id,
        next_step=_optional_str(value.get("next_step")),
        audit_ref=_optional_str(value.get("audit_ref")),
        review_ref=_optional_str(value.get("review_ref")),
    )
    return _tool_event_summary_payload(summary)


def _tool_event_summary_payload(summary: ServiceTokenToolEventSummary) -> dict[str, object]:
    payload = summary.model_dump(mode="json", exclude_none=True)
    if "error_code" not in payload:
        payload["error_code"] = None
    return payload


def _safe_tool_events_from_value(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list | tuple):
        return []
    events: list[dict[str, object]] = []
    for item in value:
        summary = _tool_event_summary_from_mapping(item)
        if summary is not None:
            events.append(summary)
    return events


def _new_tool_event_summary() -> dict[str, object]:
    return {
        "tool_event_count": 0,
        "tool_call_count": 0,
        "tool_result_count": 0,
        "tool_error_count": 0,
        "agent_run_ids": set(),
    }


def _record_tool_event_summary(summary: dict[str, object], event: RagStreamEvent) -> None:
    if not isinstance(event.payload, ToolCallEventPayload | ToolResultEventPayload):
        return
    summary["tool_event_count"] = _safe_int(summary.get("tool_event_count")) + 1
    if isinstance(event.payload, ToolCallEventPayload):
        summary["tool_call_count"] = _safe_int(summary.get("tool_call_count")) + 1
    else:
        summary["tool_result_count"] = _safe_int(summary.get("tool_result_count")) + 1
        if event.payload.status == "error" or event.payload.metadata.get("error_code"):
            summary["tool_error_count"] = _safe_int(summary.get("tool_error_count")) + 1
    agent_run_id = event.payload.metadata.get("agent_run_id")
    agent_run_ids = summary.get("agent_run_ids")
    if isinstance(agent_run_id, str) and isinstance(agent_run_ids, set):
        agent_run_ids.add(agent_run_id)


def _event_with_tool_event_summary(
    *,
    event: RagStreamEvent,
    summary: Mapping[str, object],
) -> RagStreamEvent:
    if not isinstance(event.payload, FinalEventPayload):
        return event
    count_summary = _safe_tool_event_count_summary(summary)
    if _safe_int(count_summary.get("tool_event_count")) <= 0:
        return event
    metadata = {
        **dict(event.payload.metadata),
        "tool_event_summary": count_summary,
    }
    return RagStreamEvent(
        event="final",
        payload=event.payload.model_copy(update={"metadata": metadata}),
    )


def _safe_tool_event_count_summary(summary: Mapping[str, object]) -> dict[str, object]:
    agent_run_ids = summary.get("agent_run_ids")
    ids = sorted(agent_run_ids) if isinstance(agent_run_ids, set) else []
    if not ids and isinstance(summary.get("agent_run_id"), str):
        ids = [cast(str, summary["agent_run_id"])]
    result: dict[str, object] = {
        "tool_event_count": _safe_int(summary.get("tool_event_count")),
        "tool_call_count": _safe_int(summary.get("tool_call_count")),
        "tool_result_count": _safe_int(summary.get("tool_result_count")),
        "tool_error_count": _safe_int(summary.get("tool_error_count")),
        "agent_run_id_count": len(ids),
    }
    if len(ids) == 1:
        result["agent_run_id"] = ids[0]
    return result


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _safe_number(value: object) -> float | int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, float):
        return max(value, 0.0)
    return None

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import suppress
from datetime import UTC, datetime
from time import perf_counter as default_perf_counter
from typing import Protocol, cast

from packages.auth.policies import has_rag_query_permission
from packages.common.audit import AuditEvent, AuditPort, AuditResource, AuditStatus
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError
from packages.memory import ChatMemoryService, PackedChatHistory
from packages.rag.dto import (
    ChatHistoryMessageResponse,
    ChatHistoryResponse,
    ChatResponse,
    Citation,
    PromptHistoryMessage,
    PromptMemoryContext,
    QueryCommand,
    QueryResponse,
)
from packages.rag.exceptions import (
    RAG_QUERY_CLIENT_DISCONNECTED,
    RAG_QUERY_FAILED,
    RAG_QUERY_FORBIDDEN,
    RagQueryError,
)
from packages.rag.streaming import FinalEventPayload, RagStreamEvent, final_event, safe_error_event

_DEFAULT_NO_ANSWER = "无法从给定上下文确认。"


class ChatRagQueryService(Protocol):
    async def query(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        memory_context: PromptMemoryContext | None = None,
    ) -> QueryResponse: ...

    def stream_query(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        memory_context: PromptMemoryContext | None = None,
    ) -> AsyncIterator[RagStreamEvent]: ...


class ChatApplicationService:
    def __init__(
        self,
        *,
        memory_service: ChatMemoryService,
        rag_query_service: ChatRagQueryService,
        audit: AuditPort,
        perf_counter: Callable[[], float] | None = None,
    ) -> None:
        self._memory_service = memory_service
        self._rag_query_service = rag_query_service
        self._audit = audit
        self._perf_counter = perf_counter or default_perf_counter

    async def chat(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        session_id: str | None,
    ) -> ChatResponse:
        started = self._perf_counter()
        chat_context = context
        effective_session_id = session_id or context.session_id
        try:
            _ensure_chat_permission(context)
            session = await self._memory_service.get_or_create_session(
                context=context,
                session_id=effective_session_id,
                query=command.query,
            )
            chat_context = context.model_copy(update={"session_id": session.id})
            history = await self._memory_service.load_packed_history(
                context=chat_context,
                session_id=session.id,
            )
            memory_context = _prompt_memory_context(history)
            await self._memory_service.append_user_message(
                context=chat_context,
                session_id=session.id,
                content=command.query,
            )
            await self._memory_service.commit()
            response = await self._rag_query_service.query(
                context=chat_context,
                command=command,
                memory_context=memory_context,
            )
            _validate_response_identity(response=response, context=chat_context)
            await self._memory_service.append_assistant_message(
                context=chat_context,
                session_id=session.id,
                content=response.answer,
                citations_metadata={
                    "citation_count": len(response.citations),
                    "citations": _citation_summaries(response.citations),
                    "unsupported_count": len(response.unsupported_claims),
                },
                no_answer=response.no_answer,
            )
            chat_response = _chat_response(response=response, session_id=session.id)
            await self._memory_service.commit()
            await self._record_chat_audit(
                context=chat_context,
                response=chat_response,
                action="rag.chat",
                status=AuditStatus.SUCCESS,
                latency_ms=_elapsed_ms(self._perf_counter() - started),
                error_code=None,
            )
            return chat_response
        except asyncio.CancelledError:
            error = RagQueryError(
                code=RAG_QUERY_CLIENT_DISCONNECTED,
                message="Chat query was cancelled by the client.",
                details={
                    "request_id": chat_context.request_id,
                    "trace_id": chat_context.trace_id,
                    "tenant_id": chat_context.auth.tenant_id,
                    "user_id": chat_context.auth.user_id,
                    "stage": "client_disconnect",
                    "error_code": RAG_QUERY_CLIENT_DISCONNECTED,
                },
                status_code=499,
            )
            await self._record_chat_failure_audit(
                context=chat_context,
                command=command,
                action="rag.chat",
                latency_ms=_elapsed_ms(self._perf_counter() - started),
                error=error,
                session_id=chat_context.session_id or effective_session_id,
            )
            raise
        except DomainError as exc:
            await self._memory_service.rollback()
            await self._record_chat_failure_audit(
                context=chat_context,
                command=command,
                action="rag.chat",
                latency_ms=_elapsed_ms(self._perf_counter() - started),
                error=exc,
                session_id=chat_context.session_id or effective_session_id,
            )
            raise
        except Exception as exc:
            await self._memory_service.rollback()
            wrapped = _chat_failed_error(context=chat_context, stage="chat")
            await self._record_chat_failure_audit(
                context=chat_context,
                command=command,
                action="rag.chat",
                latency_ms=_elapsed_ms(self._perf_counter() - started),
                error=wrapped,
                session_id=chat_context.session_id or effective_session_id,
            )
            raise wrapped from exc

    async def stream_chat(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        session_id: str | None,
    ) -> AsyncIterator[RagStreamEvent]:
        started = self._perf_counter()
        event_counts = _empty_event_counts()
        chat_context = context
        effective_session_id = session_id or context.session_id
        seen_final = False
        try:
            _ensure_chat_permission(context)
            session = await self._memory_service.get_or_create_session(
                context=context,
                session_id=effective_session_id,
                query=command.query,
            )
            chat_context = context.model_copy(update={"session_id": session.id})
            history = await self._memory_service.load_packed_history(
                context=chat_context,
                session_id=session.id,
            )
            memory_context = _prompt_memory_context(history)
            await self._memory_service.append_user_message(
                context=chat_context,
                session_id=session.id,
                content=command.query,
            )
            await self._memory_service.commit()

            async for event in self._rag_query_service.stream_query(
                context=chat_context,
                command=command,
                memory_context=memory_context,
            ):
                event_counts[event.event] = event_counts.get(event.event, 0) + 1
                if event.event != "final":
                    yield event
                    continue

                seen_final = True
                final_payload = cast(FinalEventPayload, event.payload)
                _validate_final_identity(payload=final_payload, context=chat_context)
                error_code = _metadata_error_code(final_payload.metadata)
                await self._memory_service.append_assistant_message(
                    context=chat_context,
                    session_id=session.id,
                    content=_assistant_storage_content(final_payload, error_code=error_code),
                    citations_metadata={
                        "citation_count": len(final_payload.citations),
                        "citations": _citation_summaries(final_payload.citations),
                        "unsupported_count": len(final_payload.unsupported_claims),
                    },
                    no_answer=final_payload.no_answer,
                    error_code=error_code,
                )
                await self._memory_service.commit()
                metadata = _with_session_metadata(final_payload.metadata, session_id=session.id)
                metadata["event_counts"] = dict(event_counts)
                enriched = final_event(
                    request_id=final_payload.request_id,
                    trace_id=final_payload.trace_id,
                    session_id=session.id,
                    tenant_id=final_payload.tenant_id,
                    user_id=final_payload.user_id,
                    answer=final_payload.answer,
                    citations=final_payload.citations,
                    no_answer=final_payload.no_answer,
                    unsupported_claims=final_payload.unsupported_claims,
                    metadata=metadata,
                    status=final_payload.status,
                )
                await self._record_chat_audit(
                    context=chat_context,
                    response=_chat_response_from_final(
                        cast(FinalEventPayload, enriched.payload),
                        session_id=session.id,
                    ),
                    action="rag.chat.stream",
                    status=AuditStatus.SUCCESS
                    if final_payload.status == "success"
                    else AuditStatus.FAILURE,
                    latency_ms=_elapsed_ms(self._perf_counter() - started),
                    error_code=error_code,
                )
                yield enriched

            if not seen_final:
                error = _chat_failed_error(context=chat_context, stage="stream_missing_final")
                await self._record_chat_failure_audit(
                    context=chat_context,
                    command=command,
                    action="rag.chat.stream",
                    latency_ms=_elapsed_ms(self._perf_counter() - started),
                    error=error,
                    session_id=chat_context.session_id or effective_session_id,
                    event_counts=event_counts,
                )
                event_counts["error"] += 1
                yield safe_error_event(
                    request_id=chat_context.request_id,
                    trace_id=chat_context.trace_id,
                    code=error.code,
                    message=error.message,
                    details=error.details,
                    terminal=True,
                )
                event_counts["final"] += 1
                yield _error_final_event(
                    context=chat_context,
                    session_id=chat_context.session_id or effective_session_id,
                    error=error,
                    event_counts=event_counts,
                )
        except asyncio.CancelledError:
            error = RagQueryError(
                code=RAG_QUERY_CLIENT_DISCONNECTED,
                message="Chat query stream was cancelled by the client.",
                details={
                    "request_id": chat_context.request_id,
                    "trace_id": chat_context.trace_id,
                    "tenant_id": chat_context.auth.tenant_id,
                    "user_id": chat_context.auth.user_id,
                    "stage": "client_disconnect",
                    "error_code": RAG_QUERY_CLIENT_DISCONNECTED,
                },
                status_code=499,
            )
            await self._record_chat_failure_audit(
                context=chat_context,
                command=command,
                action="rag.chat.stream",
                latency_ms=_elapsed_ms(self._perf_counter() - started),
                error=error,
                session_id=chat_context.session_id or effective_session_id,
                event_counts=event_counts,
            )
            raise
        except DomainError as exc:
            await self._memory_service.rollback()
            await self._record_chat_failure_audit(
                context=chat_context,
                command=command,
                action="rag.chat.stream",
                latency_ms=_elapsed_ms(self._perf_counter() - started),
                error=exc,
                session_id=chat_context.session_id or effective_session_id,
                event_counts=event_counts,
            )
            event_counts["error"] += 1
            yield safe_error_event(
                request_id=chat_context.request_id,
                trace_id=chat_context.trace_id,
                code=exc.code,
                message=exc.message,
                details=exc.details,
                terminal=True,
            )
            event_counts["final"] += 1
            yield _error_final_event(
                context=chat_context,
                session_id=chat_context.session_id or effective_session_id,
                error=exc,
                event_counts=event_counts,
            )
        except Exception as exc:
            await self._memory_service.rollback()
            wrapped = _chat_failed_error(context=chat_context, stage="chat_stream")
            await self._record_chat_failure_audit(
                context=chat_context,
                command=command,
                action="rag.chat.stream",
                latency_ms=_elapsed_ms(self._perf_counter() - started),
                error=wrapped,
                session_id=chat_context.session_id or effective_session_id,
                event_counts=event_counts,
            )
            event_counts["error"] += 1
            yield safe_error_event(
                request_id=chat_context.request_id,
                trace_id=chat_context.trace_id,
                code=wrapped.code,
                message=wrapped.message,
                details=wrapped.details,
                terminal=True,
            )
            event_counts["final"] += 1
            yield _error_final_event(
                context=chat_context,
                session_id=chat_context.session_id or effective_session_id,
                error=wrapped,
                event_counts=event_counts,
            )
            _ = exc

    async def history(
        self,
        *,
        context: AuthenticatedRequestContext,
        session_id: str,
        limit: int = 50,
    ) -> ChatHistoryResponse:
        _ensure_chat_permission(context)
        messages = await self._memory_service.list_session_messages(
            context=context,
            session_id=session_id,
            limit=limit,
        )
        return ChatHistoryResponse(
            session_id=session_id,
            messages=tuple(
                ChatHistoryMessageResponse(
                    role=message.role,
                    content=message.content,
                    sequence_no=message.sequence_no,
                    request_id=message.request_id,
                    trace_id=message.trace_id,
                    created_at=message.created_at.isoformat(),
                    citations=_citations_from_message_metadata(message.metadata),
                    no_answer=message.metadata.get("no_answer") is True,
                )
                for message in messages
                if message.role in {"user", "assistant"}
            ),
        )

    async def _record_chat_audit(
        self,
        *,
        context: AuthenticatedRequestContext,
        response: ChatResponse,
        action: str,
        status: AuditStatus,
        latency_ms: float,
        error_code: str | None,
    ) -> None:
        with suppress(Exception):
            await self._audit.record(
                AuditEvent(
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    tenant_id=context.auth.tenant_id,
                    user_id=context.auth.user_id,
                    action=action,
                    resource=AuditResource(
                        type="chat_session",
                        id=response.session_id,
                        metadata={
                            "request_id": context.request_id,
                            "trace_id": context.trace_id,
                            "session_id": response.session_id,
                        },
                    ),
                    status=status,
                    latency_ms=latency_ms,
                    error_code=error_code,
                    metadata=_chat_audit_metadata(response=response, error_code=error_code),
                    created_at=datetime.now(tz=UTC),
                )
            )

    async def _record_chat_failure_audit(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        action: str,
        latency_ms: float,
        error: DomainError,
        session_id: str | None,
        event_counts: Mapping[str, int] | None = None,
    ) -> None:
        response = _failure_chat_response(
            context=context,
            command=command,
            session_id=session_id,
            latency_ms=latency_ms,
            error=error,
            event_counts=event_counts,
        )
        await self._record_chat_audit(
            context=context,
            response=response,
            action=action,
            status=AuditStatus.FAILURE
            if error.code != RAG_QUERY_FORBIDDEN
            else AuditStatus.DENIED,
            latency_ms=latency_ms,
            error_code=error.code,
        )


def _prompt_memory_context(history: PackedChatHistory) -> PromptMemoryContext:
    return PromptMemoryContext(
        session_id=history.session_id,
        messages=tuple(
            PromptHistoryMessage(
                role=message.role,
                content=message.content,
                token_count=message.token_count,
                sequence_no=message.sequence_no,
            )
            for message in history.messages
        ),
        message_count=history.message_count,
        used_count=history.used_count,
        dropped_count=history.dropped_count,
        token_count=history.token_count,
    )


def _chat_response(*, response: QueryResponse, session_id: str) -> ChatResponse:
    return ChatResponse(**response.model_dump(), session_id=session_id)


def _chat_response_from_final(payload: FinalEventPayload, *, session_id: str) -> ChatResponse:
    return ChatResponse(
        request_id=payload.request_id,
        trace_id=payload.trace_id,
        tenant_id=payload.tenant_id,
        user_id=payload.user_id,
        session_id=session_id,
        answer=payload.answer,
        citations=payload.citations,
        no_answer=payload.no_answer,
        unsupported_claims=payload.unsupported_claims,
        metadata=_with_session_metadata(payload.metadata, session_id=session_id),
    )


def _with_session_metadata(metadata: Mapping[str, object], *, session_id: str) -> dict[str, object]:
    return {**dict(metadata), "session_id": session_id}


def _chat_audit_metadata(*, response: ChatResponse, error_code: str | None) -> dict[str, object]:
    memory = response.metadata.get("memory")
    if not isinstance(memory, Mapping):
        memory = {}
    retrieval = response.metadata.get("retrieval")
    if not isinstance(retrieval, Mapping):
        retrieval = {}
    generation = response.metadata.get("generation")
    if not isinstance(generation, Mapping):
        generation = {}
    context = response.metadata.get("context")
    if not isinstance(context, Mapping):
        context = {}
    stream = response.metadata.get("stream")
    if not isinstance(stream, Mapping):
        stream = {}
    event_counts = response.metadata.get("event_counts") or stream.get("event_counts")
    return {
        "request_id": response.request_id,
        "trace_id": response.trace_id,
        "tenant_id": response.tenant_id,
        "user_id": response.user_id,
        "session_id": response.session_id,
        "latency_ms": response.metadata.get("latency_ms"),
        "top_k": retrieval.get("top_k"),
        "result_count": retrieval.get("result_count"),
        "memory_message_count": memory.get("message_count", 0),
        "memory_used_count": memory.get("used_count", 0),
        "memory_dropped_count": memory.get("dropped_count", 0),
        "context_item_count": context.get("item_count"),
        "context_source_count": context.get("citation_source_count"),
        "provider": generation.get("provider"),
        "model": generation.get("model"),
        "version": generation.get("version"),
        "token_usage": generation.get("token_usage"),
        "event_counts": event_counts,
        "citation_count": len(response.citations),
        "unsupported_count": len(response.unsupported_claims),
        "tool_calls": 0,
        "error_code": error_code,
    }


def _citation_summaries(citations: tuple[object, ...]) -> list[dict[str, object]]:
    summaries: list[dict[str, object]] = []
    for citation in citations:
        document_id = getattr(citation, "document_id", None)
        version_id = getattr(citation, "version_id", None)
        chunk_id = getattr(citation, "chunk_id", None)
        retrieval_method = getattr(citation, "retrieval_method", None)
        score = getattr(citation, "score", None)
        source_display_name = getattr(citation, "source_display_name", None)
        source_type = getattr(citation, "source_type", None)
        title_path = getattr(citation, "title_path", None)
        page_start = getattr(citation, "page_start", None)
        page_end = getattr(citation, "page_end", None)
        if not all(isinstance(value, str) and value.strip() for value in (
            document_id,
            version_id,
            chunk_id,
        )):
            continue
        summary: dict[str, object] = {
            "document_id": document_id,
            "version_id": version_id,
            "chunk_id": chunk_id,
        }
        if isinstance(retrieval_method, str) and retrieval_method.strip():
            summary["retrieval_method"] = retrieval_method
        if isinstance(source_display_name, str) and source_display_name.strip():
            summary["source_display_name"] = source_display_name
        if isinstance(source_type, str) and source_type.strip():
            summary["source_type"] = source_type
        if isinstance(title_path, tuple | list):
            safe_title_path = tuple(
                item.strip()
                for item in title_path
                if isinstance(item, str) and item.strip()
            )
            if safe_title_path:
                summary["title_path"] = safe_title_path
        if isinstance(page_start, int) and not isinstance(page_start, bool):
            summary["page_start"] = page_start
        if isinstance(page_end, int) and not isinstance(page_end, bool):
            summary["page_end"] = page_end
        if isinstance(score, int | float) and not isinstance(score, bool):
            summary["score"] = float(score)
        summaries.append(summary)
    return summaries


def _citations_from_message_metadata(metadata: Mapping[str, object]) -> tuple[Citation, ...]:
    raw_citations = metadata.get("citations")
    if not isinstance(raw_citations, list | tuple):
        return ()
    citations: list[Citation] = []
    for item in raw_citations:
        if not isinstance(item, Mapping):
            continue
        try:
            citations.append(
                Citation(
                    document_id=str(item.get("document_id", "")).strip(),
                    version_id=str(item.get("version_id", "")).strip(),
                    chunk_id=str(item.get("chunk_id", "")).strip(),
                    source_display_name=_safe_metadata_text(item.get("source_display_name"))
                    or str(item.get("document_id", "")).strip(),
                    source_type=_safe_metadata_text(item.get("source_type")) or "unknown",
                    title_path=_safe_title_path(
                        item.get("title_path"),
                        fallback=str(item.get("document_id", "")).strip(),
                    ),
                    retrieval_method=_safe_metadata_text(item.get("retrieval_method")) or "unknown",
                    score=_safe_score(item.get("score")),
                    page_start=_safe_optional_int(item.get("page_start")),
                    page_end=_safe_optional_int(item.get("page_end")),
                )
            )
        except ValueError:
            continue
    return tuple(citations)


def _safe_metadata_text(value: object) -> str | None:
    if isinstance(value, str):
        normalized = value.strip()
        return normalized or None
    return None


def _safe_title_path(value: object, *, fallback: str) -> tuple[str, ...]:
    if isinstance(value, tuple | list):
        items = tuple(item.strip() for item in value if isinstance(item, str) and item.strip())
        if items:
            return items
    return (fallback or "source",)


def _safe_optional_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _safe_score(value: object) -> float:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return max(0.0, min(float(value), 1.0))
    return 0.0


def _elapsed_ms(elapsed_seconds: float) -> float:
    return round(max(elapsed_seconds, 0.0) * 1000, 3)


def _empty_event_counts() -> dict[str, int]:
    return {
        "token": 0,
        "citation": 0,
        "error": 0,
        "final": 0,
        "tool_call": 0,
        "tool_result": 0,
    }


def _ensure_chat_permission(context: AuthenticatedRequestContext) -> None:
    if has_rag_query_permission(context.auth):
        return
    raise RagQueryError(
        code=RAG_QUERY_FORBIDDEN,
        message="RAG query permission is required.",
        details={
            "request_id": context.request_id,
            "trace_id": context.trace_id,
            "tenant_id": context.auth.tenant_id,
            "user_id": context.auth.user_id,
            "required_permissions": ["document:read", "retrieval:query"],
            "error_code": RAG_QUERY_FORBIDDEN,
        },
        status_code=403,
    )


def _validate_response_identity(
    *,
    response: QueryResponse,
    context: AuthenticatedRequestContext,
) -> None:
    if response.tenant_id == context.auth.tenant_id and response.user_id == context.auth.user_id:
        return
    raise _chat_failed_error(context=context, stage="identity_mismatch")


def _validate_final_identity(
    *,
    payload: FinalEventPayload,
    context: AuthenticatedRequestContext,
) -> None:
    if payload.tenant_id == context.auth.tenant_id and payload.user_id == context.auth.user_id:
        return
    raise _chat_failed_error(context=context, stage="stream_identity_mismatch")


def _chat_failed_error(*, context: AuthenticatedRequestContext, stage: str) -> RagQueryError:
    return RagQueryError(
        code=RAG_QUERY_FAILED,
        message="Chat RAG query failed.",
        details={
            "request_id": context.request_id,
            "trace_id": context.trace_id,
            "tenant_id": context.auth.tenant_id,
            "user_id": context.auth.user_id,
            "stage": stage,
            "error_code": RAG_QUERY_FAILED,
        },
        status_code=500,
    )


def _failure_chat_response(
    *,
    context: AuthenticatedRequestContext,
    command: QueryCommand,
    session_id: str | None,
    latency_ms: float,
    error: DomainError,
    event_counts: Mapping[str, int] | None = None,
) -> ChatResponse:
    safe_session_id = (session_id or context.session_id or "unknown").strip() or "unknown"
    metadata: dict[str, object] = {
        "latency_ms": latency_ms,
        "error_code": error.code,
        "retrieval": {"top_k": command.top_k, "result_count": 0},
        "context": {"item_count": 0, "citation_source_count": 0},
        "generation": {"provider": None, "model": None, "version": None, "token_usage": None},
        "memory": {"message_count": 0, "used_count": 0, "dropped_count": 0},
    }
    if event_counts is not None:
        metadata["event_counts"] = dict(event_counts)
    return ChatResponse(
        request_id=context.request_id,
        trace_id=context.trace_id,
        tenant_id=context.auth.tenant_id,
        user_id=context.auth.user_id,
        session_id=safe_session_id,
        answer=_DEFAULT_NO_ANSWER,
        no_answer=True,
        metadata=metadata,
    )


def _metadata_error_code(metadata: Mapping[str, object]) -> str | None:
    error_code = metadata.get("error_code")
    if error_code is None:
        return None
    return str(error_code)


def _assistant_storage_content(payload: FinalEventPayload, *, error_code: str | None) -> str:
    if error_code is not None or payload.status == "error":
        return f"stream_error:{error_code or 'unknown'}"
    return payload.answer


def _error_final_event(
    *,
    context: AuthenticatedRequestContext,
    session_id: str | None,
    error: DomainError,
    event_counts: Mapping[str, int],
) -> RagStreamEvent:
    return final_event(
        request_id=context.request_id,
        trace_id=context.trace_id,
        session_id=session_id,
        tenant_id=context.auth.tenant_id,
        user_id=context.auth.user_id,
        answer=_DEFAULT_NO_ANSWER,
        citations=(),
        no_answer=True,
        unsupported_claims=(),
        metadata={
            "session_id": session_id,
            "error_code": error.code,
            "event_counts": dict(event_counts),
        },
        status="error",
    )

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter as default_perf_counter
from typing import Protocol

from packages.auth.context import AuthContext
from packages.auth.policies import has_rag_query_permission
from packages.common.audit import AuditEvent, AuditPort, AuditResource, AuditStatus
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError
from packages.rag.citation_extractor import CitationExtractor
from packages.rag.context_packer import ContextPacker
from packages.rag.dto import (
    Citation,
    ContextPackingConfig,
    PackedContext,
    PromptBuilderConfig,
    PromptBuildRequest,
    PromptBuildResult,
    PromptMemoryContext,
    QueryCommand,
    QueryResponse,
)
from packages.rag.exceptions import (
    RAG_QUERY_CLIENT_DISCONNECTED,
    RAG_QUERY_CONTEXT_UNAVAILABLE,
    RAG_QUERY_FAILED,
    RAG_QUERY_FORBIDDEN,
    RagCitationExtractionError,
    RagContextPackingError,
    RagGenerationError,
    RagPromptBuildError,
    RagQueryError,
)
from packages.rag.generation import RagGenerationService
from packages.rag.hydration import RetrievalCandidateHydrator
from packages.rag.prompt_builder import PromptBuilder
from packages.rag.streaming import (
    RagStreamEvent,
    citation_event,
    final_event,
    safe_error_event,
    token_event,
)
from packages.retrieval.dto import RetrievalRequest, RetrievalResult

logger = logging.getLogger(__name__)


class QueryRetrievalService(Protocol):
    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        auth: AuthContext | None,
    ) -> RetrievalResult: ...


class RagQueryApplicationService:
    def __init__(
        self,
        *,
        retrieval_service: QueryRetrievalService,
        hydrator: RetrievalCandidateHydrator,
        context_packer: ContextPacker,
        prompt_builder: PromptBuilder,
        generation_service: RagGenerationService,
        citation_extractor: CitationExtractor,
        audit: AuditPort,
        context_packing_config: ContextPackingConfig | None = None,
        prompt_builder_config: PromptBuilderConfig | None = None,
        clock: Callable[[], datetime] | None = None,
        perf_counter: Callable[[], float] | None = None,
    ) -> None:
        self._retrieval_service = retrieval_service
        self._hydrator = hydrator
        self._context_packer = context_packer
        self._prompt_builder = prompt_builder
        self._generation_service = generation_service
        self._citation_extractor = citation_extractor
        self._audit = audit
        self._context_packing_config = context_packing_config or ContextPackingConfig()
        self._prompt_builder_config = prompt_builder_config or PromptBuilderConfig()
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self._perf_counter = perf_counter or default_perf_counter

    async def query(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        memory_context: PromptMemoryContext | None = None,
    ) -> QueryResponse:
        started = self._perf_counter()
        if not has_rag_query_permission(context.auth):
            latency_ms = _elapsed_ms(self._perf_counter() - started)
            error = _query_forbidden_error(context)
            await self._record_denied(
                context=context,
                command=command,
                latency_ms=latency_ms,
                error=error,
            )
            raise error

        try:
            prepared = await self._prepare_query_context(
                context=context,
                command=command,
                memory_context=memory_context,
            )
            if prepared.prompt is None:
                response = self._no_answer_response(
                    context=context,
                    command=command,
                    retrieval=prepared.retrieval,
                    latency_ms=_elapsed_ms(self._perf_counter() - started),
                    context_item_count=prepared.context_item_count,
                    citation_source_count=prepared.citation_source_count,
                    memory_context=prepared.memory_context,
                )
                await self._record_audit(
                    context=context,
                    response=response,
                    status=AuditStatus.SUCCESS,
                    error_code=None,
                )
                return response

            assert prepared.packed_context is not None
            generation = await self._generation_service.generate(
                prompt=prepared.prompt,
                context=context,
            )
            extraction = self._citation_extractor.extract(
                answer=generation.text,
                packed_context=prepared.packed_context,
                citation_source_ids=prepared.prompt.citation_source_ids,
                config=None,
            )
            latency_ms = _elapsed_ms(self._perf_counter() - started)
            response = QueryResponse(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                answer=extraction.answer,
                citations=extraction.citations,
                no_answer=extraction.no_answer,
                unsupported_claims=extraction.unsupported_claims,
                metadata=_response_metadata(
                    command=command,
                    retrieval=prepared.retrieval,
                    context_item_count=prepared.context_item_count,
                    citation_source_count=extraction.trace.input_source_count,
                    prompt_risk_count=prepared.prompt.trace.detected_risk_count,
                    generation_metadata=generation.metadata.model_dump(),
                    citation_count=len(extraction.citations),
                    unsupported_count=len(extraction.unsupported_claims),
                    forged_reference_count=extraction.trace.forged_reference_count,
                    latency_ms=latency_ms,
                    error_code=None,
                    memory_context=prepared.memory_context,
                ),
            )
            await self._record_audit(
                context=context,
                response=response,
                status=AuditStatus.SUCCESS,
                error_code=None,
            )
            return response
        except DomainError as exc:
            latency_ms = _elapsed_ms(self._perf_counter() - started)
            await self._record_failure(
                context=context,
                command=command,
                latency_ms=latency_ms,
                error=exc,
            )
            raise
        except Exception as exc:
            latency_ms = _elapsed_ms(self._perf_counter() - started)
            wrapped = RagQueryError(
                code=RAG_QUERY_FAILED,
                message="RAG query failed.",
                details={
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "tenant_id": context.auth.tenant_id,
                    "user_id": context.auth.user_id,
                    "stage": "query",
                    "error_code": RAG_QUERY_FAILED,
                },
                status_code=500,
            )
            await self._record_failure(
                context=context,
                command=command,
                latency_ms=latency_ms,
                error=wrapped,
            )
            raise wrapped from exc

    async def stream_query(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        memory_context: PromptMemoryContext | None = None,
    ) -> AsyncIterator[RagStreamEvent]:
        started = self._perf_counter()
        event_counts = _empty_stream_event_counts()
        prepared: _PreparedQueryContext | None = None
        generation_metadata: Mapping[str, object] | None = None
        completed = False
        if not has_rag_query_permission(context.auth):
            latency_ms = _elapsed_ms(self._perf_counter() - started)
            error = _query_forbidden_error(context)
            await self._record_denied(
                context=context,
                command=command,
                latency_ms=latency_ms,
                error=error,
            )
            error_event = safe_error_event(
                request_id=context.request_id,
                trace_id=context.trace_id,
                code=error.code,
                message=error.message,
                details=_safe_stream_error_details(error=error, stage="authorization"),
                terminal=True,
            )
            event_counts["error"] += 1
            yield error_event
            event_counts["final"] += 1
            yield self._stream_error_final_event(
                context=context,
                command=command,
                latency_ms=latency_ms,
                error=error,
                event_counts=event_counts,
            )
            return

        try:
            prepared = await self._prepare_query_context(
                context=context,
                command=command,
                memory_context=memory_context,
            )
            if prepared.prompt is None:
                latency_ms = _elapsed_ms(self._perf_counter() - started)
                response = self._no_answer_response(
                    context=context,
                    command=command,
                    retrieval=prepared.retrieval,
                    latency_ms=latency_ms,
                    context_item_count=prepared.context_item_count,
                    citation_source_count=prepared.citation_source_count,
                    memory_context=prepared.memory_context,
                )
                event_counts["final"] += 1
                response = _with_stream_metadata(response, event_counts=event_counts)
                await self._record_stream_audit(
                    context=context,
                    response=response,
                    status=AuditStatus.SUCCESS,
                    error_code=None,
                    event_counts=event_counts,
                )
                completed = True
                yield _final_success_event(context=context, response=response)
                return

            assert prepared.packed_context is not None
            for citation in _citations_from_packed_context(prepared.packed_context):
                event_counts["citation"] += 1
                yield citation_event(
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    citation=citation,
                )

            final_response = None
            generation_metadata = self._generation_service.provider_summary()
            async for chunk in self._generation_service.stream(
                prompt=prepared.prompt,
                context=context,
            ):
                if chunk.is_final:
                    final_response = chunk.response
                    generation_metadata = (
                        chunk.response.metadata.model_dump()
                        if chunk.response is not None
                        else generation_metadata
                    )
                    continue
                event_counts["token"] += 1
                yield token_event(
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    index=chunk.index,
                    delta=chunk.delta,
                )

            if final_response is None:
                raise RagQueryError(
                    code=RAG_QUERY_FAILED,
                    message="RAG stream did not receive a final provider response.",
                    details={
                        "request_id": context.request_id,
                        "trace_id": context.trace_id,
                        "tenant_id": context.auth.tenant_id,
                        "user_id": context.auth.user_id,
                        "stage": "generation_stream",
                        "error_code": RAG_QUERY_FAILED,
                    },
                    status_code=502,
                )

            extraction = self._citation_extractor.extract(
                answer=final_response.text,
                packed_context=prepared.packed_context,
                citation_source_ids=prepared.prompt.citation_source_ids,
                config=None,
            )
            latency_ms = _elapsed_ms(self._perf_counter() - started)
            event_counts["final"] += 1
            response = QueryResponse(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                answer=extraction.answer,
                citations=extraction.citations,
                no_answer=extraction.no_answer,
                unsupported_claims=extraction.unsupported_claims,
                metadata=_stream_response_metadata(
                    command=command,
                    retrieval=prepared.retrieval,
                    context_item_count=prepared.context_item_count,
                    citation_source_count=extraction.trace.input_source_count,
                    prompt_risk_count=prepared.prompt.trace.detected_risk_count,
                    generation_metadata=final_response.metadata.model_dump(),
                    citation_count=len(extraction.citations),
                    unsupported_count=len(extraction.unsupported_claims),
                    forged_reference_count=extraction.trace.forged_reference_count,
                    latency_ms=latency_ms,
                    error_code=None,
                    event_counts=event_counts,
                    memory_context=prepared.memory_context,
                ),
            )
            await self._record_stream_audit(
                context=context,
                response=response,
                status=AuditStatus.SUCCESS,
                error_code=None,
                event_counts=event_counts,
            )
            completed = True
            yield _final_success_event(context=context, response=response)
        except asyncio.CancelledError:
            latency_ms = _elapsed_ms(self._perf_counter() - started)
            cancelled = RagQueryError(
                code=RAG_QUERY_CLIENT_DISCONNECTED,
                message="RAG query stream was cancelled by the client.",
                details={
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "tenant_id": context.auth.tenant_id,
                    "user_id": context.auth.user_id,
                    "stage": "client_disconnect",
                    "error_code": RAG_QUERY_CLIENT_DISCONNECTED,
                },
                status_code=499,
            )
            event_counts["error"] += 1
            await self._record_stream_failure(
                context=context,
                command=command,
                latency_ms=latency_ms,
                error=cancelled,
                event_counts=event_counts,
                prepared=prepared,
                generation_metadata=generation_metadata,
            )
            completed = True
            raise
        except DomainError as exc:
            latency_ms = _elapsed_ms(self._perf_counter() - started)
            stage = _stream_error_stage(exc)
            event_counts["error"] += 1
            yield safe_error_event(
                request_id=context.request_id,
                trace_id=context.trace_id,
                code=exc.code,
                message=exc.message,
                details=_safe_stream_error_details(error=exc, stage=stage),
                terminal=True,
            )
            event_counts["final"] += 1
            await self._record_stream_failure(
                context=context,
                command=command,
                latency_ms=latency_ms,
                error=exc,
                event_counts=event_counts,
                prepared=prepared,
                generation_metadata=generation_metadata,
            )
            completed = True
            yield self._stream_error_final_event(
                context=context,
                command=command,
                latency_ms=latency_ms,
                error=exc,
                event_counts=event_counts,
                prepared=prepared,
                generation_metadata=generation_metadata,
            )
        except Exception as exc:
            latency_ms = _elapsed_ms(self._perf_counter() - started)
            wrapped = RagQueryError(
                code=RAG_QUERY_FAILED,
                message="RAG query stream failed.",
                details={
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "tenant_id": context.auth.tenant_id,
                    "user_id": context.auth.user_id,
                    "stage": "stream_query",
                    "error_code": RAG_QUERY_FAILED,
                },
                status_code=500,
            )
            event_counts["error"] += 1
            yield safe_error_event(
                request_id=context.request_id,
                trace_id=context.trace_id,
                code=wrapped.code,
                message=wrapped.message,
                details=_safe_stream_error_details(error=wrapped, stage="stream_query"),
                terminal=True,
            )
            event_counts["final"] += 1
            await self._record_stream_failure(
                context=context,
                command=command,
                latency_ms=latency_ms,
                error=wrapped,
                event_counts=event_counts,
                prepared=prepared,
                generation_metadata=generation_metadata,
            )
            completed = True
            yield self._stream_error_final_event(
                context=context,
                command=command,
                latency_ms=latency_ms,
                error=wrapped,
                event_counts=event_counts,
                prepared=prepared,
                generation_metadata=generation_metadata,
            )
            _ = exc
        finally:
            if not completed and (
                event_counts["token"] > 0
                or event_counts["citation"] > 0
                or prepared is not None
            ):
                latency_ms = _elapsed_ms(self._perf_counter() - started)
                disconnected = RagQueryError(
                    code=RAG_QUERY_CLIENT_DISCONNECTED,
                    message="RAG query stream was closed before a terminal event.",
                    details={
                        "request_id": context.request_id,
                        "trace_id": context.trace_id,
                        "tenant_id": context.auth.tenant_id,
                        "user_id": context.auth.user_id,
                        "stage": "client_disconnect",
                        "error_code": RAG_QUERY_CLIENT_DISCONNECTED,
                    },
                    status_code=499,
                )
                event_counts["error"] += 1
                await self._record_stream_failure(
                    context=context,
                    command=command,
                    latency_ms=latency_ms,
                    error=disconnected,
                    event_counts=event_counts,
                    prepared=prepared,
                    generation_metadata=generation_metadata,
                )

    async def _prepare_query_context(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        memory_context: PromptMemoryContext | None = None,
    ) -> _PreparedQueryContext:
        retrieval_request = RetrievalRequest(
            query=command.query,
            top_k=command.top_k,
            metadata_filter=command.metadata_filter,
            score_threshold=command.score_threshold,
            request_id=context.request_id,
            trace_id=context.trace_id,
        )
        retrieval = await self._retrieval_service.retrieve(
            request=retrieval_request,
            auth=context.auth,
        )
        if not retrieval.candidates:
            return _PreparedQueryContext(
                retrieval=retrieval,
                packed_context=None,
                prompt=None,
                context_item_count=0,
                citation_source_count=0,
                memory_context=memory_context,
            )

        candidates = await self._hydrator.hydrate(
            candidates=retrieval.candidates,
            auth=context.auth,
            request_id=context.request_id,
            trace_id=context.trace_id,
        )
        packed_context = self._context_packer.pack(
            candidates=candidates,
            auth=context.auth,
            config=self._context_packing_config,
            request_id=context.request_id,
            trace_id=context.trace_id,
        )
        if not packed_context.items:
            return _PreparedQueryContext(
                retrieval=retrieval,
                packed_context=packed_context,
                prompt=None,
                context_item_count=0,
                citation_source_count=0,
                memory_context=memory_context,
            )

        prompt = self._prompt_builder.build(
            PromptBuildRequest(
                query=command.query,
                packed_context=packed_context,
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                session_id=context.session_id,
                answer_style=command.answer_style,
                max_output_tokens=command.max_output_tokens,
                memory_context=memory_context,
            ),
            config=self._prompt_builder_config,
        )
        return _PreparedQueryContext(
            retrieval=retrieval,
            packed_context=packed_context,
            prompt=prompt,
            context_item_count=len(packed_context.items),
            citation_source_count=sum(len(item.citation_sources) for item in packed_context.items),
            memory_context=memory_context,
        )

    def _no_answer_response(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        retrieval: RetrievalResult,
        latency_ms: float,
        context_item_count: int = 0,
        citation_source_count: int = 0,
        memory_context: PromptMemoryContext | None = None,
    ) -> QueryResponse:
        return QueryResponse(
            request_id=context.request_id,
            trace_id=context.trace_id,
            tenant_id=context.auth.tenant_id,
            user_id=context.auth.user_id,
            answer=self._prompt_builder_config.default_no_answer_text,
            citations=(),
            no_answer=True,
            unsupported_claims=(),
            metadata=_response_metadata(
                command=command,
                retrieval=retrieval,
                context_item_count=context_item_count,
                citation_source_count=citation_source_count,
                prompt_risk_count=0,
                generation_metadata=None,
                citation_count=0,
                unsupported_count=0,
                forged_reference_count=0,
                latency_ms=latency_ms,
                error_code=RAG_QUERY_CONTEXT_UNAVAILABLE,
                memory_context=memory_context,
            ),
        )

    async def _record_audit(
        self,
        *,
        context: AuthenticatedRequestContext,
        response: QueryResponse,
        status: AuditStatus,
        error_code: str | None,
    ) -> None:
        await self._audit.record(
            _audit_event(
                context=context,
                status=status,
                latency_ms=_metadata_latency(response.metadata),
                error_code=error_code,
                metadata={
                    "top_k": _nested_metadata_value(response.metadata, "retrieval", "top_k"),
                    "result_count": _nested_metadata_value(
                        response.metadata,
                        "retrieval",
                        "result_count",
                    ),
                    "context_item_count": _nested_metadata_value(
                        response.metadata,
                        "context",
                        "item_count",
                    ),
                    "context_source_count": _nested_metadata_value(
                        response.metadata,
                        "context",
                        "citation_source_count",
                    ),
                    "prompt_risk_count": _nested_metadata_value(
                        response.metadata,
                        "prompt_risk",
                        "detected_risk_count",
                    ),
                    "memory_message_count": _nested_metadata_value(
                        response.metadata,
                        "memory",
                        "message_count",
                    ),
                    "memory_used_count": _nested_metadata_value(
                        response.metadata,
                        "memory",
                        "used_count",
                    ),
                    "memory_dropped_count": _nested_metadata_value(
                        response.metadata,
                        "memory",
                        "dropped_count",
                    ),
                    "provider": _nested_metadata_value(
                        response.metadata,
                        "generation",
                        "provider",
                    ),
                    "model": _nested_metadata_value(
                        response.metadata,
                        "generation",
                        "model",
                    ),
                    "version": _nested_metadata_value(
                        response.metadata,
                        "generation",
                        "version",
                    ),
                    "token_usage": _nested_metadata_value(
                        response.metadata,
                        "generation",
                        "token_usage",
                    ),
                    "citation_count": len(response.citations),
                    "unsupported_count": len(response.unsupported_claims),
                    "forged_reference_count": _nested_metadata_value(
                        response.metadata,
                        "citation",
                        "forged_reference_count",
                    ),
                    "tool_calls": 0,
                    "error_code": error_code,
                },
            )
        )

    async def _record_stream_audit(
        self,
        *,
        context: AuthenticatedRequestContext,
        response: QueryResponse,
        status: AuditStatus,
        error_code: str | None,
        event_counts: Mapping[str, int],
    ) -> None:
        try:
            await self._audit.record(
                _audit_event(
                    context=context,
                    action="rag.query.stream",
                    status=status,
                    latency_ms=_metadata_latency(response.metadata),
                    error_code=error_code,
                    metadata={
                        "top_k": _nested_metadata_value(response.metadata, "retrieval", "top_k"),
                        "result_count": _nested_metadata_value(
                            response.metadata,
                            "retrieval",
                            "result_count",
                        ),
                        "context_item_count": _nested_metadata_value(
                            response.metadata,
                            "context",
                            "item_count",
                        ),
                        "context_source_count": _nested_metadata_value(
                            response.metadata,
                            "context",
                            "citation_source_count",
                        ),
                        "prompt_risk_count": _nested_metadata_value(
                            response.metadata,
                            "prompt_risk",
                            "detected_risk_count",
                        ),
                        "memory_message_count": _nested_metadata_value(
                            response.metadata,
                            "memory",
                            "message_count",
                        ),
                        "memory_used_count": _nested_metadata_value(
                            response.metadata,
                            "memory",
                            "used_count",
                        ),
                        "memory_dropped_count": _nested_metadata_value(
                            response.metadata,
                            "memory",
                            "dropped_count",
                        ),
                        "provider": _nested_metadata_value(
                            response.metadata,
                            "generation",
                            "provider",
                        ),
                        "model": _nested_metadata_value(
                            response.metadata,
                            "generation",
                            "model",
                        ),
                        "version": _nested_metadata_value(
                            response.metadata,
                            "generation",
                            "version",
                        ),
                        "token_usage": _nested_metadata_value(
                            response.metadata,
                            "generation",
                            "token_usage",
                        ),
                        "event_counts": _stream_event_counts_summary(event_counts),
                        "citation_count": len(response.citations),
                        "unsupported_count": len(response.unsupported_claims),
                        "forged_reference_count": _nested_metadata_value(
                            response.metadata,
                            "citation",
                            "forged_reference_count",
                        ),
                        "tool_calls": 0,
                        "error_code": error_code,
                    },
                )
            )
        except Exception:
            logger.warning(
                "rag.query.stream.audit_failed",
                extra={
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "tenant_id": context.auth.tenant_id,
                    "user_id": context.auth.user_id,
                    "audit_action": "rag.query.stream",
                    "audit_status": status.value,
                    "error_code": error_code,
                },
                exc_info=True,
            )

    async def _record_denied(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        latency_ms: float,
        error: DomainError,
    ) -> None:
        with suppress(Exception):
            await self._audit.record(
                _audit_event(
                    context=context,
                    status=AuditStatus.DENIED,
                    latency_ms=latency_ms,
                    error_code=error.code,
                    metadata={
                        "top_k": command.top_k,
                        "result_count": 0,
                        "context_item_count": 0,
                        "context_source_count": 0,
                        "prompt_risk_count": 0,
                        "memory_message_count": 0,
                        "memory_used_count": 0,
                        "memory_dropped_count": 0,
                        "provider": None,
                        "model": None,
                        "version": None,
                        "token_usage": None,
                        "citation_count": 0,
                        "unsupported_count": 0,
                        "forged_reference_count": 0,
                        "tool_calls": 0,
                        "error_code": error.code,
                    },
                )
            )

    async def _record_failure(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        latency_ms: float,
        error: DomainError,
    ) -> None:
        with suppress(Exception):
            await self._audit.record(
                _audit_event(
                    context=context,
                    status=AuditStatus.FAILURE,
                    latency_ms=latency_ms,
                    error_code=error.code,
                    metadata={
                        "top_k": command.top_k,
                        "result_count": 0,
                        "context_item_count": 0,
                        "context_source_count": 0,
                        "prompt_risk_count": 0,
                        "memory_message_count": 0,
                        "memory_used_count": 0,
                        "memory_dropped_count": 0,
                        "provider": None,
                        "model": None,
                        "version": None,
                        "token_usage": None,
                        "citation_count": 0,
                        "unsupported_count": 0,
                        "forged_reference_count": 0,
                        "tool_calls": 0,
                        "error_code": error.code,
                        "error_details": error.details,
                    },
                )
            )

    async def _record_stream_failure(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        latency_ms: float,
        error: DomainError,
        event_counts: Mapping[str, int],
        prepared: _PreparedQueryContext | None = None,
        generation_metadata: Mapping[str, object] | None = None,
    ) -> None:
        failure_metadata = _stream_failure_metadata(
            context=context,
            command=command,
            latency_ms=latency_ms,
            error=error,
            event_counts=event_counts,
            prepared=prepared,
            generation_metadata=generation_metadata,
        )
        try:
            await self._audit.record(
                _audit_event(
                    context=context,
                    action="rag.query.stream",
                    status=AuditStatus.FAILURE,
                    latency_ms=latency_ms,
                    error_code=error.code,
                    metadata=failure_metadata,
                )
            )
        except Exception:
            logger.warning(
                "rag.query.stream.audit_failed",
                extra={
                    "request_id": context.request_id,
                    "trace_id": context.trace_id,
                    "tenant_id": context.auth.tenant_id,
                    "user_id": context.auth.user_id,
                    "audit_action": "rag.query.stream",
                    "audit_status": AuditStatus.FAILURE.value,
                    "error_code": error.code,
                },
                exc_info=True,
            )

    def _stream_error_final_event(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        latency_ms: float,
        error: DomainError,
        event_counts: Mapping[str, int],
        prepared: _PreparedQueryContext | None = None,
        generation_metadata: Mapping[str, object] | None = None,
    ) -> RagStreamEvent:
        retrieval = prepared.retrieval if prepared is not None else _empty_retrieval_result(
            context=context,
            command=command,
        )
        context_item_count = prepared.context_item_count if prepared is not None else 0
        citation_source_count = prepared.citation_source_count if prepared is not None else 0
        prompt_risk_count = (
            prepared.prompt.trace.detected_risk_count
            if prepared is not None and prepared.prompt is not None
            else 0
        )
        metadata = _stream_response_metadata(
            command=command,
            retrieval=retrieval,
            context_item_count=context_item_count,
            citation_source_count=citation_source_count,
            prompt_risk_count=prompt_risk_count,
            generation_metadata=generation_metadata,
            citation_count=event_counts.get("citation", 0),
            unsupported_count=0,
            forged_reference_count=0,
            latency_ms=latency_ms,
            error_code=error.code,
            event_counts=event_counts,
            memory_context=prepared.memory_context if prepared is not None else None,
        )
        return final_event(
            request_id=context.request_id,
            trace_id=context.trace_id,
            tenant_id=context.auth.tenant_id,
            user_id=context.auth.user_id,
            answer=self._prompt_builder_config.default_no_answer_text,
            citations=(),
            no_answer=True,
            unsupported_claims=(),
            metadata=metadata,
            status="error",
        )


def _response_metadata(
    *,
    command: QueryCommand,
    retrieval: RetrievalResult,
    context_item_count: int,
    citation_source_count: int,
    prompt_risk_count: int,
    generation_metadata: Mapping[str, object] | None,
    citation_count: int,
    unsupported_count: int,
    forged_reference_count: int,
    latency_ms: float,
    error_code: str | None,
    memory_context: PromptMemoryContext | None = None,
) -> dict[str, object]:
    generation = _safe_generation_metadata(generation_metadata)
    metadata = {
        "retrieval": {
            "top_k": command.top_k,
            "result_count": len(retrieval.candidates),
            "latency_ms": retrieval.latency_ms,
        },
        "context": {
            "item_count": context_item_count,
            "citation_source_count": citation_source_count,
        },
        "prompt_risk": {
            "detected_risk_count": prompt_risk_count,
        },
        "memory": _memory_metadata(memory_context),
        "generation": generation,
        "citation": {
            "citation_count": citation_count,
            "unsupported_count": unsupported_count,
            "forged_reference_count": forged_reference_count,
        },
        "latency_ms": latency_ms,
        "error_code": error_code,
    }
    return metadata


def _stream_response_metadata(
    *,
    command: QueryCommand,
    retrieval: RetrievalResult,
    context_item_count: int,
    citation_source_count: int,
    prompt_risk_count: int,
    generation_metadata: Mapping[str, object] | None,
    citation_count: int,
    unsupported_count: int,
    forged_reference_count: int,
    latency_ms: float,
    error_code: str | None,
    event_counts: Mapping[str, int],
    memory_context: PromptMemoryContext | None = None,
) -> dict[str, object]:
    metadata = _response_metadata(
        command=command,
        retrieval=retrieval,
        context_item_count=context_item_count,
        citation_source_count=citation_source_count,
        prompt_risk_count=prompt_risk_count,
        generation_metadata=generation_metadata,
        citation_count=citation_count,
        unsupported_count=unsupported_count,
        forged_reference_count=forged_reference_count,
        latency_ms=latency_ms,
        error_code=error_code,
        memory_context=memory_context,
    )
    metadata["stream"] = {"event_counts": _stream_event_counts_summary(event_counts)}
    return metadata


def _stream_failure_metadata(
    *,
    context: AuthenticatedRequestContext,
    command: QueryCommand,
    latency_ms: float,
    error: DomainError,
    event_counts: Mapping[str, int],
    prepared: _PreparedQueryContext | None,
    generation_metadata: Mapping[str, object] | None,
) -> dict[str, object]:
    retrieval = prepared.retrieval if prepared is not None else _empty_retrieval_result(
        context=context,
        command=command,
    )
    response_metadata = _stream_response_metadata(
        command=command,
        retrieval=retrieval,
        context_item_count=prepared.context_item_count if prepared is not None else 0,
        citation_source_count=prepared.citation_source_count if prepared is not None else 0,
        prompt_risk_count=(
            prepared.prompt.trace.detected_risk_count
            if prepared is not None and prepared.prompt is not None
            else 0
        ),
        generation_metadata=generation_metadata,
        citation_count=event_counts.get("citation", 0),
        unsupported_count=0,
        forged_reference_count=0,
        latency_ms=latency_ms,
        error_code=error.code,
        event_counts=event_counts,
        memory_context=prepared.memory_context if prepared is not None else None,
    )
    return {
        "top_k": _nested_metadata_value(response_metadata, "retrieval", "top_k"),
        "result_count": _nested_metadata_value(response_metadata, "retrieval", "result_count"),
        "context_item_count": _nested_metadata_value(response_metadata, "context", "item_count"),
        "context_source_count": _nested_metadata_value(
            response_metadata,
            "context",
            "citation_source_count",
        ),
        "prompt_risk_count": _nested_metadata_value(
            response_metadata,
            "prompt_risk",
            "detected_risk_count",
        ),
        "memory_message_count": _nested_metadata_value(
            response_metadata,
            "memory",
            "message_count",
        ),
        "memory_used_count": _nested_metadata_value(
            response_metadata,
            "memory",
            "used_count",
        ),
        "memory_dropped_count": _nested_metadata_value(
            response_metadata,
            "memory",
            "dropped_count",
        ),
        "provider": _nested_metadata_value(response_metadata, "generation", "provider"),
        "model": _nested_metadata_value(response_metadata, "generation", "model"),
        "version": _nested_metadata_value(response_metadata, "generation", "version"),
        "token_usage": _nested_metadata_value(response_metadata, "generation", "token_usage"),
        "event_counts": _stream_event_counts_summary(event_counts),
        "citation_count": event_counts.get("citation", 0),
        "unsupported_count": 0,
        "forged_reference_count": 0,
        "tool_calls": 0,
        "error_code": error.code,
        "error_details": _safe_stream_error_details(error=error, stage=_stream_error_stage(error)),
    }


def _with_stream_metadata(
    response: QueryResponse,
    *,
    event_counts: Mapping[str, int],
) -> QueryResponse:
    metadata = dict(response.metadata)
    metadata["stream"] = {"event_counts": _stream_event_counts_summary(event_counts)}
    return response.model_copy(update={"metadata": metadata})


def _safe_generation_metadata(metadata: Mapping[str, object] | None) -> dict[str, object]:
    if metadata is None:
        return {
            "provider": None,
            "model": None,
            "version": None,
            "token_usage": None,
        }
    usage = metadata.get("usage")
    if not isinstance(usage, Mapping):
        usage = {}
    return {
        "provider": metadata.get("provider"),
        "model": metadata.get("model"),
        "version": metadata.get("version"),
        "token_usage": {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
        "latency_ms": metadata.get("latency_ms"),
        "finish_reason": metadata.get("finish_reason"),
        "error_code": metadata.get("error_code"),
    }


def _memory_metadata(memory_context: PromptMemoryContext | None) -> dict[str, int]:
    if memory_context is None:
        return {
            "message_count": 0,
            "used_count": 0,
            "dropped_count": 0,
            "token_count": 0,
        }
    return {
        "message_count": memory_context.message_count,
        "used_count": memory_context.used_count,
        "dropped_count": memory_context.dropped_count,
        "token_count": memory_context.token_count,
    }


def _audit_event(
    *,
    context: AuthenticatedRequestContext,
    action: str = "rag.query",
    status: AuditStatus,
    latency_ms: float,
    error_code: str | None,
    metadata: Mapping[str, object],
) -> AuditEvent:
    return AuditEvent(
        request_id=context.request_id,
        trace_id=context.trace_id,
        tenant_id=context.auth.tenant_id,
        user_id=context.auth.user_id,
        action=action,
        resource=AuditResource(
            type="rag_query",
            id=context.request_id,
            metadata={"request_id": context.request_id, "trace_id": context.trace_id},
        ),
        status=status,
        latency_ms=latency_ms,
        error_code=error_code,
        metadata=dict(metadata),
        created_at=datetime.now(tz=UTC),
    )


def _metadata_latency(metadata: Mapping[str, object]) -> float:
    value = metadata.get("latency_ms")
    if isinstance(value, int | float):
        return float(value)
    return 0.0


def _nested_metadata_value(
    metadata: Mapping[str, object],
    parent_key: str,
    child_key: str,
) -> object:
    parent = metadata.get(parent_key)
    if not isinstance(parent, Mapping):
        return None
    return parent.get(child_key)


def _elapsed_ms(elapsed_seconds: float) -> float:
    return round(max(elapsed_seconds, 0.0) * 1000, 3)


def _empty_stream_event_counts() -> dict[str, int]:
    return {"token": 0, "citation": 0, "error": 0, "final": 0}


def _stream_event_counts_summary(event_counts: Mapping[str, int]) -> tuple[dict[str, object], ...]:
    return tuple(
        {"event": event_type, "count": int(event_counts.get(event_type, 0))}
        for event_type in ("token", "citation", "error", "final")
    )


def _citations_from_packed_context(packed_context: PackedContext) -> tuple[Citation, ...]:
    seen: set[tuple[str, str, str]] = set()
    citations: list[Citation] = []
    for item in packed_context.items:
        for source in item.citation_sources:
            identity = (source.document_id, source.version_id, source.chunk_id)
            if identity in seen:
                continue
            seen.add(identity)
            citations.append(Citation.from_source(source))
    return tuple(citations)


def _final_success_event(
    *,
    context: AuthenticatedRequestContext,
    response: QueryResponse,
) -> RagStreamEvent:
    return final_event(
        request_id=context.request_id,
        trace_id=context.trace_id,
        tenant_id=context.auth.tenant_id,
        user_id=context.auth.user_id,
        answer=response.answer,
        citations=response.citations,
        no_answer=response.no_answer,
        unsupported_claims=response.unsupported_claims,
        metadata=response.metadata,
        status="success",
    )


def _error_details(*, error: DomainError, stage: str) -> dict[str, object]:
    details = dict(error.details)
    details.setdefault("stage", stage)
    details.setdefault("error_code", error.code)
    return details


_STREAM_ERROR_DETAIL_ALLOWLIST = frozenset(
    {
        "request_id",
        "trace_id",
        "stage",
        "reason",
        "drop_reason",
        "error_code",
        "safe_counts",
        "required_permissions",
        "mismatched_fields",
        "retryable",
    }
)


def _safe_stream_error_details(*, error: DomainError, stage: str) -> dict[str, object]:
    details = _error_details(error=error, stage=stage)
    return {key: value for key, value in details.items() if key in _STREAM_ERROR_DETAIL_ALLOWLIST}


def _stream_error_stage(error: DomainError) -> str:
    explicit_stage = error.details.get("stage")
    if isinstance(explicit_stage, str) and explicit_stage.strip():
        return explicit_stage.strip()
    if isinstance(error, RagPromptBuildError) or error.code.startswith("RAG_PROMPT_"):
        return "prompt_build"
    if isinstance(error, RagContextPackingError) or error.code.startswith("RAG_CONTEXT_"):
        return "context_packing"
    if isinstance(error, RagGenerationError) or error.code.startswith(("RAG_GENERATION_", "LLM_")):
        return "generation_stream"
    if isinstance(error, RagCitationExtractionError) or error.code.startswith("RAG_CITATION_"):
        return "citation_extraction"
    if error.code.startswith("RETRIEVAL_"):
        return "retrieval"
    if error.code == RAG_QUERY_CONTEXT_UNAVAILABLE:
        return "hydration"
    if error.code == RAG_QUERY_CLIENT_DISCONNECTED:
        return "client_disconnect"
    return "stream_query"


def _empty_retrieval_result(
    *,
    context: AuthenticatedRequestContext,
    command: QueryCommand,
) -> RetrievalResult:
    return RetrievalResult(
        request_id=context.request_id,
        trace_id=context.trace_id,
        tenant_id=context.auth.tenant_id,
        user_id=context.auth.user_id,
        top_k=command.top_k,
        query_summary={"length": len(command.query)},
        candidates=(),
        latency_ms=0.0,
    )


def _query_forbidden_error(context: AuthenticatedRequestContext) -> RagQueryError:
    return RagQueryError(
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


@dataclass(frozen=True)
class _PreparedQueryContext:
    retrieval: RetrievalResult
    packed_context: PackedContext | None
    prompt: PromptBuildResult | None
    context_item_count: int
    citation_source_count: int
    memory_context: PromptMemoryContext | None

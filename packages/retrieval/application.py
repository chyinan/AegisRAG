from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime
from math import isfinite
from time import perf_counter as default_perf_counter

from pydantic import BaseModel, ConfigDict, Field, field_validator

from packages.common.audit import AuditEvent, AuditPort, AuditResource, AuditStatus
from packages.common.context import AuthenticatedRequestContext
from packages.common.logging import REDACTED_VALUE, redact_mapping, redact_sensitive_data
from packages.retrieval.dto import (
    RetrievalCandidate,
    RetrievalLogCreate,
    RetrievalRequest,
    RetrievalResult,
)
from packages.retrieval.exceptions import RetrievalError
from packages.retrieval.ports import RetrievalLogPort
from packages.retrieval.service import RetrievalService

_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_SENSITIVE_METADATA_KEYS = {
    "access_token",
    "api_key",
    "body",
    "chunk_content",
    "chunk_text",
    "content",
    "document_content",
    "embedding",
    "embedding_vector",
    "full_query",
    "password",
    "prompt",
    "provider_raw_response",
    "query",
    "query_vector",
    "raw_response",
    "secret",
    "sql",
    "text",
    "token",
    "tsquery",
    "tsvector",
    "vector",
}
_SENSITIVE_COMPACT_KEYS = {
    "".join(char for char in key if char.isalnum()) for key in _SENSITIVE_METADATA_KEYS
} | {"chunkcontent", "chunktext", "documentcontent", "documentchunk", "querytext"}
_RETRIEVAL_SOURCE_ALLOWED_KEYS = {
    "retrieval_method",
    "rank",
    "score",
    "weight",
    "contribution",
}
PipelineTraceProvider = Callable[[], Mapping[str, object] | None]


class RetrieveCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str
    top_k: int = 10
    metadata_filter: dict[str, object] = Field(default_factory=dict)
    score_threshold: float | None = None


class RetrieveCandidateResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: str
    document_id: str
    version_id: str
    source: str | None = None
    source_uri: str | None = None
    source_type: str
    page_start: int | None = None
    page_end: int | None = None
    title_path: tuple[str, ...]
    score: float
    retrieval_method: str
    tenant_id: str
    acl: dict[str, object] = Field(default_factory=dict)
    metadata: dict[str, object] = Field(default_factory=dict)

    @classmethod
    def from_candidate(cls, candidate: RetrievalCandidate) -> RetrieveCandidateResponse:
        return cls(
            chunk_id=candidate.chunk_id,
            document_id=candidate.document_id,
            version_id=candidate.version_id,
            source=_safe_optional_text(candidate.source),
            source_uri=_safe_optional_text(candidate.source_uri),
            source_type=candidate.source_type,
            page_start=candidate.page_start,
            page_end=candidate.page_end,
            title_path=candidate.title_path,
            score=candidate.score,
            retrieval_method=candidate.retrieval_method,
            tenant_id=candidate.tenant_id,
            acl=_safe_mapping(candidate.acl),
            metadata=_safe_candidate_metadata(candidate.metadata),
        )


class RetrieveResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    top_k: int
    query_summary: dict[str, int]
    latency_ms: float | None
    candidates: tuple[RetrieveCandidateResponse, ...] = ()

    @classmethod
    def from_result(cls, result: RetrievalResult) -> RetrieveResponse:
        return cls(
            request_id=result.request_id,
            trace_id=result.trace_id,
            tenant_id=result.tenant_id,
            user_id=result.user_id,
            top_k=result.top_k,
            query_summary=result.query_summary,
            latency_ms=result.latency_ms,
            candidates=tuple(
                RetrieveCandidateResponse.from_candidate(candidate)
                for candidate in result.candidates
            ),
        )


class RetrieveRequestBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str
    top_k: int = Field(default=10, ge=1, le=100)
    metadata_filter: dict[str, object] = Field(default_factory=dict)
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("query")
    @classmethod
    def _query_required(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be blank")
        return normalized

    @field_validator("metadata_filter", mode="before")
    @classmethod
    def _metadata_filter_object(cls, value: object) -> dict[str, object]:
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
            if normalized_key.startswith("$") or any(char.isspace() for char in normalized_key):
                raise ValueError("metadata_filter keys must be structured field names")
            if not _is_scalar_metadata_value(item):
                raise ValueError("metadata_filter values must be scalar")
            normalized[normalized_key] = item
        return normalized

    def to_command(self) -> RetrieveCommand:
        return RetrieveCommand(
            query=self.query,
            top_k=self.top_k,
            metadata_filter=self.metadata_filter,
            score_threshold=self.score_threshold,
        )


class RetrieveApplicationService:
    def __init__(
        self,
        *,
        retrieval_service: RetrievalService,
        retrieval_log: RetrievalLogPort,
        audit: AuditPort,
        clock: Callable[[], datetime] | None = None,
        perf_counter: Callable[[], float] | None = None,
        pipeline_trace_provider: PipelineTraceProvider | None = None,
    ) -> None:
        self._retrieval_service = retrieval_service
        self._retrieval_log = retrieval_log
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self._perf_counter = perf_counter or default_perf_counter
        self._pipeline_trace_provider = pipeline_trace_provider

    async def retrieve(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: RetrieveCommand,
    ) -> RetrieveResponse:
        started = self._perf_counter()
        request = RetrievalRequest(
            query=command.query,
            top_k=command.top_k,
            metadata_filter=command.metadata_filter,
            score_threshold=command.score_threshold,
            request_id=context.request_id,
            trace_id=context.trace_id,
        )
        try:
            result = await self._retrieval_service.retrieve(
                request=request,
                auth=context.auth,
            )
        except RetrievalError as exc:
            latency_ms = _elapsed_ms(self._perf_counter() - started)
            try:
                await self._record_failure(
                    context=context,
                    request=request,
                    latency_ms=latency_ms,
                    error=exc,
                )
            except Exception:
                with suppress(Exception):
                    await self._retrieval_log.rollback()
            raise

        response_latency_ms = result.latency_ms
        if response_latency_ms is None:
            response_latency_ms = _elapsed_ms(self._perf_counter() - started)
        await self._record_success(
            context=context,
            result=result,
            latency_ms=response_latency_ms,
        )
        return RetrieveResponse.from_result(
            result.model_copy(update={"latency_ms": response_latency_ms})
        )

    async def _record_success(
        self,
        *,
        context: AuthenticatedRequestContext,
        result: RetrievalResult,
        latency_ms: float,
    ) -> None:
        metadata = _log_metadata_from_candidates(
            result.candidates,
            pipeline_trace=self._pipeline_trace(),
        )
        rerank_score = _max_rerank_score(result.candidates)
        record = RetrievalLogCreate(
            request_id=result.request_id,
            trace_id=result.trace_id,
            tenant_id=context.auth.tenant_id,
            user_id=context.auth.user_id,
            created_by=context.auth.user_id,
            status="success",
            latency_ms=latency_ms,
            top_k=result.top_k,
            result_count=len(result.candidates),
            rerank_score=rerank_score,
            error_code=None,
            query_summary=result.query_summary,
            metadata=metadata,
            created_at=self._clock(),
        )
        await self._retrieval_log.create(record)
        await self._audit.record(
            _audit_event(
                context=context,
                status=AuditStatus.SUCCESS,
                latency_ms=latency_ms,
                error_code=None,
                metadata={
                    "top_k": result.top_k,
                    "result_count": len(result.candidates),
                    "rerank_score": rerank_score,
                    **metadata,
                },
            )
        )
        await self._retrieval_log.commit()

    def _pipeline_trace(self) -> Mapping[str, object] | None:
        if self._pipeline_trace_provider is None:
            return None
        trace = self._pipeline_trace_provider()
        return _safe_mapping(trace) if isinstance(trace, Mapping) else None

    async def _record_failure(
        self,
        *,
        context: AuthenticatedRequestContext,
        request: RetrievalRequest,
        latency_ms: float,
        error: RetrievalError,
    ) -> None:
        metadata = redact_mapping(
            {
                "error_code": error.code,
                "error_details": _safe_mapping(error.details),
            }
        )
        record = RetrievalLogCreate(
            request_id=request.request_id,
            trace_id=request.trace_id,
            tenant_id=context.auth.tenant_id,
            user_id=context.auth.user_id,
            created_by=context.auth.user_id,
            status="failure",
            latency_ms=latency_ms,
            top_k=request.top_k,
            result_count=0,
            rerank_score=None,
            error_code=error.code,
            query_summary=_query_summary(request.query),
            metadata=metadata,
            created_at=self._clock(),
        )
        await self._retrieval_log.create(record)
        await self._audit.record(
            _audit_event(
                context=context,
                status=AuditStatus.FAILURE,
                latency_ms=latency_ms,
                error_code=error.code,
                metadata={
                    "top_k": request.top_k,
                    "result_count": 0,
                    **metadata,
                },
            )
        )
        await self._retrieval_log.commit()


def _audit_event(
    *,
    context: AuthenticatedRequestContext,
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
        action="retrieval.retrieve",
        resource=AuditResource(
            type="retrieval_request",
            id=context.request_id,
            metadata={"request_id": context.request_id, "trace_id": context.trace_id},
        ),
        status=status,
        latency_ms=latency_ms,
        error_code=error_code,
        metadata=dict(metadata),
    )


def _log_metadata_from_candidates(
    candidates: Sequence[RetrievalCandidate],
    *,
    pipeline_trace: Mapping[str, object] | None = None,
) -> dict[str, object]:
    candidate_ids = [
        {
            "document_id": candidate.document_id,
            "version_id": candidate.version_id,
            "chunk_id": candidate.chunk_id,
        }
        for candidate in candidates
    ]
    retrieval_provenances = [
        _safe_retrieval_provenance(candidate.metadata.get("retrieval_provenance"))
        for candidate in candidates
        if isinstance(candidate.metadata.get("retrieval_provenance"), Mapping)
    ]
    rerank_provenances = [
        _safe_rerank_provenance(candidate.metadata.get("rerank_provenance"))
        for candidate in candidates
        if isinstance(candidate.metadata.get("rerank_provenance"), Mapping)
    ]
    rrf = _rrf_summary(retrieval_provenances)
    rerank = _rerank_summary(rerank_provenances)
    if pipeline_trace is not None:
        rrf_trace = pipeline_trace.get("rrf")
        rerank_trace = pipeline_trace.get("rerank")
        if isinstance(rrf_trace, Mapping):
            rrf = _safe_mapping(rrf_trace)
        if isinstance(rerank_trace, Mapping):
            rerank = _safe_mapping(rerank_trace)
    dense_top_k = _trace_method_count(pipeline_trace, "dense")
    sparse_top_k = _trace_method_count(pipeline_trace, "sparse")
    metadata: dict[str, object] = {
        "candidate_ids": candidate_ids,
        "dense_top_k": dense_top_k
        if dense_top_k is not None
        else _method_count(retrieval_provenances, "dense"),
        "sparse_top_k": sparse_top_k
        if sparse_top_k is not None
        else _method_count(retrieval_provenances, "sparse"),
        "rrf": rrf,
        "rerank": rerank,
    }
    return redact_mapping(metadata)


def _trace_method_count(trace: Mapping[str, object] | None, method: str) -> int | None:
    if trace is None:
        return None
    rrf = trace.get("rrf")
    if not isinstance(rrf, Mapping):
        return None
    input_counts = rrf.get("input_counts")
    if not isinstance(input_counts, Mapping):
        return None
    value = input_counts.get(method)
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _method_count(provenances: Sequence[Mapping[str, object]], method: str) -> int:
    count = 0
    for provenance in provenances:
        methods = provenance.get("retrieval_methods")
        if isinstance(methods, Sequence) and not isinstance(methods, str) and method in methods:
            count += 1
    return count


def _rrf_summary(provenances: Sequence[Mapping[str, object]]) -> dict[str, object]:
    source_counts: dict[str, int] = {}
    filtered_reasons: dict[str, int] = {}
    for provenance in provenances:
        reason = provenance.get("fusion_reason")
        if isinstance(reason, str):
            filtered_reasons[reason] = filtered_reasons.get(reason, 0) + 1
        sources = provenance.get("sources")
        if isinstance(sources, Sequence) and not isinstance(sources, str):
            for source in sources:
                if not isinstance(source, Mapping):
                    continue
                method = source.get("retrieval_method")
                if isinstance(method, str):
                    source_counts[method] = source_counts.get(method, 0) + 1
    return {
        "input_counts": source_counts,
        "deduped_count": len(provenances),
        "filtered_count": None,
        "fusion_reasons": filtered_reasons,
    }


def _rerank_summary(provenances: Sequence[Mapping[str, object]]) -> dict[str, object]:
    if not provenances:
        return {"status": "not_available", "candidate_count": 0}
    first = provenances[0]
    latency_values = [
        value
        for provenance in provenances
        if isinstance((value := provenance.get("latency_ms")), int | float) and isfinite(value)
    ]
    return {
        "status": str(first.get("status", "unknown")),
        "provider": first.get("provider"),
        "model": first.get("model"),
        "latency_ms": max(latency_values) if latency_values else None,
        "candidate_count": len(provenances),
        "max_score": _max_score_from_provenance(provenances),
    }


def _max_rerank_score(candidates: Sequence[RetrievalCandidate]) -> float | None:
    scores = []
    for candidate in candidates:
        provenance = candidate.metadata.get("rerank_provenance")
        if not isinstance(provenance, Mapping):
            continue
        score = provenance.get("rerank_score")
        if isinstance(score, int | float) and isfinite(score):
            scores.append(float(score))
    return max(scores) if scores else None


def _max_score_from_provenance(provenances: Sequence[Mapping[str, object]]) -> float | None:
    scores = [
        float(score)
        for provenance in provenances
        if isinstance((score := provenance.get("rerank_score")), int | float) and isfinite(score)
    ]
    return max(scores) if scores else None


def _safe_candidate_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        key_text = str(key)
        if key_text == "retrieval_provenance":
            provenance = _safe_retrieval_provenance(value)
            if provenance:
                safe[key_text] = provenance
            continue
        if key_text == "rerank_provenance":
            provenance = _safe_rerank_provenance(value)
            if provenance:
                safe[key_text] = provenance
            continue
        if _is_sensitive_key(key_text):
            continue
        safe_value = _safe_value(value)
        if safe_value != REDACTED_VALUE:
            safe[key_text] = safe_value
    return redact_mapping(safe)


def _safe_retrieval_provenance(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    safe: dict[str, object] = {}
    for key, item in value.items():
        key_text = str(key)
        if key_text not in {
            "retrieval_methods",
            "sources",
            "raw_rrf_score",
            "normalized_fusion_score",
            "fusion_reason",
        }:
            continue
        if _is_sensitive_key(key_text):
            continue
        if key_text == "sources":
            safe[key_text] = _safe_retrieval_sources(item)
        else:
            safe[key_text] = _safe_value(item)
    return safe


def _safe_retrieval_sources(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, str):
        return []
    safe_sources: list[dict[str, object]] = []
    for source in value:
        if not isinstance(source, Mapping):
            continue
        safe_source = {
            str(key): _safe_value(item)
            for key, item in source.items()
            if str(key) in _RETRIEVAL_SOURCE_ALLOWED_KEYS and not _is_sensitive_key(str(key))
        }
        if safe_source:
            safe_sources.append(safe_source)
    return safe_sources


def _is_scalar_metadata_value(value: object) -> bool:
    if value is None or isinstance(value, str | int | bool):
        return True
    return isinstance(value, float) and isfinite(value)


def _safe_rerank_provenance(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    allowed = {
        "provider",
        "model",
        "status",
        "input_rank",
        "output_rank",
        "pre_score",
        "rerank_score",
        "score_source",
        "latency_ms",
        "error_code",
    }
    return {
        str(key): _safe_value(item)
        for key, item in value.items()
        if str(key) in allowed and not _is_sensitive_key(str(key))
    }


def _safe_mapping(value: Mapping[str, object]) -> dict[str, object]:
    return {
        str(key): item
        for key, item in redact_mapping(
            {
                str(key): _safe_value(item)
                for key, item in value.items()
                if not _is_sensitive_key(str(key))
            }
        ).items()
        if item != REDACTED_VALUE
    }


def _safe_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _safe_mapping(value)
    if isinstance(value, list | tuple):
        return [_safe_value(item) for item in value]
    if isinstance(value, str):
        if _looks_like_local_absolute_path(value):
            return REDACTED_VALUE
        redacted = redact_sensitive_data(value)
        if isinstance(redacted, str):
            return redacted
        return REDACTED_VALUE
    return redact_sensitive_data(value)


def _safe_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    safe = _safe_value(value)
    return safe if isinstance(safe, str) else REDACTED_VALUE


def _is_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_").replace(" ", "_")
    compact = "".join(char for char in normalized if char.isalnum())
    return normalized in _SENSITIVE_METADATA_KEYS or compact in _SENSITIVE_COMPACT_KEYS


def _looks_like_local_absolute_path(value: str) -> bool:
    normalized = value.strip()
    return (
        normalized.startswith("/")
        or normalized.startswith("\\\\")
        or _WINDOWS_ABSOLUTE_PATH.match(normalized) is not None
    )


def _query_summary(query: str) -> dict[str, int]:
    return {
        "length": len(query),
        "term_count": len([part for part in query.split() if part.strip()]),
    }


def _elapsed_ms(elapsed_seconds: float) -> float:
    return round(max(elapsed_seconds, 0.0) * 1000, 3)

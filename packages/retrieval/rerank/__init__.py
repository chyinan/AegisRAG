from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from math import isfinite
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from packages.common.logging import REDACTED_VALUE, get_request_logger, redact_sensitive_data
from packages.retrieval.dto import (
    MAX_RETRIEVAL_TOP_K,
    RetrievalCandidate,
    RetrievalFilterSet,
    RetrievalRequest,
)
from packages.retrieval.exceptions import (
    RETRIEVAL_RERANK_DEGRADED,
    RETRIEVAL_RERANK_FAILED,
    RETRIEVAL_RERANK_INVALID_CANDIDATE,
    RETRIEVAL_RERANK_INVALID_SCORE,
    RetrievalError,
)
from packages.retrieval.filters import to_vector_acl_filter
from packages.retrieval.ports import CandidateRetriever, Reranker
from packages.vectorstores.acl import acl_allows

RERANK_PROVENANCE_METADATA_KEY = "rerank_provenance"
RETRIEVAL_PROVENANCE_METADATA_KEY = "retrieval_provenance"
_WINDOWS_DRIVE_CHARS = ":\\/"
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
_SENSITIVE_METADATA_COMPACT_KEYS = {
    "".join(char for char in key if char.isalnum()) for key in _SENSITIVE_METADATA_KEYS
} | {
    "chunkcontent",
    "chunktext",
    "documentcontent",
    "documentchunk",
    "fullquery",
    "querytext",
    "rawresponse",
    "providerrawresponse",
    "sqlquery",
}


class RerankConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    enabled: bool = True
    failure_policy: Literal["fallback", "fail_closed"] = "fallback"
    timeout_seconds: float = 2.0
    provider: str = "fake"
    model: str = "fake-reranker-v1"
    max_candidates: int = MAX_RETRIEVAL_TOP_K

    @field_validator("timeout_seconds")
    @classmethod
    def _timeout_positive(cls, value: float) -> float:
        if not isfinite(value) or value <= 0.0:
            raise ValueError("timeout_seconds must be a finite positive number")
        return value

    @field_validator("provider", "model")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("provider and model must not be blank")
        return normalized

    @field_validator("max_candidates")
    @classmethod
    def _max_candidates_in_range(cls, value: int) -> int:
        if value <= 0 or value > MAX_RETRIEVAL_TOP_K:
            raise ValueError(f"max_candidates must be between 1 and {MAX_RETRIEVAL_TOP_K}")
        return value


class RerankRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    request: RetrievalRequest
    filters: RetrievalFilterSet
    candidates: tuple[RetrievalCandidate, ...] = ()


class RerankCandidateTrace(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    version_id: str
    chunk_id: str
    input_rank: int
    output_rank: int
    pre_score: float
    rerank_score: float


class RerankTrace(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    provider: str
    model: str
    latency_ms: float
    input_count: int
    output_count: int
    safe_counts: Mapping[str, int] = Field(default_factory=dict)
    candidate_traces: tuple[RerankCandidateTrace, ...] = ()
    degraded: bool = False
    error_code: str | None = None


class RerankResult(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    candidates: tuple[RetrievalCandidate, ...] = ()
    trace: RerankTrace


class FakeReranker:
    def __init__(
        self,
        *,
        score_by_chunk_id: Mapping[str, float] | None = None,
        score_by_identity: Mapping[tuple[str, str, str], float] | None = None,
        provider: str = "fake",
        model: str = "fake-reranker-v1",
        failure_mode: Literal[
            "none",
            "raise_domain",
            "raise_unexpected",
            "invalid_score",
            "timeout",
        ] = "none",
    ) -> None:
        self._score_by_chunk_id = dict(score_by_chunk_id or {})
        self._score_by_identity = dict(score_by_identity or {})
        self._provider = provider
        self._model = model
        self._failure_mode = failure_mode

    async def rerank(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
        candidates: Sequence[RetrievalCandidate],
    ) -> RerankResult:
        started = perf_counter()
        if self._failure_mode == "raise_domain":
            raise RetrievalError(
                code=RETRIEVAL_RERANK_FAILED,
                message="Fake reranker failed.",
                details=_safe_error_details(
                    request=request,
                    filters=filters,
                    provider=self._provider,
                    model=self._model,
                    error_code=RETRIEVAL_RERANK_FAILED,
                    input_count=len(candidates),
                    output_count=0,
                ),
                status_code=502,
            )
        if self._failure_mode == "raise_unexpected":
            raise RuntimeError("provider raw response password at C:\\secret\\reranker.sql")
        if self._failure_mode == "timeout":
            raise TimeoutError("reranker timeout at C:\\secret\\provider.log")

        scored: list[tuple[RetrievalCandidate, float, int]] = []
        for input_rank, candidate in enumerate(candidates, start=1):
            rerank_score = self._score_for_candidate(candidate=candidate)
            if self._failure_mode == "invalid_score":
                rerank_score = 1.5
            scored.append((candidate, rerank_score, input_rank))

        scored.sort(key=lambda item: (-item[1], _identity(item[0])))
        output_candidates = []
        traces = []
        for output_rank, (candidate, rerank_score, input_rank) in enumerate(scored, start=1):
            output_candidates.append(
                _candidate_with_rerank(
                    original=candidate,
                    score=rerank_score,
                    provider=self._provider,
                    model=self._model,
                    status="success",
                    input_rank=input_rank,
                    output_rank=output_rank,
                    pre_score=candidate.score,
                    latency_ms=(perf_counter() - started) * 1000,
                    error_code=None,
                )
            )
            traces.append(
                RerankCandidateTrace(
                    document_id=candidate.document_id,
                    version_id=candidate.version_id,
                    chunk_id=candidate.chunk_id,
                    input_rank=input_rank,
                    output_rank=output_rank,
                    pre_score=candidate.score,
                    rerank_score=rerank_score,
                )
            )

        latency_ms = (perf_counter() - started) * 1000
        return RerankResult(
            candidates=tuple(output_candidates),
            trace=RerankTrace(
                request_id=request.request_id,
                trace_id=request.trace_id,
                tenant_id=filters.tenant_id,
                user_id=filters.user_id,
                provider=self._provider,
                model=self._model,
                latency_ms=latency_ms,
                input_count=len(candidates),
                output_count=len(output_candidates),
                safe_counts={
                    "input_candidates": len(candidates),
                    "output_candidates": len(output_candidates),
                },
                candidate_traces=tuple(traces),
            ),
        )

    def _score_for_candidate(self, *, candidate: RetrievalCandidate) -> float:
        identity_score = self._score_by_identity.get(
            (candidate.document_id, candidate.version_id, candidate.chunk_id)
        )
        if identity_score is not None:
            return float(identity_score)
        chunk_score = self._score_by_chunk_id.get(candidate.chunk_id)
        if chunk_score is not None:
            return float(chunk_score)
        if 0.0 <= candidate.score <= 1.0:
            return candidate.score
        return 0.0


class RerankingRetriever:
    def __init__(
        self,
        *,
        upstream_retriever: CandidateRetriever,
        reranker: Reranker,
        config: RerankConfig,
    ) -> None:
        self._upstream_retriever = upstream_retriever
        self._reranker = reranker
        self._config = config
        self.last_trace: RerankTrace | None = None

    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> list[RetrievalCandidate]:
        candidates = await self._upstream_retriever.retrieve(request=request, filters=filters)
        if not candidates:
            self.last_trace = _empty_trace(request=request, filters=filters, config=self._config)
            return []

        candidates_for_rerank = candidates[: self._config.max_candidates]
        try:
            provider_candidates = _guard_and_sanitize_upstream_candidates(
                request=request,
                filters=filters,
                config=self._config,
                candidates=candidates_for_rerank,
            )
        except RetrievalError as exc:
            self.last_trace = _failed_trace(
                request=request,
                filters=filters,
                config=self._config,
                input_count=len(candidates_for_rerank),
                latency_ms=0.0,
                error_code=exc.code,
            )
            raise

        if not self._config.enabled:
            output = _disabled_candidates(
                request=request,
                config=self._config,
                candidates=candidates_for_rerank,
            )
            self.last_trace = _disabled_trace(
                request=request,
                filters=filters,
                config=self._config,
                input_count=len(candidates_for_rerank),
                output_count=len(output),
            )
            return output

        started = perf_counter()
        try:
            result = await asyncio.wait_for(
                self._reranker.rerank(
                    request=request,
                    filters=filters,
                    candidates=provider_candidates,
                ),
                timeout=self._config.timeout_seconds,
            )
            output = _validated_candidates(
                request=request,
                filters=filters,
                config=self._config,
                original_candidates=candidates_for_rerank,
                reranked_candidates=tuple(result.candidates),
                latency_ms=(perf_counter() - started) * 1000,
            )
        except Exception as exc:
            import traceback as _tb
            _err_log = get_request_logger()
            _err_log.info("reranking_retriever_fallback", extra={
                "exc_type": type(exc).__name__,
                "exc_message": str(exc)[:500],
                "traceback": _tb.format_exc()[-2000:],
            })
            if self._config.failure_policy == "fallback":
                output = _fallback_candidates(
                    request=request,
                    filters=filters,
                    config=self._config,
                    candidates=candidates_for_rerank,
                    latency_ms=(perf_counter() - started) * 1000,
                )
                self.last_trace = _degraded_trace(
                    request=request,
                    filters=filters,
                    config=self._config,
                    input_count=len(candidates_for_rerank),
                    output_count=len(output),
                    latency_ms=(perf_counter() - started) * 1000,
                )
                return output[: request.top_k]
            error = _rerank_error(
                request=request,
                filters=filters,
                config=self._config,
                exc=exc,
                input_count=len(candidates_for_rerank),
            )
            self.last_trace = _failed_trace(
                request=request,
                filters=filters,
                config=self._config,
                input_count=len(candidates_for_rerank),
                latency_ms=(perf_counter() - started) * 1000,
                error_code=error.code,
            )
            raise error from exc

        self.last_trace = _success_trace(
            request=request,
            filters=filters,
            config=self._config,
            candidates=output,
            original_candidates=candidates_for_rerank,
            latency_ms=(perf_counter() - started) * 1000,
        )
        return output[: request.top_k]


def _validated_candidates(
    *,
    request: RetrievalRequest,
    filters: RetrievalFilterSet,
    config: RerankConfig,
    original_candidates: Sequence[RetrievalCandidate],
    reranked_candidates: Sequence[RetrievalCandidate],
    latency_ms: float,
) -> list[RetrievalCandidate]:
    originals_by_key = {_identity(candidate): candidate for candidate in original_candidates}
    if len(originals_by_key) != len(original_candidates):
        raise _invalid_candidate_error(
            request=request,
            filters=filters,
            config=config,
            input_count=len(original_candidates),
            output_count=0,
        )
    input_ranks = {
        _identity(candidate): rank
        for rank, candidate in enumerate(original_candidates, start=1)
    }
    if len(reranked_candidates) != len(original_candidates):
        raise _invalid_candidate_error(
            request=request,
            filters=filters,
            config=config,
            input_count=len(original_candidates),
            output_count=len(reranked_candidates),
        )
    output: list[RetrievalCandidate] = []
    seen: set[tuple[str, str, str, str]] = set()
    for output_index, candidate in enumerate(reranked_candidates, start=1):
        original = originals_by_key.get(_identity(candidate))
        if original is None:
            raise _invalid_candidate_error(
                request=request,
                filters=filters,
                config=config,
                input_count=len(original_candidates),
                output_count=len(output),
            )
        key = _identity(original)
        if key in seen:
            raise _invalid_candidate_error(
                request=request,
                filters=filters,
                config=config,
                input_count=len(original_candidates),
                output_count=len(output),
            )
        seen.add(key)
        rerank_score = candidate.score
        if not _score_in_range(rerank_score):
            raise _invalid_score_error(
                request=request,
                filters=filters,
                config=config,
                input_count=len(original_candidates),
                output_count=len(output),
            )
        output.append(
            _candidate_with_rerank(
                original=original,
                score=rerank_score,
                provider=config.provider,
                model=config.model,
                status="success",
                input_rank=input_ranks[key],
                output_rank=output_index,
                pre_score=original.score,
                latency_ms=latency_ms,
                error_code=None,
            )
        )
    return output


def _guard_and_sanitize_upstream_candidates(
    *,
    request: RetrievalRequest,
    filters: RetrievalFilterSet,
    config: RerankConfig,
    candidates: Sequence[RetrievalCandidate],
) -> list[RetrievalCandidate]:
    acl_filter = to_vector_acl_filter(filters)
    safe: list[RetrievalCandidate] = []
    seen: set[tuple[str, str, str, str]] = set()
    for candidate in candidates:
        key = _identity(candidate)
        if key in seen:
            raise _invalid_candidate_error(
                request=request,
                filters=filters,
                config=config,
                input_count=len(candidates),
                output_count=len(safe),
            )
        seen.add(key)
        if candidate.tenant_id != filters.tenant_id:
            raise _invalid_candidate_error(
                request=request,
                filters=filters,
                config=config,
                input_count=len(candidates),
                output_count=len(safe),
            )
        if not _metadata_matches(candidate.metadata, filters.metadata_filter):
            raise _invalid_candidate_error(
                request=request,
                filters=filters,
                config=config,
                input_count=len(candidates),
                output_count=len(safe),
            )
        if not acl_allows(candidate.acl, acl_filter):
            raise _invalid_candidate_error(
                request=request,
                filters=filters,
                config=config,
                input_count=len(candidates),
                output_count=len(safe),
            )
        if not _score_in_range(candidate.score):
            raise _invalid_score_error(
                request=request,
                filters=filters,
                config=config,
                input_count=len(candidates),
                output_count=len(safe),
            )
        safe.append(candidate.model_copy(update={"metadata": _safe_metadata(candidate.metadata)}))
    return safe


def _metadata_matches(
    candidate_metadata: Mapping[str, object],
    required_metadata: Mapping[str, object],
) -> bool:
    return all(candidate_metadata.get(key) == value for key, value in required_metadata.items())


def _fallback_candidates(
    *,
    request: RetrievalRequest,
    filters: RetrievalFilterSet,
    config: RerankConfig,
    candidates: Sequence[RetrievalCandidate],
    latency_ms: float,
) -> list[RetrievalCandidate]:
    output = []
    for rank, candidate in enumerate(candidates, start=1):
        output.append(
            _candidate_with_rerank(
                original=candidate,
                score=candidate.score,
                provider=config.provider,
                model=config.model,
                status="degraded",
                input_rank=rank,
                output_rank=rank,
                pre_score=candidate.score,
                latency_ms=latency_ms,
                error_code=RETRIEVAL_RERANK_DEGRADED,
            )
        )
    return output[: request.top_k]


def _disabled_candidates(
    *,
    request: RetrievalRequest,
    config: RerankConfig,
    candidates: Sequence[RetrievalCandidate],
) -> list[RetrievalCandidate]:
    output = []
    for rank, candidate in enumerate(candidates, start=1):
        output.append(
            _candidate_with_rerank(
                original=candidate,
                score=candidate.score,
                provider=config.provider,
                model=config.model,
                status="disabled",
                input_rank=rank,
                output_rank=rank,
                pre_score=candidate.score,
                latency_ms=0.0,
                error_code=None,
            )
        )
    return output[: request.top_k]


def _candidate_with_rerank(
    *,
    original: RetrievalCandidate,
    score: float,
    provider: str,
    model: str,
    status: Literal["success", "degraded", "disabled"],
    input_rank: int,
    output_rank: int,
    pre_score: float,
    latency_ms: float,
    error_code: str | None,
) -> RetrievalCandidate:
    metadata = _safe_metadata(original.metadata)
    provenance: dict[str, object] = {
        "provider": provider,
        "model": model,
        "status": status,
        "input_rank": input_rank,
        "output_rank": output_rank,
        "pre_score": pre_score,
        "rerank_score": score,
        "score_source": _score_source(status),
        "latency_ms": latency_ms,
    }
    if error_code is not None:
        provenance["error_code"] = error_code
    metadata[RERANK_PROVENANCE_METADATA_KEY] = provenance
    return original.model_copy(update={"score": score, "metadata": metadata})


def _score_source(status: Literal["success", "degraded", "disabled"]) -> str:
    if status == "degraded":
        return "fallback_upstream"
    if status == "disabled":
        return "disabled_upstream"
    return "rerank"


def _identity(candidate: RetrievalCandidate) -> tuple[str, str, str, str]:
    return (
        candidate.tenant_id,
        candidate.document_id,
        candidate.version_id,
        candidate.chunk_id,
    )


def _score_in_range(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int | float)
        and isfinite(value)
        and 0.0 <= value <= 1.0
    )


def _empty_trace(
    *,
    request: RetrievalRequest,
    filters: RetrievalFilterSet,
    config: RerankConfig,
) -> RerankTrace:
    return RerankTrace(
        request_id=request.request_id,
        trace_id=request.trace_id,
        tenant_id=filters.tenant_id,
        user_id=filters.user_id,
        provider=config.provider,
        model=config.model,
        latency_ms=0.0,
        input_count=0,
        output_count=0,
        safe_counts={"input_candidates": 0, "output_candidates": 0},
    )


def _disabled_trace(
    *,
    request: RetrievalRequest,
    filters: RetrievalFilterSet,
    config: RerankConfig,
    input_count: int,
    output_count: int,
) -> RerankTrace:
    return RerankTrace(
        request_id=request.request_id,
        trace_id=request.trace_id,
        tenant_id=filters.tenant_id,
        user_id=filters.user_id,
        provider=config.provider,
        model=config.model,
        latency_ms=0.0,
        input_count=input_count,
        output_count=output_count,
        safe_counts={"input_candidates": input_count, "output_candidates": output_count},
    )


def _success_trace(
    *,
    request: RetrievalRequest,
    filters: RetrievalFilterSet,
    config: RerankConfig,
    candidates: Sequence[RetrievalCandidate],
    original_candidates: Sequence[RetrievalCandidate],
    latency_ms: float,
) -> RerankTrace:
    original_by_key = {_identity(candidate): candidate for candidate in original_candidates}
    traces = []
    for candidate in candidates:
        provenance = candidate.metadata.get(RERANK_PROVENANCE_METADATA_KEY)
        if not isinstance(provenance, Mapping):
            continue
        original = original_by_key.get(_identity(candidate), candidate)
        traces.append(
            RerankCandidateTrace(
                document_id=candidate.document_id,
                version_id=candidate.version_id,
                chunk_id=candidate.chunk_id,
                input_rank=int(provenance.get("input_rank", 0)),
                output_rank=int(provenance.get("output_rank", 0)),
                pre_score=float(provenance.get("pre_score", original.score)),
                rerank_score=float(provenance.get("rerank_score", candidate.score)),
            )
        )
    return RerankTrace(
        request_id=request.request_id,
        trace_id=request.trace_id,
        tenant_id=filters.tenant_id,
        user_id=filters.user_id,
        provider=config.provider,
        model=config.model,
        latency_ms=latency_ms,
        input_count=len(original_candidates),
        output_count=len(candidates),
        safe_counts={
            "input_candidates": len(original_candidates),
            "output_candidates": len(candidates),
        },
        candidate_traces=tuple(traces),
    )


def _degraded_trace(
    *,
    request: RetrievalRequest,
    filters: RetrievalFilterSet,
    config: RerankConfig,
    input_count: int,
    output_count: int,
    latency_ms: float,
) -> RerankTrace:
    return RerankTrace(
        request_id=request.request_id,
        trace_id=request.trace_id,
        tenant_id=filters.tenant_id,
        user_id=filters.user_id,
        provider=config.provider,
        model=config.model,
        latency_ms=latency_ms,
        input_count=input_count,
        output_count=output_count,
        safe_counts={"input_candidates": input_count, "output_candidates": output_count},
        degraded=True,
        error_code=RETRIEVAL_RERANK_DEGRADED,
    )


def _failed_trace(
    *,
    request: RetrievalRequest,
    filters: RetrievalFilterSet,
    config: RerankConfig,
    input_count: int,
    latency_ms: float,
    error_code: str,
) -> RerankTrace:
    return RerankTrace(
        request_id=request.request_id,
        trace_id=request.trace_id,
        tenant_id=filters.tenant_id,
        user_id=filters.user_id,
        provider=config.provider,
        model=config.model,
        latency_ms=latency_ms,
        input_count=input_count,
        output_count=0,
        safe_counts={"input_candidates": input_count, "output_candidates": 0},
        degraded=False,
        error_code=error_code,
    )


def _invalid_score_error(
    *,
    request: RetrievalRequest,
    filters: RetrievalFilterSet,
    config: RerankConfig,
    input_count: int,
    output_count: int,
) -> RetrievalError:
    return RetrievalError(
        code=RETRIEVAL_RERANK_INVALID_SCORE,
        message="Reranker returned an invalid score.",
        details=_safe_error_details(
            request=request,
            filters=filters,
            provider=config.provider,
            model=config.model,
            error_code=RETRIEVAL_RERANK_INVALID_SCORE,
            input_count=input_count,
            output_count=output_count,
        ),
        status_code=502,
    )


def _invalid_candidate_error(
    *,
    request: RetrievalRequest,
    filters: RetrievalFilterSet,
    config: RerankConfig,
    input_count: int,
    output_count: int,
) -> RetrievalError:
    return RetrievalError(
        code=RETRIEVAL_RERANK_INVALID_CANDIDATE,
        message="Reranker returned an invalid candidate set.",
        details=_safe_error_details(
            request=request,
            filters=filters,
            provider=config.provider,
            model=config.model,
            error_code=RETRIEVAL_RERANK_INVALID_CANDIDATE,
            input_count=input_count,
            output_count=output_count,
        ),
        status_code=502,
    )


def _rerank_error(
    *,
    request: RetrievalRequest,
    filters: RetrievalFilterSet,
    config: RerankConfig,
    exc: Exception,
    input_count: int,
) -> RetrievalError:
    if isinstance(exc, RetrievalError) and exc.code in {
        RETRIEVAL_RERANK_INVALID_CANDIDATE,
        RETRIEVAL_RERANK_INVALID_SCORE,
    }:
        return exc
    return RetrievalError(
        code=RETRIEVAL_RERANK_FAILED,
        message="Reranker failed.",
        details=_safe_error_details(
            request=request,
            filters=filters,
            provider=config.provider,
            model=config.model,
            error_code=RETRIEVAL_RERANK_FAILED,
            input_count=input_count,
            output_count=0,
        ),
        status_code=502,
    )


def _safe_error_details(
    *,
    request: RetrievalRequest,
    filters: RetrievalFilterSet,
    provider: str,
    model: str,
    error_code: str,
    input_count: int,
    output_count: int,
) -> dict[str, object]:
    return {
        "request_id": request.request_id,
        "trace_id": request.trace_id,
        "tenant_id": filters.tenant_id,
        "user_id": filters.user_id,
        "top_k": request.top_k,
        "retrieval_method": "hybrid",
        "rerank_stage": "rerank",
        "provider": provider,
        "model": model,
        "safe_counts": {
            "input_candidates": input_count,
            "output_candidates": output_count,
        },
        "error_code": error_code,
    }


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        key_text = str(key)
        if key_text == RETRIEVAL_PROVENANCE_METADATA_KEY and isinstance(value, Mapping):
            safe[key_text] = _safe_retrieval_provenance(value)
            continue
        if _is_sensitive_metadata_key(key_text):
            continue
        redacted = redact_sensitive_data({key_text: value})
        if not isinstance(redacted, Mapping):
            continue
        redacted_value = redacted.get(key_text, REDACTED_VALUE)
        if redacted_value == REDACTED_VALUE:
            continue
        safe[key_text] = _safe_value(redacted_value)
    return safe


def _safe_retrieval_provenance(provenance: Mapping[str, object]) -> dict[str, object]:
    allowed_keys = {
        "retrieval_methods",
        "sources",
        "raw_rrf_score",
        "normalized_fusion_score",
        "fusion_reason",
    }
    return {
        key: _safe_value(value)
        for key, value in provenance.items()
        if key in allowed_keys and not _is_sensitive_metadata_key(key)
    }


def _is_sensitive_metadata_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_").replace(" ", "_")
    compact = "".join(char for char in normalized if char.isalnum())
    return (
        normalized == RERANK_PROVENANCE_METADATA_KEY
        or normalized in _SENSITIVE_METADATA_KEYS
        or compact in _SENSITIVE_METADATA_COMPACT_KEYS
    )


def _safe_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _safe_value(item)
            for key, item in value.items()
            if not _is_sensitive_metadata_key(str(key))
        }
    if isinstance(value, list | tuple):
        return tuple(_safe_value(item) for item in value)
    if isinstance(value, str) and _looks_like_local_absolute_path(value):
        return REDACTED_VALUE
    return redact_sensitive_data(value)


def _looks_like_local_absolute_path(value: str) -> bool:
    normalized = value.strip()
    if normalized.startswith("/") or normalized.startswith("\\\\"):
        return True
    return len(normalized) >= 3 and normalized[1] == ":" and normalized[2] in _WINDOWS_DRIVE_CHARS

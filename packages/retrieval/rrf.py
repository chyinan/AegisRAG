from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping, Sequence
from contextvars import ContextVar
from math import isfinite

from pydantic import BaseModel, ConfigDict, Field, field_validator

from packages.common.logging import REDACTED_VALUE, redact_sensitive_data
from packages.retrieval.dto import (
    MAX_RETRIEVAL_TOP_K,
    RetrievalCandidate,
    RetrievalFilterSet,
    RetrievalRequest,
)

# pattern: Functional Core
from packages.retrieval.exceptions import (
    RETRIEVAL_HYBRID_BRANCH_FAILED,
    RETRIEVAL_HYBRID_MERGE_FAILED,
    RetrievalError,
)
from packages.retrieval.filters import to_vector_acl_filter
from packages.retrieval.ports import CandidateRetriever
from packages.vectorstores.acl import acl_allows

PROVENANCE_METADATA_KEY = "retrieval_provenance"
_DENSE = "dense"
_SPARSE = "sparse"
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


class HybridMergeConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    rank_constant: float = 60.0
    dense_weight: float = 1.0
    sparse_weight: float = 1.0
    min_fusion_score: float | None = None
    max_candidates_per_branch: int | None = None

    @field_validator("rank_constant")
    @classmethod
    def _rank_constant_positive(cls, value: float) -> float:
        if not isfinite(value) or value <= 0.0:
            raise ValueError("rank_constant must be a finite positive number")
        return value

    @field_validator("dense_weight")
    @classmethod
    def _dense_weight_positive(cls, value: float) -> float:
        if not isfinite(value) or value <= 0.0:
            raise ValueError("dense_weight must be a finite positive number")
        return value

    @field_validator("sparse_weight")
    @classmethod
    def _sparse_weight_positive(cls, value: float) -> float:
        if not isfinite(value) or value <= 0.0:
            raise ValueError("sparse_weight must be a finite positive number")
        return value

    @field_validator("min_fusion_score")
    @classmethod
    def _min_fusion_score_in_range(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if not isfinite(value) or value < 0.0 or value > 1.0:
            raise ValueError("min_fusion_score must be between 0 and 1")
        return value

    @field_validator("max_candidates_per_branch")
    @classmethod
    def _max_candidates_per_branch_in_range(cls, value: int | None) -> int | None:
        if value is None:
            return None
        if value <= 0 or value > MAX_RETRIEVAL_TOP_K:
            raise ValueError(
                f"max_candidates_per_branch must be between 1 and {MAX_RETRIEVAL_TOP_K}"
            )
        return value


class FusionSource(BaseModel):
    model_config = ConfigDict(frozen=True)

    retrieval_method: str
    rank: int
    score: float
    weight: float
    contribution: float


class FusionTrace(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_counts: dict[str, int]
    deduped_count: int
    filtered_count: int
    threshold: float | None
    rank_constant: float
    weights: dict[str, float]


class _FusionBucket(BaseModel):
    model_config = ConfigDict(frozen=True)

    candidate: RetrievalCandidate
    sources: tuple[FusionSource, ...] = Field(default_factory=tuple)


class RRFMerger:
    def __init__(self, *, config: HybridMergeConfig) -> None:
        self._config = config
        self._last_trace: ContextVar[FusionTrace | None] = ContextVar(
            "rrf_trace", default=None
        )

    @property
    def last_trace(self) -> FusionTrace | None:
        return self._last_trace.get()

    @last_trace.setter
    def last_trace(self, value: FusionTrace | None) -> None:
        self._last_trace.set(value)

    def merge(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
        dense_candidates: Sequence[RetrievalCandidate],
        sparse_candidates: Sequence[RetrievalCandidate],
    ) -> list[RetrievalCandidate]:
        buckets: dict[tuple[str, str, str, str], _FusionBucket] = {}
        filtered_count = 0
        for method, candidates in (
            (_DENSE, dense_candidates[: self._branch_limit]),
            (_SPARSE, sparse_candidates[: self._branch_limit]),
        ):
            weight = self._weight_for_method(method)
            for rank, candidate in enumerate(candidates, start=1):
                if not _candidate_in_scope(candidate=candidate, filters=filters):
                    filtered_count += 1
                    continue
                key = (
                    candidate.tenant_id,
                    candidate.document_id,
                    candidate.version_id,
                    candidate.chunk_id,
                )
                contribution = weight / (self._config.rank_constant + rank)
                source = FusionSource(
                    retrieval_method=method,
                    rank=rank,
                    score=candidate.score,
                    weight=weight,
                    contribution=contribution,
                )
                existing = buckets.get(key)
                if existing is None:
                    buckets[key] = _FusionBucket(candidate=candidate, sources=(source,))
                elif any(item.retrieval_method == method for item in existing.sources):
                    continue
                else:
                    merged_candidate = existing.candidate
                    if not merged_candidate.content and candidate.content:
                        merged_candidate = merged_candidate.model_copy(
                            update={"content": candidate.content}
                        )
                    buckets[key] = existing.model_copy(
                        update={
                            "candidate": merged_candidate,
                            "sources": (*existing.sources, source),
                        }
                    )

        threshold = _effective_threshold(request=request, config=self._config)
        fused_candidates = []
        for bucket in buckets.values():
            fused = _fused_candidate(bucket=bucket, config=self._config)
            if threshold is not None and fused.score < threshold:
                filtered_count += 1
                continue
            fused_candidates.append(fused)

        fused_candidates.sort(key=_sort_key)
        limited = fused_candidates[: request.top_k]
        self.last_trace = FusionTrace(
            input_counts={_DENSE: len(dense_candidates), _SPARSE: len(sparse_candidates)},
            deduped_count=len(buckets),
            filtered_count=filtered_count,
            threshold=threshold,
            rank_constant=self._config.rank_constant,
            weights={_DENSE: self._config.dense_weight, _SPARSE: self._config.sparse_weight},
        )
        return limited

    @property
    def _branch_limit(self) -> int | None:
        return self._config.max_candidates_per_branch

    def _weight_for_method(self, method: str) -> float:
        if method == _DENSE:
            return self._config.dense_weight
        return self._config.sparse_weight


class HybridRetriever:
    def __init__(
        self,
        *,
        dense_retriever: CandidateRetriever,
        sparse_retriever: CandidateRetriever,
        merger: RRFMerger,
        config: HybridMergeConfig,
        allow_degraded: bool = False,
        parallel_branches: bool = True,
    ) -> None:
        self._dense_retriever = dense_retriever
        self._sparse_retriever = sparse_retriever
        self._merger = merger
        self._config = config
        self._allow_degraded = allow_degraded
        self._parallel_branches = parallel_branches
        self._branch_status: ContextVar[dict[str, str] | None] = ContextVar(
            "hybrid_branch_status", default=None
        )
        self._branch_trace: ContextVar[dict[str, dict[str, object]] | None] = ContextVar(
            "hybrid_branch_trace", default=None
        )

    @property
    def last_branch_status(self) -> dict[str, str]:
        return dict(self._branch_status.get() or {})

    @property
    def last_branch_trace(self) -> dict[str, dict[str, object]]:
        return {
            branch: dict(details)
            for branch, details in (self._branch_trace.get() or {}).items()
        }

    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> list[RetrievalCandidate]:
        self._branch_status.set({})
        self._branch_trace.set({})
        self._merger.last_trace = None
        branch_request = request.model_copy(
            update={
                "score_threshold": None,
                "top_k": self._config.max_candidates_per_branch or request.top_k,
            }
        )
        if self._parallel_branches and not self._retrievers_share_session():
            branch_results: list[list[RetrievalCandidate] | BaseException] = list(
                await asyncio.gather(
                    self._retrieve_branch(
                        branch=_DENSE,
                        retriever=self._dense_retriever,
                        request=branch_request,
                        filters=filters,
                    ),
                    self._retrieve_branch(
                        branch=_SPARSE,
                        retriever=self._sparse_retriever,
                        request=branch_request,
                        filters=filters,
                    ),
                    return_exceptions=True,
                )
            )
        else:
            branch_results = await self._retrieve_sequentially(
                request=branch_request,
                filters=filters,
            )
        dense_result, sparse_result = branch_results
        branch_errors = {
            branch: result
            for branch, result in ((_DENSE, dense_result), (_SPARSE, sparse_result))
            if isinstance(result, BaseException)
        }
        if any(isinstance(error, asyncio.CancelledError) for error in branch_errors.values()):
            raise asyncio.CancelledError
        self._branch_status.set({
            _DENSE: "degraded" if _DENSE in branch_errors else "ok",
            _SPARSE: "degraded" if _SPARSE in branch_errors else "ok",
        })
        branch_trace: dict[str, dict[str, object]] = {}
        for branch, error in branch_errors.items():
            degradable = _is_degradable_error(error)
            details: dict[str, object] = {
                "status": "degraded" if degradable else "failed",
                "error_code": (
                    error.details.get("upstream_error_code")
                    if isinstance(error, RetrievalError)
                    else type(error).__name__
                ),
            }
            if isinstance(error, RetrievalError) and details["error_code"] is None:
                details["error_code"] = error.details.get("upstream_error_type")
            branch_trace[branch] = details
        for branch in (_DENSE, _SPARSE):
            branch_trace.setdefault(branch, {"status": "ok", "error_code": None})
        self._branch_trace.set(branch_trace)
        if branch_errors and not self._allow_degraded:
            first_error = next(iter(branch_errors.values()))
            raise first_error
        if branch_errors and any(
            not _is_degradable_error(error) for error in branch_errors.values()
        ):
            first_error = next(
                error for error in branch_errors.values() if not _is_degradable_error(error)
            )
            raise first_error
        if len(branch_errors) == 2:
            first_error = next(iter(branch_errors.values()))
            raise first_error
        dense_candidates = [] if isinstance(dense_result, BaseException) else dense_result
        sparse_candidates = [] if isinstance(sparse_result, BaseException) else sparse_result
        try:
            return self._merger.merge(
                request=request,
                filters=filters,
                dense_candidates=dense_candidates,
                sparse_candidates=sparse_candidates,
            )
        except RetrievalError:
            raise
        except Exception as exc:
            raise RetrievalError(
                code=RETRIEVAL_HYBRID_MERGE_FAILED,
                message="Hybrid retrieval merge failed.",
                details=_safe_error_details(
                    request=request,
                    filters=filters,
                    error_code=RETRIEVAL_HYBRID_MERGE_FAILED,
                    hybrid_stage="merge",
                    branch=None,
                    safe_counts={
                        "dense_candidates": len(dense_candidates),
                        "sparse_candidates": len(sparse_candidates),
                    },
                ),
                status_code=502,
                ) from exc

    def _retrievers_share_session(self) -> bool:
        dense_session = getattr(
            getattr(self._dense_retriever, "_vector_store", None), "_session", None
        )
        sparse_session = getattr(
            getattr(self._sparse_retriever, "_backend", None), "_session", None
        )
        return dense_session is not None and dense_session is sparse_session

    async def _retrieve_sequentially(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> list[list[RetrievalCandidate] | BaseException]:
        results: list[list[RetrievalCandidate] | BaseException] = []
        for branch, retriever in (
            (_DENSE, self._dense_retriever),
            (_SPARSE, self._sparse_retriever),
        ):
            try:
                results.append(
                    await self._retrieve_branch(
                        branch=branch,
                        retriever=retriever,
                        request=request,
                        filters=filters,
                    )
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                results.append(exc)
        return results

    async def _retrieve_branch(
        self,
        *,
        branch: str,
        retriever: CandidateRetriever,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> list[RetrievalCandidate]:
        try:
            return await retriever.retrieve(request=request, filters=filters)
        except Exception as exc:
            details = _safe_error_details(
                request=request,
                filters=filters,
                error_code=RETRIEVAL_HYBRID_BRANCH_FAILED,
                hybrid_stage="branch",
                branch=branch,
                safe_counts={"returned_candidates": 0},
            )
            if isinstance(exc, RetrievalError):
                details["upstream_error_code"] = exc.code
                if bool(getattr(exc, "retryable", False)):
                    details["upstream_retryable"] = True
            elif isinstance(exc, (TimeoutError, ConnectionError, OSError)):
                details["upstream_error_type"] = type(exc).__name__
            raise RetrievalError(
                code=RETRIEVAL_HYBRID_BRANCH_FAILED,
                message="Hybrid retrieval branch failed.",
                details=details,
                status_code=502,
            ) from exc


def _is_degradable_error(error: BaseException) -> bool:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (TimeoutError, ConnectionError, OSError)):
            return True
        retryable = getattr(current, "retryable", None)
        if retryable is False:
            return False
        if retryable is True:
            return True
        current = current.__cause__
    return False


def _candidate_in_scope(*, candidate: RetrievalCandidate, filters: RetrievalFilterSet) -> bool:
    if candidate.tenant_id != filters.tenant_id:
        return False
    if not _metadata_matches(candidate.metadata, filters.metadata_filter):
        return False
    return acl_allows(candidate.acl, to_vector_acl_filter(filters))


def _metadata_matches(
    candidate_metadata: Mapping[str, object],
    required_metadata: Mapping[str, object],
) -> bool:
    return all(candidate_metadata.get(key) == value for key, value in required_metadata.items())


def _effective_threshold(
    *,
    request: RetrievalRequest,
    config: HybridMergeConfig,
) -> float | None:
    values = [
        value
        for value in (request.score_threshold, config.min_fusion_score)
        if value is not None
    ]
    if not values:
        return None
    return max(values)


def _fused_candidate(*, bucket: _FusionBucket, config: HybridMergeConfig) -> RetrievalCandidate:
    raw_score = sum(source.contribution for source in bucket.sources)
    max_possible = (config.dense_weight + config.sparse_weight) / (config.rank_constant + 1.0)
    normalized = raw_score / max_possible
    normalized = min(1.0, max(0.0, normalized))
    source_methods = tuple(source.retrieval_method for source in bucket.sources)
    provenance = {
        "retrieval_methods": source_methods,
        "sources": tuple(source.model_dump() for source in bucket.sources),
        "raw_rrf_score": raw_score,
        "normalized_fusion_score": normalized,
        "fusion_reason": _fusion_reason(source_methods),
    }
    metadata = _safe_metadata(bucket.candidate.metadata)
    metadata[PROVENANCE_METADATA_KEY] = provenance
    return bucket.candidate.model_copy(
        update={
            "score": normalized,
            "retrieval_method": "hybrid",
            "metadata": metadata,
        }
    )


def _fusion_reason(source_methods: tuple[str, ...]) -> str:
    methods = set(source_methods)
    if methods == {_DENSE, _SPARSE}:
        return "dense_sparse_overlap"
    if methods == {_DENSE}:
        return "dense_only"
    return "sparse_only"


def _sort_key(candidate: RetrievalCandidate) -> tuple[float, float, int, int, str, str, str, str]:
    provenance = candidate.metadata.get(PROVENANCE_METADATA_KEY)
    if not isinstance(provenance, Mapping):
        return (
            -candidate.score,
            -candidate.score,
            0,
            0,
            candidate.tenant_id,
            candidate.document_id,
            candidate.version_id,
            candidate.chunk_id,
        )
    raw_score = _float_value(provenance.get("raw_rrf_score"))
    sources = provenance.get("sources")
    source_count = len(sources) if isinstance(sources, tuple | list) else 0
    best_rank = _best_rank(sources)
    return (
        -candidate.score,
        -raw_score,
        -source_count,
        best_rank,
        candidate.tenant_id,
        candidate.document_id,
        candidate.version_id,
        candidate.chunk_id,
    )


def _best_rank(sources: object) -> int:
    if not isinstance(sources, tuple | list):
        return 0
    ranks: list[int] = []
    for source in sources:
        if isinstance(source, Mapping) and isinstance(source.get("rank"), int):
            ranks.append(source["rank"])
    return min(ranks) if ranks else 0


def _float_value(value: object) -> float:
    if isinstance(value, int | float) and isfinite(value):
        return float(value)
    return 0.0


def _safe_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    safe: dict[str, object] = {}
    for key, value in metadata.items():
        key_text = str(key)
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


def _is_sensitive_metadata_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_").replace(" ", "_")
    compact = "".join(char for char in normalized if char.isalnum())
    return (
        normalized == PROVENANCE_METADATA_KEY
        or normalized in _SENSITIVE_METADATA_KEYS
        or compact in _SENSITIVE_METADATA_COMPACT_KEYS
    )


def _safe_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _safe_metadata(value)
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


def _safe_error_details(
    *,
    request: RetrievalRequest,
    filters: RetrievalFilterSet,
    error_code: str,
    hybrid_stage: str,
    branch: str | None,
    safe_counts: Mapping[str, int],
) -> dict[str, object]:
    details: dict[str, object] = {
        "request_id": request.request_id,
        "trace_id": request.trace_id,
        "tenant_id": filters.tenant_id,
        "user_id": filters.user_id,
        "top_k": request.top_k,
        "retrieval_method": "hybrid",
        "hybrid_stage": hybrid_stage,
        "safe_counts": dict(safe_counts),
    }
    if branch is not None:
        details["branch"] = branch
    details["error_code"] = error_code
    return details


def summarize_fusion_methods(candidates: Iterable[RetrievalCandidate]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for candidate in candidates:
        summary[candidate.retrieval_method] = summary.get(candidate.retrieval_method, 0) + 1
    return summary

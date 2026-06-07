from __future__ import annotations

from collections.abc import Iterable, Mapping
from time import perf_counter

from packages.auth.context import AuthContext
from packages.retrieval.dto import (
    RetrievalCandidate,
    RetrievalFilterSet,
    RetrievalRequest,
    RetrievalResult,
)
from packages.retrieval.exceptions import (
    RETRIEVAL_AUTH_REQUIRED,
    RETRIEVAL_BACKEND_FAILED,
    RetrievalError,
)
from packages.retrieval.filters import (
    build_retrieval_filter_set,
    to_vector_acl_filter,
)
from packages.retrieval.ports import CandidateRetriever
from packages.vectorstores.acl import acl_allows


class RetrievalService:
    def __init__(self, *, retriever: CandidateRetriever) -> None:
        self._retriever = retriever

    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        auth: AuthContext | None,
    ) -> RetrievalResult:
        started = perf_counter()
        if auth is None:
            raise RetrievalError(
                code=RETRIEVAL_AUTH_REQUIRED,
                message="Authentication context is required for retrieval.",
                details=_safe_details(
                    request=request,
                    auth=None,
                    error_code=RETRIEVAL_AUTH_REQUIRED,
                ),
                status_code=401,
            )

        filters = build_retrieval_filter_set(auth=auth, request=request)
        try:
            candidates = await self._retriever.retrieve(request=request, filters=filters)
        except RetrievalError:
            raise
        except Exception as exc:
            raise RetrievalError(
                code=RETRIEVAL_BACKEND_FAILED,
                message="Retrieval backend failed.",
                details=_safe_details(
                    request=request,
                    auth=auth,
                    error_code=RETRIEVAL_BACKEND_FAILED,
                ),
                status_code=502,
            ) from exc

        safe_candidates = _safe_candidates(
            request=request,
            auth=auth,
            filters=filters,
            candidates=candidates,
        )
        return RetrievalResult(
            request_id=request.request_id,
            trace_id=request.trace_id,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            top_k=request.top_k,
            query_summary=_query_summary(request.query),
            candidates=safe_candidates,
            latency_ms=(perf_counter() - started) * 1000,
            error_code=None,
        )


def _safe_details(
    *,
    request: RetrievalRequest,
    auth: AuthContext | None,
    error_code: str,
) -> dict[str, object]:
    details: dict[str, object] = {
        "request_id": request.request_id,
        "trace_id": request.trace_id,
        "top_k": request.top_k,
        "error_code": error_code,
    }
    if auth is not None:
        details["tenant_id"] = auth.tenant_id
        details["user_id"] = auth.user_id
    return details


def _query_summary(query: str) -> dict[str, int]:
    return {"length": len(query)}


def _safe_candidates(
    *,
    request: RetrievalRequest,
    auth: AuthContext,
    filters: RetrievalFilterSet,
    candidates: Iterable[RetrievalCandidate],
) -> tuple[RetrievalCandidate, ...]:
    acl_filter = to_vector_acl_filter(filters)
    safe = []
    for candidate in candidates:
        if candidate.tenant_id != auth.tenant_id:
            raise RetrievalError(
                code=RETRIEVAL_BACKEND_FAILED,
                message="Retrieval backend returned an out-of-scope candidate.",
                details=_safe_details(
                    request=request,
                    auth=auth,
                    error_code=RETRIEVAL_BACKEND_FAILED,
                ),
                status_code=502,
            )
        if request.score_threshold is not None and candidate.score < request.score_threshold:
            continue
        if not _metadata_matches(candidate.metadata, filters.metadata_filter):
            continue
        if not acl_allows(candidate.acl, acl_filter):
            continue
        safe.append(candidate)
        if len(safe) >= request.top_k:
            break
    return tuple(safe)


def _metadata_matches(
    candidate_metadata: Mapping[str, object],
    required_metadata: Mapping[str, object],
) -> bool:
    return all(candidate_metadata.get(key) == value for key, value in required_metadata.items())

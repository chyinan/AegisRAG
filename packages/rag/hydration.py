from __future__ import annotations

from collections.abc import Mapping
from math import isfinite
from typing import Protocol

from packages.auth.context import AuthContext
from packages.data.dto import ChunkRecord
from packages.rag.access import acl_filter_from_auth
from packages.rag.dto import ContextCandidate
from packages.rag.exceptions import RAG_QUERY_CONTEXT_UNAVAILABLE, RagQueryError
from packages.retrieval.dto import RetrievalCandidate
from packages.vectorstores.acl import acl_allows


class ChunkHydrationRepository(Protocol):
    async def get_chunk(
        self,
        *,
        tenant_id: str,
        chunk_id: str,
        document_id: str | None = None,
        version_id: str | None = None,
    ) -> ChunkRecord | None: ...


class RetrievalCandidateHydrator:
    def __init__(self, *, repository: ChunkHydrationRepository) -> None:
        self._repository = repository

    async def hydrate(
        self,
        *,
        candidates: tuple[RetrievalCandidate, ...],
        auth: AuthContext,
        request_id: str,
        trace_id: str,
    ) -> tuple[ContextCandidate, ...]:
        hydrated: list[ContextCandidate] = []
        for candidate in candidates:
            chunk = await self._repository.get_chunk(
                tenant_id=auth.tenant_id,
                document_id=candidate.document_id,
                version_id=candidate.version_id,
                chunk_id=candidate.chunk_id,
            )
            if chunk is None:
                raise _hydration_error(
                    candidate=candidate,
                    auth=auth,
                    request_id=request_id,
                    trace_id=trace_id,
                    reason="missing_or_unauthorized_chunk",
                )
            _validate_chunk(
                candidate=candidate,
                chunk=chunk,
                auth=auth,
                request_id=request_id,
                trace_id=trace_id,
            )
            hydrated.append(_context_candidate(candidate=candidate, chunk=chunk))
        return tuple(hydrated)


def _validate_chunk(
    *,
    candidate: RetrievalCandidate,
    chunk: ChunkRecord,
    auth: AuthContext,
    request_id: str,
    trace_id: str,
) -> None:
    reason = _invalid_chunk_reason(candidate=candidate, chunk=chunk, auth=auth)
    if reason is None:
        return
    raise _hydration_error(
        candidate=candidate,
        auth=auth,
        request_id=request_id,
        trace_id=trace_id,
        reason=reason,
    )


def _invalid_chunk_reason(
    *,
    candidate: RetrievalCandidate,
    chunk: ChunkRecord,
    auth: AuthContext,
) -> str | None:
    if chunk.tenant_id != auth.tenant_id or candidate.tenant_id != auth.tenant_id:
        return "tenant_mismatch"
    if (
        chunk.document_id != candidate.document_id
        or chunk.version_id != candidate.version_id
        or chunk.chunk_id != candidate.chunk_id
    ):
        return "identity_mismatch"
    if chunk.status != "active" or chunk.deleted_at is not None:
        return "inactive_chunk"
    if chunk.source_type != candidate.source_type:
        return "source_type_mismatch"
    if (
        candidate.source_uri is not None
        and chunk.source_uri is not None
        and candidate.source_uri != chunk.source_uri
    ):
        return "source_uri_mismatch"
    if tuple(chunk.title_path) != candidate.title_path:
        return "title_path_mismatch"
    if chunk.page_start != candidate.page_start or chunk.page_end != candidate.page_end:
        return "page_mismatch"
    acl_filter = acl_filter_from_auth(auth)
    if not acl_allows(chunk.acl, acl_filter):
        return "acl_denied"
    if not acl_allows(candidate.acl, acl_filter):
        return "candidate_acl_denied"
    return None


def _context_candidate(
    *,
    candidate: RetrievalCandidate,
    chunk: ChunkRecord,
) -> ContextCandidate:
    return ContextCandidate(
        content=chunk.content,
        token_count=chunk.token_count,
        document_id=candidate.document_id,
        version_id=candidate.version_id,
        chunk_id=candidate.chunk_id,
        tenant_id=candidate.tenant_id,
        acl=chunk.acl,
        source=candidate.source,
        source_uri=chunk.source_uri or candidate.source_uri,
        source_type=chunk.source_type,
        page_start=chunk.page_start,
        page_end=chunk.page_end,
        title_path=tuple(chunk.title_path),
        score=_normalize_score(candidate.score),
        retrieval_method=candidate.retrieval_method,
        metadata=_safe_hydration_metadata(candidate.metadata),
    )


def _normalize_score(score: float) -> float:
    if not isfinite(score):
        return 0.0
    if score < 0.0:
        return 0.0
    if score > 1.0:
        return 1.0
    return float(score)

def _safe_hydration_metadata(metadata: Mapping[str, object]) -> dict[str, object]:
    allowed = {
        "chunk_index",
        "sequence",
        "parent_chunk_id",
        "child_chunk_ids",
        "neighbor_prev_chunk_id",
        "neighbor_next_chunk_id",
        "retrieval_provenance",
        "rerank_provenance",
    }
    return {str(key): value for key, value in metadata.items() if str(key) in allowed}


def _hydration_error(
    *,
    candidate: RetrievalCandidate,
    auth: AuthContext,
    request_id: str,
    trace_id: str,
    reason: str,
) -> RagQueryError:
    return RagQueryError(
        code=RAG_QUERY_CONTEXT_UNAVAILABLE,
        message="Query context is unavailable.",
        details={
            "request_id": request_id,
            "trace_id": trace_id,
            "tenant_id": auth.tenant_id,
            "user_id": auth.user_id,
            "stage": "hydration",
            "error_code": RAG_QUERY_CONTEXT_UNAVAILABLE,
            "safe_counts": {
                "unavailable_context_count": 1,
            },
        },
        status_code=404,
    )

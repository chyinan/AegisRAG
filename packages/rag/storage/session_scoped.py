"""Short, read-only SQL operations for ordinary query and chat.

Every operation creates its own session; closing rolls back only its own read
transaction. No request/Agent/memory session is committed or rolled back here.
Retrieved/hydrated DTOs are detached before reranking or generation can wait.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.auth.context import AuthContext
from packages.common.tenant_context import current_tenant_id, tenant_scope
from packages.data.storage.repositories import DocumentRepository
from packages.rag.dto import ContextCandidate
from packages.rag.hydration import RetrievalCandidateHydrator
from packages.retrieval.dto import RetrievalCandidate, RetrievalRequest
from packages.retrieval.sparse import (
    SparseChunkRecord,
    SparseRetrieverConfig,
    SqlAlchemySparseSearchBackend,
)
from packages.vectorstores.dto import VectorSearchRequest, VectorSearchResult
from packages.vectorstores.ports import VectorStore


@contextmanager
def _read_tenant(tenant_id: str) -> Iterator[None]:
    inherited = current_tenant_id()
    if inherited is not None and inherited != tenant_id:
        raise ValueError("Read tenant differs from authenticated tenant scope")
    with tenant_scope(tenant_id):
        yield


class SessionFactoryVectorSearch:
    def __init__(
        self, *, session_factory: async_sessionmaker[AsyncSession],
        build_store: Callable[[AsyncSession], VectorStore],
    ) -> None:
        self._session_factory = session_factory
        self._build_store = build_store

    async def search(self, request: VectorSearchRequest) -> list[VectorSearchResult]:
        with _read_tenant(request.tenant_id):
            async with self._session_factory() as session:
                return await self._build_store(session).search(request)


class SessionFactorySparseSearchBackend:
    backend_kind = "postgres"

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def search(
        self, *, request: RetrievalRequest, filters_payload: Mapping[str, object],
        query_terms: tuple[str, ...], config: SparseRetrieverConfig,
    ) -> list[SparseChunkRecord]:
        tenant_id = filters_payload.get("tenant_id")
        if not isinstance(tenant_id, str) or not tenant_id:
            raise ValueError("Authenticated tenant is required for sparse reads")
        with _read_tenant(tenant_id):
            async with self._session_factory() as session:
                return await SqlAlchemySparseSearchBackend(session=session).search(
                    request=request, filters_payload=filters_payload,
                    query_terms=query_terms, config=config,
                )


class SessionFactoryCandidateHydrator(RetrievalCandidateHydrator):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def hydrate(
        self, *, candidates: tuple[RetrievalCandidate, ...], auth: AuthContext,
        request_id: str, trace_id: str,
    ) -> tuple[ContextCandidate, ...]:
        with _read_tenant(auth.tenant_id):
            async with self._session_factory() as session:
                return await RetrievalCandidateHydrator(
                    repository=DocumentRepository(session),
                ).hydrate(
                    candidates=candidates, auth=auth,
                    request_id=request_id, trace_id=trace_id,
                )

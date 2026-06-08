from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from packages.retrieval.dto import (
    RetrievalCandidate,
    RetrievalFilterSet,
    RetrievalLogCreate,
    RetrievalLogRecord,
    RetrievalRequest,
)

if TYPE_CHECKING:
    from packages.retrieval.rerank import RerankResult


class CandidateRetriever(Protocol):
    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> list[RetrievalCandidate]:
        ...


class Reranker(Protocol):
    async def rerank(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
        candidates: Sequence[RetrievalCandidate],
    ) -> RerankResult:
        ...


class RetrievalLogPort(Protocol):
    async def create(self, record: RetrievalLogCreate) -> RetrievalLogRecord:
        ...

    async def get_by_request_id(
        self,
        *,
        tenant_id: str,
        request_id: str,
    ) -> RetrievalLogRecord | None:
        ...

    async def list_by_request_id(
        self,
        *,
        tenant_id: str,
        request_id: str,
    ) -> list[RetrievalLogRecord]:
        ...

    async def list_by_trace_id(
        self,
        *,
        tenant_id: str,
        trace_id: str,
        limit: int = 100,
    ) -> list[RetrievalLogRecord]:
        ...

    async def list_by_created_at(
        self,
        *,
        tenant_id: str,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int = 100,
    ) -> list[RetrievalLogRecord]:
        ...

    async def commit(self) -> None:
        ...

    async def rollback(self) -> None:
        ...

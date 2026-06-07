from __future__ import annotations

from typing import Protocol, runtime_checkable

import pytest

from packages.auth.context import AuthContext
from packages.embeddings.adapters.fake import FakeEmbeddingProvider
from packages.embeddings.dto import EmbeddingRequest
from packages.retrieval.dense import DenseRetriever, DenseRetrieverConfig
from packages.retrieval.dto import (
    RetrievalCandidate,
    RetrievalFilterSet,
    RetrievalRequest,
)
from packages.retrieval.exceptions import (
    RETRIEVAL_AUTH_REQUIRED,
    RETRIEVAL_BACKEND_FAILED,
    RetrievalError,
)
from packages.retrieval.service import RetrievalService
from packages.vectorstores.adapters.fake import FakeVectorStore
from packages.vectorstores.dto import VectorRecord


@runtime_checkable
class _FakeRetriever(Protocol):
    last_request: RetrievalRequest | None
    last_filters: RetrievalFilterSet | None


class FilteringFakeRetriever:
    def __init__(self, candidates: list[RetrievalCandidate]) -> None:
        self._candidates = candidates
        self.last_request: RetrievalRequest | None = None
        self.last_filters: RetrievalFilterSet | None = None

    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> list[RetrievalCandidate]:
        self.last_request = request
        self.last_filters = filters
        return [
            candidate
            for candidate in self._candidates
            if candidate.tenant_id == filters.tenant_id
            and candidate.metadata.get("department") == filters.metadata_filter.get("department")
            and candidate.acl.get("visibility") != "private"
        ][: request.top_k]


class FailingRetriever:
    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> list[RetrievalCandidate]:
        raise RuntimeError("database password leaked in raw backend error")


class LeakyRetriever:
    def __init__(self, candidates: list[RetrievalCandidate]) -> None:
        self._candidates = candidates

    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> list[RetrievalCandidate]:
        return self._candidates


@pytest.mark.asyncio
async def test_service_requires_auth_context() -> None:
    service = RetrievalService(retriever=FilteringFakeRetriever([]))
    request = RetrievalRequest(query="policy", request_id="req-1", trace_id="trace-1")

    with pytest.raises(RetrievalError) as exc_info:
        await service.retrieve(request=request, auth=None)

    assert exc_info.value.code == RETRIEVAL_AUTH_REQUIRED
    assert exc_info.value.details == {
        "request_id": "req-1",
        "trace_id": "trace-1",
        "top_k": 10,
        "error_code": RETRIEVAL_AUTH_REQUIRED,
    }


@pytest.mark.asyncio
async def test_service_passes_same_filter_set_to_retriever_before_candidates_are_produced() -> None:
    retriever = FilteringFakeRetriever(
        [
            _candidate(chunk_id="allowed", tenant_id="tenant-a", department="people"),
            _candidate(chunk_id="wrong-tenant", tenant_id="tenant-b", department="people"),
            _candidate(chunk_id="wrong-metadata", tenant_id="tenant-a", department="finance"),
            _candidate(
                chunk_id="private",
                tenant_id="tenant-a",
                department="people",
                acl={"visibility": "private"},
            ),
        ]
    )
    service = RetrievalService(retriever=retriever)
    request = RetrievalRequest(
        query="policy",
        top_k=5,
        metadata_filter={"department": "people"},
        request_id="req-1",
        trace_id="trace-1",
    )
    auth = AuthContext(
        user_id="user-1",
        tenant_id="tenant-a",
        roles=("hr",),
        permissions=("document:read",),
    )

    result = await service.retrieve(request=request, auth=auth)

    assert isinstance(retriever, _FakeRetriever)
    assert retriever.last_request == request
    assert retriever.last_filters is not None
    assert retriever.last_filters.tenant_id == "tenant-a"
    assert retriever.last_filters.metadata_filter == {"department": "people"}
    assert [candidate.chunk_id for candidate in result.candidates] == ["allowed"]
    assert result.request_id == "req-1"
    assert result.trace_id == "trace-1"
    assert result.tenant_id == "tenant-a"
    assert result.user_id == "user-1"
    assert result.top_k == 5
    assert result.query_summary == {"length": 6}
    assert result.error_code is None
    assert result.latency_ms is not None
    assert result.latency_ms >= 0


@pytest.mark.asyncio
async def test_service_empty_result_is_valid_and_does_not_fabricate_candidates() -> None:
    service = RetrievalService(retriever=FilteringFakeRetriever([]))

    result = await service.retrieve(
        request=RetrievalRequest(query="missing", request_id="req-1", trace_id="trace-1"),
        auth=AuthContext(user_id="user-1", tenant_id="tenant-a"),
    )

    assert result.candidates == ()
    assert result.error_code is None


@pytest.mark.asyncio
async def test_service_wraps_backend_failures_with_safe_details() -> None:
    service = RetrievalService(retriever=FailingRetriever())

    with pytest.raises(RetrievalError) as exc_info:
        await service.retrieve(
            request=RetrievalRequest(
                query="secret full text",
                request_id="req-1",
                trace_id="trace-1",
            ),
            auth=AuthContext(user_id="user-1", tenant_id="tenant-a"),
        )

    assert exc_info.value.code == RETRIEVAL_BACKEND_FAILED
    assert exc_info.value.details == {
        "request_id": "req-1",
        "trace_id": "trace-1",
        "tenant_id": "tenant-a",
        "user_id": "user-1",
        "top_k": 10,
        "error_code": RETRIEVAL_BACKEND_FAILED,
    }
    assert "secret full text" not in str(exc_info.value.details)
    assert "password" not in str(exc_info.value.details).lower()


@pytest.mark.asyncio
async def test_service_rejects_cross_tenant_candidates_from_retriever() -> None:
    service = RetrievalService(
        retriever=LeakyRetriever(
            [_candidate(chunk_id="wrong-tenant", tenant_id="tenant-b", department="people")]
        )
    )

    with pytest.raises(RetrievalError) as exc_info:
        await service.retrieve(
            request=RetrievalRequest(
                query="policy",
                metadata_filter={"department": "people"},
                request_id="req-1",
                trace_id="trace-1",
            ),
            auth=AuthContext(user_id="user-1", tenant_id="tenant-a"),
        )

    assert exc_info.value.code == RETRIEVAL_BACKEND_FAILED
    assert exc_info.value.details == {
        "request_id": "req-1",
        "trace_id": "trace-1",
        "tenant_id": "tenant-a",
        "user_id": "user-1",
        "top_k": 10,
        "error_code": RETRIEVAL_BACKEND_FAILED,
    }


@pytest.mark.asyncio
async def test_service_filters_backend_candidates_by_metadata_acl_threshold_and_top_k() -> None:
    service = RetrievalService(
        retriever=LeakyRetriever(
            [
                _candidate(
                    chunk_id="low-score",
                    tenant_id="tenant-a",
                    department="people",
                    score=0.1,
                ),
                _candidate(chunk_id="wrong-metadata", tenant_id="tenant-a", department="finance"),
                _candidate(
                    chunk_id="private",
                    tenant_id="tenant-a",
                    department="people",
                    acl={"visibility": "private"},
                ),
                _candidate(chunk_id="allowed-1", tenant_id="tenant-a", department="people"),
                _candidate(chunk_id="allowed-2", tenant_id="tenant-a", department="people"),
                _candidate(chunk_id="allowed-3", tenant_id="tenant-a", department="people"),
            ]
        )
    )

    result = await service.retrieve(
        request=RetrievalRequest(
            query="policy",
            top_k=2,
            metadata_filter={"department": "people"},
            score_threshold=0.5,
            request_id="req-1",
            trace_id="trace-1",
        ),
        auth=AuthContext(user_id="user-1", tenant_id="tenant-a"),
    )

    assert [candidate.chunk_id for candidate in result.candidates] == ["allowed-1", "allowed-2"]


@pytest.mark.asyncio
async def test_service_accepts_dense_retriever_as_candidate_retriever_and_keeps_guard() -> None:
    embedding_provider = FakeEmbeddingProvider(
        dim=4,
        provider="fake",
        model="fake-embedding",
        version="fake-v1",
    )
    query_response = await embedding_provider.embed_texts(
        EmbeddingRequest(
            texts=["policy"],
            provider="fake",
            model="fake-embedding",
            timeout_seconds=1.0,
            retry_budget=1,
            rate_limit_key="tenant-a",
        )
    )
    vector_store = FakeVectorStore(index_dim=4)
    await vector_store.upsert(
        [
            VectorRecord(
                tenant_id="tenant-a",
                document_id="doc-1",
                version_id="ver-1",
                chunk_id="allowed",
                created_by="user-1",
                status="active",
                vector=query_response.vectors[0].vector,
                embedding_provider="fake",
                embedding_model="fake-embedding",
                embedding_version="fake-v1",
                embedding_dim=4,
                source_type="markdown",
                source_uri="kb://policy.md",
                title_path=["Policy"],
                page_start=1,
                page_end=1,
                token_count=10,
                acl={"visibility": "tenant", "allowed_roles": ["hr"]},
                checksum="checksum-allowed",
                metadata={"department": "people"},
            )
        ]
    )
    service = RetrievalService(
        retriever=DenseRetriever(
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            config=DenseRetrieverConfig(
                embedding_provider="fake",
                embedding_model="fake-embedding",
                embedding_version="fake-v1",
                timeout_seconds=1.0,
                retry_budget=1,
            ),
        )
    )

    result = await service.retrieve(
        request=RetrievalRequest(
            query="policy",
            metadata_filter={"department": "people"},
            request_id="req-1",
            trace_id="trace-1",
        ),
        auth=AuthContext(user_id="user-1", tenant_id="tenant-a", roles=("hr",)),
    )

    assert [candidate.chunk_id for candidate in result.candidates] == ["allowed"]
    assert result.candidates[0].retrieval_method == "dense"


def _candidate(
    *,
    chunk_id: str,
    tenant_id: str,
    department: str,
    score: float = 0.9,
    acl: dict[str, object] | None = None,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        document_id="doc-1",
        version_id="ver-1",
        chunk_id=chunk_id,
        source="kb://policy.md",
        source_type="markdown",
        source_uri="kb://policy.md",
        page_start=1,
        page_end=1,
        title_path=("Policy",),
        score=score,
        retrieval_method="fake",
        tenant_id=tenant_id,
        acl=acl or {"visibility": "tenant"},
        metadata={"department": department},
    )

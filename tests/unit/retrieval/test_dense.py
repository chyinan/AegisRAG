from __future__ import annotations

from math import inf, nan
from typing import Any

import pytest

from packages.auth.context import AuthContext
from packages.embeddings.adapters.fake import FakeEmbeddingProvider
from packages.embeddings.dto import EmbeddingRequest, EmbeddingResponse, EmbeddingVector
from packages.retrieval.dense import DenseRetriever, DenseRetrieverConfig
from packages.retrieval.dto import RetrievalRequest
from packages.retrieval.exceptions import (
    RETRIEVAL_EMBEDDING_FAILED,
    RETRIEVAL_VECTOR_SEARCH_FAILED,
    RetrievalError,
)
from packages.retrieval.filters import build_retrieval_filter_set
from packages.retrieval.service import RetrievalService
from packages.vectorstores.adapters.fake import FakeVectorStore
from packages.vectorstores.dto import (
    VectorDeleteResult,
    VectorRecord,
    VectorSearchRequest,
    VectorSearchResult,
    VectorUpsertResult,
)
from packages.vectorstores.exceptions import VECTOR_STORE_SEARCH_FAILED, VectorStoreError


@pytest.mark.asyncio
async def test_dense_retriever_embeds_query_searches_vector_store_and_maps_candidates() -> None:
    embedding_provider = RecordingEmbeddingProvider(dim=4)
    vector_store = RecordingVectorStore(index_dim=4)
    query_embedding = await embedding_provider.embed_texts(
        EmbeddingRequest(
            texts=["policy question"],
            provider="fake",
            model="fake-embedding",
            timeout_seconds=1.5,
            retry_budget=2,
            rate_limit_key="tenant-a",
        )
    )
    await vector_store.upsert(
        [_record(chunk_id="allowed", vector=query_embedding.vectors[0].vector)]
    )
    await vector_store.upsert(
        [_record(chunk_id="allowed-2", vector=query_embedding.vectors[0].vector)]
    )
    await vector_store.upsert(
        [_record(chunk_id="allowed-3", vector=query_embedding.vectors[0].vector)]
    )
    await vector_store.upsert(
        [
            _record(
                chunk_id="wrong-department",
                vector=query_embedding.vectors[0].vector,
                department="finance",
            )
        ]
    )
    await vector_store.upsert(
        [
            _record(
                chunk_id="wrong-model",
                vector=query_embedding.vectors[0].vector,
                embedding_model="other-model",
            )
        ]
    )
    await vector_store.upsert(
        [
            _record(
                chunk_id="wrong-provider",
                vector=query_embedding.vectors[0].vector,
                embedding_provider="other-provider",
            )
        ]
    )
    await vector_store.upsert(
        [
            _record(
                chunk_id="wrong-version",
                vector=query_embedding.vectors[0].vector,
                embedding_version="other-version",
            )
        ]
    )
    await vector_store.upsert(
        [
            _record(
                chunk_id="wrong-tenant",
                tenant_id="tenant-b",
                vector=query_embedding.vectors[0].vector,
            )
        ]
    )
    await vector_store.upsert(
        [
            _record(
                chunk_id="private",
                vector=query_embedding.vectors[0].vector,
                acl={"visibility": "private"},
            )
        ]
    )
    await vector_store.upsert(
        [
            _record(
                chunk_id="low-score",
                vector=[-value for value in query_embedding.vectors[0].vector],
            )
        ]
    )
    await vector_store.upsert(
        [_record(chunk_id="deleted", vector=query_embedding.vectors[0].vector)]
    )
    await vector_store.delete_by_document(
        "doc-deleted",
        "ver-deleted",
        tenant_id="tenant-a",
    )
    retriever = DenseRetriever(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        config=_config(),
    )
    request = RetrievalRequest(
        query="policy question",
        top_k=2,
        score_threshold=0.5,
        metadata_filter={"department": "people"},
        request_id="req-1",
        trace_id="trace-1",
    )
    filters = build_retrieval_filter_set(auth=_auth(), request=request)

    candidates = await retriever.retrieve(request=request, filters=filters)

    assert embedding_provider.last_request is not None
    assert embedding_provider.last_request.texts == ["policy question"]
    assert embedding_provider.last_request.provider == "fake"
    assert embedding_provider.last_request.model == "fake-embedding"
    assert embedding_provider.last_request.timeout_seconds == 1.5
    assert embedding_provider.last_request.retry_budget == 2
    assert embedding_provider.last_request.rate_limit_key == "tenant-a"
    assert embedding_provider.last_request.metadata == {
        "request_id": "req-1",
        "trace_id": "trace-1",
        "tenant_id": "tenant-a",
        "user_id": "user-1",
        "retrieval_method": "dense",
    }

    assert vector_store.last_request is not None
    assert vector_store.last_request.tenant_id == "tenant-a"
    assert vector_store.last_request.embedding_dim == 4
    assert vector_store.last_request.top_k == 2
    assert vector_store.last_request.score_threshold == 0.5
    assert vector_store.last_request.include_deleted is False
    assert vector_store.last_request.distance_metric == "cosine"
    assert vector_store.last_request.embedding_provider == "fake"
    assert vector_store.last_request.embedding_model == "fake-embedding"
    assert vector_store.last_request.embedding_version == "fake-v1"
    assert [(item.key, item.value) for item in vector_store.last_request.metadata_filters] == [
        ("department", "people")
    ]
    assert vector_store.last_request.acl_filter.user_id == "user-1"
    assert vector_store.last_request.acl_filter.roles == ["hr"]

    assert [candidate.chunk_id for candidate in candidates] == ["allowed", "allowed-2"]
    candidate = candidates[0]
    assert candidate.document_id == "doc-allowed"
    assert candidate.version_id == "ver-allowed"
    assert candidate.source == "kb://allowed.md"
    assert candidate.source_type == "markdown"
    assert candidate.source_uri == "kb://allowed.md"
    assert candidate.page_start == 1
    assert candidate.page_end == 2
    assert candidate.title_path == ("Policy", "allowed")
    assert candidate.tenant_id == "tenant-a"
    assert candidate.retrieval_method == "dense"
    assert candidate.metadata == {"department": "people"}
    assert "vector" not in candidate.metadata
    assert "secret" not in str(candidate.model_dump()).lower()


@pytest.mark.asyncio
async def test_dense_retriever_keeps_retrieval_service_boundary_and_guard() -> None:
    embedding_provider = RecordingEmbeddingProvider(dim=4)
    vector_store = RecordingVectorStore(index_dim=4)
    query_embedding = await embedding_provider.embed_texts(
        EmbeddingRequest(
            texts=["policy question"],
            provider="fake",
            model="fake-embedding",
            timeout_seconds=1.5,
            retry_budget=2,
            rate_limit_key="tenant-a",
        )
    )
    await vector_store.upsert(
        [_record(chunk_id="allowed", vector=query_embedding.vectors[0].vector)]
    )
    service = RetrievalService(
        retriever=DenseRetriever(
            embedding_provider=embedding_provider,
            vector_store=vector_store,
            config=_config(),
        )
    )

    result = await service.retrieve(
        request=RetrievalRequest(
            query="policy question",
            top_k=2,
            metadata_filter={"department": "people"},
            request_id="req-1",
            trace_id="trace-1",
        ),
        auth=_auth(),
    )

    assert [candidate.chunk_id for candidate in result.candidates] == ["allowed"]
    assert result.query_summary == {"length": len("policy question")}
    assert embedding_provider.last_request is not None
    assert vector_store.last_request is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "raw_message"),
    [
        (FakeEmbeddingProvider(failure_mode="failed"), "fake embedding provider failed"),
        (FakeEmbeddingProvider(failure_mode="timeout"), "fake embedding provider timeout"),
        (
            FakeEmbeddingProvider(failure_mode="rate_limited"),
            "fake embedding provider rate limited",
        ),
    ],
)
async def test_dense_retriever_maps_provider_failures_to_safe_retrieval_error(
    provider: FakeEmbeddingProvider,
    raw_message: str,
) -> None:
    retriever = DenseRetriever(
        embedding_provider=provider,
        vector_store=RecordingVectorStore(index_dim=8),
        config=_config(),
    )

    with pytest.raises(RetrievalError) as exc_info:
        await retriever.retrieve(
            request=RetrievalRequest(
                query="secret full query text",
                request_id="req-1",
                trace_id="trace-1",
            ),
            filters=build_retrieval_filter_set(
                auth=_auth(),
                request=RetrievalRequest(
                    query="secret full query text",
                    request_id="req-1",
                    trace_id="trace-1",
                ),
            ),
        )

    assert exc_info.value.code == RETRIEVAL_EMBEDDING_FAILED
    assert exc_info.value.details["error_code"] == RETRIEVAL_EMBEDDING_FAILED
    assert raw_message not in str(exc_info.value.details).lower()
    assert "secret full query text" not in str(exc_info.value.details)
    assert "api_key" not in str(exc_info.value.details).lower()
    assert "C:\\" not in str(exc_info.value.details)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider_kind",
    [
        "batch_mismatch",
        "dimension_mismatch",
        "empty_vector",
        "multi_vector",
        "wrong_index",
        "nan_vector",
        "infinite_vector",
    ],
)
async def test_dense_retriever_rejects_invalid_embedding_response(
    provider_kind: str,
) -> None:
    provider: Any
    if provider_kind in {"batch_mismatch", "dimension_mismatch"}:
        provider = FakeEmbeddingProvider(failure_mode=provider_kind)  # type: ignore[arg-type]
    elif provider_kind == "empty_vector":
        provider = EmptyVectorEmbeddingProvider()
    elif provider_kind == "wrong_index":
        provider = CustomEmbeddingProvider(
            vectors=[EmbeddingVector(index=1, vector=[0.1] * 8)]
        )
    elif provider_kind == "nan_vector":
        provider = CustomEmbeddingProvider(
            vectors=[EmbeddingVector(index=0, vector=[nan] + [0.1] * 7)]
        )
    elif provider_kind == "infinite_vector":
        provider = CustomEmbeddingProvider(
            vectors=[EmbeddingVector(index=0, vector=[inf] + [0.1] * 7)]
        )
    else:
        provider = MultiVectorEmbeddingProvider()
    retriever = DenseRetriever(
        embedding_provider=provider,
        vector_store=RecordingVectorStore(index_dim=8),
        config=_config(),
    )
    request = RetrievalRequest(
        query="secret full query text",
        request_id="req-1",
        trace_id="trace-1",
    )

    with pytest.raises(RetrievalError) as exc_info:
        await retriever.retrieve(
            request=request,
            filters=build_retrieval_filter_set(auth=_auth(), request=request),
        )

    assert exc_info.value.code == RETRIEVAL_EMBEDDING_FAILED
    assert exc_info.value.details["embedding_dim"] == 8
    assert "secret full query text" not in str(exc_info.value.details)
    assert "[" not in str(exc_info.value.details)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider", "model", "version", "config_version"),
    [
        ("other-provider", "fake-embedding", "fake-v1", "fake-v1"),
        ("fake", "other-model", "fake-v1", "fake-v1"),
        ("fake", "fake-embedding", "other-version", "fake-v1"),
        ("fake", "fake-embedding", None, None),
    ],
)
async def test_dense_retriever_rejects_embedding_model_scope_mismatch(
    provider: str,
    model: str,
    version: str | None,
    config_version: str | None,
) -> None:
    embedding_provider = CustomEmbeddingProvider(
        provider=provider,
        model=model,
        version=version,
        config_version=config_version,
    )
    retriever = DenseRetriever(
        embedding_provider=embedding_provider,
        vector_store=RecordingVectorStore(index_dim=8),
        config=DenseRetrieverConfig(
            embedding_provider="fake",
            embedding_model="fake-embedding",
            embedding_version=embedding_provider.config_version,
            timeout_seconds=1.5,
            retry_budget=2,
        ),
    )
    request = RetrievalRequest(
        query="secret full query text",
        request_id="req-1",
        trace_id="trace-1",
    )

    with pytest.raises(RetrievalError) as exc_info:
        await retriever.retrieve(
            request=request,
            filters=build_retrieval_filter_set(auth=_auth(), request=request),
        )

    assert exc_info.value.code == RETRIEVAL_EMBEDDING_FAILED
    assert exc_info.value.details["error_code"] == RETRIEVAL_EMBEDDING_FAILED
    assert "secret full query text" not in str(exc_info.value.details)


@pytest.mark.asyncio
async def test_dense_retriever_redacts_sensitive_candidate_source_and_metadata() -> None:
    retriever = DenseRetriever(
        embedding_provider=FakeEmbeddingProvider(dim=4),
        vector_store=LeakyVectorStore(),
        config=_config(),
    )
    request = RetrievalRequest(query="policy", request_id="req-1", trace_id="trace-1")

    candidates = await retriever.retrieve(
        request=request,
        filters=build_retrieval_filter_set(auth=_auth(), request=request),
    )

    dumped = candidates[0].model_dump()
    assert dumped["source"] == "[REDACTED]"
    assert dumped["source_uri"] == "[REDACTED]"
    assert dumped["metadata"]["api_key"] == "[REDACTED]"
    assert dumped["metadata"]["chunk_text"] == "[REDACTED]"
    assert dumped["metadata"]["vector"] == "[REDACTED]"
    assert dumped["metadata"]["nested"]["local_path"] == "[REDACTED]"
    assert "sk-secret-key" not in str(dumped)
    assert "full chunk content" not in str(dumped)
    assert "C:\\secret" not in str(dumped)


@pytest.mark.asyncio
async def test_dense_retriever_maps_vector_store_failure_to_safe_retrieval_error() -> None:
    retriever = DenseRetriever(
        embedding_provider=FakeEmbeddingProvider(dim=4),
        vector_store=FailingVectorStore(),
        config=_config(),
    )
    request = RetrievalRequest(
        query="secret full query text",
        request_id="req-1",
        trace_id="trace-1",
    )

    with pytest.raises(RetrievalError) as exc_info:
        await retriever.retrieve(
            request=request,
            filters=build_retrieval_filter_set(auth=_auth(), request=request),
        )

    assert exc_info.value.code == RETRIEVAL_VECTOR_SEARCH_FAILED
    assert exc_info.value.details == {
        "request_id": "req-1",
        "trace_id": "trace-1",
        "tenant_id": "tenant-a",
        "user_id": "user-1",
        "top_k": 10,
        "embedding_provider": "fake",
        "embedding_model": "fake-embedding",
        "embedding_version": "fake-v1",
        "embedding_dim": 4,
        "error_code": RETRIEVAL_VECTOR_SEARCH_FAILED,
    }
    assert "secret full query text" not in str(exc_info.value.details)
    assert "password" not in str(exc_info.value.details).lower()
    assert "C:\\" not in str(exc_info.value.details)


def test_dense_retriever_config_validates_required_fields() -> None:
    with pytest.raises(ValueError):
        DenseRetrieverConfig(
            embedding_provider=" ",
            embedding_model="fake-embedding",
            timeout_seconds=1.0,
            retry_budget=1,
        )

    with pytest.raises(ValueError):
        DenseRetrieverConfig(
            embedding_provider="fake",
            embedding_model="fake-embedding",
            timeout_seconds=0.0,
            retry_budget=1,
        )

    with pytest.raises(ValueError):
        DenseRetrieverConfig(
            embedding_provider="fake",
            embedding_model="fake-embedding",
            timeout_seconds=1.0,
            retry_budget=-1,
        )


class RecordingEmbeddingProvider(FakeEmbeddingProvider):
    def __init__(self, *, dim: int = 4) -> None:
        super().__init__(dim=dim, provider="fake", model="fake-embedding", version="fake-v1")
        self.last_request: EmbeddingRequest | None = None

    async def embed_texts(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.last_request = request
        return await super().embed_texts(request)


class EmptyVectorEmbeddingProvider:
    async def embed_texts(self, request: EmbeddingRequest) -> EmbeddingResponse:
        return EmbeddingResponse(
            vectors=[EmbeddingVector(index=0, vector=[])],
            provider=request.provider,
            model=request.model,
            version="fake-v1",
            dim=8,
            latency_ms=0,
        )


class MultiVectorEmbeddingProvider:
    async def embed_texts(self, request: EmbeddingRequest) -> EmbeddingResponse:
        return EmbeddingResponse(
            vectors=[
                EmbeddingVector(index=0, vector=[0.1] * 8),
                EmbeddingVector(index=1, vector=[0.2] * 8),
            ],
            provider=request.provider,
            model=request.model,
            version="fake-v1",
            dim=8,
            latency_ms=0,
        )


class CustomEmbeddingProvider:
    def __init__(
        self,
        *,
        vectors: list[EmbeddingVector] | None = None,
        provider: str = "fake",
        model: str = "fake-embedding",
        version: str | None = "fake-v1",
        config_version: str | None = "fake-v1",
    ) -> None:
        self._vectors = vectors or [EmbeddingVector(index=0, vector=[0.1] * 8)]
        self._provider = provider
        self._model = model
        self._version = version
        self.config_version = config_version

    async def embed_texts(self, request: EmbeddingRequest) -> EmbeddingResponse:
        return EmbeddingResponse(
            vectors=self._vectors,
            provider=self._provider,
            model=self._model,
            version=self._version,
            dim=8,
            latency_ms=0,
        )


class RecordingVectorStore(FakeVectorStore):
    def __init__(self, *, index_dim: int) -> None:
        super().__init__(index_dim=index_dim)
        self.last_request: VectorSearchRequest | None = None

    async def search(self, request: VectorSearchRequest) -> list[VectorSearchResult]:
        self.last_request = request
        return await super().search(request)


class LeakyVectorStore:
    async def upsert(self, vectors: list[VectorRecord]) -> VectorUpsertResult:
        raise AssertionError("unused")

    async def search(self, request: VectorSearchRequest) -> list[VectorSearchResult]:
        return [
            VectorSearchResult(
                document_id="doc-leaky",
                version_id="ver-leaky",
                chunk_id="chunk-leaky",
                source="C:\\secret\\policy.md",
                source_type="markdown",
                source_uri="C:\\secret\\policy.md",
                page_start=1,
                page_end=1,
                title_path=["Policy"],
                score=0.9,
                retrieval_method="dense",
                tenant_id=request.tenant_id,
                acl={"visibility": "tenant", "api_key": "sk-secret-key"},
                metadata={
                    "department": "people",
                    "api_key": "sk-secret-key",
                    "chunk_text": "full chunk content",
                    "vector": request.query_vector,
                    "nested": {"local_path": "C:\\secret\\source.txt"},
                },
            )
        ]

    async def delete_by_document(
        self,
        document_id: str,
        version_id: str | None = None,
        *,
        tenant_id: str,
    ) -> VectorDeleteResult:
        raise AssertionError("unused")


class FailingVectorStore:
    async def upsert(self, vectors: list[VectorRecord]) -> VectorUpsertResult:
        raise AssertionError("unused")

    async def search(self, request: VectorSearchRequest) -> list[VectorSearchResult]:
        raise VectorStoreError(
            code=VECTOR_STORE_SEARCH_FAILED,
            message="raw password at C:\\secret\\vector.sql",
            retryable=True,
            details={"api_key": "secret-key", "query_vector": request.query_vector},
        )

    async def delete_by_document(
        self,
        document_id: str,
        version_id: str | None = None,
        *,
        tenant_id: str,
    ) -> VectorDeleteResult:
        raise AssertionError("unused")


def _auth() -> AuthContext:
    return AuthContext(
        user_id="user-1",
        tenant_id="tenant-a",
        roles=("hr",),
        department="people",
        permissions=("document:read",),
    )


def _config() -> DenseRetrieverConfig:
    return DenseRetrieverConfig(
        embedding_provider="fake",
        embedding_model="fake-embedding",
        embedding_version="fake-v1",
        timeout_seconds=1.5,
        retry_budget=2,
        distance_metric="cosine",
    )


def _record(
    *,
    chunk_id: str,
    vector: list[float],
    tenant_id: str = "tenant-a",
    department: str = "people",
    embedding_provider: str = "fake",
    embedding_model: str = "fake-embedding",
    embedding_version: str | None = "fake-v1",
    acl: dict[str, object] | None = None,
) -> VectorRecord:
    return VectorRecord(
        tenant_id=tenant_id,
        document_id=f"doc-{chunk_id}",
        version_id=f"ver-{chunk_id}",
        chunk_id=chunk_id,
        created_by="user-1",
        status="active",
        vector=vector,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        embedding_version=embedding_version,
        embedding_dim=len(vector),
        source_type="markdown",
        source_uri=f"kb://{chunk_id}.md",
        title_path=["Policy", chunk_id],
        page_start=1,
        page_end=2,
        token_count=12,
        acl=acl or {"visibility": "tenant", "allowed_roles": ["hr"]},
        checksum=f"checksum-{chunk_id}",
        metadata={"department": department},
    )

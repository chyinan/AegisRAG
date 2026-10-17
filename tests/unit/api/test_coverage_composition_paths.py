"""Exercise resource ownership and optional retrieval composition without network."""

from __future__ import annotations

from typing import cast

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.factories.retrieval import _create_reranker, create_retrieval_service
from packages.common.config import AppSettings
from packages.common.runtime_resources import resource_scope
from packages.common.tenant_context import current_tenant_id, tenant_scope
from packages.embeddings.adapters.fake import FakeEmbeddingProvider
from packages.rag.storage.session_scoped import _read_tenant
from packages.retrieval.query_router import RoutingRetriever
from packages.retrieval.rerank import FakeReranker, RerankingRetriever
from packages.retrieval.rerank.adapters.bge_local import BGELocalReranker
from packages.retrieval.rerank.adapters.openai_compatible import OpenAICompatibleReranker
from packages.vectorstores.adapters.fake import FakeVectorStore


async def test_bge_without_lifespan_gets_operation_owned_reranker() -> None:
    settings = AppSettings(RERANK_PROVIDER="bge_local", BGE_MAX_CONCURRENCY=2, BGE_MAX_QUEUE_SIZE=3)
    with resource_scope(None):
        first = _create_reranker(settings=settings)
        second = _create_reranker(settings=settings)
    assert isinstance(first, BGELocalReranker)
    assert isinstance(second, BGELocalReranker)
    try:
        assert first is not second
    finally:
        await first.aclose()
        await second.aclose()


@pytest.mark.parametrize("key", [None, "synthetic-api-key"])
def test_external_reranker_preserves_configured_secret_and_provider(key: str | None) -> None:
    reranker = _create_reranker(
        settings=AppSettings(
            RERANK_PROVIDER="openai_compatible",
            RERANK_BASE_URL="https://unused.invalid/v1/",
            RERANK_API_KEY=SecretStr(key) if key is not None else None,
            RERANK_MODEL="synthetic-rerank",
        )
    )
    assert isinstance(reranker, OpenAICompatibleReranker)
    assert reranker._api_key == key
    assert reranker._base_url == "https://unused.invalid/v1"
    assert reranker._model == "synthetic-rerank"


def test_missing_external_rerank_endpoint_uses_fake_fallback() -> None:
    assert isinstance(
        _create_reranker(
            settings=AppSettings(
                RERANK_PROVIDER="openai_compatible",
                RERANK_BASE_URL=None,
            )
        ),
        FakeReranker,
    )


def test_adaptive_factory_preserves_shared_hybrid_and_uncached_router() -> None:
    service, trace, cache = create_retrieval_service(
        settings=AppSettings(ADAPTIVE_ROUTING_ENABLED=True, QUERY_REWRITE_ENABLED=False),
        session=cast(AsyncSession, object()),
        vector_store=FakeVectorStore(index_dim=8),
        embedding_provider=FakeEmbeddingProvider(dim=8),
    )
    router = service._retriever
    assert isinstance(router, RoutingRetriever)
    assert cache is None
    assert isinstance(router._complex_retriever, RerankingRetriever)
    assert router._complex_retriever._upstream_retriever is router._comparison_retriever
    assert router._factual_retriever is not router._comparison_retriever
    assert isinstance(trace(), dict)


def test_cross_tenant_read_is_rejected_without_changing_authenticated_scope() -> None:
    with tenant_scope("authenticated"):
        with pytest.raises(ValueError, match="Read tenant differs"):
            with _read_tenant("other"):
                pytest.fail("cross-tenant read entered")
        assert current_tenant_id() == "authenticated"


async def test_api_lifespan_closes_revocation_store_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from apps.api import main

    closed: list[str] = []

    class Store:
        async def aclose(self) -> None:
            closed.append("revocation")

    monkeypatch.setattr(main, "get_token_revocation_store", lambda: Store())
    app = main.create_app()
    async with app.router.lifespan_context(app):
        assert not closed
    assert closed == ["revocation"]


@pytest.mark.parametrize("tenant", [None, "", 123])
async def test_sparse_read_rejects_missing_authenticated_tenant_before_opening_session(
    tenant: object,
) -> None:
    from packages.rag.storage.session_scoped import SessionFactorySparseSearchBackend
    from packages.retrieval.dto import RetrievalRequest
    from packages.retrieval.sparse import SparseRetrieverConfig

    def forbidden_session() -> None:
        pytest.fail("unauthenticated sparse read opened a database session")

    backend = SessionFactorySparseSearchBackend(
        cast(async_sessionmaker[AsyncSession], forbidden_session)
    )
    with pytest.raises(ValueError, match="Authenticated tenant is required"):
        await backend.search(
            request=RetrievalRequest(query="policy", request_id="request", trace_id="trace"),
            filters_payload={"tenant_id": tenant},
            query_terms=("policy",),
            config=SparseRetrieverConfig(),
        )

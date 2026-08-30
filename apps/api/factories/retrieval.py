"""Retrieval pipeline factory with caching and real reranker support.

Extracted from service_dependencies.py as part of DI decoupling (T1 finding).
Adds: circuit breaker, real reranker, retrieval cache (T2 Phase 1 P0).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.circuit_breaker import CircuitBreaker
from packages.common.config import AppSettings
from packages.data.storage.exceptions import StorageConfigurationError
from packages.embeddings.ports import EmbeddingProvider
from packages.llm.dto import GenerateRequest, LLMMessage
from packages.llm.ports import LLMProvider
from packages.retrieval.dense import DenseRetriever, DenseRetrieverConfig
from packages.retrieval.query_rewriter import (
    HyDEQueryRewriter,
    QueryRewriteConfig,
    QueryRewriter,
    QueryRewritingRetriever,
)
from packages.retrieval.query_router import (
    QueryRouteConfig,
    QueryRouter,
    QueryRouterConfig,
    QueryType,
    RoutingRetriever,
)
from packages.retrieval.rerank import FakeReranker, RerankConfig, RerankingRetriever
from packages.retrieval.rerank.adapters.bge_local import BGELocalReranker
from packages.retrieval.rerank.adapters.llm_reranker import LLMReranker
from packages.retrieval.rerank.adapters.openai_compatible import OpenAICompatibleReranker
from packages.retrieval.rerank.cache import CachedRetriever, RetrievalCache
from packages.retrieval.rrf import HybridMergeConfig, HybridRetriever, RRFMerger
from packages.retrieval.service import RetrievalService
from packages.retrieval.sparse import PostgresSparseRetriever, SparseRetrieverConfig
from packages.vectorstores.dto import DistanceMetric
from packages.vectorstores.ports import VectorStore


class RetrievalCacheRegistry:
    def __init__(self) -> None:
        self._cache: RetrievalCache | None = None

    def get_or_create(
        self,
        *,
        redis_url: str | None = None,
        max_size: int = 1024,
        ttl_seconds: float = 300.0,
    ) -> RetrievalCache:
        if self._cache is None:
            self._cache = RetrievalCache(
                max_size=max_size,
                ttl_seconds=ttl_seconds,
                redis_url=redis_url,
            )
        return self._cache


def create_retrieval_cache(
    *,
    redis_url: str | None = None,
    max_size: int = 1024,
    ttl_seconds: float = 300.0,
) -> RetrievalCache:
    return RetrievalCache(
        max_size=max_size,
        ttl_seconds=ttl_seconds,
        redis_url=redis_url,
    )


def create_retrieval_service(
    *,
    settings: AppSettings,
    session: AsyncSession,
    vector_store: VectorStore,
    embedding_provider: EmbeddingProvider,
    llm_provider: LLMProvider | None = None,
    circuit_breaker: CircuitBreaker | None = None,
    retrieval_cache: RetrievalCache | None = None,
    query_rewriter: QueryRewriter | None = None,
) -> tuple[RetrievalService, Callable[[], Mapping[str, object]], RetrievalCache | None]:
    if settings.adaptive_routing_enabled:
        return create_adaptive_retrieval_service(
            settings=settings,
            session=session,
            vector_store=vector_store,
            embedding_provider=embedding_provider,
            llm_provider=llm_provider,
            circuit_breaker=circuit_breaker,
            retrieval_cache=retrieval_cache,
        )

    dense_retriever = DenseRetriever(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        config=DenseRetrieverConfig(
            embedding_provider=settings.embedding_provider,
            embedding_model=settings.embedding_model,
            embedding_version=(
                "fake-v1"
                if settings.embedding_provider == "fake"
                else settings.embedding_provider_version
            ),
            timeout_seconds=settings.embedding_timeout_seconds,
            retry_budget=settings.embedding_retry_budget,
            distance_metric=_distance_metric_from_settings(
                settings.vector_distance_metric
            ),
        ),
    )
    sparse_retriever = PostgresSparseRetriever(
        session=session,
        config=SparseRetrieverConfig(),
    )
    merger = RRFMerger(config=HybridMergeConfig())
    hybrid_retriever = HybridRetriever(
        dense_retriever=dense_retriever,
        sparse_retriever=sparse_retriever,
        merger=merger,
        config=HybridMergeConfig(),
        allow_degraded=settings.hybrid_degraded_mode,
        parallel_branches=settings.hybrid_parallel_enabled,
    )

    # ----- Query Rewriting (P1) -----
    # 插入可选的查询改写步骤，位于 HybridRetriever 之前。
    # 如果未显式传入 query_rewriter，则根据配置自动创建。
    if query_rewriter is None and settings.query_rewrite_enabled and llm_provider is not None:
        rewrite_config = QueryRewriteConfig(
            enabled=True,
            hyde_model=settings.query_rewrite_model or settings.llm_model,
        )
        query_rewriter = HyDEQueryRewriter(
            llm_provider=llm_provider,
            config=rewrite_config,
        )

    upstream_retriever_for_rerank = hybrid_retriever
    if query_rewriter is not None:
        upstream_retriever_for_rerank = QueryRewritingRetriever(
            query_rewriter=query_rewriter,
            upstream_retriever=hybrid_retriever,
        )

    reranker = _create_reranker(
        settings=settings,
        circuit_breaker=circuit_breaker,
        llm_provider=llm_provider,
    )
    reranking_retriever = RerankingRetriever(
        upstream_retriever=upstream_retriever_for_rerank,
        reranker=reranker,
        config=_rerank_config_from_settings(settings),
    )

    final_retriever = reranking_retriever
    if retrieval_cache is not None:
        final_retriever = CachedRetriever(
            upstream=reranking_retriever,
            cache=retrieval_cache,
        )

    return (
        RetrievalService(retriever=final_retriever),
        lambda: _retrieval_pipeline_trace(
            merger=merger,
            hybrid_retriever=hybrid_retriever,
            reranking_retriever=reranking_retriever,
        ),
        retrieval_cache,
    )


def _create_reranker(
    *,
    settings: AppSettings,
    circuit_breaker: CircuitBreaker | None = None,
    llm_provider: LLMProvider | None = None,
):
    rerank_provider = getattr(settings, "rerank_provider", "fake")
    if rerank_provider == "fake":
        return FakeReranker()
    if rerank_provider == "bge_local":
        rerank_model = getattr(settings, "rerank_model", "BAAI/bge-reranker-v2-m3")
        return BGELocalReranker(
            model_name=rerank_model,
            provider="bge_local",
            circuit_breaker=circuit_breaker,
        )
    if rerank_provider == "llm" and llm_provider is not None:
        rerank_model = getattr(settings, "rerank_model", "deepseek-v4-flash")
        return LLMReranker(
            llm_provider=llm_provider,
            model=rerank_model,
            provider="llm",
            circuit_breaker=circuit_breaker,
        )

    rerank_base_url = getattr(settings, "rerank_base_url", None)
    rerank_api_key = getattr(settings, "rerank_api_key", None)
    rerank_model = getattr(settings, "rerank_model", "bge-reranker-v2-m3")

    if rerank_base_url is None:
        return FakeReranker()

    api_key = (
        rerank_api_key.get_secret_value()
        if hasattr(rerank_api_key, "get_secret_value")
        else (str(rerank_api_key) if rerank_api_key else None)
    )
    return OpenAICompatibleReranker(
        base_url=rerank_base_url,
        api_key=api_key,
        model=rerank_model,
        provider=rerank_provider,
        circuit_breaker=circuit_breaker,
    )


def _rerank_config_from_settings(settings: AppSettings) -> RerankConfig:
    rerank_provider = getattr(settings, "rerank_provider", "fake")
    rerank_model = getattr(settings, "rerank_model", "fake-reranker-v1")
    return RerankConfig(
        provider=rerank_provider,
        model=rerank_model,
        timeout_seconds=settings.rerank_timeout_seconds,
    )


def _distance_metric_from_settings(value: str) -> DistanceMetric:
    if value == "cosine":
        return "cosine"
    if value == "l2":
        return "l2"
    raise StorageConfigurationError(
        details={
            "distance_metric": value,
            "supported_distance_metrics": ["cosine", "l2"],
        }
    )


def _retrieval_pipeline_trace(
    *,
    merger: RRFMerger,
    hybrid_retriever: HybridRetriever,
    reranking_retriever: RerankingRetriever,
    routing_retriever: RoutingRetriever | None = None,
) -> dict[str, object]:
    rrf_trace = merger.last_trace
    rerank_trace = reranking_retriever.last_trace
    rrf: dict[str, object] = {}
    if rrf_trace is not None:
        rrf = {
            "input_counts": dict(rrf_trace.input_counts),
            "deduped_count": rrf_trace.deduped_count,
            "filtered_count": rrf_trace.filtered_count,
            "threshold": rrf_trace.threshold,
            "rank_constant": rrf_trace.rank_constant,
            "weights": dict(rrf_trace.weights),
        }
    rerank: dict[str, object] = {"status": "not_available", "candidate_count": 0}
    if rerank_trace is not None:
        if rerank_trace.error_code:
            status = "failed"
        elif rerank_trace.degraded:
            status = "degraded"
        else:
            status = "success"
        rerank = {
            "status": status,
            "provider": rerank_trace.provider,
            "model": rerank_trace.model,
            "latency_ms": rerank_trace.latency_ms,
            "input_count": rerank_trace.input_count,
            "output_count": rerank_trace.output_count,
            "candidate_count": rerank_trace.output_count,
            "safe_counts": dict(rerank_trace.safe_counts),
            "error_code": rerank_trace.error_code,
        }
    selected_route = routing_retriever.last_query_type if routing_retriever is not None else None
    if selected_route in {"factual", "comparison"}:
        rerank = {"status": "not_executed", "candidate_count": 0}
    if selected_route == "factual":
        rrf = {}
    hybrid_branches: object = hybrid_retriever.last_branch_trace
    if selected_route == "factual":
        hybrid_branches = {"status": "not_executed", "reason": "factual_fast_path"}
    return {
        "rrf": rrf,
        "selected_route": selected_route,
        "hybrid_branches": hybrid_branches,
        "rerank": rerank,
    }


# ── Adaptive Retrieval Routing (P3) ──────────────────────────────────────────


def _build_router_config_from_settings(settings: AppSettings) -> QueryRouterConfig:
    """Build QueryRouterConfig from AppSettings."""
    return QueryRouterConfig(
        factual=QueryRouteConfig(
            top_k=settings.adaptive_routing_factual_top_k,
            score_threshold=settings.adaptive_routing_factual_score_threshold,
            skip_rerank=True,
        ),
        complex=QueryRouteConfig(
            top_k=settings.adaptive_routing_complex_top_k,
            score_threshold=settings.adaptive_routing_complex_score_threshold,
            skip_rerank=False,
        ),
        comparison=QueryRouteConfig(
            top_k=settings.adaptive_routing_comparison_top_k,
            score_threshold=settings.adaptive_routing_comparison_score_threshold,
            skip_rerank=False,
        ),
        llm_fallback_enabled=settings.adaptive_routing_llm_fallback,
        confidence_threshold=settings.adaptive_routing_confidence_threshold,
    )


def _build_dense_retriever(
    *,
    settings: AppSettings,
    embedding_provider: EmbeddingProvider,
    vector_store: VectorStore,
) -> DenseRetriever:
    """Build a DenseRetriever from settings (shared across all routing paths)."""
    return DenseRetriever(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        config=DenseRetrieverConfig(
            embedding_provider=settings.embedding_provider,
            embedding_model=settings.embedding_model,
            embedding_version=(
                "fake-v1"
                if settings.embedding_provider == "fake"
                else settings.embedding_provider_version
            ),
            timeout_seconds=settings.embedding_timeout_seconds,
            retry_budget=settings.embedding_retry_budget,
            distance_metric=_distance_metric_from_settings(
                settings.vector_distance_metric
            ),
        ),
    )


class _LLMQueryClassifier:
    def __init__(self, *, llm_provider: LLMProvider, settings: AppSettings) -> None:
        self._llm = llm_provider
        self._settings = settings

    async def classify_query(
        self,
        *,
        query: str,
        request_id: str = "adaptive-router",
        trace_id: str = "adaptive-router",
        tenant_id: str = "system",
        user_id: str = "system",
    ) -> QueryType:
        response = await self._llm.generate(
            GenerateRequest(
                messages=(
                    LLMMessage(
                        role="system",
                        content=(
                            "Classify the user query as exactly one label: factual, "
                            "complex, or comparison. Return only the label."
                        ),
                    ),
                    LLMMessage(role="user", content=query),
                ),
                provider=self._settings.llm_provider,
                model=self._settings.query_rewrite_model or self._settings.llm_model,
                timeout_seconds=self._settings.llm_timeout_seconds,
                retry_budget=self._settings.llm_retry_budget,
                request_id=request_id,
                trace_id=trace_id,
                tenant_id=tenant_id,
                user_id=user_id,
                temperature=0.0,
                max_output_tokens=8,
            )
        )
        label = response.text.strip().lower().split()[0] if response.text.strip() else ""
        if label not in {"factual", "complex", "comparison"}:
            raise ValueError("LLM returned an invalid query route")
        return cast(QueryType, label)


def create_adaptive_retrieval_service(
    *,
    settings: AppSettings,
    session: AsyncSession,
    vector_store: VectorStore,
    embedding_provider: EmbeddingProvider,
    llm_provider: LLMProvider | None = None,
    circuit_breaker: CircuitBreaker | None = None,
    retrieval_cache: RetrievalCache | None = None,
) -> tuple[RetrievalService, Callable[[], Mapping[str, object]], RetrievalCache | None]:
    """Create a RetrievalService with adaptive query routing.

    Builds three retrieval paths:
      - factual:    dense-only fast path (no rerank, no sparse, no rewrite)
      - complex:    hybrid + optional rewrite + rerank (full pipeline)
      - comparison: hybrid + optional rewrite, high top-k, no-rerank fallback

    The QueryRouter classifies queries by keyword patterns (with optional
    LLM fallback) and the RoutingRetriever delegates to the appropriate path.
    """
    router_config = _build_router_config_from_settings(settings)

    # ── Shared components ──
    dense_retriever = _build_dense_retriever(
        settings=settings,
        embedding_provider=embedding_provider,
        vector_store=vector_store,
    )
    sparse_retriever = PostgresSparseRetriever(
        session=session,
        config=SparseRetrieverConfig(),
    )
    merger = RRFMerger(config=HybridMergeConfig())
    hybrid_retriever = HybridRetriever(
        dense_retriever=dense_retriever,
        sparse_retriever=sparse_retriever,
        merger=merger,
        config=HybridMergeConfig(),
        allow_degraded=settings.hybrid_degraded_mode,
        parallel_branches=settings.hybrid_parallel_enabled,
    )

    # ── Path 1: Factual — dense-only fast path (no rerank, no rewrite) ──
    factual_retriever = dense_retriever

    # ── Path 2: Complex — full pipeline (hybrid + rewrite + rerank) ──
    complex_upstream = hybrid_retriever
    if settings.query_rewrite_enabled and llm_provider is not None:
        rewrite_config = QueryRewriteConfig(
            enabled=True,
            hyde_model=settings.query_rewrite_model or settings.llm_model,
        )
        query_rewriter = HyDEQueryRewriter(
            llm_provider=llm_provider,
            config=rewrite_config,
        )
        complex_upstream = QueryRewritingRetriever(
            query_rewriter=query_rewriter,
            upstream_retriever=hybrid_retriever,
        )

    reranker = _create_reranker(
        settings=settings,
        circuit_breaker=circuit_breaker,
        llm_provider=llm_provider,
    )
    complex_retriever = RerankingRetriever(
        upstream_retriever=complex_upstream,
        reranker=reranker,
        config=_rerank_config_from_settings(settings),
    )

    # ── Path 3: Comparison — hybrid high-recall, no rerank ──
    comparison_upstream = hybrid_retriever
    if settings.query_rewrite_enabled and llm_provider is not None:
        rewrite_config = QueryRewriteConfig(
            enabled=True,
            hyde_model=settings.query_rewrite_model or settings.llm_model,
        )
        comparison_rewriter = HyDEQueryRewriter(
            llm_provider=llm_provider,
            config=rewrite_config,
        )
        comparison_upstream = QueryRewritingRetriever(
            query_rewriter=comparison_rewriter,
            upstream_retriever=hybrid_retriever,
        )
    comparison_retriever = comparison_upstream

    # ── Query Router ──
    router = QueryRouter(
        config=router_config,
        llm_classifier=(
            _LLMQueryClassifier(llm_provider=llm_provider, settings=settings)
            if settings.adaptive_routing_llm_fallback and llm_provider is not None
            else None
        ),
    )


    routing_retriever = RoutingRetriever(
        router=router,
        factual_retriever=factual_retriever,
        complex_retriever=complex_retriever,
        comparison_retriever=comparison_retriever,
    )

    # ── Optional caching layer ──
    final_retriever = routing_retriever
    if retrieval_cache is not None:
        final_retriever = CachedRetriever(
            upstream=routing_retriever,
            cache=retrieval_cache,
        )

    return (
        RetrievalService(retriever=final_retriever),
        lambda: _retrieval_pipeline_trace(
            merger=merger,
            hybrid_retriever=hybrid_retriever,
            reranking_retriever=complex_retriever,
            routing_retriever=routing_retriever,
        ),
        retrieval_cache,
    )

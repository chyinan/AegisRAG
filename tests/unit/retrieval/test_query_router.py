"""Unit tests for Adaptive Retrieval Routing (P3).

Covers: QueryRouter classification, QueryRouteConfig, RoutingRetriever,
configuration wiring, and the adaptive retrieval factory.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from packages.retrieval.dto import RetrievalCandidate, RetrievalFilterSet, RetrievalRequest
from packages.retrieval.ports import CandidateRetriever
from packages.retrieval.query_router import (
    QueryRouteConfig,
    QueryRouter,
    QueryRouterConfig,
    RoutingRetriever,
)

# ── Test helpers ────────────────────────────────────────────────────────────


def _make_request(
    query: str,
    top_k: int = 10,
    score_threshold: float | None = None,
) -> RetrievalRequest:
    return RetrievalRequest(
        query=query,
        top_k=top_k,
        score_threshold=score_threshold,
        request_id="req-test-1",
        trace_id="trace-test-1",
    )


def _make_filters() -> RetrievalFilterSet:
    return RetrievalFilterSet(
        tenant_id="tenant-1",
        user_id="user-1",
    )


def _make_candidate(chunk_id: str, score: float = 0.85) -> RetrievalCandidate:
    return RetrievalCandidate(
        document_id="doc-1",
        version_id="v1",
        chunk_id=chunk_id,
        source_type="pdf",
        title_path=("Test Doc",),
        score=score,
        retrieval_method="dense",
        tenant_id="tenant-1",
    )


# ── QueryRouteConfig tests ──────────────────────────────────────────────────


class TestQueryRouteConfig:
    def test_default_values(self) -> None:
        config = QueryRouteConfig()
        assert config.top_k == 10
        assert config.score_threshold is None
        assert config.skip_rerank is False

    def test_custom_values(self) -> None:
        config = QueryRouteConfig(top_k=20, score_threshold=0.5, skip_rerank=True)
        assert config.top_k == 20
        assert config.score_threshold == 0.5
        assert config.skip_rerank is True

    def test_top_k_out_of_range_raises(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            QueryRouteConfig(top_k=0)
        with pytest.raises(Exception):  # noqa: B017
            QueryRouteConfig(top_k=101)

    def test_score_threshold_out_of_range_raises(self) -> None:
        with pytest.raises(Exception):  # noqa: B017
            QueryRouteConfig(score_threshold=-0.1)
        with pytest.raises(Exception):  # noqa: B017
            QueryRouteConfig(score_threshold=1.1)

    def test_immutable(self) -> None:
        config = QueryRouteConfig(top_k=15)
        with pytest.raises(Exception):  # noqa: B017
            config.top_k = 20  # type: ignore[misc]


# ── QueryRouterConfig tests ─────────────────────────────────────────────────


class TestQueryRouterConfig:
    def test_default_per_type_configs(self) -> None:
        config = QueryRouterConfig()
        assert config.factual.top_k == 5
        assert config.factual.skip_rerank is True
        assert config.complex.top_k == 10
        assert config.complex.skip_rerank is False
        assert config.comparison.top_k == 20
        assert config.comparison.score_threshold is None

    def test_get_route_returns_correct_config(self) -> None:
        config = QueryRouterConfig(
            factual=QueryRouteConfig(top_k=3, skip_rerank=True),
            complex=QueryRouteConfig(top_k=12, score_threshold=0.4),
            comparison=QueryRouteConfig(top_k=25),
        )
        assert config.get_route("factual").top_k == 3
        assert config.get_route("complex").top_k == 12
        assert config.get_route("comparison").top_k == 25

    def test_llm_fallback_defaults_disabled(self) -> None:
        config = QueryRouterConfig()
        assert config.llm_fallback_enabled is False

    def test_confidence_threshold_default(self) -> None:
        config = QueryRouterConfig()
        assert config.confidence_threshold == 0.6


# ── QueryRouter classification tests ────────────────────────────────────────


class TestQueryRouterClassification:
    @pytest.fixture
    def router(self) -> QueryRouter:
        return QueryRouter(config=QueryRouterConfig())

    # ── Factual queries ──

    def test_simple_factual_what(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify("What is machine learning?")
        assert qtype == "factual"
        assert confidence > 0.6

    def test_simple_factual_when(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify("When was Python created?")
        assert qtype == "factual"
        assert confidence > 0.6

    def test_simple_factual_where(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify("Where is the Eiffel Tower?")
        assert qtype == "factual"
        assert confidence > 0.6

    def test_simple_factual_who(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify("Who invented the telephone?")
        assert qtype == "factual"
        assert confidence > 0.6

    def test_how_many_factual(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify("How many planets are in the solar system?")
        assert qtype == "factual"

    def test_define_factual(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify("Define entropy")
        assert qtype == "factual"

    def test_short_query_factual(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify("Python")
        assert qtype == "factual"

    def test_empty_query_defaults_factual(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify("")
        assert qtype == "factual"

    # ── Complex queries ──

    def test_multi_step_first_then(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify(
            "First, how does photosynthesis work, and then how does it affect the carbon cycle?"
        )
        assert qtype == "complex"
        assert confidence >= 0.8

    def test_explain_how_complex(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify("Explain how quantum computing works")
        assert qtype == "complex"

    def test_explain_why_complex(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify("Explain why the sky is blue")
        assert qtype == "complex"

    def test_reasons_for_complex(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify("What are the reasons for climate change?")
        assert qtype == "complex"

    def test_relationship_between_complex(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify(
            "What is the relationship between inflation and unemployment?"
        )
        assert qtype == "complex"

    def test_multi_question_complex(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify("What is AI? How does it work?")
        assert qtype == "complex"

    def test_underlying_cause_complex(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify(
            "What is the underlying cause of the French Revolution?"
        )
        assert qtype == "complex"

    def test_affect_impact_complex(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify(
            "How does social media affect mental health?"
        )
        assert qtype == "complex"

    def test_long_query_complex(self, router: QueryRouter) -> None:
        long_query = (
            "I need a detailed explanation of how renewable energy technologies "
            "have evolved over the past two decades and what implications "
            "this has for global economic policy going forward into the next century"
        )
        qtype, confidence = router.classify(long_query)
        assert qtype == "complex"

    # ── Comparison queries ──

    def test_compare_keyword(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify("Compare Python and JavaScript")
        assert qtype == "comparison"
        assert confidence >= 0.85

    def test_versus_keyword(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify("Python versus JavaScript for web development")
        assert qtype == "comparison"

    def test_vs_keyword(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify("React vs Vue performance")
        assert qtype == "comparison"

    def test_difference_between(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify("What is the difference between SQL and NoSQL?")
        assert qtype == "comparison"

    def test_pros_and_cons(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify("What are the pros and cons of remote work?")
        assert qtype == "comparison"

    def test_advantages_disadvantages(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify(
            "Advantages and disadvantages of electric vehicles"
        )
        assert qtype == "comparison"

    def test_which_is_better(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify("Which is better: Mac or PC?")
        assert qtype == "comparison"

    def test_contrast_keyword(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify("Contrast capitalism and socialism")
        assert qtype == "comparison"

    def test_analyze_keyword(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify("Analyze the impact of AI on employment")
        assert qtype == "comparison"

    def test_tradeoffs_keyword(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify("What are the trade-offs of using microservices?")
        assert qtype == "comparison"

    def test_evaluate_keyword(self, router: QueryRouter) -> None:
        qtype, confidence = router.classify("Evaluate the effectiveness of agile methodology")
        assert qtype == "comparison"

    # ── Priority: comparison beats complex ──

    def test_comparison_beats_complex(self, router: QueryRouter) -> None:
        """Comparison patterns take priority over complex patterns."""
        qtype, confidence = router.classify(
            "Compare and explain how solar and wind energy work"
        )
        assert qtype == "comparison"


# ── QueryRouter configuration-driven tests ──────────────────────────────────


class TestQueryRouterConfiguration:
    def test_factual_route_config(self) -> None:
        config = QueryRouterConfig(
            factual=QueryRouteConfig(top_k=3, score_threshold=0.5, skip_rerank=True),
        )
        router = QueryRouter(config=config)
        route = router.get_route_config("What is Python?")
        assert route.top_k == 3
        assert route.score_threshold == 0.5
        assert route.skip_rerank is True

    def test_complex_route_config(self) -> None:
        config = QueryRouterConfig(
            complex=QueryRouteConfig(top_k=15, score_threshold=0.2),
        )
        router = QueryRouter(config=config)
        route = router.get_route_config(
            "Explain how quantum entanglement works and why it matters"
        )
        assert route.top_k == 15
        assert route.score_threshold == 0.2

    def test_comparison_route_config(self) -> None:
        config = QueryRouterConfig(
            comparison=QueryRouteConfig(top_k=25, score_threshold=None),
        )
        router = QueryRouter(config=config)
        route = router.get_route_config("Compare Python and Rust")
        assert route.top_k == 25
        assert route.score_threshold is None


# ── LLM fallback tests ──────────────────────────────────────────────────────


class TestQueryRouterLLMFallback:
    @pytest.fixture
    def mock_llm_classifier(self) -> AsyncMock:
        return AsyncMock()

    @pytest.fixture
    def router_config(self) -> QueryRouterConfig:
        return QueryRouterConfig(
            llm_fallback_enabled=True,
            confidence_threshold=0.5,
        )

    @pytest.mark.anyio
    async def test_llm_fallback_called_for_ambiguous(
        self, mock_llm_classifier: AsyncMock, router_config: QueryRouterConfig
    ) -> None:
        """LLM fallback triggered when keyword confidence is below threshold."""
        mock_llm_classifier.classify_query.return_value = "comparison"
        router = QueryRouter(
            config=router_config,
            llm_classifier=mock_llm_classifier,
        )

        # Ambiguous query that neither matches comparison nor complex patterns
        ambiguous = "Tell me about the economic situation"
        qtype, confidence = await router.classify_with_llm(ambiguous)

        # Keyword confidence should be below 0.5, triggering LLM fallback
        assert mock_llm_classifier.classify_query.called
        assert qtype == "comparison"

    @pytest.mark.anyio
    async def test_llm_fallback_not_called_for_high_confidence(
        self, mock_llm_classifier: AsyncMock, router_config: QueryRouterConfig
    ) -> None:
        """LLM fallback skipped when keyword confidence exceeds threshold."""
        router = QueryRouter(
            config=router_config,
            llm_classifier=mock_llm_classifier,
        )

        qtype, confidence = await router.classify_with_llm("What is Python?")
        assert qtype == "factual"
        assert confidence > 0.5
        # LLM should not have been called
        mock_llm_classifier.classify_query.assert_not_called()

    @pytest.mark.anyio
    async def test_llm_fallback_not_called_when_disabled(
        self, mock_llm_classifier: AsyncMock
    ) -> None:
        """LLM fallback skipped when llm_fallback_enabled is False."""
        config = QueryRouterConfig(llm_fallback_enabled=False, confidence_threshold=0.5)
        router = QueryRouter(config=config, llm_classifier=mock_llm_classifier)

        # Ambiguous query
        qtype, confidence = await router.classify_with_llm("some random text here")
        mock_llm_classifier.classify_query.assert_not_called()

    @pytest.mark.anyio
    async def test_llm_fallback_handles_error_gracefully(
        self, mock_llm_classifier: AsyncMock, router_config: QueryRouterConfig
    ) -> None:
        """If LLM fails, fallback to keyword classification result."""
        mock_llm_classifier.classify_query.side_effect = RuntimeError("LLM down")
        router = QueryRouter(
            config=router_config,
            llm_classifier=mock_llm_classifier,
        )

        ambiguous = "some ambiguous text here for testing"
        qtype, confidence = await router.classify_with_llm(ambiguous)
        # Should return whatever keyword classification gave, not crash
        assert qtype in ("factual", "complex", "comparison")


# ── RoutingRetriever tests ──────────────────────────────────────────────────


class TestRoutingRetriever:
    @pytest.fixture
    def router(self) -> QueryRouter:
        return QueryRouter(config=QueryRouterConfig())

    @pytest.fixture
    def mock_factual(self) -> AsyncMock:
        ret = AsyncMock(spec=CandidateRetriever)
        ret.retrieve.return_value = [_make_candidate("chunk-fact-1")]
        return ret

    @pytest.fixture
    def mock_complex(self) -> AsyncMock:
        ret = AsyncMock(spec=CandidateRetriever)
        ret.retrieve.return_value = [
            _make_candidate("chunk-comp-1"), _make_candidate("chunk-comp-2")]
        return ret

    @pytest.fixture
    def mock_comparison(self) -> AsyncMock:
        ret = AsyncMock(spec=CandidateRetriever)
        ret.retrieve.return_value = [
            _make_candidate(f"chunk-cmp-{i}") for i in range(5)
        ]
        return ret

    @pytest.mark.anyio
    async def test_routes_factual_to_factual_retriever(
        self,
        router: QueryRouter,
        mock_factual: AsyncMock,
        mock_complex: AsyncMock,
        mock_comparison: AsyncMock,
    ) -> None:
        routing = RoutingRetriever(
            router=router,
            factual_retriever=mock_factual,
            complex_retriever=mock_complex,
            comparison_retriever=mock_comparison,
        )
        request = _make_request("What is Python?")
        filters = _make_filters()

        result = await routing.retrieve(request=request, filters=filters)

        assert len(result) == 1
        assert result[0].chunk_id == "chunk-fact-1"
        mock_factual.retrieve.assert_called_once()
        mock_complex.retrieve.assert_not_called()
        mock_comparison.retrieve.assert_not_called()
        assert routing.last_query_type == "factual"

    @pytest.mark.anyio
    async def test_routes_complex_to_complex_retriever(
        self,
        router: QueryRouter,
        mock_factual: AsyncMock,
        mock_complex: AsyncMock,
        mock_comparison: AsyncMock,
    ) -> None:
        routing = RoutingRetriever(
            router=router,
            factual_retriever=mock_factual,
            complex_retriever=mock_complex,
            comparison_retriever=mock_comparison,
        )
        request = _make_request(
            "Explain how machine learning works and what impact it has on society"
        )
        filters = _make_filters()

        result = await routing.retrieve(request=request, filters=filters)

        assert len(result) == 2
        mock_complex.retrieve.assert_called_once()
        mock_factual.retrieve.assert_not_called()
        mock_comparison.retrieve.assert_not_called()
        assert routing.last_query_type == "complex"

    @pytest.mark.anyio
    async def test_routes_comparison_to_comparison_retriever(
        self,
        router: QueryRouter,
        mock_factual: AsyncMock,
        mock_complex: AsyncMock,
        mock_comparison: AsyncMock,
    ) -> None:
        routing = RoutingRetriever(
            router=router,
            factual_retriever=mock_factual,
            complex_retriever=mock_complex,
            comparison_retriever=mock_comparison,
        )
        request = _make_request("Compare Python and JavaScript")
        filters = _make_filters()

        result = await routing.retrieve(request=request, filters=filters)

        assert len(result) == 5
        mock_comparison.retrieve.assert_called_once()
        mock_factual.retrieve.assert_not_called()
        mock_complex.retrieve.assert_not_called()
        assert routing.last_query_type == "comparison"

    @pytest.mark.anyio
    async def test_adjusts_top_k_from_route_config(
        self,
        mock_factual: AsyncMock,
        mock_complex: AsyncMock,
        mock_comparison: AsyncMock,
    ) -> None:
        """The route's top_k overrides the request top_k for factual queries."""
        config = QueryRouterConfig(
            factual=QueryRouteConfig(top_k=3, skip_rerank=True),
        )
        router = QueryRouter(config=config)
        routing = RoutingRetriever(
            router=router,
            factual_retriever=mock_factual,
            complex_retriever=mock_complex,
            comparison_retriever=mock_comparison,
        )
        request = _make_request("What is Python?", top_k=50)
        filters = _make_filters()

        await routing.retrieve(request=request, filters=filters)

        call_args = mock_factual.retrieve.call_args
        adjusted_request = call_args.kwargs["request"]
        # Route config top_k=3 should override user's top_k=50
        assert adjusted_request.top_k == 3

    @pytest.mark.anyio
    async def test_user_score_threshold_wins_if_stricter(
        self,
        mock_complex: AsyncMock,
        mock_factual: AsyncMock,
        mock_comparison: AsyncMock,
    ) -> None:
        """User's score_threshold is respected when stricter than route default."""
        config = QueryRouterConfig(
            complex=QueryRouteConfig(top_k=10, score_threshold=0.3),
        )
        router = QueryRouter(config=config)
        routing = RoutingRetriever(
            router=router,
            factual_retriever=mock_factual,
            complex_retriever=mock_complex,
            comparison_retriever=mock_comparison,
        )
        # complex query with user's stricter score_threshold
        request = _make_request(
            "Explain how blockchain technology works",
            score_threshold=0.7,
        )
        filters = _make_filters()

        await routing.retrieve(request=request, filters=filters)

        call_args = mock_complex.retrieve.call_args
        adjusted_request = call_args.kwargs["request"]
        # User's stricter threshold (0.7) should win over route default (0.3)
        assert adjusted_request.score_threshold == 0.7


# ── Config wiring tests ─────────────────────────────────────────────────────


class TestConfigWiring:
    def test_query_router_config_defaults_match_v3(self) -> None:
        """Verify that QueryRouterConfig default values create sensible
        retrieval paths for the three query types."""
        config = QueryRouterConfig()

        # Factual: fast path
        assert config.factual.top_k == 5
        assert config.factual.score_threshold == 0.3
        assert config.factual.skip_rerank is True

        # Complex: full pipeline
        assert config.complex.top_k == 10
        assert config.complex.score_threshold == 0.3
        assert config.complex.skip_rerank is False

        # Comparison: high-recall
        assert config.comparison.top_k == 20
        assert config.comparison.score_threshold is None
        assert config.comparison.skip_rerank is False

    def test_all_query_types_covered(self) -> None:
        """Ensure all QueryType literals have a corresponding config slot."""
        config = QueryRouterConfig()
        for qtype in ("factual", "complex", "comparison"):
            route = config.get_route(qtype)
            assert isinstance(route, QueryRouteConfig)

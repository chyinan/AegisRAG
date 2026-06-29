"""Adaptive Retrieval Routing — query classifier and routing retriever.

Classifies queries into factual/complex/comparison types and routes each
to a tailored retrieval strategy: dense-only fast path for factual queries,
hybrid+rerank full path for complex multi-hop queries, and high-recall path
for comparison/analysis queries.

Lightweight keyword-based classifier with optional LLM fallback for ambiguous
queries. Disabled by default; enable via ADAPTIVE_ROUTING_ENABLED.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from packages.retrieval.dto import MAX_RETRIEVAL_TOP_K, RetrievalCandidate, RetrievalFilterSet, RetrievalRequest

# ── Query type ──────────────────────────────────────────────────────────────

QueryType = Literal["factual", "complex", "comparison"]
ALL_QUERY_TYPES: tuple[QueryType, ...] = ("factual", "complex", "comparison")

# ── Lightweight LLM provider protocol (optional) ────────────────────────────


class QueryClassifierLLM(Protocol):
    """Minimal protocol for LLM-based query classification fallback."""

    async def classify_query(self, *, query: str) -> QueryType: ...


# ── Per-type route configuration ────────────────────────────────────────────


class QueryRouteConfig(BaseModel):
    """Retrieval parameters selected per query type."""

    model_config = ConfigDict(frozen=True)

    top_k: int = 10
    score_threshold: float | None = None
    skip_rerank: bool = False

    @field_validator("top_k")
    @classmethod
    def _top_k_in_range(cls, value: int) -> int:
        if value <= 0 or value > MAX_RETRIEVAL_TOP_K:
            raise ValueError(f"top_k must be between 1 and {MAX_RETRIEVAL_TOP_K}")
        return value

    @field_validator("score_threshold")
    @classmethod
    def _score_threshold_in_range(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value < 0.0 or value > 1.0:
            raise ValueError("score_threshold must be between 0 and 1")
        return value


class QueryRouterConfig(BaseModel):
    """Configuration for the query router including per-type route defaults."""

    model_config = ConfigDict(frozen=True)

    factual: QueryRouteConfig = Field(
        default_factory=lambda: QueryRouteConfig(top_k=5, score_threshold=0.3, skip_rerank=True),
    )
    complex: QueryRouteConfig = Field(
        default_factory=lambda: QueryRouteConfig(top_k=10, score_threshold=0.3, skip_rerank=False),
    )
    comparison: QueryRouteConfig = Field(
        default_factory=lambda: QueryRouteConfig(top_k=20, score_threshold=None, skip_rerank=False),
    )
    llm_fallback_enabled: bool = False
    confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)

    def get_route(self, query_type: QueryType) -> QueryRouteConfig:
        return getattr(self, query_type)


# ── Keyword patterns for classification ─────────────────────────────────────

# Queries containing comparison/analysis indicator words
_COMPARISON_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bcompare\b", re.IGNORECASE),
    re.compile(r"\bversus\b", re.IGNORECASE),
    re.compile(r"\bvs\.?\b", re.IGNORECASE),
    re.compile(r"\bdifferences?\s+between\b", re.IGNORECASE),
    re.compile(r"\bpros?\s+and\s+cons?\b", re.IGNORECASE),
    re.compile(r"\badvantages?\s+(?:and|&|vs)\s+disadvantages?\b", re.IGNORECASE),
    re.compile(r"\bstrengths?\s+(?:and|&|vs)\s+weaknesses?\b", re.IGNORECASE),
    re.compile(r"\bwhich\s+(?:is|one)\s+(?:better|best|worse|worst)\b", re.IGNORECASE),
    re.compile(r"\bcontrast\b", re.IGNORECASE),
    re.compile(r"\banalyze\b", re.IGNORECASE),
    re.compile(r"\banalysis\b", re.IGNORECASE),
    re.compile(r"\btrade[\s-]?offs?\b", re.IGNORECASE),
    re.compile(r"\bevaluate\b", re.IGNORECASE),
)

# Queries containing multi-hop / complex reasoning indicators.
# NOTE: Patterns use bounded quantifiers and avoid DOTALL to prevent
# catastrophic backtracking (ReDoS) on adversarial input.
_COMPLEX_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Multi-step connectives (bounded — check first 200 chars)
    re.compile(r"\b(first.{1,80}then)\b", re.IGNORECASE),
    re.compile(r"\b(first|initially)\b.{1,100}\b(after|subsequently|next|finally)\b", re.IGNORECASE),
    # Multi-hop bridging
    re.compile(r"\b(how\s+(?:does|do|can|did|is|are)|what\s+(?:is|are))\b.{1,120}\b(affect|impact|influence|lead\s+to|result\s+in|cause|because|since|due\s+to)\b", re.IGNORECASE),
    re.compile(r"\b(explain|describe|elaborate)\s+(how|why)\b", re.IGNORECASE),
    # Reasoning chains
    re.compile(r"\b(what\s+(?:is|are)\s+the\s+(?:reasons?|causes?|implications?|consequences?|effects?))\b", re.IGNORECASE),
    re.compile(r"\b(relationship|connection|link)\s+between\b", re.IGNORECASE),
    re.compile(r"\b(underlying|root)\s+(cause|reason|mechanism)\b", re.IGNORECASE),
    # Multiple sub-questions
    re.compile(r"\?.{1,100}\?"),
    # Explicit multi-hop
    re.compile(r"\bmulti[\s-]?(?:hop|step|part|stage)\b", re.IGNORECASE),
    re.compile(r"\bchain\s+of\s+(?:thought|reasoning|events)\b", re.IGNORECASE),
)

# Queries that are clearly factual/simple (boost confidence when matched)
_FACTUAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:what|when|where)\s+(?:is|are|was|were)\b", re.IGNORECASE),
    re.compile(r"\bwho\s+(?:is|are|was|were|invented|discovered|created|founded|built|made|wrote|painted|composed|developed|designed)\b", re.IGNORECASE),
    re.compile(r"\bhow\s+(?:many|much|old|long|far|often|tall|big|large|small|fast|heavy|deep|wide)\b", re.IGNORECASE),
    re.compile(r"\bdefine\b", re.IGNORECASE),
    re.compile(r"\bdefinition\s+of\b", re.IGNORECASE),
)

# ── Query Router ────────────────────────────────────────────────────────────


class QueryRouter:
    """Lightweight query classifier that routes queries to optimal retrieval strategy.

    Uses fast keyword-based classification first. Optionally falls back to an
    LLM-based classifier for ambiguous queries.

    Classification rules (in priority order):
      1. comparison patterns → 'comparison'
      2. complex/multi-hop patterns → 'complex'
      3. factual patterns → 'factual' (boosted confidence)
      4. Short queries (≤ 3 words) → 'factual'
      5. Fallback → 'complex' (safe default, uses full pipeline)
    """

    def __init__(
        self,
        *,
        config: QueryRouterConfig,
        llm_classifier: QueryClassifierLLM | None = None,
    ) -> None:
        self._config = config
        self._llm_classifier = llm_classifier

    @property
    def config(self) -> QueryRouterConfig:
        return self._config

    @property
    def llm_classifier(self) -> QueryClassifierLLM | None:
        return self._llm_classifier

    def classify(self, query: str) -> tuple[QueryType, float]:
        """Classify a query and return (type, confidence).

        Confidence is 0.0–1.0 where 1.0 = high confidence keyword match.
        """
        stripped = query.strip()
        if not stripped:
            return ("factual", 1.0)

        # Check comparison patterns first (highest priority)
        for pattern in _COMPARISON_PATTERNS:
            if pattern.search(stripped):
                return ("comparison", 0.9)

        # Check complex/multi-hop patterns
        for pattern in _COMPLEX_PATTERNS:
            if pattern.search(stripped):
                return ("complex", 0.85)

        # Check factual patterns (boost factual confidence)
        factual_matches = 0
        for pattern in _FACTUAL_PATTERNS:
            if pattern.search(stripped):
                factual_matches += 1
        if factual_matches > 0:
            return ("factual", min(0.7 + factual_matches * 0.1, 1.0))

        # Heuristic: short queries (≤ 3 words, ≤ 50 chars) are likely factual
        word_count = len(stripped.split())
        if word_count <= 3 and len(stripped) <= 50:
            return ("factual", 0.65)

        # Heuristic: very long queries (> 20 words) are likely complex
        if word_count > 20:
            return ("complex", 0.6)

        # Ambiguous — low confidence, default to complex for safety
        return ("complex", 0.4)

    async def classify_with_llm(self, query: str) -> tuple[QueryType, float]:
        """Classify with optional LLM fallback for ambiguous queries."""
        qtype, confidence = self.classify(query)

        if confidence >= self._config.confidence_threshold:
            return qtype, confidence

        # Ambiguous query — attempt LLM fallback if available
        if self._llm_classifier is not None and self._config.llm_fallback_enabled:
            try:
                llm_type = await self._llm_classifier.classify_query(query=query)
                if llm_type in ALL_QUERY_TYPES:
                    return llm_type, 0.85  # LLM classification = medium-high confidence
            except Exception:
                pass  # Fallback to keyword result on error

        return qtype, confidence

    def get_route_config(self, query: str) -> QueryRouteConfig:
        """Classify query and return the appropriate route configuration.

        Synchronous version — uses keyword classification only.
        """
        qtype, _confidence = self.classify(query)
        return self._config.get_route(qtype)

    async def get_route_config_async(self, query: str) -> QueryRouteConfig:
        """Classify query (with optional LLM fallback) and return route config."""
        qtype, _confidence = await self.classify_with_llm(query)
        return self._config.get_route(qtype)


# ── Routing Retriever ───────────────────────────────────────────────────────


class RoutingRetriever:
    """CandidateRetriever that routes queries to different retrieval strategies.

    Uses QueryRouter to classify the query and delegates to the appropriate
    sub-retriever with adjusted top_k and score_threshold.
    """

    def __init__(
        self,
        *,
        router: QueryRouter,
        factual_retriever: CandidateRetriever,
        complex_retriever: CandidateRetriever,
        comparison_retriever: CandidateRetriever,
    ) -> None:
        self._router = router
        self._factual_retriever = factual_retriever
        self._complex_retriever = complex_retriever
        self._comparison_retriever = comparison_retriever
        self._last_query_type: QueryType | None = None
        self._last_confidence: float = 0.0

    @property
    def last_query_type(self) -> QueryType | None:
        return self._last_query_type

    @property
    def last_confidence(self) -> float:
        return self._last_confidence

    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> list[RetrievalCandidate]:
        route = await self._router.get_route_config_async(request.query)
        qtype, confidence = self._router.classify(request.query)
        self._last_query_type = qtype
        self._last_confidence = confidence

        # Build an adjusted request with route-specific parameters.
        # The route's top_k always takes precedence; user-provided
        # overrides are respected only when stricter.
        effective_top_k = route.top_k
        effective_threshold = route.score_threshold
        if request.score_threshold is not None:
            # User's threshold wins if stricter (higher value)
            effective_threshold = (
                max(request.score_threshold, effective_threshold)
                if effective_threshold is not None
                else request.score_threshold
            )

        adjusted_request = RetrievalRequest(
            query=request.query,
            top_k=effective_top_k,
            metadata_filter=request.metadata_filter,
            score_threshold=effective_threshold,
            request_id=request.request_id,
            trace_id=request.trace_id,
        )

        if qtype == "factual":
            return await self._factual_retriever.retrieve(
                request=adjusted_request, filters=filters
            )
        elif qtype == "comparison":
            return await self._comparison_retriever.retrieve(
                request=adjusted_request, filters=filters
            )
        else:
            return await self._complex_retriever.retrieve(
                request=adjusted_request, filters=filters
            )


# ── Re-export CandidateRetriever for type hints ─────────────────────────────

from packages.retrieval.ports import CandidateRetriever  # noqa: E402

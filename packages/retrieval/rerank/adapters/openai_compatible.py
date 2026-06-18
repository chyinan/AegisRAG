from __future__ import annotations

import asyncio
from collections.abc import Sequence
from time import perf_counter

import httpx

from packages.common.circuit_breaker import CircuitBreaker, CircuitOpenError
from packages.common.logging import get_request_logger
from packages.retrieval.dto import RetrievalCandidate, RetrievalFilterSet, RetrievalRequest
from packages.retrieval.exceptions import (
    RETRIEVAL_RERANK_DEGRADED,
    RETRIEVAL_RERANK_FAILED,
    RetrievalError,
)
from packages.retrieval.rerank import RerankResult, RerankTrace

_logger = get_request_logger()


class OpenAICompatibleReranker:
    """Real reranker via OpenAI-compatible /rerank endpoint (e.g. BGE, Cohere, Jina).

    Supports:
      - bge-reranker-v2-m3 (via TEI or vLLM with rerank API)
      - Cohere rerank API (OpenAI-compatible mode)
      - Any /v1/rerank endpoint
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        model: str = "bge-reranker-v2-m3",
        provider: str = "openai_compatible",
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._provider = provider
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._breaker = circuit_breaker

    async def rerank(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
        candidates: Sequence[RetrievalCandidate],
    ) -> RerankResult:
        started = perf_counter()
        if not candidates:
            return RerankResult(
                candidates=(),
                trace=_empty_rerank_trace(
                    request=request,
                    filters=filters,
                    provider=self._provider,
                    model=self._model,
                ),
            )

        documents = [_candidate_text(c) for c in candidates]
        payload = {
            "model": self._model,
            "query": request.query,
            "documents": documents,
            "top_n": min(len(candidates), request.top_k),
        }

        scores: list[float] = []
        error_code: str | None = None
        for attempt in range(self._max_retries + 1):
            try:
                async def _call() -> list[float]:
                    return await self._send_rerank_request(payload)
                if self._breaker is not None:
                    scores = await self._breaker.call(_call)
                else:
                    scores = await _call()
                error_code = None
                break
            except CircuitOpenError:
                error_code = RETRIEVAL_RERANK_DEGRADED
                _logger.warning("reranker_circuit_open", extra={"model": self._model})
                break
            except Exception:
                if attempt < self._max_retries:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                error_code = RETRIEVAL_RERANK_FAILED

        if error_code is not None:
            latency_ms = (perf_counter() - started) * 1000
            raise RetrievalError(
                code=error_code,
                message=f"Reranker call failed after {self._max_retries + 1} attempts.",
                details={
                    "provider": self._provider,
                    "model": self._model,
                    "input_count": len(candidates),
                    "output_count": 0,
                    "error_code": error_code,
                },
                status_code=502,
            )

        ranked = sorted(
            zip(candidates, scores, strict=False),
            key=lambda pair: -pair[1],
        )
        output_candidates: list[RetrievalCandidate] = []
        for output_rank, (candidate, score) in enumerate(ranked, start=1):
            metadata = dict(candidate.metadata)
            metadata["rerank_provenance"] = {
                "provider": self._provider,
                "model": self._model,
                "status": "success",
                "rerank_score": score,
                "output_rank": output_rank,
                "score_source": "rerank",
                "latency_ms": (perf_counter() - started) * 1000,
            }
            output_candidates.append(
                candidate.model_copy(update={"score": score, "metadata": metadata})
            )

        latency_ms = (perf_counter() - started) * 1000
        return RerankResult(
            candidates=tuple(output_candidates[: request.top_k]),
            trace=RerankTrace(
                request_id=request.request_id,
                trace_id=request.trace_id,
                tenant_id=filters.tenant_id,
                user_id=filters.user_id,
                provider=self._provider,
                model=self._model,
                latency_ms=latency_ms,
                input_count=len(candidates),
                output_count=min(len(output_candidates), request.top_k),
                safe_counts={
                    "input_candidates": len(candidates),
                    "output_candidates": min(len(output_candidates), request.top_k),
                },
            ),
        )

    async def _send_rerank_request(self, payload: dict[str, object]) -> list[float]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._base_url}/v1/rerank",
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
            data = response.json()

        results = data.get("results", data.get("data", []))
        scores: list[float] = []
        for item in results:
            score = item.get("relevance_score", item.get("score", 0.0))
            scores.append(float(score))
        return scores


def _candidate_text(candidate: RetrievalCandidate) -> str:
    text = candidate.metadata.get("chunk_text", candidate.metadata.get("content", ""))
    return str(text) if text else candidate.chunk_id


def _empty_rerank_trace(
    *,
    request: RetrievalRequest,
    filters: RetrievalFilterSet,
    provider: str,
    model: str,
) -> RerankTrace:
    return RerankTrace(
        request_id=request.request_id,
        trace_id=request.trace_id,
        tenant_id=filters.tenant_id,
        user_id=filters.user_id,
        provider=provider,
        model=model,
        latency_ms=0.0,
        input_count=0,
        output_count=0,
        safe_counts={"input_candidates": 0, "output_candidates": 0},
    )

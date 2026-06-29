"""LLM-based reranker that scores candidates via an existing LLM provider.

Uses the LLM to score each candidate's relevance to the query on a 0-10 scale,
then normalizes and ranks. Works with any LLM (DeepSeek, OpenAI, etc.) —
zero new API keys or infrastructure needed.
"""
from __future__ import annotations

import asyncio
from collections.abc import Sequence
from time import perf_counter

from packages.common.circuit_breaker import CircuitBreaker, CircuitOpenError
from packages.common.logging import get_request_logger
from packages.llm.dto import GenerateRequest, LLMMessage
from packages.llm.ports import LLMProvider
from packages.retrieval.dto import RetrievalCandidate, RetrievalFilterSet, RetrievalRequest
from packages.retrieval.exceptions import (
    RETRIEVAL_RERANK_DEGRADED,
    RETRIEVAL_RERANK_FAILED,
    RetrievalError,
)
from packages.retrieval.rerank import RerankResult, RerankTrace

_logger = get_request_logger()

_RERANK_SYSTEM_PROMPT = """\
You are a relevance scoring assistant. Your task is to score how relevant each document is to the user's query.

For each document, output a single line in the format:
SCORE: <number>

The score must be a float between 0.0 (completely irrelevant) and 10.0 (perfectly relevant).
Score based on:
- Does the document contain information that directly answers the query?
- Is the document factually relevant to the query's topic?
- Is the document content specific and detailed enough to be useful?

Be strict: only give high scores (8-10) to documents that are clearly and directly relevant.
Give medium scores (4-7) to partially or tangentially relevant documents.
Give low scores (0-3) to irrelevant or off-topic documents.

Output exactly one score per document, in the same order as presented. No explanations."""


class LLMReranker:
    """LLM-based reranker using an existing LLMProvider."""

    def __init__(
        self,
        *,
        llm_provider: LLMProvider,
        model: str = "deepseek-v4-flash",
        provider: str = "llm",
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        circuit_breaker: CircuitBreaker | None = None,
        batch_size: int = 10,
    ) -> None:
        self._llm = llm_provider
        self._model = model
        self._provider = provider
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._breaker = circuit_breaker
        self._batch_size = batch_size

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

        all_scores: list[float] = []
        error_code: str | None = None
        candidate_texts = [_candidate_text(c) for c in candidates]

        for batch_start in range(0, len(candidates), self._batch_size):
            batch_end = min(batch_start + self._batch_size, len(candidates))
            batch_texts = candidate_texts[batch_start:batch_end]

            documents_str = "\n\n".join(
                f"[Document {i+1}]:\n{text}"
                for i, text in enumerate(batch_texts)
            )
            user_prompt = f"""Query: {request.query}

Documents to score:
{documents_str}

Output one score per document in format: SCORE: <float>"""

            batch_scores: list[float] = []
            for attempt in range(self._max_retries + 1):
                try:
                    async def _call() -> list[float]:
                        return await self._score_batch(
                            request=request,
                            filters=filters,
                            user_prompt=user_prompt,
                            num_docs=len(batch_texts),
                        )

                    if self._breaker is not None:
                        batch_scores = await self._breaker.call(_call)
                    else:
                        batch_scores = await _call()
                    break
                except CircuitOpenError:
                    error_code = RETRIEVAL_RERANK_DEGRADED
                    _logger.warning("llm_reranker_circuit_open")
                    break
                except Exception:
                    if attempt < self._max_retries:
                        await asyncio.sleep(0.5 * (2 ** attempt))
                        continue
                    error_code = RETRIEVAL_RERANK_FAILED

            if error_code is not None:
                break

            while len(batch_scores) < len(batch_texts):
                batch_scores.append(0.0)
            all_scores.extend(batch_scores[: len(batch_texts)])

        if error_code is not None:
            latency_ms = (perf_counter() - started) * 1000
            raise RetrievalError(
                code=error_code,
                message=f"LLM reranker failed after {self._max_retries + 1} attempts.",
                details={
                    "provider": self._provider,
                    "model": self._model,
                    "input_count": len(candidates),
                    "output_count": 0,
                    "error_code": error_code,
                },
                status_code=502,
            )

        max_score = max(all_scores) if all_scores else 10.0
        normalized = [
            s / max_score if max_score > 0 else 0.0 for s in all_scores
        ]

        ranked = sorted(
            zip(candidates, normalized, strict=False),
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
                "score_source": "llm_rerank",
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

    async def _score_batch(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
        user_prompt: str,
        num_docs: int,
    ) -> list[float]:
        gen_request = GenerateRequest(
            messages=(
                LLMMessage(role="system", content=_RERANK_SYSTEM_PROMPT),
                LLMMessage(role="user", content=user_prompt),
            ),
            provider=self._provider,
            model=self._model,
            timeout_seconds=self._timeout,
            retry_budget=1,
            request_id=f"{request.request_id}-rerank",
            trace_id=request.trace_id,
            tenant_id=filters.tenant_id,
            user_id=filters.user_id,
            temperature=0.0,
            max_output_tokens=num_docs * 30,
        )

        response = await self._llm.generate(gen_request)
        text = response.content.strip()

        scores: list[float] = []
        for line in text.split("\n"):
            line = line.strip()
            if line.upper().startswith("SCORE:"):
                try:
                    score_str = line.split(":", 1)[1].strip()
                    score = float(score_str)
                    score = max(0.0, min(10.0, score))
                    scores.append(score)
                except (ValueError, IndexError):
                    scores.append(0.0)

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

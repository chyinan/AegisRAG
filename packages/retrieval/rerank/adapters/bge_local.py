"""本地 HuggingFace BGE-Reranker 适配器。

使用 transformers 的 AutoModelForSequenceClassification + AutoTokenizer
直接在本地运行 BGE Reranker 模型，替代 LLM Reranker 降低延迟和成本。

支持 GPU (cuda) 和 CPU fallback，模型懒加载，批量推理。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from time import perf_counter
from typing import TYPE_CHECKING

from packages.common.circuit_breaker import CircuitBreaker, CircuitOpenError
from packages.common.logging import get_request_logger
from packages.retrieval.dto import RetrievalCandidate, RetrievalFilterSet, RetrievalRequest
from packages.retrieval.exceptions import (
    RETRIEVAL_RERANK_DEGRADED,
    RETRIEVAL_RERANK_FAILED,
    RetrievalError,
)
from packages.retrieval.rerank import RerankResult, RerankTrace

if TYPE_CHECKING:
    pass

_logger = get_request_logger()

# 全局模型缓存，避免重复加载
_model_cache: dict[str, tuple[object, object]] = {}


def _load_model(
    model_name: str,
    device: str | None = None,
) -> tuple[object, object]:
    """加载 BGE Reranker 模型和分词器（同步，在 executor 中调用）。"""
    try:
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
    except ImportError as exc:
        raise ImportError(
            "transformers is required for BGELocalReranker. "
            "Install it with: pip install transformers torch"
        ) from exc

    if device is None:
        import torch as _torch
        device = "cuda" if _torch.cuda.is_available() else "cpu"

    _logger.info("bge_local_loading_model", extra={"model": model_name, "device": device})

    tokenizer = AutoTokenizer.from_pretrained(model_name)
    model = AutoModelForSequenceClassification.from_pretrained(model_name)
    model.eval()
    model.to(device)

    _logger.info("bge_local_model_loaded", extra={"model": model_name, "device": device})
    return model, tokenizer


class BGELocalReranker:
    """本地 HuggingFace BGE Reranker 适配器，满足 Reranker Protocol。

    使用 BAAI/bge-reranker-v2-m3 模型进行文档相关性打分。
    模型在首次调用时懒加载，支持 GPU 加速和 CPU fallback。
    使用线程池执行同步模型推理，避免阻塞事件循环。
    """

    def __init__(
        self,
        *,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        provider: str = "bge_local",
        device: str | None = None,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        circuit_breaker: CircuitBreaker | None = None,
        batch_size: int = 32,
    ) -> None:
        self._model_name = model_name
        self._provider = provider
        self._device = device
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._breaker = circuit_breaker
        self._batch_size = batch_size
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="bge_rerank")

    def _ensure_model_loaded(self) -> tuple[object, object]:
        """懒加载模型，首次调用时加载并缓存。"""
        cache_key = self._model_name
        if cache_key not in _model_cache:
            _model_cache[cache_key] = _load_model(
                self._model_name,
                device=self._device,
            )
        return _model_cache[cache_key]

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
                    model=self._model_name,
                ),
            )

        documents = [_candidate_text(c) for c in candidates]
        all_scores: list[float] = []
        error_code: str | None = None

        for attempt in range(self._max_retries + 1):
            try:
                async def _call() -> list[float]:
                    return await self._compute_scores(
                        query=request.query,
                        documents=documents,
                    )

                if self._breaker is not None:
                    all_scores = await self._breaker.call(_call)
                else:
                    all_scores = await _call()
                error_code = None
                break
            except CircuitOpenError:
                error_code = RETRIEVAL_RERANK_DEGRADED
                _logger.warning("bge_local_reranker_circuit_open")
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
                message=f"BGE Local reranker failed after {self._max_retries + 1} attempts.",
                details={
                    "provider": self._provider,
                    "model": self._model_name,
                    "input_count": len(candidates),
                    "output_count": 0,
                    "error_code": error_code,
                },
                status_code=502,
            )

        ranked = sorted(
            zip(candidates, all_scores, strict=False),
            key=lambda pair: -pair[1],
        )
        output_candidates: list[RetrievalCandidate] = []
        for output_rank, (candidate, score) in enumerate(ranked, start=1):
            metadata = dict(candidate.metadata)
            metadata["rerank_provenance"] = {
                "provider": self._provider,
                "model": self._model_name,
                "status": "success",
                "rerank_score": score,
                "output_rank": output_rank,
                "score_source": "bge_local",
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
                model=self._model_name,
                latency_ms=latency_ms,
                input_count=len(candidates),
                output_count=min(len(output_candidates), request.top_k),
                safe_counts={
                    "input_candidates": len(candidates),
                    "output_candidates": min(len(output_candidates), request.top_k),
                },
            ),
        )

    async def _compute_scores(
        self,
        *,
        query: str,
        documents: list[str],
    ) -> list[float]:
        """批量计算 query-document 相关性分数。

        将同步的 transformers 推理放到线程池中执行，避免阻塞事件循环。
        支持批量处理以提升 GPU 利用率。
        """
        model, tokenizer = self._ensure_model_loaded()

        all_scores: list[float] = []
        pairs: list[tuple[str, str]] = [(query, doc) for doc in documents]

        for batch_start in range(0, len(pairs), self._batch_size):
            batch_pairs = pairs[batch_start : batch_start + self._batch_size]
            try:
                batch_scores = await asyncio.get_event_loop().run_in_executor(
                    self._executor,
                    _score_pairs_sync,
                    model,
                    tokenizer,
                    batch_pairs,
                )
            except Exception:
                import traceback
                _logger.info("bge_local_compute_scores_failed", extra={
                    "batch_start": batch_start,
                    "batch_size": len(batch_pairs),
                    "query_len": len(query),
                    "doc_lens": [len(d) for d in [p[1] for p in batch_pairs]],
                    "traceback": traceback.format_exc(),
                })
                raise
            all_scores.extend(batch_scores)

        # 归一化到 0-1 范围（sigmoid + min-max）
        # BGE 输出 raw logit，可能为负值，先 sigmoid 再 min-max 确保 [0,1]
        import math
        if all_scores:
            all_scores = [1.0 / (1.0 + math.exp(-s)) for s in all_scores]
            min_score = min(all_scores)
            max_score = max(all_scores)
            if max_score > min_score:
                all_scores = [(s - min_score) / (max_score - min_score) for s in all_scores]

        return all_scores


def _score_pairs_sync(
    model: object,
    tokenizer: object,
    pairs: list[tuple[str, str]],
) -> list[float]:
    """同步执行批量打分（在 executor 线程中运行）。

    BGE Reranker 使用 [CLS] token 的输出作为相关性分数。
    """
    import torch

    queries, docs = zip(*pairs, strict=True)
    inputs = tokenizer(
        list(queries),
        list(docs),
        padding=True,
        truncation=True,
        max_length=512,
        return_tensors="pt",
    )

    device = next(model.parameters()).device
    inputs = {k: v.to(device) for k, v in inputs.items()}

    with torch.no_grad():
        outputs = model(**inputs)
        scores = outputs.logits.squeeze(-1).cpu().tolist()

    if isinstance(scores, float):
        scores = [scores]

    return [float(s) for s in scores]


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

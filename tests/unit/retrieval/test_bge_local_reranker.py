"""Tests for BGELocalReranker adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast
from unittest.mock import patch

import pytest

from packages.retrieval.dto import RetrievalCandidate, RetrievalFilterSet, RetrievalRequest
from packages.retrieval.exceptions import (
    RETRIEVAL_RERANK_FAILED,
    RetrievalError,
)
from packages.retrieval.rerank import RERANK_PROVENANCE_METADATA_KEY
from packages.retrieval.rerank.adapters import bge_local as bge_local_module
from packages.retrieval.rerank.adapters.bge_local import BGELocalReranker


@pytest.fixture(autouse=True)
def _clear_model_cache() -> None:
    """在每个测试前清理全局模型缓存，确保测试隔离。"""
    bge_local_module._model_cache.clear()


class _FakeModel:
    """Fake model for testing — avoids thread pool / MagicMock cross-thread issues."""

    def __init__(self, scores: list[float]) -> None:
        self._scores = scores
        self._call_count = 0

    def parameters(self):
        return iter([_FakeParam()])

    def eval(self):
        return self

    def to(self, device):
        return self

    def __call__(self, **inputs):
        self._call_count += 1
        # 根据 input_ids 的第一个维度确定 batch size
        input_ids = inputs.get("input_ids")
        batch_size = len(input_ids._data) if hasattr(input_ids, "_data") else len(self._scores)
        scores = (
            self._scores[:batch_size]
            if len(self._scores) >= batch_size
            else self._scores * batch_size
        )
        logits = _FakeLogits(scores[:batch_size])
        return _FakeOutput(logits=logits)


class _FakeParam:
    device = "cpu"


class _FakeOutput:
    def __init__(self, logits):
        self.logits = logits


class _FakeLogits:
    def __init__(self, scores: list[float]):
        self._scores = scores

    def squeeze(self, dim):
        return self

    def cpu(self):
        return self

    def tolist(self):
        return self._scores


class _FakeTokenizer:
    """Fake tokenizer returns predictable dict, tracks batch size."""

    def __call__(self, queries, docs, padding, truncation, max_length, return_tensors):
        batch_size = len(queries)
        return {
            "input_ids": _FakeTensor([[1, 2, 3]] * batch_size),
            "attention_mask": _FakeTensor([[1, 1, 1]] * batch_size),
        }


class _FakeTensor:
    def __init__(self, data):
        self._data = data

    def to(self, device):
        return self


@pytest.mark.asyncio
async def test_bge_local_reranker_empty_candidates() -> None:
    """空候选列表应返回空结果。"""
    reranker = BGELocalReranker()
    request = _request()
    filters = _filters()

    result = await reranker.rerank(
        request=request,
        filters=filters,
        candidates=[],
    )

    assert result.candidates == ()
    assert result.trace.provider == "bge_local"
    assert result.trace.model == "BAAI/bge-reranker-v2-m3"
    assert result.trace.input_count == 0
    assert result.trace.output_count == 0


@pytest.mark.asyncio
async def test_bge_local_ranks_by_relevance_score() -> None:
    """验证 BGE Local Reranker 按相关性分数降序排列候选。"""
    model = _FakeModel(scores=[0.85, 0.15])
    tokenizer = _FakeTokenizer()

    with patch(
        "packages.retrieval.rerank.adapters.bge_local._load_model",
        return_value=(model, tokenizer),
    ):
        reranker = BGELocalReranker()
        request = _request(query="What is the company policy?")
        filters = _filters()
        candidates = [
            _candidate(chunk_id="high", score=0.5),
            _candidate(chunk_id="low", score=0.5),
        ]

        result = await reranker.rerank(
            request=request,
            filters=filters,
            candidates=candidates,
        )

    # 高分 candidate 应排前面
    assert [c.chunk_id for c in result.candidates] == ["high", "low"]
    assert result.candidates[0].score > result.candidates[1].score
    assert result.trace.provider == "bge_local"
    assert result.trace.input_count == 2
    assert result.trace.output_count == 2
    assert result.trace.degraded is False


@pytest.mark.asyncio
async def test_bge_local_normalizes_scores_to_0_1_range() -> None:
    """验证分数归一化到 0-1 范围。"""
    # 原始 logits 可能不在 0-1 范围
    model = _FakeModel(scores=[3.5, 1.2, 0.3])
    tokenizer = _FakeTokenizer()

    with patch(
        "packages.retrieval.rerank.adapters.bge_local._load_model",
        return_value=(model, tokenizer),
    ):
        reranker = BGELocalReranker()
        request = _request()
        filters = _filters()
        candidates = [
            _candidate(chunk_id="a", score=0.5),
            _candidate(chunk_id="b", score=0.5),
            _candidate(chunk_id="c", score=0.5),
        ]

        result = await reranker.rerank(
            request=request,
            filters=filters,
            candidates=candidates,
        )

    scores = [c.score for c in result.candidates]
    # 归一化后最大值应为 1.0
    assert max(scores) == pytest.approx(1.0)
    assert min(scores) >= 0.0
    # 3.5/3.5=1.0, 1.2/3.5≈0.343, 0.3/3.5≈0.086
    assert scores[0] == pytest.approx(1.0, abs=0.01)
    assert scores[1] == pytest.approx(1.2 / 3.5, abs=0.01)
    assert scores[2] == pytest.approx(0.3 / 3.5, abs=0.01)


@pytest.mark.asyncio
async def test_bge_local_sets_provenance_metadata() -> None:
    """验证 rerank_provenance 元数据正确设置。"""
    model = _FakeModel(scores=[0.9])
    tokenizer = _FakeTokenizer()

    with patch(
        "packages.retrieval.rerank.adapters.bge_local._load_model",
        return_value=(model, tokenizer),
    ):
        reranker = BGELocalReranker()
        request = _request()
        filters = _filters()
        candidates = [_candidate(chunk_id="doc", score=0.8)]

        result = await reranker.rerank(
            request=request,
            filters=filters,
            candidates=candidates,
        )

    candidate = result.candidates[0]
    provenance = cast(
        "Mapping[str, object]",
        candidate.metadata[RERANK_PROVENANCE_METADATA_KEY],
    )
    assert provenance["provider"] == "bge_local"
    assert provenance["model"] == "BAAI/bge-reranker-v2-m3"
    assert provenance["status"] == "success"
    assert provenance["score_source"] == "bge_local"
    assert isinstance(provenance["rerank_score"], float)
    assert isinstance(provenance["output_rank"], int)
    assert isinstance(provenance["latency_ms"], float)


@pytest.mark.asyncio
async def test_bge_local_respects_top_k() -> None:
    """验证仅返回 top_k 个候选。"""
    model = _FakeModel(scores=[0.9, 0.7, 0.5, 0.3])
    tokenizer = _FakeTokenizer()

    with patch(
        "packages.retrieval.rerank.adapters.bge_local._load_model",
        return_value=(model, tokenizer),
    ):
        reranker = BGELocalReranker()
        request = _request(top_k=2)
        filters = _filters()
        candidates = [
            _candidate(chunk_id="a", score=0.5),
            _candidate(chunk_id="b", score=0.5),
            _candidate(chunk_id="c", score=0.5),
            _candidate(chunk_id="d", score=0.5),
        ]

        result = await reranker.rerank(
            request=request,
            filters=filters,
            candidates=candidates,
        )

    assert len(result.candidates) == 2
    assert result.trace.output_count == 2


@pytest.mark.asyncio
async def test_bge_local_model_cache_is_shared() -> None:
    """验证模型缓存在多个实例间共享。"""
    model = _FakeModel(scores=[0.9])
    tokenizer = _FakeTokenizer()

    load_count = 0

    def _counting_load(model_name, device=None):
        nonlocal load_count
        load_count += 1
        return model, tokenizer

    with patch(
        "packages.retrieval.rerank.adapters.bge_local._load_model",
        side_effect=_counting_load,
    ):
        reranker1 = BGELocalReranker()
        reranker2 = BGELocalReranker()

        await reranker1.rerank(
            request=_request(),
            filters=_filters(),
            candidates=[_candidate(chunk_id="a", score=0.8)],
        )
        await reranker2.rerank(
            request=_request(),
            filters=_filters(),
            candidates=[_candidate(chunk_id="b", score=0.8)],
        )

    # 两个实例应共享同一份模型缓存
    assert load_count == 1


@pytest.mark.asyncio
async def test_bge_local_custom_model_name() -> None:
    """验证自定义模型名称。"""
    model = _FakeModel(scores=[0.8])
    tokenizer = _FakeTokenizer()

    with patch(
        "packages.retrieval.rerank.adapters.bge_local._load_model",
        return_value=(model, tokenizer),
    ) as mock_load:
        reranker = BGELocalReranker(
            model_name="BAAI/bge-reranker-large",
            provider="bge_local_large",
        )
        await reranker.rerank(
            request=_request(),
            filters=_filters(),
            candidates=[_candidate(chunk_id="a", score=0.8)],
        )

        mock_load.assert_called_once_with(
            "BAAI/bge-reranker-large",
            device=None,
        )
        assert reranker._provider == "bge_local_large"  # noqa: SLF001


@pytest.mark.asyncio
async def test_bge_local_error_raises_retrieval_error() -> None:
    """验证模型加载失败时抛出 RetrievalError。"""
    with patch(
        "packages.retrieval.rerank.adapters.bge_local._load_model",
        side_effect=RuntimeError("CUDA out of memory"),
    ):
        reranker = BGELocalReranker(max_retries=0)
        request = _request()
        filters = _filters()
        candidates = [_candidate(chunk_id="a", score=0.8)]

        with pytest.raises(RetrievalError) as exc_info:
            await reranker.rerank(
                request=request,
                filters=filters,
                candidates=candidates,
            )

        assert exc_info.value.code == RETRIEVAL_RERANK_FAILED
        assert exc_info.value.details["provider"] == "bge_local"
        assert exc_info.value.details["model"] == "BAAI/bge-reranker-v2-m3"


@pytest.mark.asyncio
async def test_bge_local_batch_processing() -> None:
    """验证批量推理：多候选被分成多个 batch 处理。"""
    model = _FakeModel(scores=[0.7])
    tokenizer = _FakeTokenizer()

    with patch(
        "packages.retrieval.rerank.adapters.bge_local._load_model",
        return_value=(model, tokenizer),
    ):
        # batch_size=2，5 个候选 → 应调用 3 次（2+2+1）
        reranker = BGELocalReranker(batch_size=2)
        request = _request()
        filters = _filters()
        candidates = [
            _candidate(chunk_id=str(i), score=0.5) for i in range(5)
        ]

        result = await reranker.rerank(
            request=request,
            filters=filters,
            candidates=candidates,
        )

    assert len(result.candidates) == 5
    # batch_size=2, 5 candidates → ceil(5/2) = 3 batches
    assert model._call_count == 3


# ── Helpers ──────────────────────────────────────────────────────────────────


def _request(
    *,
    query: str = "policy",
    top_k: int = 10,
    request_id: str = "req-1",
    trace_id: str = "trace-1",
) -> RetrievalRequest:
    return RetrievalRequest(
        query=query,
        top_k=top_k,
        request_id=request_id,
        trace_id=trace_id,
    )


def _filters() -> RetrievalFilterSet:
    return RetrievalFilterSet(
        tenant_id="tenant-a",
        user_id="user-1",
    )


def _candidate(
    *,
    chunk_id: str,
    score: float,
    tenant_id: str = "tenant-a",
) -> RetrievalCandidate:
    return RetrievalCandidate(
        document_id=f"doc-{chunk_id}",
        version_id=f"ver-{chunk_id}",
        chunk_id=chunk_id,
        source=f"kb://{chunk_id}.md",
        source_type="markdown",
        source_uri=f"kb://{chunk_id}.md",
        page_start=1,
        page_end=2,
        title_path=("Policy", chunk_id),
        score=score,
        retrieval_method="hybrid",
        tenant_id=tenant_id,
        acl={"visibility": "tenant", "allowed_roles": ["hr"]},
        metadata={
            "department": "people",
            "chunk_content": "Some document content for testing.",
        },
    )

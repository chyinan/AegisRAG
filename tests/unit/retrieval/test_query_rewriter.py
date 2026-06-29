"""查询改写模块单元测试。

测试覆盖：
  - QueryRewriteConfig 配置验证
  - QueryRewriter Protocol 接口验证
  - HyDEQueryRewriter（LLM驱动）正常流程与回退逻辑
  - KeywordExtractionRewriter 关键词提取
  - QueryRewritingRetriever 管道集成
  - _keyword_extraction 和 _extract_key_terms 辅助函数
"""
from __future__ import annotations

from collections.abc import Mapping

import pytest

from packages.llm.adapters.fake import FakeLLMProvider
from packages.llm.dto import LLMMessage
from packages.llm.ports import LLMProvider
from packages.retrieval.dto import (
    RetrievalCandidate,
    RetrievalFilterSet,
    RetrievalRequest,
)
from packages.retrieval.ports import CandidateRetriever
from packages.retrieval.query_rewriter import (
    HyDEQueryRewriter,
    KeywordExtractionRewriter,
    QueryRewriteConfig,
    QueryRewritingRetriever,
    QueryRewriter,
    _extract_key_terms,
    _keyword_extraction,
)


# ---------------------------------------------------------------------------
# 测试夹具
# ---------------------------------------------------------------------------
@pytest.fixture
def fake_llm() -> FakeLLMProvider:
    """返回默认的 FakeLLMProvider，用于测试 HyDE 改写器。"""
    return FakeLLMProvider()


@pytest.fixture
def default_config() -> QueryRewriteConfig:
    """返回默认查询改写配置。"""
    return QueryRewriteConfig()


@pytest.fixture
def retrieval_request() -> RetrievalRequest:
    """返回一个标准的检索请求测试数据。"""
    return RetrievalRequest(
        query="什么是深度学习？",
        top_k=10,
        request_id="test-req-1",
        trace_id="test-trace-1",
    )


@pytest.fixture
def filter_set() -> RetrievalFilterSet:
    """返回一个标准的检索过滤条件测试数据。"""
    return RetrievalFilterSet(
        tenant_id="test-tenant",
        user_id="test-user",
    )


# ---------------------------------------------------------------------------
# QueryRewriteConfig 测试
# ---------------------------------------------------------------------------
class TestQueryRewriteConfig:
    """QueryRewriteConfig 配置模型测试。"""

    def test_default_values(self) -> None:
        """验证默认配置值。"""
        config = QueryRewriteConfig()
        assert config.enabled is True
        assert "{query}" in config.hyde_prompt_template
        assert config.hyde_temperature == 0.7
        assert config.hyde_max_output_tokens == 512
        assert config.hyde_timeout_seconds == 30.0
        assert config.extraction_top_k == 10
        assert config.hyde_model == ""

    def test_prompt_template_must_contain_query_placeholder(self) -> None:
        """验证提示词模板必须包含 {query} 占位符。"""
        with pytest.raises(ValueError, match="must contain '{query}' placeholder"):
            QueryRewriteConfig(hyde_prompt_template="没有占位符的模板")

    def test_temperature_range_validation(self) -> None:
        """验证 temperature 必须在 0-2 范围内。"""
        with pytest.raises(ValueError):
            QueryRewriteConfig(hyde_temperature=-0.1)
        with pytest.raises(ValueError):
            QueryRewriteConfig(hyde_temperature=2.1)

    def test_max_output_tokens_positive(self) -> None:
        """验证 max_output_tokens 必须为正数。"""
        with pytest.raises(ValueError):
            QueryRewriteConfig(hyde_max_output_tokens=0)
        with pytest.raises(ValueError):
            QueryRewriteConfig(hyde_max_output_tokens=-1)

    def test_frozen_config(self) -> None:
        """验证配置是不可变的 frozen 模型。"""
        config = QueryRewriteConfig()
        with pytest.raises(Exception):
            config.enabled = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# _keyword_extraction 辅助函数测试
# ---------------------------------------------------------------------------
class TestKeywordExtraction:
    """关键词提取辅助函数测试。"""

    def test_empty_string(self) -> None:
        """验证空字符串返回空字符串。"""
        assert _keyword_extraction("") == ""

    def test_whitespace_only(self) -> None:
        """验证只有空格的字符串返回空字符串。"""
        assert _keyword_extraction("   ") == ""

    def test_chinese_simple(self) -> None:
        """验证中文简单查询关键词提取。"""
        result = _keyword_extraction("深度学习如何工作")
        # 预期提取：深度 学习 如何 工作 等
        assert "深度" in result or "学习" in result or "如何" in result

    def test_english_simple(self) -> None:
        """验证英文简单查询关键词提取。"""
        result = _keyword_extraction("what is deep learning")
        assert "deep" in result.lower()
        assert "learning" in result.lower()
        assert "what" not in result.lower().split()  # 停用词应被过滤
        assert "is" not in result.lower().split()

    def test_mixed_chinese_english(self) -> None:
        """验证中英混合查询关键词提取。"""
        result = _keyword_extraction("什么是 RAG 检索增强生成")
        assert "RAG" in result

    def test_top_k_limit(self) -> None:
        """验证 top_k 限制关键词数量。"""
        result = _keyword_extraction(
            "人工智能 机器学习 深度学习 自然语言处理 计算机视觉 强化学习",
            top_k=3,
        )
        parts = result.split()
        assert len(parts) <= 3

    def test_stop_words_filtered(self) -> None:
        """验证中文停用词被正确过滤。"""
        result = _keyword_extraction("学习笔记")
        # "笔记" 和 "学习" 应该被提取为关键词
        parts = result.split()
        assert len(parts) > 0
        for word in ["的", "了", "在", "是"]:
            assert word not in parts

    def test_returns_original_on_no_keywords(self) -> None:
        """验证无法提取关键词时返回原始查询。"""
        # 如果全部是停用词
        result = _keyword_extraction("的了的")
        # 应至少返回非空字符串
        assert len(result) > 0

    def test_chinese_long_text(self) -> None:
        """验证长中文文本的关键词切分。"""
        result = _keyword_extraction("人工智能技术正在快速发展")
        # 应该有多个2-3字片段
        parts = result.split()
        assert len(parts) > 0


# ---------------------------------------------------------------------------
# _extract_key_terms 辅助函数测试
# ---------------------------------------------------------------------------
class TestExtractKeyTerms:
    """假设性回答关键术语提取测试。"""

    def test_empty_text(self) -> None:
        """验证空文本返回空字符串。"""
        assert _extract_key_terms("", top_k=5) == ""

    def test_simple_extraction(self) -> None:
        """验证从简单文本中提取关键术语。"""
        text = "深度学习是机器学习的一个子领域。它使用多层神经网络进行特征学习。"
        result = _extract_key_terms(text, top_k=5)
        parts = result.split()
        assert len(parts) <= 5
        assert len(parts) > 0

    def test_top_k_respected(self) -> None:
        """验证 top_k 限制被遵守。"""
        text = (
            "人工智能 机器学习 深度学习 自然语言处理 计算机视觉 "
            "强化学习 迁移学习 联邦学习 对比学习 自监督学习"
        )
        result = _extract_key_terms(text, top_k=3)
        parts = result.split()
        assert len(parts) <= 3

    def test_sentence_splitting(self) -> None:
        """验证按句子分割提取关键术语。"""
        text = "深度学习使用神经网络。机器学习包括监督学习。自然语言处理很重要。"
        result = _extract_key_terms(text, top_k=10)
        parts = result.split()
        # 所有独特关键词应小于等于top_k
        assert len(parts) <= 10


# ---------------------------------------------------------------------------
# KeywordExtractionRewriter 测试
# ---------------------------------------------------------------------------
class TestKeywordExtractionRewriter:
    """KeywordExtractionRewriter 类测试。"""

    def test_rewrite_returns_non_empty(self) -> None:
        """验证改写结果非空。"""
        rewriter = KeywordExtractionRewriter(top_k=5)
        import asyncio
        result = asyncio.run(rewriter.rewrite("什么是深度学习"))
        assert len(result) > 0

    def test_rewrite_with_chinese(self) -> None:
        """验证中文查询改写。"""
        rewriter = KeywordExtractionRewriter(top_k=5)
        import asyncio
        result = asyncio.run(rewriter.rewrite("深度学习如何训练"))
        assert len(result) > 0

    def test_trace_after_rewrite(self) -> None:
        """验证改写后追踪信息已更新。"""
        rewriter = KeywordExtractionRewriter(top_k=5)
        import asyncio
        asyncio.run(rewriter.rewrite("test query"))
        trace = rewriter.last_trace
        assert isinstance(trace, Mapping)
        assert trace["method"] == "keyword_extraction"
        assert "rewritten_query" in trace

    def test_invalid_top_k(self) -> None:
        """验证无效的 top_k 抛出异常。"""
        with pytest.raises(ValueError, match="top_k must be greater than 0"):
            KeywordExtractionRewriter(top_k=0)
        with pytest.raises(ValueError, match="top_k must be greater than 0"):
            KeywordExtractionRewriter(top_k=-1)

    def test_satisfies_protocol(self) -> None:
        """验证类满足 QueryRewriter 协议。"""
        rewriter = KeywordExtractionRewriter(top_k=5)
        assert hasattr(rewriter, "rewrite")
        assert hasattr(rewriter, "last_trace")
        assert callable(rewriter.rewrite)


# ---------------------------------------------------------------------------
# HyDEQueryRewriter 测试
# ---------------------------------------------------------------------------
class TestHyDEQueryRewriter:
    """HyDEQueryRewriter LLM驱动改写器测试。"""

    def test_rewrite_with_fake_llm(self, fake_llm: FakeLLMProvider) -> None:
        """验证使用 FakeLLM 的 HyDE 改写流程。"""
        rewriter = HyDEQueryRewriter(llm_provider=fake_llm)
        import asyncio
        result = asyncio.run(rewriter.rewrite("什么是深度学习？"))
        assert len(result) > 0
        trace = dict(rewriter.last_trace)
        assert trace["method"] == "hyde"

    def test_fallback_on_llm_failure(self) -> None:
        """验证 LLM 失败时自动回退到关键词提取。"""

        class FailingLLM:
            async def generate(self, request):
                raise RuntimeError("LLM unavailable")

            def stream(self, request):
                raise RuntimeError("LLM unavailable")

        rewriter = HyDEQueryRewriter(llm_provider=FailingLLM())
        import asyncio
        # 使用3个以上单词的查询，避免触发短查询短路
        result = asyncio.run(rewriter.rewrite("how does deep learning work in practice"))
        assert len(result) > 0
        trace = dict(rewriter.last_trace)
        assert trace["rewritten_via"] == "keyword_fallback"

    def test_short_query_skips_llm(self, fake_llm: FakeLLMProvider) -> None:
        """验证极短查询跳过 LLM，直接做关键词提取。"""
        rewriter = HyDEQueryRewriter(llm_provider=fake_llm)
        import asyncio
        result = asyncio.run(rewriter.rewrite("AI"))
        assert len(result) > 0
        # 短查询应直接走 keyword_extraction 而不是 hyde_llm
        # （因为只有1个单词 <= 2，不调用LLM）

    def test_custom_config(self, fake_llm: FakeLLMProvider) -> None:
        """验证自定义配置生效。"""
        config = QueryRewriteConfig(
            hyde_temperature=1.5,
            hyde_max_output_tokens=256,
            extraction_top_k=5,
        )
        rewriter = HyDEQueryRewriter(llm_provider=fake_llm, config=config)
        assert rewriter._config.hyde_temperature == 1.5  # noqa: SLF001
        assert rewriter._config.extraction_top_k == 5  # noqa: SLF001

    def test_default_config(self, fake_llm: FakeLLMProvider) -> None:
        """验证不传配置时使用默认值。"""
        rewriter = HyDEQueryRewriter(llm_provider=fake_llm)
        assert rewriter._config.enabled is True  # noqa: SLF001

    def test_last_trace_updates(self, fake_llm: FakeLLMProvider) -> None:
        """验证每次改写后 last_trace 被更新。"""
        rewriter = HyDEQueryRewriter(llm_provider=fake_llm)
        import asyncio

        # 第一次改写（使用3+英文单词避免短路）
        result1 = asyncio.run(rewriter.rewrite("what is deep learning about"))
        trace1 = dict(rewriter.last_trace)
        assert "rewritten_query" in trace1

        # 第二次改写（短查询，跳过LLM）
        result2 = asyncio.run(rewriter.rewrite("AI"))
        trace2 = dict(rewriter.last_trace)
        assert "rewritten_query" in trace2

    def test_satisfies_protocol(self, fake_llm: FakeLLMProvider) -> None:
        """验证类满足 QueryRewriter 协议。"""
        rewriter = HyDEQueryRewriter(llm_provider=fake_llm)
        assert hasattr(rewriter, "rewrite")
        assert hasattr(rewriter, "last_trace")
        assert callable(rewriter.rewrite)

    def test_prompt_template_used(self, fake_llm: FakeLLMProvider) -> None:
        """验证 HyDE 提示词模板被正确使用。"""
        custom_template = "Question: {query}\n\nHypothetical Answer:"
        config = QueryRewriteConfig(hyde_prompt_template=custom_template)
        rewriter = HyDEQueryRewriter(llm_provider=fake_llm, config=config)
        import asyncio
        result = asyncio.run(rewriter.rewrite("What is deep learning?"))
        assert len(result) > 0


# ---------------------------------------------------------------------------
# QueryRewritingRetriever 管道集成测试
# ---------------------------------------------------------------------------
class TestQueryRewritingRetriever:
    """QueryRewritingRetriever 装饰器集成测试。"""

    @pytest.fixture
    def fake_upstream(self) -> CandidateRetriever:
        """返回一个假的上游检索器，用于验证查询改写效果。"""

        class _FakeUpstream:
            def __init__(self) -> None:
                self.requests: list[RetrievalRequest] = []

            async def retrieve(
                self,
                *,
                request: RetrievalRequest,
                filters: RetrievalFilterSet,
            ) -> list[RetrievalCandidate]:
                self.requests.append(request)
                return []

        return _FakeUpstream()

    def test_retrieve_rewrites_query(
        self,
        fake_upstream: CandidateRetriever,
        retrieval_request: RetrievalRequest,
        filter_set: RetrievalFilterSet,
    ) -> None:
        """验证检索时查询被改写。"""
        rewriter = KeywordExtractionRewriter(top_k=5)
        retriever = QueryRewritingRetriever(
            query_rewriter=rewriter,
            upstream_retriever=fake_upstream,
        )
        import asyncio

        asyncio.run(
            retriever.retrieve(request=retrieval_request, filters=filter_set)
        )

        # 上游检索器应收到改写后的查询
        assert len(fake_upstream.requests) == 1  # type: ignore[attr-defined]
        upstream_request = fake_upstream.requests[0]  # type: ignore[attr-defined]
        assert isinstance(upstream_request, RetrievalRequest)
        # 改写后的查询应与原始不同（除非提取不到关键词）
        assert len(upstream_request.query) > 0

    def test_rewrite_trace_accessible(
        self,
        fake_upstream: CandidateRetriever,
        retrieval_request: RetrievalRequest,
        filter_set: RetrievalFilterSet,
    ) -> None:
        """验证可以通过 last_rewrite_trace 获取改写追踪信息。"""
        rewriter = KeywordExtractionRewriter(top_k=5)
        retriever = QueryRewritingRetriever(
            query_rewriter=rewriter,
            upstream_retriever=fake_upstream,
        )
        import asyncio

        asyncio.run(
            retriever.retrieve(request=retrieval_request, filters=filter_set)
        )

        trace = retriever.last_rewrite_trace
        assert isinstance(trace, Mapping)

    def test_with_hyde_rewriter(
        self,
        fake_upstream: CandidateRetriever,
        retrieval_request: RetrievalRequest,
        filter_set: RetrievalFilterSet,
        fake_llm: FakeLLMProvider,
    ) -> None:
        """验证 HyDEQueryRewriter 与 QueryRewritingRetriever 集成。"""
        rewriter = HyDEQueryRewriter(llm_provider=fake_llm)
        retriever = QueryRewritingRetriever(
            query_rewriter=rewriter,
            upstream_retriever=fake_upstream,
        )
        import asyncio

        asyncio.run(
            retriever.retrieve(request=retrieval_request, filters=filter_set)
        )

        assert len(fake_upstream.requests) == 1  # type: ignore[attr-defined]

    def test_multiple_retrieve_calls(
        self,
        fake_upstream: CandidateRetriever,
        filter_set: RetrievalFilterSet,
    ) -> None:
        """验证多次检索调用各自独立改写查询。"""
        rewriter = KeywordExtractionRewriter(top_k=5)
        retriever = QueryRewritingRetriever(
            query_rewriter=rewriter,
            upstream_retriever=fake_upstream,
        )
        import asyncio

        request1 = RetrievalRequest(
            query="什么是深度学习",
            top_k=10,
            request_id="req-1",
            trace_id="trace-1",
        )
        request2 = RetrievalRequest(
            query="自然语言处理应用",
            top_k=10,
            request_id="req-2",
            trace_id="trace-2",
        )

        asyncio.run(retriever.retrieve(request=request1, filters=filter_set))
        asyncio.run(retriever.retrieve(request=request2, filters=filter_set))

        assert len(fake_upstream.requests) == 2  # type: ignore[attr-defined]
        req1 = fake_upstream.requests[0]  # type: ignore[attr-defined]
        req2 = fake_upstream.requests[1]  # type: ignore[attr-defined]
        # 不同查询的改写结果应不同
        assert req1.query != req2.query


# ---------------------------------------------------------------------------
# 集成：QueryRewriter Protocol 验证
# ---------------------------------------------------------------------------
def test_custom_implementations_satisfy_protocol() -> None:
    """验证自定义实现满足 QueryRewriter 协议。"""

    class CustomRewriter:
        def __init__(self) -> None:
            self._trace: dict[str, object] = {}

        async def rewrite(self, query: str) -> str:
            self._trace = {"custom": True, "query": query}
            return f"rewritten: {query}"

        @property
        def last_trace(self) -> Mapping[str, object]:
            return self._trace

    # 类型检查: 应该可以被赋值给 QueryRewriter 类型
    rewriter: QueryRewriter = CustomRewriter()
    assert rewriter is not None

    import asyncio
    result = asyncio.run(rewriter.rewrite("test"))
    assert result == "rewritten: test"
    trace = dict(rewriter.last_trace)
    assert trace["custom"] is True

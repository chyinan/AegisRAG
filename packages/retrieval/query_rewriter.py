"""查询改写模块 —— 提升检索召回率。

提供两种策略：
  - HyDE（Hypothetical Document Embeddings）：用LLM生成假设性答案，再提取关键术语
  - 关键词提取：基于规则的轻量级备选方案（无需LLM调用）

所有实现遵循 Protocol 接口，支持依赖注入，可插入检索管道中作为预处理步骤。
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from packages.llm.dto import GenerateRequest, LLMMessage
from packages.llm.ports import LLMProvider


# ---------------------------------------------------------------------------
# 中文停用词表（用于关键词提取的回退方案）
# ---------------------------------------------------------------------------
_CN_STOP_WORDS: set[str] = {
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
    "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
    "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些",
    "什么", "怎么", "如何", "为什么", "哪个", "哪里", "可以", "应该", "可能",
    "已经", "还", "又", "才", "刚", "将", "正在", "一直", "总是", "从来",
    "就是", "但是", "如果", "因为", "所以", "虽然", "然而", "而且", "或者",
    "以及", "与", "并", "但", "而", "却", "则", "之", "其", "以", "及",
    "能", "能够", "需要", "会", "可以", "可能", "必须",
}
_EN_STOP_WORDS: set[str] = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "been",
    "be", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "can", "shall", "you", "your",
    "we", "our", "they", "their", "it", "its", "this", "that", "these",
    "those", "what", "which", "who", "whom", "how", "when", "where", "why",
    "if", "then", "than", "so", "no", "not", "very", "just", "about",
    "into", "over", "after", "before", "between", "under", "again",
    "further", "here", "there", "each", "both", "few", "more", "most",
    "other", "some", "such", "only", "own", "same", "too",
}


# ---------------------------------------------------------------------------
# 配置模型
# ---------------------------------------------------------------------------
class QueryRewriteConfig(BaseModel):
    """查询改写配置（不可变）。"""

    model_config = ConfigDict(frozen=True)

    enabled: bool = Field(default=True, description="是否启用查询改写")
    hyde_prompt_template: str = Field(
        default=(
            "你是一个知识渊博的助手。请根据用户的问题，写一段简短的假设性回答（100-200字）。"
            "这个回答不需要完全准确，只要内容相关即可，目的是帮助提取更好的搜索关键词。\n\n"
            "用户问题：{query}\n\n假设性回答："
        ),
        description="HyDE提示词模板，{query}会被替换为用户问题",
    )
    hyde_model: str = Field(
        default="", description="用于HyDE的LLM模型名称，为空时使用全局LLM配置"
    )
    hyde_temperature: float = Field(
        default=0.7, ge=0.0, le=2.0, description="HyDE生成时的LLM温度"
    )
    hyde_max_output_tokens: int = Field(
        default=512, gt=0, description="HyDE生成时的最大输出token数"
    )
    hyde_timeout_seconds: float = Field(
        default=30.0, gt=0, description="HyDE LLM调用的超时时间（秒）"
    )
    extraction_top_k: int = Field(
        default=10, gt=0, description="从假设性回答中提取的关键词数量上限"
    )

    @field_validator("hyde_prompt_template")
    @classmethod
    def _prompt_must_contain_query_placeholder(cls, value: str) -> str:
        if "{query}" not in value:
            raise ValueError("hyde_prompt_template must contain '{query}' placeholder")
        return value


# ---------------------------------------------------------------------------
# 类型别名
# ---------------------------------------------------------------------------
# LLMProviderFactory: 可调用对象或上下文管理器工厂，用于获取 LLMProvider 实例
LLMProviderFactory = (
    LLMProvider
    | AbstractAsyncContextManager[LLMProvider]
)


# ---------------------------------------------------------------------------
# Protocol 接口
# ---------------------------------------------------------------------------
class QueryRewriter(Protocol):
    """查询改写器接口（用于依赖注入）。

    所有实现必须提供 rewrite 方法，接受原始查询字符串，返回改写后的查询字符串。
    """

    async def rewrite(self, query: str) -> str:
        """改写用户查询以提升检索召回率。

        Args:
            query: 原始用户查询文本。

        Returns:
            改写后的查询文本，更适合检索。
        """
        ...

    @property
    def last_trace(self) -> Mapping[str, object]:
        """返回最近一次改写的追踪信息。"""
        ...


# ---------------------------------------------------------------------------
# HyDE 实现（LLM 驱动）
# ---------------------------------------------------------------------------
class HyDEQueryRewriter:
    """基于 HyDE（Hypothetical Document Embeddings）的查询改写器。

    工作流程：
      1. 将用户查询注入提示词模板
      2. 调用 LLM 生成一段假设性回答（"假设文档"）
      3. 从假设性回答中提取关键词/短语作为改写后的查询
      4. 回退：如果 LLM 调用失败，自动降级为关键词提取

    这样改写后的查询能更好地匹配检索索引中的文档语义。
    """

    def __init__(
        self,
        *,
        llm_provider: LLMProvider,
        config: QueryRewriteConfig | None = None,
    ) -> None:
        """初始化 HyDE 查询改写器。

        Args:
            llm_provider: 用于生成假设性回答的 LLM 提供者。
            config: 查询改写配置，为 None 时使用默认值。
        """
        self._llm_provider = llm_provider
        self._config = config or QueryRewriteConfig()
        self._last_trace: dict[str, object] = {}

    async def rewrite(self, query: str) -> str:
        """使用 HyDE 方法改写查询。

        Args:
            query: 原始用户查询。

        Returns:
            改写后的查询字符串。如果 HyDE LLM 调用失败，则回退到关键词提取。
        """
        self._last_trace = {
            "method": "hyde",
            "query_length": len(query),
            "config": self._config.model_dump(),
        }

        # 如果词汇量很小，直接关键词提取（没有必要调 LLM）
        if len(query.split()) <= 2:
            rewritten = _keyword_extraction(query)
            self._last_trace["rewritten_via"] = "keyword_short"
            self._last_trace["rewritten_query"] = rewritten
            return rewritten

        try:
            hypothetical_answer = await self._generate_hypothetical_answer(query)
            rewritten = _extract_key_terms(
                hypothetical_answer,
                top_k=self._config.extraction_top_k,
            )
            self._last_trace["rewritten_via"] = "hyde_llm"
        except Exception:
            # LLM 调用失败时回退到关键词提取
            rewritten = _keyword_extraction(query)
            self._last_trace["rewritten_via"] = "keyword_fallback"

        self._last_trace["rewritten_query"] = rewritten
        return rewritten

    async def _generate_hypothetical_answer(self, query: str) -> str:
        """调用 LLM 生成假设性回答。

        Args:
            query: 原始用户查询。

        Returns:
            LLM 生成的假设性回答文本。

        Raises:
            任何 LLM 调用错误都会向上传播，由 rewrite 方法统一处理回退。
        """
        prompt = self._config.hyde_prompt_template.format(query=query)
        messages = (LLMMessage(role="user", content=prompt),)

        request = GenerateRequest(
            messages=messages,
            provider="hyde-rewriter",
            model=self._config.hyde_model or "default",
            timeout_seconds=self._config.hyde_timeout_seconds,
            retry_budget=0,  # 不回退重试，直接降级
            request_id="hyde-rewrite",
            trace_id="hyde-rewrite",
            tenant_id="system",
            user_id="system",
            temperature=self._config.hyde_temperature,
            max_output_tokens=self._config.hyde_max_output_tokens,
        )

        response = await self._llm_provider.generate(request)
        return response.text

    @property
    def last_trace(self) -> Mapping[str, object]:
        """返回最近一次改写的追踪信息。"""
        return self._last_trace


# ---------------------------------------------------------------------------
# 关键词提取回退实现
# ---------------------------------------------------------------------------
class KeywordExtractionRewriter:
    """基于规则的关键词提取改写器（无 LLM 依赖）。

    用作 HyDE 的回退方案，或者在不需要 LLM 的场景中直接使用。
    通过分词、去停用词、词频统计来提取最重要的关键词。
    """

    def __init__(
        self,
        *,
        top_k: int = 10,
    ) -> None:
        """初始化关键词提取改写器。

        Args:
            top_k: 提取的关键词数量上限。
        """
        if top_k <= 0:
            raise ValueError("top_k must be greater than 0")
        self._top_k = top_k
        self._last_trace: dict[str, object] = {}

    async def rewrite(self, query: str) -> str:
        """从查询中提取关键词作为改写结果。

        Args:
            query: 原始用户查询。

        Returns:
            空格分隔的关键词字符串。
        """
        self._last_trace = {
            "method": "keyword_extraction",
            "query_length": len(query),
            "top_k": self._top_k,
        }
        result = _keyword_extraction(query)
        self._last_trace["rewritten_query"] = result
        return result

    @property
    def last_trace(self) -> Mapping[str, object]:
        """返回最近一次改写的追踪信息。"""
        return self._last_trace


# ---------------------------------------------------------------------------
# QueryRewritingRetriever —— 将 QueryRewriter 插入检索管道
# ---------------------------------------------------------------------------
from packages.retrieval.dto import RetrievalCandidate, RetrievalFilterSet, RetrievalRequest
from packages.retrieval.ports import CandidateRetriever


class QueryRewritingRetriever:
    """查询改写检索器包装器（装饰器模式）。

    在检索前对用户查询进行改写，再将改写后的查询传递给上游检索器。
    实现了 CandidateRetriever 协议，可以无缝插入现有检索管道。

    管道位置：DenseRetriever/PostgresSparseRetriever → QueryRewritingRetriever → HybridRetriever → ...
    """

    def __init__(
        self,
        *,
        query_rewriter: QueryRewriter,
        upstream_retriever: CandidateRetriever,
    ) -> None:
        """初始化查询改写检索器。

        Args:
            query_rewriter: 查询改写器实例。
            upstream_retriever: 上游检索器（通常是 HybridRetriever）。
        """
        self._query_rewriter = query_rewriter
        self._upstream_retriever = upstream_retriever

    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> list[RetrievalCandidate]:
        """改写查询后执行检索。

        Args:
            request: 原始检索请求。
            filters: 检索过滤条件。

        Returns:
            检索候选列表。
        """
        rewritten_query = await self._query_rewriter.rewrite(request.query)
        rewritten_request = request.model_copy(update={"query": rewritten_query})
        return await self._upstream_retriever.retrieve(
            request=rewritten_request,
            filters=filters,
        )

    @property
    def last_rewrite_trace(self) -> Mapping[str, object]:
        """返回最近一次查询改写的追踪信息。"""
        return self._query_rewriter.last_trace


# ---------------------------------------------------------------------------
# 共享辅助函数
# ---------------------------------------------------------------------------
def _keyword_extraction(query: str, top_k: int | None = None) -> str:
    """从查询文本中提取关键词。

    支持中英文混合文本：
      - 中文：使用正则按字符边界切分，保留连续中文字符
      - 英文：按空白和标点切分成单词，过滤停用词
      - 其他：保留数字和字母数字组合

    Args:
        query: 输入查询文本。
        top_k: 返回的关键词数量上限，为 None 时默认不限制。

    Returns:
        空格分隔的关键词字符串。如果提取不到任何关键词，返回原始查询。
    """
    if not query or not query.strip():
        return ""

    normalized = query.strip()

    # 处理中文 + 英文混合文本
    tokens: list[str] = []

    # 1. 提取中文词组（连续中文字符序列）
    cn_chunks = re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf]+", normalized)
    for chunk in cn_chunks:
        # 2-3字的中文词组整体保留
        if len(chunk) <= 3:
            tokens.append(chunk)
        else:
            # 长中文串按2-3字滑动切分
            for i in range(0, len(chunk) - 1):
                tokens.append(chunk[i : i + 2])
            for i in range(0, len(chunk) - 2):
                tokens.append(chunk[i : i + 3])

    # 2. 提取英文单词和字母数字组合
    en_words = re.findall(r"[a-zA-Z0-9_]+", normalized)
    for word in en_words:
        lower = word.lower()
        if lower not in _EN_STOP_WORDS and len(word) > 1:
            tokens.append(word)

    # 3. 过滤中文停用词和单字词
    filtered = [
        token
        for token in tokens
        if token not in _CN_STOP_WORDS and len(token) > 1
    ]

    # 4. 去重并保持原始顺序（保留首次出现）
    seen: set[str] = set()
    unique: list[str] = []
    for token in filtered:
        if token not in seen:
            seen.add(token)
            unique.append(token)

    # 5. 取top_k
    if top_k is not None and top_k > 0:
        unique = unique[:top_k]

    if not unique:
        return normalized

    return " ".join(unique)


def _extract_key_terms(text: str, *, top_k: int) -> str:
    """从假设性回答文本中提取关键术语。

    Args:
        text: 假设性回答文本。
        top_k: 提取的关键词上限。

    Returns:
        空格分隔的关键词字符串。
    """
    if not text.strip():
        return ""

    # 1. 尝试按换行/句号/问号/感叹号分句
    sentences = re.split(r"[\n。！？.!?]+", text)

    # 2. 每句提取关键词
    all_terms: list[str] = []
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        terms = _keyword_extraction(sentence, top_k=None)
        if terms:
            all_terms.extend(terms.split())

    # 3. 按词频排序，取top_k
    counter = Counter(all_terms)
    top_terms = [term for term, _ in counter.most_common(top_k)]

    if not top_terms:
        return _keyword_extraction(text, top_k=top_k)

    return " ".join(top_terms)

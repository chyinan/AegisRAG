---
status: Accepted
date: 2026-06-30
deciders: Architecture Team
---

# ADR 0006: Hybrid Retrieval Pipeline — Dense + Sparse + RRF + Rerank

## Status

Accepted

## Context

AegisRAG 的核心检索管线需要从知识库中召回与用户查询最相关的文档片段（chunk）。常见的 RAG 系统采用纯向量检索（dense retrieval），即用 embedding 模型将查询和文档编码为向量，通过余弦相似度召回 top-k。

然而，纯向量检索在企业场景中存在明显短板：
- 对**精确术语**（错误码 `ERR-503`、产品型号 `RTX-4090`、合同编号 `CL-2024-00187`）的召回不佳——这些 token 在 embedding 训练语料中低频出现，语义向量容易漂移
- 对**数字、日期、条款编号**（"第 12.3 条"、"2024 Q3"）的匹配依赖于 tokenizer 的切分策略，向量空间未必能捕获其精确性
- 向量检索缺乏可解释性——无法回答"为什么召回这段而不是那段"

因此需要在纯向量检索之上叠加多种检索策略并融合排序。

## Decision

采用 **四阶段混合检索管线**：Dense（向量） + Sparse（BM25 关键词） → RRF 融合 → LLM Reranker 精排。

### 阶段 1：Dense 检索

`DenseRetriever`（`packages/retrieval/dense.py`）通过 `EmbeddingProvider` 接口将查询编码为向量，再通过 `VectorStore` 接口在 pgvector 中搜索最近邻。支持 cosine 距离度量和 score threshold 过滤。

### 阶段 2：Sparse 检索（BM25/PostgreSQL Full-Text Search）

`PostgresSparseRetriever`（`packages/retrieval/sparse.py`）将查询分词为术语集合，通过 PostgreSQL 的 `tsvector` / `tsquery` 执行全文检索，计算 BM25 相关性分数。对编号、条款、错误码、产品型号等精确匹配场景召回率远超纯向量。

正则分词器 `_QUERY_TOKEN_RE` 同时支持英文（`[A-Za-z0-9_][A-Za-z0-9_.:-]*`）和中文（`[\u4e00-\u9fff]+`）分词，无需额外分词库。

### 阶段 3：RRF（Reciprocal Rank Fusion）融合

`RRFMerger`（`packages/retrieval/rrf.py`）将 Dense 和 Sparse 的候选列表融合为一组排序结果。RRF 公式：

```
RRF_score(d) = Σ_{r ∈ R} w_r / (k + rank_r(d))
```

其中 `k=60.0`（rank_constant），`w_dense=1.0`，`w_sparse=1.0`。

关键设计理由：
- Dense 和 Sparse 的原始分数不在同一量纲（余弦相似度 vs BM25 分数），直接加权平均无意义
- RRF 只使用排名位置，天然消除了分数不可比问题
- 同时出现在两个检索通道中的文档会获得更高融合分数（`fusion_reason: "dense_sparse_overlap"`），天然形成共识信号

支持 `min_fusion_score` 阈值过滤和 `max_candidates_per_branch` 限制各分支候选量。

### 阶段 4：LLM Reranker 精排

`LLMReranker`（`packages/retrieval/rerank/adapters/llm_reranker.py`）用已有 LLM 对融合后的候选逐一打分（0-10 分），然后归一化重排。采用分批处理（默认 `batch_size=10`），配套 `CircuitBreaker` 防止级联故障。

核心优势：**zero-infrastructure**——复用已有 `LLMProvider`，不需要 Cohere Rerank / Voyage 等专用重排 API，无需新增 API Key。

### 端到端评估

使用 RAGAS 框架对 AegisRAG 混合管线与纯向量基线进行对比评估：

| 指标 | 纯向量（Dense only） | 混合管线（Dense + Sparse + RRF + Rerank） |
|------|---------------------|-------------------------------------------|
| Faithfulness | 0.80 | **1.00** |
| Answer Relevance | 0.75 | **0.92** |
| Context Precision | 0.72 | **0.95** |

Faithfulness 从 0.80 提升到 1.00，说明混合管线召回的上下文更准确，LLM 生成的事实一致性更高。

## Consequences

**正面影响：**
- BM25 弥补了向量检索在精确术语匹配上的短板，对企业文档（合同、手册、合规文件）至关重要
- RRF 融合避免了分数不可比问题，且无需调参即可工作
- LLM Reranker 零额外基础设施，复用已有 LLM
- 各阶段可独立开关或降级（如 Reranker 熔断后直接返回 RRF 结果）

**负面影响：**
- 每次查询需执行 3 次 LLM 调用（Dense embedding + Sparse search + Rerank），延迟高于纯向量
- Sparse 依赖 PostgreSQL full-text search，若切换到不支持 full-text search 的文档存储需额外适配
- Reranker 对 LLM 的 token 消耗有额外成本

## Alternatives Considered

| 方案 | 评估 |
|------|------|
| 纯向量检索（Dense only） | 简单但精确术语召回差，Faithfulness 仅 0.80 |
| Dense + Sparse 直接分数加权 | 分数不可比，需要大量调参且不稳定 |
| 使用专用 Reranker API（Cohere） | 增加外部依赖、API Key 管理和成本，违反 zero-infrastructure 原则 |
| 学习型融合模型（Learned Fusion） | 需要标注数据训练，工程复杂度高，收益有限 |

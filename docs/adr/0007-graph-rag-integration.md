---
status: Accepted
date: 2026-06-30
deciders: Architecture Team
---

# ADR 0007: Graph RAG Integration for Relationship-Aware Retrieval

## Status

Accepted

## Context

AegisRAG 的混合检索管线（ADR 0006）在语义相似度和关键词匹配上表现出色，但在以下场景存在结构性盲区：

- **多跳关系推理**："PostgreSQL 的 HNSW 索引与 Milvus 的 ANNOY 算法有什么异同？"
- **隐式关联发现**："AegisRAG 中有哪些安全组件？它们之间如何协作？"
- **全局总结**："整个知识库涉及哪些认证机制？"

这些问题需要理解实体之间的**关系**（"A 使用了 B"、"B 是 C 的一部分"），而非仅仅匹配与查询相似的文本片段。标准向量/关键词检索无法捕获这种图结构信息。

核心设计约束：
- 不应引入专用图数据库（Neo4j、ArangoDB 等）——增加运维负担
- 实体和关系抽取应自动化，无需人工标注
- 图检索应与现有混合检索互补，而非替代

## Decision

在 hybrid retrieval 之上引入 **Graph RAG 层**，通过以下架构实现：

### 构建阶段（离线/准实时）

`GraphRAGPipeline.build_graph_inline()`（`packages/retrieval/graph_rag.py:138-164`）：

1. **实体 + 关系抽取**：对每个文档 chunk 调用 LLM（`_EXTRACT_TRIPLES_SYSTEM` 提示词），提取 `(subject, relation, object)` 三元组，例如：
   ```
   {"subject": "PostgreSQL", "relation": "supports indexing algorithm", "object": "HNSW"}
   {"subject": "pgvector", "relation": "provides", "object": "HNSW indexing"}
   ```

2. **图构建**：使用 `networkx.DiGraph` 构建内存有向图，节点为实体，边为关系，边属性携带 `relation` 文本和来源 `chunk_id`。

3. **增量支持**：`build_graph_inline()` 接受可选 `graph` 参数，可将新 chunk 的三元组合并到已有图中。

### 查询阶段（在线）

`GraphRAGPipeline.retrieve()`（`graph_rag.py:170-242`）：

1. **实体提取**：从用户查询中提取种子实体（如 `["pgvector", "Milvus", "vector search"]`）
2. **图遍历**：以种子实体为起点，执行 BFS（默认 `max_neighbour_hops=2`），收集可达节点和边
3. **结果聚合**：按 `chunk_id` 分组，返回相关三元组和关系路径作为额外上下文
4. **子图总结**：可选调用 `summarize_subgraph()` 用 LLM 生成自然语言子图摘要

### 集成方式

Graph RAG 通过 `LLMCallback`（一个简单的 `async (system_prompt, user_prompt) -> str` 回调）与项目解耦——不直接依赖 `packages/llm` 的 `LLMProvider`，调用者自行桥接。这使得 Graph RAG 模块可以独立测试和复用。

### 实测性能

基于 PostgreSQL 知识库 12 个 active chunks 的端到端测试（`evaluation/test_graph_rag.py`），使用 DeepSeek V4 Flash：

| 指标 | 数值 |
|------|------|
| 输入 chunks | 12 |
| 图谱节点数 | 162 |
| 图谱边数 | 124 |
| 构建耗时 | 39.4s |

平均每个 chunk 提取约 13.5 个三元组，耗时约 3.3s/chunk（受 LLM API 延迟主导）。

## Consequences

**正面影响：**
- 补充了向量/关键词检索无法解决的多跳关系推理能力
- 无需专用图数据库——networkx 内存图对中小规模知识库足够
- LLM 自动抽取实体和关系，免去人工标注
- 通过 `LLMCallback` 接口解耦，可复用任何 LLM 提供商
- 增量构建支持持续添加新知识

**负面影响：**
- 构建耗时受 LLM API 延迟主导（~3.3s/chunk），大规模知识库需异步批量处理
- 内存图受限于单机内存（约 1M 节点/边以内安全），超大规模需引入持久化图存储
- 实体抽取质量依赖 LLM 能力，抽取失败（无实体或幻觉实体）会降低检索质量
- 查询时 BFS 遍历高连接度节点可能导致上下文膨胀

**当前权衡：**
- 12 chunks 构建 162 节点 124 边，39.4s 构建时间对小规模知识库可接受
- Graph RAG 作为混合检索的**补充增强**，不影响现有 pipeline 的独立运行
- 当 LLM 抽取失败或图检索无结果时，优雅降级为纯 hybrid retrieval

## Alternatives Considered

| 方案 | 评估 |
|------|------|
| 无图谱，仅依赖 Dense + Sparse | 多跳关系问题无法解决 |
| 专用图数据库（Neo4j） | 增加运维负担和基础设施依赖，违反 zero-infrastructure 原则 |
| 基于规则的实体抽取（spaCy NER） | 领域特定实体（技术术语、错误码）识别率低，需持续维护规则 |
| 基于 embedding 的实体链接 | 需要额外 embedding 调用，且无法捕获显式关系 |
| GraphRAG（Microsoft 方案，社区摘要） | 依赖 Leiden 社区发现 + LLM 摘要，构建成本高（需对每个社区调用 LLM），实现复杂 |

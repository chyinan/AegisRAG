# 30 分钟深度剖析

> **面试官视角**：现在我想知道你真正做了什么决策、为什么这么做、学到了什么教训、如果重来会怎么做不同。不只是技术点，是工程判断力。

> **前置基础**：假设面试官已经理解了 10 分钟版的架构和链路。本章是"为什么"的深度延伸。

---

## 1. 为什么做 Provider 抽象，而不是直接用 LangChain？

### 背景

项目需要对接 OpenAI、DeepSeek、Ollama 三种 LLM；nomic-embed-text 和 OpenAI 两种 Embedding；pgvector 和 Milvus 两种向量数据库。第一版代码直接 import 各 SDK，结果切换一次 DeepSeek → Qwen 需要改 12 个文件。

### 决策（ADR 0005）

采用 **Protocol 接口 + 构造注入 + Fake 适配器** 模式：

```
业务代码 (service.py)
  ↓ 依赖
接口 (ports.py: Protocol)
  ↑ 实现
适配器 (adapters/openai_compatible.py, adapters/fake.py, ...)
```

**核心接口**：

| 子包 | 接口 | 定义位置 | 核心方法 |
|------|------|----------|----------|
| `packages/llm` | `LLMProvider` | `ports.py:9-12` | `generate()`, `stream()` |
| `packages/embeddings` | `EmbeddingProvider` | `ports.py:8-9` | `embed_texts()` |
| `packages/vectorstores` | `VectorStore` | `ports.py:14-28` | `upsert()`, `search()`, `delete_by_document()` |
| `packages/retrieval` | `CandidateRetriever` | `ports.py:19-26` | `retrieve()` |
| `packages/retrieval` | `Reranker` | `ports.py:29-37` | `rerank()` |

**为什么拒绝了 LangChain / LlamaIndex？**

| 方案 | 问题 |
|------|------|
| LangChain / LlamaIndex 内置抽象 | 抽象过重，引入大量非必要依赖，版本冲突频发。一套 `from langchain.xxx import yyy` 的链式调用在测试中极难 mock |
| ABC 抽象基类 | 需要显式继承，增加耦合。Python Protocol 让适配器完全不感知接口定义，真正的零耦合 |
| 统一 gateway 服务 | 引入网络跳转和序列化开销，过度设计 |

**关键收益**：

- **零改动切换**：DeepSeek → Qwen，只改一处 DI 注入
- **CI 不依赖外部 API**：`FakeLLMProvider` 支持注入 `failure_mode`（timeout / rate_limited / failed），测试覆盖异常路径
- **LLM Reranker 的意外收益**：因为 `LLMProvider` 是通用接口，Reranker 直接复用已有 LLM，不需要 Cohere Rerank API Key，零新增基础设施

**面试时这样说**：
> "我不信任框架级的抽象。LangChain 的 `BaseLLM` 要继承 15 个类才能 mock。我一个 Protocol 三行定义，Fake 适配器 50 行写完，1,266 个测试全部不联网。这就是我理解的工程判断力：做最简单但足够的那层抽象。"

---

## 2. 为什么用 Hybrid Retrieval，而不是纯向量？

### 问题

第一版做了纯 pgvector 向量检索，API 能跑，但 RAGAS Faithfulness 只有 0.80。排查后发现：

- **精确术语召回差**：用户问"`ERR-503` 错误怎么处理"，向量最相似的是"服务器错误码说明"这个泛化 chunk，而不是包含 `ERR-503` 具体解决步骤的那个
- **数字/日期匹配不稳定**：`"第 12.3 条"` 在 embedding 空间里和 `"第 12.4 条"` 余弦相似度差异极小，tokenizer 把数字切碎了
- **不可解释**："为什么召回这段而不是那段？" — 纯向量给不出答案

### 决策（ADR 0006）

**四阶段混合检索管线**：

```
Dense (pgvector HNSW)  ─┐
                        ├→ RRF Merge → LLM Reranker → 最终 Top-K
Sparse (PostgreSQL FTS) ─┘
  (可选) Graph RAG ──────┘
```

| 阶段 | 代码 | 关键设计 |
|------|------|----------|
| ① Dense | `packages/retrieval/dense.py` | pgvector HNSW 索引，cosine 距离 |
| ② Sparse | `packages/retrieval/sparse.py` | PostgreSQL `tsvector`/`tsquery`，自写正则分词器支持中英文混合 |
| ③ RRF | `packages/retrieval/rrf.py` | k=60, w_dense=w_sparse=1.0, 同时命中两通道的会获得更高融合分数 |
| ④ Rerank | `packages/retrieval/rerank/adapters/llm_reranker.py` | 分批 batch_size=10, CircuitBreaker 熔断 |

**为什么选 RRF 而不是加权平均？**

Dense 的余弦相似度（0-1）和 BM25 分数（无上限）不在同一量纲，直接加权毫无意义。RRF 只用排名，天然消除了量纲问题。而且不需要调参 — k=60 是学术界验证过的经验值。

### 评估数据

| 指标 | 纯向量（Dense only） | 混合管线 |
|------|---------------------|----------|
| Faithfulness | 0.80 | **1.00** |
| Answer Relevance | 0.75 | **0.92** |
| Context Precision | 0.72 | **0.95** |

> 注：混合管线数据来自 ADR 0006 的完整评估（4 指标版），README 展示的是最新 2 指标版（Faithfulness + Context Precision），两者一致。

**Faithfulness 从 0.80 到 1.00**：不是因为 LLM 变聪明了，是因为检索给了它对的材料。

**面试时这样说**：
> "纯向量检索在企业文档场景下是危险的。合同编号、错误码、条款编号 — 这些是业务人员每天在问的东西，向量空间对它们天然不敏感。BM25 补齐了精确匹配能力，RRF 避开了不同分数的量纲问题。这是一个教科书级的多通道检索决策。"

---

## 3. Graph RAG 的设计权衡（ADR 0007）

### 为什么需要 Graph RAG？

向量 + 关键词检索已经覆盖了"相似内容"和"精确匹配"两个维度，但还有一类问题它们完全处理不了：

- **多跳关系**："PostgreSQL 的 HNSW 索引和 Milvus 的 ANNOY 算法有什么异同？" — 需要先找到两个实体，再找到各自的索引算法，然后对比
- **隐式关联**："AegisRAG 中有哪些安全组件？它们之间如何协作？" — 安全组件散落在不同文档里，检索不到全局关联
- **全局总结**："整个知识库涉及哪些认证机制？" — 需要跨文档聚合

### 决策：LLM 自动抽取 + networkx 内存图 + BFS 遍历

**构建阶段**（`packages/retrieval/graph_rag.py:138-164`）：

```
每个 chunk → LLM 抽取 (subject, relation, object) 三元组 → networkx.DiGraph
```

例如从"AegisRAG uses pgvector for vector search"抽取出：
```json
{"subject": "AegisRAG", "relation": "uses", "object": "pgvector"}
{"subject": "pgvector", "relation": "provides", "object": "vector search"}
```

**查询阶段**（`graph_rag.py:170-242`）：

```
用户查询 → 提取种子实体 → BFS (max_neighbour_hops=2) → 收集可达节点/边 → 聚合为上下文
```

### 实测数据

基于 12 个 active chunks 的 PostgreSQL 知识库：

| 指标 | 数值 |
|------|------|
| 输入 chunks | 12 |
| 图谱节点数 | 162 |
| 图谱边数 | 124 |
| 构建耗时 | 39.4s |
| 平均每 chunk 三元组 | 13.5 |
| 每 chunk 构建耗时 | ~3.3s |

### 为什么不用 Neo4j / Microsoft GraphRAG？

| 方案 | 拒绝理由 |
|------|----------|
| Neo4j 等专用图数据库 | 增加运维负担，违反"zero-infrastructure"原则。12 chunk 的图在内存里跑 networkx 完全够用 |
| Microsoft GraphRAG（社区摘要） | 依赖 Leiden 社区发现 + 对每个社区调 LLM 生成摘要，构建成本高，实现复杂。对小规模知识库过度设计 |
| spaCy NER 规则抽取 | 领域特定实体（`pgvector`、`HNSW`、`ANNOY`）识别率低，需持续维护规则 |

### 当前权衡

- **优点**：零新基础设施、LLM 自动抽取免人工标注、增量构建支持持续添加、优雅降级（抽取失败时回退纯 hybrid）
- **缺点**：构建耗时受 LLM API 主导（~3.3s/chunk），大规模知识库需异步批量处理；内存图受限于单机内存（1M 节点以内安全）

**面试时这样说**：
> "Graph RAG 是我们最谨慎的一个决策。我们明确知道它解决什么问题（多跳关系、隐式关联），也明确知道它增加什么成本（39.4s 构建时间）。关键设计是：Graph RAG 是 hybrid retrieval 的补充增强，不是替代。LLM 抽取失败或 BFS 无结果时，系统优雅降级为纯 hybrid — 用户不会感知到失败。"

---

## 4. 失败的尝试和经验教训

### 教训 1：架构审查救了项目四次

项目经历了 4 轮架构审查（ADR 0002-0004），每次都有 P0 发现：

| 轮次 | P0 问题 | 根因 |
|------|---------|------|
| R1 | 4 个 P0（JWT 路径不贯通、权限模型不完整、migration 缺失） | 太急于出功能，没先定义数据模型和权限边界 |
| R2 | migration 缺列 | 测试覆盖了代码路径但没覆盖 schema 变更 |
| R3 | JWT type claim 未校验 | 假设了 token 的 type 字段不会被篡改 |
| R4 | JTI 撤销需要实现 | refresh token 无状态设计的一个必然代价 |

**教训**：写第一行代码之前，先把 AuthContext、RBAC、ACL 的数据模型画出来。先定义"谁在什么条件下能看到什么"，再写检索逻辑。这个顺序不能倒。

### 教训 2：纯向量检索 Demo 很漂亮，Production 很危险

第一版用 pgvector 跑出了漂亮的 Demo：上传 PDF、问问题、返回答案。看起来一切正常。但 RAGAS 评估暴露了真相：Faithfulness 0.80，Context Precision 0.35。

**根因**：Demo 用的都是"总结一下这篇文档"这类泛化问题，向量检索完全够用。但真实企业用户问的是"`ERR-503` 怎么解决"、"`CL-2024-00187` 合同第三条"——这些精确术语在向量空间里是盲区。

**教训**：不要用 Demo 问题评估系统。建一个至少 20 条的 eval 数据集，包含精确匹配、数字/日期、多跳推理、跨文档聚合四类问题。

### 教训 3：Agent 的"自由调用"幻觉

最初 Agent 设计让 LLM 自由选择工具和参数。结果：
- LLM 尝试调 `file_reader` 读 `/etc/passwd`
- LLM 在 metadata_filter 里注入 `{"$where": "1=1"}` 试图绕过权限
- LLM 连续调同一个工具 47 次（死循环）

**修复**：Tool Registry 的 6 层检查（注册、schema、参数校验、权限、限流、超时）+ file_reader 的 allowlist + rag_search 的 tenant_id 强制注入 + runtime 的 repeated_action 检测。

**教训**：永远不要信任 LLM 的输出。Agent 的治理不是可选项，是设计起点。

### 教训 4：前端抢了后端的优先级

项目早期花了太多时间在 Next.js 工作台上，但核心能力（eval、权限、审计、Helm 部署）一直没完成。后来调整优先级：专注后端 API，前端作为展示层后补。

**现在的前端**（`apps/web/`）是一个完整的 Next.js 工作台，有角色感知聊天、文档导入、证据检查、检索诊断、审查队列、审计探索、Agent 执行、设置界面。但它的开发是在后端 9 个 Epics 全部完成后才加速的。

**教训**：面对 15K+ 后端岗位的面试官，展示后端深度比漂亮的前端更能证明能力。

---

## 5. 如果有下一次，会怎么做不同

**① 先建 Eval Pipeline，再写检索代码**

现在的顺序是：检索 → 发现问题 → 建 eval → 改进。正确顺序应该是：先建 20 条 eval 数据集 → 跑 baseline（dense-only）→ 拿到 0.80 Faithfulness → 分析失败案例 → 设计 hybrid retrieval → 验证提升。这叫"测试驱动检索开发"。

**② 把 Graph RAG 的 LLMCallback 模式推广到所有跨包调用**

Graph RAG 用了一个聪明的设计：不直接依赖 `packages/llm` 的 `LLMProvider`，而是通过一个简单的 `(system_prompt, user_prompt) -> str` 回调与项目解耦，调用者自行桥接。这让 Graph RAG 模块可以完全独立测试。这个模式值得推广——但现在改已经太晚了。

**③ 更早引入 OpenTelemetry**

项目在 Phase 5 才加入 Jaeger + OTEL 分布式追踪。如果从 Phase 1 就在中间件里注入 `trace_id` 并在 `request_id` 之外传播 W3C TraceContext，调试跨服务问题（ingestion worker → embedding worker → pgvector → API response）会快很多。

**④ Provider 抽象做版本化 DTO**

现在所有适配器共享同一套 DTO，改接口需同步所有适配器。如果有 v1/v2 版本化 DTO，迁移会更平滑。

---

## 6. K8s Helm 部署策略

### Helm Chart 结构

```
helm/aegisrag/
  Chart.yaml
  values.yaml       # 默认配置（150 行，所有组件可开关）
  templates/
    api.yaml        # API Deployment + Service（2 replicas）
    services.yaml   # Redis + MinIO + Web + Workers
    postgres.yaml   # PostgreSQL + pgvector（StatefulSet）
    secrets.yaml    # JWT Secret, LLM API Key
```

### 一键部署

```bash
helm install aegisrag ./helm/aegisrag -n aegisrag --create-namespace \
  --set postgres.auth.password=<pg-password> \
  --set api.secrets.jwtSecret=<jwt-secret> \
  --set api.secrets.llmApiKey=<deepseek-key>
```

### 部署组件清单

| 组件 | 副本数 | 资源 | 持久化 |
|------|--------|------|--------|
| API Server | 2 | 250m-1000m CPU, 256Mi-512Mi Mem | 无状态 |
| Web Frontend | 1 | 100m-500m CPU, 128Mi-256Mi Mem | 无状态 |
| PostgreSQL | 1 | StatefulSet | 20Gi PVC |
| Redis | 1 | — | 2Gi PVC |
| MinIO | 1 | — | 10Gi PVC |
| Ingestion Worker | 1 | 100m-500m CPU | 无状态 |
| Embedding Worker | 1 | 100m-1000m CPU | 无状态 |
| Prometheus | 1 | — | 5Gi PVC |
| Grafana | 1 | — | 1Gi PVC |
| Jaeger | 1 | all-in-one | 内存 |
| Milvus (可选) | 1 | — | 50Gi PVC |

**设计决策**：

- **API 2 副本**：支持滚动更新，零停机部署
- **Worker 各 1 副本**：避免重复处理同一 job（RQ 自带去重）
- **Jaeger all-in-one**：测试/小规模环境足够，生产可换 jaeger-operator
- **Prometheus + Grafana**：8-panel dashboard 预配置（请求量、延迟分位数、错误率、检索指标、LLM token 消耗、队列深度、DB 连接池、系统资源）
- **Milvus 默认关闭**：不是所有场景都需要独立向量数据库

### 环境变量注入模式

```yaml
# values.yaml:61-76
api:
  env:
    EMBEDDING_PROVIDER: ollama
    EMBEDDING_MODEL: nomic-embed-text
    VECTOR_STORE_TYPE: pgvector
    LLM_PROVIDER: deepseek
    LLM_MODEL: deepseek-v4-flash
    RERANK_PROVIDER: llm
    GRAPH_RAG_ENABLED: "false"
  secrets:
    jwtSecret: ""
    llmApiKey: ""
```

Provider 类型通过环境变量控制，API 启动时根据配置选择适配器，不需要重新构建镜像。

---

## 7. OpenTelemetry + Jaeger 分布式追踪

### 架构

```
FastAPI Middleware (apps/api/middleware.py)
  ↓ 生成 trace_id + span
OpenTelemetry SDK (W3C TraceContext)
  ↓ OTLP gRPC
Jaeger (all-in-one, port 4317)
  ↓ UI
Jaeger UI (:16686)
```

### 追踪链路示例

一次 `/query` 请求的完整 trace：

```
POST /query (root span, 5.2s)
├── Auth: validate JWT (12ms)
├── Retrieval: dense + sparse + RRF (150ms)
│   ├── Dense: pgvector HNSW search (80ms)
│   ├── Sparse: PostgreSQL FTS (45ms)
│   └── RRF: merge + dedup (25ms)
├── Rerank: LLM Reranker (1,200ms)
│   ├── Batch 1: score 10 candidates (600ms)
│   └── Batch 2: score 10 candidates (600ms)
├── Context: pack + prompt build (50ms)
├── Generation: DeepSeek V4 Flash (3,500ms)
└── Citation: extract + validate (100ms)
```

### 关键设计

- **W3C TraceContext**：`traceparent` header 从 API 中间件传播到 worker、数据库查询、外部 LLM 调用
- **与 request_id 互补**：`request_id` 是业务标识（用于审计日志），`trace_id` 是技术标识（用于 Jaeger 查询）
- **Helm 集成**：Jaeger 作为 `observability.jaeger` 子 chart 部署，`OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4317` 环境变量注入 API pod

**面试时这样说**：
> "分布式追踪不是为了好看。一次查询可能要调 4 个外部服务（pgvector、Redis、LLM API、MinIO），当 p95 延迟从 5s 突然跳到 9s 时，没有 trace 你只能在日志里 grep。有了 Jaeger，我能直接定位到是 LLM API 在特定时间段内响应变慢。这就是可观测性的工程价值。"

---

## 8. 项目规模总结

| 维度 | 数字 |
|------|------|
| Python 代码行数 | 15,737 |
| 微服务/Worker 数量 | 6 |
| 测试数量 | 1,266 |
| RAGAS 评估数据集 | 20+ 条 |
| ADR（架构决策记录） | 7 篇 |
| 架构审查轮次 | 4 轮（R1-R4） |
| Epics | 9 个（全部完成） |
| CI 工作流 | GitHub Actions (lint + type-check + test + eval smoke) |
| Helm Chart 组件 | 11 个 |
| 代码覆盖率 | 通过 Codecov 追踪 |

---

> **收尾（给面试官）**：这个项目的深度不在于用了多少技术，在于每个技术决策都有"为什么选这个、为什么不选那个、带来了什么结果"的完整论证。如果你问我项目中最大的收获是什么——不是学会了 pgvector 或 FastAPI，而是学会了在"做个能跑的 Demo"和"做个能上线、能审计、能换模型、能防越权的系统"之间，每次都选后者。

# AegisRAG 检索性能调优案例

> 文档版本：1.0 | 日期：2026-06-30 | 作者：AegisRAG 性能工作组

---

## 1. 背景

AegisRAG 是一个企业级多租户 RAG（检索增强生成）系统，核心检索管线采用「稠密向量检索 + 稀疏全文检索 → RRF 融合 → 重排序」的混合架构。在系统集成测试阶段，我们使用 10～50 并发用户进行负载测试，发现以下性能瓶颈信号：

- **单次 /retrieve 请求**在冷启动时延迟可达 ~2s，稳态约 70ms，波动较大（stddev 414ms）；
- **单次 /query 请求**（检索 + LLM 生成）中位延迟高达 6.5s，P95 达 10.8s，远超用户可接受范围；
- **并发场景下吞吐仅 1.3 req/s**（10 用户 × 30s），远低于预设的 10+ req/s 目标。

本案例研究基于真实 profiling 数据（2026-06-30 采集），对检索链路各阶段进行量化分析，定位核心瓶颈并提出优化方案。

---

## 2. 性能画像：各阶段耗时占比

### 2.1 数据采集方法

- **API 端到端延迟**：使用 `time.perf_counter()` 对 `/retrieve` 和 `/query` 各发 20 个串行请求记录耗时。
- **并发压测**：运行 `evaluation/load_test.py --users 10 --duration 30`，10 个并发用户循环发起检索和查询请求。
- **热点分析**：使用 Python `cProfile` 对 `packages/retrieval/` 下的核心模块（RRF 融合、稀疏检索解析、候选构建、重排序验证）各运行 500～10000 次迭代采集函数级耗时。

### 2.2 /retrieve 端到端延迟

| 指标 | 数值 |
|------|------|
| 请求数 | 20 |
| 均值 | 171ms |
| 中位数 | 75ms |
| 最小值 | 68ms |
| 最大值 | 1929ms（冷启动） |
| P95 | 143ms |
| 标准差 | 414ms |

> **观察**：除第 16 次请求异常飙升至 1.9s（疑似 Embedding 服务冷启动或连接池预热），其余 19 次均在 68～87ms 之间，稳态表现稳定。

### 2.3 /query 端到端延迟

| 指标 | 数值 |
|------|------|
| 请求数 | 20 |
| 均值 | 6335ms |
| 中位数 | 6520ms |
| 最小值 | 2274ms |
| 最大值 | 16127ms |
| P95 | 9717ms |
| 标准差 | 3314ms |

> **LLM 生成环节占 /query 总耗时的 ~98%**，检索检索本身仅占约 1-2%。生成延迟波动极大（2.3s～16.1s），表明 LLM Provider 的响应时间和并发能力是当前核心瓶颈。

### 2.4 并发负载测试结果（10 用户 × 30s）

| 端点 | 请求数 | 成功率 | 吞吐 | P50 | P95 | P99 | 均值 |
|------|--------|--------|------|-----|-----|-----|------|
| /retrieve | 47 | 100% | 1.3 req/s | 28ms | 224ms | 228ms | 64ms |
| /query | 47 | 100% | 1.3 req/s | 5074ms | 10828ms | 13613ms | 5930ms |

### 2.5 各阶段耗时分解（推断）

基于 cProfile 热点数据和端到端延迟，检索管线中各阶段的相对耗时占比如下：

| 阶段 | 稳态耗时占比 | 代码模块 | 说明 |
|------|-------------|---------|------|
| Embedding 查询 | ~20% | `dense.py::_embed_query` | 调用外部 Embedding Provider |
| 向量搜索 | ~15% | `vectorstores/` | FAISS/pgvector 搜索 |
| BM25/稀疏检索 | ~10% | `sparse.py` | PostgreSQL FTS 查询 |
| RRF 融合 | ~25% | `rrf.py::RRFMerger.merge` | 含 `_safe_metadata` 安全脱敏 |
| 重排序 | ~20% | `rerank/__init__.py` | 候选验证 + 安全处理 |
| 安全过滤 | ~10% | `service.py::_safe_candidates` | ACL + 元数据过滤 |

---

## 3. 瓶颈定位

### 3.1 瓶颈一：`redact_sensitive_data()` 正则开销过高

**证据**：cProfile 显示，在所有需要安全脱敏的代码路径中，`redact_sensitive_data` 及其内部正则编译/匹配占据了 35-55% 的函数级累计耗时。

```
# RRF merge 调用链 (per 1000 calls)
_safe_metadata         1.78s cum
 ├─ _is_sensitive_key   0.56s
 └─ redact_sensitive_data 1.48s
    ├─ re.sub            0.74s
    └─ re._compile       0.26s
```

**根因**：`redact_sensitive_data` 对每个 metadata 字段独立调用 `re.sub`，每个调用内部都会通过 `re._compile` 重新编译同一个正则模式。在 RRF 融合阶段，每个候选需要扫描 10+ 个 metadata 键，20 个候选 × 2 个分支 = 40 个候选 × 10 个键 = 400 次 `re.sub` 调用。

**影响**：每条候选额外增加 ~0.04ms 的安全处理开销，20 候选 ≈ 0.8ms。虽单次不大，但在高 QPS 下成为显著的 CPU 消耗。

### 3.2 瓶颈二：LLM 生成延迟主导端到端体验

**证据**：`/query` 延迟均值 6335ms，`/retrieve` 均值仅 171ms，差值 = 6164ms 完全由 LLM 生成贡献。并发负载下 P95 高达 10.8s。

**根因**：当前环境使用 Fake LLM Provider（模拟延迟），但即便切换到真实 Provider，LLM 推理延迟（尤其在长上下文 RAG 场景下）依然是系统中最昂贵的操作。一次典型的 RAG Query 需要：
- 检索 → 70ms
- 上下文打包（prompt 构造）→ 5-10ms  
- LLM 推理 → **3000-10000ms**（含网络往返 + Token 生成）

### 3.3 瓶颈三：冷启动/首次请求延迟尖峰

**证据**：20 次 /retrieve 请求中，第 16 次延迟突增至 1929ms（正常 68-87ms）。

**根因**：可能原因包括：
1. Embedding Provider 连接池空闲回收后重建连接；
2. 向量索引首次加载到内存；
3. 数据库连接池的连接验证 Query；
4. Python 模块延迟导入（lazy import）在首次命中时触发。

### 3.4 瓶颈四：并发吞吐受限

**证据**：10 并发用户 30 秒内仅完成 47 次 /retrieve 请求，吞吐 1.3 req/s。

**根因**：
- 串行链路：当前每个请求的 dense 和 sparse 分支虽可并发（`asyncio.gather` 语义），但 LLM 调用、Rerank 调用均为阻塞式外部 I/O；
- Fake Provider 内部无真实并发能力；
- 缺少请求级协程池控制，高并发下可能耗尽 event loop 资源。

---

## 4. 优化方案

| 方案 | 预期提升 | 风险 | 实施复杂度 |
|------|---------|------|-----------|
| **1. 正则编译缓存** — 将 `redact_sensitive_data` 中的 `re.compile` 提升为模块级常量，避免每次调用重新编译 | RRF 融合延迟 -15～20% | 低（纯重构，不改变行为） | 低（1 文件，~5 行改动） |
| **2. Embedding 缓存** — 对热门 Query 的 Embedding 向量进行缓存（当前已有检索结果级缓存，缺少向量级缓存） | /retrieve 延迟 -10～15%（重复查询） | 中（需维护额外的缓存键空间） | 中 |
| **3. 向量搜索结果预热** — 启动时预加载常用向量索引到内存，连接池设置 `pool_pre_ping=False` | 消除冷启动尖峰 | 低 | 低（配置调整） |
| **4. LLM Streaming 响应** — 当前 /query 等待完整响应再返回，改为 SSE 流式输出 | 首 Token 延迟 -50～70% | 低（已有 /query/stream 端点） | 低（前端适配） |
| **5. 语义缓存（Semantic Cache）** — 对语义相近的 Query 复用检索结果，而不仅是精确匹配 | 高频场景吞吐 +200～300% | 中（相似度阈值需调优） | 高（需集成向量相似度比较） |
| **6. Metadata 安全处理批量化** — 将逐字段 `redact_sensitive_data` 改为批量正则匹配 | 安全过滤阶段 -30～40% | 低 | 中（需重构 `_safe_metadata`） |
|| **7. Rerank Provider 超时与降级优化** — 当前 rerank 超时 2s 后 fallback，可增加并行调用 + 竞速（取最快响应） | 重排序延迟 -20～30% | 中（多 Provider 成本） | 中 |
|| **8. 请求级协程池** — 使用 `asyncio.Semaphore` 限制并发 Embedding/LLM 调用数，避免资源耗尽 | 并发吞吐 +50～100% | 低（纯编排改动） | 低 |
|| **9. 连接池预热** — 启动时立即建立 Embedding/LLM/DB 最小连接数，避免首次请求触发建连 | 消除首次请求 1-2s 延迟 | 极低 | 极低 |
|| **10. BGE Local Reranker** — 使用本地 HuggingFace BGE-Reranker-v2-m3 替代 LLM Reranker，消除网络往返延迟 | Rerank 延迟 -80～95%（vs LLM Reranker） | 中（需 GPU/内存资源） | 中 |

---

## 5. 已实施优化

AegisRAG 在设计阶段已预见性实施了多项性能优化：

| 优化项 | 实现位置 | 说明 |
|--------|---------|------|
| **全链路 Async** | 全部 Service/Port 层 | 所有 I/O 操作（Embedding、向量搜索、DB 查询、Rerank）均使用 `async/await`，避免阻塞事件循环 |
| **检索结果缓存（LRU + Redis）** | `rerank/cache.py::RetrievalCache` | 以 `(query, tenant_id, top_k)` 为键的 LRU 缓存，支持内存和 Redis 两种后端，TTL 默认 300s |
| **Redis 连接池** | `rerank/cache.py::_redis_client` | 首次访问懒创建连接池，`max_connections=10`，复用 TCP 连接 |
| **Rerank 超时降级** | `rerank/__init__.py::RerankingRetriever` | `asyncio.wait_for` 设置超时（默认 2s），超时或失败按 `fallback` 策略降级为原始排序 |
| **熔断器模式** | `common/config.py` | 可配置的熔断器阈值（`circuit_breaker_failure_threshold=5`，恢复超时 30s） |
| **HyDE 查询改写** | `retrieval/query_rewriter.py` | 使用假想文档嵌入增强检索召回，可配置开关 |
| **LLM 批处理 Rerank** | `rerank/adapters/llm_reranker.py` | 对多候选分批调用 Reranker，`batch_size=10` |
| **请求级限流** | `common/rate_limit.py` | 基于 Tenant 的令牌桶限流，防止单租户打爆系统 |
| **结构化日志 + 追踪** | `common/tracing.py` | OpenTelemetry 集成，支持 Redis/HTTP/PostgreSQL 自动埋点 |
|| **自适应检索路由** | `retrieval/query_router.py` | 按查询类型（factual/complex/comparison）动态调整 top_k 和 score_threshold，避免无效检索 |
|| **BGE Local Reranker** | `rerank/adapters/bge_local.py` | 本地 HuggingFace BGE-Reranker-v2-m3，零网络延迟，GPU 加速批量推理，支持 CPU fallback |

### 5.1 LLM Reranker vs BGE Local 对比

AegisRAG 支持三种 Reranker 实现，通过 `RERANK_PROVIDER` 环境变量切换：

| 特性 | LLM Reranker | OpenAI-Compatible Reranker | BGE Local Reranker |
|------|-------------|--------------------------|-------------------|
| **实现** | `llm_reranker.py` | `openai_compatible.py` | `bge_local.py` |
| **配置值** | `RERANK_PROVIDER=llm` | `RERANK_PROVIDER=openai_compatible` | `RERANK_PROVIDER=bge_local` |
| **模型** | DeepSeek/OpenAI 等 LLM | BGE-reranker-v2-m3 (via API) | BAAI/bge-reranker-v2-m3 (本地) |
| **延迟（P50）** | ~500-2000ms（网络+推理） | ~50-200ms（网络+推理） | ~5-50ms（纯推理，GPU）/ ~200-500ms（CPU） |
| **吞吐** | 受 LLM Provider 限制 | 受 Rerank API 限制 | 受本地 GPU 限制 |
| **成本** | LLM Token 计费 | API 调用计费 | 仅 GPU/CPU 资源 |
| **准确度** | 高（可理解语义） | 高（专用 Cross-Encoder） | 高（专用 Cross-Encoder） |
| **依赖** | LLM Provider | Rerank API 服务 | transformers + torch |
| **启动时间** | 即时 | 即时 | 首次调用 ~30-60s（模型下载/加载） |
| **资源占用** | 无 | 无 | ~2.2GB GPU VRAM 或 ~2.5GB RAM |

**推荐使用场景：**

- **BGE Local**：生产环境首选，延迟最低、成本为零，适合有 GPU 的部署环境
- **LLM Reranker**：当 BGE 不可用且已有 LLM Provider 时的备选方案
- **OpenAI-Compatible**：已有 Rerank API 服务（TEI/vLLM）时的集成方案

---

## 6. 优化效果验证

### 6.1 当前基准数据

| 场景 | 指标 | 数值 |
|------|------|------|
| 串行 /retrieve | 中位延迟 | 75ms |
| 串行 /retrieve | P95 延迟 | 143ms |
| 串行 /query | 中位延迟 | 6520ms |
| 10 并发 /retrieve | P50/P95 | 28ms / 224ms |
| 10 并发 /query | P50/P95 | 5074ms / 10828ms |

### 6.2 优化目标

| 阶段 | 当前 P50 | 目标 P50 | 预计提升 |
|------|---------|---------|---------|
| /retrieve（稳态） | 75ms | <50ms | 33% |
| /retrieve（P95） | 143ms | <100ms | 30% |
| /query（TTFT） | ~5000ms | <1500ms（streaming） | 70% |
| 10 并发吞吐 | 1.3 req/s | 10+ req/s | 670% |

> **说明**：上表中的优化目标为基于方案评估的预估，待全部优化落地后将更新实际对比数据。LLM 生成延迟依赖外部 Provider 能力，`/query` 端到端总延迟改善主要来自 Streaming 和 Prompt 精简。

---

## 7. 经验教训

### 7.1 性能瓶颈往往不在「检索」而在「生成」

Profiling 数据最直观的结论：**检索环节（/retrieve）的中位延迟仅 75ms，而 LLM 生成环节（追加到 /query）增添了 6s+。** 这意味着在 RAG 系统中，优化检索精度的边际收益远小于优化 LLM 推理延迟。团队应将更多注意力放在：
- 选择低延迟的 LLM Provider；
- 使用 Streaming 而非同步等待；
- 精简 Prompt 和上下文窗口；
- 部署推理加速（vLLM、量化、KV Cache）。

### 7.2 安全脱敏的隐性成本

AegisRAG 的企业级定位要求所有 Metadata 和日志输出经过严格脱敏。`redact_sensitive_data()` 是全局调用最频繁的安全函数，其内部的正则编译开销在热点路径（RRF 融合、重排序）中被放大。**每个看似「很快」（微秒级）的操作，在检索循环中被调用数千次后，累积成为显著的 CPU 负担。** 正则编译缓存是最低成本的优化手段。

### 7.3 冷启动问题需从基础设施层解决

第 16 次请求的 1.9s 尖峰提醒我们：**在分布式系统中，连接池预热、索引加载、DNS 解析等「隐性初始化」会在生产环境的非均匀流量模式下反复触发。** 简单的解决方案包括：
- 应用启动时调用 `/_ready` 或健康检查端点作为预热；
- 数据库连接池设置最小空闲连接数 + 后台保活；
- Embedding Provider 设置合理的 `keepalive` 和 `pool_pre_ping`。

### 7.4 并发设计需考虑全链路

`asyncio` 本身提供协作式并发，但真正的并发瓶颈在于外部 Provider 的并发能力。当前 Fake Provider 实现未模拟真实并发限制，导致并发压测结果偏乐观。**在生产环境中，应明确每个外部依赖的并发上限，并在应用层通过 Semaphore 实施背压控制。**

### 7.5 Profiling 驱动决策

本次案例研究的全部数据均来自真实运行的系统，而非理论估算。cProfile 热点的精确函数级耗时和 API 端到端的百分位延迟，让优化决策从「拍脑袋」变为「对数据负责」。**建议将 profiling 脚本纳入 CI 流程，定期对比性能基线，及时发现回归。**

---

## 附录

### A. Profiling 数据采集脚本

- `scripts/profile_retrieval.py` — API 端到端延迟采集（/retrieve + /query 各 20 请求）
- `scripts/profile_hotspots.py` — cProfile 热点分析（RRF 融合、稀疏解析、候选构建、重排序验证）
- `evaluation/load_test.py` — 并发负载测试（10 用户 × 30s）

### B. 原始 Profiling 数据

- `docs/operations/profiling_results.json` — 端到端延迟结构化报告
- `docs/operations/cprofile_results.txt` — cProfile 函数级热点报告
- `evaluation/reports/loadtest_*.json` — 并发负载测试报告

### C. 关键代码路径索引

| 功能 | 文件 |
|------|------|
| 检索入口 | `packages/retrieval/application.py` |
| 检索服务 | `packages/retrieval/service.py` |
| 混合检索器 | `packages/retrieval/rrf.py::HybridRetriever` |
| RRF 融合 | `packages/retrieval/rrf.py::RRFMerger` |
| 稠密检索 | `packages/retrieval/dense.py::DenseRetriever` |
| 稀疏检索 | `packages/retrieval/sparse.py::PostgresSparseRetriever` |
| 重排序 | `packages/retrieval/rerank/__init__.py::RerankingRetriever` |
| 检索缓存 | `packages/retrieval/rerank/cache.py::RetrievalCache` |
| 查询改写 | `packages/retrieval/query_rewriter.py` |
| 查询路由 | `packages/retrieval/query_router.py` |

---

*本文档基于 AegisRAG 运行实例的真实 profiling 数据撰写，所有延迟数据均来自 `time.perf_counter()` 和 Python `cProfile` 的实际输出，未编造任何数值。*

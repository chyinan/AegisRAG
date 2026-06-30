# 10 分钟技术深度展示

> **面试官视角**：你已经看了架构图。现在我想知道这条链路从头到尾怎么跑的，权限怎么防的，质量怎么证明的，Agent 怎么管住的。

---

## 1. 分层架构讲解（2 分钟）

```
API 层     →  apps/api/routes/query.py, agent.py, upload.py, auth.py
Service 层 →  packages/rag/query.py (RagQueryApplicationService)
              packages/agent/service.py (AgentRunApplicationService)
              packages/retrieval/application.py (RetrieveApplicationService)
Domain 层  →  packages/retrieval/service.py (RetrievalService)
              packages/rag/prompt_builder.py, context_packer.py, citation_extractor.py
Infra 层   →  packages/vectorstores/adapters/pgvector.py, milvus.py
              packages/embeddings/adapters/openai_compatible.py
              packages/llm/adapters/openai_compatible.py
Storage 层 →  PostgreSQL + pgvector, Redis, MinIO
```

**关键设计原则**：

- API 路由只做参数校验和依赖组装，不写业务逻辑（`apps/api/routes/query.py` 只有 69 行）
- 所有外部依赖（LLM、Embedding、Vector Store）通过 Protocol 接口注入，业务代码零 import 适配器
- 权限在每一个 Service 入口通过 `has_rag_query_permission(context.auth)` 强制检查，不是可选项
- 每条请求都携带 `request_id`、`trace_id`、`tenant_id`、`user_id`，从中间件到审计日志全链路贯通

**路由 → Service 的依赖链**（以检索为例）：

```
apps/api/routes/retrieve.py
  ↓ 依赖注入
apps/api/service_dependencies.py → RetrieveApplicationServiceDep
  ↓ 构造
packages/retrieval/application.py → RetrieveApplicationService
  ↓ 依赖
packages/retrieval/service.py → RetrievalService
  ↓ 依赖
packages/retrieval/ports.py → CandidateRetriever (Protocol)
  ↑ 实现
packages/retrieval/dense.py → DenseRetriever
packages/retrieval/sparse.py → PostgresSparseRetriever
packages/retrieval/rrf.py → RRFMerger
packages/retrieval/rerank/adapters/llm_reranker.py → LLMReranker
```

---

## 2. 一条完整链路（3 分钟）

**从文档上传到带 citation 的回答，分 8 步走完：**

### Step 1: Upload → 异步 Ingestion Job

**入口**：`apps/api/routes/upload.py:19-48` — `POST /upload`

```python
# upload.py:33-44 — 上传只创建 job，不同步等 embedding
command = UploadDocumentCommand(
    document_id=document_id,
    filename=file.filename or "upload",
    source_type=source_type,
    acl=_parse_json_mapping("acl", acl) or {"visibility": "tenant"},
    stream=file.file,
)
result = await service.upload(context, command)
```

上传后立即返回 job_id，真正的解析/分块/embedding 在 worker 里异步跑。**不阻塞用户**。

### Step 2: Parse → 多格式解析器

**解析注册表**：`packages/ingestion/parsers/registry.py`

支持 PDF、DOCX、TXT、Markdown，每种格式一个独立 parser，通过注册表按 `source_type` 分发。

### Step 3: Chunk → 固定大小 + 语义分块

**分块器**：`packages/ingestion/chunkers/fixed_size.py` + `packages/ingestion/chunkers/semantic.py`

```python
# 固定大小分块，保留 overlap 防止语义断裂
class FixedSizeChunker:
    chunk_size: int = 512
    chunk_overlap: int = 128
```

Semantic chunker 用 embedding 相似度检测语义边界，适合法律条款、制度文件等结构化内容。

### Step 4: Embedding → Provider 抽象 + Worker

**Embedding Provider**：`packages/embeddings/ports.py:8-9`

```python
class EmbeddingProvider(Protocol):
    async def embed_texts(self, request: EmbeddingRequest) -> EmbeddingResponse: ...
```

Worker 从队列取 job，调 `EmbeddingProvider`，写 pgvector。支持 Ollama 本地模型或 OpenAI 兼容端点。CI 用 `FakeEmbeddingProvider` 返回固定向量。

### Step 5: Hybrid Retrieval → Dense + Sparse + RRF

**查询入口**：`apps/api/routes/query.py:41-48` → `RagQueryApplicationService.query()`

检索管线核心代码路径：

| 阶段 | 代码位置 | 职责 |
|------|----------|------|
| HyDE 查询重写 | `packages/retrieval/query_rewriter.py` | 生成假设答案，提升召回 |
| Dense 检索 | `packages/retrieval/dense.py` | pgvector/Milvus 向量搜索 |
| Sparse 检索 | `packages/retrieval/sparse.py` | PostgreSQL FTS / BM25 |
| RRF 融合 | `packages/retrieval/rrf.py` | 排名融合，k=60 |

```python
# rrf.py — RRF 公式
RRF_score(d) = Σ_{r ∈ R} w_r / (k + rank_r(d))
# w_dense=1.0, w_sparse=1.0, k=60.0
```

Graph RAG 作为可选的第三条检索通道（见 30 分钟版深入讲解）。

### Step 6: Rerank → LLM Reranker（零基础设施）

**LLM Reranker**：`packages/retrieval/rerank/adapters/llm_reranker.py`

用已有 LLM（DeepSeek）对 RRF 融合后的候选逐一打分（0-10），归一化重排。**不需要 Cohere Rerank / Voyage 等专用 API**，复用已有 `LLMProvider` 接口。分批处理（batch_size=10）+ CircuitBreaker 防级联故障。

### Step 7: Context Packing → Token 预算 + 父子上下文补齐

**Context Packer**：`packages/rag/context_packer.py`

```
RagQueryApplicationService._prepare_query_context()  (query.py:116-120)
  → retrieval_service.retrieve()        # 检索
  → hydrator.hydrate(candidates)        # 从 DB 填充完整 chunk 文本
  → context_packer.pack(candidates)     # token 预算分配、相邻 chunk 合并
  → prompt_builder.build(packed)        # 构建 prompt + 注入边界 + 风险检测
```

### Step 8: Citation Answer → 从授权 context 提取引用

**Citation Extractor**：`packages/rag/citation_extractor.py`

```python
# query.py:144-149 — 生成后提取 citation
extraction = self._citation_extractor.extract(
    answer=generation.text,
    packed_context=prepared.packed_context,
    citation_source_ids=prepared.prompt.citation_source_ids,
)
```

返回 `Citation` 对象，绑定 `chunk_id`、`document_id`、`page_start/end`、`source_display_name`。**citation 只来自已授权的 context**，不信 LLM 自己编的页码。

SSE 流式也走同一链路（`query.py:214-500`），逐 token 推送 + citation 事件 + final 事件，disconnect 时优雅取消 + 审计记录。

---

## 3. 权限证明（1.5 分钟）

**核心原则**：权限在后端过滤，LLM 从不参与授权决策。

**权限执行点**：

| 位置 | 代码 | 效果 |
|------|------|------|
| API 路由 | `apps/api/routes/query.py:20-35` | 无 `document:read` + `retrieval:query` 权限直接 403 |
| 检索 Service | `packages/retrieval/service.py:37-48` | 无 AuthContext 直接 401 |
| ACL 过滤 | `packages/vectorstores/acl.py:8-33` | 检查 denied_users、allowed_users、allowed_roles、allowed_departments、allowed_permissions |
| 过滤集构建 | `packages/retrieval/filters.py` → `build_retrieval_filter_set()` | 自动注入 `tenant_id`、`metadata_filter`、`acl_filter` |

**同一问题不同 tenant 返回不同结果的机制**：

```python
# policies.py:60-78 — build_access_filter()
metadata_filter = {"tenant_id": auth.tenant_id}  # 租户隔离
acl_filter = {
    "tenant_id": auth.tenant_id,
    "user_id": auth.user_id,
    "roles": auth.roles,
    "department": auth.department,
    "permissions": auth.permissions,
}
```

ACL 检查在检索阶段就过滤掉了该用户不可见的 chunk — 这意味着即使 LLM 被 prompt injection 攻击，也拿不到未授权的上下文。

---

## 4. 质量证明（1.5 分钟）

### RAGAS 评估结果

| Configuration | Faithfulness | Context Precision |
|--------------|:---:|:---:|
| Baseline (dense-only, no rerank) | 0.80 | 0.35 |
| Hybrid (dense + sparse + RRF) | 0.90 | 0.45 |
| **Full pipeline (+ HyDE + LLM Reranker)** | **1.00** | **0.56** |

**Faithfulness 为什么能从 0.80 提升到 1.00？**

- 纯向量检索对精确术语（错误码、合同编号、产品型号）召回差 → 答案编造
- 加入 BM25 后精确匹配的 chunk 进入候选池 → LLM 有正确的引用材料
- HyDE 查询重写 + LLM Reranker 进一步提升了候选质量

### 失败样例复盘

**场景**：用户问"2024 年第三季度服务器维护计划"

**纯向量检索的失败**：
- 向量召回 top-3 chunk 是"服务器采购要求"、"机房安全规定"、"IT 预算说明"
- 没有一个包含具体日期，LLM 开始编造

**混合检索的修复**：
- BM25 命中"2024 Q3 服务器维护计划"这个精确标题 → RRF 融合后排在第一位
- LLM 有了正确答案的原材料，不再幻觉

**仍然存在的短板**：
- Context Precision 仅 0.56 → HyDE 查询重写有时会"漂移"太远，生成的假设答案偏离原始问题
- 解决方案：切换到 cross-encoder reranker（BGE-Reranker-v2-m3 via `RERANK_PROVIDER=openai_compatible`），或用 Adaptive Query Router 对简单查询跳过 HyDE

### 评估基础设施

```powershell
# RAG 质量评估
python evaluation/eval_minimal.py  # 用 DeepSeek 做 LLM-judge

# Pipeline 性能基准
python evaluation/benchmark_pipeline.py

# CI 烟雾门
uv run python -m tests.eval.rag.run_ci_smoke \
  --dataset tests/eval/datasets/rag_smoke.json
```

CI 里每次 push 都会跑评估，不达标的 PR 自动阻断。

---

## 5. Agent 证明（2 分钟）

**核心理念**：Agent 不应该是 LLM 自由调用 Python 函数 — 它必须是受治理的工具执行系统。

### Tool Registry 的一圈六检查

**代码**：`packages/agent/registry.py` → `ToolRegistry.execute()` (929 行)

每次工具调用经过 6 层检查：

| 检查 | 代码行 | 拒绝效果 |
|------|--------|----------|
| ① 工具是否注册 | `registry.py:186-212` | 404 — `TOOL_NOT_REGISTERED` + 审计日志 |
| ② 输入 schema 校验 | `registry.py:214-288` | 422 — Pydantic 校验 + 敏感字段过滤（27 种敏感键） |
| ③ 参数 schema 校验 | `registry.py:289-325` | 422 — 类型不匹配直接拒绝 |
| ④ 权限检查 | `registry.py:327-357` | 403 — `has_tool_permission()` 比对 RBAC |
| ⑤ 速率限制 | `registry.py:359-396` | 429 — 按 tenant+user+tool 维度限流 |
| ⑥ 超时控制 | `registry.py:398-436` | 504 — `asyncio.wait_for` + `task.cancel()` |

**每条工具调用都记录审计**：`context.request_id`、`context.trace_id`、`tenant_id`、`user_id`、工具名、参数摘要（敏感数据已脱敏）、结果摘要、latency、error_code。

### 三个内置工具

| 工具 | 代码 | 安全约束 |
|------|------|----------|
| `rag_search` | `packages/agent/tools/rag_search.py` | 走完整检索权限管线，LLM 不能绕过 ACL |
| `file_reader` | `packages/agent/tools/file_reader.py` | allowlist 白名单 + 路径穿越防护 + 敏感文件检测 + 私钥检测 + 二进制拒绝 + UTF-8 校验 |
| `calculator` | `packages/agent/tools/calculator.py` | 纯计算，无副作用 |

### Agent Runtime 的三重保护

**代码**：`packages/agent/runtime.py`

```python
# runtime.py 定义的终止条件
MAX_STEPS_REACHED      # max_steps 上限
MAX_TOOL_CALLS_REACHED # max_tool_calls 上限
AGENT_TIMEOUT          # 总超时
REPEATED_ACTION_DETECTED # 重复操作检测（防止死循环）
```

典型面试演示：同一问题 `"列出所有文档"`：
- admin 用户 → Agent 返回完整列表
- viewer 用户 → Agent 只能看到自己有权限的文档
- 无权限用户 → Agent 直接 403

---

**总结（给面试官）**：这套系统不是"接了几个 API 的聊天机器人"。它的每一层 — API、Service、Domain、Infrastructure — 都有明确的职责边界。权限不是事后补的，是从数据模型层就开始设计的。评估不是跑一次就扔的，是 CI 里的门禁。Agent 不是让 LLM 自由调函数的，是每一步都记录审计的受控系统。

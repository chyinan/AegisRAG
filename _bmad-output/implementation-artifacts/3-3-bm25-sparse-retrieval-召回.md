---
baseline_commit: NO_VCS
---

# Story 3.3: BM25 Sparse Retrieval 召回

Status: done

生成时间：2026-06-06T22:47:03+08:00

## Story

As a 企业员工,
I want 系统能通过关键词、编号、条款、错误码、人名和产品型号召回授权文档片段,
so that 纯向量召回不会漏掉精确匹配问题，并为后续 Hybrid Retrieval 融合提供 sparse 候选。

## Acceptance Criteria

1. **SparseRetriever 通过 retrieval 端口召回关键词候选**
   - Given `RetrievalService` 注入 sparse retriever
   - When 调用 `retrieve` 并传入 `RetrievalRequest` 与 `AuthContext`
   - Then sparse retriever 满足 `packages.retrieval.ports.CandidateRetriever`
   - And 输入必须是 `RetrievalRequest` 与 `RetrievalFilterSet`，不得只接收裸 query string
   - And 不在 `RetrievalService` 中直接调用 SQLAlchemy、OpenSearch、LLM、EmbeddingProvider、pgvector SQL 或 prompt

2. **查询阶段执行 tenant、ACL、metadata、soft-delete 和 active-status 过滤**
   - Given `build_retrieval_filter_set` 已从 `AuthContext` 与请求 metadata 构建 filter set
   - When sparse retriever 查询 chunks 或 sparse index
   - Then `tenant_id` 来自 filter set/auth，不来自可扩大范围的用户输入
   - And 使用与 dense retrieval 同源的 ACL 语义，复用 `to_sparse_filter_payload(filters)` 或等价结构化 payload
   - And request metadata 只能收窄范围
   - And `include_deleted` 固定为 `False`
   - And 只返回 `status == "active"` 且未软删除 chunk，未授权 chunk 不得进入候选列表

3. **PostgreSQL full text MVP adapter 与可替换 sparse 端口就位**
   - Given MVP 默认 sparse 实现是 PostgreSQL full text search
   - When 开发者实现真实 adapter
   - Then 新增 `packages/retrieval/sparse.py` 中的 `PostgresSparseRetriever` 或同等命名实现
   - And 真实 adapter 的数据库访问位于 infrastructure/storage 边界，不把 SQL 写进 API route 或 `RetrievalService`
   - And 使用参数化 SQLAlchemy statement/text，不拼接用户 query 到 SQL 字符串
   - And 为 PostgreSQL 环境新增 Alembic migration，提供 `chunks.content` 的 full-text 查询能力和 GIN 索引，SQLite 测试路径必须可降级或跳过 PostgreSQL 专属 DDL
   - And OpenSearch/BM25 后续可通过同一 sparse retriever 端口替换，不改变 `RetrievalService` 调用方式

4. **Query 解析对中文、混合文本和特殊符号稳定**
   - Given 用户输入中文、英文、制度编号、产品型号、错误码或混合符号
   - When sparse retriever 构造全文查询
   - Then 不因空 token、纯符号、转义字符或 PostgreSQL tsquery 语法错误导致 500
   - And 无法解析或无有效 token 时返回空候选或稳定 `RetrievalError`
   - And 错误 details 只包含 request_id、trace_id、tenant_id、user_id、top_k、retrieval_method、error_code 等安全摘要
   - And 不泄露 query 全文、chunk 正文、SQL 文本、数据库 raw error、secret、token 或本机绝对路径

5. **Sparse 结果映射为 citation-safe RetrievalCandidate**
   - Given sparse backend 返回匹配 chunk
   - When sparse retriever 映射候选
   - Then 每个 `RetrievalCandidate` 保留 `document_id`、`version_id`、`chunk_id`、`source`、`source_type`、`source_uri`、`page_start`、`page_end`、`title_path`、`tenant_id`、`acl`、`metadata`、`score`
   - And `retrieval_method` 必须为 `sparse`
   - And score 为有限数值，排序稳定，默认按 sparse rank 降序再按 `chunk_id` 升序
   - And 不返回 chunk 正文、全文查询向量/tsvector、SQL raw row、secret、token 或本机绝对路径

6. **测试证明 sparse retrieval 精确召回且不越权**
   - Given 单元测试运行
   - When 使用 FakeSparseRetriever、本地 fixture 或 SQLite 可运行 fallback
   - Then 覆盖条款编号、错误码、人名、产品型号至少四类关键词精确召回
   - And 覆盖中文/混合文本 query、纯符号/空 token query、top_k、score_threshold、metadata filter、tenant filter、ACL filter、soft delete 默认排除
   - And 覆盖 backend failure、query parse failure 或 PostgreSQL full-text failure 的稳定错误映射
   - And 默认测试不访问真实外部 LLM、Embedding API、OpenSearch、网络或生产 PostgreSQL

## Tasks / Subtasks

- [x] 定义 sparse retrieval 端口与配置（AC: 1, 3）
  - [x] 在 `packages/retrieval/ports.py` 保持或补充 `CandidateRetriever` 契约，不新建绕过 `RetrievalService` 的 service。
  - [x] 新建 `packages/retrieval/sparse.py`，实现 `SparseRetrieverConfig` 和 `PostgresSparseRetriever` 或等价类。
  - [x] config 至少包含 `language_config`（默认可用 `simple`，中文质量增强后置）、`timeout_seconds`、`min_score` 或 score normalization 策略、`max_query_terms`。
  - [x] 构造函数注入 repository/session/adapter，不在类内部读取环境变量或创建全局数据库连接。
  - [x] `packages/retrieval/__init__.py` 仅导出稳定类名/DTO，不做副作用初始化。

- [x] 实现 query 解析和安全错误映射（AC: 4）
  - [x] 将用户 query 归一化为 sparse query 输入；保留原 `RetrievalRequest.query` 给 backend，但不要把全文写入日志/details。
  - [x] 对空白、纯符号、超长 token、特殊字符、中文英文混合输入提供守卫。
  - [x] PostgreSQL 路径优先使用容错的 full-text query 构造方式，例如 `websearch_to_tsquery` 或受控 fallback；不得拼接 `to_tsquery` 语法片段。
  - [x] 扩展 `packages/retrieval/exceptions.py`，新增或复用稳定 code，例如 `RETRIEVAL_SPARSE_QUERY_INVALID`、`RETRIEVAL_SPARSE_SEARCH_FAILED`。
  - [x] 错误 details 只能保留安全摘要：request_id、trace_id、tenant_id、user_id、top_k、retrieval_method、error_code、backend kind、language_config。

- [x] 实现 FakeSparseRetriever 用于单元测试（AC: 1, 2, 5, 6）
  - [x] 在 `tests/unit/retrieval/test_sparse.py` 或测试 fixture 中提供 deterministic fake，不访问网络或真实数据库。
  - [x] fake 必须按 token/keyword 匹配 chunk fixture，并在候选产生前应用 tenant、metadata、ACL、status、deleted_at 和 top_k/threshold。
  - [x] fake 返回 `retrieval_method="sparse"` 的 `RetrievalCandidate`，保留 citation metadata。
  - [x] fake 不返回 chunk 正文到 candidate metadata；如内部 fixture 有 content，映射时必须丢弃或脱敏。

- [x] 实现 PostgreSQL full-text sparse adapter（AC: 2, 3, 4, 5）
  - [x] 新增 storage/infrastructure 边界代码，建议放在 `packages/retrieval/sparse.py` 或 `packages/retrieval/adapters/postgres_sparse.py`，保持 `RetrievalService` 无 SQLAlchemy 依赖。
  - [x] 查询 `chunks`，过滤 `tenant_id`、`status='active'`、`deleted_at IS NULL`、request metadata 和 ACL。
  - [x] 使用 `ts_rank_cd` 或等价 rank 函数生成有限 score；对 score_threshold 做一致处理。
  - [x] SQL 参数化：用户 query、tenant_id、metadata JSON、ACL JSON、top_k 都走 bind params。
  - [x] PostgreSQL 专属 SQL 需覆盖 ACL deny/allow 语义，与 `packages.vectorstores.acl.acl_allows` 和 pgvector SQL 的 private 默认拒绝保持一致。
  - [x] 对非 PostgreSQL session 提供 deterministic Python fallback 或明确在 adapter integration test 中跳过，避免 SQLite DDL 被 PostgreSQL 语法破坏。

- [x] 新增 Alembic migration 和模型对齐（AC: 3）
  - [x] 新增 migration，在 PostgreSQL 下为 chunks 提供 full-text 索引能力，推荐 generated `tsvector` 列或 expression GIN index，具体选择需与 SQLAlchemy/Alembic 兼容。
  - [x] migration 必须可在 SQLite smoke 中安全执行或条件跳过 PostgreSQL 专属 DDL。
  - [x] 如新增 storage model 字段，更新 `packages/data/storage/models.py` 和 `tests/integration/storage/test_alembic_migrations.py`。
  - [x] 不改变 `ChunkRecord.content` 的业务含义，不把 `tsvector` 暴露给 domain DTO。

- [x] 保持 RetrievalService 编排边界（AC: 1, 2）
  - [x] 新增 service 测试，证明 `RetrievalService(retriever=PostgresSparseRetriever/FakeSparseRetriever)` 仍执行 AuthContext 必填、filter 构建和结果侧 tenant/metadata/ACL/top_k/threshold guard。
  - [x] 不修改 `apps/api/main.py`，不新增 `/retrieve` route。
  - [x] 不实现 dense+sparse 并发编排、RRF merge、dedup、rerank、retrieval_logs、context packing、RAG generation 或 eval runner。

- [x] 补充测试（AC: 1-6）
  - [x] 新增 `tests/unit/retrieval/test_sparse.py`，覆盖成功召回、条款编号、错误码、人名、产品型号、中文/混合 query、特殊符号 query。
  - [x] 覆盖 tenant、metadata、ACL、soft delete、status、score_threshold、top_k 在候选产生前生效。
  - [x] 覆盖 private ACL 无 allow-list 默认拒绝、denied_users 优先拒绝、roles/department/permissions allow 语义。
  - [x] 覆盖 backend/query parse failure 映射为稳定 `RetrievalError`，并断言 details 不包含 query 全文、chunk 正文、SQL raw error、secret、token、本机绝对路径。
  - [x] 如实现 PostgreSQL adapter，新增 integration/mock 测试验证 SQL 参数化和 PostgreSQL DDL 条件逻辑；默认本地单测不得依赖真实 PostgreSQL。

- [x] 更新文档与开发说明（AC: 1-6）
  - [x] 更新 `README.md#Retrieval Foundation`，说明 sparse retrieval 已可通过 `CandidateRetriever` 端口执行，PostgreSQL full-text 是 MVP 默认实现，OpenSearch 是后续 adapter。
  - [x] 更新 `docs/operations/local-development.md` 或 `docs/api/retrieval.md`，记录 sparse 当前能力、权限边界、测试命令和非目标。
  - [x] 文档不得宣称 RRF、rerank、`POST /retrieve`、retrieval log 或完整 hybrid retrieval 已完成。

- [x] 验证（AC: 1-6）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/retrieval tests/unit/vectorstores tests/unit/auth`
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/storage`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`
- [x] 如全量成本可接受，运行 `.venv\Scripts\python.exe -m pytest`

### Review Findings

- [x] [Review][Patch] PostgreSQL 查询绕过 `parse_sparse_query_terms` 的 `max_query_terms` 与超长 token 守卫 [packages/retrieval/sparse.py:440]
- [x] [Review][Patch] `SparseRetrieverConfig.timeout_seconds` 已配置但真实 backend search 未执行超时限制 [packages/retrieval/sparse.py:163]
- [x] [Review][Patch] SQLite/Python fallback 在 ACL、metadata、threshold 和稳定排序前先按 `top_k` 截断候选 [packages/retrieval/sparse.py:274]
- [x] [Review][Patch] SQLite/Python fallback 会在 ACL 与 metadata 过滤前加载同租户 active chunk 正文 [packages/retrieval/sparse.py:254]
- [x] [Review][Patch] PostgreSQL ACL JSONB 条件与 Python `acl_allows` 对标量 ACL 成员的语义不一致 [packages/retrieval/sparse.py:452]
- [x] [Review][Patch] sparse candidate 映射阶段的 Pydantic 校验错误未转换为稳定 `RetrievalError` [packages/retrieval/sparse.py:336]
- [x] [Review][Patch] candidate metadata 脱敏未复用共享敏感 key 规则，可能漏掉 `prompt`、`body`、`full_query` 等字段 [packages/retrieval/sparse.py:373]

## Dev Notes

### Current Repository State

- 当前目录不是 git repository，`git log`/`git status` 不可用；本 story 的上下文来自已完成 story 文件、源码扫描、epics、architecture、PRD、UX 和项目规则。
- `packages/retrieval` 已存在，包含 `RetrievalRequest`、`RetrievalFilterSet`、`RetrievalCandidate`、`RetrievalResult`、`CandidateRetriever`、`RetrievalService`、`filters.py`、`dense.py` 和 typed exceptions。
- `RetrievalRequest` 已限制 query/request/trace 非空、`top_k` 1..100、`score_threshold` 0..1 且 finite、metadata filter 只允许标量结构化 mapping。
- `build_retrieval_filter_set(auth, request)` 已复用 `packages.auth.policies.build_access_filter(auth)`；请求 metadata 中的跨 tenant `tenant_id` 会被拒绝。
- `to_sparse_filter_payload(filters)` 已存在，输出 tenant/user/roles/department/permissions/metadata/acl/include_deleted，可作为 sparse adapter 的统一 payload。
- `RetrievalService` 现在只依赖 `CandidateRetriever`，会强制 AuthContext、构建 filters、包装非 retrieval backend error，并在返回前做 tenant、metadata、ACL、score_threshold、top_k 的结果侧守卫。
- `packages/vectorstores.acl.acl_allows` 是当前 ACL 语义基准：`denied_users` 优先拒绝；`public`/`tenant` 可见；`private` 必须命中 allowed_users/roles/departments/permissions，否则拒绝。
- `packages/data/storage/models.py` 中 `ChunkModel` 当前包含 `content`、tenant/document/version/chunk/source/page/title_path/acl/status/deleted_at/metadata，但没有 `tsvector` 字段或 GIN index。
- `migrations/versions/20260527_0003_chunks.py` 创建 chunks 基础表和 tenant/status/document/version/chunk 索引，没有 sparse/full-text index。
- `DocumentRepository.list_chunks_for_version()` 和 `get_chunk()` 是 version/chunk 定向读取，不适合直接作为全局 sparse search；3.3 应新增专门 sparse search adapter/repository，而不是滥用 list all chunks。
- `README.md#Retrieval Foundation` 明确 dense 已完成，BM25/RRF/rerank/API/log/RAG 尚未完成；完成 3.3 后只能更新 sparse 状态，不能宣称完整 hybrid retrieval。

### Architecture Requirements

- 本 story 属于 Retrieval Domain/Application boundary 与 Storage/Infrastructure adapter；不涉及 API route、RAG、Agent 或完整 eval runner。
- Sparse retrieval 必须与 dense retrieval 使用同一个 filter contract，禁止出现“dense 有权限过滤、sparse 先全量搜索再过滤”的双标准。
- MVP sparse 默认 PostgreSQL full text search；OpenSearch/BM25 adapter 后置，但接口必须可替换。
- `packages/retrieval` owns dense/sparse/RRF/rerank/threshold/filter orchestration；`apps/api` 不参与本 story。
- Domain DTO 不得导入 FastAPI、SQLAlchemy、Redis、MinIO、httpx 或外部模型 SDK。
- PostgreSQL full-text SQL 属于 infrastructure/storage 细节，必须参数化，并把 SQLAlchemy/DB exceptions 映射为 retrieval domain error。
- Sparse candidate metadata 是后续 RRF、rerank、context packing、citation、Source Inspector 的共享契约，不能丢失 document/version/chunk/source/page/title_path/tenant/acl。

### Current Files To Preserve And Extend

- `packages/retrieval/dto.py`
  - Current state: retrieval 请求、filter、candidate、result DTO 已稳定；candidate 校验 score finite 和页码范围。
  - Story change: 通常不需要修改，除非新增 sparse-specific config DTO 更适合放这里。
  - Preserve: 不把 chunk content、tsvector、SQL raw row 或全文 query 放进 `RetrievalCandidate.metadata`。

- `packages/retrieval/ports.py`
  - Current state: `CandidateRetriever` 入参为 `RetrievalRequest` 与 `RetrievalFilterSet`。
  - Story change: sparse retriever 实现此协议即可；避免新建平行 service。
  - Preserve: port 不接收裸 query string，不接收 AuthContext 重新解释权限。

- `packages/retrieval/service.py`
  - Current state: service 是候选召回编排入口并有结果侧安全守卫。
  - Story change: 可扩展测试证明 sparse retriever 也通过同一 service guard。
  - Preserve: service 不直接调用 SQLAlchemy、PostgreSQL full text、OpenSearch、EmbeddingProvider、LLM 或 reranker。

- `packages/retrieval/filters.py`
  - Current state: 提供 `to_vector_acl_filter`、`to_vector_metadata_filters`、`to_sparse_filter_payload`。
  - Story change: sparse adapter 应复用 `to_sparse_filter_payload` 或直接消费 `RetrievalFilterSet`。
  - Preserve: `include_deleted=False` 是普通 retrieval 的固定默认。

- `packages/retrieval/dense.py`
  - Current state: dense retriever 展示了端口组合、safe error details、candidate source/metadata redaction 的本地风格。
  - Story change: 可复用其 redaction 思路，但不要复制 dense embedding/vector 逻辑。
  - Preserve: dense 行为不应因 sparse story 变化而回归。

- `packages/data/storage/models.py`
  - Current state: `ChunkModel.content` 是 sparse 的文本来源；没有 full-text 物化字段。
  - Story change: 如新增 generated `search_vector` 或表达式 index，需兼容 Alembic 和 SQLite smoke。
  - Preserve: SQLAlchemy model 不跨入 domain DTO；`content` 不进入 retrieval logs/result metadata。

- `packages/vectorstores/acl.py` and `packages/vectorstores/adapters/pgvector.py`
  - Current state: dense/vector SQL 已实现 ACL JSON 过滤语义。
  - Story change: sparse PostgreSQL SQL 应对齐该 ACL 语义，特别是 denied_users 和 private 默认拒绝。
  - Preserve: 不修改 dense/vector adapter，除非发现共享 ACL bug 且有回归测试。

- `tests/unit/retrieval/test_dense.py` and `tests/unit/retrieval/test_service.py`
  - Current state: 已建立 fake provider/store、safe error、service guard 的测试风格。
  - Story change: 新增 `test_sparse.py`，沿用 deterministic fixtures 和安全断言。
  - Preserve: 默认测试不依赖外部服务。

### Previous Story Intelligence

- Story 3.1 建立了 retrieval filter contract，并修复 private ACL 默认放行、无效 request 不转稳定 error、service 过度信任 retriever 输出、top_k 无上限、NaN threshold、多值 metadata filter 等问题。3.3 不得回退这些边界。
- Story 3.2 的 DenseRetriever 已证明 `RetrievalService` 能接受任何 `CandidateRetriever`。SparseRetriever 应成为同类候选源，而不是改写 service。
- 3.2 code review 后加入了 provider/model/version 一致性、query vector finite 校验、candidate source/metadata 脱敏。Sparse 同样需要对 score finite、candidate metadata/source 安全做明确处理。
- Story 2.6 到 2.9 已把 chunk metadata、embedding summary、vector record、soft delete 和 retrieval_ready 状态串起来。3.3 应消费 active chunks，不重新设计 chunk schema 的治理字段。
- 当前项目无 git 历史可分析，开发者应依赖 story file、测试和源码状态，而不是假设最近 commit 模式。

### Suggested Implementation Shape

示例只表达目标形状，开发时按现有本地风格落地：

```python
class PostgresSparseRetriever:
    def __init__(self, *, session: AsyncSession, config: SparseRetrieverConfig) -> None:
        self._session = session
        self._config = config

    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> list[RetrievalCandidate]:
        sparse_payload = to_sparse_filter_payload(filters)
        query = _parse_sparse_query_or_empty(request.query, self._config)
        if query is None:
            return []
        rows = await self._search_chunks(
            request=request,
            filters=filters,
            sparse_payload=sparse_payload,
            query=query,
        )
        return [_candidate_from_chunk_row(row) for row in rows]
```

PostgreSQL SQL shape should be parameterized and adapter-local. Prefer safe query construction like `websearch_to_tsquery(:language_config, :query)` over assembling `to_tsquery` syntax from user tokens. If a fallback path is needed for SQLite tests, keep it deterministic and still apply the same filters.

### Implementation Boundaries

- Do not implement dense retrieval changes; Story 3.2 owns dense.
- Do not implement RRF merge, dedup, weighted fusion, or hybrid result provenance; Story 3.4 owns it.
- Do not implement Reranker, fake reranker, cross-encoder adapter, fallback strategy, or rerank latency; Story 3.5 owns it.
- Do not implement `POST /retrieve`, API schema, retrieval log table, Alembic migration for retrieval_logs, or route registration; Story 3.6 owns it.
- Do not implement eval fixtures/smoke runner; Story 3.7 owns it.
- Do not implement context packing, prompt building, citation extraction, LLM generation, SSE, chat, Agent, or Tool Registry.
- Do not call real external embedding APIs, LLM APIs, OpenSearch, network services, or production PostgreSQL in default tests.
- Do not log or return query full text, chunk content, SQL raw text, vectors, provider raw responses, API keys, access tokens or local absolute paths.

### Latest Technical Information

- PostgreSQL 18 full text search supports parsing documents into `tsvector` and queries into `tsquery`; ranking functions such as `ts_rank` and `ts_rank_cd` are documented for relevance scoring. Use this behind a sparse adapter, not in service code. Source: https://www.postgresql.org/docs/current/textsearch-controls.html
- PostgreSQL full text indexes are typically implemented with GIN or GiST over `tsvector`; GIN is the common choice for text search indexes. Source: https://www.postgresql.org/docs/current/textsearch-indexes.html
- `websearch_to_tsquery` is documented as accepting web-search-like text and not raising syntax errors for common user input, making it safer for end-user search boxes than manually assembled `to_tsquery` syntax. Source: https://www.postgresql.org/docs/current/textsearch-controls.html

### UX / Product Notes

- 本 story 不实现 UI，但 UX 明确 Open WebUI/前端不是治理边界；用户在 UI 中选择知识范围只能收窄权限，不能扩大 tenant/ACL。
- 后续 Source Inspector 会展示 retrieval_method 和 score 摘要；sparse candidates 必须带 `retrieval_method="sparse"` 和可追溯 citation metadata。
- 后续 Retrieval Diagnostics 会展示 dense/sparse/RRF/rerank/context packing trace；本 story 可保留安全 latency/score 元数据，但不落库 retrieval_logs。
- 前端不得渲染完整 retrieval trace、tool raw output 或完整企业文档正文；sparse candidate 不应携带 chunk content。

### References

- `_bmad-output/planning-artifacts/epics.md#Story-3.3-BM25-Sparse-Retrieval-召回`
- `_bmad-output/planning-artifacts/epics.md#Epic-3-授权-Hybrid-Retrieval-与检索复盘`
- `_bmad-output/planning-artifacts/architecture.md#Sparse-Search`
- `_bmad-output/planning-artifacts/architecture.md#Project-Structure-Boundaries`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-9-BM25-Sparse-Retrieval`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-22-RBAC-与-ACL-检索过滤`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md#Interaction-Rules`
- `_bmad-output/implementation-artifacts/3-1-retrieval-请求模型与权限过滤构建.md`
- `_bmad-output/implementation-artifacts/3-2-dense-retrieval-召回.md`
- `project-context.md`
- `packages/retrieval/dto.py`
- `packages/retrieval/ports.py`
- `packages/retrieval/service.py`
- `packages/retrieval/filters.py`
- `packages/retrieval/dense.py`
- `packages/retrieval/exceptions.py`
- `packages/data/storage/models.py`
- `packages/data/storage/repositories.py`
- `packages/data/ports.py`
- `packages/vectorstores/acl.py`
- `packages/vectorstores/adapters/pgvector.py`
- `tests/unit/retrieval/test_dense.py`
- `tests/unit/retrieval/test_service.py`
- `tests/unit/retrieval/test_filters.py`
- `tests/integration/storage/test_alembic_migrations.py`
- `README.md#Retrieval-Foundation`
- PostgreSQL full text controls: https://www.postgresql.org/docs/current/textsearch-controls.html
- PostgreSQL full text indexes: https://www.postgresql.org/docs/current/textsearch-indexes.html

## Validation Checklist

Validation Result: PASS（2026-06-06T22:47:03+08:00）

- [x] Story 明确了用户角色、目标和收益。
- [x] Acceptance Criteria 覆盖 SparseRetriever、PostgreSQL full text MVP adapter、tenant/ACL/metadata/soft-delete/status filters、query 解析稳定性、candidate metadata、安全错误和 fake-only tests。
- [x] Tasks 覆盖 sparse implementation、错误码、PostgreSQL adapter、Alembic migration、service boundary、unit/integration tests、docs 和验证命令。
- [x] Dev Notes 明确当前源码状态，尤其是 `CandidateRetriever`、`to_sparse_filter_payload`、`ChunkModel.content`、ACL 语义和无 full-text index 的现状。
- [x] 明确不实现 RRF、rerank、`/retrieve` API、retrieval logs、eval runner 或 RAG。
- [x] 明确 query 全文、chunk 正文、SQL raw text、tsvector、secret、本机绝对路径不得进入错误 details、日志或 candidate metadata。

## Change Log

- 2026-06-06: Created comprehensive Story 3.3 developer context for BM25/PostgreSQL full-text sparse retrieval through the existing retrieval port and AuthContext-derived filters.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- `.venv\Scripts\python.exe -m pytest tests/unit/retrieval/test_sparse.py`（red phase: missing sparse errors/module; green phase: 10 passed）
- `.venv\Scripts\python.exe -m pytest tests/unit/retrieval tests/unit/vectorstores tests/unit/auth`（104 passed）
- `.venv\Scripts\python.exe -m pytest tests/integration/storage`（25 passed）
- `.venv\Scripts\python.exe -m ruff check .`（passed）
- `.venv\Scripts\python.exe -m mypy apps packages tests`（147 source files, no issues）
- `.venv\Scripts\python.exe -m pytest`（349 passed）
- `.venv\Scripts\python.exe -m pytest tests/unit/retrieval/test_sparse.py -q`（review fixes: 15 passed）
- `.venv\Scripts\python.exe -m pytest tests/unit/retrieval -q`（review fixes: 70 passed）
- `.venv\Scripts\python.exe -m pytest tests/unit/retrieval tests/unit/vectorstores tests/unit/auth -q`（review fixes: 109 passed）
- `.venv\Scripts\python.exe -m pytest tests/integration/storage -q`（review fixes: 25 passed）
- `.venv\Scripts\python.exe -m ruff check .`（review fixes: passed）
- `.venv\Scripts\python.exe -m mypy apps packages tests`（review fixes: 147 source files, no issues）
- `.venv\Scripts\python.exe -m pytest -q`（review fixes: 354 passed）

### Completion Notes List

- Implemented `PostgresSparseRetriever` behind the existing `CandidateRetriever` contract, accepting full `RetrievalRequest` and `RetrievalFilterSet` without changing `RetrievalService` or API routes.
- Added sparse query parsing for Chinese/mixed identifiers and symbol-heavy input, safe sparse error codes/details, candidate-side redaction, and finite score/top_k/threshold handling.
- Added PostgreSQL full-text SQL construction with bind parameters, `websearch_to_tsquery`, `ts_rank_cd`, metadata filters, active/soft-delete filters, and ACL deny/allow semantics aligned with `acl_allows`.
- Added deterministic fake/backend unit tests for exact clause/error/person/product recall, tenant/metadata/ACL/status/deleted filtering, private default deny, denied user precedence, service guard, SQL parameterization, and safe error mapping.
- Added PostgreSQL-only Alembic GIN full-text index migration and verified SQLite storage smoke remains green.
- Updated retrieval documentation with current sparse capabilities, safety boundaries, test commands, and explicit non-goals.
- Applied code review fixes for capped PostgreSQL sparse query terms, backend timeout enforcement, SQLite fallback ACL/metadata filtering before content load and `top_k`, scalar ACL SQL semantics, safe candidate validation errors, and shared sensitive metadata redaction.

### File List

- `packages/retrieval/sparse.py`
- `packages/retrieval/exceptions.py`
- `packages/retrieval/__init__.py`
- `tests/unit/retrieval/test_sparse.py`
- `migrations/versions/20260527_0007_chunks_full_text_index.py`
- `README.md`
- `docs/operations/local-development.md`
- `_bmad-output/implementation-artifacts/3-3-bm25-sparse-retrieval-召回.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

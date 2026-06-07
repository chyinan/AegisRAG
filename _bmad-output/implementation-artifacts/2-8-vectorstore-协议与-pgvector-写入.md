---
baseline_commit: NO_VCS
---

# Story 2.8: VectorStore 协议与 pgvector 写入

Status: done

生成时间：2026-06-06T17:57:20+08:00

## Story

As a 平台工程师,
I want 向量写入通过统一 `VectorStore` 接口完成,
so that 默认使用 pgvector，同时保留 FAISS 和 Milvus 的替换边界。

## Acceptance Criteria

1. **VectorStore 端口和 DTO 覆盖写入、查询和删除契约**
   - Given embedding vectors 已由 `EmbeddingProvider.embed_texts` 生成
   - When application service 构造 vector records
   - Then 只能通过 `VectorStore.upsert` 写入向量
   - And `VectorStore` Protocol 必须包含 `upsert`、`search`、`delete_by_document`
   - And DTO 必须支持 metadata filter、tenant filter、ACL filter、soft delete、top_k、score threshold

2. **pgvector adapter 是默认持久化实现**
   - Given `VectorRecord` 包含 vector 和 chunk metadata
   - When 调用默认 pgvector adapter
   - Then vectors 与 chunk metadata 一起写入 PostgreSQL pgvector-backed table
   - And 写入记录包含 `tenant_id`、`acl`、`document_id`、`version_id`、`chunk_id`、`source`/`source_uri`、`page_start`、`page_end`、`title_path`、`embedding_provider`、`embedding_model`、`embedding_version`、`embedding_dim`
   - And 不把完整 provider raw response、API key、access token、绝对路径或非必要 chunk 正文写入日志、audit 或 job metadata

3. **维度和模型兼容性失败必须阻止部分索引**
   - Given `embedding_dim` 与目标 vector index 维度不一致
   - When 执行 `VectorStore.upsert`
   - Then 系统拒绝写入并返回稳定错误码 `INDEX_DIMENSION_MISMATCH`
   - And 不产生部分写入，不标记 `retrieval_ready`
   - And embedding job 或 indexing 结果记录安全 `error_code`、tenant/document/version/job IDs 和 latency

4. **写入流程复用 2.7 的 embedding response，不重新 embedding**
   - Given `EmbeddingJobService` 已拿到 provider response 和 active chunks
   - When Story 2.8 接入 vector indexing
   - Then 在同一 worker/job 成功路径中把 response vectors 映射为 `VectorRecord` 并调用 `VectorStore.upsert`
   - And 不新增携带完整向量的 queue payload，不把完整向量塞入 `document_versions.metadata`、`embedding_jobs.metadata` 或 audit
   - And 不重新读取 raw document、不重新切 chunk、不为索引重跑 provider

5. **搜索和删除契约为 Epic 3/2.9 做好边界**
   - Given 开发者实现或替换 vector store adapter
   - When 运行 contract tests
   - Then `search` 必须在查询阶段应用 `tenant_id`、metadata filters、ACL filters、soft delete、top_k 和 score threshold
   - And search result 必须返回 chunk/document/version/source/page/title_path/score/retrieval_method/tenant_id/acl
   - And `delete_by_document(document_id, version_id)` 默认软删除或标记 vector records，使其后续不可检索

6. **测试默认不依赖真实 PostgreSQL 或外部模型**
   - Given 单元测试和默认集成测试运行
   - When 测试 VectorStore contract、indexing service 和 worker flow
   - Then 使用 `FakeVectorStore`、SQLite portable migration smoke 或 mock session
   - And 不真实调用外部 LLM/embedding API
   - And pgvector-specific SQL 通过隔离的 adapter 测试或 PostgreSQL-only smoke 明确跳过条件

## Tasks / Subtasks

- [x] 定义 `packages/vectorstores` 端口、DTO 和异常（AC: 1, 3, 5）
  - [x] 新建 `packages/vectorstores/__init__.py`、`dto.py`、`ports.py`、`exceptions.py`、`service.py`、`adapters/fake.py`、`adapters/pgvector.py`。
  - [x] `VectorStore` Protocol 定义：
    `async def upsert(self, vectors: list[VectorRecord]) -> VectorUpsertResult`;
    `async def search(self, request: VectorSearchRequest) -> list[VectorSearchResult]`;
    `async def delete_by_document(self, document_id: str, version_id: str | None = None, *, tenant_id: str) -> VectorDeleteResult`。
  - [x] DTO 至少包含 `VectorRecord`、`VectorSearchRequest`、`VectorSearchResult`、`VectorUpsertResult`、`VectorDeleteResult`、`MetadataFilter`/`AclFilter` 或复用 auth policy filter DTO。
  - [x] `VectorRecord` 必须携带治理字段：tenant/document/version/chunk/source/page/title_path/acl/status/checksum/embedding provider/model/version/dim/vector/metadata。
  - [x] 异常使用稳定 code：`INDEX_DIMENSION_MISMATCH`、`VECTOR_STORE_WRITE_FAILED`、`VECTOR_STORE_SEARCH_FAILED`、`VECTOR_STORE_DELETE_FAILED`、`VECTOR_RECORD_SCOPE_MISMATCH`。

- [x] 新增 pgvector-backed storage schema（AC: 2, 3, 5）
  - [x] 新增 Alembic migration `20260527_0005_vector_records.py`，`down_revision = "20260527_0004"`；不得修改旧 migration。
  - [x] PostgreSQL 分支启用 `CREATE EXTENSION IF NOT EXISTS vector`；SQLite smoke 分支必须保持可运行，使用 portable fallback column 或跳过 pgvector-only DDL。
  - [x] 建议表名为 `vector_records` 或 `chunk_embeddings`，但必须统一到 DTO、repository、docs 和 tests；不要在 `chunks` 表中塞完整向量。
  - [x] 表字段至少包含 `id`、`created_at`、`updated_at`、`tenant_id`、`created_by`、`status`、`document_id`、`version_id`、`chunk_id`、`source_type`、`source_uri`、`title_path`、`page_start`、`page_end`、`token_count`、`acl`、`checksum`、`embedding_provider`、`embedding_model`、`embedding_version`、`embedding_dim`、`embedding`、`metadata`、`deleted_at`。
  - [x] 建立唯一约束防止同一 tenant/document/version/chunk/model/version 被重复插入；upsert 应更新既有记录而不是产生重复可检索记录。
  - [x] 建立 tenant/document/version/chunk/status/deleted_at 查询索引；pgvector ANN index 可先用 HNSW cosine 或 L2，必须与默认 distance strategy 一致。
  - [x] 如果使用 `pgvector-python`，在 `pyproject.toml` 增加 `pgvector>=0.4.2,<1`，并把 PostgreSQL type registration 放在 pgvector adapter/session 初始化边界，不要污染 domain 层。

- [x] 实现 `FakeVectorStore` 和 pgvector adapter（AC: 1, 2, 3, 5, 6）
  - [x] `FakeVectorStore` 在内存中执行 deterministic upsert/search/delete，支持维度检查、tenant filter、metadata filter、ACL filter、soft delete、top_k、score threshold，用于 contract tests。
  - [x] `PgVectorStore` 放在 `packages/vectorstores/adapters/pgvector.py`，只依赖 SQLAlchemy/pgvector infrastructure；不得被 API route 或 domain 层直接调用。
  - [x] upsert 前校验所有 record 的 tenant/document/version/chunk scope 一致性、vector 非空、`len(vector) == embedding_dim`、目标 index dim 匹配。
  - [x] upsert 必须在事务内完成；任何单条失败应 rollback，不能留下部分 active vector records。
  - [x] search 必须先约束 `tenant_id`、`deleted_at is null`、`status = active`、ACL/metadata filters，再计算距离并应用 top_k/threshold；禁止先查全量再由调用方过滤。
  - [x] delete_by_document 默认设置 `status="deleted"` 和 `deleted_at`，后续 search 不返回；硬删除只能作为显式维护任务，不作为默认行为。

- [x] 接入 embedding worker 成功路径（AC: 2, 3, 4）
  - [x] 扩展 `packages/embeddings/service.py` 或新增清晰的 indexing application service，使 `EmbeddingResponse.vectors + ChunkRecord` 在同一 job 内映射为 `VectorRecord`。
  - [x] 保持 2.7 现有 provider validation、chunk snapshot check、安全 usage summary 和 audit/log 规则；不要删除已通过的 idempotency、retry backoff、snapshot mismatch 测试。
  - [x] vector upsert 成功后可在 `embedding_jobs.metadata` 或 `document_versions.metadata` 写安全摘要，例如 `vector_index_summary`，只包含 vector_count、provider/model/version/dim、status、latency，不包含完整向量。
  - [x] 不要把 document/version 状态标记为 `retrieval_ready`；Story 2.9 才负责文档版本、软删除与索引状态闭环。2.8 只保证向量已写入并可由 VectorStore contract 检索。
  - [x] vector upsert 失败时把 job 标记为 retryable 或 terminal failure，错误码稳定且不产生可检索的半成品。
  - [x] 更新 `apps/worker/jobs/embedding_jobs.py` 的 provider/vectorstore factory；默认 local/test 可用 `FakeEmbeddingProvider + FakeVectorStore` 或配置 pgvector。

- [x] 增加配置和文档（AC: 2, 3, 6）
  - [x] 在 `packages/common/config.py` 和 `.env.example` 增加 `VECTOR_STORE_TYPE`、`VECTOR_INDEX_DIM`、`VECTOR_DISTANCE_METRIC`、`PGVECTOR_INDEX_TYPE`、`PGVECTOR_HNSW_M`、`PGVECTOR_HNSW_EF_CONSTRUCTION` 等配置入口；默认仍可 fake/SQLite 测试运行。
  - [x] 更新 `README.md` 和 `docs/operations/local-development.md`：说明 `embedded -> vector indexed` 是检索前置阶段，但不等同于 `retrieval_ready`。
  - [x] 更新 `docs/api/upload.md`：说明 upload 不等待 vector indexing，job/status 查询只展示安全摘要。
  - [x] 如新增 PostgreSQL-only smoke，文档给出 Docker Compose 启动 postgres + migration 的验证命令。

- [x] 补充测试（AC: 1-6）
  - [x] `tests/unit/vectorstores/test_contract.py` 覆盖 FakeVectorStore upsert/search/delete、维度不匹配、tenant filter、ACL filter、metadata filter、soft delete、threshold、top_k。
  - [x] `tests/unit/vectorstores/test_vector_record_mapping.py` 覆盖 EmbeddingResponse + ChunkRecord -> VectorRecord，确保 page/source/title_path/acl/checksum/provider/model/dim 不丢失。
  - [x] `tests/unit/embeddings/test_embedding_service.py` 增加 vector upsert 成功、upsert retryable failure、dimension mismatch terminal、无 partial indexing、日志/audit 不含完整向量。
  - [x] `tests/integration/storage/test_alembic_migrations.py` 扩展 expected tables/columns/indexes；SQLite smoke 不得因 pgvector extension 失败。
  - [x] `tests/integration/storage/test_vector_repositories.py` 或 pgvector adapter smoke 覆盖 tenant-scoped upsert/delete/search；如需真实 PostgreSQL，用 marker 和环境变量显式启用。
  - [x] `tests/integration/worker/test_embedding_jobs.py` 覆盖 worker factory 选择 vectorstore、payload 仍为 ID-only、未配置真实 pgvector 时不误连外部服务。
  - [x] 全量验证命令：`.venv\Scripts\python.exe -m pytest tests/unit/vectorstores tests/unit/embeddings tests/integration/storage tests/integration/worker`、`.venv\Scripts\python.exe -m ruff check .`、`.venv\Scripts\python.exe -m mypy apps packages tests`、`.venv\Scripts\python.exe -m pytest`。

### Review Findings

- [x] [Review][Patch] PgVectorStore search bypasses pgvector/query-stage retrieval and filtering [packages/vectorstores/adapters/pgvector.py:84]
- [x] [Review][Patch] PgVectorStore ACL filter ignores department and permission grants [packages/vectorstores/adapters/pgvector.py:301]

## Dev Notes

### Current Repository State

- 当前目录不是 git repository，`git log` 不可用；本 story 基于现有源码、Story 2.7 完成记录、epics、architecture、项目规则和最新 pgvector 文档生成。
- `packages/vectorstores` 当前不存在；本 story 应新增该包，不要把 VectorStore Protocol 放进 `packages/embeddings`、`packages/data` 或 FastAPI route。
- 当前 `pyproject.toml` 没有 `pgvector` Python 依赖；如果实现 SQLAlchemy `Vector` column 或 asyncpg type registration，需要新增 `pgvector>=0.4.2,<1`。
- 当前 migrations head 是 `20260527_0004_embedding_jobs.py`；本 story 只能新增 `20260527_0005_*`，不能重写旧 migration。
- 当前 `EmbeddingJobService` 已拿到 `EmbeddingResponse.vectors`，但 2.7 成功后只保存安全摘要并丢弃完整向量。2.8 必须在该成功路径接入 `VectorStore.upsert`，否则会被迫重新调用 provider 或保存完整向量，二者都不符合安全和成本边界。

### Architecture Requirements

- 本 story 横跨 Application Service Layer、Domain/Port、Infrastructure Layer、Storage Layer：`packages/vectorstores` 定义 port/DTO/adapter，`packages/embeddings/service.py` 或专用 indexing service 编排 provider response -> VectorStore upsert。
- `packages/vectorstores/dto.py`、`ports.py`、`exceptions.py` 不得 import FastAPI、SQLAlchemy model、Redis、MinIO、OpenAI/Qwen/DeepSeek/Ollama SDK。
- pgvector adapter 可以依赖 SQLAlchemy、pgvector-python、PostgreSQL dialect，但必须隔离在 `packages/vectorstores/adapters/pgvector.py` 或 storage adapter 内。
- API route 不参与本 story；不要新增 `/vectorstores` 或调试 API。
- 所有 vector read/write/delete 必须 tenant-scoped；ACL 和 metadata filter 是查询阶段约束，不是 prompt 或调用方后过滤。
- 日志和 audit 记录 request_id、trace_id、tenant_id、user_id、document_id、version_id、job_id、vector_count、provider、model、dim、latency、status、error_code；不得记录完整向量、chunk 正文、provider raw response、secret 或绝对路径。

### Current Files To Preserve And Extend

- `packages/data/dto.py`
  - Current state: 已有 `DocumentRecord`、`DocumentVersionRecord`、`IngestionJobRecord`、`EmbeddingJobRecord`、`ChunkRecord`。
  - Story change: 不建议把完整向量加入这些 DTO；新增 vector-specific DTO 到 `packages/vectorstores/dto.py`。
  - Preserve: DTO frozen、typed、无 SQLAlchemy state。

- `packages/data/ports.py`
  - Current state: `DocumentRepository` 已包含 chunk 和 embedding job 方法。
  - Story change: 可新增 vector repository/storage protocol，但 VectorStore 端口应归属 `packages/vectorstores`；避免把检索查询语义塞进 `DocumentRepository`。
  - Preserve: 所有 repository 方法显式包含 `tenant_id`。

- `packages/data/storage/models.py`
  - Current state: SQLAlchemy typed declarative models 已包含 documents、document_versions、ingestion_jobs、chunks、embedding_jobs。
  - Story change: 可新增 vector storage model 或在 vectorstores adapter 中定义 pgvector-specific table mapping。
  - Preserve: storage model 不放 service/provider 逻辑；SQLite migration smoke 仍可导入。

- `packages/data/storage/repositories.py`
  - Current state: tenant-scoped repository，embedding job 成功时更新 `embedding_jobs`、`document_versions.metadata`、chunk embedding summary。
  - Story change: 若扩展 repository，保持 SQLAlchemy error -> `StorageError`，details 只包含安全 ID；vector upsert 必须事务化。
  - Preserve: `claim_embedding_job` retry backoff、embedded idempotency、安全 summary、chunk snapshot check 相关行为。

- `packages/embeddings/service.py`
  - Current state: provider response validation、chunk snapshot check、safe audit/log、`embedded` 状态已完成。
  - Story change: 在 provider response 校验和 snapshot check 之后、最终成功提交之前接入 vector upsert。
  - Preserve: 不把完整向量写入 metadata/audit/log；provider failure 和 vectorstore failure 要区分稳定 error code。

- `apps/worker/jobs/embedding_jobs.py`
  - Current state: worker 根据 ID-only payload 重建 `AuthenticatedRequestContext` 并调用 `EmbeddingJobService`。
  - Story change: 增加 vectorstore factory/config 注入；未配置真实 pgvector 时不要尝试连接外部服务。
  - Preserve: queue payload 不包含 chunk content、完整向量、ORM model、AuthContext、prompt、API key 或绝对路径。

### Previous Story Intelligence

- Story 2.1 建立 `/upload` 异步返回 job id；2.8 不得让 upload 等待 embedding 或 vector indexing。
- Story 2.2/2.3 建立 parser worker 的 claim/failed/status/audit 模式；2.8 的 vector upsert 失败也应映射为稳定状态和安全错误码。
- Story 2.4 明确不能把 cleaned document/full content 写入 metadata；2.8 同理不能把完整向量或 provider response 写入 metadata。
- Story 2.5 建立稳定 `chunk_id`、checksum、page/title/ACL lineage；2.8 必须从 `ChunkRecord` 复制这些治理字段到 `VectorRecord`。
- Story 2.6 已落地 `chunks` 表和 tenant-scoped chunk repository；2.8 不重新实现 chunk persistence。
- Story 2.7 已落地 `EmbeddingProvider`、`FakeEmbeddingProvider`、embedding job、worker 和安全摘要；2.8 只接 VectorStore 和 pgvector 写入，不新增真实 LLM/embedding vendor SDK。

### Suggested Contracts

VectorStore protocol:

```python
class VectorStore(Protocol):
    async def upsert(self, vectors: list[VectorRecord]) -> VectorUpsertResult:
        ...

    async def search(self, request: VectorSearchRequest) -> list[VectorSearchResult]:
        ...

    async def delete_by_document(
        self,
        document_id: str,
        version_id: str | None = None,
        *,
        tenant_id: str,
    ) -> VectorDeleteResult:
        ...
```

VectorRecord shape:

```python
class VectorRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    document_id: str
    version_id: str
    chunk_id: str
    created_by: str
    status: str = "active"
    vector: list[float]
    embedding_provider: str
    embedding_model: str
    embedding_version: str | None = None
    embedding_dim: int
    source_type: str
    source_uri: str | None = None
    title_path: list[str]
    page_start: int | None = None
    page_end: int | None = None
    token_count: int
    acl: dict[str, object]
    checksum: str
    metadata: dict[str, object] = Field(default_factory=dict)
```

Search result shape:

```python
class VectorSearchResult(BaseModel):
    document_id: str
    version_id: str
    chunk_id: str
    source: str | None
    page_start: int | None
    page_end: int | None
    title_path: list[str]
    score: float
    retrieval_method: str = "dense"
    tenant_id: str
    acl: dict[str, object]
    metadata: dict[str, object] = Field(default_factory=dict)
```

### Implementation Boundaries

- 不要实现 BM25 sparse retrieval、RRF merge、reranker、retrieve API、context packing、RAG answer 或 citation extraction；这些属于 Epic 3/4。
- 不要把权限逻辑推迟到 LLM 或 prompt；VectorStore.search 必须在查询阶段过滤 tenant/ACL/metadata/soft delete。
- 不要让 vector upsert 成功自动等同于 `retrieval_ready`；Story 2.9 负责文档版本、软删除与索引状态闭环。
- 不要为索引重新调用 embedding provider；必须复用 2.7 当前 job 内的 `EmbeddingResponse.vectors`。
- 不要把完整向量通过 RQ payload、metadata、audit、logs、API response 传播；完整向量只进入 vector storage adapter。
- 不要默认启用真实外部 provider 或真实 PostgreSQL smoke；本地测试必须 fake-first。

### Latest Technical Information

- pgvector extension 当前架构基线为 v0.8.2；官方 changelog 记录 0.8.2 修复 parallel HNSW index build buffer overflow，并改进 Windows install target 与 Postgres 18 EXPLAIN 输出。[Source: https://raw.githubusercontent.com/pgvector/pgvector/master/CHANGELOG.md]
- pgvector README 说明其支持 Postgres 13+、exact/approximate nearest neighbor、vector/halfvec/bit/sparsevec、L2/inner product/cosine/L1/Hamming/Jaccard 等距离，并且默认 exact search，可用 HNSW/IVFFlat 做 approximate index。[Source: https://raw.githubusercontent.com/pgvector/pgvector/master/README.md]
- pgvector README 对 filtering 有重要限制：approximate index 下过滤条件在 index scan 后应用，默认 HNSW `ef_search=40` 时过滤可能导致召回不足；2.8 search contract 必须把 tenant/ACL/metadata filter 放进 SQL 条件，并为 Epic 3 保留 iterative scan/扩大 candidate 的调优入口。[Source: https://raw.githubusercontent.com/pgvector/pgvector/master/README.md]
- pgvector-python PyPI 当前版本为 0.4.2（2025-12-05），支持 SQLAlchemy、Psycopg 3、asyncpg 等；SQLAlchemy 示例使用 `CREATE EXTENSION IF NOT EXISTS vector`、`Vector(dim)`、HNSW/IVFFlat index 和 distance methods。[Source: https://pypi.org/project/pgvector/]

### UX / Product Notes

- 本 story 不实现 UI，但 Knowledge Admin 后续需要展示 vector indexing 安全摘要：vector_count、provider/model/version/dim、status、error_code、request_id。
- 员工查询端不得看到未完成 vector indexing 的文档为可检索；`embedded` 和 vector upsert 成功都不是最终可信 RAG 闭环。
- 管理端错误提示必须显示安全摘要和 request_id，不展示完整向量、chunk 正文、provider raw response 或数据库内部错误。

### References

- `_bmad-output/planning-artifacts/epics.md#Story-2.8-VectorStore-协议与-pgvector-写入`
- `_bmad-output/planning-artifacts/epics.md#Story-2.9-文档版本-软删除与索引状态闭环`
- `_bmad-output/planning-artifacts/epics.md#Epic-3-授权-Hybrid-Retrieval-与检索复盘`
- `_bmad-output/planning-artifacts/architecture.md#Data-Architecture`
- `_bmad-output/planning-artifacts/architecture.md#Project-Structure-Boundaries`
- `_bmad-output/implementation-artifacts/2-7-embeddingprovider-抽象与-embedding-job.md`
- `project-context.md`
- `packages/data/dto.py`
- `packages/data/ports.py`
- `packages/data/storage/models.py`
- `packages/data/storage/repositories.py`
- `packages/embeddings/service.py`
- `apps/worker/jobs/embedding_jobs.py`
- `packages/common/config.py`
- `pyproject.toml`
- `https://raw.githubusercontent.com/pgvector/pgvector/master/CHANGELOG.md`
- `https://raw.githubusercontent.com/pgvector/pgvector/master/README.md`
- `https://pypi.org/project/pgvector/`

## Validation Checklist

Validation Result: PASS（2026-06-06T17:57:20+08:00）

- [x] Story 明确了用户角色、目标和收益。
- [x] Acceptance Criteria 覆盖 Epic Story 2.8 的 VectorStore abstraction、pgvector 默认写入、维度校验、contract tests、filter/search/delete 边界。
- [x] Tasks 覆盖 `packages/vectorstores`、pgvector-backed schema、Fake/PgVector adapters、embedding worker 接入、配置、文档和测试。
- [x] Dev Notes 明确复用 Story 2.7 的 `EmbeddingResponse.vectors`，不重新调用 provider、不通过 queue 或 metadata 传播完整向量。
- [x] 明确 2.8 不实现 BM25、RRF、rerank、retrieve API、RAG 或 `retrieval_ready` 状态闭环。
- [x] 明确 tenant_id/user_id/ACL/source metadata、安全日志和审计约束贯穿 vector write/search/delete。
- [x] 包含当前代码文件状态、前序 story 经验、架构/PRD/UX 约束、最新技术参考和实现边界。

## Change Log

- 2026-06-06: Created comprehensive Story 2.8 developer context for VectorStore protocol, pgvector storage, dimension enforcement, embedding worker integration, contract tests, configuration and documentation.
- 2026-06-06: Implemented VectorStore protocol, fake/pgvector adapters, vector_records migration, embedding worker vector upsert integration, configuration, documentation, and validation tests.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- `.venv\Scripts\python.exe -m pytest tests/unit/vectorstores tests/unit/embeddings tests/integration/storage tests/integration/worker` -> 39 passed
- `.venv\Scripts\python.exe -m ruff check .` -> passed
- `.venv\Scripts\python.exe -m mypy apps packages tests` -> success, 129 source files
- `.venv\Scripts\python.exe -m pytest` -> 258 passed

### Completion Notes List

- Added `packages/vectorstores` with stable DTOs, `VectorStore` Protocol, domain errors, deterministic `FakeVectorStore`, and SQLAlchemy-isolated `PgVectorStore`.
- Added `vector_records` schema with PostgreSQL `vector` branch, SQLite JSON fallback, governance fields, uniqueness, tenant/status/deleted indexes, and HNSW cosine index for PostgreSQL.
- Connected `EmbeddingJobService` success path to map `EmbeddingResponse.vectors + ChunkRecord` into `VectorRecord` and call `VectorStore.upsert` before marking jobs embedded.
- Preserved 2.7 safety rules: queue payloads remain ID-only, provider is called once, raw provider responses/full vectors/chunk text/secrets are not written to logs, audit, metadata, or payloads.
- Added safe `vector_index_summary` metadata and stable vector failure handling: dimension mismatch is terminal, other vector store failures can remain retryable.
- Added config/env/Compose entries and documentation that vector indexing is a retrieval prerequisite but not `retrieval_ready`.
- Verified FakeVectorStore contract, vector record mapping, embedding service vector upsert/failure behavior, Alembic SQLite smoke, PgVectorStore SQLite fallback smoke, worker factory behavior, ruff, mypy, and full pytest.

### File List

- `.env.example`
- `README.md`
- `_bmad-output/implementation-artifacts/2-8-vectorstore-协议与-pgvector-写入.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `apps/worker/jobs/embedding_jobs.py`
- `docker/compose.yaml`
- `docs/api/upload.md`
- `docs/operations/local-development.md`
- `migrations/versions/20260527_0005_vector_records.py`
- `packages/common/config.py`
- `packages/data/storage/models.py`
- `packages/data/storage/repositories.py`
- `packages/embeddings/service.py`
- `packages/vectorstores/__init__.py`
- `packages/vectorstores/adapters/__init__.py`
- `packages/vectorstores/adapters/fake.py`
- `packages/vectorstores/adapters/pgvector.py`
- `packages/vectorstores/dto.py`
- `packages/vectorstores/exceptions.py`
- `packages/vectorstores/ports.py`
- `packages/vectorstores/service.py`
- `tests/integration/storage/test_alembic_migrations.py`
- `tests/integration/storage/test_vector_repositories.py`
- `tests/integration/worker/test_embedding_jobs.py`
- `tests/unit/common/test_config.py`
- `tests/unit/embeddings/test_embedding_service.py`
- `tests/unit/vectorstores/test_contract.py`
- `tests/unit/vectorstores/test_vector_record_mapping.py`

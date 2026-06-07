---
baseline_commit: NO_VCS
---

# Story 2.6: Chunk Metadata Contract 与持久化

Status: done

生成时间：2026-06-06T16:18:29+08:00

## Story

As a 平台工程师,
I want chunk metadata 和数据库迁移明确落地,
so that retrieval、citation、ACL 和版本治理能共享同一可信数据契约。

## Acceptance Criteria

1. **Chunk metadata 使用 typed DTO 且不暴露 SQLAlchemy model**
   - Given `FixedSizeChunker` 已生成 `packages.ingestion.domain.Chunk`
   - When chunk 进入 storage/application 边界
   - Then 必须转换为 typed DTO，例如 `ChunkRecord`
   - And DTO 必须包含 `document_id`、`version_id`、`chunk_id`、`tenant_id`、`source_type`、`source_uri`、`title_path`、`page_start`、`page_end`、`token_count`、`acl`、`checksum`、`status`
   - And 不允许把 SQLAlchemy model 传入 ingestion、retrieval、rag 或 API domain 逻辑

2. **`chunks` 表和 Alembic migration 可追溯、可查询**
   - Given `chunks` 表首次引入
   - When Alembic upgrade 到 head
   - Then `chunks` 包含 `id`、`created_at`、`updated_at`、`tenant_id`、`document_id`、`version_id`、`chunk_id`、`status`、`acl`、`checksum`、source metadata、页码字段、`token_count`、`title_path`、`metadata`
   - And 建立 tenant/document/version/chunk/status 查询所需索引
   - And `chunk_id` 在同一 tenant/document/version 下唯一，旧版本 chunk 不被新版本静默覆盖

3. **Repository 按 tenant 和版本持久化与读取 chunk**
   - Given 已有 document/version records
   - When repository upsert 或 replace 当前版本 chunks
   - Then 只写入同一 `tenant_id`、`document_id`、`version_id` 下的 chunk
   - And 读取接口必须支持 `tenant_id` + `document_id` + `version_id` 和 `tenant_id` + `chunk_id` 查询
   - And cross-tenant 查询返回空结果或稳定 storage error，不泄露资源存在性

4. **检索、citation 和 Source Inspector 所需字段完整保留**
   - Given chunk 从 storage 转回 DTO/domain contract
   - When 后续 retrieval、citation 或 Source Inspector 使用该记录
   - Then 保留 `acl`、`source_type`、`source_uri`、`title_path`、`page_start`、`page_end`、`section_ids`、`checksum`
   - And 未授权 chunk 不得进入 retrieval、context packing、prompt 或 source detail response

5. **状态推进和安全摘要不记录正文**
   - Given chunk persistence 成功
   - When ingestion job 状态推进
   - Then job/document/version 可更新为 `chunked`，并在 version metadata 记录安全摘要，例如 `chunk_count`、`token_count_min`、`token_count_max`
   - And 不得把完整 chunk content 写入日志、audit、job metadata 或 `document_versions.metadata`

## Tasks / Subtasks

- [x] 定义 storage DTO 和 port contract（AC: 1, 3, 4）
  - [x] 在 `packages/data/dto.py` 新增 frozen Pydantic v2 `ChunkRecord`，字段覆盖 AC1，内部可包含 `content` 作为后续 embedding/retrieval 输入，但日志和 metadata 摘要不得记录正文。
  - [x] `ChunkRecord` 必须校验 required IDs、`token_count > 0`、`title_path` 非空、`section_ids` 非空、页码为 1-based 且 `page_end >= page_start`。
  - [x] `acl` 和 `metadata` 使用 mapping validator；显式 `None` 应降级为空 dict 或 tenant-default ACL，不能保留为 `None`。
  - [x] 在 `packages/data/ports.py` 增加 repository protocol 方法，例如 `replace_chunks_for_version(...)`、`list_chunks_for_version(...)`、`get_chunk(...)`；方法入参必须包含 `tenant_id`。
  - [x] 不要把 `packages.ingestion.domain.Chunk` 直接作为 storage 返回类型；storage 层返回 `ChunkRecord`，必要时提供显式转换 helper。

- [x] 新增 `chunks` SQLAlchemy model（AC: 1, 2, 4）
  - [x] 在 `packages/data/storage/models.py` 新增 `ChunkModel(IdMixin, TimestampMixin, Base)`，表名 `chunks`。
  - [x] 字段至少包括：`tenant_id`、`document_id`、`version_id`、`chunk_id`、`created_by`、`status`、`source_type`、`source_uri`、`title_path` JSON、`content` Text、`page_start`、`page_end`、`token_count`、`acl` JSON、`checksum`、`section_ids` JSON、`metadata_` mapped to DB column `metadata`、`deleted_at`。
  - [x] 外键绑定 `documents.id` 和 `document_versions.id`；不要把 foreign key 只绑到 `chunk_id`，因为 `chunk_id` 是业务标识，不是主键。
  - [x] 建立索引：`ix_chunks_tenant_id_status`、`ix_chunks_tenant_document_version`、`ix_chunks_tenant_chunk_id`、`ix_chunks_tenant_document_version_chunk_id`、`ix_chunks_document_id`、`ix_chunks_version_id`。
  - [x] 建立唯一约束：`uq_chunks_tenant_document_version_chunk_id`。

- [x] 新增 Alembic migration（AC: 2）
  - [x] 新建 `migrations/versions/20260527_0003_chunks.py`，`down_revision = "20260527_0002"`。
  - [x] migration 使用当前项目可移植 DDL 风格，优先保持 SQLite smoke 可运行；PostgreSQL/pgvector embedding 字段不要在本 story 提前实现。
  - [x] downgrade 必须按创建逆序删除索引/约束/表。
  - [x] 更新 `tests/integration/storage/test_alembic_migrations.py`，把 `chunks` 加入 expected tables，并断言 base columns、治理字段、source/page/token 字段、索引和唯一约束。

- [x] 实现 chunk repository 持久化（AC: 1, 3, 4, 5）
  - [x] 在 `packages/data/storage/repositories.py` 新增转换函数 `_chunk_model(record)` 和 `chunk_record_from_model(model)`，保持与 `DocumentRecord`/`DocumentVersionRecord` 的 DTO/model 分离模式一致。
  - [x] 在 `DocumentRepository` 中新增 `replace_chunks_for_version(...)`：同一 tenant/document/version 下先 soft-delete 或删除旧 active chunks，再写入新 chunks；MVP 可物理删除该版本旧 chunks，但必须在 story notes 中说明 Story 2.9 会统一软删除/版本治理。
  - [x] 新增 `list_chunks_for_version(tenant_id, document_id, version_id, status=None)`，按 `created_at` 或 `chunk_index` 稳定排序。
  - [x] 新增 `get_chunk(tenant_id, chunk_id, document_id=None, version_id=None)`，必须 tenant-scoped；找不到或跨租户返回 `None`。
  - [x] 新增 `mark_ingestion_job_chunked(tenant_id, job_id, chunk_metadata)`，更新 job/document/version status 为 `chunked`，version metadata 只合并安全摘要，不存 chunk content。
  - [x] SQLAlchemy errors 转换为 `StorageError`，details 只包含 `tenant_id`、`document_id`、`version_id`、`chunk_id`、`job_id` 等安全 ID。

- [x] 补充测试（AC: 1, 2, 3, 4, 5）
  - [x] 新增或扩展 `tests/integration/storage/test_document_repositories.py`，覆盖 `replace_chunks_for_version` 写入、同版本替换、按版本读取、按 chunk_id 读取、cross-tenant isolation。
  - [x] 覆盖 `ChunkRecord` 不带 SQLAlchemy state：返回 DTO 不应有 `_sa_instance_state`。
  - [x] 覆盖 `mark_ingestion_job_chunked` 推进 job/document/version 到 `chunked`，并断言 version metadata 只有 `chunk_count`、token range、checksum summary 等安全摘要。
  - [x] 覆盖 `page_start/page_end=None` 的 DOCX/TXT 场景，不得伪造页码。
  - [x] 覆盖重复 `chunk_id` 在同一 tenant/document/version 下违反唯一约束，repository 应抛出稳定 `StorageError`。
  - [x] 所有测试使用 synthetic chunks，不调用 embedding、LLM、vector store、MinIO、Redis、真实外部数据库或外部 API。

- [x] 更新文档和验证命令（AC: 1, 2, 5）
  - [x] 更新 `docs/api/upload.md`：说明 ingestion pipeline 的 chunk persistence 阶段和 `chunked` 状态，但不要宣称 embedding/vector indexing 已完成。
  - [x] 更新 `docs/operations/local-development.md`：加入 migration smoke、chunk repository 测试命令，以及安全 metadata 约束。
  - [x] 如 `README.md` 当前能力列表涉及 ingestion，补充 chunk metadata persistence 已实现，embedding/vector indexing 仍属后续 story。
  - [x] 运行 `.venv\Scripts\python.exe -m pytest tests/integration/storage/test_alembic_migrations.py`、`.venv\Scripts\python.exe -m pytest tests/integration/storage/test_document_repositories.py`、`.venv\Scripts\python.exe -m pytest tests/unit/ingestion/test_fixed_size_chunker.py`、`.venv\Scripts\python.exe -m pytest`、`.venv\Scripts\python.exe -m ruff check .`、`.venv\Scripts\python.exe -m mypy apps packages tests`。

### Review Findings

- [x] [Review][Patch] `get_chunk(tenant_id, chunk_id)` is ambiguous when chunk_id is not tenant-unique — resolved by enforcing `(tenant_id, chunk_id)` uniqueness in the `chunks` table/model and migration smoke.
- [x] [Review][Patch] `replace_chunks_for_version` accepts a version that belongs to another document [packages/data/storage/repositories.py:230]
- [x] [Review][Patch] `replace_chunks_for_version([])` deletes all existing chunks and succeeds [packages/data/storage/repositories.py:206]
- [x] [Review][Patch] `mark_ingestion_job_chunked` can mark records chunked without persisted chunk validation [packages/data/storage/repositories.py:310]
- [x] [Review][Patch] Empty chunk ACL maps are persisted instead of tenant-default ACL [packages/data/dto.py:299]
- [x] [Review][Defer] `create_upload_records` lacks cross-record tenant/document/version consistency checks [packages/data/storage/repositories.py:29] — deferred, pre-existing

## Dev Notes

### Current Repository State

- 当前目录不是 git repository，`git log` 不可用；本 story 基于现有源码、Story 2.1 到 2.5 记录、epics、architecture、PRD、UX 和项目规则生成。
- Story 2.5 已实现 `packages.ingestion.domain.Chunk`、`packages.ingestion.ports.Chunker`、`FixedSizeChunker`、chunker 错误码和单测。本 story 不要重复创建 chunker，也不要改默认 chunk 策略。
- 当前 `packages/data/storage/models.py` 只有 `DocumentModel`、`DocumentVersionModel`、`IngestionJobModel`；尚无 `chunks` 表。
- 当前 migration chain 为 `20260527_0001_governance -> 20260527_0002_document_upload`；本 story 应新增 `20260527_0003_chunks`。
- 当前 `DocumentRepository` 已负责 upload records、job parsing/parsed/failed 状态、tenant-scoped document/version/job 查询；本 story 应在同一 repository 模式上扩展 chunk 持久化，不要另起一个不一致的 storage abstraction。
- `tests/integration/storage/test_alembic_migrations.py` 使用 SQLite smoke 验证 portable DDL；新增 migration 必须保持该测试可运行。

### Architecture Requirements

- 本 story 位于 Storage Layer + Application Service Layer 边界：storage model/repository 在 `packages/data/storage/*`，DTO/port 在 `packages/data/*`。
- `packages/ingestion` 仍只拥有 `RawDocument -> ParsedDocument -> Section -> Chunk` 纯组件；不要让 ingestion domain import SQLAlchemy。
- `packages/retrieval`、`packages/rag`、`packages/agent` 后续只能消费 typed DTO/ports，不能直接 import `ChunkModel`。
- 所有 chunk 查询必须 tenant-scoped；检索权限过滤后续在 `packages/auth`/`packages/retrieval` 中实现，但本 story 必须保存 `acl` 字段并让 storage 查询支持 tenant/document/version/chunk 组合。
- `content` 可以存在 `chunks` 表中供 embedding 和 retrieval 使用，但不得进入 job metadata、安全摘要、日志或 audit resource metadata。
- Source metadata 必须支持 citation 和 Source Inspector：`source_type`、`source_uri`、`title_path`、`page_start`、`page_end`、`section_ids` 不得丢失。

### Current Files To Preserve And Extend

- `packages/ingestion/domain.py`
  - Current state: 已定义 frozen Pydantic v2 `Chunk`，含治理字段、页码校验、`section_ids`、`metadata`。
  - Story change: 通常无需修改；仅作为输入 contract 引用。
  - Preserve: 不把 SQLAlchemy、repository 或 DB 字段塞进 ingestion DTO。

- `packages/data/dto.py`
  - Current state: upload/object/job/document/version DTO。
  - Story change: 新增 `ChunkRecord` 和必要校验。
  - Preserve: DTO 不含 `_sa_instance_state`，不依赖 SQLAlchemy。

- `packages/data/ports.py`
  - Current state: `DocumentRepository` Protocol 覆盖 upload/job/parser 阶段。
  - Story change: 增加 chunk persistence 和 `chunked` 状态方法。
  - Preserve: Protocol 保持 async storage boundary，不接受 FastAPI request 或 LLM/provider 对象。

- `packages/data/storage/models.py`
  - Current state: SQLAlchemy 2.x typed declarative models，`metadata_` 映射到 DB `metadata` 字段。
  - Story change: 新增 `ChunkModel`，复用 `IdMixin`、`TimestampMixin`、`Mapped`、`mapped_column`、`Index`。
  - Preserve: `models.py` 只放 storage model，不放 repository logic。

- `packages/data/storage/repositories.py`
  - Current state: `DocumentRepository` 使用 `AsyncSession`、`select/update`、DTO/model mapper、`StorageError`。
  - Story change: 新增 chunk mapper 和 tenant-scoped chunk read/write 方法。
  - Preserve: SQLAlchemy 异常必须 rollback 或稳定转换；返回 DTO。

- `migrations/versions/20260527_0002_document_upload.py`
  - Current state: 最后一条 migration，创建 documents/document_versions/ingestion_jobs。
  - Story change: 不修改旧 migration，新增 `20260527_0003_chunks.py`。
  - Preserve: migration history 不重写。

- `tests/integration/storage/test_alembic_migrations.py`
  - Current state: SQLite smoke upgrade 到 head 并检查基础治理和 document tables。
  - Story change: 扩展 expected table 和 index/column assertions。
  - Preserve: 测试仍用 temporary SQLite，不要求本地 PostgreSQL。

- `tests/integration/storage/test_document_repositories.py`
  - Current state: 覆盖 upload records、parser job 状态、atomic claim。
  - Story change: 增加 chunk repository 测试。
  - Preserve: 使用 async SQLAlchemy session 和 migration helper，不接入外部服务。

### Previous Story Intelligence

- Story 2.1 建立授权上传、ObjectStorage port、DocumentRepository、RQ queue、ID-only payload、audit/error envelope；本 story 不应修改上传权限或 queue payload 结构。
- Story 2.2 建立 parser DTO、registry、Markdown/TXT parser、worker parser service 和 job 状态推进；本 story 不应重新设计 parser service。
- Story 2.3 完成 PDF/DOCX parser，并修复 parser job claim、payload mismatch、section 逐段校验、安全摘要、parser 异常映射和 service failure tests；本 story 的 storage 摘要必须延续“安全摘要而非正文”原则。
- Story 2.4 已实现 cleaner/dedup，强调不存完整 cleaned document 到 metadata；本 story 可以把 chunk content 存到 `chunks.content`，但不能复制到 version metadata。
- Story 2.5 建立 `Chunk` DTO、`Chunker` Protocol、`FixedSizeChunker`、稳定 UUIDv5 chunk_id、SHA-256 checksum、section lineage、title path summary、页码范围和 ACL consistency；本 story 应持久化这些字段，不要重新计算或放宽校验。
- Story 2.5 的 review 已修复：重复 section_id、ACL `None` 默认、页码范围、token estimator 使用、paragraph boundary 等问题。2.6 不应回退这些合同。

### Suggested Contracts

Storage DTO shape:

```python
class ChunkRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str | None = None
    tenant_id: str
    document_id: str
    version_id: str
    chunk_id: str
    created_by: str
    status: str
    source_type: str
    source_uri: str | None = None
    title_path: list[str]
    content: str
    page_start: int | None = None
    page_end: int | None = None
    token_count: int
    acl: dict[str, object] = Field(default_factory=dict)
    checksum: str
    section_ids: list[str]
    metadata: dict[str, object] = Field(default_factory=dict)
    deleted_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
```

Repository method shape:

```python
class DocumentRepository(Protocol):
    async def replace_chunks_for_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        chunks: list[ChunkRecord],
    ) -> list[ChunkRecord]:
        ...

    async def list_chunks_for_version(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        status: str | None = None,
    ) -> list[ChunkRecord]:
        ...

    async def get_chunk(
        self,
        *,
        tenant_id: str,
        chunk_id: str,
        document_id: str | None = None,
        version_id: str | None = None,
    ) -> ChunkRecord | None:
        ...
```

### Implementation Boundaries

- 不要实现 embedding job、EmbeddingProvider、VectorStore、pgvector 列、sparse index、retrieval API 或 Source Inspector API；它们属于后续 stories。
- 不要改 `/upload` 为同步等待 chunking、embedding 或 indexing 完成。
- 不要让 LLM、prompt、LangChain 或外部 tokenizer 参与 chunk metadata persistence。
- 不要在 `document_versions.metadata` 中存 full chunks、full content、prompt-like content、企业原文片段、API key、access token 或本机绝对路径。
- 不要让 `chunk_id` 全局唯一成为唯一查询条件；tenant/version/document 仍是隔离边界。
- 不要把旧版本 chunk 覆盖成新版本 chunk；同一 document 的不同 `version_id` 必须可并存。
- 不要提前实现完整软删除治理；如 MVP 替换同版本 chunk 使用物理删除，必须保留 version 维度并为 Story 2.9 留出软删除扩展。
- 不要在 repository 中做 RBAC 判断；repository 只做 tenant-scoped storage，权限表达和 ACL filter builder 属于 auth/retrieval 层。

### Latest Technical Information

- SQLAlchemy 官方站点显示当前稳定 2.0 release 为 2.0.50，发布时间 2026-05-24；2.1.0b2 是 beta，不应为本 story 升级到 beta 线。[Source: https://www.sqlalchemy.org/]
- SQLAlchemy 2.0 文档仍推荐 `DeclarativeBase`、`Mapped`、`mapped_column` 的 typed declarative mapping；本仓库现有 `Base`/model 风格与该模式一致。[Source: https://docs.sqlalchemy.org/en/20/orm/mapping_styles.html]
- SQLAlchemy 2.0 文档说明 `mapped_column()` 可从 `Mapped[...]` 类型标注推导 datatype/nullability；新增 model 应保持显式 `String`/`Integer`/`JSON`/`Text` 字段以匹配当前项目迁移风格。[Source: https://docs.sqlalchemy.org/20/orm/declarative_tables.html]
- Architecture 当前记录 Alembic 1.x 作为 migration 基线，且现有 tests 已通过 SQLite smoke 验证 portable DDL；本 story 不应引入 PostgreSQL-only DDL，pgvector 在 Story 2.8 落地。

### UX / Product Notes

- 本 story 不实现前端，但 Source Inspector、citation chip、Knowledge Admin job status 都依赖本 story 的字段完整性。
- UX 要求 citation 不由前端或 LLM 猜测；后端必须返回 document/version/chunk/page/source metadata。
- Source Inspector 打开 citation 时必须重新校验 AuthContext、tenant、RBAC、ACL、soft delete 和 version visibility；本 story 只提供足够 storage 字段，权限逻辑后续实现。
- Knowledge Admin 后续应能展示 chunk_count、checksum、状态和安全错误摘要；不得展示未授权 chunk 正文。

### References

- `_bmad-output/planning-artifacts/epics.md#Story 2.6`
- `_bmad-output/planning-artifacts/epics.md#Epic 2`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-3-文档清洗Chunking`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-22-RBAC-与-ACL-检索过滤`
- `_bmad-output/planning-artifacts/architecture.md#Implementation-Patterns-Consistency-Rules`
- `_bmad-output/planning-artifacts/architecture.md#Architectural-Boundaries`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md#Interaction-Patterns`
- `_bmad-output/implementation-artifacts/2-5-fixedsizechunker.md`
- `project-context.md`
- `packages/ingestion/domain.py`
- `packages/ingestion/chunkers/fixed_size.py`
- `packages/data/dto.py`
- `packages/data/ports.py`
- `packages/data/storage/models.py`
- `packages/data/storage/repositories.py`
- `migrations/versions/20260527_0002_document_upload.py`
- `tests/integration/storage/test_alembic_migrations.py`
- `tests/integration/storage/test_document_repositories.py`
- `https://www.sqlalchemy.org/`
- `https://docs.sqlalchemy.org/en/20/orm/mapping_styles.html`
- `https://docs.sqlalchemy.org/20/orm/declarative_tables.html`

## Validation Checklist

Validation Result: PASS（2026-06-06T16:18:29+08:00）

- [x] Story 明确了用户角色、目标和收益。
- [x] Acceptance Criteria 覆盖 Epic Story 2.6 的 typed metadata contract、chunks migration、tenant-scoped persistence、retrieval/citation/source metadata 和安全摘要。
- [x] Tasks 覆盖 DTO、port、SQLAlchemy model、Alembic migration、repository、integration tests 和 docs。
- [x] Dev Notes 明确复用 Story 2.5 的 `Chunk` DTO 和 `FixedSizeChunker`，不重新实现 chunker。
- [x] 明确 storage model/DTO/domain 分离，不暴露 SQLAlchemy model 给 ingestion/retrieval/rag/API。
- [x] 明确 tenant/document/version/chunk 查询、唯一约束和 cross-tenant 隔离。
- [x] 明确禁止提前实现 embedding/vector/retrieval/source inspector，以及禁止将 chunk content 写入日志、audit 或 version metadata。
- [x] 包含当前代码文件状态、前序 story 经验、架构/PRD/UX 约束、最新 SQLAlchemy 技术参考和实现边界。

## Change Log

- 2026-06-06: Created comprehensive Story 2.6 developer context for chunk metadata contract, `chunks` storage model, Alembic migration, repository persistence, tenant isolation, status summary, tests and docs.
- 2026-06-06: Implemented Story 2.6 chunk metadata DTO, storage model, migration, repository persistence, tests, docs, and validation.
- 2026-06-06: Resolved code review findings for tenant-unique chunk lookup, document/version scope validation, empty chunk replacement, chunked status validation, and ACL defaulting.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- 2026-06-06T16:24+08:00: Red tests added and confirmed failing for missing `chunks` migration and `ChunkRecord`.
- 2026-06-06T16:39+08:00: Storage migration and document repository tests passed.
- 2026-06-06T16:43+08:00: Full regression suite, ruff, and mypy passed.
- 2026-06-06T17:01+08:00: Code review fixes passed full regression suite, ruff, and mypy.

### Completion Notes List

- Added frozen `ChunkRecord` DTO with required governance/source/citation metadata, page range validation, tenant-default ACL normalization, and metadata mapping normalization.
- Added tenant-scoped chunk repository methods returning DTOs only: replace/list/get plus `mark_ingestion_job_chunked` with safe version metadata summary.
- Code review fixes enforce tenant-unique `chunk_id`, document/version scope matching, non-empty chunk replacement, persisted chunk count checks before `chunked`, and tenant-default ACL normalization for empty ACL maps.
- Added portable `chunks` migration and SQLAlchemy model with tenant/document/version/chunk indexes and uniqueness.
- `replace_chunks_for_version` uses physical replacement for the same tenant/document/version in this MVP; Story 2.9 remains responsible for unified soft delete/version governance.
- Updated ingestion docs and README to describe chunk persistence while keeping embedding and vector indexing as later work.

### File List

- `packages/data/dto.py`
- `packages/data/ports.py`
- `packages/data/storage/models.py`
- `packages/data/storage/repositories.py`
- `migrations/versions/20260527_0003_chunks.py`
- `tests/integration/storage/test_alembic_migrations.py`
- `tests/integration/storage/test_document_repositories.py`
- `docs/api/upload.md`
- `docs/operations/local-development.md`
- `README.md`
- `_bmad-output/implementation-artifacts/2-6-chunk-metadata-contract-与持久化.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

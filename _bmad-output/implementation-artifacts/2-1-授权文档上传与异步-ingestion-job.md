---
baseline_commit: NO_VCS
---

# Story 2.1: 授权文档上传与异步 Ingestion Job

Status: done

生成时间：2026-05-27T14:17:26+08:00

## Story

As a 知识库管理员,
I want 上传文档后立即获得文档版本和 job 状态,
so that 大文件 embedding 不会阻塞上传体验。

## Acceptance Criteria

1. **授权上传立即返回文档、版本和 job 状态**
   - Given 授权用户提交 PDF、DOCX、TXT 或 Markdown 文件
   - When 调用 `POST /upload`
   - Then API 返回 `document_id`、`version_id`、`job_id`、`status`
   - And 初始状态为 `uploaded` 或 `parsing`

2. **缺少文档管理权限时拒绝且没有副作用**
   - Given 上传请求缺少文档管理权限
   - When 调用 `POST /upload`
   - Then API 返回结构化权限错误
   - And 不写入 object storage、document metadata 或 queue job

3. **上传成功后保存可追溯文档 metadata**
   - Given 上传成功
   - When metadata 被保存
   - Then `documents` 和 `document_versions` 包含 `tenant_id`、`created_by`、`source_type`、`source_uri`、`acl`、`checksum`、`status`
   - And worker queue payload 只包含 IDs，不包含文件内容

4. **首次引入 documents 和 document_versions migration**
   - Given `documents` 和 `document_versions` 表首次引入
   - When Alembic migration 生成
   - Then 两张表包含 `id`、`created_at`、`updated_at`、`tenant_id`、`created_by`、`status`、`acl`、`checksum` 和 source metadata
   - And 支持按 `tenant_id`、`document_id`、`version_id`、`status` 查询

## Tasks / Subtasks

- [x] 定义上传 API contract 与薄 route（AC: 1, 2）
  - [x] 新增 `apps/api/routes/upload.py` 并在 `apps/api/main.py` 注册 router；route 只负责 HTTP/multipart 解析、依赖注入和调用 application service。
  - [x] `POST /upload` 使用 `AuthenticatedRequestContextDep`，必须带 `request_id`、`trace_id`、`tenant_id`、`user_id`；缺少 AuthContext 继续走现有 401 envelope。
  - [x] multipart 字段至少包含 `file`、`source_type`、可选 `source_uri`、可选 `title`、可选 `acl` JSON、可选 `metadata` JSON；JSON 字段解析失败返回结构化 validation/domain error。
  - [x] route 不得直接写 DB、对象存储、队列、checksum、审计或业务权限逻辑。

- [x] 实现 document upload application service（AC: 1, 2, 3）
  - [x] 新增 `packages/data/service.py`，实现 `DocumentUploadService` 或等价 application service。
  - [x] 新增 `packages/data/dto.py`，定义 `UploadDocumentCommand`、`UploadDocumentResult`、`DocumentRecord`、`DocumentVersionRecord`、`IngestionJobRecord` 等内部 DTO。
  - [x] service 显式接收 `AuthenticatedRequestContext` 或 `RequestContext + AuthContext`，禁止从全局状态读取用户、租户或权限。
  - [x] 在任何 object storage、DB 或 queue side effect 前检查权限；建议接受 `document:upload` 或 `document:manage`，缺失时抛出稳定权限错误并保证 fakes 记录无副作用。
  - [x] 允许的上传格式限定为 PDF、DOCX、TXT、Markdown；同时校验扩展名、`source_type` 和安全 content type，错误返回稳定错误码。
  - [x] 增加可配置上传大小限制，例如 `UPLOAD_MAX_BYTES`；读取文件时按 chunk 计算 byte size 和 SHA-256 checksum，不要一次性读入完整文档内容。
  - [x] 成功时返回 `document_id`、`version_id`、`job_id`、`status`，并使用统一 `success_response` envelope。

- [x] 引入 ObjectStorage 端口和 MinIO/S3-compatible adapter（AC: 2, 3）
  - [x] 新增 `packages/data/ports.py` 或等价文件，定义 async `ObjectStorage` Protocol，方法至少支持保存 raw file 并返回 `object_key`/`etag`/size 摘要。
  - [x] 新增 `packages/data/adapters/minio_object_storage.py` 或等价 infrastructure adapter；业务 service 只能依赖 Protocol，不能直接依赖 MinIO SDK。
  - [x] 如实现真实 MinIO adapter，新增 `minio>=7.2.20,<8` 到 `pyproject.toml` 并更新 `uv.lock`；adapter 必须设置 timeout，不记录 endpoint、access key、secret、bucket 内部敏感路径或文件全文。
  - [x] 单元测试默认使用 Fake/InMemory ObjectStorage，不要求真实 MinIO；真实 MinIO smoke 可通过 Docker Compose 手动验证。
  - [x] 权限拒绝、文件类型错误或 metadata validation 错误时不得调用 object storage。

- [x] 新增文档治理 storage models、repositories 和 migration（AC: 3, 4）
  - [x] 新增 `packages/data/storage/models.py`，至少定义 `DocumentModel`、`DocumentVersionModel`；建议同时定义 `IngestionJobModel`，使 DB 成为 job 状态真相。
  - [x] 新增 `packages/data/storage/repositories.py`，返回 Pydantic DTO，不把 SQLAlchemy model 泄漏给 domain/application 层。
  - [x] 新增 Alembic migration，创建 `documents`、`document_versions`，并为 job source of truth 创建 `ingestion_jobs` 或等价持久模型。
  - [x] `documents` 至少包含 `id`、`created_at`、`updated_at`、`tenant_id`、`created_by`、`status`、`source_type`、`source_uri`、`title`、`acl`、`checksum`、`metadata`、可选 `deleted_at`。
  - [x] `document_versions` 至少包含 `id`、`document_id`、`created_at`、`updated_at`、`tenant_id`、`created_by`、`status`、`source_type`、`source_uri`、`object_key`、`filename`、`content_type`、`byte_size`、`acl`、`checksum`、`metadata`。
  - [x] `ingestion_jobs` 如新增，至少包含 `id`、`created_at`、`updated_at`、`tenant_id`、`created_by`、`status`、`document_id`、`version_id`、`queue_name`、`queue_job_id`、`attempt_count`、`error_code`、`last_attempt_at`、`next_retry_at`。
  - [x] 添加索引：`documents(tenant_id, status)`、`documents(tenant_id, id)`、`document_versions(tenant_id, document_id)`、`document_versions(tenant_id, status)`、`document_versions(tenant_id, document_id, id)`；job 表按 `tenant_id/status/version_id` 可查。
  - [x] 不在本 Story 实现 chunk、embedding、VectorStore、soft delete 重建索引或重复上传版本策略；这些属于后续 Story 2.2-2.9。

- [x] 接入受限 RQ ingestion queue（AC: 1, 3）
  - [x] 新增 `apps/worker/jobs/ingestion_jobs.py` 或等价入口，提供可导入的 job target；当前 Story 只接受 ID payload，不实现 parser/chunk/embedding。
  - [x] 新增 queue adapter 或 helper，复用 `packages.data.queue.contracts.QueuePayload` 和 `packages.data.queue.rq_worker.create_queue` 的 JSON serializer。
  - [x] queue payload 必须只包含 `request_id`、`tenant_id`、`user_id`、`job_type`、`resource_id=job_id`、`parameters={document_id, version_id}`；禁止文件内容、`UploadFile`、SQLAlchemy model、AuthContext、prompt、token、API key、本机绝对路径。
  - [x] enqueue 失败必须转为领域错误并更新/回滚 job 状态；不要留下“API 成功但没有持久 job 状态”的不可追踪状态。
  - [x] 初始状态可选择 `uploaded`，若 worker 立即接管可改为 `parsing`；必须使用既有稳定 job status 集合。

- [x] 补充审计、日志和错误映射（AC: 1, 2, 3）
  - [x] 上传成功、权限拒绝、文件验证失败、object storage 失败、queue enqueue 失败都记录 `AuditEvent`，包含 `request_id`、`trace_id`、`tenant_id`、`user_id`、`action`、`resource`、`latency_ms`、`status`、`error_code`。
  - [x] 审计 metadata 只记录摘要：source_type、content_type、byte_size、checksum、document_id、version_id、job_id；不得记录文件全文、token、secret、原始本机绝对路径。
  - [x] 新增或复用稳定错误码：`DOCUMENT_UPLOAD_FORBIDDEN`、`DOCUMENT_UPLOAD_UNSUPPORTED_TYPE`、`DOCUMENT_UPLOAD_TOO_LARGE`、`DOCUMENT_UPLOAD_INVALID_METADATA`、`DOCUMENT_STORAGE_WRITE_FAILED`、`INGESTION_JOB_ENQUEUE_FAILED`。
  - [x] 权限错误应返回 HTTP 403 envelope；如当前 `DomainError` 统一映射 400，需要扩展错误处理或引入可携带 status 的领域异常，避免权限拒绝被误报为 400。

- [x] 更新配置和文档（AC: 1, 3）
  - [x] 扩展 `packages/common/config.py`，读取 `UPLOAD_MAX_BYTES`、对象存储 bucket/prefix 或 adapter 所需配置；不要硬编码 tenant、user、bucket secret 或本机绝对路径。
  - [x] 更新 `.env.example`，只给占位值和安全本地示例，不提交真实 secret。
  - [x] 更新 `README.md`、`docs/operations/local-development.md`，说明 `/upload` multipart 示例、dev auth headers/JWT、权限、返回字段、job 状态和本地 Compose 依赖。
  - [x] 可新增 `docs/api/upload.md` 记录请求/响应/错误码；文档必须说明上传不会等待 parser、chunk 或 embedding 完成。

- [x] 补充测试与验证（AC: 1, 2, 3, 4）
  - [x] 单元测试：DTO validation、ACL/metadata JSON parsing、file type/size validation、checksum chunked calculation、permission check before side effects、queue payload 只含 IDs。
  - [x] 单元测试：Fake ObjectStorage、Fake DocumentRepository、Fake JobQueue、Fake AuditPort 断言授权成功和权限拒绝的 side effects。
  - [x] storage integration 测试：Alembic migration 后创建 document/version/job 并返回 DTO；支持按 tenant/document/version/status 查询；跨租户查询不返回对方数据。
  - [x] API integration 测试：`POST /upload` 成功返回 envelope 和 IDs；缺少权限返回 403 envelope；缺少 AuthContext 返回 401；非法 metadata 返回结构化错误；拒绝时无 object storage / DB / queue side effect。
  - [x] 队列测试：继续覆盖 `QueuePayload` 拒绝 bytes、file handle、SQLAlchemy model、本机绝对路径、token/prompt/document_content。
  - [x] 运行 `uv run pytest`、`uv run ruff check .`、`uv run mypy apps packages tests`；如新增 migration，运行 Alembic upgrade smoke。

### Review Findings

- [x] [Review][Patch] Queue job is published before DB job state is durable [packages/data/service.py:185]
- [x] [Review][Patch] Object storage write has no compensation if DB persistence fails [packages/data/service.py:119]
- [x] [Review][Patch] Failure audit records are not committed on most domain failures [packages/data/service.py:301]
- [x] [Review][Patch] Route-level DTO validation can escape structured error handling and return 500 [apps/api/routes/upload.py:54]
- [x] [Review][Patch] Upload route owns infrastructure wiring instead of only HTTP parsing and service invocation [apps/api/routes/upload.py:14]
- [x] [Review][Patch] Missing content type is accepted despite safe content-type validation requirement [packages/data/service.py:409]
- [x] [Review][Patch] Worker target accepts arbitrary dict payload instead of validating QueuePayload ID-only contract [apps/worker/jobs/ingestion_jobs.py:4]
- [x] [Review][Patch] Document version lookup by tenant_id and version_id is missing [packages/data/storage/repositories.py:92]
- [x] [Review][Patch] User-supplied ACL is persisted without schema or authority checks [apps/api/routes/upload.py:60]
- [x] [Review][Patch] API and worker queue defaults diverge, so uploads can enqueue to a queue no worker consumes [packages/common/config.py:18]
- [x] [Review][Patch] Empty uploads are accepted and enqueued [packages/data/service.py:378]
- [x] [Review][Patch] DB length limits are enforced only after object storage write [packages/data/dto.py:17]

## Dev Notes

### 当前仓库状态

- 当前目录不是 git repository，无法读取 commit 历史；Story context 基于现有文件扫描和 Story 1.1-1.6 的 Dev Notes。
- `apps/api/main.py` 当前只注册 health router；新增 `/upload` 时需要注册 upload router，但不要在 app startup 连接 MinIO、Redis 或运行 migration。
- `apps/api/dependencies.py` 已提供 `AuthenticatedRequestContextDep`，会统一产出 `RequestContext + AuthContext`；business endpoints 必须使用它。
- `apps/api/error_handlers.py` 目前把所有 `DomainError` 映射为 HTTP 400；本 Story 的权限拒绝需要 HTTP 403，建议扩展错误映射。
- `packages/auth/policies.py` 当前只有 `build_access_filter()`；上传权限需要新增明确 policy helper 或由 data service 内部做稳定权限检查，不能交给 prompt 或 LLM。
- `packages/common/audit.py` 已有 `AuditPort`、`AuditEvent`、`AuditStatus`、`AuditResource` 和 redaction；Story 2.1 应复用它。
- `packages/data/queue/contracts.py` 已有安全 `QueuePayload`，拒绝敏感字段、非 JSON、绝对路径和 secret-like value；不要绕过该 contract。
- `packages/data/queue/rq_worker.py` 已显式使用 RQ `JSONSerializer` 和 Redis socket timeout；新增 ingestion enqueue 应复用该安全边界。
- `packages/data/storage/base.py` 已有 shared SQLAlchemy `Base`、`IdMixin`、`TimestampMixin`；新增 storage models 应复用这些 mixin。
- `migrations/versions/20260527_0001_governance.py` 已创建 tenants/users/roles/user_roles/audit_logs；本 Story 应新增独立 revision，不修改已应用基线 migration。
- Docker Compose 已有 `postgres`、`redis`、`minio`、`api`、`worker-ingestion`、`worker-embedding`；上传实现可依赖这些配置，但单元测试不得要求真实服务。

### Source Context

- Story 2.1 覆盖 FR1：授权用户上传 PDF/DOCX/TXT/Markdown，立即返回 `document_id`、`version_id`、`job_id` 和初始状态，不等待 embedding 完成。[Source: `_bmad-output/planning-artifacts/epics.md#Story 2.1`]
- PRD UJ-2 要求上传写入 raw document metadata 和 object storage，创建 ingestion job，后续 worker 完成 parse、clean、dedup、chunk、embedding、index，状态最终到 `retrieval_ready`。[Source: `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#2.4-关键用户旅程`]
- PRD FR-18 要求所有 API 返回统一 data/error/metadata envelope，route 层不得直接调用复杂业务逻辑。[Source: `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-18`]
- PRD FR-21/FR-23 要求 application service 显式接收 AuthContext，并对上传等关键业务行为写审计日志。[Source: `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-21`]
- Architecture 决定 Redis + RQ 为 MVP 默认异步任务方案，MinIO/S3-compatible ObjectStorage 通过端口接入，上传接口禁止同步等待 embedding。[Source: `_bmad-output/planning-artifacts/architecture.md#Core-Architectural-Decisions`]
- Architecture 明确 `apps/api/routes/*` 只拥有 HTTP contract，routes 调 application services，不能直接调用 infrastructure adapters。[Source: `_bmad-output/planning-artifacts/architecture.md#Architectural-Boundaries`]
- UX Flow 2 要求 Knowledge Admin 上传后立即看到 `document_id`、`version_id`、`job_id`，job row 展示 parsing/chunking/embedding/indexing 等状态，失败显示安全错误摘要。[Source: `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md#Flow-2`]

### Architecture Requirements

- 本 Story 横跨 API Layer、Application Service Layer、Infrastructure Layer 和 Storage Layer；它不实现 parser/chunker/embedding/retrieval/RAG/Agent。
- API route 只能解析 multipart/form-data 和注入依赖；上传副作用顺序、权限、checksum、对象存储、DB、队列和审计必须在 application service 或 adapters 中完成。
- `packages/data` 是 upload/API contract 和文档治理 storage 的主位置；`packages/ingestion` 仍保留给 RawDocument/ParsedDocument/Section/Chunk 以及 parser/chunker。
- `ObjectStorage` 必须是端口；MinIO 是 adapter。业务 service 不得 import `minio`、`boto3`、`httpx` 或 SDK 类。
- Domain/DTO 不得依赖 FastAPI `UploadFile`。route 应把 FastAPI 对象转换为内部 stream/metadata DTO 后传入 service。
- DB 是 document/version/job 状态真相；Redis/RQ 只能是 queue/ephemeral worker 状态，不能成为唯一 job 状态来源。
- Worker job payload 只能是 ID 和摘要；文件内容只在 object storage 中，metadata 和 job status 只在 DB 中。
- Migration 是 schema 真相；不要在 app startup 或 tests 中用 `Base.metadata.create_all()` 代替 Alembic。

### Current Files To Preserve And Extend

- `apps/api/main.py`
  - Current state: 配置 logging middleware、error handlers、health router。
  - Story change: 注册 upload router。
  - Preserve: app import 不连接外部服务，不运行 migration，不创建 schema。

- `apps/api/dependencies.py`
  - Current state: 提供 request/auth/authenticated context dependency，支持 dev headers 和 JWT。
  - Story change: `/upload` 使用 `AuthenticatedRequestContextDep`。
  - Preserve: 所有业务请求共享同一个 AuthContext DTO；生产默认不信任 dev headers。

- `apps/api/error_handlers.py`
  - Current state: Auth 错误映射 401，DomainError 映射 400。
  - Story change: 权限拒绝需要 403 envelope，可新增 `PermissionDeniedError` 或 status-aware DomainError handler。
  - Preserve: error details redaction，不泄露 token、文件内容或本机路径。

- `packages/auth/policies.py`
  - Current state: `build_access_filter()` 为检索 ACL filter 建立结构化数据。
  - Story change: 新增上传权限检查 helper 时保持结构化策略，不写 prompt 文本。
  - Preserve: tenant/user/roles/department/permissions 不可变 DTO 语义。

- `packages/common/audit.py`
  - Current state: 定义 audit DTO 和 `AuditPort`。
  - Story change: 上传 service 使用 `AuditPort.record()` 记录 success/failure/denied。
  - Preserve: metadata redaction 和不记录敏感全文。

- `packages/data/queue/contracts.py`
  - Current state: `QueuePayload` 已限制 JSON、安全 key、secret-like value、本机绝对路径。
  - Story change: ingestion queue payload 复用此 DTO。
  - Preserve: 不允许 file object、bytes、AuthContext、SQLAlchemy model、文档全文、prompt、token。

- `packages/data/queue/rq_worker.py`
  - Current state: RQ worker/queue 使用 `JSONSerializer`，Redis 连接有 timeout。
  - Story change: queue adapter 使用 `create_queue()` 或同等安全配置。
  - Preserve: 不退回 RQ 默认 pickle serializer。

- `packages/data/storage/base.py`
  - Current state: SQLAlchemy Base/ID/timestamp mixins。
  - Story change: 新增 document/version/job models 复用它。
  - Preserve: storage model 与 DTO 分离。

- `migrations/versions/20260527_0001_governance.py`
  - Current state: tenants/users/roles/user_roles/audit_logs 基线。
  - Story change: 新增后续 revision。
  - Preserve: 不重写已存在 migration。

### Previous Story Intelligence

- Story 1.6 已建立 Docker Compose 的 PostgreSQL、Redis、MinIO、API 和 worker 依赖栈；Story 2.1 不需要重新设计 Compose，只需要复用配置。
- Story 1.6 的 queue payload review 发现敏感字段、绝对路径和非标准 JSON 数字容易漏掉；本 Story 必须继续使用现有 `QueuePayload`，不要自定义宽松 payload。
- Story 1.5 已建立 SQLAlchemy storage pattern：repository 返回 Pydantic DTO，捕获 SQLAlchemyError 并转为 `StorageError`；document repository 应保持同一风格。
- Story 1.4 已建立 request logging 和 redaction；上传日志和审计不得记录 request body、文档内容、token、secret、MinIO 凭据或本机绝对路径。
- Story 1.3 已建立 AuthContext dependency；不要在 upload route 中手写 header 解析。
- 前序验证命令应保持通过：`uv run pytest`、`uv run ruff check .`、`uv run mypy apps packages tests`。

### File Structure Guidance

建议最小落地文件集：

```text
apps/api/main.py                              # UPDATE: register upload router
apps/api/routes/upload.py                     # NEW: POST /upload HTTP contract only
apps/worker/jobs/__init__.py                  # NEW optional
apps/worker/jobs/ingestion_jobs.py            # NEW: ID-only RQ job target stub
packages/auth/policies.py                     # UPDATE: explicit upload permission helper if chosen
packages/data/dto.py                          # NEW: document/upload/job DTOs
packages/data/exceptions.py                   # NEW: upload/domain errors
packages/data/ports.py                        # NEW: ObjectStorage, JobQueue protocols
packages/data/service.py                      # NEW: DocumentUploadService orchestration
packages/data/adapters/__init__.py            # NEW optional
packages/data/adapters/minio_object_storage.py # NEW if real MinIO adapter implemented
packages/data/storage/models.py               # NEW: documents, document_versions, ingestion_jobs
packages/data/storage/repositories.py         # NEW: document/version/job repositories
migrations/versions/<next>_document_upload.py # NEW: document governance tables
tests/unit/data/test_document_upload_service.py
tests/unit/data/test_document_upload_dto.py
tests/unit/data/test_object_storage_ports.py   # optional fake adapter/contract tests
tests/integration/api/test_upload_routes.py
tests/integration/storage/test_document_repositories.py
tests/integration/storage/test_alembic_migrations.py # UPDATE for new revision
README.md                                     # UPDATE: upload usage
docs/operations/local-development.md          # UPDATE: upload local testing
docs/api/upload.md                            # NEW optional
.env.example                                  # UPDATE if new upload/object storage settings added
pyproject.toml                                # UPDATE only if MinIO SDK dependency is added
uv.lock                                       # UPDATE if dependency changes
```

If implementation chooses different names, it must still preserve:

- route declarations only under `apps/api/routes`;
- `packages/common` remains free of FastAPI/SQLAlchemy/Redis/MinIO/httpx imports;
- business service depends on Protocols, not direct SDKs;
- storage models stay in storage layer and DTOs stay outside storage models;
- tests cover permission-denied no-side-effect behavior.

### Suggested Contracts

Object storage port:

```python
from collections.abc import BinaryIO
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class StoredObject(BaseModel):
    model_config = ConfigDict(frozen=True)

    bucket: str
    object_key: str
    etag: str | None = None
    byte_size: int
    checksum: str


class ObjectStorage(Protocol):
    async def put_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        filename: str,
        content_type: str | None,
        stream: BinaryIO,
        byte_size: int,
        checksum: str,
    ) -> StoredObject:
        ...
```

Upload service result:

```python
class UploadDocumentResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    version_id: str
    job_id: str
    status: str
```

Queue payload shape:

```json
{
  "request_id": "req-123",
  "tenant_id": "tenant-abc",
  "user_id": "user-123",
  "job_type": "ingestion.process_document",
  "resource_id": "job-123",
  "parameters": {
    "document_id": "doc-123",
    "version_id": "ver-123"
  }
}
```

### Implementation Boundaries

- 不要实现 Markdown/TXT/PDF/DOCX parser；Story 2.2 和 2.3 负责。
- 不要实现 cleaner、dedup、chunker、embedding provider、embedding job、VectorStore、retrieval、RAG generation、citation 或 Agent。
- 不要让 `/upload` 同步等待 parser、chunk、embedding 或 vector indexing。
- 不要把上传权限写入 prompt，不要让 LLM 判断用户权限。
- 不要把 ACL 存为自然语言 prompt；必须是结构化 JSON。
- 不要在 queue payload、日志、审计、错误响应中记录文件全文、prompt、token、secret、API key、MinIO secret、本机绝对路径或企业敏感原文。
- 不要把 `UploadFile`、file handle、SQLAlchemy model、AuthContext 对象或任意 Python object 直接入队。
- 不要在 `packages/common` 中新增 MinIO、Redis、SQLAlchemy、FastAPI、httpx 依赖。
- 不要重写已存在 migration；新增 revision。

### Latest Technical Information

- 2026-05-27 通过 PyPI JSON 验证，当前 `pyproject.toml` baseline 仍匹配最新已发布版本：FastAPI `0.136.3`、Pydantic `2.13.4`、SQLAlchemy `2.0.50`、Alembic `1.18.4`、RQ `2.9.0`。本 Story 不需要扩大这些版本范围。
- MinIO Python SDK 当前 PyPI 版本为 `7.2.20`。如果 Story 2.1 实现真实 MinIO adapter，使用 `minio>=7.2.20,<8`，并保持 adapter 在 infrastructure 层。[Source: https://pypi.org/project/minio/]
- FastAPI 官方文件上传文档建议用 `UploadFile` 处理上传文件；它适合 multipart upload，并支持文件对象/异步读取。route 仍需把 FastAPI 类型转换为内部 DTO，不把 `UploadFile` 传入 domain/service。[Source: https://fastapi.tiangolo.com/tutorial/request-files/]
- MinIO Python SDK 文档提供 `put_object` 等对象写入 API。由于 SDK 是具体 adapter 细节，业务逻辑必须通过 `ObjectStorage` port 调用。[Source: https://docs.min.io/enterprise/aistor-object-store/developers/sdk/python/api/]
- RQ 官方文档支持 serializer 配置；本仓库已经用 `JSONSerializer` 避免默认 pickle 风险，新增 enqueue 不得绕过它。[Source: https://python-rq.org/docs/jobs/]
- SQLAlchemy asyncio 文档要求 async session/transaction 边界显式管理；repository 应使用 `AsyncSession`、`flush`、`refresh` 和事务边界，不把 model 泄漏给上层。[Source: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html]

### UX / Frontend Notes

- 本 Story 不实现自定义前端，但 `/upload` response 和 job metadata 会被 Knowledge Admin/job row 使用。
- Response 必须包含可复制的 `request_id` 和 `job_id`，状态值必须稳定，方便后续 job status UI。
- 权限拒绝文案和错误响应不得说明未授权资源是否存在；当前上传场景没有资源探测，但仍应保持统一安全语义。
- Job status row 后续需要显示 `uploaded -> parsing -> parsed -> chunking -> chunked -> embedding -> indexing -> retrieval_ready`，失败区分 `failed_retryable` 和 `failed_terminal`。
- 管理端后续会按 status/source_type/created_by/updated_at/error_code 过滤；storage schema 和 API response 应保留这些字段。

### References

- `_bmad-output/planning-artifacts/epics.md#Story 2.1`
- `_bmad-output/planning-artifacts/epics.md#Additional Requirements`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-1`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-18`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-21`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-23`
- `_bmad-output/planning-artifacts/architecture.md#Core Architectural Decisions`
- `_bmad-output/planning-artifacts/architecture.md#API & Communication Patterns`
- `_bmad-output/planning-artifacts/architecture.md#Implementation Patterns & Consistency Rules`
- `_bmad-output/planning-artifacts/architecture.md#Project Structure & Boundaries`
- `_bmad-output/planning-artifacts/architecture.md#Integration Points`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md#Flow 2`
- `apps/api/main.py`
- `apps/api/dependencies.py`
- `apps/api/error_handlers.py`
- `packages/auth/policies.py`
- `packages/common/audit.py`
- `packages/data/queue/contracts.py`
- `packages/data/queue/rq_worker.py`
- `packages/data/storage/base.py`
- `migrations/versions/20260527_0001_governance.py`
- `tests/unit/test_architecture_boundaries.py`
- `https://fastapi.tiangolo.com/tutorial/request-files/`
- `https://docs.min.io/enterprise/aistor-object-store/developers/sdk/python/api/`
- `https://python-rq.org/docs/jobs/`
- `https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html`
- `https://pypi.org/project/minio/`

## Validation Checklist

Validation Result: PASS（2026-05-27T14:17:26+08:00）

- [x] Story 明确了用户角色、目标和收益。
- [x] Acceptance Criteria 完整覆盖 Epic Story 2.1 的授权上传、权限拒绝无副作用、metadata 持久化和 migration 要求。
- [x] Tasks 覆盖 API、application service、ObjectStorage port、storage/migration、RQ queue、审计、配置、文档和测试，并标注 AC 映射。
- [x] Dev Notes 包含当前代码状态、架构边界、前序 Story 经验、推荐文件位置、测试要求和实现边界。
- [x] 明确要求 route 薄层、AuthContext 显式传入、权限检查先于任何副作用、queue payload 只包含 IDs。
- [x] 明确要求 DB 是 document/version/job 状态真相，Redis/RQ 不能成为唯一 job 状态来源。
- [x] 明确禁止同步 parser/chunk/embedding、prompt 权限、直接 SDK 绑定、`UploadFile` 进入 domain、文件全文入队和敏感信息入日志/审计。
- [x] 包含 FastAPI UploadFile、MinIO SDK、RQ serializer、SQLAlchemy asyncio 和当前 PyPI 版本的最新技术参考。

## Change Log

- 2026-05-27: Created comprehensive Story 2.1 developer context for authorized document upload, document/version persistence, object storage port, ingestion job queueing, permissions, audit, tests and implementation boundaries.
- 2026-05-27: Implemented authorized upload API, document/version/job persistence, MinIO object storage adapter, RQ ingestion enqueue, audit/error handling, docs, and tests.
- 2026-06-04: Addressed code review findings for upload transaction ordering, object storage compensation, validation, queue payload safety, route boundary, and tenant-scoped version lookup.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- `uv run pytest tests/unit/data/test_document_upload_dto.py tests/unit/data/test_document_upload_service.py tests/unit/data/test_ingestion_queue_payload.py tests/integration/api/test_upload_routes.py tests/integration/storage/test_document_repositories.py tests/integration/storage/test_alembic_migrations.py`（初次红灯：缺少 upload DTO/service/route/storage/queue 实现）
- `uv run pytest tests/unit/data/test_document_upload_dto.py tests/unit/data/test_document_upload_service.py tests/unit/data/test_ingestion_queue_payload.py tests/integration/api/test_upload_routes.py tests/integration/storage/test_document_repositories.py tests/integration/storage/test_alembic_migrations.py`（目标测试通过）
- `uv run pytest`（142 passed）
- `uv run ruff check .`（passed）
- `uv run mypy apps packages tests`（passed）
- `uv run alembic upgrade head` with temporary SQLite `DATABASE_URL`（upgrade 20260527_0001 -> 20260527_0002 passed）
- `.venv\Scripts\python.exe -m pytest`（152 passed）
- `.venv\Scripts\python.exe -m ruff check .`（passed）
- `.venv\Scripts\python.exe -m mypy apps packages tests`（passed）
- `.venv\Scripts\python.exe -m alembic upgrade head` with temporary SQLite `DATABASE_URL`（upgrade 20260527_0001 -> 20260527_0002 passed）

### Completion Notes List

- 实现 `/upload` multipart route，使用 `AuthenticatedRequestContextDep` 和统一 response envelope；JSON `acl`/`metadata` 解析失败返回 `DOCUMENT_UPLOAD_INVALID_METADATA`。
- 实现 `DocumentUploadService`，在任何 object storage、DB、queue side effect 前校验 `document:upload` / `document:manage` 权限；按 chunk 计算 byte size 和 SHA-256 checksum，并限制 `UPLOAD_MAX_BYTES`。
- 引入 `ObjectStorage`、`JobQueue`、`DocumentRepository` Protocol；业务 service 只依赖端口，真实 MinIO/RQ/SQLAlchemy 留在 adapter/storage 层。
- 新增 `documents`、`document_versions`、`ingestion_jobs` models、repository 和 Alembic revision；repository 返回 Pydantic DTO 并支持 tenant/status/document/version 查询。
- RQ ingestion payload 复用 `QueuePayload`，只包含 request/user/tenant/job/resource IDs 和 `{document_id, version_id}`；worker job target 只做 ID payload stub，不实现 parser/chunk/embedding。
- 上传成功、权限拒绝、文件验证失败、storage 失败、queue enqueue 失败均记录 `AuditEvent`，metadata 只含摘要字段。
- 更新 `.env.example`、Compose、README、local development 文档和 `docs/api/upload.md`。
- Code review patch pass fixed 12 findings: durable initial job commit before enqueue, object delete compensation on DB failure, committed failure audits, structured DTO validation errors, strict ACL/content-type/empty-file/length checks, ID-only worker payload validation, aligned queue defaults, route dependency boundary, and tenant-scoped version lookup.

### File List

- `.env.example`
- `README.md`
- `apps/api/main.py`
- `apps/api/routes/upload.py`
- `apps/api/service_dependencies.py`
- `apps/worker/jobs/__init__.py`
- `apps/worker/jobs/ingestion_jobs.py`
- `docker/compose.yaml`
- `docs/api/upload.md`
- `docs/operations/local-development.md`
- `migrations/env.py`
- `migrations/versions/20260527_0002_document_upload.py`
- `packages/auth/policies.py`
- `packages/common/config.py`
- `packages/common/errors.py`
- `packages/data/adapters/__init__.py`
- `packages/data/adapters/minio_object_storage.py`
- `packages/data/dto.py`
- `packages/data/exceptions.py`
- `packages/data/ports.py`
- `packages/data/queue/adapters.py`
- `packages/data/queue/ingestion.py`
- `packages/data/service.py`
- `packages/data/storage/models.py`
- `packages/data/storage/repositories.py`
- `pyproject.toml`
- `tests/integration/api/test_upload_routes.py`
- `tests/integration/storage/test_alembic_migrations.py`
- `tests/integration/storage/test_document_repositories.py`
- `tests/unit/common/test_config.py`
- `tests/unit/data/test_document_upload_dto.py`
- `tests/unit/data/test_document_upload_service.py`
- `tests/unit/data/test_ingestion_queue_payload.py`
- `uv.lock`

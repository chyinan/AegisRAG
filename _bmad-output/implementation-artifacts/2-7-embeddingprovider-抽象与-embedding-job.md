---
baseline_commit: NO_VCS
---

# Story 2.7: EmbeddingProvider 抽象与 Embedding Job

Status: done

生成时间：2026-06-06T17:08:32+08:00

## Story

As a 平台工程师,
I want embedding 通过 Provider 抽象批量执行并可测试,
so that 系统可以切换 OpenAI、Qwen、DeepSeek、本地 vLLM 或 Ollama embedding 实现。

## Acceptance Criteria

1. **EmbeddingProvider 使用端口抽象并支持 batch embedding**
   - Given `chunks` 已由 Story 2.6 持久化为 `ChunkRecord`
   - When embedding application service 处理某个 document version
   - Then 只能通过 `EmbeddingProvider.embed_texts` 批量生成向量
   - And provider 调用必须带 timeout、retry budget、rate limit 配置入口
   - And 业务代码不得直接依赖 OpenAI、Qwen、DeepSeek、Ollama、vLLM 等单一厂商 SDK

2. **FakeEmbeddingProvider 是默认测试实现**
   - Given 测试环境运行 embedding 单测或 worker 测试
   - When 调用 embedding service
   - Then 使用 `FakeEmbeddingProvider`
   - And 不发生真实外部 API、网络模型或本地模型进程调用
   - And fake provider 必须返回确定性向量，便于断言 batch、维度、模型 metadata 和失败路径

3. **`embedding_jobs` 表和 DTO 可追踪 provider/model/version/dim**
   - Given `embedding_jobs` 表首次引入
   - When Alembic migration 生成
   - Then 表包含 `id`、`created_at`、`updated_at`、`tenant_id`、`created_by`、`status`、`document_id`、`version_id`、`provider`、`model`、`version`、`dim`、`attempt_count`、`next_retry_at`、`error_code`
   - And 建立 tenant/status、tenant/document/version、tenant/job 查询索引
   - And job 状态可表达 `queued`、`embedding`、`embedded`、`failed_retryable`、`failed_terminal`

4. **Embedding job 从已 chunked 版本读取 active chunks**
   - Given document/version/job 当前状态为 `chunked`
   - When embedding worker claim job
   - Then 读取同一 `tenant_id`、`document_id`、`version_id` 下的 active chunks
   - And cross-tenant、version mismatch、未 chunked 状态必须拒绝并返回稳定领域错误
   - And queue payload 只包含 `job_id`、`document_id`、`version_id` 等安全 ID，不包含 chunk content、prompt、API key 或绝对路径

5. **Provider 返回 metadata 后安全保存 embedding 结果摘要**
   - Given provider 成功返回 vectors 和 metadata
   - When embedding record 被保存
   - Then 每个结果记录 `embedding_provider`、`embedding_model`、`embedding_version`、`embedding_dim`
   - And 本 story 只保存 embedding job 摘要和 chunk metadata 中安全 embedding 摘要；不写 pgvector，不建向量索引，不标记 `retrieval_ready`
   - And `document_versions.metadata` 不得保存 chunk 正文、完整向量、API response 原文或敏感上下文

6. **Provider 失败和维度异常有明确状态**
   - Given provider 超时、限流或临时错误
   - When service 捕获 expected provider error
   - Then embedding job 更新为 `failed_retryable`，记录安全 `error_code`、`attempt_count`、`last_attempt_at`、`next_retry_at`
   - And audit/log 记录 request_id、trace_id、tenant_id、user_id、document_id、version_id、job_id、provider、model、latency、status、error_code
   - Given provider 返回空向量、维度不一致或 batch 长度不匹配
   - When service 校验结果
   - Then job 更新为 `failed_terminal` 或稳定领域错误，不产生部分成功的 embedded 状态

## Tasks / Subtasks

- [x] 定义 `packages/embeddings` 端口和 DTO（AC: 1, 2, 5, 6）
  - [x] 新建 `packages/embeddings/__init__.py`、`dto.py`、`ports.py`、`exceptions.py`、`service.py`、`adapters/fake.py`。
  - [x] 在 `ports.py` 定义 `EmbeddingProvider` Protocol，方法形态建议为 `async def embed_texts(self, request: EmbeddingRequest) -> EmbeddingResponse`，不要只返回裸 `list[list[float]]`，否则无法携带 provider/model/version/dim/token usage/latency。
  - [x] DTO 至少包含 `EmbeddingRequest(texts, provider, model, timeout_seconds, retry_budget, rate_limit_key, metadata)`、`EmbeddingVector(index, vector, chunk_id)`、`EmbeddingResponse(vectors, provider, model, version, dim, usage, latency_ms)`。
  - [x] provider/domain 异常使用稳定 code，例如 `EMBEDDING_PROVIDER_TIMEOUT`、`EMBEDDING_PROVIDER_RATE_LIMITED`、`EMBEDDING_PROVIDER_FAILED`、`EMBEDDING_VECTOR_DIMENSION_MISMATCH`、`EMBEDDING_BATCH_SIZE_MISMATCH`。
  - [x] `FakeEmbeddingProvider` 根据输入文本和配置维度返回确定性浮点向量；支持配置 timeout/rate-limit/failure 模式用于测试，不使用网络。

- [x] 新增 embedding job storage contract（AC: 3, 4, 5, 6）
  - [x] 在 `packages/data/dto.py` 新增 frozen Pydantic v2 `EmbeddingJobRecord`，字段覆盖 AC3，并校验 required IDs、`attempt_count >= 0`、`dim > 0`（允许初始未完成时为 `None` 的字段要明确）。
  - [x] 在 `packages/data/ports.py` 扩展 repository protocol：`create_embedding_job(...)`、`claim_embedding_job(...)`、`mark_embedding_job_embedded(...)`、`mark_embedding_job_failed(...)`、`get_embedding_job(...)`、`list_embedding_jobs(...)`。
  - [x] 新增 `EmbeddingJobModel` 到 `packages/data/storage/models.py`，表名 `embedding_jobs`；storage model 不得包含完整向量字段，向量落地留给 Story 2.8。
  - [x] 新增 Alembic migration `20260527_0004_embedding_jobs.py`，`down_revision = "20260527_0003"`；保持 SQLite smoke 可运行，不引入 pgvector/PostgreSQL-only DDL。
  - [x] repository 方法必须 tenant-scoped；job 的 document/version 必须与现有 document/version 记录匹配。

- [x] 实现 embedding application service（AC: 1, 4, 5, 6）
  - [x] 在 `packages/embeddings/service.py` 实现 `EmbeddingJobService`，显式接收 `AuthenticatedRequestContext`、repository、provider、audit、logger、clock/config。
  - [x] service 只处理状态为 `chunked` 的 document/version；读取 chunks 复用 `DocumentRepository.list_chunks_for_version(..., status="active")`。
  - [x] 生成 provider 请求时只传 chunk content 给 provider；日志、audit、job metadata 只记录 chunk_count、token_count range、provider/model/version/dim 和安全错误码。
  - [x] 成功后更新 `embedding_jobs.status = "embedded"`，并把 document/version status 推进到 `embedded` 或保留明确的 `chunked` + embedding summary 状态；不要标记 `indexing` 或 `retrieval_ready`。
  - [x] provider 返回 vectors 数量必须等于 active chunk 数量；每个 vector 维度必须一致且与 response dim 匹配。
  - [x] 失败路径区分 retryable provider errors 与 terminal validation errors，并设置 `last_attempt_at`、`next_retry_at`、`attempt_count`。

- [x] 增加 embedding queue payload 与 worker job（AC: 4, 6）
  - [x] 在 `packages/data/queue` 或新的 embedding 队列模块中定义 `EMBEDDING_JOB_TYPE = "embedding.embed_document"` 和 builder，保持 `QueuePayload` JSON-safe contract。
  - [x] 新增 `apps/worker/jobs/embedding_jobs.py`，解析 payload、重建 `AuthenticatedRequestContext`，并调用 `EmbeddingJobService`。
  - [x] worker 默认使用 `FakeEmbeddingProvider` 或 settings 明确配置的 provider factory；未配置真实 provider 时不得尝试外部调用。
  - [x] 如需要队列适配，复用 RQ JSONSerializer 模式；队列参数只传安全 ID，不传正文或复杂对象。
  - [x] 更新 `AppSettings` 和 `.env.example`：`EMBEDDING_PROVIDER`、`EMBEDDING_MODEL`、`EMBEDDING_DIM`、`EMBEDDING_TIMEOUT_SECONDS`、`EMBEDDING_RETRY_BUDGET`、`EMBEDDING_QUEUE_NAME`，默认配置必须可本地 fake 运行。

- [x] 补充测试（AC: 1-6）
  - [x] `tests/unit/embeddings/test_fake_provider.py` 覆盖确定性向量、批量顺序、维度、失败模式。
  - [x] `tests/unit/embeddings/test_embedding_service.py` 覆盖成功嵌入、provider timeout -> retryable、空向量/维度不一致/batch mismatch -> terminal、日志/audit 安全摘要。
  - [x] `tests/integration/storage/test_alembic_migrations.py` 扩展 expected tables、columns、indexes，包含 `embedding_jobs`。
  - [x] `tests/integration/storage/test_document_repositories.py` 或独立 `test_embedding_job_repositories.py` 覆盖创建、claim、embedded、failed、cross-tenant isolation、document/version mismatch。
  - [x] `tests/unit/data/test_embedding_queue_payload.py` 覆盖 payload 只含安全 ID，拒绝敏感字段和绝对路径。
  - [x] `tests/integration/worker/test_embedding_jobs.py` 使用 fake service/provider 验证 worker payload validation 和结果结构。

- [x] 更新文档和验证命令（AC: 3-6）
  - [x] 更新 `docs/api/upload.md`：说明 `chunked -> embedding -> embedded`，并明确 `embedded` 仍未代表 vector indexing/retrieval ready。
  - [x] 更新 `docs/operations/local-development.md`：加入 embedding fake provider 配置、migration smoke 和测试命令。
  - [x] 更新 `README.md` 当前能力列表：EmbeddingProvider abstraction 和 embedding job 是 ready-for-vector-store 的中间阶段。
  - [x] 推荐验证命令：`.venv\Scripts\python.exe -m pytest tests/unit/embeddings tests/integration/storage tests/integration/worker`、`.venv\Scripts\python.exe -m pytest`、`.venv\Scripts\python.exe -m ruff check .`、`.venv\Scripts\python.exe -m mypy apps packages tests`。

### Review Findings

- [x] [Review][Patch] 已 `embedded` 的 job 重跑会被改成失败 [packages/embeddings/service.py:137]
- [x] [Review][Patch] `next_retry_at` 写入后 claim 完全不看，退避无效 [packages/data/storage/repositories.py:464]
- [x] [Review][Patch] Provider 空向量会被归类为 retryable provider failure [packages/embeddings/dto.py:90]
- [x] [Review][Patch] Provider `usage` 被原样写入 metadata，安全摘要边界不成立 [packages/embeddings/service.py:604]
- [x] [Review][Patch] 失败日志和 audit 缺少 provider/model/dim [packages/embeddings/service.py:377]
- [x] [Review][Patch] 长 provider 调用期间 chunk 集合可变化，完成时仍标记 embedded [packages/embeddings/service.py:187]
- [x] [Review][Patch] 示例配置覆盖 fake provider，且非 fake provider 会静默使用 Fake [apps/worker/jobs/embedding_jobs.py:139]

## Dev Notes

### Current Repository State

- 当前目录不是 git repository，`git log` 不可用；本 story 基于现有源码、Story 2.1 到 2.6、epics、architecture、PRD、UX 和项目规则生成。
- `packages/embeddings` 目录尚不存在；本 story 应新增该包，不要把 embedding provider 放进 `packages/ingestion` 或 route/worker 文件内。
- 当前 `pyproject.toml` 已包含 `httpx`、Pydantic v2、SQLAlchemy 2.x、Alembic、Redis/RQ、structlog；没有 OpenAI/Qwen/DeepSeek/Ollama SDK。2.7 默认不需要新增真实厂商 SDK。
- 当前 settings 只有 upload、MinIO、DB、Redis、queue 基础配置；embedding provider/model/dim/timeout/retry/queue 配置需要在 `packages/common/config.py` 增加。
- 当前 worker 只处理 ingestion parse job：`apps/worker/jobs/ingestion_jobs.py`。Embedding worker 应新增独立 job target，不要把 parse job 改成多阶段大函数。
- 当前 RQ queue 使用 `JSONSerializer`，`QueuePayload` 会拒绝敏感字段、绝对路径和非 JSON payload；embedding queue 必须复用这个安全边界。

### Architecture Requirements

- 本 story 位于 Application Service Layer + Domain/Port + Storage Layer：provider port 在 `packages/embeddings`，job persistence 在 `packages/data/storage`，worker process 在 `apps/worker`。
- `packages/embeddings` 不得 import FastAPI、SQLAlchemy model、MinIO、Redis 或外部 SDK 到 domain/port 层；真实 provider adapter 后续只能放在 `packages/embeddings/adapters/*`。
- API route 不参与本 story；不要新增 `/embedding` 管理 API，除非 story 明确要求。
- Embedding 是 ingestion pipeline 中 `chunked -> embedded` 的阶段；VectorStore、pgvector 列、index dimension enforcement against target index、retrieval_ready 属于 Story 2.8/2.9。
- 所有业务状态必须 tenant-scoped，所有 repository read/write 必须带 `tenant_id`。
- 日志和 audit 必须记录 request_id、trace_id、tenant_id、user_id、latency、provider、model、dim、chunk_count、error_code；不得记录 chunk content、完整向量或 provider raw response。

### Current Files To Preserve And Extend

- `packages/data/dto.py`
  - Current state: 已有 `DocumentRecord`、`DocumentVersionRecord`、`IngestionJobRecord`、`ChunkRecord`。
  - Story change: 新增 `EmbeddingJobRecord`，不要把 vector list 放进 storage DTO。
  - Preserve: DTO frozen、typed、无 SQLAlchemy state。

- `packages/data/ports.py`
  - Current state: `DocumentRepository` Protocol 覆盖 upload、parse、chunk persistence。
  - Story change: 增加 embedding job 方法或拆出清晰的 embedding job repository protocol；实现仍可复用 `DocumentRepository` storage class。
  - Preserve: 方法入参显式包含 `tenant_id`。

- `packages/data/storage/models.py`
  - Current state: SQLAlchemy 2 typed declarative models，已有 `DocumentModel`、`DocumentVersionModel`、`IngestionJobModel`、`ChunkModel`。
  - Story change: 新增 `EmbeddingJobModel`。
  - Preserve: model 文件只放 storage mapping，不放 provider/service 逻辑。

- `packages/data/storage/repositories.py`
  - Current state: tenant-scoped repository，已实现 chunk replacement/list/get 和 `mark_ingestion_job_chunked`。
  - Story change: 新增 embedding job create/claim/mark methods 和 mapper。
  - Preserve: SQLAlchemy error -> `StorageError`，details 只包含安全 ID。

- `migrations/versions/20260527_0003_chunks.py`
  - Current state: migration chain head，创建 `chunks`。
  - Story change: 不修改旧 migration；新增 `20260527_0004_embedding_jobs.py`。
  - Preserve: migration history 不重写。

- `packages/ingestion/service.py`
  - Current state: parse service 只负责读取 object storage、parser、parsed 状态和安全 audit。
  - Story change: 通常不应修改；embedding service 应独立。
  - Preserve: 不把 provider 调用塞进 parse service。

- `apps/worker/jobs/ingestion_jobs.py`
  - Current state: RQ job target 只解析 ingestion payload 并调用 `IngestionParseService`。
  - Story change: 新增 `apps/worker/jobs/embedding_jobs.py`；不要扩大 ingestion job target 职责。
  - Preserve: payload validation 和 AuthContext 重建模式。

### Previous Story Intelligence

- Story 2.1 建立 `/upload` 异步返回 job id，上传接口不得等待 embedding。2.7 不应改 `/upload` 为同步 embedding。
- Story 2.2/2.3 建立 parser service、payload mismatch 检查、job claim、领域异常和安全摘要。2.7 应复用相同的 claim/failed/status/audit 思路。
- Story 2.4 明确不把 cleaned document/full content 写入 metadata。2.7 也不能把 chunk content、完整向量或 provider response 原文写入 `document_versions.metadata`。
- Story 2.5 建立 `Chunk` DTO、`FixedSizeChunker`、稳定 `chunk_id`、checksum、page/title/ACL lineage。2.7 应使用已持久化 `ChunkRecord.content` 作为 embedding 输入，不重新切 chunk。
- Story 2.6 已落地 `chunks` 表、`ChunkRecord`、tenant-scoped chunk repository、chunked 状态和安全摘要。2.7 的第一步应从 `chunked` 版本读取 active chunks，不重新设计 chunk persistence。

### Suggested Contracts

Embedding provider protocol:

```python
class EmbeddingProvider(Protocol):
    async def embed_texts(self, request: EmbeddingRequest) -> EmbeddingResponse:
        ...
```

Embedding job DTO shape:

```python
class EmbeddingJobRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    created_by: str
    status: str
    document_id: str
    version_id: str
    provider: str
    model: str
    version: str | None = None
    dim: int | None = None
    chunk_count: int | None = None
    attempt_count: int = 0
    error_code: str | None = None
    last_attempt_at: datetime | None = None
    next_retry_at: datetime | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
```

Repository method shape:

```python
async def claim_embedding_job(
    *,
    tenant_id: str,
    job_id: str,
    document_id: str,
    version_id: str,
    stale_before: datetime | None,
) -> EmbeddingJobRecord | None:
    ...
```

### Implementation Boundaries

- 不要实现 `VectorStore`、pgvector adapter、FAISS/Milvus、embedding vector upsert、retrieval API、dense retrieval 或 sparse index；这些属于 Story 2.8+。
- 不要新增真实 OpenAI/Qwen/DeepSeek/Ollama SDK 依赖作为默认路径；真实 adapters 可后置，2.7 以 port + fake + service contract 为完成标准。
- 不要把 provider timeout/retry/rate limit 写进 prompt、route 或全局变量；配置来自 `AppSettings` 或显式 service 参数。
- 不要把 chunk content、完整向量、provider raw response、API key、access token、企业机密全文或本地绝对路径写入日志、audit、queue payload 或 version metadata。
- 不要跨 tenant 读取 chunks 或 embedding jobs；找不到或无权范围返回稳定 not found/storage error，不泄露存在性。
- 不要把 `embedded` 误称为 `retrieval_ready`。没有 Story 2.8 的 vector index 写入前，文档仍不可检索。
- 不要让 LLM 参与 embedding job 判断、权限判断或失败分类。

### Latest Technical Information

- OpenAI 官方 Embeddings 文档仍以 embeddings endpoint 作为文本向量化入口，并明确不同 embedding model 有不同输出维度；本 story 必须把 provider/model/version/dim 作为 runtime metadata 保存，不能写死维度。[Source: https://platform.openai.com/docs/guides/embeddings]
- Ollama 官方 API 使用 `/api/embed` 生成 embeddings，返回维度随本地 embedding 模型变化；本 story 的 provider 抽象必须容纳本地模型维度差异。[Source: https://github.com/ollama/ollama/blob/main/docs/api.md]
- HTTPX 官方文档强调 timeout 是显式配置项；真实 provider adapter 后续应使用 `httpx.AsyncClient(timeout=...)` 或等价配置，2.7 的 service contract 先暴露 timeout 参数。[Source: https://www.python-httpx.org/advanced/timeouts/]
- 当前项目依赖 FastAPI 0.136.x、Pydantic 2.13.x、SQLAlchemy 2.0.x、Alembic 1.18.x、RQ 2.9.x；新增代码应匹配现有版本，不升级到 beta 或引入不必要大依赖。

### UX / Product Notes

- 本 story 不实现 UI，但 Knowledge Admin 后续 job status 依赖 `embedding_jobs` 的状态、attempt_count、error_code、provider/model/dim 和安全摘要。
- 员工查询端不得看到未完成 embedding 的文档为可检索；`embedded` 只表示 provider 阶段完成，仍需 vector indexing。
- 管理端错误提示必须显示安全摘要和 request_id，不展示 provider raw response、chunk 正文或完整向量。

### References

- `_bmad-output/planning-artifacts/epics.md#Story-2.7-EmbeddingProvider-抽象与-Embedding-Job`
- `_bmad-output/planning-artifacts/epics.md#Epic-2-知识文档接入到可检索资产`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-5-Embedding-Provider-抽象`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-6-Embedding-元数据记录`
- `_bmad-output/planning-artifacts/architecture.md#Implementation-Patterns-Consistency-Rules`
- `_bmad-output/planning-artifacts/architecture.md#Project-Structure-Boundaries`
- `_bmad-output/implementation-artifacts/2-6-chunk-metadata-contract-与持久化.md`
- `project-context.md`
- `packages/data/dto.py`
- `packages/data/ports.py`
- `packages/data/storage/models.py`
- `packages/data/storage/repositories.py`
- `packages/data/queue/contracts.py`
- `packages/data/queue/ingestion.py`
- `packages/data/queue/adapters.py`
- `apps/worker/jobs/ingestion_jobs.py`
- `packages/common/config.py`
- `https://platform.openai.com/docs/guides/embeddings`
- `https://github.com/ollama/ollama/blob/main/docs/api.md`
- `https://www.python-httpx.org/advanced/timeouts/`

## Validation Checklist

Validation Result: PASS（2026-06-06T17:08:32+08:00）

- [x] Story 明确了用户角色、目标和收益。
- [x] Acceptance Criteria 覆盖 Epic Story 2.7 的 provider batch abstraction、fake provider、embedding_jobs migration、tenant-scoped job processing、provider metadata 和失败状态。
- [x] Tasks 覆盖 `packages/embeddings`、storage DTO/model/repository、Alembic migration、service、queue/worker、tests 和 docs。
- [x] Dev Notes 明确复用 Story 2.6 的 `ChunkRecord` 和 chunk repository，不重新实现 chunker 或 chunk persistence。
- [x] 明确 provider abstraction 不绑定单一厂商 SDK，默认测试使用 fake，不真实调用外部 API。
- [x] 明确 2.7 不实现 VectorStore、pgvector、dense retrieval、sparse index 或 `retrieval_ready`。
- [x] 明确 tenant_id/user_id/ACL/source metadata、安全日志和审计约束贯穿 embedding job。
- [x] 包含当前代码文件状态、前序 story 经验、架构/PRD/UX 约束、最新技术参考和实现边界。

## Change Log

- 2026-06-06: Created comprehensive Story 2.7 developer context for EmbeddingProvider abstraction, fake provider, embedding job persistence, worker contract, provider metadata, failure handling, tests and docs.
- 2026-06-06: Implemented EmbeddingProvider abstraction, fake provider, embedding job persistence, worker queue contract, service failure handling, tests, and documentation.
- 2026-06-06: Resolved code review findings for embedding job idempotency, retry backoff, terminal validation, safe metadata, audit fields, chunk snapshot checks, and fake provider configuration.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- `.venv\Scripts\python.exe -m pytest tests/unit/embeddings tests/unit/data/test_embedding_queue_payload.py tests/unit/common/test_config.py tests/integration/worker/test_embedding_jobs.py tests/integration/storage/test_alembic_migrations.py tests/integration/storage/test_document_repositories.py` -> 27 passed
- `.venv\Scripts\python.exe -m ruff check .` -> passed
- `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed
- `.venv\Scripts\python.exe -m pytest` -> 243 passed
- `.venv\Scripts\python.exe -m pytest tests/unit/embeddings tests/unit/data/test_embedding_queue_payload.py tests/integration/worker/test_embedding_jobs.py tests/integration/storage/test_document_repositories.py` -> 28 passed
- `.venv\Scripts\python.exe -m pytest tests/unit/embeddings tests/integration/storage tests/integration/worker` -> 30 passed
- `.venv\Scripts\python.exe -m ruff check .` -> passed
- `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed
- `.venv\Scripts\python.exe -m pytest` -> 249 passed

### Completion Notes List

- Implemented `packages.embeddings` with typed request/response DTOs, stable error codes, `EmbeddingProvider` port, deterministic `FakeEmbeddingProvider`, and `EmbeddingJobService`.
- Added `embedding_jobs` storage DTO/model/migration/repository flow with tenant-scoped create, claim, embedded, failed, get, and list methods.
- Added ID-only embedding queue payload builder, RQ adapter, worker job target, and settings/env defaults for local fake provider execution.
- Added unit and integration tests for provider determinism, service success/failure handling, safe queue payloads, worker delegation, Alembic DDL, repository state transitions, and config.
- Updated docs to clarify `chunked -> embedding -> embedded` and that `embedded` is not vector indexing or retrieval-ready.

### File List

- `.env.example`
- `README.md`
- `apps/worker/jobs/embedding_jobs.py`
- `docs/api/upload.md`
- `docs/operations/local-development.md`
- `migrations/versions/20260527_0004_embedding_jobs.py`
- `packages/common/config.py`
- `packages/data/dto.py`
- `packages/data/exceptions.py`
- `packages/data/ports.py`
- `packages/data/queue/adapters.py`
- `packages/data/queue/embedding.py`
- `packages/data/storage/models.py`
- `packages/data/storage/repositories.py`
- `packages/embeddings/__init__.py`
- `packages/embeddings/adapters/__init__.py`
- `packages/embeddings/adapters/fake.py`
- `packages/embeddings/dto.py`
- `packages/embeddings/exceptions.py`
- `packages/embeddings/ports.py`
- `packages/embeddings/service.py`
- `tests/integration/storage/test_alembic_migrations.py`
- `tests/integration/storage/test_document_repositories.py`
- `tests/integration/worker/test_embedding_jobs.py`
- `tests/unit/common/test_config.py`
- `tests/unit/data/test_embedding_queue_payload.py`
- `tests/unit/embeddings/test_embedding_service.py`
- `tests/unit/embeddings/test_fake_provider.py`

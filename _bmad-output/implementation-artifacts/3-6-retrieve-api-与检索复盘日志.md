---
baseline_commit: NO_VCS
---

# Story 3.6: `/retrieve` API 与检索复盘日志

Status: done

生成时间：2026-06-07T12:02:26+08:00

## Story

As a 平台工程师,
I want 每次检索都能通过日志复盘召回、融合、rerank 和过滤过程,
so that 质量问题可以定位到具体阶段。

## Acceptance Criteria

1. **`POST /retrieve` 暴露授权检索能力并返回统一 envelope**
   - Given 授权用户调用 `POST /retrieve`
   - When route 构造 `RetrievalRequest` 并调用 application service
   - Then API 返回 `ApiResponse[RetrieveResponse]`，包含 `request_id`、`data`、`error`、`metadata`
   - And `data` 至少包含 `request_id`、`trace_id`、`tenant_id`、`user_id`、`top_k`、`query_summary`、`latency_ms`、`candidates`
   - And 每个 candidate 至少包含 `chunk_id`、`document_id`、`version_id`、`source`/`source_uri`、`source_type`、`page_start`、`page_end`、`title_path`、`score`、`retrieval_method`、`tenant_id`、`acl`、安全 `metadata`

2. **route 保持薄层职责，不直接触碰检索基础设施**
   - Given `apps/api/routes/retrieve.py` 被实现
   - When 检查依赖和调用路径
   - Then route 只处理 Pydantic request schema、`AuthenticatedRequestContext`、service 调用和 `success_response`
   - And route 不直接创建或调用 `VectorStore`、`EmbeddingProvider`、`PostgresSparseRetriever`、`RRFMerger`、`Reranker`、SQLAlchemy session、LLM SDK 或 prompt builder
   - And route 不接收或信任请求体中的 `tenant_id`、`user_id`、roles、permissions；这些只能来自 AuthContext

3. **application service 复用已完成 retrieval pipeline**
   - Given 已存在 `RetrievalService`、`DenseRetriever`、`PostgresSparseRetriever`、`HybridRetriever`、`RRFMerger`、`RerankingRetriever`、`FakeReranker`
   - When `POST /retrieve` 执行
   - Then application service 通过注入的 `RetrievalService` 调用单一 `CandidateRetriever`
   - And 默认 pipeline 为 dense + sparse -> RRF merge/dedup/threshold -> rerank -> `RetrievalService` 结果侧 guard
   - And 不重新实现 dense、sparse、RRF、rerank、ACL 过滤、score threshold 或 candidate 安全校验

4. **检索日志成功落库且可按 request_id 复盘**
   - Given retrieval 成功返回
   - When retrieval log 被保存
   - Then `retrieval_logs` 记录 `request_id`、`trace_id`、`tenant_id`、`user_id`、`status=success`、`latency_ms`、`top_k`、`result_count`、`rerank_score`、`error_code=None`、安全 `query_summary`
   - And metadata 中可复盘 `dense_top_k`、`sparse_top_k`、RRF 输入/去重/过滤摘要、rerank status/score/latency、安全 candidate IDs
   - And 平台工程师可通过 repository 按 `request_id`、`tenant_id`、`created_at` 查询

5. **失败路径也写入安全日志和审计**
   - Given retrieval 阶段发生 expected `RetrievalError`
   - When API error handler 返回结构化错误
   - Then retrieval log 记录 `status=failure`、`error_code`、`request_id`、`trace_id`、`tenant_id`、`user_id`、`top_k`、安全 `query_summary`、`latency_ms`
   - And audit/log 能关联同一 `request_id` 与 `trace_id`
   - And API error envelope 使用稳定 error code，不泄露 raw exception、query 全文、chunk 正文、SQL、vector、embedding、provider raw response、secret、token 或本机绝对路径

6. **`retrieval_logs` 表和 migration 满足治理字段与索引要求**
   - Given Alembic migration 首次引入 `retrieval_logs`
   - When `alembic upgrade head` 执行
   - Then 表包含 `id`、`created_at`、`updated_at`、`request_id`、`trace_id`、`tenant_id`、`user_id`、`created_by`、`status`、`latency_ms`、`top_k`、`result_count`、`rerank_score`、`error_code`、`query_summary`、`metadata`
   - And 至少有 `request_id`、`trace_id`、`tenant_id`、`created_at`、`tenant_id + request_id`、`tenant_id + created_at` 索引
   - And SQLite migration smoke test 覆盖 portable DDL；PostgreSQL 专用优化不得破坏 SQLite 测试

7. **敏感信息脱敏规则可测试**
   - Given query、candidate metadata、retriever/reranker details 或 provider error 中包含敏感内容
   - When 写入 retrieval log、audit event、API error details 或 response metadata
   - Then 不保存 query 全文、chunk content、prompt、SQL、tsquery/tsvector、vector、embedding、provider raw response、API key、access token、password、secret、本机绝对路径
   - And response candidate 不包含 chunk 正文；只返回 source/citation metadata、score 和安全 provenance

8. **测试覆盖 API、日志、错误、权限和迁移**
   - Given 单元和集成测试运行
   - When 执行本 story 的测试集
   - Then 覆盖 route success、missing auth、invalid request、expected retrieval error、thin route dependency override、successful retrieval log、failure retrieval log、audit event、query redaction、candidate metadata redaction、migration columns/indexes
   - And 默认测试使用 fake providers/stub service，不真实调用外部 LLM、embedding API、rerank API、OpenSearch、网络服务或生产 PostgreSQL

## Tasks / Subtasks

- [x] 定义 API schema 与 route（AC: 1, 2, 7）
  - [x] 新增 `apps/api/routes/retrieve.py`。
  - [x] 定义 `RetrieveRequestBody`，字段建议为 `query: str`、`top_k: int = 10`、`metadata_filter: dict[str, object] = {}`、`score_threshold: float | None = None`。
  - [x] 定义 `RetrieveCandidateResponse` 与 `RetrieveResponse`，或复用安全的 retrieval DTO 输出；不得返回 chunk 正文。
  - [x] route 使用 `AuthenticatedRequestContextDep` 与 `RetrieveApplicationServiceDep`，从 context 注入 `request_id`、`trace_id`、AuthContext。
  - [x] 在 `apps/api/main.py` 注册 retrieve router。
  - [x] route 返回 `success_response(request_id=context.request_id, data=result)`，不要自己拼 envelope。

- [x] 实现 retrieval application service / logging wrapper（AC: 3, 4, 5, 7）
  - [x] 新增 `packages/retrieval/application.py` 或在 `packages/retrieval/service.py` 中新增清晰命名的 application wrapper，例如 `RetrieveApplicationService`。
  - [x] wrapper 注入 `RetrievalService`、`RetrievalLogPort`、`AuditPort`、clock/perf counter；不要在方法内部创建真实 provider、session 或 SDK。
  - [x] 将 API body 转为 `RetrievalRequest(query=..., top_k=..., metadata_filter=..., score_threshold=..., request_id=context.request_id, trace_id=context.trace_id)`。
  - [x] 成功时先拿 `RetrievalResult`，再写 `retrieval_logs` 和 audit event；写日志失败应转稳定 storage/domain error，不应假装成功。
  - [x] expected `RetrievalError` 时也写 failure log 和 audit event，然后原样抛出，让现有 `DomainError` handler 生成 error envelope。
  - [x] 日志 metadata 从 `RetrievalResult.candidates[*].metadata["retrieval_provenance"]` 与 `["rerank_provenance"]` 提取安全摘要；不要依赖 query 全文或 chunk 正文。
  - [x] 明确 `rerank_score` 语义：建议记录最终候选中的最高 rerank score；没有 rerank provenance 时为 `None`。

- [x] 新增 retrieval log DTO、端口和 repository（AC: 4, 5, 6, 7）
  - [x] 在 `packages/retrieval/dto.py` 或新文件定义 `RetrievalLogRecord` / `RetrievalLogCreate`，包含 AC6 字段。
  - [x] 在 `packages/retrieval/ports.py` 定义 `RetrievalLogPort`，至少包含 `async def create(record: RetrievalLogCreate) -> RetrievalLogRecord` 和按 `request_id` 查询能力。
  - [x] 新增 `packages/retrieval/storage/models.py` 与 `packages/retrieval/storage/repositories.py`，或采用同等清晰边界；storage model 不得进入 domain 逻辑。
  - [x] repository 捕获 SQLAlchemy expected errors 并转稳定 storage/domain error；details 只含 request_id、trace_id、tenant_id、user_id、error_code。
  - [x] 使用现有 `packages.common.logging.redact_mapping` / `redact_sensitive_data` 处理 `query_summary`、metadata 和 error details。

- [x] 新增 Alembic migration（AC: 6）
  - [x] 新增 `migrations/versions/20260527_0008_retrieval_logs.py`，`down_revision` 指向 `20260527_0007`。
  - [x] 创建 `retrieval_logs` 表，字段满足 AC6。
  - [x] 对 PostgreSQL/SQLite 均可运行的字段使用 SQLAlchemy portable types：`String`、`Float`、`Integer`、`JSON`、`DateTime`。
  - [x] 在 `migrations/env.py` 导入新的 retrieval storage models，确保 metadata 被 Alembic 看到。
  - [x] 更新 `tests/integration/storage/test_alembic_migrations.py`，把 `retrieval_logs` 加入 expected table/column/index 断言。

- [x] 接入 service dependency 与默认 pipeline 工厂（AC: 2, 3）
  - [x] 在 `apps/api/service_dependencies.py` 新增 `get_retrieve_application_service` 与 `RetrieveApplicationServiceDep`。
  - [x] 复用 `_session_factory` 与 `_vector_store_from_settings`，不要新增另一套 DB/session 工厂。
  - [x] 组装 `DenseRetriever`、`PostgresSparseRetriever`、`RRFMerger`、`HybridRetriever`、`RerankingRetriever`、`RetrievalService`。
  - [x] Embedding provider 必须通过 provider 抽象；当前只有 fake adapter 时，只有 `EMBEDDING_PROVIDER=fake` 可构建，其他值应返回明确配置错误，不要静默硬编码 fake。
  - [x] `RerankConfig`、`HybridMergeConfig`、`SparseRetrieverConfig` 的默认值可先使用现有类默认；新增配置项必须进入 `AppSettings` 和 `.env.example`，不得散落 magic numbers。

- [x] 写 API 集成测试（AC: 1, 2, 5, 8）
  - [x] 新增 `tests/integration/api/test_retrieve_routes.py`。
  - [x] 使用 dependency override 注入 stub application service，验证 success envelope、request/auth context、body 到 service 的传递。
  - [x] 测试 missing auth 返回 `AUTH_CONTEXT_REQUIRED` 且 service 未调用。
  - [x] 测试 invalid body（blank query、bad top_k、bad score_threshold、metadata_filter 非对象）返回结构化 error。
  - [x] 测试 stub service 抛 `RetrievalError` 时返回稳定 error envelope，并包含 request_id。
  - [x] 测试 route 不需要真实 vector store、embedding provider、reranker、DB 或网络。

- [x] 写 retrieval log / application 单元测试（AC: 3, 4, 5, 7, 8）
  - [x] 新增 `tests/unit/retrieval/test_retrieve_application.py`。
  - [x] 成功路径：fake `RetrievalService` 返回带 retrieval/rerank provenance 的 candidates，断言 log/audit 写入 safe summary。
  - [x] 失败路径：fake service 抛 `RetrievalError`，断言 failure log/audit 仍写入且原 error code 被保留。
  - [x] 脱敏测试：query 全文、chunk content、SQL、vector、embedding、provider raw response、secret/token、本机绝对路径不得出现在 log metadata、audit metadata 或 error details。
  - [x] top_k/result_count/rerank_score/latency 语义有确定性断言。

- [x] 写 retrieval log storage 测试（AC: 4, 6, 8）
  - [x] 新增 `tests/unit/retrieval/test_retrieval_log_storage.py` 或 `tests/integration/storage/test_retrieval_log_repositories.py`。
  - [x] 覆盖 create、get/list by request_id、tenant isolation、created_at ordering。
  - [x] 覆盖 storage error 转稳定错误且不泄露 raw SQL。

- [x] 更新文档（AC: 1-8）
  - [x] 更新 `README.md#Retrieval Foundation`，说明 `/retrieve` API 和 `retrieval_logs` 已完成，RAG context packing、`/query`、`/chat`、SSE 和 eval runner 仍未完成。
  - [x] 更新 `docs/operations/local-development.md#Retrieval Local Checks`，加入 `POST /retrieve` 本地 curl 示例、dev auth headers、日志查询说明和测试命令。
  - [x] 如新增配置项，更新 `.env.example`。

- [x] 验证（AC: 1-8）
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/api/test_retrieve_routes.py`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/retrieval/test_retrieve_application.py`
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/storage/test_alembic_migrations.py`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/retrieval tests/integration/api tests/integration/storage`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`
  - [x] 如成本可接受，运行 `.venv\Scripts\python.exe -m pytest`

### Review Findings

- [x] [Review][Patch] API error details can leak SQL/vector/embedding/provider payloads [packages/common/logging.py:47]
- [x] [Review][Patch] Invalid structured `metadata_filter` bypasses route validation and is not logged/audited as a retrieval failure [packages/retrieval/application.py:188]
- [x] [Review][Patch] Failure logging can mask the original `RetrievalError` returned to clients [packages/retrieval/application.py:201]
- [x] [Review][Patch] Candidate provenance `sources` can leak arbitrary nested non-sensitive fields such as excerpts/snippets [packages/retrieval/application.py:463]
- [x] [Review][Patch] Retrieval replay metadata is inferred from final candidates and reports misleading dense/sparse/RRF filter counts [packages/retrieval/application.py:338]
- [x] [Review][Patch] Retrieval log repository lacks created-at range query support required for replay lookup [packages/retrieval/ports.py:43]
- [x] [Review][Patch] Read-side repository errors leave the SQLAlchemy session transaction unrolled back [packages/retrieval/storage/repositories.py:78]
- [x] [Review][Patch] Retrieval log `status` accepts arbitrary strings instead of success/failure only [packages/retrieval/dto.py:220]

## Dev Notes

### Current Repository State

- 当前目录不是 git repository，`git status` 和 `git log` 不可用；本 story 的历史上下文来自 sprint status、epics、architecture、PRD、project-context、Story 3.5 Dev Agent Record 和源码扫描。
- `POST /retrieve` 尚未实现。`README.md#Retrieval Foundation` 和 `docs/operations/local-development.md#Retrieval Local Checks` 明确当前 non-goals 包含 `POST /retrieve`、retrieval logs、context packing、RAG generation、eval runners。
- FastAPI app 当前注册 `health_router`、`upload_router`、`documents_router`。新增 retrieve router 应沿用 `apps/api/main.py` 的 include pattern。
- 现有 route 风格：
  - `apps/api/routes/upload.py` 使用 `AuthenticatedRequestContextDep`、service dependency、`ApiResponse[...]`、`success_response`。
  - `apps/api/routes/documents.py` route 只做 context/service/command 调用，不触碰 storage adapter。
- 现有 request/auth 注入：
  - `apps/api/dependencies.py` 从 `X-Request-ID`、`X-Trace-ID`、`X-Session-ID` 构造 `RequestContext`。
  - dev auth headers 只在 `ENABLE_DEV_AUTH_HEADERS=true` 且 `APP_ENV` 为 local/dev/test 时启用。
  - `AuthenticatedRequestContext` 包含 request_id、trace_id、session_id、auth。
- 现有 error handler：
  - `apps/api/error_handlers.py` 已把 `DomainError` 转统一 error envelope。
  - `DomainError.status_code` 会作为 HTTP status；不要在 route 中手写 error response。
- 现有 envelope：
  - `packages/common/envelope.py` 定义 `ApiResponse[T]`、`ApiError`、`ResponseMetadata`、`success_response`、`error_response`。
  - `/retrieve` 必须复用这些类型。

### Existing Retrieval Components To Reuse

- `packages/retrieval/dto.py`
  - `RetrievalRequest` 已校验 non-blank query/request_id/trace_id、`top_k` 1..100、`score_threshold` 0..1、structured scalar `metadata_filter`。
  - `RetrievalCandidate` 已包含 citation 必需字段、tenant、ACL、metadata 和 score。
  - `RetrievalResult` 已包含 request_id、trace_id、tenant_id、user_id、top_k、query_summary、candidates、latency_ms、error_code。
  - `_query_summary` 目前只返回 `{"length": len(query)}`，安全但信息有限。3.6 可在 application log 层增加 term_count 等安全摘要，但不得记录 query 全文。

- `packages/retrieval/service.py`
  - `RetrievalService` 只依赖一个 `CandidateRetriever`。
  - service 负责 AuthContext 必填、`build_retrieval_filter_set`、包装 unexpected backend error、结果侧 tenant/metadata/ACL/score_threshold/top_k guard。
  - 3.6 不应把 dense/sparse/RRF/rerank 细节塞进 `RetrievalService`；只在 dependency factory 中组装 retriever pipeline。

- `packages/retrieval/dense.py`
  - `DenseRetriever` 通过 `EmbeddingProvider` + `VectorStore` 执行 query embedding 和 vector search。
  - 已把 provider/vector errors 转 `RetrievalError`，safe details 不含 query vector、embedding、raw provider output。

- `packages/retrieval/sparse.py`
  - `PostgresSparseRetriever` 支持 PostgreSQL full text，SQLite fallback 可用于测试。
  - 它应用 tenant、metadata、ACL、active status、soft delete、score threshold。
  - SQL/tsquery 不得写入 retrieval log。

- `packages/retrieval/rrf.py`
  - `HybridRetriever` 注入 dense/sparse retrievers 和 `RRFMerger`。
  - branch request 清空 `score_threshold`，先召回再融合，fusion 后再阈值过滤。
  - `metadata["retrieval_provenance"]` 包含 safe source methods、source ranks/scores/contributions、raw RRF score、normalized score、fusion reason。
  - `RRFMerger.last_trace` 有 input_counts、deduped_count、filtered_count、threshold、rank_constant、weights；如果 application service 需要更完整日志，可从 merger trace 或 candidate provenance 提取。

- `packages/retrieval/rerank.py`
  - `RerankingRetriever` 包裹 upstream `CandidateRetriever` 和 `Reranker` port。
  - `FakeReranker` 不访问网络、真实模型、文件系统模型路径或生产数据库。
  - `metadata["rerank_provenance"]` 包含 provider、model、status、input_rank、output_rank、pre_score、rerank_score、score_source、latency_ms、error_code。
  - `RerankingRetriever.last_trace` 有 rerank success/degraded/failed 摘要；3.6 的 log 可利用它，但不要让 API route 直接访问它。

### Current Files To Preserve And Extend

- `apps/api/main.py`
  - Current state: 注册 health/upload/documents router。
  - Story change: import 并 include retrieve router。
  - Preserve: logging、middleware、error handler 初始化顺序。

- `apps/api/service_dependencies.py`
  - Current state: 提供 document upload/lifecycle services，复用 `_session_factory` 和 `_vector_store_from_settings`。
  - Story change: 新增 retrieval application service dependency 和 retrieval pipeline factory。
  - Preserve: 不复制 session factory；`VECTOR_STORE_TYPE` unsupported 继续明确失败。

- `packages/common/config.py`
  - Current state: 包含 embedding/vector/readiness 等配置，无 retrieval/rerank 专属配置项。
  - Story change: 如需要新增 retrieval defaults，必须通过 `AppSettings` 和 `.env.example`，不要在 route 或 service_dependency 中散落 magic numbers。
  - Preserve: secret 仍来自环境变量，不硬编码。

- `packages/data/storage/audit_models.py` 与 `packages/data/storage/audit_repositories.py`
  - Current state: 已有 `audit_logs` 表和 `SqlAlchemyAuditPort`，会 redact resource metadata 和 event metadata。
  - Story change: retrieval application service 应使用 `AuditPort` 写 action，例如 `retrieval.retrieve`，resource type 可为 `retrieval_request`。
  - Preserve: audit 不保存 query 全文或 chunk 正文。

- `migrations/env.py`
  - Current state: 导入 auth/data/audit models 到 Base metadata。
  - Story change: 新增 retrieval storage models 后必须导入，否则 migration smoke 可能漏表。

- `tests/integration/api/test_upload_routes.py`
  - Current state: 已展示 route dependency override、dev auth headers、missing auth、invalid body、permission denied 的测试风格。
  - Story change: `test_retrieve_routes.py` 应沿用这个风格，避免真实外部依赖。

- `tests/integration/storage/test_alembic_migrations.py`
  - Current state: SQLite smoke 检查 foundational/document/vector tables。
  - Story change: 加入 `retrieval_logs` 表、base columns、治理字段和 indexes 断言。

### Suggested Implementation Shape

示例只表达目标结构，开发时按现有本地风格落地：

```python
class RetrieveRequestBody(BaseModel):
    query: str
    top_k: int = 10
    metadata_filter: dict[str, object] = Field(default_factory=dict)
    score_threshold: float | None = None
```

```python
@router.post("/retrieve", response_model=ApiResponse[RetrieveResponse])
async def retrieve(
    context: AuthenticatedRequestContextDep,
    service: RetrieveApplicationServiceDep,
    body: RetrieveRequestBody,
) -> ApiResponse[RetrieveResponse]:
    result = await service.retrieve(context=context, body=body)
    return success_response(request_id=context.request_id, data=result)
```

```python
class RetrieveApplicationService:
    def __init__(
        self,
        *,
        retrieval_service: RetrievalService,
        retrieval_log: RetrievalLogPort,
        audit: AuditPort,
        clock: Clock | None = None,
    ) -> None: ...

    async def retrieve(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: RetrieveCommand,
    ) -> RetrieveResponse:
        request = RetrievalRequest(
            query=command.query,
            top_k=command.top_k,
            metadata_filter=command.metadata_filter,
            score_threshold=command.score_threshold,
            request_id=context.request_id,
            trace_id=context.trace_id,
        )
        try:
            result = await self._retrieval_service.retrieve(request=request, auth=context.auth)
        except RetrievalError as exc:
            await self._record_failure(context=context, request=request, error=exc)
            raise
        await self._record_success(context=context, result=result)
        return RetrieveResponse.from_result(result)
```

推荐 `retrieval_logs.metadata` shape：

```text
{
  "dense_top_k": 10,
  "sparse_top_k": 10,
  "rrf": {
    "input_counts": {"dense": 8, "sparse": 6},
    "deduped_count": 11,
    "filtered_count": 2
  },
  "rerank": {
    "status": "success",
    "provider": "fake",
    "model": "fake-reranker-v1",
    "latency_ms": 1.2
  },
  "candidate_ids": [
    {"document_id": "doc-1", "version_id": "ver-1", "chunk_id": "chunk-1"}
  ]
}
```

### Previous Story Intelligence

- Story 3.1 修复过 private ACL 默认放行、无效 request 不转稳定 error、service 过度信任 retriever 输出、top_k 无上限、NaN threshold、多值 metadata filter 等问题。3.6 不得让 API 请求体绕过 `AuthContext` 或 `RetrievalRequest` 校验。
- Story 3.2 建立 DenseRetriever 的 provider/vector store 抽象和 safe details，证明 `RetrievalService` 可以接受任意 `CandidateRetriever`。3.6 应通过 dependency factory 注入 pipeline，不改变 service contract。
- Story 3.3 建立 SparseRetriever，并修复 PostgreSQL query term cap、backend timeout、fallback 过滤顺序、ACL SQL 语义、candidate validation error 和敏感 metadata redaction。3.6 的 log 不得保存 SQL、tsquery、raw content 或 query_terms 原文。
- Story 3.4 完成 `HybridRetriever`、`RRFMerger`、RRF provenance、normalized fusion score 和安全 trace。3.6 要复用 provenance 做复盘摘要，不要重新实现 RRF merge。
- Story 3.5 完成 `RerankingRetriever`、`FakeReranker`、safe rerank provenance、fallback/fail_closed、pre-rerank guard、provider output permutation validation 和 fail_closed trace freshness。3.6 要把 degraded/failed rerank 状态写入日志，但不得扩大 top_k 或引入未授权候选。

### Architecture Requirements

- 本 story 跨 API Layer、Application Service Layer、Retrieval Domain、Storage Layer。
- API route 必须保持薄层，不调用 LLM、vector DB、embedding provider、reranker 或 SQLAlchemy。
- `tenant_id`、`user_id`、roles、department、permissions 必须来自 `AuthContext`，不得从请求体读取。
- 检索权限必须继续在 dense/sparse 查询阶段执行，`RetrievalService` 结果侧 guard 是最后防线。
- `retrieval_logs` 是可复盘证据，不是企业全文存储；只保存摘要和 metadata。
- storage model 和 domain DTO 必须分离；SQLAlchemy model 不得传入 retrieval domain。
- expected domain/storage errors 必须转稳定 error code；不要裸 `except Exception` 后吞异常。

### Implementation Boundaries

- Do not implement context packing.
- Do not implement prompt building.
- Do not implement citation extraction beyond returning existing retrieval source metadata.
- Do not implement `/query`, `/chat`, SSE streaming, LLMProvider, RAG generation, session memory or Open WebUI adapter.
- Do not implement retrieval eval fixtures or smoke runner; Story 3.7 owns that.
- Do not implement real cross-encoder, Cohere, OpenAI, Qwen, DeepSeek, vLLM, Ollama or OpenSearch adapters in this story.
- Do not add `sentence-transformers`, `transformers`, `torch`, `cohere` or other model dependencies.
- Do not call real external embedding APIs, LLM APIs, rerank APIs, OpenSearch, network services or production PostgreSQL in default tests.
- Do not log or return query full text, chunk content, SQL raw text, tsquery/tsvector, vector, embedding, provider raw response, API keys, access tokens, secrets, passwords or local absolute paths.

### Latest Technical Information

- FastAPI official docs still recommend using return annotations or `response_model` for output validation, OpenAPI schema generation, serialization and output filtering. This supports keeping `/retrieve` on `response_model=ApiResponse[RetrieveResponse]` rather than returning arbitrary dicts. Source: https://fastapi.tiangolo.com/tutorial/response-model/
- FastAPI official bigger-applications docs use `APIRouter` split across modules, matching this repo's `apps/api/routes/*` pattern. Source: https://fastapi.tiangolo.com/tutorial/bigger-applications/
- SQLAlchemy 2.0 async documentation is the relevant reference for `AsyncSession` usage; this story should continue existing async repository/session patterns instead of adding sync DB access in API code. Source: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- Alembic operation docs remain the reference for `op.create_table` and `op.create_index`; the migration should be explicit and portable for SQLite smoke while preserving PostgreSQL compatibility. Source: https://alembic.sqlalchemy.org/en/latest/ops.html

### UX / Product Notes

- 本 story 不实现自定义 UI，但 Retrieval Diagnostics 后续需要依赖 `retrieval_logs` 展示 dense/sparse/RRF/rerank/threshold 阶段摘要。
- 任何面向前端的 retrieval result 都必须能支撑 Source Inspector 后续使用 document/version/chunk/source/page，但 Source Inspector 的二次授权由后续 `/sources/resolve` 负责。
- API response 中长 `document_id`、`version_id`、`chunk_id`、`request_id`、`trace_id` 要保持完整机器可读；前端展示截断不是本 story 职责。

### References

- `_bmad-output/planning-artifacts/epics.md#Story-3.6-retrieve-API-与检索复盘日志`
- `_bmad-output/planning-artifacts/epics.md#Epic-3-授权-Hybrid-Retrieval-与检索复盘`
- `_bmad-output/planning-artifacts/architecture.md#API-Communication-Patterns`
- `_bmad-output/planning-artifacts/architecture.md#Project-Structure-Boundaries`
- `_bmad-output/planning-artifacts/architecture.md#Integration-Points`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-12-Retrieval-Log`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-18-核心-API`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-22-RBAC-与-ACL-检索过滤`
- `project-context.md`
- `_bmad-output/implementation-artifacts/3-1-retrieval-请求模型与权限过滤构建.md`
- `_bmad-output/implementation-artifacts/3-2-dense-retrieval-召回.md`
- `_bmad-output/implementation-artifacts/3-3-bm25-sparse-retrieval-召回.md`
- `_bmad-output/implementation-artifacts/3-4-rrf-merge-去重与阈值过滤.md`
- `_bmad-output/implementation-artifacts/3-5-reranker-接口与降级策略.md`
- `apps/api/main.py`
- `apps/api/routes/upload.py`
- `apps/api/routes/documents.py`
- `apps/api/dependencies.py`
- `apps/api/error_handlers.py`
- `apps/api/service_dependencies.py`
- `packages/common/envelope.py`
- `packages/common/config.py`
- `packages/common/audit.py`
- `packages/retrieval/dto.py`
- `packages/retrieval/service.py`
- `packages/retrieval/filters.py`
- `packages/retrieval/dense.py`
- `packages/retrieval/sparse.py`
- `packages/retrieval/rrf.py`
- `packages/retrieval/rerank.py`
- `packages/retrieval/ports.py`
- `packages/retrieval/exceptions.py`
- `packages/data/storage/audit_models.py`
- `packages/data/storage/audit_repositories.py`
- `packages/data/storage/base.py`
- `migrations/env.py`
- `tests/integration/api/test_upload_routes.py`
- `tests/unit/retrieval/test_service.py`
- `tests/integration/storage/test_alembic_migrations.py`
- `README.md#Retrieval-Foundation`
- `docs/operations/local-development.md#Retrieval-Local-Checks`
- FastAPI response model docs: https://fastapi.tiangolo.com/tutorial/response-model/
- FastAPI APIRouter docs: https://fastapi.tiangolo.com/tutorial/bigger-applications/
- SQLAlchemy asyncio docs: https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html
- Alembic operation reference: https://alembic.sqlalchemy.org/en/latest/ops.html

## Validation Checklist

Validation Result: PASS（2026-06-07T12:02:26+08:00）

- [x] Story 明确了用户角色、目标和收益。
- [x] Acceptance Criteria 覆盖 `/retrieve` API、薄 route、pipeline 复用、成功/失败 retrieval log、migration、脱敏和测试。
- [x] Tasks 覆盖 API schema/route、application wrapper、log DTO/port/repository、migration、dependency factory、API/storage/application tests、文档和验证命令。
- [x] Dev Notes 明确当前源码状态，尤其是现有 envelope、error handler、AuthContext、RetrievalService、dense/sparse/RRF/rerank provenance 和 audit port。
- [x] 明确不实现 context packing、RAG generation、`/query`、`/chat`、SSE、eval runner、真实 reranker/LLM/OpenSearch adapter。
- [x] 明确 query 全文、chunk 正文、SQL raw text、tsquery/tsvector、vector、embedding、provider raw response、secret、token、本机绝对路径不得进入日志、error details、audit 或 response。

## Change Log

- 2026-06-07: Created comprehensive Story 3.6 developer context for `/retrieve` API, retrieval log persistence, safe observability, and route/application/storage boundaries.
- 2026-06-07: Implemented `/retrieve` API, retrieval application logging wrapper, `retrieval_logs` storage/migration, tests, and documentation updates.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- `.venv\Scripts\python.exe -m pytest tests/integration/api/test_retrieve_routes.py tests/unit/retrieval/test_retrieve_application.py tests/integration/storage/test_alembic_migrations.py tests/integration/storage/test_retrieval_log_repositories.py` -> 13 passed
- `.venv\Scripts\python.exe -m pytest tests/unit/retrieval tests/integration/api tests/integration/storage` -> 170 passed
- `.venv\Scripts\python.exe -m ruff check .` -> passed
- `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed
- `.venv\Scripts\python.exe -m pytest` -> 395 passed

### Completion Notes List

- Implemented thin `POST /retrieve` route returning `ApiResponse[RetrieveResponse]` and deriving auth/request context exclusively from dependencies.
- Added `RetrieveApplicationService` logging wrapper around existing `RetrievalService`, with success/failure retrieval log and audit persistence plus safe retrieval/rerank provenance summaries.
- Added `retrieval_logs` DTOs, port, SQLAlchemy model/repository, Alembic migration, and SQLite migration/storage coverage.
- Wired default dependency pipeline as dense + sparse -> RRF hybrid merge -> fake reranker -> guarded retrieval service, with fake-only embedding provider construction until real adapters exist.
- Updated retrieval docs and local operation checks for `/retrieve`, `retrieval_logs`, safe curl/query examples, and remaining non-goals.

### File List

- apps/api/main.py
- apps/api/routes/retrieve.py
- apps/api/service_dependencies.py
- docs/operations/local-development.md
- migrations/env.py
- migrations/versions/20260527_0008_retrieval_logs.py
- packages/retrieval/application.py
- packages/retrieval/dto.py
- packages/retrieval/ports.py
- packages/retrieval/storage/__init__.py
- packages/retrieval/storage/models.py
- packages/retrieval/storage/repositories.py
- README.md
- tests/integration/api/test_retrieve_routes.py
- tests/integration/storage/test_alembic_migrations.py
- tests/integration/storage/test_retrieval_log_repositories.py
- tests/unit/retrieval/test_retrieve_application.py

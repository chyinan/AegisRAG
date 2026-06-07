---
baseline_commit: NO_VCS
---

# Story 4.7: Open WebUI Chat Adapter、Source Detail 与轻量前端契约

Status: done

生成时间：2026-06-07T19:24:29+08:00

## Story

As a 企业员工,
I want 通过 Open WebUI 兼容 chat adapter 和轻量 sidecar 使用查询、citation、source drilldown 和 job 状态,
so that MVP 可以展示可信企业 RAG 闭环而不是只暴露裸 API。

## Acceptance Criteria

1. **Open WebUI 兼容 Chat Completions adapter**
   - Given Open WebUI 或兼容客户端配置本服务为 OpenAI-compatible server
   - When 调用 `GET /v1/models`
   - Then 返回 OpenAI-compatible model list，至少包含当前后端 RAG chat model id、object、created、owned_by
   - And model id 来自配置或现有 `LLM_PROVIDER` / `LLM_MODEL`，不得硬编码真实厂商模型
   - When 调用 `POST /v1/chat/completions`
   - Then route 只解析 OpenAI-compatible schema、注入 `AuthenticatedRequestContext`、调用 adapter/application service、格式化响应
   - And adapter 复用现有 `ChatApplicationService` / `/chat` RAG 链路，不复制 retrieval、context packing、prompt build、generation、citation extraction
   - And 支持 `stream=false` 和 `stream=true`，streaming 输出 OpenAI-compatible `data: {...}` chunks 和 `[DONE]` 终止，不破坏现有 `/chat/stream` 命名 SSE 协议
   - And Open WebUI 传入的 system/developer/tool messages 只能作为客户端会话输入处理，不得覆盖后端 PromptBuilder 安全策略、权限策略或 citation 策略

2. **Adapter 返回足够的可信 RAG metadata**
   - Given Open WebUI adapter 收到 chat 结果
   - When 返回非流式 response
   - Then response 包含 `id`、`object`、`created`、`model`、`choices`、`usage` 兼容字段
   - And 通过后端扩展字段返回 `request_id`、`trace_id`、`session_id`、`citations`、`no_answer`、`unsupported_claims`、safe `metadata`
   - And citation 只能来自后端 `ChatResponse.citations`，前端/adapter 不得从 answer 文本解析或补造 citation
   - When 返回 stream chunks
   - Then final 或最后一个 metadata chunk 必须携带 `request_id`、`trace_id`、`session_id` 和 citation summary，且不得泄露 prompt、chunk content、provider raw response 或用户敏感全文

3. **`POST /sources/resolve` 二次授权 Source Detail**
   - Given 用户点击 citation
   - When 前端调用 `POST /sources/resolve`
   - Then 后端必须重新校验当前 `AuthContext`、tenant、RBAC、ACL、soft delete、document/version/chunk identity 和 version visibility
   - And 请求 schema 至少支持 `document_id`、`version_id`、`chunk_id`，可选 `page_start`、`page_end`、`request_id` 或 `citation_ref`
   - And 成功 response 只返回授权片段、安全摘要、document/version/chunk/page/source metadata、title_path、retrieval_method、score、request_id、trace_id
   - And 无权限、不存在、软删除、版本不可见、chunk inactive、ACL denied 使用同一类安全 denial shape，不能暴露资源是否存在
   - And source resolve 不调用 LLM、不调用 retrieval top_k、不依赖前端传入的 ACL 结论

4. **Source resolve 服务边界和复用规则**
   - Given 当前 `RetrievalCandidateHydrator` 已有 chunk identity、status、tenant 和 ACL 校验逻辑
   - When 实现 source resolve
   - Then 优先复用或抽取同等 ACL 校验 helper，避免实现一套不一致的权限判断
   - And 新增服务应位于 application/domain 边界，例如 `packages/rag/source_resolver.py` 或等价模块，repository 通过 Protocol 注入
   - And SQLAlchemy 只允许出现在 `packages/data/storage/*` repository，route 和 RAG source resolver 不直接导入 SQLAlchemy
   - And route 不读数据库、不拼接文本片段、不直接检查 ACL mapping

5. **Job / Document version status 展示契约**
   - Given 管理员或知识管理员查看上传和索引状态
   - When 调用已有或新增 status endpoint
   - Then 返回 uploaded、parsing、parsed、chunking、chunked、embedding、indexing、retrieval_ready、failed_retryable、failed_terminal、deleted 等稳定状态
   - And 返回安全字段：document_id、version_id、job_id、status、chunk_count、embedding_provider、embedding_model、embedding_version、embedding_dim、vector_count、index_status、attempt_count、last_attempt_at、next_retry_at、error_code、request_id、trace_id
   - And 错误只显示安全摘要和稳定 error_code，不返回 parser stack trace、object_key、本地路径、原文内容、SQL、provider raw response
   - And 普通查询用户不能通过 status endpoint 枚举未授权文档；管理 status 继续使用 `document:manage` 或等价管理权限

6. **轻量前端 / sidecar 契约文档**
   - Given 第一阶段自定义前端或 sidecar 存在
   - When 验收 MVP
   - Then 只要求上传、查询、citation、Source Inspector、job 状态和日志/eval 入口契约
   - And 不要求实现完整 React/Next.js 管理台、复杂文档预览器、Graph RAG、多 Agent 或 Tool Review UI
   - And 文档必须说明 Open WebUI 是入口，不是治理边界；权限、citation、source visibility 全部由后端决定
   - And README 或 `docs/operations/local-development.md` 给出 Open WebUI Base URL、Bearer/JWT 或本地 dev headers、`/v1/models`、`/v1/chat/completions`、`/sources/resolve` 示例

7. **自定义 UI 可访问性和状态规则**
   - Given Source Inspector、Knowledge Admin、Diagnostics、Eval Reports 或 Agent Review 进入自定义 UI
   - When 验收 UI 行为
   - Then 满足 WCAG 2.2 AA、键盘聚焦、`aria-live`、alert region、drawer/sheet 焦点恢复、非纯颜色状态表达
   - And 长 `document_id`、`version_id`、`chunk_id`、`request_id`、`trace_id` 必须换行或截断并提供完整值读取方式
   - And citation chip、source drawer、job row 不得依赖 hover-only 关键操作
   - And final event 到达前不得启用“复制带来源答案”

8. **审计、日志和 redaction**
   - Given Open WebUI adapter、source resolve、job status 成功、权限拒绝或 expected error
   - When application service 完成
   - Then 记录结构化 audit/log，包含 request_id、trace_id、tenant_id、user_id、action、resource type/id、latency、status、error_code
   - And source resolve audit metadata 包含 document_id、version_id、chunk_id、authorized、denial_reason 的安全枚举，不包含 chunk content 全文
   - And adapter audit metadata 包含 model、stream、session_id、citation_count、token usage 或 safe usage summary
   - And 日志不得记录 API key、Bearer token、完整用户消息、prompt、chunk content、provider raw response、本地绝对路径或 SQL

9. **错误语义和兼容性**
   - Given Open WebUI adapter 请求缺少认证上下文或权限
   - When 后端拒绝
   - Then 非流式返回 OpenAI-compatible error envelope 或现有结构化错误映射，且包含 request_id
   - And streaming 在已开始后发生 expected error 时输出兼容 error chunk 或安全终止 chunk，再发送 `[DONE]`
   - Given `/query`、`/query/stream`、`/chat`、`/chat/stream` 已存在
   - When 实现 4.7
   - Then 不改变这些现有 endpoint 的 response contract、SSE event 类型、权限 gate 或测试期望

10. **测试覆盖**
    - Given 单元测试运行
    - When 验证 Open WebUI adapter DTO/service
    - Then 覆盖 model list、latest user message extraction、system/developer message 不覆盖后端策略、non-stream response、stream chunk formatting、citation extension fields、safe metadata redaction
    - And 覆盖 adapter 复用 ChatApplicationService，不复制 RAG query internals
    - Given source resolve 测试运行
    - Then 覆盖授权 chunk 成功、跨 tenant 拒绝、跨 user/role/department ACL 拒绝、soft-deleted document/version/chunk 拒绝、identity mismatch 拒绝、无权限和不存在使用同一安全 denial
    - And 覆盖 route 权限拒绝不调用 service
    - Given API 集成测试运行
    - Then 覆盖 `GET /v1/models`、`POST /v1/chat/completions` stream/non-stream、`POST /sources/resolve`、document/job status 契约
    - And 使用 fake provider、service override、sqlite 或 repository mock，不真实调用 OpenAI/Qwen/DeepSeek/Ollama/vLLM、网络、Docker、Redis、MinIO 或生产数据库

11. **架构边界测试和文档验证**
    - Given 架构边界测试运行
    - When 扫描新 route 和 service
    - Then `apps/api/routes/openwebui.py`、`apps/api/routes/sources.py` 不导入 storage、provider adapter、SQLAlchemy、vector store adapter 或 retrieval internals
    - And `packages/rag/openwebui.py`、`packages/rag/source_resolver.py` 不导入 FastAPI、Starlette、SQLAlchemy、provider SDK、Redis、MinIO
    - And 文档列出 out of scope：完整自定义前端、真实 provider adapter、文档预览器、Agent tool events、Open WebUI function/tool bridge、eval dashboard
    - And 运行并记录：相关 unit tests、API integration tests、architecture boundaries、ruff、mypy；如成本可接受，运行全量 pytest

## Tasks / Subtasks

- [x] 新增 Open WebUI adapter DTO 和 service（AC: 1, 2, 8, 9, 10）
  - [x] 新增 `packages/rag/openwebui.py` 或等价模块，定义 `OpenAIModelListResponse`、`OpenAIChatCompletionRequest`、`OpenAIChatCompletionResponse`、stream chunk DTO。
  - [x] 从 OpenAI-compatible messages 中只提取当前用户 query；历史继续交给已有 `session_id` / `ChatApplicationService` 管理，避免客户端 system/developer 消息影响后端 prompt policy。
  - [x] 支持 `max_tokens` / `max_completion_tokens` 映射到 `QueryCommand.max_output_tokens`，`metadata_filter` 只能来自后端允许的 structured metadata，不接受 tenant scope 扩大。
  - [x] 将 `ChatResponse.citations` 映射到后端扩展字段，不从 answer 文本解析 citation。
  - [x] streaming adapter 使用独立 OpenAI-compatible SSE formatter，不复用 `format_sse_event()` 输出命名事件。

- [x] 新增 OpenAI-compatible API route（AC: 1, 2, 9, 10, 11）
  - [x] 新增 `apps/api/routes/openwebui.py`。
  - [x] `GET /v1/models` 返回当前 RAG model list。
  - [x] `POST /v1/chat/completions` 支持 stream/non-stream，复用 `RagQueryContextDep` 或等价权限 gate。
  - [x] 在 `apps/api/main.py` include router。
  - [x] 在 `apps/api/service_dependencies.py` 装配 adapter service，复用现有 `ChatApplicationService` wiring。

- [x] 新增 Source Resolve DTO、service 和错误（AC: 3, 4, 8, 9, 10）
  - [x] 在 `packages/rag/dto.py` 或 `packages/rag/source_resolver.py` 定义 `SourceResolveRequestBody`、`SourceResolveCommand`、`SourceResolveResponse`。
  - [x] 定义稳定错误码：`SOURCE_ACCESS_DENIED`、`SOURCE_REFERENCE_NOT_FOUND`、`SOURCE_REFERENCE_INVALID`、`SOURCE_RESOLVE_FAILED` 或等价集合；无权限和不存在对外使用同一安全语义。
  - [x] Source resolve service 通过 Protocol 读取 `ChunkRecord`、`DocumentRecord`、`DocumentVersionRecord`，并校验 tenant、document/version/chunk identity、status、deleted_at、ACL。
  - [x] 返回内容片段必须有长度上限和安全摘要；默认不返回整篇文档，不返回 object_key 或本地路径。
  - [x] 记录 `rag.source.resolve` audit 事件。

- [x] 新增 `/sources/resolve` route 和 dependency wiring（AC: 3, 4, 8, 9, 10, 11）
  - [x] 新增 `apps/api/routes/sources.py`，`POST /sources/resolve` 返回统一 envelope。
  - [x] route 使用 RAG 查询权限 gate：必须具备 `document:read` 和 `retrieval:query`。
  - [x] route 只调用 source resolve service，不直接导入 repository 或 SQLAlchemy。
  - [x] 在 `apps/api/main.py` include router。
  - [x] 在 `apps/api/service_dependencies.py` 装配 `SourceResolveService(repository=DocumentRepository(session), audit=SqlAlchemyAuditPort(...))`。

- [x] 补强 document/job status 契约（AC: 5, 8, 10）
  - [x] 评估是否扩展 `DocumentVersionStatusResult` 增加 `job_id`、`attempt_count`、`last_attempt_at`、`next_retry_at`、safe `error_summary`；如果已有字段不足，以 backward-compatible 方式新增 optional fields。
  - [x] 如需要新增 `GET /documents/{document_id}/versions/{version_id}/jobs` 或扩展现有 status route，保持 `document:manage` 权限。
  - [x] repository 查询只能按当前 tenant 范围，不能提供跨租户枚举接口。
  - [x] 错误摘要使用 allowlist 字段，禁止返回 parser stack trace、object_key、原文、SQL、provider raw response。

- [x] 文档更新（AC: 6, 7, 11）
  - [x] 更新 `README.md#RAG Foundation`：标记 Open WebUI adapter、Source Resolve、job/status 契约状态。
  - [x] 更新 `docs/operations/local-development.md`：增加 Open WebUI 连接说明、`/v1/models`、`/v1/chat/completions`、`/sources/resolve` curl 示例。
  - [x] 如有必要新增 `docs/api/openwebui.md` 或 `docs/api/source-resolve.md`，明确扩展字段、错误语义、out of scope 和安全边界。
  - [x] 文档说明 Source Inspector UI 可访问性规则和 final 前禁止复制带来源答案。

- [x] 单元测试（AC: 1-4, 8-11）
  - [x] 新增 `tests/unit/rag/test_openwebui_adapter.py`。
  - [x] 新增 `tests/unit/rag/test_source_resolver.py`。
  - [x] 扩展 `tests/unit/rag/test_streaming.py` 或新增 formatter 测试覆盖 OpenAI-compatible stream chunks。
  - [x] 扩展 `tests/unit/test_architecture_boundaries.py`，覆盖新 route/service import 边界。

- [x] API / storage 集成测试（AC: 3, 5, 9, 10）
  - [x] 新增 `tests/integration/api/test_openwebui_routes.py`。
  - [x] 新增 `tests/integration/api/test_sources_routes.py`。
  - [x] 扩展 `tests/integration/api/test_document_routes.py` 覆盖增强 status 字段和权限。
  - [x] 如新增 repository 查询方法，扩展 `tests/integration/storage/test_document_repositories.py`。

- [x] 验证命令（AC: 10, 11）
  - [x] `.venv\Scripts\python.exe -m pytest tests\unit\rag\test_openwebui_adapter.py tests\unit\rag\test_source_resolver.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests\integration\api\test_openwebui_routes.py tests\integration\api\test_sources_routes.py tests\integration\api\test_document_routes.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests\unit\test_architecture_boundaries.py -q`
- [x] `.venv\Scripts\python.exe -m ruff check .`
- [x] `.venv\Scripts\python.exe -m mypy apps packages tests`
- [x] 如成本可接受，运行 `.venv\Scripts\python.exe -m pytest -q`

### Review Findings

- [x] [Review][Patch] Source resolve 没有权威来源填充 retrieval_method / score；按生产级后端授权边界选择通过 `citation_ref` 回查服务端 retrieval/chat 记录，不能信任前端回传分数 [packages/rag/source_resolver.py:55]
- [x] [Review][Patch] OpenWebUI adapter 缺少 AC8 要求的 adapter 级审计 [packages/rag/openwebui.py:191]
- [x] [Review][Patch] Document/job status endpoint 没有记录成功、拒绝或 expected error audit [packages/data/lifecycle.py:93]
- [x] [Review][Patch] OpenAI-compatible message content parts / nullable assistant-tool content 会被 schema 直接拒绝 [packages/rag/openwebui.py:76]
- [x] [Review][Patch] OpenAI-compatible streaming upstream exception 未保证输出安全 error chunk 和 `[DONE]` [packages/rag/openwebui.py:229]
- [x] [Review][Patch] Source resolve 直接返回原始 source_uri，可能泄露本地路径或内部对象 URI [packages/rag/source_resolver.py:378]
- [x] [Review][Patch] Source resolve 允许 failed / in-progress version 在存在 stale active chunk 时被解析 [packages/rag/source_resolver.py:27]
- [x] [Review][Patch] Source resolve 测试缺少 AC10 指定的跨 tenant、role/department ACL、chunk deleted_at、不可见版本等拒绝矩阵 [tests/unit/rag/test_source_resolver.py:114]

## Dev Notes

### Current Repository State

- 当前目录不是 git repository；`git log --oneline -5` 返回 `fatal: not a git repository`。最近实现上下文来自已完成 story 文件和源码扫描。
- Story 4.6 已完成 `/chat`、`/chat/stream`、chat memory、safe audit、final-only assistant persistence，并在 `apps/api/routes/chat.py` 保持薄 route。
- 现有 `apps/api/routes/query.py` 定义 `RagQueryContextDep`，要求同时具备 `document:read` 和 `retrieval:query`。
- 现有 `apps/api/dependencies.py` 支持 Bearer JWT 和本地 dev auth headers，二者产出同一 `AuthContext`。
- `packages/rag/streaming.py` 的 `format_sse_event()` 输出命名 SSE：`event: token|citation|error|final`。OpenAI-compatible streaming 需要独立 `data: {...}` / `data: [DONE]` formatter，不能直接复用它。
- `packages/rag/dto.py` 已有 `ChatRequestBody`、`ChatResponse`、`Citation`、`QueryCommand`、`PromptMemoryContext`。
- `packages/rag/chat.py` 已封装 chat application service，且记录 `rag.chat` / `rag.chat.stream` audit。
- `packages/rag/hydration.py` 已有对 `ChunkRecord` 的 tenant、identity、status、deleted_at、ACL 校验；Source Resolve 应复用或抽取该逻辑，避免 ACL 语义分叉。
- `packages/data/storage/repositories.py` 已有 `get_chunk()`、`get_document()`、`get_document_version_status()`，但没有专门的 source resolve 方法。
- `apps/api/routes/documents.py` 目前提供 `GET /documents/{document_id}/versions/{version_id}/status`，使用 `DocumentLifecycleService` 和 `document:manage` 语义。
- `DocumentVersionStatusResult` 当前包含 status、chunk_count、embedding provider/model/version/dim、vector_count、index_status、deleted_at、error_code、request_id、trace_id；缺少 job_id、attempt_count、last_attempt_at、next_retry_at。
- README 仍明确未包含 Open WebUI adapter 和 Source Inspector `/sources/resolve`，本 story 应更新该状态。

### Previous Story Intelligence

- Story 4.5 建立了 framework-free RAG streaming DTO 与 SSE formatter，并修复了 stream error detail allowlist、provider failure metadata、client disconnect audit、reserved tool event payload DTO。
- Story 4.6 建立了 chat memory 和 `/chat` endpoint，review 后修复了权限 gate、当前消息重复进入 history、summary redaction、显式 commit、stream terminal hardening、chat audit coverage 和 sequence uniqueness。
- 4.7 不能回退这些边界：不得让 Open WebUI adapter 直接调用 LLM provider，不得在 route 里拼 prompt，不得让前端决定 citation/source visibility。

### Files Likely To Touch

- NEW `packages/rag/openwebui.py`
- NEW `packages/rag/source_resolver.py`
- UPDATE `packages/rag/dto.py` if shared DTOs are placed there
- UPDATE `packages/rag/__init__.py`
- NEW `apps/api/routes/openwebui.py`
- NEW `apps/api/routes/sources.py`
- UPDATE `apps/api/main.py`
- UPDATE `apps/api/service_dependencies.py`
- UPDATE `packages/data/dto.py`
- UPDATE `packages/data/lifecycle.py`
- UPDATE `packages/data/storage/repositories.py`
- UPDATE `packages/data/exceptions.py`
- UPDATE `tests/unit/test_architecture_boundaries.py`
- NEW `tests/unit/rag/test_openwebui_adapter.py`
- NEW `tests/unit/rag/test_source_resolver.py`
- NEW `tests/integration/api/test_openwebui_routes.py`
- NEW `tests/integration/api/test_sources_routes.py`
- UPDATE `tests/integration/api/test_document_routes.py`
- UPDATE `tests/integration/storage/test_document_repositories.py` if repository methods are added
- UPDATE `README.md`
- UPDATE `docs/operations/local-development.md`
- OPTIONAL NEW `docs/api/openwebui.md`
- OPTIONAL NEW `docs/api/source-resolve.md`

### Existing Patterns To Reuse

- Thin route pattern: `apps/api/routes/chat.py` and `apps/api/routes/query.py`.
- Permission gate: `RagQueryContextDep` for query/chat/source resolve; document management status remains `document:manage`.
- Chat orchestration: `ChatApplicationService.chat()` and `stream_chat()` are the only adapter entry points for RAG answer generation.
- Audit pattern: `packages.common.audit.AuditEvent` and `SqlAlchemyAuditPort(auto_commit=True)` for API lifecycle audit.
- Redaction: `packages.common.logging.redact_mapping()` and existing chat/RAG audit metadata patterns.
- ACL logic: `packages.vectorstores.acl.acl_allows()` and hydration validation in `packages/rag/hydration.py`.
- Storage pattern: `DocumentRepository` converts SQLAlchemy errors into stable storage/domain errors and keeps tenant-scoped queries.
- API tests: service dependency overrides in `tests/integration/api/test_chat_routes.py` and `test_document_routes.py`.

### Architecture Requirements

- Story layer: API adapter + RAG application adapter + source detail application service + data repository extension.
- Open WebUI route may be FastAPI-specific, but adapter DTO/service must be framework-free.
- Source Resolve is a backend authorization boundary. Frontend passes a citation reference; backend decides visibility.
- `AuthContext` and tenant filter must be enforced before any chunk content is returned.
- OpenAI-compatible adapter is not an LLM provider adapter. It is an inbound client compatibility adapter backed by this project's RAG `/chat` pipeline.
- Do not add LangChain/LangGraph/OpenAI SDK dependency for this story.
- Do not implement `POST /v1/embeddings`, Open WebUI Functions, Tool Calling bridge, image/audio endpoints, or Agent tool events in this story.

### Critical Implementation Guardrails

- Do not let Open WebUI client `system` messages become backend system instructions.
- Do not parse citations from generated answer text.
- Do not return source chunks unless Source Resolve has rechecked AuthContext, tenant, ACL, soft delete and identity.
- Do not disclose whether an unauthorized document/chunk exists.
- Do not require `document:manage` for ordinary citation resolve; that would break employee Source Inspector usage.
- Do not loosen management status endpoints to `document:read`; status can reveal ingestion/indexing metadata and remains admin/knowledge-admin scope.
- Do not break existing `/chat/stream` named SSE contract while adding OpenAI-compatible streaming.
- Do not store API key/Bearer token in logs or audit metadata.

### Suggested DTO Shape

```python
class SourceResolveCommand(BaseModel):
    document_id: str
    version_id: str
    chunk_id: str
    page_start: int | None = None
    page_end: int | None = None
    citation_ref: str | None = None
```

```python
class SourceResolveResponse(BaseModel):
    request_id: str
    trace_id: str
    document_id: str
    version_id: str
    chunk_id: str
    source: str | None
    source_uri: str | None
    source_type: str
    page_start: int | None
    page_end: int | None
    title_path: tuple[str, ...]
    text_excerpt: str
    excerpt_char_count: int
    token_count: int
    retrieval_method: str | None = None
    score: float | None = None
    metadata: Mapping[str, object] = Field(default_factory=FrozenDict)
```

```json
{
  "id": "chatcmpl-req-1",
  "object": "chat.completion",
  "created": 1780831469,
  "model": "local-rag-chat",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "基于授权上下文的回答。"
      },
      "finish_reason": "stop"
    }
  ],
  "request_id": "req-1",
  "trace_id": "trace-1",
  "session_id": "session-1",
  "citations": []
}
```

### Latest Technical Information

- Open WebUI 当前文档说明其对 OpenAI-compatible servers 采用 OpenAI Chat Completions protocol，推荐通过 Admin Settings -> Connections -> OpenAI 添加 compatible server Base URL。
- Open WebUI 兼容服务必须实现 `GET /v1/models` 用于模型发现，`POST /v1/chat/completions` 用于核心 chat 和 streaming；`/v1/embeddings`、audio、image endpoint 是可选项。
- Open WebUI 会传递标准 OpenAI 参数，例如 `temperature`、`top_p`、`max_tokens` / `max_completion_tokens`、`stop`、`seed`、`logit_bias`，并可能传递 tools/tool_choice；本 story 只映射安全需要的字段，tools/tool_choice 保留到 Epic 6 Tool Registry。
- 当前仓库 pins：FastAPI `>=0.136.3,<0.137`、Pydantic `>=2.13.4,<3`、SQLAlchemy `>=2.0.50,<3`。无需新增外部依赖即可完成 adapter、source resolver 和 tests。
- Source: Open WebUI docs, "Starting with OpenAI-Compatible Servers"（访问日期 2026-06-07）：https://docs.openwebui.com/getting-started/quick-start/starting-with-openai-compatible/

### References

- `_bmad-output/planning-artifacts/epics.md#Story-4.7-Open-WebUI-Chat-Adapter-Source-Detail-与轻量前端契约`
- `_bmad-output/planning-artifacts/epics.md#Epic-4-可信-RAG-问答-Citation-与流式会话`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-16-Citation-Answer`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-18-核心-API`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-20-前端集成路径`
- `_bmad-output/planning-artifacts/architecture.md#API-&-Communication-Patterns`
- `_bmad-output/planning-artifacts/architecture.md#Frontend-Architecture`
- `_bmad-output/planning-artifacts/architecture.md#Project-Structure-&-Boundaries`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md#Component-Patterns`
- `_bmad-output/implementation-artifacts/4-6-chat-session-memory-与安全上下文.md`
- `project-context.md`
- `apps/api/dependencies.py`
- `apps/api/routes/chat.py`
- `apps/api/routes/query.py`
- `apps/api/routes/documents.py`
- `apps/api/service_dependencies.py`
- `packages/auth/policies.py`
- `packages/rag/chat.py`
- `packages/rag/dto.py`
- `packages/rag/streaming.py`
- `packages/rag/hydration.py`
- `packages/data/dto.py`
- `packages/data/lifecycle.py`
- `packages/data/storage/repositories.py`
- `packages/vectorstores/acl.py`
- `tests/integration/api/test_chat_routes.py`
- `tests/integration/api/test_document_routes.py`
- `tests/unit/test_architecture_boundaries.py`
- `pyproject.toml`

## Validation Checklist

Validation Result: PASS（2026-06-07T19:24:29+08:00）

- [x] Story 明确了 Open WebUI adapter、Source Resolve、job/status 和轻量 sidecar 的边界。
- [x] Acceptance Criteria 覆盖 OpenAI-compatible endpoints、streaming、citation metadata、Source Resolve 二次授权、job/status、UI 可访问性、审计、错误语义、测试和文档。
- [x] 明确普通 citation resolve 使用 RAG 查询权限，管理 status 使用 document manage 权限。
- [x] 明确复用现有 `ChatApplicationService` 和 RAG 链路，不复制 retrieval/prompt/generation/citation。
- [x] 明确 Open WebUI system/developer messages 不能覆盖后端 PromptBuilder 和权限策略。
- [x] 明确 Source Resolve 不能泄露未授权资源是否存在。
- [x] 明确 route 不直接导入 storage、SQLAlchemy、LLM provider、vector store 或 retrieval internals。
- [x] 明确测试使用 fake/service override/mock/sqlite，不调用外部 LLM 或网络。

## Change Log

- 2026-06-07: Created comprehensive Story 4.7 developer context for Open WebUI-compatible chat adapter, source resolve, job/status contract, lightweight frontend contract, audit, tests and docs.
- 2026-06-07: Implemented Story 4.7 Open WebUI-compatible chat adapter, `/sources/resolve`, enhanced status contract, docs, tests, and validation.

## Dev Agent Record

### Agent Model Used

TBD by dev-story

### Debug Log References

- `.venv\Scripts\python.exe -m pytest tests\unit\rag\test_openwebui_adapter.py -q` -> 5 passed
- `.venv\Scripts\python.exe -m pytest tests\integration\api\test_openwebui_routes.py -q` -> 4 passed
- `.venv\Scripts\python.exe -m pytest tests\unit\rag\test_openwebui_adapter.py tests\unit\rag\test_source_resolver.py -q` -> 13 passed
- `.venv\Scripts\python.exe -m pytest tests\integration\api\test_openwebui_routes.py tests\integration\api\test_sources_routes.py tests\integration\api\test_document_routes.py -q` -> 11 passed
- `.venv\Scripts\python.exe -m pytest tests\unit\test_architecture_boundaries.py -q` -> 14 passed
- `.venv\Scripts\python.exe -m pytest tests\integration\storage\test_document_repositories.py::test_document_repository_marks_retrieval_ready_only_after_index_summary_matches_chunks -q` -> 1 passed
- `.venv\Scripts\python.exe -m ruff check .` -> passed
- `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed, 224 source files
- `.venv\Scripts\python.exe -m pytest -q` -> 571 passed
- Review patch validation (2026-06-07T20:20:42+08:00): `.venv\Scripts\python.exe -m pytest tests\unit\rag\test_openwebui_adapter.py tests\unit\rag\test_source_resolver.py tests\unit\data\test_document_lifecycle_service.py -q` -> 28 passed
- Review patch validation (2026-06-07T20:20:42+08:00): `.venv\Scripts\python.exe -m pytest tests\integration\api\test_openwebui_routes.py tests\integration\api\test_sources_routes.py tests\integration\api\test_document_routes.py tests\unit\test_architecture_boundaries.py -q` -> 25 passed
- Review patch validation (2026-06-07T20:20:42+08:00): `.venv\Scripts\python.exe -m ruff check .` -> passed
- Review patch validation (2026-06-07T20:20:42+08:00): `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed, 224 source files
- Review patch validation (2026-06-07T20:20:42+08:00): `.venv\Scripts\python.exe -m pytest -q` -> 580 passed

### Completion Notes List

- Implemented framework-free Open WebUI adapter DTO/service with configured model discovery, latest-user-message extraction, safe metadata redaction, citation extension fields, and OpenAI-compatible SSE formatter.
- Added `/v1/models` and `/v1/chat/completions` routes using existing RAG query permission gate and `ChatApplicationService` wiring.
- Implemented Source Resolve service and `/sources/resolve` route with tenant/RBAC/ACL/soft-delete/identity revalidation, safe denial semantics, excerpt length limit, and `rag.source.resolve` audit.
- Added shared RAG ACL helper reused by retrieval hydration and source resolve to avoid ACL semantic drift.
- Extended document version status with backward-compatible job/retry/error summary fields while keeping `document:manage` permission and tenant-scoped repository reads.
- Updated README and local development docs for Open WebUI setup, OpenAI-compatible endpoints, source resolve, status contract, sidecar scope, accessibility rules, and out-of-scope items.
- Review fixes added OpenWebUI adapter audit, OpenAI content-part normalization, stream error chunk termination, status endpoint audit, source URI filtering, retrieval-ready-only source visibility, server-side citation metadata lookup for source resolve, and expanded denial matrix tests.

### File List

- README.md
- docs/operations/local-development.md
- apps/api/main.py
- packages/rag/openwebui.py
- packages/rag/__init__.py
- apps/api/routes/openwebui.py
- apps/api/routes/sources.py
- apps/api/service_dependencies.py
- packages/data/dto.py
- packages/data/storage/repositories.py
- packages/rag/access.py
- packages/rag/hydration.py
- packages/rag/source_resolver.py
- tests/unit/rag/test_openwebui_adapter.py
- tests/unit/rag/test_source_resolver.py
- tests/unit/test_architecture_boundaries.py
- tests/unit/data/test_document_lifecycle_service.py
- tests/integration/api/test_openwebui_routes.py
- tests/integration/api/test_sources_routes.py
- tests/integration/api/test_document_routes.py
- tests/integration/storage/test_document_repositories.py

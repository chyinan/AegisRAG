---
baseline_commit: NO_VCS
---

# Story 4.5: SSE Streaming 回答事件

Status: done

生成时间：2026-06-07T17:01:37+08:00

## Story

As a 前端调用方,
I want `/query` 或 `/chat` 可以流式返回 token、citation 和 final 事件,
so that 用户能更快看到回答并获得完整 metadata。

## Acceptance Criteria

1. **新增后端 SSE 事件契约**
   - Given 调用方请求流式 RAG 回答
   - When 后端产生 SSE event
   - Then 每个事件都使用稳定 `event` 类型和 JSON `data` payload
   - And 事件类型至少支持 `token`、`citation`、`error`、`final`
   - And 预留 `tool_call`、`tool_result` 事件类型给 Epic 6 Agent，但本故事不实现 Agent 工具流
   - And 每个 payload 必须包含 `request_id`，可获得时还必须包含 `trace_id`

2. **token 事件来自 `LLMProvider.stream`**
   - Given `RagGenerationService.stream()` 接收已构造的 `PromptBuildResult`
   - When provider 产出非 final `GenerateChunk`
   - Then streaming pipeline 发送 `token` SSE event
   - And payload 至少包含 `request_id`、`trace_id`、`index`、`delta`
   - And 不包含 prompt、chunk content、完整 query、provider raw response、API key、access token、本地绝对路径或 SQL

3. **citation 事件只来自授权 packed context / extractor**
   - Given context packing 和 prompt build 已完成
   - When citation source 可用
   - Then 发送 `citation` SSE event
   - And citation event 包含 `document_id`、`version_id`、`chunk_id`、`source`、`source_uri`、`source_type`、`page_start`、`page_end`、`title_path`、`retrieval_method`、`score`
   - And citation 只能从本次 `PackedContext.items[].citation_sources` 或 `CitationExtractor` 结果派生
   - And 不得从 LLM 输出、前端 payload 或用户输入中补造来源

4. **final 事件包含完整安全收尾信息**
   - Given streaming pipeline 正常完成
   - When final provider chunk 到达并完成 citation extraction
   - Then 发送单个 `final` SSE event
   - And payload 至少包含 `request_id`、`trace_id`、`tenant_id`、`user_id`、`answer`、`citations`、`no_answer`、`unsupported_claims`、`metadata`
   - And `metadata` 使用与非流式 `QueryResponse` 等价的安全摘要：retrieval top_k/result_count、context item/source counts、prompt risk counts、generation provider/model/version/token usage、citation counts、latency_ms、error_code
   - And final 之前的 token 顺序必须可还原为最终 answer 或明确记录 provider final response 为准

5. **结构化 streaming error 事件**
   - Given retrieval、hydration、context packing、prompt build、LLM stream、citation extraction 或 audit 任一阶段发生 expected domain error
   - When stream 尚未正常完成
   - Then 发送 `error` SSE event，payload 包含 `request_id`、`trace_id`、`code`、`message`、`details` 的安全摘要
   - And 错误 details 不泄露 query、prompt、chunk content、provider raw response、secret、token、本机路径、SQL 或未授权资源存在性
   - And 尽可能随后发送 `final` event，标记 `status="error"` 或等价终止状态；如果连接必须终止，必须在 error event 中明确 `terminal=true`

6. **API route 保持薄层职责**
   - Given FastAPI route 处理 streaming 请求
   - When 检查代码边界
   - Then route 只解析 schema、注入 `AuthenticatedRequestContext`、调用 application service、返回 streaming response
   - And route 不拼 prompt、不调用 LLM provider、不直接访问 vector store、chunk storage、retriever 或 citation extractor
   - And 权限 gate 与 `/query` 保持一致，缺少 `document:read` 或等价 query 权限时在调用 service 前返回结构化错误

7. **非流式 `/query` 兼容性不被破坏**
   - Given 现有 `POST /query` 非流式链路已完成
   - When 添加 streaming 能力
   - Then 现有 `/query` response contract、tests、audit metadata、hydration fail-closed 语义保持不变
   - And 新增 streaming 入口不得让非流式调用改用不同的权限、retrieval、context packing、prompt build、generation 或 citation 规则
   - And 新增代码应复用 4.4 中已建立的 query orchestration 边界，避免复制整条 RAG 链路造成漂移

8. **审计和日志覆盖 streaming 生命周期**
   - Given streaming 请求成功、no-answer、provider stream failure 或客户端断开
   - When application service 完成或捕获 expected error
   - Then 记录 `rag.query.stream` 或等价 audit action
   - And metadata 包含 request_id、trace_id、tenant_id、user_id、latency、top_k/result_count、context item/source counts、provider/model/version、token usage、event counts、citation_count、unsupported_count、error_code
   - And 不记录 token 原文、answer 全文、query、prompt、chunk content 或 provider raw payload

9. **测试覆盖主路径、错误路径和安全边界**
   - Given 单元测试运行
   - When 验证 streaming service / event formatter / route
   - Then 覆盖 token -> citation -> final 正常顺序、no-answer、provider stream failure、DomainError -> error event、final terminal state、safe metadata redaction
   - And 覆盖每个 event payload 都有 request_id，citation event 包含完整来源字段
   - And 覆盖 route 权限拒绝不调用 application service
   - And 使用 `FakeLLMProvider` 或 fake stream provider，不真实调用 OpenAI/Qwen/DeepSeek/Ollama/vLLM、网络、Docker、Redis、MinIO 或生产数据库

10. **文档更新标记 SSE streaming 状态**
    - Given story 完成
    - When 阅读 `README.md#RAG Foundation` 和 `docs/operations/local-development.md`
    - Then 文档说明 streaming 入口、SSE event 类型、curl 示例、本地 fake provider 行为和测试命令
    - And 明确 `/chat`、chat session memory、Source Inspector `/sources/resolve`、Open WebUI adapter、真实 provider adapter、Tool events、RAG eval 仍属后续 story

## Tasks / Subtasks

- [x] 定义 streaming DTO 与 SSE formatter（AC: 1, 2, 3, 4, 5）
  - [x] 在 `packages/rag/dto.py` 新增 `QueryStreamCommand` 或复用 `QueryCommand` 的 streaming 版本；如复用，保持非流式 schema 不受影响。
  - [x] 新增 `RagStreamEvent`、`RagStreamEventPayload` 或具体事件 DTO：`TokenEvent`、`CitationEvent`、`ErrorEvent`、`FinalEvent`。
  - [x] 事件 DTO 使用 Pydantic v2 frozen model；字段保持 JSON 可序列化，source/citation metadata 不使用 raw dict 来源。
  - [x] 新增 `packages/rag/streaming.py`，提供纯 Python SSE frame formatter，例如 `format_sse_event(event) -> str`。
  - [x] formatter 输出 `event: <type>\n` 和 `data: <json>\n\n`，JSON 使用 `model_dump(mode="json")` 或等价安全序列化。
  - [x] formatter 必须拒绝未知事件类型或空 `request_id`；不要把 SSE 逻辑写进 FastAPI route。

- [x] 抽取并复用 4.4 Query orchestration 公共步骤（AC: 6, 7）
  - [x] 审视 `packages/rag/query.py` 当前 `query()` 主链路，把 retrieval、hydration、context packing、prompt build 提取为私有 helper 或小 DTO，例如 `_prepare_query_context()`。
  - [x] helper 输出仅包含 `RetrievalResult`、`PackedContext`、`PromptBuildResult` 和安全阶段计数；不得输出 route-specific 或 HTTP 类型。
  - [x] 保留现有 `RagQueryApplicationService.query()` 行为和 tests；不要为了 streaming 改变非流式 `/query` response shape。
  - [x] 如果需要新增 class，优先放在 `packages/rag/query.py` 或 `packages/rag/streaming.py`，不要新建跨包 application 层。

- [x] 实现 streaming application service 方法（AC: 2, 3, 4, 5, 8）
  - [x] 在 `RagQueryApplicationService` 新增 `stream_query(...) -> AsyncIterator[RagStreamEvent]` 或新增 `RagQueryStreamingService`；优先复用同一个 service 的依赖，避免重复装配。
  - [x] 执行顺序必须为 `retrieval -> hydration -> context packing -> prompt build -> LLM stream -> citation extraction -> final`。
  - [x] 在 provider 非 final chunk 到达时 yield `token` 事件，使用 chunk `index` 和 `delta`。
  - [x] 在 citation source 可用时 yield `citation` 事件；推荐在 prompt build 后、token 前先发送 citation 元数据，让前端能显示“来源确认中/已确认”。如果选择 final 前发送，需在 story completion notes 中说明原因并保证 tests 固化顺序。
  - [x] final chunk 到达后用 `chunk.response.text` 或聚合 token 文本生成最终 answer；优先使用 provider final response，避免 token 拼接和最终文本不一致。
  - [x] 对 provider stream final chunk 缺失、identity mismatch、LLMProviderError、RagGenerationError、RagQueryError 等 expected errors 转为 `error` event。
  - [x] 成功、no-answer、error terminal 都记录 audit；audit 写入失败不得把用户 stream 变成业务失败，但要通过安全日志记录。

- [x] 新增 streaming API route（AC: 1, 5, 6, 7）
  - [x] 在 `apps/api/routes/query.py` 增加 streaming 入口，推荐路径 `POST /query/stream`，避免破坏现有 `POST /query` envelope。
  - [x] 使用已有 `RagQueryContextDep` 做权限 gate。
  - [x] 返回 `StreamingResponse` 或 FastAPI/Starlette 等价流式响应，media type 为 `text/event-stream`。
  - [x] 设置必要 headers：`X-Request-ID`，并考虑 `Cache-Control: no-cache`；不要在 route 中拼接事件 payload。
  - [x] route 只把 `service.stream_query(...)` 的事件交给 SSE formatter；不要直接调用 `RagGenerationService.stream()`。

- [x] 更新 dependency wiring（AC: 6, 8）
  - [x] `apps/api/service_dependencies.py` 继续装配同一个 `RagQueryApplicationService`，无需新增真实 provider。
  - [x] 确认 `FakeLLMProvider.stream()` 可支持正常 token/final 流；必要时只扩展 fake 测试能力，不加外部 SDK。
  - [x] 确认 audit port 使用 `SqlAlchemyAuditPort(session, auto_commit=True)` 或现有约定，streaming audit 不因 generator 生命周期丢失。

- [x] 单元测试（AC: 1, 2, 3, 4, 5, 7, 8, 9）
  - [x] 新增 `tests/unit/rag/test_streaming.py`，覆盖 SSE formatter 的 frame 格式、JSON payload、安全字段和未知事件拒绝。
  - [x] 扩展或新增 `tests/unit/rag/test_query_streaming_service.py`，使用 fake retriever/chunk repo/fake stream provider 验证正常事件顺序。
  - [x] 覆盖 citation event 来源字段完整且只来自 `PackedCitationSource`。
  - [x] 覆盖 no-answer 时不调用 LLM stream，返回 final/no-answer 或等价事件，并记录 audit。
  - [x] 覆盖 provider `stream_failed`、identity mismatch、hydration failure、prompt failure 等错误事件，断言 error details 安全。
  - [x] 覆盖 audit metadata 有 event counts、citation_count、token usage、error_code 且不含 query/prompt/content/token。

- [x] API 集成测试（AC: 5, 6, 7, 9）
  - [x] 扩展 `tests/integration/api/test_query_routes.py` 或新增 `test_query_stream_routes.py`。
  - [x] 覆盖 `POST /query/stream` 成功返回 `text/event-stream`，包含 token/citation/final frames。
  - [x] 覆盖 missing auth、missing permission、invalid body 不调用 service，仍返回现有 structured error envelope。
  - [x] 覆盖 service 抛出 expected stream error 时输出 `error` SSE event，不通过 JSON envelope 混入已开始的 stream。
  - [x] 测试不得依赖真实网络、模型、Docker 或生产数据库。

- [x] 架构边界测试（AC: 6, 7）
  - [x] 扩展 `tests/unit/test_architecture_boundaries.py`，确保 `apps/api/routes/query.py` 不导入 `packages.llm.adapters`、`packages.vectorstores.adapters`、`packages.data.storage`、OpenAI/Qwen/DeepSeek/Ollama/vLLM SDK 或 SQLAlchemy。
  - [x] 如新增 `packages/rag/streaming.py`，确认它不导入 FastAPI/Starlette、SQLAlchemy、Redis、MinIO、外部 provider SDK。
  - [x] 确认 `packages/llm` 仍 provider-neutral 且 framework-free。

- [x] 文档更新（AC: 10）
  - [x] 更新 `README.md#RAG Foundation`，把 Story 4.5 SSE streaming 标记为已完成能力。
  - [x] 更新 `docs/operations/local-development.md`，新增 `curl.exe -N` 或等价 SSE 示例、event 格式说明、fake provider 行为、测试命令。
  - [x] 明确 `/chat`、chat memory、Open WebUI adapter、Source Inspector `/sources/resolve`、Agent `tool_call/tool_result` events、真实 provider adapters 和 RAG eval 仍未完成。

- [x] 验证（AC: 1-10）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/rag tests/unit/llm tests/unit/common -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/api/test_query_routes.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/test_architecture_boundaries.py -q`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`
  - [x] 如成本可接受，运行 `.venv\Scripts\python.exe -m pytest -q`

### Review Findings

- [x] [Review][Patch] Streaming error details can leak unauthorized resource identifiers [packages/rag/query.py:338]
- [x] [Review][Patch] Provider stream failures discard known retrieval/context/provider metadata in audit and final event [packages/rag/query.py:347]
- [x] [Review][Patch] Client disconnect/cancellation is not audited as a `rag.query.stream` terminal lifecycle event [packages/rag/query.py:335]
- [x] [Review][Patch] Non-generation DomainError stages can be mislabeled as `generation_stream` [packages/rag/query.py:343]
- [x] [Review][Patch] Malformed provider stream ordering is not rejected [packages/rag/query.py:264]
- [x] [Review][Patch] Reserved `tool_call` and `tool_result` event types have no payload contract [packages/rag/streaming.py:19]
- [x] [Review][Patch] Streaming audit write failures are silently swallowed without safe logging [packages/rag/query.py:585]
- [x] [Review][Defer] Citation `source_uri` can expose local filesystem paths [packages/rag/dto.py:337] — deferred, pre-existing

## Dev Notes

### Current Repository State

- 当前目录不是 git repository；`git log` 返回 `fatal: not a git repository`。最近实现上下文来自 sprint story 文件和源码扫描。
- Story 4.4 已完成非流式 `/query`：
  - `apps/api/routes/query.py` 提供薄路由，已做 `RagQueryContextDep` 权限 gate。
  - `RagQueryApplicationService.query()` 已按 `retrieval -> hydration -> context packing -> prompt build -> LLM generate -> citation extraction` 编排。
  - `RetrievalCandidateHydrator` 已负责按 tenant/document/version/chunk scope 读取正文并 fail-closed 校验。
  - `CitationExtractor` 已只信任当前 `PackedCitationSource`，拒绝 LLM/前端/用户伪造来源。
  - `QueryResponse.metadata` 已建立安全摘要模式，禁止 query/prompt/chunk content/raw provider response。
- `packages/llm` 已有 streaming DTO：
  - `GenerateChunk(delta, index, is_final, response, metadata)`。
  - final chunk 必须携带 `GenerateResponse`。
  - `FakeLLMProvider.stream()` 已按响应文本产出 token chunks 和 final chunk。
  - `RagGenerationService.stream()` 已校验 chunk/response identity。
- 当前没有 `/chat` route，也没有 `packages/rag/streaming.py`。本故事应优先完成 `/query/stream`，为后续 `/chat` 复用相同 event pipeline。

### Files Likely To Touch

- NEW or UPDATE `packages/rag/streaming.py`：RAG stream event DTO helper、SSE frame formatter、safe error payload builder。
- UPDATE `packages/rag/dto.py`：新增 streaming event DTO / stream trace / event count metadata，或把 DTO 放入 `streaming.py` 后从 `__init__.py` 导出。
- UPDATE `packages/rag/query.py`：新增 `stream_query()`，抽取 query preparation helper，增加 streaming audit。
- UPDATE `packages/rag/exceptions.py`：必要时新增 `RAG_STREAM_FAILED`、`RAG_STREAM_INVALID_EVENT` 等稳定错误码。
- UPDATE `packages/rag/__init__.py`：导出 streaming DTO/service/helper。
- UPDATE `apps/api/routes/query.py`：新增 `POST /query/stream` 薄路由。
- UPDATE `apps/api/service_dependencies.py`：通常只需保留现有 service 装配；如新增单独 streaming service 才增加 dependency。
- NEW `tests/unit/rag/test_streaming.py` and/or `tests/unit/rag/test_query_streaming_service.py`。
- UPDATE or NEW `tests/integration/api/test_query_routes.py` / `test_query_stream_routes.py`。
- UPDATE `tests/unit/test_architecture_boundaries.py`。
- UPDATE `README.md` and `docs/operations/local-development.md`。

### Existing Patterns To Reuse

- `/query` route pattern: `apps/api/routes/query.py` already has `RagQueryContextDep` and `QueryRequestBody` handling. Streaming route should reuse this auth gate.
- Service dependency pattern: `apps/api/service_dependencies.py` already wires retrieval, hydrator, context packer, prompt builder, generation service, citation extractor and audit behind ports.
- Non-streaming safe metadata: `packages/rag/query.py::_response_metadata()` and audit helpers already define the safe summary shape. Streaming final metadata should match it instead of inventing a second vocabulary.
- Generation stream validation: `RagGenerationService.stream()` already validates provider chunk identity. Do not bypass it by calling `LLMProvider.stream()` directly in application or route code.
- Fake provider: `FakeLLMProvider.stream()` is deterministic; use it for local and tests.
- Error response redaction: existing `apps/api/error_handlers.py` handles pre-stream JSON errors. Once streaming starts, use SSE `error` event instead of JSON envelope.

### Architecture Requirements

- Story layer: RAG Application Service + API Layer adapter.
- `packages/rag/streaming.py` must be framework-free if possible. It can format SSE strings without importing FastAPI or Starlette.
- `apps/api/routes/query.py` may import `StreamingResponse` from FastAPI/Starlette because it is API layer code.
- Route must not import provider adapters, vector store adapters, storage repositories, SQLAlchemy, or prompt/generation internals.
- Streaming and non-streaming paths must share:

```text
AuthContext
 -> retrieval with tenant/ACL filters
 -> hydration fail-closed checks
 -> context packing
 -> prompt build
 -> LLMProvider abstraction
 -> citation extraction
 -> audit/log safe summary
```

- Permissions stay in backend policy and retrieval/context layers. No prompt, frontend, or LLM decision may widen access.

### Critical Implementation Guardrails

- Do not implement `/chat`, chat session persistence, Open WebUI adapter, Source Inspector `/sources/resolve`, real provider adapters, Tool Registry, Agent events, eval runner, or custom frontend in this story.
- Do not add `sse-starlette` unless there is a concrete reason. Current stack can use FastAPI/Starlette streaming response with a local SSE formatter.
- Do not stream chunk content as citation body. Citation event carries metadata only; source detail remains future `/sources/resolve`.
- Do not log token text, generated answer text, prompt, full query, chunk content, provider raw response, SQL, local absolute paths, secrets, or auth headers.
- Do not trust citation markers or source IDs emitted by the model. Citation events come from packed context/extractor only.
- Do not swallow expected provider stream errors. Convert them to structured `error` event and audit failure/terminal status.
- Do not create a second RAG chain for streaming. Reuse the same orchestrated boundaries as `/query`.
- Do not let client disconnect handling expose partial sensitive payloads in logs. If cancellation is handled, audit only safe counts and status.

### Suggested Event Shape

```json
{
  "request_id": "req-1",
  "trace_id": "trace-1",
  "event": "token",
  "index": 0,
  "delta": "基于"
}
```

```json
{
  "request_id": "req-1",
  "trace_id": "trace-1",
  "event": "citation",
  "citation": {
    "document_id": "doc-1",
    "version_id": "v1",
    "chunk_id": "chunk-1",
    "source": "policy.md",
    "source_uri": "kb://policy.md",
    "source_type": "markdown",
    "page_start": 1,
    "page_end": 1,
    "title_path": ["Policy"],
    "retrieval_method": "hybrid",
    "score": 0.91
  }
}
```

```json
{
  "request_id": "req-1",
  "trace_id": "trace-1",
  "event": "error",
  "code": "LLM_STREAM_FAILED",
  "message": "LLM stream failed.",
  "details": {"stage": "generation_stream"},
  "terminal": true
}
```

```json
{
  "request_id": "req-1",
  "trace_id": "trace-1",
  "event": "final",
  "status": "success",
  "answer": "基于上下文的回答。",
  "citations": [],
  "no_answer": false,
  "unsupported_claims": [],
  "metadata": {
    "retrieval": {"top_k": 5, "result_count": 1},
    "generation": {"provider": "fake", "model": "fake-llm"}
  }
}
```

SSE frame format should be:

```text
event: token
data: {"request_id":"req-1","trace_id":"trace-1","event":"token","index":0,"delta":"基于"}

```

### Previous Story Intelligence

- Story 4.4 review fixed a potential citation fail-open issue. Streaming must preserve the same citation allowlist semantics and not treat empty `citation_source_ids` as "allow all" unless that is explicitly produced by the existing extractor contract.
- Story 4.4 added route-level RBAC for `/query`; `/query/stream` must use the same gate before service invocation.
- Story 4.4 fixed audit persistence with `SqlAlchemyAuditPort(auto_commit=True)` in the API path. Streaming audit must not be lost when the generator closes.
- Story 4.4 fixed hydration errors to avoid exposing document/version/chunk IDs for unauthorized or missing sources. Streaming error events must preserve that indistinguishable shape.
- Story 4.3 already validates LLM provider request/response identity. Do not bypass `RagGenerationService.stream()`.
- Story 4.2 already prevents route-side prompt building. Do not add prompt text assembly to `apps/api/routes/query.py`.

### UX / Product Notes

- Knowledge Chat uses streaming answer with pending citation state. Citation event may arrive before or during token rendering; `final` locks the citation list.
- `Copy answer with citations` should only be enabled after final event; backend must provide final metadata for that future UI behavior.
- No-answer is a valid final state, not an error. It should be sent as final event with `no_answer=true` and no forged citations.
- Source Inspector remains future work. This story returns identifiers and metadata only.
- Tool events are reserved for Agent stories. Do not simulate tool_call/tool_result from RAG generation.

### Latest Technical Information

- Current repo pins `fastapi[standard]>=0.136.3,<0.137`; no new dependency is required for SSE.
- FastAPI custom response docs and Starlette response docs support returning a streaming response from an iterator/generator. Use `text/event-stream` for SSE.
- MDN SSE documentation confirms browser `EventSource` consumes line-delimited event streams using `event:` and `data:` fields separated by blank lines.
- Keep implementation dependency-light: a small local formatter is sufficient for MVP; adding `sse-starlette` should require a documented reason and tests.

### References

- `_bmad-output/planning-artifacts/epics.md#Story-4.5-SSE-Streaming-回答事件`
- `_bmad-output/planning-artifacts/epics.md#Epic-4-可信-RAG-问答-Citation-与流式会话`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-17-SSE-Streaming`
- `_bmad-output/planning-artifacts/architecture.md#API-Communication-Patterns`
- `_bmad-output/planning-artifacts/architecture.md#Communication-Patterns`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md#Component-Patterns`
- `project-context.md`
- `_bmad-output/implementation-artifacts/4-4-citation-extraction-与-query-问答.md`
- `packages/rag/query.py`
- `packages/rag/generation.py`
- `packages/llm/dto.py`
- `packages/llm/adapters/fake.py`
- `apps/api/routes/query.py`
- `apps/api/service_dependencies.py`
- `tests/unit/rag/test_query_service.py`
- `tests/integration/api/test_query_routes.py`
- `https://fastapi.tiangolo.com/advanced/custom-response/`
- `https://www.starlette.io/responses/`
- `https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events`

## Validation Checklist

Validation Result: PASS（2026-06-07T17:01:37+08:00）

- [x] Story 明确了角色、目标和收益。
- [x] Acceptance Criteria 覆盖 token、citation、error、final、route 边界、非流式兼容、audit/log、tests 和 docs。
- [x] Tasks 拆分到具体模块和文件，且限定不实现 `/chat`、memory、Open WebUI adapter、Agent、真实 provider。
- [x] Dev Notes 记录当前源码状态：4.4 非流式 `/query`、`RagGenerationService.stream()`、`FakeLLMProvider.stream()` 已存在。
- [x] 明确复用 retrieval、hydration、context packing、prompt build、generation、citation extraction，而不是复制 RAG 链路。
- [x] 明确 SSE event payload 的安全字段和禁止泄露内容。
- [x] 明确 route 不拼 prompt、不调用 LLM/vector store/storage。
- [x] 明确测试必须使用 fake provider，不调用外部 LLM 或网络。

## Change Log

- 2026-06-07: Created comprehensive Story 4.5 developer context for SSE streaming events, `/query/stream`, safe event payloads, streaming audit, tests and docs.
- 2026-06-07: Implemented `/query/stream` SSE streaming events, safe stream metadata/audit, tests, and local documentation.

## Dev Agent Record

### Agent Model Used

Codex (GPT-5)

### Debug Log References

- `.venv\Scripts\python.exe -m pytest tests/unit/rag/test_streaming.py -q` -> 5 passed
- `.venv\Scripts\python.exe -m pytest tests/unit/rag/test_query_service.py tests/unit/rag/test_streaming.py -q` -> 15 passed
- `.venv\Scripts\python.exe -m pytest tests/integration/api/test_query_routes.py -q` -> 15 passed
- `.venv\Scripts\python.exe -m pytest tests/unit/test_architecture_boundaries.py -q` -> 9 passed
- `.venv\Scripts\python.exe -m pytest tests/unit/rag tests/unit/llm tests/unit/common -q` -> 97 passed
- `.venv\Scripts\python.exe -m ruff check .` -> passed
- `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed
- `.venv\Scripts\python.exe -m pytest -q` -> 518 passed
- 2026-06-07T18:08:11+08:00 review fixes: `.venv\Scripts\python.exe -m pytest tests/unit/rag tests/unit/llm tests/unit/common -q` -> 102 passed
- 2026-06-07T18:08:11+08:00 review fixes: `.venv\Scripts\python.exe -m pytest tests/integration/api/test_query_routes.py -q` -> 15 passed
- 2026-06-07T18:08:11+08:00 review fixes: `.venv\Scripts\python.exe -m pytest tests/unit/test_architecture_boundaries.py -q` -> 9 passed
- 2026-06-07T18:08:11+08:00 review fixes: `.venv\Scripts\python.exe -m ruff check .` -> passed
- 2026-06-07T18:08:11+08:00 review fixes: `.venv\Scripts\python.exe -m mypy apps packages tests` -> no issues in 200 source files
- 2026-06-07T18:08:11+08:00 review fixes: `.venv\Scripts\python.exe -m pytest -q` -> 523 passed

### Completion Notes List

- Implemented framework-free RAG streaming DTOs and SSE formatter in `packages/rag/streaming.py`.
- Refactored query preparation into `_prepare_query_context()` so `/query` and `/query/stream` share retrieval, hydration, context packing, and prompt build.
- Added `RagQueryApplicationService.stream_query()` with citation-before-token streaming, token events from `RagGenerationService.stream()`, provider-final answer handling, structured error/final terminal events, and safe `rag.query.stream` audit metadata.
- Added thin `POST /query/stream` API route using `RagQueryContextDep`, `StreamingResponse`, `X-Request-ID`, `Cache-Control: no-cache`, and the shared SSE formatter.
- Used a safe event-count summary shape (`[{event, count}]`) instead of a raw `{"token": n}` mapping so global redaction does not confuse event names with auth tokens.
- Updated README and local development docs with SSE event types, curl example, fake provider behavior, tests, and remaining out-of-scope work.
- Review fixes hardened streaming error detail allowlisting, provider failure metadata, client disconnect audit, malformed provider stream ordering, reserved tool event payload DTOs, and safe audit-failure logging.

### File List

- `apps/api/routes/query.py`
- `docs/operations/local-development.md`
- `packages/rag/__init__.py`
- `packages/rag/query.py`
- `packages/rag/streaming.py`
- `README.md`
- `tests/integration/api/test_query_routes.py`
- `tests/unit/rag/test_query_service.py`
- `tests/unit/rag/test_streaming.py`
- `tests/unit/test_architecture_boundaries.py`
- `_bmad-output/implementation-artifacts/4-5-sse-streaming-回答事件.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

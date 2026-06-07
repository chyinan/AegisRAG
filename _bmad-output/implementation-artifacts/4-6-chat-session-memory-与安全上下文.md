---
baseline_commit: NO_VCS
---

# Story 4.6: Chat Session Memory 与安全上下文

Status: done

生成时间：2026-06-07T18:15:58+08:00

## Story

As a 企业员工,
I want 多轮会话记住必要历史,
so that 我可以围绕同一授权知识主题连续追问。

## Acceptance Criteria

1. **新增 chat memory 持久化 schema**
   - Given Alembic migration 执行
   - When `chat_sessions` 和 `chat_messages` 首次引入
   - Then 两张表都包含 `id`、`created_at`、`updated_at`、`tenant_id`、`user_id`、`created_by`、`status`
   - And `chat_sessions` 至少包含 `request_id`、`trace_id`、`title`、`last_message_at`、`message_count`、`metadata`
   - And `chat_messages` 至少包含 `session_id`、`request_id`、`trace_id`、`role`、`content`、`content_summary`、`token_count`、`sequence_no`、`metadata`
   - And `chat_messages.session_id` 外键指向 `chat_sessions.id`
   - And 支持按 `tenant_id + user_id + session_id` 查询会话，按 `tenant_id + session_id + sequence_no/created_at` 查询消息
   - And 不允许用全局变量、进程内 dict、Redis-only cache 保存权威会话历史

2. **新增 memory 模块边界和 DTO**
   - Given 当前仓库没有 `packages/memory`
   - When 实现 chat memory
   - Then 新增 `packages/memory/dto.py`、`packages/memory/exceptions.py`、`packages/memory/service.py`、`packages/memory/storage/models.py`、`packages/memory/storage/repositories.py`
   - And DTO 使用 Pydantic v2 frozen model 或 dataclass，字段带 type hints
   - And storage model 不直接传入 RAG、prompt builder 或 API route
   - And repository 只按显式 `tenant_id`、`user_id`、`session_id` 读取，不提供跨租户或跨用户宽查询

3. **Chat session 创建和归属校验**
   - Given 用户调用 `POST /chat` 或 `POST /chat/stream` 且未提供有效 `session_id`
   - When application service 处理请求
   - Then 创建新的 active chat session，并返回 `session_id`
   - And session 记录当前 `tenant_id`、`user_id`、`created_by`、`request_id`、`trace_id`
   - And session title 只能来自安全摘要或短 query 摘要，不保存完整敏感问题到 title
   - Given 用户提供 `session_id`
   - When memory service 加载会话
   - Then 必须同时匹配 `tenant_id`、`user_id`、active status
   - And 跨 tenant、跨 user、deleted/closed session 返回同一类 `CHAT_SESSION_NOT_FOUND` 或安全权限错误，不暴露会话是否存在

4. **Chat message 持久化和内容治理**
   - Given chat 请求进入 application service
   - When 用户消息通过 validation
   - Then 持久化 user message，包含 `request_id`、`trace_id`、`tenant_id`、`user_id`、`session_id`、`role="user"`、`sequence_no`、`token_count`、`content_summary`
   - And `content_summary` 不包含完整 query、prompt、chunk content、provider raw response、secret、token、本地绝对路径或 SQL
   - When RAG 回答成功或安全无答案
   - Then 持久化 assistant message，包含最终 answer 的安全存储策略和 `citations` metadata 摘要
   - And 如果项目选择保存 assistant 完整 answer，必须在 story completion notes 中说明原因，并确保 audit/log 不保存全文
   - And streaming 请求只能在 final event 到达后持久化 assistant message；partial token 不得逐 token 写入数据库

5. **会话历史进入 prompt 前经过预算和安全过滤**
   - Given session 内已有历史消息
   - When `/chat` 或 `/chat/stream` 构造 RAG prompt
   - Then memory service 只返回当前 tenant/user/session 下最近且 active 的消息
   - And history 经过独立 token/char budget、role allowlist、消息数量限制和安全摘要过滤
   - And 历史消息在 PromptBuilder 中作为 untrusted conversation history 进入 prompt，不得成为 system/developer 指令
   - And 历史只能帮助理解追问，不得扩大 retrieval 的 tenant、RBAC、ACL、metadata filter 或 knowledge scope
   - And 如果历史预算超限，必须 deterministic 截断或 drop，并在 metadata/audit 中记录 `memory_message_count`、`memory_used_count`、`memory_dropped_count`

6. **Chat RAG 编排复用 4.4/4.5 query 链路**
   - Given 当前 `RagQueryApplicationService` 已实现非流式和 streaming RAG 编排
   - When 实现 chat application service
   - Then 不得复制一套 `retrieval -> hydration -> context packing -> prompt build -> generation -> citation extraction` 链路
   - And 应通过扩展 `RagQueryApplicationService.query()` / `stream_query()` 的可选 memory context，或抽取共享 preparation/generation helper 复用既有逻辑
   - And `/query` 和 `/query/stream` 的 request/response contract、权限、audit、安全 metadata 和 tests 不得被破坏
   - And `context.session_id` 必须传入 `RagGenerationService`，保持 `GenerateRequest.session_id` 与当前 chat session 一致

7. **新增薄 `/chat` 和 `/chat/stream` API route**
   - Given FastAPI route 处理 chat 请求
   - When 调用方使用 `POST /chat`
   - Then 返回统一 envelope，data 至少包含 `session_id`、`answer`、`citations`、`no_answer`、`unsupported_claims`、`metadata`
   - When 调用方使用 `POST /chat/stream`
   - Then 返回 `text/event-stream`，复用 4.5 SSE formatter，事件类型至少包含 `citation`、`token`、`error`、`final`
   - And final event payload 包含 `session_id`、`tenant_id`、`user_id`、answer、citations、safe metadata
   - And route 只解析 schema、注入 `AuthenticatedRequestContext`、调用 chat application service、返回 response
   - And route 不拼 prompt、不读写数据库、不直接调用 retrieval、vector store、LLM provider 或 memory repository

8. **权限 gate 和错误语义**
   - Given 用户缺少 `document:read` 或 `retrieval:query` 权限
   - When 调用 `/chat` 或 `/chat/stream`
   - Then 在调用 chat service 前返回结构化权限错误，service 不被调用
   - Given memory service 读取 session/message 失败
   - When 错误是跨租户、跨用户、不存在、deleted、closed 或 storage expected error
   - Then API/SSE error 使用稳定 code 和安全 details
   - And 不泄露未授权 session 是否存在、历史消息数量、完整消息内容、query、prompt、chunk content、provider raw payload、本地路径或 SQL

9. **审计和日志覆盖 chat 生命周期**
   - Given chat 请求成功、no-answer、权限拒绝、memory read failure、RAG failure、provider stream failure 或客户端断开
   - When application service 完成或捕获 expected error
   - Then 记录 `rag.chat` 或 `rag.chat.stream` 审计事件
   - And metadata 包含 request_id、trace_id、tenant_id、user_id、session_id、latency、top_k/result_count、memory_message_count、memory_used_count、memory_dropped_count、context item/source counts、provider/model/version、token usage、citation_count、unsupported_count、event counts、error_code
   - And 不记录用户消息全文、assistant answer 全文、prompt、chunk content、provider raw response、API key、access token、本地绝对路径或 SQL

10. **测试覆盖 storage、service、API 和安全边界**
    - Given 单元测试运行
    - When 验证 memory DTO/service/prompt integration/chat service
    - Then 覆盖 session 创建、session 归属校验、跨 tenant/user 拒绝、deleted/closed session 拒绝、history budget、role allowlist、safe summary redaction、message sequence
    - And 覆盖 `/chat` 复用 RAG query service，不改变 `/query` 非流式和 streaming 行为
    - And 覆盖 `/chat/stream` 正常顺序、error event、final terminal state、assistant message 只在 final 后持久化
    - And 覆盖 route 权限拒绝不调用 service
    - And 使用 fake provider / fake retriever / sqlite or repository mock，不真实调用 OpenAI/Qwen/DeepSeek/Ollama/vLLM、网络、Docker、Redis、MinIO 或生产数据库

11. **文档更新**
    - Given story 完成
    - When 阅读 `README.md#RAG Foundation` 和 `docs/operations/local-development.md`
    - Then 文档说明 `/chat`、`/chat/stream`、`session_id` 使用方式、SSE final 中的 session 信息、本地 fake provider 行为和测试命令
    - And 明确 Open WebUI adapter、Source Inspector `/sources/resolve`、真实 provider adapter、Agent tool events、conversation summarization LLM 和 RAG eval 扩展仍属后续 story

## Tasks / Subtasks

- [x] 定义 memory DTO、异常和预算配置（AC: 2, 3, 4, 5, 8）
  - [x] 新增 `packages/memory/__init__.py`、`dto.py`、`exceptions.py`、`service.py`。
  - [x] 定义 `ChatSessionRecord`、`ChatMessageRecord`、`ChatSessionCreate`、`ChatMessageCreate`、`ChatHistoryMessage`、`PackedChatHistory`、`ChatMemoryConfig`。
  - [x] 定义稳定错误码：`CHAT_SESSION_NOT_FOUND`、`CHAT_MEMORY_FORBIDDEN`、`CHAT_MEMORY_STORAGE_FAILED`、`CHAT_MEMORY_INVALID_REQUEST`、`CHAT_MEMORY_BUDGET_EXCEEDED` 或等价集合。
  - [x] DTO validator 必须拒绝空 `tenant_id`、`user_id`、`session_id`、非法 role、负 token_count、过长 content。
  - [x] `ChatMemoryConfig` 默认限制建议：最近 10 条消息、history budget 800 到 1200 tokens、单条消息 content 4000 chars 上限；实际值可来自 config 后续扩展，当前不要硬编码到 prompt 文案。

- [x] 新增 storage model、repository 和 Alembic migration（AC: 1, 2, 3, 4）
  - [x] 新增 `packages/memory/storage/models.py` 和 `repositories.py`，使用 `packages.data.storage.base.Base`、`IdMixin`、`TimestampMixin`。
  - [x] Migration 编号接在 `20260527_0008_retrieval_logs.py` 后，例如 `20260527_0009_chat_memory.py`。
  - [x] `chat_sessions.status` 至少支持 `active`、`closed`、`deleted`；`chat_messages.status` 至少支持 `active`、`deleted`。
  - [x] `chat_messages.role` 只允许 `user`、`assistant`、`system_summary` 中的实现所需集合；不要保存 tool 角色，Agent tool events 属 Epic 6。
  - [x] 增加唯一约束或 repository 逻辑保证同一 session 内 `sequence_no` 单调不重复。
  - [x] 增加 indexes：`tenant_id/user_id/id`、`tenant_id/user_id/status`、`tenant_id/session_id/sequence_no`、`tenant_id/session_id/created_at`。
  - [x] repository 捕获 SQLAlchemyError 后 rollback，并转成 memory/storage 领域异常；错误 details 只包含 request/trace/session 安全字段。

- [x] 实现 ChatMemoryService（AC: 3, 4, 5, 8, 9）
  - [x] `get_or_create_session(context, session_id)`：无 session_id 创建，有 session_id 则按 tenant/user/status 验证。
  - [x] `append_user_message(...)`：保存用户消息和安全摘要，分配 sequence_no。
  - [x] `append_assistant_message(...)`：只在 RAG final 成功、no-answer 或安全 error final 后保存 assistant 结果。
  - [x] `load_packed_history(...)`：按当前 session 加载最近消息，按预算、role allowlist 和 content safety 生成 `PackedChatHistory`。
  - [x] 历史消息作为 untrusted data，不得输出 prompt 指令、权限变更或工具调用意图。
  - [x] service 不依赖 FastAPI、Starlette、LLM provider SDK、vector store adapter 或 route 类型。

- [x] 扩展 PromptBuilder 支持受限 chat history（AC: 5, 6）
  - [x] 在 `packages/rag/dto.py` 新增 prompt history DTO，例如 `PromptHistoryMessage` / `PromptMemoryContext`，或从 `packages/memory.dto` 引入纯 DTO，避免循环依赖。
  - [x] 扩展 `PromptBuildRequest` 可选 `memory_context`，默认 `None`，确保 `/query` 现有 tests 不变。
  - [x] 在 `packages/rag/prompt_builder.py` 将 history 作为 `<conversation_history untrusted_content="true">` 或等价结构加入 user/context message。
  - [x] PromptBuilder 的 risk detection 应扫描 history 内容，但 error details 不能泄露 history 原文。
  - [x] prompt trace/metadata 增加 safe counts：`memory_message_count`、`memory_used_count`、`memory_dropped_count`、`memory_token_count`。
  - [x] 不允许历史消息覆盖 system/security/citation/no-answer policy。

- [x] 复用并扩展 RagQueryApplicationService（AC: 5, 6, 9）
  - [x] 给 `query()` / `stream_query()` 添加可选 memory context 参数，默认值保持 `/query` 兼容。
  - [x] `_prepare_query_context()` 将 memory context 传入 `PromptBuildRequest`。
  - [x] `_response_metadata()` 和 `_stream_response_metadata()` 增加 memory safe counts，默认 0。
  - [x] `RagGenerationService` 已传 `context.session_id` 到 `GenerateRequest.session_id`，只需确保 chat route/context 使用当前 session id。
  - [x] 不要在 `apps/api/routes/chat.py` 中调用 `PromptBuilder`、`RagGenerationService`、retrieval service 或 repository。

- [x] 实现 ChatApplicationService（AC: 3, 4, 5, 6, 8, 9）
  - [x] 推荐新增 `packages/memory/chat.py` 或 `packages/rag/chat.py`；若放 `packages/rag`，memory storage 仍在 `packages/memory`。
  - [x] 非流式流程：权限校验由 route 先做，service 再防御性校验；get/create session -> append user message -> load packed history -> call `rag_query_service.query(..., memory_context=...)` -> append assistant message -> audit `rag.chat`。
  - [x] 流式流程：get/create session -> append user message -> load packed history -> call `rag_query_service.stream_query(..., memory_context=...)` -> pass through SSE events -> on final append assistant message -> audit `rag.chat.stream`。
  - [x] streaming error terminal 时保存 assistant message 的策略必须明确：可以保存安全 no-answer/error summary，不保存 partial token。
  - [x] 客户端断开要审计 safe counts；不要因 audit 失败让已开始的 stream 变成业务失败。
  - [x] Chat service 返回/事件 final 中必须包含 `session_id`。

- [x] 新增 API schema 和 route（AC: 7, 8）
  - [x] 新增 `apps/api/routes/chat.py`。
  - [x] 定义 `ChatRequestBody`：`query`、`session_id` optional、`top_k`、`metadata_filter`、`score_threshold`、`answer_style`、`max_output_tokens`；可复用 `QueryCommand` validation，但不要暴露 memory internals。
  - [x] 定义 `ChatResponse` 或 `ChatQueryResponse`，包含 `session_id` 和 RAG response fields。
  - [x] `POST /chat` 返回 `ApiResponse[ChatResponse]`。
  - [x] `POST /chat/stream` 返回 `StreamingResponse`，使用 `format_sse_event()`；如需要 final 加 `session_id`，优先通过 event payload DTO 扩展而不是拼 JSON 字符串。
  - [x] 在 `apps/api/main.py` include chat router。
  - [x] 在 `apps/api/service_dependencies.py` 装配 `ChatApplicationService`、`ChatMemoryRepository`、`ChatMemoryService`，并复用现有 `RagQueryApplicationService` wiring。

- [x] 审计、日志和 redaction（AC: 4, 8, 9）
  - [x] 新增 audit action `rag.chat`、`rag.chat.stream`。
  - [x] audit resource type 建议为 `chat_session`，id 为 session_id。
  - [x] metadata 只记录 safe counts、model/provider、latency、citation_count、event_counts、error_code。
  - [x] 扩展或复用 `packages.common.logging.redact_mapping`，确保 message content、answer、prompt、chunk content、provider raw response、token、local path 被 redacted。
  - [x] expected errors details 不包含完整 session history 或消息正文。

- [x] 单元测试（AC: 1-10）
  - [x] 新增 `tests/unit/memory/test_dto.py` 覆盖 DTO validation。
  - [x] 新增 `tests/unit/memory/test_service.py` 覆盖 session 创建、归属校验、history packing、budget/drop、summary redaction。
  - [x] 新增或扩展 `tests/unit/rag/test_prompt_builder.py` 覆盖 conversation history untrusted 注入、risk detection、安全 trace。
  - [x] 扩展 `tests/unit/rag/test_query_service.py` 覆盖 memory context metadata、`/query` 默认不带 memory 的兼容性。
  - [x] 新增 `tests/unit/memory/test_chat_application_service.py` 覆盖非流式和 streaming chat 编排、assistant message final 后持久化、error terminal 行为。
  - [x] 测试不得调用真实外部 LLM、网络、Docker、Redis、MinIO 或生产数据库。

- [x] storage 和 API 集成测试（AC: 1, 3, 4, 7, 8, 10）
  - [x] 新增 `tests/integration/storage/test_chat_memory_repositories.py`，使用 sqlite async engine 创建 Base metadata，验证 CRUD、索引查询路径、跨 tenant/user 返回 None 或安全错误。
  - [x] 新增 `tests/integration/api/test_chat_routes.py`，使用 service dependency override 验证 `/chat` 和 `/chat/stream`。
  - [x] 覆盖 missing auth、missing permission、invalid body 不调用 service。
  - [x] 覆盖 stream 正常 frames 中包含 `session_id`，error/final terminal 安全。
  - [x] 覆盖 `/query` 和 `/query/stream` 现有集成测试仍通过。

- [x] 架构边界测试（AC: 2, 6, 7, 10）
  - [x] 扩展 `tests/unit/test_architecture_boundaries.py`，确保 `apps/api/routes/chat.py` 不导入 storage、provider adapter、SQLAlchemy、retrieval internals。
  - [x] 确保 `packages/memory/service.py` 不导入 FastAPI/Starlette、provider SDK、vector store adapter。
  - [x] 确保 `packages/memory/storage/*` 是唯一允许导入 SQLAlchemy 的 memory 子层。
  - [x] 确保 `packages/rag/prompt_builder.py` 仍不导入 FastAPI、SQLAlchemy、provider SDK。

- [x] 文档和验证（AC: 10, 11）
  - [x] 更新 `README.md#RAG Foundation`，标记 Story 4.6 `/chat`、session memory 和安全上下文状态。
  - [x] 更新 `docs/operations/local-development.md`，添加 `/chat` 与 `/chat/stream` curl 示例，说明 `X-Session-ID` 或 body `session_id` 使用方式。
  - [x] 明确 out of scope：Open WebUI adapter、Source Inspector、真实 provider、Agent tool events、LLM summarization、RAG eval 扩展。
  - [x] 运行 `.venv\Scripts\python.exe -m pytest tests/unit/memory tests/unit/rag -q`。
  - [x] 运行 `.venv\Scripts\python.exe -m pytest tests/integration/api/test_chat_routes.py tests/integration/api/test_query_routes.py -q`。
  - [x] 运行 `.venv\Scripts\python.exe -m pytest tests/integration/storage/test_chat_memory_repositories.py -q`。
  - [x] 运行 `.venv\Scripts\python.exe -m pytest tests/unit/test_architecture_boundaries.py -q`。
  - [x] 运行 `.venv\Scripts\python.exe -m ruff check .`。
  - [x] 运行 `.venv\Scripts\python.exe -m mypy apps packages tests`。
  - [x] 如成本可接受，运行 `.venv\Scripts\python.exe -m pytest -q`。

### Review Findings

- [x] [Review][Patch] Permission gate accepts either `document:read` or `retrieval:query`, but AC8 requires rejecting when either is missing [packages/auth/policies.py:82]
- [x] [Review][Patch] `ChatApplicationService` creates/loads a session and appends the user message before any defensive service-level permission check [packages/rag/chat.py:62]
- [x] [Review][Patch] Current user message is appended before history packing, so the prompt sees the current query both as `user_question` and prior `conversation_history` [packages/rag/chat.py:68]
- [x] [Review][Patch] User `content_summary` can persist the full query when it is below the summary limit, violating AC4's "not complete query" rule [packages/memory/service.py:121]
- [x] [Review][Patch] Assistant full answer is persisted without the AC4-required completion-note rationale for choosing full-answer storage [packages/memory/service.py:155]
- [x] [Review][Patch] Chat message `sequence_no` uses `max(sequence_no) + 1` without locking or a unique constraint, so concurrent appends can duplicate sequence numbers [packages/memory/storage/repositories.py:91]
- [x] [Review][Patch] Chat memory persistence relies on later audit auto-commit, and audit failure is suppressed, so a successful response can lose flushed chat messages [packages/rag/chat.py:83]
- [x] [Review][Patch] `rag.chat` / `rag.chat.stream` audit is only written on success/final events and misses memory failures, RAG failures, provider failures, and client disconnects required by AC9 [packages/rag/chat.py:94]
- [x] [Review][Patch] Chat audit metadata omits required lifecycle fields such as request_id, trace_id, tenant_id, user_id, latency, and stream event counts [packages/rag/chat.py:262]
- [x] [Review][Patch] `/chat/stream` memory setup or assistant persistence failures can break the stream instead of emitting documented SSE `error` and terminal `final` events [apps/api/routes/chat.py:37]
- [x] [Review][Patch] Long-session history drop accounting ignores messages omitted by `list_recent_messages(limit=max_messages)`, underreporting `memory_dropped_count` [packages/memory/service.py:182]

## Dev Notes

### Current Repository State

- 当前目录不是 git repository；`git log --oneline -5` 返回 `fatal: not a git repository`。最近实现上下文来自 sprint story 文件和源码扫描。
- `packages/memory` 当前不存在，本故事需要新增该 package。
- `apps/api/routes/chat.py` 当前不存在；`apps/api/main.py` 目前只 include health、upload、documents、retrieve、query routers。
- `packages/common/context.py` 已有 `RequestContext.session_id`，`apps/api/dependencies.py` 已从 `X-Session-ID` 读取 session id。
- `packages/llm/dto.py::GenerateRequest` 已有 `session_id`；`packages/rag/generation.py::RagGenerationService._request()` 已把 `context.session_id` 传给 provider request。
- `packages/rag/dto.py::PromptBuildRequest` 已有 `session_id`，但没有 conversation history/memory context 字段。
- `packages/rag/prompt_builder.py` 已建立 system/security/citation/no-answer policy，并把 query/context 标记为 untrusted data。新增 history 必须沿用同一安全模型。
- `packages/rag/query.py::RagQueryApplicationService` 已实现 `query()` 和 `stream_query()`，并复用 `_prepare_query_context()` 进行 retrieval、hydration、context packing、prompt build。
- `apps/api/routes/query.py` 已提供薄 `POST /query` 和 `POST /query/stream`；权限 gate 是 `RagQueryContextDep`，缺少权限时 route 前置拒绝。
- `apps/api/service_dependencies.py` 目前每次装配 `RagQueryApplicationService` 时创建 retrieval、hydrator、context packer、prompt builder、generation service、citation extractor、audit。
- `packages/data/storage/base.py` 提供 `Base`、`IdMixin`、`TimestampMixin`；chat memory storage model 应复用它们，避免新建 declarative base。

### Previous Story Intelligence

- Story 4.5 已完成 `/query/stream`，包括 framework-free `packages/rag/streaming.py`、token/citation/error/final DTO、SSE formatter、安全 stream audit 和 route 集成。
- Story 4.5 review 修复了 streaming error detail allowlist、provider failure metadata、client disconnect audit、malformed provider stream ordering和 reserved tool event payload DTO。
- Story 4.5 明确 `/chat`、chat session memory、Open WebUI adapter、Source Inspector `/sources/resolve`、真实 provider adapter、Agent tool events 和 RAG eval 属后续 story。本故事只接 `/chat` 与 chat memory，不实现 Open WebUI adapter 或 Source Inspector。
- Story 4.4 已完成非流式 `/query`、citation extraction、no-answer、安全 metadata 和 route-level RBAC。Chat 必须继承这些边界，不允许绕开 CitationExtractor 或让前端/LLM 补造 citation。
- Story 4.2 已完成 prompt injection 防护。Conversation history 是用户/assistant 历史数据，仍然是不可信内容，不能变成系统指令。

### Files Likely To Touch

- NEW `packages/memory/__init__.py`
- NEW `packages/memory/dto.py`
- NEW `packages/memory/exceptions.py`
- NEW `packages/memory/service.py`
- NEW `packages/memory/storage/__init__.py`
- NEW `packages/memory/storage/models.py`
- NEW `packages/memory/storage/repositories.py`
- NEW `migrations/versions/20260527_0009_chat_memory.py`
- UPDATE `packages/rag/dto.py`
- UPDATE `packages/rag/prompt_builder.py`
- UPDATE `packages/rag/query.py`
- UPDATE `packages/rag/streaming.py` if final event payload must carry `session_id`
- UPDATE `packages/rag/__init__.py`
- NEW `apps/api/routes/chat.py`
- UPDATE `apps/api/main.py`
- UPDATE `apps/api/service_dependencies.py`
- NEW `tests/unit/memory/test_dto.py`
- NEW `tests/unit/memory/test_service.py`
- NEW `tests/unit/memory/test_chat_application_service.py`
- UPDATE `tests/unit/rag/test_prompt_builder.py`
- UPDATE `tests/unit/rag/test_query_service.py`
- NEW `tests/integration/storage/test_chat_memory_repositories.py`
- NEW `tests/integration/api/test_chat_routes.py`
- UPDATE `tests/integration/api/test_query_routes.py` only if shared route setup requires it
- UPDATE `tests/unit/test_architecture_boundaries.py`
- UPDATE `README.md`
- UPDATE `docs/operations/local-development.md`

### Existing Patterns To Reuse

- Thin route pattern: `apps/api/routes/query.py` parses schema, injects auth context, calls application service, formats response.
- Permission gate: reuse `has_rag_query_permission()` and the same required permissions as `/query`, unless `packages/auth` already has a narrower chat permission.
- Audit pattern: use `packages.common.audit.AuditEvent` and `SqlAlchemyAuditPort(auto_commit=True)` for API path lifecycle safety.
- Storage pattern: `DocumentRepository` converts SQLAlchemy errors into domain/storage exceptions and rolls back on writes.
- Migration pattern: `migrations/versions/20260527_0008_retrieval_logs.py` shows table creation, indexes, check constraints and downgrade cleanup style.
- Prompt safety pattern: `PromptBuilder` uses explicit system/security/citation/no-answer messages and XML-like untrusted blocks; memory history should be a separate untrusted block.
- Streaming pattern: `packages/rag/streaming.py::format_sse_event()` and `apps/api/routes/query.py::query_stream()` already provide the route/formatter split.
- Generation identity pattern: `RagGenerationService` validates request/response identity and already carries session_id.

### Architecture Requirements

- Story layer: Memory Storage + Memory Application Service + RAG Application Service extension + API Layer adapter.
- `packages/memory/service.py` must be framework-free and infrastructure-free except through repository protocols or pure DTOs.
- `packages/memory/storage/*` may import SQLAlchemy; memory DTO/service must not.
- API route may import FastAPI/Starlette response classes, but must not import storage repositories, SQLAlchemy, vector store adapters, LLM adapters or retrieval internals.
- Chat memory cannot become a permission mechanism. Permissions remain AuthContext/RBAC/ACL filters in retrieval and backend policy.
- Chat history cannot override prompt safety rules. It is untrusted conversation data.
- `/query` must continue to work without session memory. Do not force direct query users to create sessions.

### Critical Implementation Guardrails

- Do not implement Open WebUI adapter in this story. `/chat` is the backend primitive it will use later.
- Do not implement Source Inspector `/sources/resolve`; that is story 4.7.
- Do not implement conversation summarization through an LLM provider in this story. Use deterministic truncation/safe summaries for MVP.
- Do not store conversation in globals or Redis-only state. PostgreSQL is source of truth.
- Do not let a provided `session_id` choose tenant, user, department, permissions, metadata filter or ACL.
- Do not use chat history to rerun retrieval across a broader scope than the current request allows.
- Do not log or audit full user messages, assistant answers, prompt, chunk content or provider raw response.
- Do not write assistant streaming tokens one by one to database. Persist only final state or a safe terminal summary.
- Do not create a new RAG chain for chat. Extend or reuse existing query service boundaries.
- Do not add external dependencies for memory unless a concrete repository pattern requires it; current stack already has FastAPI, SQLAlchemy async, Pydantic v2 and pytest.

### Suggested DTO Shape

```python
class ChatRequestBody(BaseModel):
    query: str
    session_id: str | None = None
    top_k: int = 10
    metadata_filter: dict[str, object] = Field(default_factory=dict)
    score_threshold: float | None = None
    answer_style: str | None = None
    max_output_tokens: int | None = None
```

```python
class ChatResponse(BaseModel):
    request_id: str
    trace_id: str
    session_id: str
    tenant_id: str
    user_id: str
    answer: str
    citations: tuple[Citation, ...] = ()
    no_answer: bool = False
    unsupported_claims: tuple[UnsupportedClaim, ...] = ()
    metadata: Mapping[str, object] = Field(default_factory=FrozenDict)
```

```json
{
  "request_id": "req-1",
  "trace_id": "trace-1",
  "event": "final",
  "status": "success",
  "session_id": "session-1",
  "tenant_id": "tenant-1",
  "user_id": "user-1",
  "answer": "基于上下文的回答。",
  "citations": [],
  "no_answer": false,
  "metadata": {
    "memory": {
      "message_count": 4,
      "used_count": 3,
      "dropped_count": 1
    }
  }
}
```

### Latest Technical Information

- Current repo pins `fastapi[standard]>=0.136.3,<0.137`; Starlette `StreamingResponse` already works in Story 4.5, so `/chat/stream` should reuse the same response mechanism.
- Current repo pins `sqlalchemy>=2.0.50,<3` and uses async SQLAlchemy sessions. New repositories should follow existing async session and rollback patterns.
- Current repo pins `pydantic>=2.13.4,<3`; use Pydantic v2 `ConfigDict(frozen=True)` and validators like existing DTOs.
- No new dependency is needed for chat memory. The feature is primarily schema, repository, service orchestration and tests.

### References

- `_bmad-output/planning-artifacts/epics.md#Story-4.6-Chat-Session-Memory-与安全上下文`
- `_bmad-output/planning-artifacts/epics.md#Epic-4-可信-RAG-问答-Citation-与流式会话`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-19-多轮会话记忆`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-22-RBAC-与-ACL-检索过滤`
- `_bmad-output/planning-artifacts/architecture.md#API-&-Communication-Patterns`
- `_bmad-output/planning-artifacts/architecture.md#Project-Structure-&-Boundaries`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md#Component-Patterns`
- `project-context.md`
- `_bmad-output/implementation-artifacts/4-5-sse-streaming-回答事件.md`
- `packages/common/context.py`
- `packages/rag/query.py`
- `packages/rag/dto.py`
- `packages/rag/prompt_builder.py`
- `packages/rag/generation.py`
- `packages/llm/dto.py`
- `packages/rag/streaming.py`
- `apps/api/routes/query.py`
- `apps/api/service_dependencies.py`
- `apps/api/main.py`
- `packages/data/storage/base.py`
- `migrations/versions/20260527_0008_retrieval_logs.py`
- `tests/unit/test_architecture_boundaries.py`
- `tests/integration/api/test_query_routes.py`
- `pyproject.toml`

## Validation Checklist

Validation Result: PASS（2026-06-07T18:15:58+08:00）

- [x] Story 明确了角色、目标和收益。
- [x] Acceptance Criteria 覆盖数据库、memory 模块、session 归属、message 持久化、history budget、prompt 安全、chat API、SSE、审计、测试和文档。
- [x] Tasks 拆分到具体模块和文件，且限定不实现 Open WebUI adapter、Source Inspector、Agent、真实 provider、LLM summarization。
- [x] Dev Notes 记录当前源码状态：无 `packages/memory`、无 `apps/api/routes/chat.py`、已有 `/query` 和 `/query/stream`。
- [x] 明确复用 4.4/4.5 的 RAG query/stream 编排，不复制 RAG 链路。
- [x] 明确 chat history 是 untrusted data，不能扩大 tenant/RBAC/ACL/metadata scope。
- [x] 明确 route 不拼 prompt、不读写数据库、不调用 LLM/vector store/storage。
- [x] 明确测试必须使用 fake provider/mock/sqlite，不调用外部 LLM 或网络。
- [x] 明确审计和日志只能记录 safe metadata，不记录全文或敏感信息。

## Change Log

- 2026-06-07: Created comprehensive Story 4.6 developer context for chat session memory, safe conversation history, `/chat`, `/chat/stream`, persistence, audit, tests and docs.

## Dev Agent Record

### Agent Model Used

Codex GPT-5

### Debug Log References

- `.venv\Scripts\python.exe -m pytest tests\unit\memory\test_dto.py -q`（6 passed）
- `.venv\Scripts\python.exe -m pytest tests\integration\storage\test_chat_memory_repositories.py -q`（3 passed）
- `.venv\Scripts\python.exe -m pytest tests\integration\storage\test_alembic_migrations.py -q`（1 passed）
- `.venv\Scripts\python.exe -m pytest tests\unit\memory\test_service.py tests\unit\memory\test_dto.py -q`（10 passed）
- `.venv\Scripts\python.exe -m pytest tests\unit\rag\test_prompt_builder.py -q`（19 passed）
- `.venv\Scripts\python.exe -m pytest tests\unit\rag\test_query_service.py tests\unit\rag\test_prompt_builder.py -q`（34 passed）
- `.venv\Scripts\python.exe -m pytest tests\unit\memory tests\unit\rag -q`（77 passed）
- `.venv\Scripts\python.exe -m pytest tests\integration\api\test_chat_routes.py tests\integration\api\test_query_routes.py -q`（19 passed）
- `.venv\Scripts\python.exe -m pytest tests\integration\storage\test_chat_memory_repositories.py tests\integration\storage\test_alembic_migrations.py -q`（4 passed）
- `.venv\Scripts\python.exe -m pytest tests\unit\test_architecture_boundaries.py tests\unit\common\test_logging.py -q`（17 passed）
- `.venv\Scripts\python.exe -m pytest tests\unit\common\test_logging.py tests\unit\retrieval\test_dense.py tests\unit\retrieval\test_sparse.py -q`（39 passed）
- `.venv\Scripts\python.exe -m ruff check .`（All checks passed）
- `.venv\Scripts\python.exe -m mypy apps packages tests`（Success: no issues found in 215 source files）
- `.venv\Scripts\python.exe -m pytest -q`（548 passed）

### Completion Notes List

- Implemented frozen memory DTOs, stable chat memory error codes, safe history packing counts, and deterministic content summary redaction foundations without FastAPI, storage, provider, or vector store dependencies.
- Added chat memory SQLAlchemy models, Alembic migration, scoped async repository, monotonic per-session sequence assignment, tenant/user/status isolation, and safe storage error wrapping.
- Extended ChatMemoryService with repository protocol orchestration for get/create session, safe user/assistant persistence DTO creation, safe not-found semantics, and deterministic role/budget history packing.
- Added prompt memory DTOs and untrusted conversation history injection with safe trace/metadata counts and risk scanning.
- Extended RagQueryApplicationService query/stream paths to accept optional memory context, pass it into PromptBuilder, and surface memory safe counts in response/audit metadata while preserving default `/query` behavior.
- Added ChatApplicationService in `packages/rag/chat.py`, including non-streaming and streaming chat orchestration, final-only assistant persistence, `rag.chat`/`rag.chat.stream` audit events, and final SSE `session_id` enrichment.
- Added thin `/chat` and `/chat/stream` API routes, ChatRequestBody/ChatResponse DTOs, FastAPI dependency wiring, and service override-friendly integration tests.
- Extended shared redaction for chat/RAG content fields and local absolute path values without redacting `kb://` citation sources.
- Updated README and local development docs with chat/session usage, SSE final payload details, fake provider behavior, validation commands, and out-of-scope items.
- Stores bounded assistant final answers because follow-up resolution needs prior assistant turns as conversation memory; error stream terminals store only a safe error summary, and audit/log metadata still excludes full answers.
- Addressed code review findings by requiring both RAG permissions, excluding the current turn from prompt history, adding explicit memory commits, hardening stream terminal failures, improving chat audit coverage, and enforcing per-session sequence uniqueness.

### File List

- `packages/memory/__init__.py`
- `packages/memory/dto.py`
- `packages/memory/exceptions.py`
- `packages/memory/service.py`
- `packages/memory/storage/__init__.py`
- `packages/memory/storage/models.py`
- `packages/memory/storage/repositories.py`
- `packages/common/logging.py`
- `packages/rag/chat.py`
- `packages/rag/streaming.py`
- `packages/rag/dto.py`
- `packages/rag/prompt_builder.py`
- `packages/rag/query.py`
- `packages/rag/__init__.py`
- `apps/api/routes/chat.py`
- `apps/api/main.py`
- `apps/api/service_dependencies.py`
- `migrations/versions/20260527_0009_chat_memory.py`
- `README.md`
- `docs/operations/local-development.md`
- `tests/unit/memory/__init__.py`
- `tests/unit/memory/test_dto.py`
- `tests/unit/memory/test_service.py`
- `tests/unit/memory/test_chat_application_service.py`
- `tests/unit/common/test_logging.py`
- `tests/unit/test_architecture_boundaries.py`
- `tests/unit/rag/test_prompt_builder.py`
- `tests/unit/rag/test_query_service.py`
- `tests/integration/api/test_chat_routes.py`
- `tests/integration/storage/test_chat_memory_repositories.py`
- `tests/integration/storage/test_alembic_migrations.py`

---
baseline_commit: 8e7e56a
---

# Story 9.3: Open WebUI Function/Tool Bridge 与权限映射

Status: review

生成时间：2026-06-09T21:55:54+08:00

## Story

As a 平台工程师,
I want Open WebUI 的 function/tool 调用只进入后端 Tool Registry,
so that UI 侧工具能力不会绕过 schema、permission、timeout、rate limit 和 audit。

## Acceptance Criteria

1. **OpenAI-compatible tools/functions 请求被显式解析和治理**
   - Given Open WebUI 请求在 `POST /v1/chat/completions` 中声明 `tools`、legacy `functions`、`tool_choice` 或 `function_call`
   - When 后端接收兼容请求
   - Then 请求必须被规范化为受控工具声明候选，字段仅包含 `name`、`description`、safe JSON schema 摘要、choice 模式和 request/trace 关联
   - And 未注册工具、非法工具名、重复工具、非 `type=function`、schema 非 object、schema 过大、legacy/modern 混用冲突全部被结构化拒绝并审计
   - And 工具声明不得进入 prompt、answer、QueryCommand metadata_filter、日志全文或前端权限判断

2. **工具执行入口只能是后端 Agent run 或 Tool Registry 候选**
   - Given Open WebUI 声明了可用工具
   - When adapter 决定进入工具路径
   - Then 必须映射为受控 `AgentRunApplicationService` 调用或明确的 Tool Registry 调用候选；route 不得直接调用 `ToolRegistry.execute`
   - And 所有执行仍由后端已注册 `ToolDefinition` 决定 input_schema、output_schema、permission、timeout、rate_limit 和 handler
   - And 不新增第二套 Tool Registry、第二套 tool audit 表、第二套 Agent runtime、前端 tool executor 或任意 Python callable 入口

3. **权限映射以后端 AuthContext 为唯一依据**
   - Given Open WebUI service token 默认只具备 `document:read` 和 `retrieval:query`
   - When 请求尝试调用 `rag_search`、`calculator`、`file_reader` 或未来 `web_search`
   - Then 后端必须根据 `AuthenticatedRequestContext.auth.permissions`、`agent:run` 和具体 `agent:tool:*` 权限拒绝或允许
   - And 不允许通过 tool schema、model message、metadata、Open WebUI 配置、tool_choice、function_call、roles 字段或 prompt 内容提升权限
   - And 拒绝形态不得泄露未授权工具是否可用于其他用户、文件是否存在、ACL、roles、permissions、source_uri、本地路径或内部策略细节

4. **成功路径返回安全 observation 和可追溯审计引用**
   - Given 后端允许并执行工具路径
   - When 返回给 Open WebUI 非流式或 streaming 响应
   - Then 响应只能包含 safe observation summary、`agent_run_id`、`tool_call_id`、`tool_name`、`status`、`latency_ms`、`error_code`、`request_id`、`trace_id` 和 citation-safe identifiers
   - And `rag_search` 结果只能返回 document/version/chunk/page/source_display_name 等 citation-safe identifiers，不返回 chunk text、raw source_uri、object key 或未授权上下文
   - And `calculator` 不回显危险表达式上下文；`file_reader` 不返回任意文件内容，仍受 allowlist、大小、类型和敏感内容规则限制

5. **流式工具桥接复用 9.2 tool event contract**
   - Given 工具路径产生 `tool_call` 或 `tool_result`
   - When Open WebUI 使用 `stream=true`
   - Then 必须复用 `packages/rag/openwebui.py` 已有 safe `tool_event` chunk 和 final `metadata.tool_event_summary`
   - And token chunks 不携带工具 metadata；final chunk 只汇总安全计数和单个 `agent_run_id`（如唯一）
   - And `[DONE]` 终止行为、error chunk redaction、citation evidence links 和 audit failure fallback 不回归

6. **审计与日志覆盖声明、拒绝和执行结果**
   - Given Open WebUI tool/function bridge 接收请求、拒绝请求或执行工具
   - When 写入 audit/log
   - Then 只记录 request_id、trace_id、tenant_id、user_id、requested_model、tool_declaration_count、allowed_tool_count、denied_tool_count、agent_run_id、tool_call_id、status、latency、error_code 和安全 reason code
   - And 不记录 raw tool schema、raw arguments、raw output、完整 query、answer、prompt、message history、tool observation、provider payload、SQL、vectors、embeddings、token、secret、ACL、roles 或 permissions

7. **生产依赖组装和配置保持单一后端治理路径**
   - Given 当前 `apps/api/service_dependencies.py` 中 OpenWebUI adapter 和 Agent service 分开组装
   - When 实现 9.3
   - Then 必须在依赖层显式组装可复用工具 registry/agent bridge，并确认 `rag_search`、`calculator`、`file_reader` 的注册策略
   - And 不得把业务逻辑放入 `apps/api/routes/openwebui.py`；route 仍只做 schema、AuthContext 注入、adapter 调用和 streaming response
   - And 如果生产依赖暂不启用某个工具，必须返回稳定 unsupported/permission denial，而不是默默忽略或伪造成功

8. **文档、README 和限制说明同步**
   - Given Open WebUI function/tool bridge 完成
   - When 用户查看 README、Open WebUI 本地开发文档、tool event 文档或 Agent 文档
   - Then 文档说明支持的 OpenAI-compatible `tools/functions` 字段、权限要求、拒绝形态、安全字段白名单、out of scope 和验证命令
   - And README 的 Build Status、Open WebUI/Agent/Governance 当前能力、Current Limits 或验证命令必须按本次能力同步；如果实现不影响 README，Dev Agent 最终回复必须说明原因

9. **测试覆盖**
   - Given 单元、集成、静态契约和安全回归测试运行
   - When 验证 9.3
   - Then 覆盖 request schema 解析、modern/legacy tool declaration、unknown tool、duplicate tool、invalid schema、tool_choice/function_call、missing `agent:run`、missing `agent:tool:*`、service token 默认拒绝、成功 safe observation、stream tool events、audit metadata、route contract、docs/README expectations
   - And 测试使用 fake service、fake tool registry、repository mock、TestClient、Node `vm` runner或静态契约测试；不得真实调用 OpenAI/Qwen/DeepSeek/Ollama/vLLM、真实 Open WebUI、浏览器、Docker、PostgreSQL、Redis、MinIO 或网络

## Tasks / Subtasks

- [x] 扩展 OpenAI-compatible request schema 与规范化 helper（AC: 1, 3, 6）
  - [x] 在 `packages/rag/openwebui.py` 为 `OpenAIChatCompletionRequest` 增加 explicit optional fields：`tools`、`tool_choice`、legacy `functions`、legacy `function_call`；保持 `extra="ignore"`，但对这些字段做严格验证。
  - [x] 新增 framework-free DTO/helper，例如 `OpenAICompatibleToolDeclaration`、`OpenAICompatibleFunctionDeclaration`、`OpenWebUIToolBridgeCandidate`，只保留安全字段和 schema 摘要。
  - [x] 支持 modern `tools=[{"type":"function","function":{...}}]`；legacy `functions=[...]` 只作为兼容输入归一化，不新增旧执行路径。
  - [x] 拒绝非法工具名、非 lower snake case、空 description、非 object parameters、schema 过大、重复工具、unknown `tool_choice`、同时声明冲突的 modern/legacy forced choice。
  - [x] 明确禁止 tool declaration 中的 `tenant_id`、`user_id`、roles、permissions、ACL、token、secret、source_uri、file_path、prompt、raw output 等字段进入 safe metadata。

- [x] 设计 OpenWebUI 工具桥接服务/端口（AC: 2, 3, 7）
  - [x] 优先新增小型 application/service 边界，例如 `packages/agent/openwebui_bridge.py` 或 `packages/rag/openwebui.py` 中的 `OpenWebUIToolBridge` Protocol；选择时必须避免 `packages/agent` 依赖 FastAPI 或 route。
  - [x] bridge 输入应包含 `AuthenticatedRequestContext`、latest user query、session_id、规范化 tool candidates、tool_choice/function_call summary、stream flag 和 max limits。
  - [x] bridge 输出应是 safe `ChatResponse` / `RagStreamEvent` 兼容结果，或一个明确的 structured denial；不得返回 raw tool output。
  - [x] 工具路径必须复用 `AgentRunApplicationService` 或现有 `AgentRuntime` + `ToolRegistry`，不能在 adapter 中手写工具执行流程。
  - [x] 如果选择先把 Open WebUI tool declaration 映射为 Agent run：`AgentRunCommand.metadata` 只能包含 safe tool candidate names/counts，不包含 raw schema、raw arguments 或 message history。

- [x] 补齐并复用生产 Tool Registry 注册路径（AC: 2, 3, 7）
  - [x] 检查 `apps/api/service_dependencies.py` 当前 `ToolRegistry(...)` 是否实际注册 `rag_search`、`calculator`、`file_reader`；当前片段显示 registry 被创建后未注册工具，Dev Agent 必须先修正或确认这一点。
  - [x] 提供一个集中 helper，例如 `build_agent_tool_registry(...)`，在 `/agent/run` 和 OpenWebUI tool bridge 中复用相同注册逻辑。
  - [x] `rag_search` 注册必须注入现有 `RetrieveApplicationService` 或受控 fake/mock；`calculator` 保持纯计算；`file_reader` 必须使用配置化 allowlist，默认不允许任意项目根或系统路径。
  - [x] 注册 helper 不得真实调用外部 LLM provider，不得绕过 `ToolDefinition` schema/permission/rate_limit/timeout。

- [x] 将 OpenWebUI adapter 接入工具桥接路径（AC: 1, 2, 4, 5, 6）
  - [x] 在 `OpenWebUIChatAdapter.chat_completion()` 中，当 request 有工具声明时，先走 bridge/policy；无工具声明时保持现有 RAG chat path 完全不变。
  - [x] 在 `OpenWebUIChatAdapter.stream_chat_completion()` 中，工具路径必须产生与 9.2 兼容的 `tool_call` / `tool_result` / `final` events，并继续输出 OpenAI-compatible chunks 和 `[DONE]`。
  - [x] 工具路径返回的 final metadata 必须同时保留 9.1 evidence links 和 9.2 tool event summary；无 citation 时不得伪造 evidence link。
  - [x] 对 `tool_choice="none"` 或 equivalent legacy none，应明确走普通 RAG chat path，且 audit 记录 declaration_count 和 bridge_skipped reason。
  - [x] 对 forced unknown/denied tool，应返回 structured OpenAI-compatible error/denial，而不是回退普通聊天掩盖问题。

- [x] 权限和拒绝策略（AC: 3, 6）
  - [x] 增加后端 policy：工具桥接需要 `agent:run`；具体工具执行需要 `agent:tool:<name>`；`rag_search` 内部仍需要 `document:read` + `retrieval:query`。
  - [x] Open WebUI service token 默认权限为 `document:read,retrieval:query` 时，声明 `calculator`、`file_reader` 等必须被拒绝并审计；不能由 tool schema、message 或 metadata 提权。
  - [x] 拒绝错误只暴露 stable `error_code`、safe status、request_id、trace_id、next_step；不暴露 required permissions 列表给 Open WebUI 响应，除非已有统一安全错误 contract 允许。
  - [x] 对 unknown tool 统一返回 safe denial，不泄露“存在但无权”和“未注册”的内部差异给低权限入口；audit 可记录安全 reason code。

- [x] safe observation 和 citation-safe result contract（AC: 4, 5, 6）
  - [x] 复用 `ToolExecutionResult.metadata`、`ToolCallRecord` 和 `AgentObservationSummary` 中的安全摘要字段，禁止透传 raw output。
  - [x] `rag_search` 成功时只暴露 `document_id`、`version_id`、`chunk_id`、page range、`source_display_name`、`retrieval_method`、score bucket/count 等安全字段；是否展示 excerpt 必须仍由 `/sources/resolve` 二次授权。
  - [x] `calculator` result summary 只暴露 status、result numeric summary 或 bounded safe value，不回显复杂表达式上下文、prompt 或用户原文。
  - [x] `file_reader` result summary 不返回文件全文；如果现有工具输出包含截断 excerpt，OpenWebUI bridge 必须二次 allowlist 输出字段或只给 audit/source review 引用。

- [x] 审计、日志和 observability（AC: 6, 7）
  - [x] 扩展 `_adapter_audit_metadata()` 或新增 bridge audit metadata，记录 `tool_declaration_count`、`tool_candidate_count`、`tool_bridge_used`、`tool_bridge_status`、`tool_error_count`、`agent_run_id` 等安全摘要。
  - [x] 记录 bridge denial/success/failure 的 `AuditEvent`，resource type 建议为 `openwebui_tool_bridge` 或复用现有 `openwebui_chat` safe metadata。
  - [x] 确保 audit write failure 不破坏 SSE `[DONE]`，但有结构化 warning 测试；warning 不含 raw schema/arguments/output。

- [x] 后端测试（AC: 1-7, 9）
  - [x] 扩展 `tests/unit/rag/test_openwebui_adapter.py`，覆盖 tools/functions schema 解析、normalization、unknown tool、duplicate tool、tool_choice none/auto/forced、unsafe field redaction、ordinary RAG path 不回归。
  - [x] 新增或扩展 `tests/unit/agent`，覆盖 bridge/service 对 `agent:run`、`agent:tool:rag_search`、`agent:tool:calculator`、`agent:tool:file_reader` 的权限矩阵，service token 默认拒绝 calculator/file_reader。
  - [x] 扩展 `tests/integration/api/test_openwebui_routes.py`，用 fake bridge/service 验证 route contract、streaming chunks、structured denial、auth rejection before bridge call。
  - [x] 扩展 `tests/unit/test_architecture_boundaries.py`，防止 route 直接 import/call ToolRegistry、OpenWebUI adapter 直接绑定 concrete storage/LLM/vector DB、以及第二套 registry/runtime。
  - [x] 若补齐生产 registry helper，添加 tests 确认 `/agent/run` 和 OpenWebUI bridge 使用同一注册 helper 或等价 definition set。

- [x] 前端/governance fallback 测试（AC: 4, 5, 9）
  - [x] 如 OpenWebUI bridge 新增 fallback metadata shape，扩展 `apps/web/sidecar/sidecar.js` allowlist/parser，保持 9.2 tool event fallback 安全字段。
  - [x] 扩展 `tests/unit/web/test_governance_static_contract.py`、`tests/unit/web/test_sidecar_static_contract.py` 和 `tests/unit/web/sidecar_behavior_runner.js`，只允许 safe observation/tool event 字段进入 Audit Explorer/Review Queue seed。
  - [x] 不新增 React/Next/Vite/Playwright/Open WebUI 容器测试依赖。

- [x] 文档和 README 更新（AC: 8）
  - [x] 更新 `docs/api/openwebui-tool-events.md` 或新增 `docs/api/openwebui-tool-bridge.md`，记录 request fields、field allowlist、denial/error examples、permission requirements 和 out of scope。
  - [x] 更新 `docs/operations/local-development.md`，加入 focused curl/TestClient 示例和本地验证命令。
  - [x] 更新 `docs/demo/governance-workbench.md`，说明 Audit Explorer/Review Queue 如何查看 bridge denial/safe tool events。
  - [x] 更新 README Build Status / Open WebUI / Agent / Current Limits / 验证命令；不要宣称 Open WebUI fork、插件体系、复杂 Web crawler、多 Agent 或敏感写操作已完成。

- [x] 建议验证命令（AC: 1-9）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/rag/test_openwebui_adapter.py tests/unit/rag/test_streaming.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/api/test_openwebui_routes.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/agent -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/web/test_governance_static_contract.py tests/unit/web/test_sidecar_static_contract.py -q`
  - [x] `node tests/unit/web/sidecar_behavior_runner.js`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/test_architecture_boundaries.py tests/unit/test_readme_expectations.py -q`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`
  - [x] 如成本可接受，运行 `.venv\Scripts\python.exe -m pytest -q`

## Dev Notes

### Current Repository State

- Current HEAD at story creation: `8e7e56a feat(openwebui): stream safe tool events`.
- `git status --short` was clean at story creation.
- Sprint status shows 8.6、8.7、9.1、9.2 still in `review`; before implementation, dev agent should check whether review feedback landed because 9.3 touches OpenWebUI adapter、Agent runtime、tool audit、governance fallback and docs.
- `packages/rag/openwebui.py` currently ignores extra request fields by default. It does not model `tools`、`tool_choice`、`functions` or `function_call`, so Open WebUI tool declarations are currently discarded.
- `packages/rag/openwebui.py` already implements 9.1 evidence links and 9.2 safe `tool_event` chunks. 9.3 must add bridge behavior without regressing those contracts.
- `packages/agent/dto.py` already defines `ToolDefinition` with `name`、`description`、`input_schema`、`output_schema`、`permission`、`timeout_seconds`、`rate_limit` and async `handler`.
- `packages/agent/registry.py` already enforces registered tool lookup、input/output schema validation、permission、rate limit、timeout、tool_call persistence and safe audit summaries.
- `packages/agent/runtime.py` already has `AgentEventSink` / `AgentToolEvent` from 9.2 and emits safe tool_call/tool_result events for runtime-controlled tool execution.
- `packages/agent/service.py` already gates `/agent/run` with `agent:run` and persists `agent_runs`, but input metadata must stay safe.
- `apps/api/service_dependencies.py` currently creates `ToolRegistry(audit=audit, tool_call_recorder=tool_call_repository)` in `get_agent_run_application_service()` but the visible production dependency snippet does not register `rag_search`、`calculator` or `file_reader`. This is a likely integration gap to resolve before claiming the bridge executes real tools.
- `apps/api/routes/openwebui.py` is thin and should remain thin.
- `docs/api/openwebui-tool-events.md` explicitly says 9.2 is visibility-only and does not let Open WebUI declare tools. 9.3 must update that limitation once implemented.

### Existing Files To Read Before Implementation

- `packages/rag/openwebui.py`
  - Current state: OpenAI-compatible request/response DTOs, chat adapter, evidence links, safe tool event formatter, stream audit and safe metadata redaction.
  - What this story changes: parse OpenAI-compatible tool/function declarations; branch to a controlled bridge when tools are present; add safe bridge audit metadata.
  - Preserve: latest-user-message extraction, metadata filter scope rejection, evidence links, tool event chunks, final metadata, error redaction, `[DONE]`, no raw source locators.

- `packages/rag/streaming.py`
  - Current state: named SSE payloads including `ToolCallEventPayload` and `ToolResultEventPayload`.
  - What this story changes: usually none unless bridge needs a small helper to convert Agent events to RAG stream events.
  - Preserve: `/query/stream` and `/chat/stream` named SSE contract.

- `packages/agent/dto.py`
  - Current state: ToolDefinition, ToolExecutionResult, ToolCall persistence DTOs, AgentRun request/response DTOs, safe summary validation.
  - What this story changes: maybe add OpenWebUI bridge DTOs or safe metadata fields if they belong in Agent package.
  - Preserve: forbidden summary keys and no unsafe string values.

- `packages/agent/registry.py`
  - Current state: authoritative Tool Registry execution path.
  - What this story changes: preferably no core behavior change; maybe expose safe registry introspection for known tool names if needed.
  - Preserve: input/output schema validation, permission, rate limit, timeout, audit and tool_call persistence.

- `packages/agent/runtime.py`
  - Current state: ReAct-style loop with limits, repeated action detection, event sink and final answer validation.
  - What this story changes: usually no change unless bridge needs a specific stepper or safe conversion of OpenWebUI tool choice into runtime hints.
  - Preserve: max_steps, max_tool_calls, timeout, no direct arbitrary functions, event sink failure isolation.

- `packages/agent/service.py`
  - Current state: `AgentRunApplicationService` creates/persists runs and requires `agent:run`.
  - What this story changes: bridge may call this service or a sibling service; metadata must only contain safe summaries.
  - Preserve: storage rollback, audit lifecycle and safe metadata filtering.

- `packages/agent/tools/rag_search.py`
  - Current state: governed RAG search tool with `RAG_SEARCH_PERMISSION`.
  - What this story changes: no behavior change expected; bridge should reuse definition and respect internal retrieval permissions.
  - Preserve: no unauthorized chunk text/source leakage.

- `packages/agent/tools/calculator.py`
  - Current state: deterministic arithmetic subset with `CALCULATOR_PERMISSION`.
  - What this story changes: no behavior change expected; bridge should register/reuse it.
  - Preserve: no external I/O and bounded expression/result behavior.

- `packages/agent/tools/file_reader.py`
  - Current state: allowlisted local file adapter with `FILE_READER_PERMISSION`.
  - What this story changes: no behavior change expected; bridge should never broaden allowlist for OpenWebUI.
  - Preserve: no arbitrary paths, no sensitive content, no local path leakage.

- `apps/api/service_dependencies.py`
  - Current state: builds OpenWebUI adapter around `ChatApplicationService`; builds Agent service separately; production Agent registry registration appears incomplete in the inspected snippet.
  - What this story changes: centralize tool registry registration and assemble OpenWebUI tool bridge without route business logic.
  - Preserve: settings-driven construction, provider abstraction, SQLAlchemy only in dependency/storage layers.

- `apps/api/routes/openwebui.py`
  - Current state: thin `/v1/models` and `/v1/chat/completions` routes.
  - What this story changes: likely none beyond dependency injection type if adapter constructor changes.
  - Preserve: no direct ToolRegistry, storage, LLM, vector DB or policy logic.

- `tests/unit/rag/test_openwebui_adapter.py`
  - Current state: covers OpenWebUI request normalization, evidence links, safe stream tool events and audit counts.
  - What this story changes: add tools/functions parsing, bridge used/skipped/denied/success cases.

- `tests/integration/api/test_openwebui_routes.py`
  - Current state: route-level OpenWebUI contract tests with fake services.
  - What this story changes: validate tool declaration route contract and auth rejection before bridge.

- `tests/unit/agent/*`
  - Current state: Tool Registry, tools, runtime, Agent run service and final answer validation tests.
  - What this story changes: add bridge and shared registry registration tests.

- `README.md`, `docs/api/openwebui-tool-events.md`, `docs/operations/local-development.md`, `docs/demo/governance-workbench.md`
  - Current state: document 9.1/9.2 OpenWebUI evidence/tool event capabilities and current limitations.
  - What this story changes: update function/tool bridge capability and limits after implementation.

### Previous Story Intelligence

- Story 9.1 established same-origin evidence links and Source Evidence reauthorization. 9.3 tool results must use evidence identifiers and `/sources/resolve`, not raw excerpts or raw source locators.
- Story 9.2 established safe tool event streaming/fallback and explicitly left OpenWebUI function/tool declaration mapping to 9.3. Reuse its event DTOs and do not create a new event shape.
- Story 7.2 established Open WebUI service token authentication. Service token permissions are backend AuthContext, not UI authority.
- Story 6.1-6.7 established Tool Registry、tools、Agent runtime、tool call persistence and final answer validation. 9.3 should wire those into OpenWebUI, not reimplement them.
- Story 8.6/8.7 established safe Audit Explorer and Review Queue export/review flows. Tool bridge denial and safe events should link into those summaries without exposing raw tool data.
- Recent commits follow the pattern: framework-free DTOs in `packages/*`, thin FastAPI routes, static no-build governance UI, focused unit/integration tests, README/docs updates and explicit safety allowlists.

### Architecture and Security Guardrails

- Open WebUI is an integration surface, not a permission boundary.
- Tool declaration is not authorization. The backend decides tool availability from registered `ToolDefinition` and `AuthContext`.
- Client-provided tool schema is untrusted metadata. It may describe a requested interface, but it must never override registered server schema, permission, timeout, rate limit or handler.
- Tool output is observation, not system instruction. It must not alter backend policy, prompt policy or user permissions.
- Prompt content, model messages, metadata and Open WebUI config cannot grant `agent:run` or `agent:tool:*`.
- Do not add Open WebUI fork/plugin/custom frontend work in this story; Story 9.4 owns maintainable customization strategy.
- Do not add new tools beyond existing `rag_search`、`calculator`、`file_reader`; future `web_search` stays optional and must follow Tool Registry rules.
- Do not implement multi-agent, LangGraph workflow, sensitive write tools, arbitrary file access, external web crawler or raw provider payload pass-through.

### Latest Technical Information

- Open WebUI's OpenAI-compatible provider integration continues to center on `GET /v1/models` and `POST /v1/chat/completions`; keep 9.3 on that compatibility surface rather than requiring an Open WebUI fork. Source: https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-openai-compatible
- OpenAI Chat Completions supports tool declarations through the `tools` field and tool selection through `tool_choice`; compatible clients may also still send legacy `functions`/`function_call`. Treat legacy fields as compatibility input only and normalize them into the same backend policy path. Source: https://platform.openai.com/docs/api-reference/chat/create
- No new external dependency is required. Existing FastAPI, Pydantic v2, pytest, TestClient and Node `vm` runner are sufficient.

### References

- `_bmad-output/planning-artifacts/epics.md#Story-9.3-Open-WebUI-Function-Tool-Bridge-与权限映射`
- `_bmad-output/planning-artifacts/epics.md#Epic-9-Open-WebUI-企业级集成增强与轻量魔改路线`
- `_bmad-output/planning-artifacts/architecture.md#Architectural-Boundaries`
- `_bmad-output/planning-artifacts/architecture.md#Integration-Points`
- `PRD.md#FR-20-前端集成路径`
- `PRD.md#FR-25-Tool-Registry`
- `PRD.md#FR-27-Agent-Runtime`
- `PRD.md#FR-28-Tool-Call-Audit`
- `project-context.md#12-Agent-规则`
- `project-context.md#13-Prompt-Injection-防护`
- `project-context.md#16-权限规则`
- `_bmad-output/implementation-artifacts/9-1-open-webui-citation-evidence-link-contract.md`
- `_bmad-output/implementation-artifacts/9-2-open-webui-tool-event-streaming-bridge.md`
- `_bmad-output/implementation-artifacts/6-1-tool-registry-与工具治理模型.md`
- `_bmad-output/implementation-artifacts/6-2-rag-search-工具.md`
- `_bmad-output/implementation-artifacts/6-3-calculator-与受限-file-reader-工具.md`
- `_bmad-output/implementation-artifacts/6-4-react-agent-runtime-限制与重复动作检测.md`
- `_bmad-output/implementation-artifacts/6-6-tool-call-audit-persistence.md`
- `packages/rag/openwebui.py`
- `packages/rag/streaming.py`
- `packages/agent/dto.py`
- `packages/agent/registry.py`
- `packages/agent/runtime.py`
- `packages/agent/service.py`
- `packages/agent/tools/rag_search.py`
- `packages/agent/tools/calculator.py`
- `packages/agent/tools/file_reader.py`
- `apps/api/routes/openwebui.py`
- `apps/api/routes/agent.py`
- `apps/api/service_dependencies.py`
- `docs/api/openwebui-tool-events.md`
- `tests/unit/rag/test_openwebui_adapter.py`
- `tests/integration/api/test_openwebui_routes.py`
- `tests/unit/agent/test_tool_registry.py`
- `tests/unit/agent/test_runtime.py`
- `tests/unit/agent/test_agent_run_service.py`
- Open WebUI OpenAI-compatible server docs: https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-openai-compatible
- OpenAI Chat Completions API reference: https://platform.openai.com/docs/api-reference/chat/create

## Validation Checklist

Validation Result: PASS（2026-06-09T21:55:54+08:00）

- [x] Story 明确 9.3 只实现 Open WebUI tools/functions 到后端治理入口的桥接，不实现 Open WebUI fork/plugin、复杂前端、多 Agent、外部 web crawler 或任意 Python 函数执行。
- [x] Acceptance Criteria 覆盖 request schema、Tool Registry/Agent run 映射、AuthContext 权限、safe observation、stream tool events、audit/log、生产依赖组装、docs/README 和测试。
- [x] Tasks 指向现有 `packages/rag/openwebui.py`、`packages/agent/*`、`apps/api/service_dependencies.py`、OpenWebUI route、工具实现和既有测试体系，避免重复 registry/runtime/event DTO。
- [x] Dev Notes 记录当前代码状态、生产 ToolRegistry 注册疑点、9.1/9.2 复用边界、前序 story learnings 和最新 OpenAI/OpenWebUI 兼容信息。
- [x] 明确禁止 raw tool schema/arguments/output、prompt、query、answer、message history、chunk text、source_uri、object key、本地路径、ACL、roles、permissions、token、secret 和 raw exception。
- [x] README 同步要求已写入 AC/Tasks；本次 create-story 只创建 story，不实现功能，因此不更新 README。

## Change Log

- 2026-06-09: Created comprehensive Story 9.3 developer context for Open WebUI function/tool bridge and permission mapping.
- 2026-06-09: Implemented governed Open WebUI function/tool bridge, shared Tool Registry assembly, safe bridge docs, and regression coverage.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

### Completion Notes List

- Added OpenAI-compatible `tools/functions` request validation and normalization in `packages/rag/openwebui.py`, including safe schema summaries, duplicate/mix rejection, and `tool_choice/function_call` governance.
- Added `packages/agent/openwebui_bridge.py` to route Open WebUI tool declarations through governed backend Tool Registry execution with `agent:run` and `agent:tool:*` permission enforcement, safe denial shape, persisted `agent_run_id`/`tool_call_id`, and citation-safe observation summaries.
- Centralized production tool registration in `build_agent_tool_registry(...)` so `/agent/run` and the Open WebUI bridge share `rag_search`, `calculator`, and configuration-bound `file_reader` definitions.
- Updated Open WebUI adapter streaming and non-streaming paths to reuse the 9.2 tool event contract, preserve 9.1 evidence links, and keep ordinary RAG chat behavior when no executable tool bridge path is selected.
- Updated `.env.example`, README, and Open WebUI/governance/local-development docs to reflect the new bridge, permission requirements, allowlisted fields, and current limitations.
- Verified with `pytest -q`, focused Open WebUI/agent/static-contract suites, `ruff check apps packages tests`, `mypy apps packages tests`, and `node tests/unit/web/sidecar_behavior_runner.js`.

### File List

- .env.example
- README.md
- _bmad-output/implementation-artifacts/9-3-open-webui-function-tool-bridge-与权限映射.md
- _bmad-output/implementation-artifacts/sprint-status.yaml
- apps/api/service_dependencies.py
- docs/api/openwebui-tool-bridge.md
- docs/api/openwebui-tool-events.md
- docs/demo/governance-workbench.md
- docs/operations/local-development.md
- packages/agent/openwebui_bridge.py
- packages/agent/registry.py
- packages/common/config.py
- packages/rag/openwebui.py
- tests/unit/agent/test_openwebui_bridge.py
- tests/unit/rag/test_openwebui_adapter.py

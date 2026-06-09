---
baseline_commit: 632cc48
---

# Story 9.2: Open WebUI Tool Event Streaming Bridge

Status: review

生成时间：2026-06-09T20:39:33+08:00

## Story

As a Agent 用户,
I want 在 Open WebUI 中看到 `tool_call` 和 `tool_result` 的安全事件摘要,
so that Agent 执行过程可解释但不泄露敏感内容。

## Acceptance Criteria

1. **OpenAI-compatible streaming 显式承载工具事件摘要**
   - Given 后端生成 `tool_call` 或 `tool_result` `RagStreamEvent`
   - When Open WebUI 通过 `POST /v1/chat/completions` 且 `stream=true` 接收响应
   - Then OpenAI-compatible chunk 必须包含安全工具事件摘要：`event`、`agent_run_id`、`tool_call_id`、`tool_name`、`status`、`latency_ms`、`error_code`、`request_id`、`trace_id`
   - And chunk 仍保持 OpenAI-compatible `chat.completion.chunk` 外形和 `[DONE]` 终止行为
   - And token chunks 不得携带 tool metadata；final chunk 可汇总 `tool_event_count` 和安全 next links

2. **非原生 UI fallback 可读且可复制**
   - Given Open WebUI 不支持原生 tool event UI
   - When adapter 需要展示工具过程
   - Then 提供安全 markdown/metadata fallback，例如 `tool_events` 数组或 `tool_event_summary`
   - And fallback 可被复制到 Governance Workbench 的 Audit Explorer、Review Queue 或 Agent Review 入口
   - And fallback 不依赖 Open WebUI fork、浏览器插件、React/Next/Vite 构建或 Open WebUI 容器内补丁

3. **拒绝和失败事件保持结构化但不泄露策略细节**
   - Given 工具调用因 permission、schema、timeout、rate_limit、max_tool_calls、repeated action 或 final answer validation 被拒绝/失败
   - When Open WebUI 展示事件摘要
   - Then 用户能看到后端稳定 `error_code`、安全 `status`、`latency_ms`、`request_id`、`trace_id` 和安全 next-step hint
   - And 不泄露策略内部规则、未授权资源是否存在、raw arguments、raw output、tool observation、文件内容、prompt、token、source_uri、object key、本地路径、ACL、roles 或 permissions

4. **复用现有 Tool Registry、Agent audit 和 stream DTO**
   - Given 当前已有 `ToolCallEventPayload`、`ToolResultEventPayload`、Tool Registry、tool call persistence、Agent run audit、Audit Explorer 和 Review Queue
   - When 实现 9.2
   - Then 优先扩展 `packages/rag/streaming.py` 和 `packages/rag/openwebui.py` 的格式化/DTO/helper；如需 Agent side 事件生成，扩展现有 Agent runtime/service 的安全 event sink
   - And 不新增第二套 tool event DTO、第二套 tool audit 表、第二套 Agent runtime、第二套 Open WebUI adapter 或前端权限判断器
   - And 不实现 9.3 的 Open WebUI function/tool bridge；Open WebUI 声明 tool/function 到 Tool Registry 的映射仍属于后续 story

5. **事件来源和审计可追溯**
   - Given Agent runtime 产生工具事件
   - When 事件被输出到 Open WebUI stream 或 fallback metadata
   - Then 每个事件都能关联 `agent_run_id`、`tool_call_id` 或 request/trace 范围内的 tool audit 记录
   - And adapter audit metadata 只记录 `tool_event_count`、`tool_call_count`、`tool_result_count`、`error_event_count`、`agent_run_id` 安全摘要、latency/status/error_code
   - And 不记录或导出完整 query、answer、prompt、chunk text、tool input/output、provider payload、SQL、vectors、embeddings、tokens、secrets 或 raw exception

6. **前端治理入口只展示后端确认事实**
   - Given fallback metadata 包含 tool event summary 或 governance links
   - When `/governance` 或 `/sidecar` 解析这些事件
   - Then 只允许安全字段进入 UI、copy/download 和 review item 创建表单
   - And UI 必须复用现有 no-build `apps/web/sidecar/sidecar.js` allowlist/stale clearing 模式
   - And 前端不得根据 tool name、status 或 error_code 判断权限、补造 tool result、构造 source excerpt 或推断未授权资源存在性

7. **文档和 README 同步**
   - Given tool event streaming bridge 完成
   - When 用户查看 README、Open WebUI 本地开发文档、Governance Workbench 文档或 Source Inspector 文档
   - Then 文档说明 Open WebUI tool event metadata/fallback 字段、拒绝/失败显示、安全字段白名单、Audit Explorer/Review Queue 跳转、限制和验证命令
   - And README 的 Build Status、Open WebUI/Agent/Governance 当前能力、Current Limits 或验证命令必须按本次能力同步；如果实际实现不影响 README，Dev Agent 最终回复必须说明原因

8. **测试覆盖**
   - Given 单元、集成、静态契约和行为测试运行
   - When 验证 9.2
   - Then 覆盖 OpenAI-compatible stream 的 `tool_call` / `tool_result` chunk、safe metadata、redaction、`[DONE]`、error fallback、audit summary、no fake tool events、route contract、governance parser/allowlist/stale clearing 和 README/docs expectations
   - And 测试使用 fake service、TestClient、Node `vm` runner、静态契约测试或 repository mock；不得真实调用 OpenAI/Qwen/DeepSeek/Ollama/vLLM、真实 Open WebUI、浏览器、Docker、PostgreSQL、Redis、MinIO 或网络

## Tasks / Subtasks

- [x] 设计工具事件安全摘要 contract（AC: 1, 2, 3, 5）
  - [x] 在 `packages/rag/openwebui.py` 增加 framework-free DTO/helper，例如 `OpenWebUIToolEventSummary`，或明确复用 `ToolCallEventPayload` / `ToolResultEventPayload` 的安全字段。
  - [x] 允许字段限定为：`event`、`agent_run_id`、`tool_call_id`、`tool_name`、`status`、`latency_ms`、`error_code`、`request_id`、`trace_id`、`next_step`、`audit_ref`、`review_ref`。
  - [x] 禁止字段和值进入 response、metadata、audit、copy/export：`arguments`、`raw_arguments`、`output`、`raw_output`、`observation`、`query`、`answer`、`content`、`chunk_text`、`prompt`、`source_uri`、`object_key`、`file_path`、`local_path`、`sql`、`vector`、`embedding`、`provider_payload`、`token`、`secret`、`authorization`、`acl`、`roles`、`permissions`。
  - [x] 对 `metadata` 做 allowlist extraction，而不是透传 `redact_mapping()` 后的完整 mapping；redaction 是防线，不是 API contract。

- [x] 扩展 OpenAI-compatible stream formatter（AC: 1, 2, 3, 5）
  - [x] 更新 `format_openai_stream_event()`，显式处理 `ToolCallEventPayload` 和 `ToolResultEventPayload`，不要继续落入空 delta fallback。
  - [x] 输出保持 `object="chat.completion.chunk"`、`choices[0].delta` 可兼容；工具事件摘要放在顶层安全字段或 `metadata.tool_event`。
  - [x] 对 error chunk 保持现有 redaction 和 `[DONE]` 终止行为。
  - [x] token chunk 不输出 tool_event；final chunk 可输出 `tool_event_count`、`agent_run_id`、`tool_call_count`、`tool_result_count` 和 governance link summary。
  - [x] 非 stream chat response 如需要展示 fallback，只添加 backward-compatible optional metadata，不改变 answer 事实内容。

- [x] 让 Agent side 产生或转发安全工具事件（AC: 1, 3, 4, 5）
  - [x] 先评估是否已有调用链能产生 `RagStreamEvent(event="tool_call"/"tool_result")`；如没有，新增最小 `AgentEventSink` / async callback，而不是重写 Agent runtime。
  - [x] 在工具执行前发出 `tool_call` 摘要，字段来自后端已验证 tool definition 和安全 argument keys，不含 raw arguments。
  - [x] 在工具执行后发出 `tool_result` 摘要，字段来自 `ToolExecutionResult.metadata`、tool call persistence 或 safe result summary，不含 raw output。
  - [x] 拒绝/失败路径必须覆盖 permission、schema validation、timeout、rate limit、max tool calls、repeated action 和 final validation failure。
  - [x] 如 `agent_run_id` 或 `tool_call_id` 尚不可用，应输出 request/trace 关联和后续可解析的 audit reference，不得伪造 ID。

- [x] 保持 9.3 边界：不实现 Open WebUI function/tool bridge（AC: 4）
  - [x] `OpenAIChatCompletionRequest` 不应在本 story 中广泛接受/执行 client-provided `tools` 或 `functions`。
  - [x] 如果为了兼容 Open WebUI 参数需要保留字段，必须忽略或安全记录为 unsupported/pass-through summary，不调用 Tool Registry。
  - [x] Tool Registry 仍由后端 Agent runtime 调用；Open WebUI 不能直接选择任意 Python 函数或扩展权限。

- [x] 扩展 adapter audit metadata（AC: 5）
  - [x] 在 streaming audit 中统计 `tool_call_count`、`tool_result_count`、`tool_error_count`、`tool_event_count` 和可选 `agent_run_id_count`。
  - [x] 不记录每个事件完整 payload、raw metadata、tool arguments、tool output 或完整 answer。
  - [x] 确保 audit write 失败不破坏 stream 终止语义，但 failure 要有结构化 warning 测试或现有模式覆盖。

- [x] 扩展 governance/sidecar no-build 前端解析和 copy allowlist（AC: 2, 6）
  - [x] 在 `apps/web/sidecar/sidecar.js` 添加 `SAFE_TOOL_EVENT_FIELDS`、`SAFE_TOOL_EVENT_METADATA_FIELDS` 或复用 `SAFE_AUDIT_ASSOCIATION_FIELDS` 的明确子集。
  - [x] Source Evidence/Governance parser 接受 Open WebUI top-level `tool_events`、`metadata.tool_events`、`tool_event_summary` 或 direct chunk JSON。
  - [x] 解析后只生成安全展示、Audit Explorer lookup 或 Review Queue safe item seed；不生成 source resolve 请求，不展示 raw tool output。
  - [x] 新请求、解析失败、tab switch、权限失败和 malformed response 必须清理 stale tool event/copy/export/review state。
  - [x] 保持 no-storage：不读写 `localStorage`、`sessionStorage`、cookie、URL history 或 console log。

- [x] 后端测试：stream formatter、adapter 和 route contract（AC: 1, 3, 5, 8）
  - [x] 扩展 `tests/unit/rag/test_streaming.py`，验证 reserved tool payload redaction、safe field contract 和 forbidden values。
  - [x] 扩展 `tests/unit/rag/test_openwebui_adapter.py`，验证 `tool_call` / `tool_result` OpenAI-compatible stream chunk、token chunk 不含 tool metadata、final chunk summary、error fallback 和 audit counts。
  - [x] 扩展 `tests/integration/api/test_openwebui_routes.py`，使用 fake streaming service 验证 route 输出 contract 和 auth rejection，不真实运行 Agent/Open WebUI。
  - [x] 如果新增 Agent event sink，增加 `tests/unit/agent` 覆盖成功、拒绝、timeout、rate limit、max tool calls 和 repeated action event emission。

- [x] 前端静态契约和 Node VM 行为测试（AC: 2, 6, 8）
  - [x] 扩展 `tests/unit/web/test_governance_static_contract.py` 和 `tests/unit/web/test_sidecar_static_contract.py`，验证 tool event safe fields、forbidden fragments absence、ARIA/live region、长 ID wrapping 和 no unsafe field allowlist。
  - [x] 扩展 `tests/unit/web/sidecar_behavior_runner.js`，覆盖 Open WebUI chunk JSON、metadata `tool_events`、malformed event、unsafe key ignoring、stale clearing、copy/export allowlist 和 review seed。
  - [x] 不引入 Playwright、浏览器自动化、Node build pipeline、React/Vite 或 Open WebUI 容器依赖。

- [x] 文档和 README 更新（AC: 7）
  - [x] 更新 `docs/operations/local-development.md`，加入 Open WebUI tool event streaming/fallback curl 示例和验证命令。
  - [x] 更新 `docs/demo/governance-workbench.md`，说明 Audit Explorer/Review Queue 可接收安全 tool event summary。
  - [x] 更新 `docs/demo/source-inspector-sidecar.md`，说明 Source Inspector 不展示 raw tool output，tool events 只作为安全治理入口。
  - [x] 更新 `docs/api/source-metadata.md` 或新增 `docs/api/openwebui-tool-events.md`，记录字段白名单、禁止字段、fallback、out of scope。
  - [x] 更新 README Build Status / Open WebUI / Agent / Current Limits / 验证命令；不要宣称 9.3 function/tool bridge、Open WebUI fork/patch 或长期定制策略已完成。

- [x] 建议验证命令（AC: 1-8）
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

- Current HEAD at story creation: `632cc48 feat(openwebui): add citation evidence links`.
- `git status --short` was clean at story creation.
- Sprint status shows 8.6, 8.7 and 9.1 are still `review`; before implementation, dev agent should check whether review feedback has landed because this story touches shared OpenWebUI/governance assets.
- `packages/rag/streaming.py` already declares `ToolCallEventPayload` and `ToolResultEventPayload`; they redact `metadata` with `redact_mapping()`.
- `packages/rag/openwebui.py` currently formats token/final/error events explicitly; tool events currently fall through to a generic empty-delta chunk and lose their summary.
- `packages/rag/openwebui.py` already implements safe citation evidence links and stream final extension fields. 9.2 should follow that pattern, not replace it.
- `apps/api/routes/openwebui.py` is a thin route around `OpenWebUIChatAdapter`; keep route logic thin.
- `/agent/run` is currently non-streaming and separate from OpenWebUI chat. If this story needs Agent-origin streaming, implement a small event bridge/sink without moving Agent business logic into route or OpenWebUI adapter.
- Governance UI is static HTML/CSS/JS served by FastAPI. There is no React/Next/Vite/package manifest and no Open WebUI fork in the repo.

### Existing Files To Read Before Implementation

- `packages/rag/openwebui.py`
  - Current state: OpenAI-compatible request/response DTOs, response mapping, stream formatter, evidence link contract, adapter audit and safe metadata redaction.
  - What this story changes: add explicit tool event formatting, safe summaries and audit counts.
  - Preserve: latest-user-message extraction, metadata filter scope rejection, evidence links, final chunk fields, error redaction, `[DONE]`, no raw source locators.

- `packages/rag/streaming.py`
  - Current state: named SSE payloads for token, citation, error, final, tool_call and tool_result.
  - What this story changes: likely add constructors/helpers or stricter allowlisted tool event metadata.
  - Preserve: existing `/query/stream` and `/chat/stream` named SSE event contract.

- `packages/agent/runtime.py`
  - Current state: ReAct-style runtime loop with max_steps, max_tool_calls, timeout, repeated action detection, Tool Registry execution and final answer validation.
  - What this story changes: optionally emit safe tool event summaries before/after Tool Registry execution.
  - Preserve: no unbounded loops, no direct arbitrary function calls, no raw arguments/output in observations or audit.

- `packages/agent/registry.py`
  - Current state: validates tool schema, permission, rate limit, timeout, output schema, tool call persistence and audit.
  - What this story changes: usually no direct change unless event IDs/summaries need a safe hook.
  - Preserve: schema/permission/rate-limit enforcement stays backend-owned.

- `packages/agent/dto.py`
  - Current state: owns ToolDefinition, ToolExecutionResult, ToolCallCreate/Record/Query, AgentRun DTOs and safe summary validation.
  - What this story changes: maybe add a safe event DTO only if existing stream DTOs are insufficient.
  - Preserve: forbidden summary keys and no unsafe string values.

- `apps/api/routes/openwebui.py`
  - Current state: thin `/v1/models` and `/v1/chat/completions` routes with streaming response.
  - What this story changes: usually none beyond route tests.
  - Preserve: no business logic, no Tool Registry calls, no storage calls in route.

- `apps/api/routes/agent.py`
  - Current state: thin `/agent/run` non-streaming route.
  - What this story changes: do not add OpenWebUI tool bridge here unless explicitly required by event sink design.
  - Preserve: route delegates to `AgentRunApplicationService`.

- `apps/api/service_dependencies.py`
  - Current state: builds OpenWebUI adapter around `ChatApplicationService`; builds Agent runtime/service separately.
  - What this story changes: if Agent event bridge is needed, dependency assembly should stay centralized here.
  - Preserve: provider abstractions and settings-driven construction.

- `apps/web/sidecar/sidecar.js`
  - Current state: shared no-build JS for source evidence, diagnostics, eval evidence, audit explorer, review queue; has strict allowlists and stale clearing.
  - What this story changes: add tool event parser/render/copy/review seed allowlists if frontend fallback is in scope.
  - Preserve: no storage/cookies/history/console logs; no unsafe field names; request token race protection.

- `apps/web/governance/index.html` and `apps/web/sidecar/index.html`
  - Current state: governance workbench and source inspector already include evidence/audit/review workflows.
  - What this story changes: minimal text/control updates for pasted OpenWebUI tool event fallback.
  - Preserve: existing tabs, ARIA regions, keyboard behavior and no-build constraints.

- `tests/unit/rag/test_openwebui_adapter.py`
  - Current state: verifies request normalization, response metadata, evidence links, stream chunks and error chunks.
  - What this story changes: add tool event chunk and audit summary coverage.
  - Preserve: fake service only, no external LLM/OpenWebUI.

- `tests/unit/rag/test_streaming.py`
  - Current state: verifies named SSE event payloads and reserved tool event redaction.
  - What this story changes: assert stricter safe fields and constructors if added.

- `tests/integration/api/test_openwebui_routes.py`
  - Current state: route-level OpenWebUI adapter tests with service overrides.
  - What this story changes: validate streaming tool event route contract.

- `tests/unit/web/test_governance_static_contract.py`, `tests/unit/web/test_sidecar_static_contract.py`, `tests/unit/web/sidecar_behavior_runner.js`
  - Current state: static and Node VM coverage for no-build governance behavior.
  - What this story changes: add tool event fallback parsing and stale clearing coverage.

- `README.md`, `docs/operations/local-development.md`, `docs/demo/governance-workbench.md`, `docs/demo/source-inspector-sidecar.md`, `docs/api/source-metadata.md`
  - Current state: document OpenWebUI citation evidence links, governance workbench, sidecar and local checks.
  - What this story changes: update OpenWebUI tool event bridge capability and limits after implementation.

### Previous Story Intelligence

- Story 9.1 added `CitationEvidenceLink` and final stream evidence metadata; follow the same same-origin, allowlisted metadata pattern.
- Story 9.1 explicitly left tool event streaming to 9.2 and function/tool bridge to 9.3. Do not collapse these boundaries.
- Story 8.6 Audit Explorer established safe backend query/export of agent/tool audit summaries. 9.2 fallback should link into those records instead of exposing raw tool output.
- Story 8.7 Review Queue established safe review item creation and eval candidate preview. Tool event fallback may seed review items only with safe identifiers/summaries.
- Story 8.3-8.7 repeatedly fixed stale UI state, unsafe copy/export payloads, malformed response handling and no-build frontend tests. Apply the same stale clearing discipline.
- Story 6.1-6.7 established Tool Registry, tools, Agent runtime, tool call persistence and final answer validation. Reuse those contracts.
- Recent commits show the current pattern: `packages/*` application/domain DTOs, thin FastAPI routes, SQLAlchemy only in storage, static governance UI, Python static contract tests, Node `vm` behavior runner, README/docs updates and full safety allowlists.

### Architecture and Security Guardrails

- Module ownership: stream payloads live in `packages/rag/streaming.py`; OpenAI-compatible formatting lives in `packages/rag/openwebui.py`; Agent execution lives in `packages/agent/*`; routes stay in `apps/api/routes`; no-build fallback lives in `apps/web/sidecar`.
- Auth boundary: Open WebUI is an entry surface, not permission authority. Tool permission remains in `ToolRegistry` and backend policies.
- Event boundary: tool event summaries are observations for UI/audit, not instructions and not authorization proof.
- Data boundary: never expose raw tool args, raw tool output, file content, retrieved excerpt, prompt, query, source_uri, object key, local path, provider payload, SQL, vectors, embeddings, token or secret.
- Scope boundary: this story implements safe event visibility/fallback only. It does not implement OpenWebUI function/tool invocation mapping, OpenWebUI plugin/fork strategy, multi-agent workflow, new tools, full custom frontend or source resolver changes.
- Compatibility boundary: keep `/query/stream`, `/chat/stream`, `/v1/chat/completions` and evidence link fields backward-compatible by adding optional fields only.
- Observability: record request_id, trace_id, tenant_id, user_id, agent_run_id if available, tool event counts, latency, status and error_code; avoid raw payload logging.

### Latest Technical Information

- Open WebUI official OpenAI-compatible provider guidance still centers model discovery on `GET /v1/models` and chat on `POST /v1/chat/completions`; keep this story on that compatibility surface rather than a custom Open WebUI fork. Source: https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-openai-compatible
- OpenAI-compatible streaming uses `data: {...}` chat completion chunks followed by `data: [DONE]`. Tool-call information in OpenAI-style streams is represented as structured delta/metadata, so this project should add safe optional tool event fields without breaking normal token chunks. Source: https://platform.openai.com/docs/api-reference/chat/create
- No new external dependency is required. Existing FastAPI, Pydantic v2, pytest and Node `vm` runner are sufficient.

### References

- `_bmad-output/planning-artifacts/epics.md#Story-9.2-Open-WebUI-Tool-Event-Streaming-Bridge`
- `_bmad-output/planning-artifacts/epics.md#Epic-9-Open-WebUI-企业级集成增强与轻量魔改路线`
- `_bmad-output/planning-artifacts/architecture.md#API-&-Communication-Patterns`
- `_bmad-output/planning-artifacts/architecture.md#Frontend-Architecture`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md#Experience-Principles`
- `project-context.md#12-Agent-规则`
- `project-context.md#13-Prompt-Injection-防护`
- `project-context.md#17-测试规则`
- `project-context.md#18-可观测性规则`
- `_bmad-output/implementation-artifacts/9-1-open-webui-citation-evidence-link-contract.md`
- `_bmad-output/implementation-artifacts/8-6-审计日志-explorer-与安全导出.md`
- `_bmad-output/implementation-artifacts/8-7-人工审阅队列与-eval-回流.md`
- `_bmad-output/implementation-artifacts/6-1-tool-registry-与工具治理模型.md`
- `_bmad-output/implementation-artifacts/6-4-react-agent-runtime-限制与重复动作检测.md`
- `_bmad-output/implementation-artifacts/6-6-tool-call-audit-persistence.md`
- `packages/rag/openwebui.py`
- `packages/rag/streaming.py`
- `packages/agent/runtime.py`
- `packages/agent/registry.py`
- `packages/agent/dto.py`
- `apps/api/routes/openwebui.py`
- `apps/api/routes/agent.py`
- `apps/api/service_dependencies.py`
- `apps/web/sidecar/sidecar.js`
- `apps/web/governance/index.html`
- `tests/unit/rag/test_openwebui_adapter.py`
- `tests/unit/rag/test_streaming.py`
- `tests/integration/api/test_openwebui_routes.py`
- `tests/unit/web/test_governance_static_contract.py`
- `tests/unit/web/test_sidecar_static_contract.py`
- `tests/unit/web/sidecar_behavior_runner.js`

## Validation Checklist

Validation Result: PASS（2026-06-09T20:39:33+08:00）

- [x] Story 明确 9.2 只实现 Open WebUI tool event streaming/fallback，不实现 9.3 function/tool bridge、Open WebUI fork/plugin 或完整自定义前端。
- [x] Acceptance Criteria 覆盖 stream chunk、fallback、安全拒绝、复用现有 DTO/Agent/audit、治理入口、docs/README 和测试。
- [x] Tasks 指向现有 `packages/rag/openwebui.py`、`packages/rag/streaming.py`、Agent runtime/registry、OpenWebUI route、sidecar/governance assets 和既有测试体系，避免重复实现。
- [x] Dev Notes 记录当前实现状态、9.1/8.6/8.7 仍为 review 的上下文、前序 story learnings、相关文件和 Open WebUI/OpenAI-compatible 最新兼容信息。
- [x] 明确禁止 raw tool arguments/output、prompt、query、answer、chunk content、source_uri、object key、本机路径、ACL、roles、permissions、token、secret 和 raw exception。
- [x] README 同步要求已写入 AC/Tasks；本次 create-story 只创建 story，不实现功能，因此不更新 README。

## Change Log

- 2026-06-09: Created comprehensive Story 9.2 developer context for Open WebUI tool event streaming bridge.
- 2026-06-09: Implemented Open WebUI safe tool event streaming/fallback bridge and marked story ready for review.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- `.venv\Scripts\python.exe -m pytest tests/unit/rag/test_openwebui_adapter.py tests/unit/rag/test_streaming.py -q` -> 20 passed.
- `.venv\Scripts\python.exe -m pytest tests/integration/api/test_openwebui_routes.py -q` -> 15 passed.
- `.venv\Scripts\python.exe -m pytest tests/unit/agent -q` -> 163 passed.
- `.venv\Scripts\python.exe -m pytest tests/unit/web/test_governance_static_contract.py tests/unit/web/test_sidecar_static_contract.py -q` -> 72 passed.
- `node tests/unit/web/sidecar_behavior_runner.js` -> passed.
- `.venv\Scripts\python.exe -m pytest tests/unit/test_architecture_boundaries.py tests/unit/test_readme_expectations.py -q` -> 25 passed.
- `.venv\Scripts\python.exe -m ruff check .` -> passed.
- `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed.
- `.venv\Scripts\python.exe -m pytest -q` -> 1064 passed.

### Completion Notes List

- Added strict safe tool event metadata helpers in `packages/rag/streaming.py`; unsafe raw arguments/output, observations, prompts, source locators, ACL/role/permission fields, tokens, and secrets are excluded by allowlist rather than relying on generic redaction.
- Extended `packages/rag/openwebui.py` to emit OpenAI-compatible `tool_event` chunks for `tool_call` / `tool_result`, keep token chunks metadata-free, attach final `metadata.tool_event_summary` counts, and record only safe stream audit counters while preserving `[DONE]` on audit failure.
- Added a framework-neutral `AgentToolEvent` / `AgentEventSink` in `packages/agent/runtime.py` so Agent runtime can emit safe call/result summaries without importing RAG/OpenWebUI modules or breaking package architecture boundaries.
- Added no-build sidecar/governance parsing for Open WebUI tool event fallback with explicit safe fields, Audit Explorer / Review Queue safe rendering, stale-state clearing, no storage, and no Source Evidence resolution for tool output.
- Kept Story 9.3 boundary intact: no client-provided Open WebUI tools/functions are accepted or executed, and Tool Registry remains backend-owned.
- Updated README and docs for Open WebUI tool event fields, fallback behavior, governance links, security limits, and validation commands.

### File List

- `_bmad-output/implementation-artifacts/9-2-open-webui-tool-event-streaming-bridge.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `apps/web/sidecar/sidecar.js`
- `docs/api/openwebui-tool-events.md`
- `docs/api/source-metadata.md`
- `docs/demo/governance-workbench.md`
- `docs/demo/source-inspector-sidecar.md`
- `docs/operations/local-development.md`
- `packages/agent/runtime.py`
- `packages/rag/__init__.py`
- `packages/rag/openwebui.py`
- `packages/rag/streaming.py`
- `README.md`
- `tests/integration/api/test_openwebui_routes.py`
- `tests/unit/agent/test_runtime.py`
- `tests/unit/rag/test_openwebui_adapter.py`
- `tests/unit/rag/test_streaming.py`
- `tests/unit/web/sidecar_behavior_runner.js`
- `tests/unit/web/test_governance_static_contract.py`
- `tests/unit/web/test_sidecar_static_contract.py`

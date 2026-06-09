---
baseline_commit: 87c1aca8063a641258abbbcf128accf485db2f51
---

# Story 8.6: 审计日志 Explorer 与安全导出

Status: review

生成时间：2026-06-09T18:17:35+08:00

## Story

As a 安全审计员,
I want 按 tenant、user、request_id、trace_id、action、resource 和 status 查询安全审计摘要,
so that 可以复盘上传、检索、问答、source resolve、Agent run 和 tool call 行为。

## Acceptance Criteria

1. **Audit Explorer 通过后端查询 tenant-scoped 审计摘要**
   - Given 审计员打开 `/governance` 的 Audit Explorer
   - When 按 user_id、request_id、trace_id、action、resource_type、resource_id、status、created_at window 和 limit 查询审计记录
   - Then UI 调用授权后端 API 展示 action、resource_type、resource_id、tenant_id、user_id、request_id、trace_id、latency_ms、status、error_code、created_at 和后端生成的安全摘要
   - And tenant_id 来自 `AuthenticatedRequestContext.auth.tenant_id`，前端不得提交或覆盖 tenant_id
   - And UI 不展示 secrets、access tokens、full prompts、full chunks、raw queries、provider payload、SQL、vectors、embeddings、source_uri、object key、本地绝对路径或 raw exception

2. **权限拒绝和不存在/空结果保持安全形态**
   - Given 用户没有 `audit:read`
   - When 调用 Audit Explorer list/export API
   - Then API 返回统一结构化拒绝，且不泄露审计表结构、报告目录、其它 tenant/user、raw SQL 或目标记录是否存在
   - And UI 必须立即清理旧列表、detail、copy/export state、selected row state 和 next-step state
   - And 空结果只显示安全空状态，不从本地缓存、URL、storage 或旧 DOM 推断记录

3. **Agent tool call 和 final answer validation 关联由后端安全抽取**
   - Given 查询结果包含 `agent.tool.execute`、`agent.run.*` 或 `agent.final_answer_validation`
   - When UI 渲染关联关系
   - Then 能展示 agent_run_id、tool_name、permission、status、error_code、latency_ms、safe argument/result summaries、steps_used、tool_calls_used 或 validation safe counts 中已存在且白名单允许的字段
   - And 关联数据来自 `audit_logs.resource_metadata`、`audit_logs.metadata`、`tool_calls` 或 `agent_runs` 的后端安全映射，前端不得自行 join 或推断
   - And 不泄露 tool 输入输出敏感全文、Agent 原始指令、observation 全文、文件路径、RAG query 原文或授权 excerpt

4. **安全导出由后端生成并审计**
   - Given 审计员导出当前查询结果
   - When 执行导出
   - Then 后端返回只包含白名单字段、查询条件摘要、generated_at、export_id、item_count、request_ids 和 trace_ids 的 JSON 导出 payload
   - And 导出 API 自身写入 `audit_logs`，action 建议为 `audit_explorer.export`，metadata 只包含 filter_summary、item_count、export_fields、format 和 safe status，不包含导出明细全文
   - And 前端 copy/download 只能使用后端返回的 export payload，不得直接导出 raw API envelope、raw audit metadata 或当前 DOM

5. **复用现有 no-build governance 前端和审计存储**
   - Given 当前已有 `/governance` shell、sidecar shared JS/CSS、`audit_logs` storage、`SqlAlchemyAuditPort`、Diagnostics/Eval Evidence API pattern 和 static contract tests
   - When 实现 Audit Explorer
   - Then 默认扩展现有 FastAPI route/service dependency、`packages.data.storage.audit_repositories`、`apps/web/governance/index.html`、`apps/web/sidecar/sidecar.js`、`apps/web/sidecar/sidecar.css` 和现有测试 runner
   - And 不新增 React、Next.js、Vite、Grafana replacement、Open WebUI fork、浏览器插件、前端权限判断器、前端 audit parser、第二套 audit 存储或未授权数据库直连

6. **文档、可访问性和验证闭环**
   - Given Audit Explorer 在桌面、平板和移动尺寸使用
   - When 用户通过键盘、屏幕阅读器、触控、复制或下载操作
   - Then 保留 governance tabs 的 ARIA/focus 行为，动态结果使用 `aria-live`，错误使用 alert region，状态含文本/符号而非只靠颜色，长 request/trace/resource/agent/tool IDs 安全换行或截断并可复制
   - And README、`docs/demo/governance-workbench.md`、`docs/demo/source-inspector-sidecar.md` 和 `docs/operations/local-development.md` 按本次能力同步说明入口、权限、字段白名单、限制和验证命令
   - And 新增/更新测试覆盖 DTO 白名单、repository filters、权限拒绝、导出审计、Agent 关联、安全字段过滤、stale clearing、copy/download allowlist、responsive/accessibility contract 和 README 期望

## Tasks / Subtasks

- [x] 设计 Audit Explorer DTO、异常和字段白名单（AC: 1, 2, 3, 4）
  - [x] 新建 `packages/audit` 或等价 application/domain 包，建议文件为 `dto.py`、`exceptions.py`、`service.py`；不要移动或重命名 `packages.common.audit` 的事件/port 定义。
  - [x] 定义 Pydantic v2 frozen DTO，例如 `AuditLogQueryRequest`、`AuditLogSummary`、`AuditLogAssociationSummary`、`AuditExportRequest`、`AuditExportPayload`、`AuditExplorerListResponse`。
  - [x] `AuditLogQueryRequest` 不接受 tenant_id；允许 user_id、request_id、trace_id、action、resource_type、resource_id、status、created_at_from、created_at_to、limit、include_associations。
  - [x] limit 后端限制在 1 到 200；export item_count 后端限制在 500 或更小配置值；时间窗口必须校验 from <= to。
  - [x] DTO 只暴露白名单字段：id、tenant_id、user_id、request_id、trace_id、action、resource_type、resource_id、status、latency_ms、error_code、created_at、safe_summary、association、safe_counts。
  - [x] 禁止字段和值进入响应和导出：query、answer、content、prompt、source_uri、object_key、file_path、local_path、sql、tsquery、vector、embedding、provider_raw_response、raw_exception、token、secret、access_token、api_key、authorization、本机绝对路径。
  - [x] 可预期错误使用领域异常和 stable error code，例如 `AUDIT_EXPLORER_FORBIDDEN`、`AUDIT_EXPLORER_INVALID_QUERY`、`AUDIT_EXPLORER_STORAGE_READ_FAILED`、`AUDIT_EXPLORER_EXPORT_FAILED`。

- [x] 扩展 audit storage 查询能力，不创建第二套审计表（AC: 1, 2, 4）
  - [x] 在 `packages/data/storage/audit_repositories.py` 增加 tenant-scoped query 方法，例如 `list_records(query: AuditLogStorageQuery)`，复用 `AuditLogRecord` 和 `AuditLogModel`。
  - [x] 查询必须始终包含 `AuditLogModel.tenant_id == context.auth.tenant_id`；user_id/action/resource/status/time filters 只能在该范围内追加。
  - [x] request_id 和 trace_id 支持单独或组合过滤；组合过滤必须是 AND，不要把任一 ID 当作跨 tenant 查找口令。
  - [x] 排序使用 `created_at desc, id desc` 或明确稳定顺序；返回 bounded limit，不返回 raw SQL、database exception 或绝对路径。
  - [x] 评估并新增 Alembic index migration，建议覆盖 `tenant_id + created_at`、`tenant_id + action + created_at`、`tenant_id + resource_type + created_at`；如果决定不加 migration，必须在 Dev Agent Record 说明原因并有测试覆盖现有索引行为。
  - [x] 保留 `list_by_request_id`、`list_by_trace_id` 供 Diagnostics 继续使用，不破坏现有 diagnostics tests。

- [x] 实现 Audit Explorer application service（AC: 1, 2, 3, 4）
  - [x] Service 输入 `AuthenticatedRequestContext` 和 DTO query/export request，不接收前端传入的 tenant/user/roles/permissions。
  - [x] 权限入口集中放在 `packages/auth/policies.py`，新增 `AUDIT_EXPLORER_READ_PERMISSIONS = {"audit:read"}` 和 `has_audit_explorer_read_permission()` 或复用同等清晰 helper。
  - [x] 将 `AuditLogRecord.resource_metadata` 和 `metadata` 映射为安全摘要时只读取白名单 key；即使 `redact_mapping()` 已处理，也不能把原始 metadata 透传给 API/UI/export。
  - [x] safe_summary 建议包含 counts 和 labels：metadata_count、resource_metadata_count、role_count、permission_count、citation_count、context_item_count、context_source_count、result_count、event_count、top_k、token_usage safe counts、termination_reason、failure_stage、auth_method。
  - [x] Agent 关联：优先从 `resource_metadata.agent_run_id` 或 `metadata.agent_run_id` 读取；tool call 详情优先从 `tool_calls` 安全表补齐 `permission`、`arguments_summary`、`result_summary`，必要时扩展 `ToolCallRepository` 按 tenant/request_id/trace_id/tool_name 查询。
  - [x] 如果需要给 `agent.tool.execute` audit metadata 增加 `agent_run_id`，必须同步更新 `packages/agent/registry.py` 和相关单元测试，且只加入 agent_run_id，不加入 raw arguments/output。
  - [x] list 操作可记录轻量 audit event `audit_explorer.list`，但不得因 audit 写失败阻断只读查询；export 操作必须尝试记录 `audit_explorer.export`，失败时返回受控错误或记录结构化 warning，测试需固定预期。

- [x] 暴露薄 FastAPI route 和 service dependency（AC: 1, 2, 4）
  - [x] 新增 `apps/api/routes/audit_explorer.py`，建议 endpoint：`GET /audit/logs` 查询列表，`POST /audit/export` 生成安全 JSON export payload。
  - [x] Route 只做 dependency、DTO、service call、`success_response`；不得拼 SQL、直接操作 SQLAlchemy session、读取 raw metadata 或判断权限。
  - [x] 在 `apps/api/main.py` 注册 router，在 `apps/api/service_dependencies.py` 注入 `AuditExplorerService`、`AuditLogRepository`、可选 `ToolCallRepository`/`AgentRunRepository` 和 `SqlAlchemyAuditPort(auto_commit=True)`。
  - [x] structured error details 只包含 request_id、trace_id、filter_summary、error_code、stage，不包含 SQL、stack、raw metadata 或资源存在性提示。

- [x] 升级 `/governance` Audit Explorer 面板（AC: 1, 2, 3, 4, 5, 6）
  - [x] 替换 `governance-view-audit-explorer` placeholder，加入 filter form：user_id、request_id、trace_id、action、resource_type、resource_id、status、created_at_from、created_at_to、limit。
  - [x] 表单不提供 tenant_id、roles、permissions、database path、raw SQL、metadata key/value 或 free-form export filename 输入。
  - [x] 增加 results region、association/detail region、next-step region、copy/export buttons、alert/live region 复用现有 shared helper 模式。
  - [x] 请求开始前立即清理旧结果、detail、copy/export state；403/404/network/malformed response 必须清理旧授权数据。
  - [x] Audit row 渲染只使用 `SAFE_AUDIT_LOG_FIELDS`；association row 只使用 `SAFE_AUDIT_ASSOCIATION_FIELDS`；export 只使用后端返回 payload。
  - [x] 保留 Document Review、Source Evidence、Retrieval Diagnostics、Eval Evidence 和 Review Queue 行为；切换 tab 不自动拉取 audit 记录。

- [x] 扩展 shared `sidecar.js` 的 Audit Explorer allowlist、fetch/render 和 export（AC: 1, 2, 3, 4, 5, 6）
  - [x] 添加 `SAFE_AUDIT_LOG_FIELDS`、`SAFE_AUDIT_ASSOCIATION_FIELDS`、`SAFE_AUDIT_EXPORT_FIELDS`，并纳入 `GOVERNANCE_SAFE_FIELDS.auditSummary`。
  - [x] 新增 `AUDIT_EXPLORER_LOGS_ENDPOINT = "/audit/logs"` 和 `AUDIT_EXPLORER_EXPORT_ENDPOINT = "/audit/export"`；复用 `buildHeaders()`、`pickFields()`、`safeFilenamePart()`、`copyText()` 和现有 download pattern。
  - [x] Export/copy 只能序列化后端 export payload 的白名单字段；不得从 rendered row 或 raw list response 拼导出。
  - [x] 下载文件名使用后端 `export_id`、request_id/trace_id 或固定 `audit-export` 的 sanitized 片段；不得包含 `..`、`/`、`\`、`:`、空白路径或用户提供的未清洗字符串。
  - [x] 处理并发请求 token，避免旧请求覆盖新 Audit Explorer 结果；沿用 Eval Evidence request token 模式。
  - [x] 不读取或写入 `localStorage`、`sessionStorage`、cookie、URL history 或 console log。

- [x] 扩展 CSS 为紧凑、可扫描的 Audit Explorer 工作区（AC: 1, 3, 6）
  - [x] 更新 `apps/web/sidecar/sidecar.css`，新增 audit filter grid、audit row、association row、export summary、safe count chips 样式。
  - [x] 状态必须有文本和符号，不只依赖颜色；长 action/resource/request/trace/agent/tool IDs 使用 `overflow-wrap: anywhere`。
  - [x] 移动端保持单列，按钮文本不溢出，列表/detail 不遮挡后续内容。
  - [x] 不引入 hero、营销卡片、装饰渐变、嵌套卡片或大面积单色主题。

- [x] 后端测试覆盖 repository、service、安全边界和导出审计（AC: 1, 2, 3, 4）
  - [x] 新增 `tests/unit/audit` 或 `tests/unit/audit_explorer` DTO/service tests，覆盖 query validation、safe summary extraction、forbidden fields/value redaction、Agent association、export payload allowlist。
  - [x] 扩展或新增 `tests/integration/storage/test_governance_repositories.py` / `test_audit_log_repositories.py`，覆盖 tenant-scoped filters、created_at window、limit bounds、request+trace AND 过滤、storage error safe details。
  - [x] 新增 `tests/integration/api/test_audit_explorer_routes.py`，覆盖 authorized list/export、permission denial、malformed query、empty result、path/value leakage absence、export writes audit event 和 response envelope。
  - [x] 使用 in-memory/SQLite/temp fixtures；不真实调用外部 LLM、embedding、browser、PostgreSQL、Redis、MinIO 或 Open WebUI。
  - [x] 保留现有 Diagnostics、Eval Evidence、Agent tool audit tests 不回归。

- [x] 前端静态契约和 JS 行为测试（AC: 1, 2, 3, 4, 5, 6）
  - [x] 扩展 `tests/unit/web/test_governance_static_contract.py`，验证 Audit Explorer controls、result/detail/export/next-step regions、ARIA live/alert、安全字段白名单、forbidden fragments absence 和 responsive CSS。
  - [x] 扩展 `tests/unit/web/test_sidecar_static_contract.py`，确认 audit allowlists 不包含 query、answer、content、prompt、source_uri、object_key、SQL、vectors、embeddings、provider payload、token、secret、raw_exception。
  - [x] 扩展 `tests/unit/web/sidecar_behavior_runner.js`，覆盖 audit list rendering、association rendering、permission failure stale clearing、new lookup clears old copy/export、backend export allowlist、sanitized filename、export endpoint called、tab switch no auto lookup。
  - [x] 不引入 Playwright、Node build pipeline 或浏览器自动化；现有 Node `vm` runner 应足够覆盖 no-build 行为。

- [x] 更新 README 和 demo/operations docs（AC: 6）
  - [x] 更新 `docs/demo/governance-workbench.md`，说明 Audit Explorer 支持授权审计摘要查询、安全导出、Agent/tool/final validation 关联、权限要求、字段白名单和限制。
  - [x] 更新 `docs/demo/source-inspector-sidecar.md`，说明 sidecar 不直接承载完整 Audit Explorer；治理工作台通过后端 API 获取 safe audit evidence。
  - [x] 更新 `docs/operations/local-development.md`，加入本地调用示例、权限 header、验证命令和安全边界。
  - [x] 更新 README Build Status、Governance Workbench、Current Limits、API/verification 段落；不得宣称 Review Queue、长期审计归档、SIEM 集成或跨租户审计已完成。
  - [x] Dev Agent Record 填写实现决策、验证结果和文件列表。

- [x] 建议验证命令（AC: 1-6）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/audit tests/unit/audit_explorer -q`（按实际测试路径替换）
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/api/test_audit_explorer_routes.py tests/integration/storage/test_governance_repositories.py -q`（按实际测试路径替换）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/agent tests/integration/storage/test_tool_call_repositories.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/web/test_governance_static_contract.py tests/unit/web/test_sidecar_static_contract.py -q`
  - [x] `node tests/unit/web/sidecar_behavior_runner.js`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/test_readme_expectations.py -q`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`

## Dev Notes

### Current Repository State

- Current HEAD at story creation: `87c1aca fix(governance): address eval evidence review findings`.
- Story 8.1 established the no-build `/governance` shell with six stable tabs and ARIA tab behavior.
- Story 8.2 implemented Document Review with backend lifecycle APIs and stale state clearing.
- Story 8.3 implemented Source Evidence through `POST /sources/resolve`; pasted evidence identifiers remain untrusted.
- Story 8.4 implemented Retrieval Diagnostics safe timeline through `POST /diagnostics/resolve`; diagnostics already reads `audit_logs` by request/trace.
- Story 8.5 implemented Eval Evidence with backend report parsing, strict allowlists, report copy/download and safe stale clearing.
- Audit Explorer is currently only a placeholder in `apps/web/governance/index.html`. The backend already writes audit events to `audit_logs`; repository queries are currently limited to request_id/trace_id for Diagnostics.
- Existing frontend remains static HTML/CSS/JS served by FastAPI. There is no React/Next/Vite/package manifest and no Node build step.

### Existing Files To Read Before Implementation

- `packages/common/audit.py`
  - Current state: defines `AuditEvent`, `AuditResource`, `AuditStatus`, `AuditPort`, `InMemoryAuditPort`; metadata/resource_metadata are redacted through `redact_mapping()`.
  - What this story changes: usually no change; use these contracts to write list/export audit events.
  - Preserve: event contract remains provider-neutral and storage-free; do not add UI/export DTOs here.

- `packages/common/logging.py`
  - Current state: central sensitive key/value redaction for logs and audit metadata.
  - What this story changes: no direct change expected unless tests expose missing redaction patterns.
  - Preserve: Audit Explorer must still use response/export allowlists; redaction is a defense layer, not a reason to return raw metadata.

- `packages/data/storage/audit_models.py`
  - Current state: `audit_logs` table has id, timestamps, tenant_id, user_id, created_by, status, request_id, trace_id, action, resource_type, resource_id, resource_metadata, latency_ms, error_code, metadata.
  - What this story changes: likely add composite indexes through a new Alembic migration, not a new table.
  - Preserve: existing columns and separate indexes used by current tests/migrations.

- `packages/data/storage/audit_repositories.py`
  - Current state: writes `AuditEvent` and reads by tenant+request_id or tenant+trace_id for Diagnostics.
  - What this story changes: add a general tenant-scoped query with bounded filters and stable ordering.
  - Preserve: `list_by_request_id` and `list_by_trace_id` behavior for `DiagnosticsService`.

- `packages/auth/policies.py`
  - Current state: permission helpers exist for document manage, RAG query, agent run, diagnostics and eval evidence.
  - What this story changes: add audit explorer read permission helper or explicit reuse of `audit:read`.
  - Preserve: LLM/frontend must not decide permissions.

- `packages/diagnostics/service.py` and `packages/diagnostics/dto.py`
  - Current state: demonstrate safe audit/retrieval record aggregation, failure stage mapping, count allowlists and structured next steps.
  - What this story changes: no direct change expected, but Audit Explorer can reuse patterns for safe count extraction and no raw metadata.
  - Preserve: diagnostics semantics and tests.

- `packages/eval/service.py`, `packages/eval/dto.py`, `apps/api/routes/eval_evidence.py`
  - Current state: demonstrate backend-only evidence parsing, permission audit, export allowlists and thin route patterns.
  - What this story changes: no direct change expected.
  - Preserve: Eval Evidence report browsing remains separate from Audit Explorer.

- `packages/agent/registry.py`
  - Current state: `agent.tool.execute` audit metadata includes tool_name, permission, argument_keys, timeout_seconds, rate_limit/status/result_keys, but persisted tool call records carry agent_run_id and safe argument/result summaries.
  - What this story changes: Audit Explorer may need backend enrichment from `tool_calls`; if adding agent_run_id to tool audit metadata, keep it safe and test it.
  - Preserve: never include raw tool arguments/output in audit metadata or export.

- `packages/agent/storage/models.py` and `packages/agent/storage/repositories.py`
  - Current state: `tool_calls` table stores agent_run_id, request_id, trace_id, tenant_id, user_id, tool_name, permission, status, latency, error_code, arguments_summary, result_summary; repository filters tenant/user/agent_run/tool/status/time.
  - What this story changes: likely add request_id/trace_id filters or helper query for Audit Explorer association enrichment.
  - Preserve: tenant/user scoping and safe summaries.

- `packages/agent/service.py`, `packages/agent/runtime.py`, `packages/agent/final_answer.py`
  - Current state: Agent run and final answer validation audit events use resource type `agent_run`; final answer validation includes agent_run_id in resource metadata.
  - What this story changes: usually no direct change unless association gaps are found.
  - Preserve: final answer text and raw observations never enter Audit Explorer payloads.

- `apps/api/service_dependencies.py`
  - Current state: central service factories include Diagnostics and Eval Evidence dependencies with `AuditLogRepository` and `SqlAlchemyAuditPort`.
  - What this story changes: add Audit Explorer service dependency.
  - Preserve: route code stays thin; settings/session factory reuse remains consistent.

- `apps/web/governance/index.html`
  - Current state: Audit Explorer panel is placeholder text; Eval Evidence panel is implemented.
  - What this story changes: replace placeholder with filter form, result/detail/export regions and buttons.
  - Preserve: six governance tabs, existing panel IDs, local/test auth helper, no-storage behavior and existing tab switch behavior.

- `apps/web/sidecar/sidecar.js`
  - Current state: shared frontend behavior, endpoint constants, safe field allowlists, fetch/render/copy/download helpers and test exports.
  - What this story changes: add Audit Explorer allowlists, API helpers, render helpers, export helpers and test exports.
  - Preserve: no local/session storage, no cookies/history/console logs, immediate stale clearing before async fetch and request token race protection.

- `apps/web/sidecar/sidecar.css`
  - Current state: compact operational styling for governance workbench, diagnostics and eval evidence.
  - What this story changes: add audit list/association/export styles.
  - Preserve: no nested cards, no marketing layout, no text overlap, mobile single-column fallback.

- `tests/unit/web/test_governance_static_contract.py`, `tests/unit/web/test_sidecar_static_contract.py`, `tests/unit/web/sidecar_behavior_runner.js`
  - Current state: cover governance shell, Source Evidence, Document Review, Retrieval Diagnostics, Eval Evidence, safe allowlists and stale clearing.
  - What this story changes: add Audit Explorer static and behavior tests.
  - Preserve: no browser automation or build step.

- `README.md`, `docs/demo/governance-workbench.md`, `docs/demo/source-inspector-sidecar.md`, `docs/operations/local-development.md`
  - Current state: document current governance workbench through Eval Evidence; README says Audit Explorer remains later Epic 8 work.
  - What this story changes: update current capability and limitations after implementation.
  - Preserve: do not claim Review Queue is complete.

### Previous Story Intelligence

- Story 8.1 review caught governance entry identity, navigation wiring, keyboard behavior, responsive overflow and stale alert issues. Preserve tablist semantics and focus behavior.
- Story 8.2 review caught unsafe detail failure responses, nested unsafe error summaries, deleted latest-version selection, cursor bounds and stale Document Review UI state. Apply the same stale clearing discipline to audit list/detail/export state.
- Story 8.3 review caught stale evidence copyability during new resolves, direct evidence link parsing, pasted `request_id` being reused as current `X-Request-ID`, and unsafe metadata rendering. Audit query inputs are lookup filters only, not current request headers.
- Story 8.4 review caught nested stage error leakage, false success statuses without backend evidence, overly broad count fields, stale diagnostics report export and stale backend diagnostics DOM. Audit Explorer must use fixed allowlists and must not mark association evidence present unless backend provides it.
- Story 8.5 review caught unsafe string leakage, malformed report list blocking, stale selected filename and overlapping request rendering. Audit Explorer should reject unsafe values, skip/handle malformed metadata safely, and use request token guards for async UI.
- Recent frontend strategy is intentionally no-build: Python static contract tests plus Node `vm` behavior runner. Reuse this pattern.
- Governance workbench is presentation only. Backend AuthContext, RBAC, ACL, eval report parsing, audit export and future review APIs remain authoritative.

### Architecture and Security Guardrails

- Module ownership: API route in `apps/api/routes`, application/domain DTOs in `packages/audit` or equivalent, storage query in `packages/data/storage`, static UI in `apps/web/governance`, shared JS/CSS in `apps/web/sidecar`.
- Auth boundary: all data requests use `AuthenticatedRequestContext`; front-end payload must never include tenant_id/roles/permissions and must not override user identity.
- Query boundary: all audit reads are tenant-scoped before additional filters; no cross-tenant mode in this story.
- Export boundary: export is a backend action that returns a generated safe JSON payload and records its own audit event. Frontend download is only a transport for that safe payload.
- Redaction boundary: never return raw `AuditLogRecord.metadata` or `resource_metadata`. Extract explicitly allowed keys and safe numeric/string labels only.
- Agent boundary: Agent/tool relationships are data evidence, not frontend inference. Use persisted `agent_runs`/`tool_calls` safe summaries or audited safe IDs only.
- Scope boundary: this story does not implement SIEM integration, long-term audit archive, async export jobs, CSV/spreadsheet export, admin cross-tenant search, Review Queue, or policy editing.
- Observability: service and route should record request_id, trace_id, user_id, tenant_id, filters safe summary, item_count, export_id, latency_ms, status and error_code without logging audit record contents.

### Latest Technical Information

- No new external framework is required. Current `pyproject.toml` already pins FastAPI, Pydantic v2, SQLAlchemy 2.x, pytest, ruff and mypy; follow existing patterns rather than adding dependencies.
- MDN documents `aria-live` for announcing dynamic updates without moving focus; use polite live updates for successful list/export loads and alert/assertive behavior for errors.
- MDN Clipboard `writeText()` is Promise-based and only available in secure contexts; keep existing copy fallback behavior and tests for unavailable clipboard.
- MDN `URL.createObjectURL()` creates Blob URLs and should be paired with `URL.revokeObjectURL()` after download; preserve existing download cleanup pattern.
- WAI-ARIA APG Tabs Pattern expects stable `tablist`/`tab`/`tabpanel` roles, `aria-selected`, `aria-controls`, keyboard navigation and focus behavior; do not regress the existing governance tabs.

### References

- `_bmad-output/planning-artifacts/epics.md#Story-8.6-审计日志-Explorer-与安全导出`
- `_bmad-output/planning-artifacts/epics.md#Epic-8-企业审阅治理前端与可信证据工作台`
- `_bmad-output/planning-artifacts/architecture.md#API-Architecture`
- `_bmad-output/planning-artifacts/architecture.md#Frontend-Architecture`
- `_bmad-output/planning-artifacts/architecture.md#Process-Patterns`
- `_bmad-output/planning-artifacts/architecture.md#Requirements-Coverage-Validation`
- `project-context.md#18-可观测性规则`
- `project-context.md#16-权限规则`
- `_bmad-output/implementation-artifacts/8-1-审阅治理工作台信息架构与前端边界.md`
- `_bmad-output/implementation-artifacts/8-2-文档生命周期审阅看板.md`
- `_bmad-output/implementation-artifacts/8-3-citation-与-source-evidence-审阅器.md`
- `_bmad-output/implementation-artifacts/8-4-retrieval-diagnostics-安全时间线.md`
- `_bmad-output/implementation-artifacts/8-5-eval-evidence-与质量回归工作区.md`
- `packages/common/audit.py`
- `packages/common/logging.py`
- `packages/data/storage/audit_models.py`
- `packages/data/storage/audit_repositories.py`
- `packages/auth/policies.py`
- `packages/diagnostics/service.py`
- `packages/eval/service.py`
- `packages/agent/registry.py`
- `packages/agent/storage/models.py`
- `packages/agent/storage/repositories.py`
- `apps/api/service_dependencies.py`
- `apps/web/governance/index.html`
- `apps/web/sidecar/sidecar.js`
- `apps/web/sidecar/sidecar.css`
- `tests/unit/web/test_governance_static_contract.py`
- `tests/unit/web/test_sidecar_static_contract.py`
- `tests/unit/web/sidecar_behavior_runner.js`
- MDN `aria-live`: https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Attributes/aria-live
- MDN Clipboard `writeText()`: https://developer.mozilla.org/en-US/docs/Web/API/Clipboard/writeText
- MDN `URL.createObjectURL()`: https://developer.mozilla.org/en-US/docs/Web/API/URL/createObjectURL_static
- WAI-ARIA Tabs Pattern: https://www.w3.org/WAI/ARIA/apg/patterns/tabs/

## Validation Checklist

Validation Result: PASS（2026-06-09T18:17:35+08:00）

- [x] Story 明确 8.6 只实现 Audit Explorer 和安全 JSON 导出，不扩展 Review Queue、SIEM、跨租户审计或长期归档。
- [x] Acceptance Criteria 覆盖审计查询、权限拒绝、Agent/tool/final validation 关联、后端导出审计、no-build 前端、可访问性、docs/tests。
- [x] Tasks 指向现有 audit storage、FastAPI route/service dependency、governance HTML、sidecar JS/CSS 和测试文件，避免重建前端栈或审计存储。
- [x] Dev Notes 记录 8.1-8.5 learnings、recent git patterns、audit/tool storage shapes、unsafe field 防线和 no new framework 约束。
- [x] 明确禁止 raw query、answer、chunk content、prompt、SQL、vectors、embeddings、provider payload、token、secret、source_uri、object key、本机路径和 raw exception。
- [x] README 同步要求已写入 AC/Tasks；本次 create-story 只创建 story，不实现功能，因此不更新 README。

## Change Log

- 2026-06-09: Created comprehensive Story 8.6 developer context for Audit Explorer and safe export.
- 2026-06-09: Implemented Audit Explorer backend APIs, safe export, no-build governance UI, tests, migrations, and docs.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- 2026-06-09: Added failing unit/integration/static/behavior tests for Audit Explorer DTO/service, repository filters, API routes, governance controls, stale clearing, and export allowlists.
- 2026-06-09: Implemented `packages.audit` DTO/service boundary, `GET /audit/logs`, `POST /audit/export`, tenant-scoped repository query, audit indexes, and ToolCall request/trace filters.
- 2026-06-09: Implemented governance Audit Explorer HTML, sidecar JS/CSS allowlists, backend export copy/download, responsive styles, and no-storage stale clearing.
- 2026-06-09: Validation note: a single combined pytest process containing API route tests followed by `tests/unit/agent` produced 3 caplog-only warning capture failures; isolated `tests/unit/agent tests/integration/storage/test_tool_call_repositories.py` passed. This appears to be logging configuration order interaction, not a behavior regression.
- 2026-06-09: Full regression suite subsequently passed with `.venv\Scripts\python.exe -m pytest -q` (`1032 passed`).

### Implementation Plan

- Keep backend route thin: FastAPI route builds DTOs and delegates to `AuditExplorerService`.
- Keep tenant/user scope backend-owned: service receives `AuthenticatedRequestContext`, and repository queries always bind tenant before filters.
- Treat audit metadata as untrusted: extract only explicit safe summary/count/association fields; never pass raw metadata/resource metadata to API, UI, or export.
- Make export backend-authoritative: frontend copy/download calls `POST /audit/export` and serializes only the returned allowlisted export payload.
- Preserve no-build frontend strategy: extend existing governance HTML, shared sidecar JS/CSS, Python static contract tests, and Node VM behavior runner.

### Completion Notes List

- Added `packages.audit` application package with frozen DTOs, stable domain errors, safe summary extraction, permission checks, Agent/tool/final-validation association summaries, and backend-generated export payloads.
- Extended `AuditLogRepository` with bounded tenant-scoped filters and added composite audit log indexes for tenant+created/action/resource query paths.
- Added Audit Explorer API routes and dependency assembly; `GET /audit/logs` rejects tenant/roles/permissions query overrides and `POST /audit/export` writes `audit_explorer.export` audit events.
- Extended ToolCall query DTO/repository to support request_id and trace_id association enrichment.
- Replaced `/governance` Audit Explorer placeholder with filter form, result/detail/next-step regions, copy/download export controls, ARIA live regions, and responsive CSS.
- Extended sidecar JS allowlists and behavior for safe audit rendering, stale clearing, backend export copy/download, sanitized filenames, and tab-switch no auto-lookup.
- Updated README and demo/operations docs for Audit Explorer entry, permissions, allowlists, limitations, and verification commands.
- Validation passed:
  - `.venv\Scripts\python.exe -m pytest tests/unit/audit_explorer -q`
  - `.venv\Scripts\python.exe -m pytest tests/integration/api/test_audit_explorer_routes.py tests/integration/storage/test_audit_log_repositories.py -q`
  - `.venv\Scripts\python.exe -m pytest tests/unit/agent tests/integration/storage/test_tool_call_repositories.py -q`
  - `.venv\Scripts\python.exe -m pytest tests/unit/web/test_governance_static_contract.py tests/unit/web/test_sidecar_static_contract.py -q`
  - `node tests/unit/web/sidecar_behavior_runner.js`
  - `.venv\Scripts\python.exe -m pytest tests/unit/test_readme_expectations.py -q`
  - `.venv\Scripts\python.exe -m pytest -q`
  - `.venv\Scripts\python.exe -m ruff check .`
  - `.venv\Scripts\python.exe -m mypy apps packages tests`

### File List

- README.md
- _bmad-output/implementation-artifacts/8-6-审计日志-explorer-与安全导出.md
- _bmad-output/implementation-artifacts/sprint-status.yaml
- apps/api/main.py
- apps/api/routes/audit_explorer.py
- apps/api/service_dependencies.py
- apps/web/governance/index.html
- apps/web/sidecar/sidecar.css
- apps/web/sidecar/sidecar.js
- docs/demo/governance-workbench.md
- docs/demo/source-inspector-sidecar.md
- docs/operations/local-development.md
- migrations/versions/20260609_0012_audit_explorer_indexes.py
- packages/agent/dto.py
- packages/agent/storage/repositories.py
- packages/audit/__init__.py
- packages/audit/dto.py
- packages/audit/exceptions.py
- packages/audit/service.py
- packages/auth/policies.py
- packages/data/storage/audit_models.py
- packages/data/storage/audit_repositories.py
- tests/integration/api/test_audit_explorer_routes.py
- tests/integration/storage/test_audit_log_repositories.py
- tests/unit/audit_explorer/test_audit_explorer_service.py
- tests/unit/web/sidecar_behavior_runner.js
- tests/unit/web/test_governance_static_contract.py
- tests/unit/web/test_sidecar_static_contract.py


---
baseline_commit: c1e892ce9316
---

# Story 8.7: 人工审阅队列与 Eval 回流

Status: review

生成时间：2026-06-09T19:21:00+08:00

## Story

As a 交付顾问,
I want 把可疑回答、低置信 citation、no-answer 和权限边界案例加入人工审阅队列,
so that 演示中发现的问题可以转化为可执行 eval 回归样本。

## Acceptance Criteria

1. **Review item 创建只保存安全证据摘要**
   - Given 用户在 Source Evidence、Retrieval Diagnostics、Eval Evidence 或 Audit Explorer 中发现问题
   - When 创建 review item
   - Then 后端保存 `item_type`、`severity`、`status`、`request_id`、`trace_id`、safe identifiers、`created_by`、`tenant_id` 和安全摘要
   - And `tenant_id`、`created_by`、当前用户和权限只来自 `AuthenticatedRequestContext`，前端不得提交或覆盖
   - And 不保存 prompt、raw query、answer 全文、chunk 全文、provider raw response、tool observation 全文、token、secret、source_uri、object key、本机绝对路径或未授权 excerpt

2. **Review Queue 支持 tenant-scoped 查询与安全空/拒绝状态**
   - Given 审阅员打开 `/governance` 的 Review Queue
   - When 按 `item_type`、`severity`、`status`、`request_id`、`trace_id`、`source_view`、`created_at` window 和 bounded `limit` 查询
   - Then API 只返回当前 tenant 范围内的 item 摘要、safe identifiers、safe summary、状态历史摘要和 next steps
   - And 用户无 `review:read` 或跨 tenant 查询时返回统一结构化拒绝，不泄露目标 item、source evidence、eval report 或 audit record 是否存在
   - And UI 必须在新查询、失败、403/404、malformed response 时清理旧列表、detail、eval candidate、copy/export state 和选中项

3. **状态转换被后端校验并写入 audit**
   - Given 审阅员处理 review item
   - When 标记为 `accepted`、`rejected`、`needs_followup` 或 `converted_to_eval_case`
   - Then 状态转换由后端 application service 校验，写入状态历史并记录 audit event
   - And 修改状态需要 `review:write`，转换 eval candidate 需要 `review:write` 和 `eval:write` 或项目定义的等价权限
   - And 状态转换不得由前端、LLM、prompt 或 URL 参数决定权限

4. **Eval candidate 只生成脱敏候选，不直接进入正式 dataset**
   - Given review item 被转换为 eval candidate
   - When 后端生成候选 case
   - Then 候选只包含 synthetic-safe 或脱敏字段：`candidate_id`、`source_review_item_id`、`case_type`、safe identifiers、failure stage、安全指标计数、expected behavior 摘要、request/trace IDs
   - And 必须标记 `requires_human_confirmation=true`，不得自动写入 `tests/eval/datasets/*.json` 或正式 eval dataset
   - And 后端和文档都说明该机制不是自动采集真实企业数据

5. **复用现有 governance/no-build 前端与审计/eval 模式**
   - Given 当前已有 `/governance` shell、Review Queue placeholder、sidecar shared JS/CSS、Audit Explorer、Eval Evidence、Source Evidence 和 Diagnostics API patterns
   - When 实现 Review Queue
   - Then 默认扩展现有 FastAPI route/service dependency、SQLAlchemy storage、`apps/web/governance/index.html`、`apps/web/sidecar/sidecar.js`、`apps/web/sidecar/sidecar.css` 和现有 static/Node VM 测试 runner
   - And 不新增 React、Next.js、Vite、Grafana replacement、Open WebUI fork、浏览器插件、第二套前端状态库、前端权限判断器、前端 eval writer 或未授权数据库直连

6. **文档、可访问性和验证闭环**
   - Given Review Queue 在桌面、平板和移动尺寸使用
   - When 用户通过键盘、屏幕阅读器、触控、复制、状态更新或候选生成操作
   - Then 保留 governance tabs 的 ARIA/focus 行为，动态结果使用 `aria-live`，错误使用 alert region，状态含文本/符号而非只靠颜色，长 request/trace/document/version/chunk/review IDs 安全换行或截断并可复制
   - And README、`docs/demo/governance-workbench.md`、`docs/demo/source-inspector-sidecar.md` 和 `docs/operations/local-development.md` 按本次能力同步入口、权限、字段白名单、限制、人工确认边界和验证命令
   - And 新增/更新测试覆盖 DTO 白名单、repository tenant filters、权限拒绝、状态转换、audit 写入、eval candidate 脱敏、stale clearing、copy/export allowlist、responsive/accessibility contract 和 README 期望

## Tasks / Subtasks

- [x] 设计 Review Queue DTO、状态机、异常和字段白名单（AC: 1, 2, 3, 4）
  - [x] 新建 `packages/review` 或等价 application/domain 包，建议文件为 `dto.py`、`exceptions.py`、`service.py`；不要把 review DTO 放入 `packages.audit` 或 `packages.eval`。
  - [x] 定义 Pydantic v2 frozen DTO，例如 `ReviewItemCreateRequest`、`ReviewItemQueryRequest`、`ReviewItemSummary`、`ReviewItemStatusUpdateRequest`、`ReviewItemStatusHistoryEntry`、`EvalCandidatePreview`、`ReviewQueueListResponse`。
  - [x] `ReviewItemCreateRequest` 不接受 `tenant_id`、`created_by`、`user_id`、`roles` 或 `permissions`；这些字段由 `AuthenticatedRequestContext` 注入。
  - [x] `item_type` 建议限定为 `questionable_answer`、`low_confidence_citation`、`no_answer`、`acl_boundary`、`prompt_injection`、`tool_output`、`eval_failure`。
  - [x] `severity` 建议限定为 `low`、`medium`、`high`、`critical`；默认 `medium`。
  - [x] `status` 建议限定为 `open`、`accepted`、`rejected`、`needs_followup`、`converted_to_eval_case`；创建时只能是 `open`。
  - [x] safe identifiers 建议包含 `document_id`、`version_id`、`chunk_id`、`page_start`、`page_end`、`citation_ref`、`eval_report_filename`、`eval_case_id`、`audit_log_id`、`agent_run_id`、`tool_call_id`，但必须通过安全文本校验。
  - [x] 禁止字段和值进入存储/API/UI/export：`prompt`、`query`、`answer`、`content`、`chunk_text`、`source_uri`、`object_key`、`file_path`、`local_path`、`sql`、`tsquery`、`vector`、`embedding`、`provider_raw_response`、`tool_observation`、`raw_exception`、`token`、`secret`、`access_token`、`api_key`、`authorization`、本机绝对路径。
  - [x] 可预期错误使用领域异常和 stable error code，例如 `REVIEW_QUEUE_FORBIDDEN`、`REVIEW_QUEUE_INVALID_ITEM`、`REVIEW_QUEUE_INVALID_STATUS_TRANSITION`、`REVIEW_QUEUE_NOT_FOUND`、`REVIEW_QUEUE_STORAGE_READ_FAILED`、`REVIEW_QUEUE_STORAGE_WRITE_FAILED`、`REVIEW_QUEUE_EVAL_CANDIDATE_FAILED`。

- [x] 新增 tenant-scoped Review Queue 存储，不复用 audit_logs 当业务表（AC: 1, 2, 3, 4）
  - [x] 新增 SQLAlchemy model，建议表名 `review_items`，包含 `id`、`created_at`、`updated_at`、`tenant_id`、`created_by`、`status`、`item_type`、`severity`、`request_id`、`trace_id`、`source_view`、`safe_identifiers`、`safe_summary`、`eval_candidate`、`status_history`。
  - [x] 如状态历史需要可查询审计，优先新增 `review_item_status_events` 表；若使用 JSON history，必须解释查询边界并用 tests 固化。
  - [x] 新增 Alembic migration，包含 tenant/status/type/severity/created_at/request_id/trace_id 索引；不要修改旧 audit migration 的语义。
  - [x] Repository 查询必须始终先绑定 `tenant_id == context.auth.tenant_id`，再追加 filters；组合 request_id + trace_id 使用 AND。
  - [x] `limit` 后端限制在 1 到 100 或项目配置值；排序使用 `created_at desc, id desc`。
  - [x] 不返回 raw SQL、database exception、路径、source_uri、raw report path 或其它 tenant 信息。

- [x] 实现 Review Queue application service（AC: 1, 2, 3, 4）
  - [x] Service 输入 `AuthenticatedRequestContext` 和 DTO；不接收前端传入的 tenant/user/roles/permissions。
  - [x] 权限入口集中放在 `packages/auth/policies.py`，新增 `REVIEW_QUEUE_READ_PERMISSIONS`、`REVIEW_QUEUE_WRITE_PERMISSIONS`、`EVAL_CANDIDATE_WRITE_PERMISSIONS` 或项目等价 helper。
  - [x] 创建 item 时只调用安全抽取函数构造 `safe_identifiers` 和 `safe_summary`；即使来源是 Source Evidence/Diagnostics/Eval/Audit response，也不得透传 raw payload。
  - [x] 查询 list/detail 时只返回 `SAFE_REVIEW_ITEM_FIELDS`；detail 也不得包含未授权 excerpt 或 eval report 原文。
  - [x] 状态转换必须校验当前状态，例如 `open -> accepted/rejected/needs_followup`、`needs_followup -> accepted/rejected/converted_to_eval_case`、`accepted -> converted_to_eval_case`；禁止从 terminal 状态回退，除非明确实现受审计的 reopen。
  - [x] 所有 create/update/convert 操作写入 `audit_logs`，建议 action：`review_queue.create_item`、`review_queue.update_status`、`review_queue.convert_to_eval_candidate`。
  - [x] audit metadata 只包含 `review_item_id`、`item_type`、`severity`、`old_status`、`new_status`、`source_view`、safe identifier counts、`candidate_id`、`requires_human_confirmation`、request/trace IDs。

- [x] 暴露薄 FastAPI route 和 service dependency（AC: 1, 2, 3, 4）
  - [x] 新增 `apps/api/routes/review_queue.py`，建议 endpoints：`POST /review/items`、`GET /review/items`、`GET /review/items/{item_id}`、`POST /review/items/{item_id}/status`、`POST /review/items/{item_id}/eval-candidate`。
  - [x] Route 只做 dependency、DTO、service call、`success_response`；不得拼 SQL、读取 raw eval files、直接操作 SQLAlchemy session、判断权限或清洗 raw payload。
  - [x] 在 `apps/api/main.py` 注册 router，在 `apps/api/service_dependencies.py` 注入 `ReviewQueueService`、repository 和 `SqlAlchemyAuditPort(auto_commit=True)`。
  - [x] structured error details 只包含 request_id、trace_id、review_item_id、stage、error_code，不包含 raw source payload、eval report path、SQL、stack、raw metadata 或资源存在性提示。

- [x] 升级 `/governance` Review Queue 面板（AC: 1, 2, 3, 4, 5, 6）
  - [x] 替换 `governance-view-review-queue` placeholder，加入 create form 和 filter form：`item_type`、`severity`、`status`、`source_view`、`request_id`、`trace_id`、safe identifiers、safe summary、created window、limit。
  - [x] 表单不提供 `tenant_id`、`created_by`、`roles`、`permissions`、raw prompt/query/answer/chunk、metadata key/value、dataset path、free-form local filename 或 SQL 输入。
  - [x] 增加 list region、detail/status history region、eval candidate preview region、next-step region、copy/export buttons、alert/live region，复用现有 shared helper 模式。
  - [x] 请求开始前立即清理旧结果、detail、candidate、copy/export state；403/404/network/malformed response 必须清理旧授权数据。
  - [x] 状态按钮必须根据后端返回的 `allowed_transitions` 或 service response 渲染；前端只能展示可选项，不能决定权限。
  - [x] 转换 eval candidate 只显示后端返回的 preview，并明确 `requires_human_confirmation`；不得下载或写入正式 dataset 文件。
  - [x] 保留 Document Review、Source Evidence、Retrieval Diagnostics、Eval Evidence 和 Audit Explorer 行为；切换 tab 不自动拉取 review items。

- [x] 扩展 shared `sidecar.js` 的 Review Queue allowlist、fetch/render、copy/export（AC: 1, 2, 3, 4, 5, 6）
  - [x] 添加 `SAFE_REVIEW_ITEM_FIELDS`、`SAFE_REVIEW_IDENTIFIER_FIELDS`、`SAFE_REVIEW_SUMMARY_FIELDS`、`SAFE_REVIEW_STATUS_HISTORY_FIELDS`、`SAFE_EVAL_CANDIDATE_FIELDS`，并纳入 `GOVERNANCE_SAFE_FIELDS.reviewItem`。
  - [x] 新增 endpoint 常量，例如 `REVIEW_QUEUE_ITEMS_ENDPOINT = "/review/items"`；复用 `buildHeaders()`、`pickFields()`、`safeFilenamePart()`、`copyText()`、download pattern 和 request token 防竞态模式。
  - [x] Copy/export 只能序列化后端返回的 review safe payload 或 candidate preview 白名单字段；不得从 rendered DOM 或 raw API envelope 拼导出。
  - [x] 处理并发请求 token，避免旧请求覆盖新 Review Queue 结果；沿用 Eval Evidence/Audit Explorer request token 模式。
  - [x] 不读取或写入 `localStorage`、`sessionStorage`、cookie、URL history 或 console log。

- [x] 扩展 CSS 为紧凑、可扫描的 Review Queue 工作区（AC: 2, 3, 4, 6）
  - [x] 更新 `apps/web/sidecar/sidecar.css`，新增 review queue filter grid、review item row、status history row、candidate preview、safe summary chips、transition buttons 样式。
  - [x] 状态必须有文本和符号，不只依赖颜色；长 review/request/trace/document/version/chunk/candidate IDs 使用 `overflow-wrap: anywhere`。
  - [x] 移动端保持单列，按钮文本不溢出，列表/detail/candidate 不遮挡后续内容。
  - [x] 不引入 hero、营销卡片、装饰渐变、嵌套卡片或大面积单色主题。

- [x] 后端测试覆盖 DTO、repository、service、安全边界和 audit（AC: 1, 2, 3, 4）
  - [x] 新增 `tests/unit/review_queue/test_review_queue_service.py`，覆盖 create/list/detail/status/convert、permission denial、forbidden field/value filtering、status transition validation、eval candidate preview allowlist。
  - [x] 新增或扩展 `tests/integration/storage/test_review_queue_repositories.py`，覆盖 tenant-scoped filters、request+trace AND、created_at window、limit bounds、status history persistence、storage error safe details。
  - [x] 新增 `tests/integration/api/test_review_queue_routes.py`，覆盖 authorized create/list/detail/update/convert、permission denial、identity override rejection、malformed request、not found safe error、audit event writes 和 response envelope。
  - [x] 使用 in-memory/SQLite/temp fixtures；不真实调用外部 LLM、embedding、browser、PostgreSQL、Redis、MinIO 或 Open WebUI。
  - [x] 保留 Source Evidence、Diagnostics、Eval Evidence、Audit Explorer 和 Agent tests 不回归。

- [x] 前端静态契约和 JS 行为测试（AC: 1, 2, 3, 4, 5, 6）
  - [x] 扩展 `tests/unit/web/test_governance_static_contract.py`，验证 Review Queue controls、list/detail/status/candidate/next-step regions、ARIA live/alert、安全字段白名单、forbidden fragments absence 和 responsive CSS。
  - [x] 扩展 `tests/unit/web/test_sidecar_static_contract.py`，确认 review allowlists 不包含 query、answer、content、prompt、source_uri、object_key、SQL、vectors、embeddings、provider payload、token、secret、raw_exception、tool_observation。
  - [x] 扩展 `tests/unit/web/sidecar_behavior_runner.js`，覆盖 create/list rendering、status transitions rendering、permission failure stale clearing、new lookup clears candidate copy/export、candidate preview allowlist、sanitized filename、tab switch no auto lookup。
  - [x] 不引入 Playwright、Node build pipeline 或浏览器自动化；现有 Node `vm` runner 应足够覆盖 no-build 行为。

- [x] 更新 README 和 demo/operations docs（AC: 4, 6）
  - [x] 更新 `docs/demo/governance-workbench.md`，说明 Review Queue 支持创建安全 review item、状态流转、eval candidate preview、权限要求、字段白名单和限制。
  - [x] 更新 `docs/demo/source-inspector-sidecar.md`，说明 sidecar 不直接承载完整 Review Queue；治理工作台通过后端 API 创建和审阅 safe review evidence。
  - [x] 更新 `docs/operations/local-development.md`，加入本地调用示例、权限 header、验证命令、安全边界和人工确认说明。
  - [x] 更新 README Build Status、Governance Workbench、Current Limits、API/verification 段落；不得宣称自动采集真实企业数据、自动写入正式 eval dataset、长期 review workflow、SIEM 集成或跨租户 review 已完成。
  - [x] Dev Agent Record 填写实现决策、验证结果和文件列表。

- [x] 建议验证命令（AC: 1-6）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/review_queue -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/api/test_review_queue_routes.py tests/integration/storage/test_review_queue_repositories.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/web/test_governance_static_contract.py tests/unit/web/test_sidecar_static_contract.py -q`
  - [x] `node tests/unit/web/sidecar_behavior_runner.js`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/test_readme_expectations.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/audit_explorer tests/unit/eval_evidence tests/unit/diagnostics -q`
  - [x] `.venv\Scripts\python.exe -m pytest -q`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`

## Dev Notes

### Current Repository State

- Current HEAD at story creation: `c1e892c feat(audit): add audit explorer and safe export`.
- Story 8.1 established the no-build `/governance` shell with six stable tabs and ARIA tab behavior.
- Story 8.2 implemented Document Review with backend lifecycle APIs and stale state clearing.
- Story 8.3 implemented Source Evidence through `POST /sources/resolve`; pasted citation and Open WebUI metadata remain untrusted.
- Story 8.4 implemented Retrieval Diagnostics safe timeline through `POST /diagnostics/resolve`; diagnostics reads retrieval and audit evidence by request/trace without exposing raw query, prompt, vectors or chunks.
- Story 8.5 implemented Eval Evidence with backend report parsing, strict allowlists, report copy/download and safe stale clearing.
- Story 8.6 implemented Audit Explorer backend APIs, safe export, no-build governance UI, tests, migrations and docs. Its sprint status is still `review` at story creation time, so the dev agent must check review feedback before assuming all 8.6 changes are accepted.
- Review Queue is currently only a placeholder in `apps/web/governance/index.html`; `apps/web/sidecar/sidecar.js` already has a minimal `GOVERNANCE_SAFE_FIELDS.reviewItem` placeholder but no endpoints, forms or behavior.
- Existing frontend remains static HTML/CSS/JS served by FastAPI. There is no React/Next/Vite/package manifest and no Node build step.

### Existing Files To Read Before Implementation

- `packages/audit/dto.py`
  - Current state: defines Audit Explorer frozen DTOs and safe fields.
  - What this story changes: no direct change expected; use its allowlist style as a pattern, not as the review item DTO home.
  - Preserve: Audit Explorer payload shape and export fields.

- `packages/audit/service.py`
  - Current state: enforces `audit:read`, tenant-scoped query, safe summary extraction, Agent/tool association and backend-generated export audit.
  - What this story changes: no direct change expected; reuse the service style for permission checks, `_safe_label`-style sanitization and audit event metadata discipline.
  - Preserve: no raw audit metadata passthrough and no frontend inference.

- `packages/data/storage/audit_repositories.py` and `packages/data/storage/audit_models.py`
  - Current state: `audit_logs` persists audit events and supports tenant-scoped list filters for Audit Explorer.
  - What this story changes: use `SqlAlchemyAuditPort` to audit Review Queue actions; do not store review items inside `audit_logs`.
  - Preserve: existing `list_by_request_id`, `list_by_trace_id` and `list_records` semantics for Diagnostics/Audit Explorer.

- `packages/auth/policies.py`
  - Current state: permission helpers exist for document manage, RAG query, agent run, diagnostics, eval evidence and audit explorer.
  - What this story changes: add review read/write and eval candidate permission helpers.
  - Preserve: permissions remain backend-owned; frontend and LLM never decide access.

- `packages/eval/dto.py` and `packages/eval/service.py`
  - Current state: Eval Evidence parses existing reports into synthetic-safe summaries and failed case evidence; it does not write datasets.
  - What this story changes: Review Queue may create eval candidate previews, but should not mutate Eval Evidence report parsing or directly write formal dataset files.
  - Preserve: eval report filename validation, unsafe text filtering, and no static report browsing from frontend.

- `apps/api/routes/eval_evidence.py` and `apps/api/routes/audit_explorer.py`
  - Current state: thin route pattern with dependency-injected services and `success_response`.
  - What this story changes: add analogous `review_queue.py` route.
  - Preserve: no business logic, SQL or permission decisions inside route handlers.

- `apps/api/main.py`
  - Current state: registers existing routers including `audit_explorer_router`, `eval_evidence_router` and static sidecar assets.
  - What this story changes: register `review_queue_router`.
  - Preserve: existing router order and sidecar mount behavior unless tests require a narrow change.

- `apps/api/service_dependencies.py`
  - Current state: central service factories assemble repositories and audit ports.
  - What this story changes: add `get_review_queue_service()` and dependency alias.
  - Preserve: use session factory and `SqlAlchemyAuditPort(auto_commit=True)` consistently.

- `apps/web/governance/index.html`
  - Current state: Review Queue tab exists but contains only placeholder text.
  - What this story changes: replace placeholder with backend-backed create/list/detail/status/candidate UI.
  - Preserve: six governance tabs, IDs, ARIA roles, local/test auth helper and no-storage behavior.

- `apps/web/sidecar/sidecar.js`
  - Current state: shared frontend behavior, endpoint constants, safe field allowlists, request-token stale clearing and test exports for Source Evidence, Document Review, Diagnostics, Eval Evidence and Audit Explorer.
  - What this story changes: add Review Queue allowlists, API helpers, render helpers, status transition and candidate preview helpers.
  - Preserve: no local/session storage, no cookies/history/console logs, immediate stale clearing before async fetch and request token race protection.

- `apps/web/sidecar/sidecar.css`
  - Current state: compact operational styling for governance workbench, diagnostics, eval evidence and audit explorer.
  - What this story changes: add review queue list/detail/status/candidate styles.
  - Preserve: no nested cards, no marketing layout, no text overlap, mobile single-column fallback.

- `tests/unit/web/test_governance_static_contract.py`, `tests/unit/web/test_sidecar_static_contract.py`, `tests/unit/web/sidecar_behavior_runner.js`
  - Current state: cover governance shell, Source Evidence, Document Review, Retrieval Diagnostics, Eval Evidence, Audit Explorer, safe allowlists and stale clearing.
  - What this story changes: add Review Queue static and behavior tests.
  - Preserve: no browser automation or build step.

- `README.md`, `docs/demo/governance-workbench.md`, `docs/demo/source-inspector-sidecar.md`, `docs/operations/local-development.md`
  - Current state: document current governance workbench through Audit Explorer; Review Queue is still a later/placeholder capability.
  - What this story changes: update current capability and limitations after implementation.
  - Preserve: do not claim automatic real-enterprise data capture or direct formal eval dataset writes.

### Previous Story Intelligence

- Story 8.1 review caught governance entry identity, navigation wiring, keyboard behavior, responsive overflow and stale alert issues. Preserve tablist semantics and focus behavior.
- Story 8.2 review caught unsafe detail failure responses, nested unsafe error summaries, deleted latest-version selection, cursor bounds and stale Document Review UI state. Apply the same stale clearing discipline to review list/detail/status/candidate regions.
- Story 8.3 review caught stale evidence copyability during new resolves, direct evidence link parsing, pasted `request_id` being reused as current `X-Request-ID`, and unsafe metadata rendering. Review Queue `request_id` and `trace_id` fields are evidence identifiers, not current auth headers.
- Story 8.4 review caught nested stage error leakage, false success statuses without backend evidence, overly broad count fields, stale diagnostics report export and stale backend diagnostics DOM. Review Queue must use fixed allowlists and must not mark eval candidate created unless backend returns it.
- Story 8.5 review caught unsafe string leakage, malformed report list blocking, stale selected filename and overlapping request rendering. Review Queue should reject unsafe values, handle malformed summaries safely and use request token guards.
- Story 8.6 implemented audit export allowlists and backend-generated export payloads. Review Queue copy/export must follow the same backend-authoritative pattern, but should not reuse Audit Explorer export routes or audit DTOs.
- Recent frontend strategy is intentionally no-build: Python static contract tests plus Node `vm` behavior runner. Reuse this pattern.
- Governance workbench is presentation only. Backend AuthContext, RBAC, ACL, eval candidate creation, review item persistence and audit remain authoritative.

### Architecture and Security Guardrails

- Module ownership: API route in `apps/api/routes`, application/domain DTOs in `packages/review`, storage model/repository in `packages/data/storage` or `packages/review/storage` consistent with existing patterns, static UI in `apps/web/governance`, shared JS/CSS in `apps/web/sidecar`.
- Auth boundary: all data requests use `AuthenticatedRequestContext`; front-end payload must never include tenant_id/roles/permissions and must not override user identity.
- Query boundary: all review reads are tenant-scoped before filters; no cross-tenant mode in this story.
- Creation boundary: review item content is safe evidence metadata only. It is acceptable to store safe IDs and summarized labels/counts, not raw enterprise content.
- Status boundary: transitions are backend-validated and audit-logged. UI transition controls are hints from backend state, not authorization logic.
- Eval boundary: conversion creates a candidate preview requiring human confirmation. This story must not auto-append to formal eval datasets or silently collect real enterprise data.
- Redaction boundary: never return raw source/eval/audit metadata. Extract explicitly allowed keys and safe numeric/string labels only.
- Scope boundary: this story does not implement long-term review workflow analytics, human assignment queues, notifications, SIEM integration, full eval dataset editor, LLM-as-judge triage, trend warehouse, cross-tenant review or Open WebUI fork changes.
- Observability: service and route should record request_id, trace_id, user_id, tenant_id, review_item_id, item_type, severity, status transition, candidate_id, latency_ms, status and error_code without logging review summary raw text beyond the safe allowlist.

### Latest Technical Information

- No new external framework is required. Current `pyproject.toml` already pins FastAPI, Pydantic v2, SQLAlchemy 2.x, pytest, ruff and mypy; follow existing patterns rather than adding dependencies.
- FastAPI official documentation supports splitting route groups with `APIRouter` and dependency-injected services; keep Review Queue route thin and register it in `apps/api/main.py`.
- Pydantic v2 supports model-level `ConfigDict(extra="forbid", frozen=True)` and validators; use this for DTO immutability, identity override rejection and bounded input validation.
- SQLAlchemy 2.0 typed ORM uses `Mapped[...]` and `mapped_column`; follow existing model style and add Alembic migration for new tables/indexes.
- WAI-ARIA APG Tabs Pattern expects stable `tablist`/`tab`/`tabpanel` roles, `aria-selected`, `aria-controls`, keyboard navigation and focus behavior; do not regress existing governance tabs.
- MDN documents `aria-live` for announcing dynamic updates without moving focus; use polite live updates for successful list/status/candidate loads and alert/assertive behavior for errors.
- MDN Clipboard `writeText()` is Promise-based and only available in secure contexts; keep existing copy fallback behavior and tests for unavailable clipboard.
- MDN `URL.createObjectURL()` creates Blob URLs and should be paired with `URL.revokeObjectURL()` after download; preserve existing download cleanup pattern.

### References

- `_bmad-output/planning-artifacts/epics.md#Story-8.7-人工审阅队列与-Eval-回流`
- `_bmad-output/planning-artifacts/epics.md#Epic-8-企业审阅治理前端与可信证据工作台`
- `_bmad-output/planning-artifacts/architecture.md#API-Architecture`
- `_bmad-output/planning-artifacts/architecture.md#Frontend-Architecture`
- `_bmad-output/planning-artifacts/architecture.md#Process-Patterns`
- `_bmad-output/planning-artifacts/architecture.md#Requirements-Coverage-Validation`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md#Experience-Principles`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/DESIGN.md#Component-Patterns`
- `project-context.md#16-权限规则`
- `project-context.md#17-测试规则`
- `project-context.md#18-可观测性规则`
- `_bmad-output/implementation-artifacts/8-1-审阅治理工作台信息架构与前端边界.md`
- `_bmad-output/implementation-artifacts/8-2-文档生命周期审阅看板.md`
- `_bmad-output/implementation-artifacts/8-3-citation-与-source-evidence-审阅器.md`
- `_bmad-output/implementation-artifacts/8-4-retrieval-diagnostics-安全时间线.md`
- `_bmad-output/implementation-artifacts/8-5-eval-evidence-与质量回归工作区.md`
- `_bmad-output/implementation-artifacts/8-6-审计日志-explorer-与安全导出.md`
- `packages/audit/dto.py`
- `packages/audit/service.py`
- `packages/data/storage/audit_repositories.py`
- `packages/data/storage/audit_models.py`
- `packages/auth/policies.py`
- `packages/eval/dto.py`
- `packages/eval/service.py`
- `apps/api/routes/audit_explorer.py`
- `apps/api/routes/eval_evidence.py`
- `apps/api/main.py`
- `apps/api/service_dependencies.py`
- `apps/web/governance/index.html`
- `apps/web/sidecar/sidecar.js`
- `apps/web/sidecar/sidecar.css`
- `tests/unit/web/test_governance_static_contract.py`
- `tests/unit/web/test_sidecar_static_contract.py`
- `tests/unit/web/sidecar_behavior_runner.js`
- FastAPI Bigger Applications: https://fastapi.tiangolo.com/tutorial/bigger-applications/
- Pydantic Models and Config: https://docs.pydantic.dev/latest/concepts/models/
- SQLAlchemy ORM Mapped Class Configuration: https://docs.sqlalchemy.org/en/20/orm/declarative_config.html
- MDN `aria-live`: https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Attributes/aria-live
- MDN Clipboard `writeText()`: https://developer.mozilla.org/en-US/docs/Web/API/Clipboard/writeText
- MDN `URL.createObjectURL()`: https://developer.mozilla.org/en-US/docs/Web/API/URL/createObjectURL_static
- WAI-ARIA Tabs Pattern: https://www.w3.org/WAI/ARIA/apg/patterns/tabs/

## Validation Checklist

Validation Result: PASS（2026-06-09T19:21:00+08:00）

- [x] Story 明确 8.7 只实现人工审阅队列、状态流转和 eval candidate preview，不自动采集真实企业数据，不直接写入正式 eval dataset。
- [x] Acceptance Criteria 覆盖创建安全摘要、tenant-scoped 查询、权限拒绝、状态转换审计、eval candidate 人工确认、no-build 前端、可访问性、docs/tests。
- [x] Tasks 指向现有 FastAPI route/service dependency、SQLAlchemy storage、governance HTML、sidecar JS/CSS 和测试文件，避免重建前端栈或复用 audit_logs 当业务表。
- [x] Dev Notes 记录 8.1-8.6 learnings、8.6 当前仍为 review 的状态、recent git patterns、safe allowlist、stale clearing 和 no new framework 约束。
- [x] 明确禁止 raw query、answer、chunk content、prompt、SQL、vectors、embeddings、provider payload、tool observation、token、secret、source_uri、object key、本机路径和 raw exception。
- [x] README 同步要求已写入 AC/Tasks；本次 create-story 只创建 story，不实现功能，因此不更新 README。

## Change Log

- 2026-06-09: Created comprehensive Story 8.7 developer context for Review Queue and eval candidate feedback.
- 2026-06-09: Implemented Review Queue backend, no-build governance UI, tests, docs, and validation.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- `.venv\Scripts\python.exe -m pytest tests/unit/review_queue -q`
- `.venv\Scripts\python.exe -m pytest tests/integration/api/test_review_queue_routes.py tests/integration/storage/test_review_queue_repositories.py -q`
- `.venv\Scripts\python.exe -m pytest tests/unit/web/test_governance_static_contract.py tests/unit/web/test_sidecar_static_contract.py -q`
- `node tests/unit/web/sidecar_behavior_runner.js`
- `.venv\Scripts\python.exe -m pytest tests/unit/test_readme_expectations.py -q`
- `.venv\Scripts\python.exe -m pytest tests/unit/audit_explorer tests/unit/eval_evidence tests/unit/diagnostics -q`
- `.venv\Scripts\python.exe -m pytest -q`
- `.venv\Scripts\python.exe -m ruff check .`
- `.venv\Scripts\python.exe -m mypy apps packages tests`

### Implementation Plan

- Added a dedicated `packages.review` application/domain package with frozen Pydantic DTOs, stable domain errors, safe identifier/summary allowlists, backend-owned status transitions, and eval candidate preview generation that requires human confirmation.
- Added tenant-scoped `review_items` SQLAlchemy storage and Alembic migration, keeping review business data separate from `audit_logs` while using `SqlAlchemyAuditPort` for create/status/convert audit events.
- Exposed thin FastAPI Review Queue routes and service dependencies; route code only builds DTOs and delegates to `ReviewQueueService`.
- Extended the no-build `/governance` Review Queue panel and shared sidecar JS/CSS with stale clearing, request-token race protection, safe copy/download export, backend-provided transition buttons, and eval candidate preview rendering.
- Updated README and demo/operations docs to document permissions, field white/blacklists, artificial candidate boundaries, and validation commands.

### Completion Notes List

- Review item creation persists only safe summaries and safe identifiers; `tenant_id` and `created_by` are injected from `AuthenticatedRequestContext`.
- List/detail APIs are tenant-scoped and support bounded filters for item type, severity, status, source view, request/trace IDs, created-at window, and limit.
- Status transitions are backend-validated, persisted in status history, and audited with safe metadata.
- Eval candidate conversion returns a synthetic-safe preview with `requires_human_confirmation=true`; it does not write formal eval datasets or collect raw enterprise content.
- Governance UI clears stale list/detail/candidate/copy/export state on new requests and safe failures, and copy/download uses allowlisted state payloads.
- README was updated because the story adds new user-facing governance/API capability and verification commands.

### File List

- README.md
- _bmad-output/implementation-artifacts/8-7-人工审阅队列与-eval-回流.md
- _bmad-output/implementation-artifacts/sprint-status.yaml
- apps/api/main.py
- apps/api/routes/review_queue.py
- apps/api/service_dependencies.py
- apps/web/governance/index.html
- apps/web/sidecar/sidecar.css
- apps/web/sidecar/sidecar.js
- docs/demo/governance-workbench.md
- docs/demo/source-inspector-sidecar.md
- docs/operations/local-development.md
- migrations/env.py
- migrations/versions/20260609_0013_review_items.py
- packages/auth/policies.py
- packages/data/storage/review_models.py
- packages/data/storage/review_repositories.py
- packages/review/__init__.py
- packages/review/dto.py
- packages/review/exceptions.py
- packages/review/service.py
- tests/integration/api/test_review_queue_routes.py
- tests/integration/storage/test_review_queue_repositories.py
- tests/unit/review_queue/test_review_queue_service.py
- tests/unit/web/sidecar_behavior_runner.js
- tests/unit/web/test_governance_static_contract.py
- tests/unit/web/test_sidecar_static_contract.py

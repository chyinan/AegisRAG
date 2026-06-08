---
baseline_commit: 90a0e9a434c8af0043ca8757486e7ce3d9aef158
---

# Story 7.5: 轻量 Sidecar Source Inspector 体验设计

Status: done

生成时间：2026-06-08T23:40:44+08:00

## Story

As a 企业员工或知识库管理员,
I want 最小 sidecar 展示 Source Inspector、job status 和诊断入口,
so that Open WebUI 聊天体验之外的可信度证据可以被查看，而不建设完整管理后台。

## Acceptance Criteria

1. **Source Inspector 只基于后端授权解析来源**
   - Given 用户从 Open WebUI、walkthrough report 或演示页获得 citation identifiers
   - When 打开轻量 sidecar 的 Source Inspector
   - Then UI 必须使用 `POST /sources/resolve` 获取授权 excerpt、source display metadata、document/version/chunk/page、title_path、retrieval_method、score、request_id 和 trace_id
   - And 前端不得补造 citation、不得从回答文本猜测来源、不得判断权限、不得缓存未授权片段
   - And 请求失败、无权限、资源不存在、软删除、版本不可见或 ACL 拒绝时显示同一类安全失败状态，不暴露资源是否存在

2. **Citation 输入与来源展示安全**
   - Given 用户通过 query/hash 参数、粘贴 JSON 或表单输入 citation identifiers
   - When sidecar 解析这些 identifiers
   - Then 只接受 `document_id`、`version_id`、`chunk_id`、可选 `page_start`、`page_end`、`request_id`、`citation_ref`
   - And URL、DOM、日志、错误状态和 copy 内容不得包含 bearer token、service token、raw `source_uri`、本机绝对路径、MinIO object key、完整 chunk、prompt、SQL、vectors、embeddings 或 provider raw response
   - And 长 `document_id`、`version_id`、`chunk_id`、`request_id`、`trace_id` 必须可换行或截断并提供完整值复制方式

3. **Job/status 视图复用现有 document status API**
   - Given 知识库管理员输入或打开 document/version status
   - When sidecar 调用 `GET /documents/{document_id}/versions/{version_id}/status`
   - Then UI 展示 `uploaded`、`parsing`、`parsed`、`chunking`、`chunked`、`embedding`、`indexing`、`retrieval_ready`、`failed_retryable`、`failed_terminal`、`deleted` 等业务状态
   - And 展示 chunk_count、embedding provider/model/version/dim、vector_count、index_status、job_id、attempt_count、last_attempt_at、next_retry_at、safe error_summary、request_id、trace_id
   - And 错误只显示稳定 error code、安全摘要和 request/trace IDs，不显示内部异常、SQL、对象存储路径或企业全文

4. **诊断入口是轻量链接和 request_id 驱动，不提前做完整仪表盘**
   - Given 用户在 Source Inspector、job status 或 walkthrough report 中看到 request_id/trace_id
   - When 打开 diagnostics 区域
   - Then MVP 至少提供可复制 request_id/trace_id、相关验证命令、eval/walkthrough report 位置和后续 Story 7.6 入口说明
   - And 不实现 full observability dashboard、Grafana 替代品、OpenTelemetry viewer 或完整 retrieval trace UI
   - And 不渲染 full query、chunk content、prompt、provider raw response、SQL、vectors、embeddings、tokens 或 secrets

5. **Open WebUI 集成保持 sidecar 边界**
   - Given Open WebUI 通过 OpenAI-compatible `/v1` 接入本后端
   - When 用户点击或复制 citation metadata 到 sidecar
   - Then sidecar 只作为同源或本地 companion 页面调用后端 API，不成为认证、授权、citation 或 source visibility 的决策点
   - And Open WebUI provider key/service token 不进入 sidecar URL、本地存储、报告或 README 示例输出
   - And 若需要本地 demo auth 输入，只能作为显式 local/test 辅助路径，默认不持久化 token，不在生产说明中推荐

6. **响应式与可访问性达到 UX 基线**
   - Given Source Inspector、job status 或 diagnostics 页面在桌面、平板、移动尺寸打开
   - When 用户使用键盘、屏幕阅读器或触屏操作
   - Then UI 满足 WCAG 2.2 AA 基础、可见焦点、键盘可达、非纯颜色状态表达、`aria-live="polite"` status 更新、alert 区域错误提示
   - And drawer/sheet 打开时焦点进入标题，关闭后恢复到触发元素；移动端使用 bottom sheet，不遮挡关键操作
   - And 不依赖 hover-only 操作；copy、retry/status refresh、open source 和 close 操作都可键盘触发

7. **实现保持轻量，不引入完整自定义管理后台**
   - Given 当前仓库没有 `apps/web` 前端工程
   - When 实现 Story 7.5
   - Then 优先创建最小 sidecar 静态页面或轻量 FastAPI-served assets，例如 `apps/web/sidecar/` + `apps/api/routes/sidecar.py`
   - And 不引入 Next.js、复杂设计系统、状态管理库、build pipeline 或 Open WebUI fork，除非实现说明证明现有 repo 已有对应基础并补足测试/文档
   - And FastAPI route 仅负责静态页面/asset 暴露，不写 source resolve、job status 或权限业务逻辑

8. **测试覆盖 UI 契约、安全回归和文档同步**
   - Given Story 7.5 实现完成
   - When 运行 focused tests
   - Then 覆盖 source resolve payload 构造、安全字段展示、权限失败状态、job status 渲染、长 ID 包裹/复制、keyboard/focus 基础行为、aria-live/alert 标记、token/source_uri/path 泄露回归
   - And API integration tests 继续证明 `/sources/resolve` 和 document status route 由后端权限控制，sidecar 不能绕过
   - And README 项目进度更新到 Story 7.5，`docs/operations/local-development.md` 或新增 sidecar docs 说明入口、能力、限制、安全边界和验证命令

## Tasks / Subtasks

- [x] 定义 sidecar 信息架构与路由入口（AC: 1, 3, 4, 5, 7）
  - [x] 确认最小实现路径：优先 `apps/web/sidecar/` 静态资源 + `apps/api/routes/sidecar.py` 同源入口；如选择其他路径，必须在 Dev Agent Record 中说明原因。
  - [x] 新增页面入口建议：`/sidecar`、`/sidecar/source` 或等价稳定路径；入口应包含 Source Inspector、Job Status、Diagnostics 三个轻量视图或 tabs。
  - [x] route 只返回 HTML/static assets，不直接查询数据库、不直接调用 `SourceResolveService` 或 `DocumentLifecycleService`。
  - [x] 不 fork Open WebUI，不实现完整 Knowledge Admin、Eval Reports 或 observability dashboard。

- [x] 实现 Source Inspector 表单/解析与 API 调用（AC: 1, 2, 5, 6）
  - [x] 支持从 query/hash 参数、粘贴 JSON 或表单读取 `document_id`、`version_id`、`chunk_id`、可选 `page_start`、`page_end`、`request_id`、`citation_ref`。
  - [x] 调用 `POST /sources/resolve`，只渲染响应 envelope 的 `data` 安全字段。
  - [x] 显示 source_display_name、source_type、document/version/chunk/page、title_path、text_excerpt、excerpt_char_count、token_count、retrieval_method、score、request_id、trace_id。
  - [x] 对 401/403/404/422/5xx 显示安全状态和 request_id，不暴露输入中未授权资源是否存在。
  - [x] 不把成功 excerpt 写入 localStorage/sessionStorage/URL；需要 copy 时只复制用户明确选择的安全字段。

- [x] 实现最小 auth 输入策略（AC: 2, 5）
  - [x] 支持本地/demo 输入 Bearer token 或 dev auth headers，但默认不保存 token；不得把 token 放在 URL、localStorage、report、console log 或错误消息中。
  - [x] 对 dev auth headers 明确标记为 local/test 辅助，仅在后端 `ENABLE_DEV_AUTH_HEADERS=true` 且 `APP_ENV` local/test 时可用。
  - [x] 生产文档应推荐使用后端 JWT/service-token 映射后的正常 API 调用，不让 sidecar 成为权限配置中心。

- [x] 实现 Job Status 视图（AC: 3, 6）
  - [x] 输入 document_id 和 version_id 后调用 `GET /documents/{document_id}/versions/{version_id}/status`。
  - [x] 状态展示必须覆盖 upload/parsing/chunking/embedding/indexing/retrieval_ready/deleted/failure 状态；内部实际状态如 `embedded` 若出现，按安全状态映射展示并在实现说明中记录。
  - [x] 使用图标/文本/状态标签表达状态，不只靠颜色；失败状态显示 error_code/error_summary、attempt_count、next_retry_at、request_id、trace_id。
  - [x] 不暴露对象存储路径、raw source_uri、企业全文、stack trace 或内部 SQL。

- [x] 实现轻量 Diagnostics 入口（AC: 4, 8）
  - [x] 提供 request_id/trace_id copy、常用验证命令、walkthrough/eval report 位置和 Story 7.6 说明。
  - [x] 可链接到 `docs/demo/enterprise-rag-walkthrough.md` 或本地 docs 路径说明，但不要把 docs 内容硬编码成业务逻辑。
  - [x] 明确当前不提供 full retrieval trace UI；不能伪造 dense/sparse/rerank/context packing 数据。

- [x] 实现响应式和可访问性约束（AC: 6）
  - [x] 桌面端 Source Inspector 可作为右侧 drawer/panel；移动端为 bottom sheet 或单列详情。
  - [x] 使用稳定尺寸和 responsive constraints，长 ID wrap/truncate + copy，不让内容撑破页面。
  - [x] Streaming/status 或 async fetch 状态使用 `aria-live="polite"`；错误区域使用 `role="alert"` 或等价可发现机制。
  - [x] Drawer/sheet 管理焦点进入、Esc/关闭、关闭后焦点恢复；所有 icon/button 有可访问名称。

- [x] 添加测试（AC: 1-8）
  - [x] 新增 API/static route integration tests，例如 `tests/integration/api/test_sidecar_routes.py`，验证 sidecar 页面可访问且不需要业务 service。
  - [x] 新增 UI/static contract tests 或 DOM-oriented lightweight tests，验证 source resolve request payload、safe rendering、error state、job status state mapping、long ID handling、aria-live/alert/focus markup。
  - [x] 增加泄露回归：页面源码、rendered fixture、failure state 和 copy helper 不包含 `source_uri`、`object_key`、Windows/Unix 绝对路径、Bearer token、JWT、prompt、chunk full content、SQL、vectors、embeddings。
  - [x] 继续运行 `tests/integration/api/test_sources_routes.py`、`tests/integration/api/test_document_routes.py`、OpenWebUI/demo walkthrough 相关 focused tests，确认 sidecar 没有改变后端契约。

- [x] 更新 README 和操作文档（AC: 4, 5, 8）
  - [x] README Build Status 更新为 Story 7.5 完成，并说明 sidecar 能力、限制和仍待 Story 7.6 完成的诊断能力。
  - [x] `docs/operations/local-development.md` 或新增 `docs/demo/source-inspector-sidecar.md` 记录启动 API、打开 sidecar、填入 citation/job identifiers、使用 local/test auth、验证 source resolve/job status、检查安全失败状态。
  - [x] 文档必须说明 Open WebUI 是入口不是权限边界，sidecar 只显示后端确认的信息，不缓存未授权片段。

- [x] 验证（AC: 1-8）
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/api/test_sidecar_routes.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/api/test_sources_routes.py tests/integration/api/test_document_routes.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/api/test_openwebui_routes.py tests/integration/api/test_demo_walkthrough.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/test_readme_expectations.py -q`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`
  - [x] 如新增前端构建工具，必须补充相应 install/lint/test 命令；默认建议不新增 Node 构建链

### Review Findings

- [x] [Review][Patch] Safe failure rendering can leave stale authorized results and writes failures into the wrong panel [apps/web/sidecar/sidecar.js:287]
- [x] [Review][Patch] Safe failure trace_id can incorrectly fall back to request_id [apps/web/sidecar/sidecar.js:290]
- [x] [Review][Patch] Status failure copy buttons lose handlers because cloned rows drop event listeners [apps/web/sidecar/sidecar.js:297]
- [x] [Review][Patch] Source inspector declares a modal dialog without trapping Tab/Shift+Tab focus [apps/web/sidecar/sidecar.js:342]
- [x] [Review][Patch] Invalid page_start/page_end values are converted to NaN and serialized as null [apps/web/sidecar/sidecar.js:127]
- [x] [Review][Patch] Unknown document statuses are visually downgraded to a working state [apps/web/sidecar/sidecar.js:333]
- [x] [Review][Patch] Copy helpers fail silently when Clipboard API is unavailable or denied [apps/web/sidecar/sidecar.js:373]
- [x] [Review][Patch] Required sidecar UI behavior tests do not execute JS, fetch, failure rendering, copy, or focus behavior [tests/unit/web/test_sidecar_static_contract.py:12]

## Dev Notes

### Current Repository State

- Sprint status auto-selected `7-5-轻量-sidecar-source-inspector-体验设计` as the first backlog story after Story 7.4.
- Git worktree was clean at story creation time.
- Current recent commits:
  - `90a0e9a fix(demo): address walkthrough review findings`
  - `a56ac97 feat(openwebui): complete demo walkthrough hardening`
  - `7edc69f feat(openwebui): add optional compose profile`
  - `74a464d feat(openwebui): harden service token auth`
  - `3f79c15 fix(rag): address safe source metadata review findings`
- There is currently no `apps/web` directory. Do not assume React/Next/Vite exists. If sidecar assets are needed, create the smallest project-local static structure and serve it through existing FastAPI assembly.
- Epic 1-6 and Story 7.1-7.4 are complete. Story 7.5 must not rebuild source metadata sanitization, OpenWebUI auth, OpenWebUI compose, demo seed/walkthrough, retrieval, RAG generation, citation extraction, `/sources/resolve`, eval runner or Agent runtime.

### Existing Files To Read Before Implementation

- `apps/api/main.py`
  - Current state: assembles FastAPI app, middleware, error handlers and route modules.
  - What this story changes: likely include a new sidecar route/static mount.
  - Preserve: route registration stays explicit; middleware/error handler order and existing routers remain intact.

- `apps/api/routes/sources.py`
  - Current state: `POST /sources/resolve` accepts `SourceResolveRequestBody`, injects `RagQueryContextDep`, delegates to `SourceResolveService`, and returns shared response envelope.
  - What this story changes: sidecar calls this endpoint from UI. Do not move source resolution logic into frontend or static route.
  - Preserve: backend permission checks and response envelope.

- `packages/rag/source_resolver.py`
  - Current state: rechecks tenant, document/version/chunk identity, soft delete, retrieval_ready version visibility, active chunk status and ACL before returning safe excerpt and source metadata; denied references use safe 404 shape.
  - What this story changes: UI should render only `SourceResolveResponse` safe fields and handle denial uniformly.
  - Preserve: no raw `source_uri`, no resource existence disclosure, no ACL logic in frontend.

- `apps/api/routes/documents.py`
  - Current state: exposes `GET /documents/{document_id}/versions/{version_id}/status` and delete endpoints through `DocumentLifecycleService`.
  - What this story changes: sidecar job/status view should call the existing status endpoint.
  - Preserve: document lifecycle permissions are backend-only. Sidecar must not widen permissions or infer existence from error details.

- `tests/integration/api/test_sources_routes.py` and `tests/integration/api/test_document_routes.py`
  - Current state: verify route envelopes, permission failures and safe errors.
  - What this story changes: sidecar tests should reuse these expectations rather than duplicating backend authorization logic in UI.
  - Preserve: source route requires `document:read,retrieval:query`; document status route currently uses document lifecycle service permissions.

- `apps/api/routes/openwebui.py` and `packages/rag/openwebui.py`
  - Current state: OpenAI-compatible `/v1/models` and `/v1/chat/completions` require backend auth and return citations/no_answer/metadata extension fields. Streaming uses data-only OpenAI-compatible chunks and terminal `[DONE]`.
  - What this story changes: sidecar may document how to copy citation identifiers from Open WebUI responses.
  - Preserve: OpenWebUI request body, model name, metadata_filter or UI user info cannot override backend AuthContext, RBAC, ACL or source visibility.

- `packages/data/demo_walkthrough.py`
  - Current state: calls `/v1/chat/completions` and `/sources/resolve`, validates source resolve response safety, ACL isolation, no-answer and prompt injection cases, and writes safe reports.
  - What this story changes: can reuse its safety rules and expected response shapes for sidecar test fixtures.
  - Preserve: report and UI output must not contain forbidden source locators or secrets.

- `_bmad-output/planning-artifacts/ux-designs/.../DESIGN.md` and `EXPERIENCE.md`
  - Current state: define visual vocabulary, Source Inspector behavior, job status row, diagnostics entry, WCAG 2.2 AA, keyboard focus, `aria-live`, alert region, non-color-only status and long ID handling.
  - What this story changes: implementation must follow these rules for the sidecar.
  - Preserve: quiet enterprise tool style, dense operational UI, no landing page/hero/marketing treatment.

- `README.md` and `docs/operations/local-development.md`
  - Current state: project status is complete through Story 7.4 and documents Open WebUI profile plus synthetic walkthrough.
  - What this story changes: update progress and usage instructions after implementation.
  - Preserve: README must distinguish implemented sidecar capabilities from Story 7.6 diagnostics roadmap.

### What Must Be Preserved

- Backend AuthContext, RBAC, ACL filters, source resolve authorization and audit remain authoritative.
- Sidecar cannot interpret OpenWebUI users, provider keys, model names, prompt text or metadata filters as backend authorization.
- Public source metadata remains governed by Story 7.1: no raw `source_uri`, local path, object key, token-bearing URL, prompt text, chunk full content, vector, embedding, provider raw response or SQL in rendered UI.
- Story 7.2 service token hardening remains intact. Do not put OpenWebUI provider API key or service token hash into frontend code or docs output examples.
- Story 7.3 OpenWebUI profile remains optional. Default backend tests, lint and mypy must not require OpenWebUI.
- Story 7.4 synthetic walkthrough remains a demo/evidence path. Reuse its manifest/report where useful; do not create a second demo corpus or duplicate walkthrough runner.
- Tests must use fake/stub/local fixtures by default. Real OpenWebUI container, real LLM/embedding provider, PostgreSQL, Redis, MinIO and external network are out of scope for unit/integration tests unless already covered by explicit optional smoke docs.

### Suggested Implementation Shape

Use this as guidance, not as mandatory file names if the existing implementation makes a better local pattern obvious:

```text
apps/api/routes/sidecar.py
apps/web/sidecar/index.html
apps/web/sidecar/sidecar.css
apps/web/sidecar/sidecar.js
tests/integration/api/test_sidecar_routes.py
tests/unit/web/test_sidecar_static_contract.py
docs/demo/source-inspector-sidecar.md
```

Preferred behavior:

```text
/sidecar
  -> static shell with Source Inspector, Job Status, Diagnostics tabs
  -> JS fetches /sources/resolve and /documents/{document_id}/versions/{version_id}/status
  -> all authorization still comes from backend headers/token
```

If serving static assets through FastAPI, use standard Starlette/FastAPI primitives already available through the existing dependency stack. Do not add Jinja, React, Next.js, Vite or a Node build unless there is a specific, tested reason.

### Previous Story Intelligence

- Story 7.1 unified safe source metadata across `/retrieve`, `/query`, `/chat`, SSE, OpenWebUI, `/sources/resolve` and `rag_search`. Sidecar must render only these safe fields.
- Story 7.2 made OpenWebUI auth fail closed through JWT bearer or hash-configured service tokens. Sidecar auth helpers must not weaken that boundary or persist secrets.
- Story 7.3 added optional OpenWebUI compose profile and provider-key/hash separation. Sidecar docs should reference that setup, not create another frontend service.
- Story 7.4 added synthetic demo corpus, seed validation/materialization, walkthrough runner and safe reports. Sidecar can use its manifest/report outputs as demo input examples, but must not duplicate the runner or corpus.

### Git Intelligence

- Recent work repeatedly addressed source safety, OpenWebUI auth, compose secret handling, demo report redaction and review-finding regressions.
- Follow the same pattern: add explicit leak tests and fail closed. Do not add convenience URL/token handling that creates a new leak channel.

### Latest Technical Information

- Open WebUI official docs describe provider connection around OpenAI-compatible APIs; it expects a backend that supports standard chat completions and commonly verifies `/models` with a Bearer token. Keep this project pointed at backend `/v1`, not a sidecar-specific model bridge.
- WAI-ARIA Authoring Practices for modal dialogs require focus to move into the dialog, Tab/Shift+Tab to remain inside the dialog, Escape to close, and focus to return to the invoking element when the dialog closes. Apply this to drawer/sheet behavior.
- WCAG 2.2 includes focus appearance requirements. Sidecar controls must have visible focus and cannot rely on color alone.
- MDN documents `aria-live` for dynamic content updates. Use polite live regions for status/loading updates and alert semantics for failures.
- Sources checked 2026-06-08:
  - Open WebUI OpenAI-Compatible docs: https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-openai-compatible/
  - WAI-ARIA APG Modal Dialog Pattern: https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/
  - WCAG 2.2 Focus Appearance: https://www.w3.org/TR/WCAG22/#focus-appearance
  - MDN `aria-live`: https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Attributes/aria-live

### References

- `_bmad-output/planning-artifacts/epics.md#Story-7.5-轻量-Sidecar-Source-Inspector-体验设计`
- `_bmad-output/planning-artifacts/epics.md#Epic-7-Open-WebUI-展示闭环与生产接入硬化`
- `_bmad-output/planning-artifacts/architecture.md#Frontend-Architecture`
- `_bmad-output/planning-artifacts/architecture.md#API-&-Communication-Patterns`
- `_bmad-output/planning-artifacts/architecture.md#Authentication-&-Security`
- `_bmad-output/planning-artifacts/architecture.md#Project-Structure-&-Boundaries`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/DESIGN.md`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md`
- `_bmad-output/implementation-artifacts/7-1-source-metadata-安全展示策略.md`
- `_bmad-output/implementation-artifacts/7-2-open-webui-认证接入硬化.md`
- `_bmad-output/implementation-artifacts/7-3-open-webui-docker-compose-profile.md`
- `_bmad-output/implementation-artifacts/7-4-企业-rag-演示脚本与-synthetic-seed-corpus.md`
- `project-context.md#13-Prompt-Injection-防护`
- `project-context.md#16-权限规则`
- `project-context.md#18-可观测性规则`
- `project-context.md#21-完成定义`
- `apps/api/main.py`
- `apps/api/routes/sources.py`
- `apps/api/routes/documents.py`
- `apps/api/routes/openwebui.py`
- `packages/rag/source_resolver.py`
- `packages/rag/openwebui.py`
- `packages/data/demo_walkthrough.py`
- `tests/integration/api/test_sources_routes.py`
- `tests/integration/api/test_document_routes.py`
- `tests/integration/api/test_openwebui_routes.py`
- `tests/integration/api/test_demo_walkthrough.py`
- `README.md`
- `docs/operations/local-development.md`

## Validation Checklist

Validation Result: PASS（2026-06-08T23:40:44+08:00）

- [x] Story 明确了 Source Inspector、job status、diagnostics 轻量入口，不把它扩大成完整管理后台。
- [x] Acceptance Criteria 覆盖 `/sources/resolve` 二次授权、citation 输入安全、document status、安全失败、OpenWebUI 边界、可访问性、测试和文档。
- [x] Tasks 指向当前存在的 API/service/test 文件和合理新增位置，避免重建 RAG、OpenWebUI auth、source sanitizer 或 walkthrough runner。
- [x] Dev Notes 记录了当前代码状态、必须保留的行为、前序 story lessons、recent git patterns 和最新 OpenWebUI/WAI-ARIA/WCAG/ARIA live context。
- [x] 明确默认不新增 Node/React/Next/Vite 构建链；如实现阶段新增，必须证明必要性并补齐验证。
- [x] 明确 README 和 operations/sidecar docs 在实现阶段必须同步；本 create-story 仅创建 story 文件并更新 sprint status。

## Change Log

- 2026-06-08: Created comprehensive Story 7.5 developer context for lightweight Source Inspector sidecar UX and safe frontend contract.
- 2026-06-08: Implemented lightweight sidecar static shell, source/status/diagnostics UI contract, docs, tests, and validation.
- 2026-06-09: Addressed code review findings for sidecar failure handling, focus trap, page validation, copy fallback, unknown statuses, and executable JS behavior tests.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- `python3 .\_bmad\scripts\resolve_customization.py --skill D:\Programs\RAG-Local-System\.agents\skills\bmad-dev-story --key workflow`
- `.venv\Scripts\python.exe -m pytest tests/integration/api/test_sidecar_routes.py -q`
- `.venv\Scripts\python.exe -m pytest tests/unit/web/test_sidecar_static_contract.py -q`
- `.venv\Scripts\python.exe -m pytest tests/integration/api/test_sources_routes.py tests/integration/api/test_document_routes.py -q`
- `.venv\Scripts\python.exe -m pytest tests/integration/api/test_openwebui_routes.py tests/integration/api/test_demo_walkthrough.py -q`
- `.venv\Scripts\python.exe -m pytest tests/unit/test_readme_expectations.py -q`
- `.venv\Scripts\python.exe -m ruff check .`
- `.venv\Scripts\python.exe -m mypy apps packages tests`
- `.venv\Scripts\python.exe -m pytest`
- `.venv\Scripts\python.exe -m pytest tests/unit/web/test_sidecar_static_contract.py -q`

### Completion Notes List

- Implemented `apps/web/sidecar/` as a minimal static same-origin companion UI served by FastAPI at `/sidecar`; no React/Next/Vite/Node build chain was added.
- Added Source Inspector, Job Status, and Diagnostics views. Source resolution calls `/sources/resolve`; status calls `/documents/{document_id}/versions/{version_id}/status`; all authorization remains backend-controlled.
- Added safe rendering allowlists, safe failure states, no client persistence for auth/excerpts, long ID wrapping/copy, `aria-live`, alert region, focus entry/restore for the inspector sheet, keyboard close, and mobile bottom-sheet layout.
- Added integration/static contract tests and README expectations covering route availability, safe fields, status mapping, forbidden field leakage, accessibility markers, and documentation sync.
- Updated README, local development docs, and added `docs/demo/source-inspector-sidecar.md` with entrypoint, local/test auth, safety boundary, validation commands, and Story 7.6 diagnostics limits.
- Verification passed: focused story tests, `ruff`, `mypy`, and full regression suite (`913 passed`).
- Code review patches resolved stale failure rendering, request/trace fallback, status failure copy actions, modal focus trapping, page bound validation, unknown status tone, clipboard fallback, and executable sidecar JS behavior tests.

### File List

- `README.md`
- `_bmad-output/implementation-artifacts/7-5-轻量-sidecar-source-inspector-体验设计.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `apps/api/main.py`
- `apps/api/routes/sidecar.py`
- `apps/web/sidecar/index.html`
- `apps/web/sidecar/sidecar.css`
- `apps/web/sidecar/sidecar.js`
- `docs/demo/source-inspector-sidecar.md`
- `docs/operations/local-development.md`
- `tests/integration/api/test_sidecar_routes.py`
- `tests/unit/test_readme_expectations.py`
- `tests/unit/web/test_sidecar_static_contract.py`
- `tests/unit/web/sidecar_behavior_runner.js`

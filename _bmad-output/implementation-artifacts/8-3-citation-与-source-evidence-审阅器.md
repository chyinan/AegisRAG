---
baseline_commit: 76b95c0
---

# Story 8.3: Citation 与 Source Evidence 审阅器

Status: review

生成时间：2026-06-09T11:56:31+08:00

## Story

As a 企业员工或交付顾问,
I want 可视化查看每条 citation 为什么可信,
so that 我可以向业务方解释回答不是模型编造的。

## Acceptance Criteria

1. **Source Evidence 支持从 citation identifiers、Open WebUI metadata 或 sidecar link 构建 evidence set**
   - Given 用户打开 `/governance` 的 Source Evidence
   - When 粘贴单条 citation JSON、多条 citations 数组、Open WebUI metadata、或输入 document/version/chunk/page/request/trace identifiers
   - Then UI 解析出候选 citation references，并逐条调用 `POST /sources/resolve` 或后端批准的 source review API 获取授权 excerpt、`source_display_name`、document/version/chunk/page、`title_path`、`retrieval_method`、`score`、`request_id` 和 `trace_id`
   - And 前端不能从 citation 字符串、Open WebUI metadata、URL 参数或本地 state 自行构造 excerpt、来源结论、授权状态、score 或 retrieval method

2. **每条 evidence 都只展示后端确认的安全字段**
   - Given source resolve 返回授权结果
   - When UI 渲染 evidence item
   - Then 只能展示 allowlist 字段：authorization status、`source_display_name`、`source_type`、`document_id`、`version_id`、`chunk_id`、`page_start`、`page_end`、`title_path`、授权 `text_excerpt`、`excerpt_char_count`、`token_count`、`retrieval_method`、`score`、`request_id`、`trace_id`、以及 source resolver 明确允许的安全 metadata
   - And 不展示 raw `source_uri`、object key、本机绝对路径、ACL 原文、full chunk text、prompt、answer text、raw query、provider raw response、SQL、vectors、embeddings、tokens、secrets、raw exception 或未授权 excerpt

3. **denied/not found/soft deleted/inactive/ACL mismatch 使用统一安全失败形态**
   - Given source resolve 返回 denied、not found、soft deleted、inactive version、inactive chunk、page identity mismatch、cross-tenant 或 ACL mismatch
   - When UI 渲染 evidence item
   - Then 使用同一种安全失败状态，不区分资源是否存在
   - And 该 item 清理旧授权 excerpt、旧 source metadata、旧 score 和旧 retrieval method，只保留 request/trace/error_code/failure_stage/next step 等安全字段
   - And 多条 citation 中某条失败时，不影响其它已授权 item，但失败 item 不得继承任何上一次成功内容

4. **多 citation evidence set 可审阅、复制和定位，但不泄露敏感内容**
   - Given 多条 citation 来自同一次回答
   - When 用户审阅 evidence set
   - Then UI 显示每条 citation 的授权状态、页码范围、chunk identity、safe source metadata、score/retrieval method、可复制 identifiers 和安全 request/trace IDs
   - And evidence set 支持复制 synthetic-safe summary，但复制内容不得包含 raw source locator、完整 excerpt 集合、prompt、answer、chunk full text、provider payload、token 或 secret
   - And 长 document_id、version_id、chunk_id、request_id、trace_id 必须安全换行或截断并提供完整值复制方式

5. **复用现有 Source Inspector 与 SourceResolveService，不重建权限或 citation 逻辑**
   - Given 当前已有 `/sources/resolve`、`SourceResolveService`、`SafeSourceMetadata`、`CitationExtractor`、governance shell 和 sidecar JS/CSS
   - When 实现 Source Evidence 审阅器
   - Then 默认扩展现有 no-build static HTML/CSS/JS 和 executable JS test runner
   - And 不新增 React、Next.js、Vite、Node build pipeline、Open WebUI fork、浏览器扩展、前端权限判断器、前端 citation extractor 或第二套 source resolver
   - And 后端 AuthContext、tenant/RBAC/ACL、soft delete、visible version、chunk status、page identity 和 audit 仍由 source resolve service 或等价 application service authoritative 执行

6. **可访问性、响应式和文档验证闭环**
   - Given Source Evidence 在桌面、平板和移动尺寸使用
   - When 用户通过键盘、屏幕阅读器或触控操作
   - Then 支持 tab/焦点顺序、`aria-live`、alert region、非纯颜色状态表达、非 hover-only 操作、批量结果焦点可达和错误提示可读
   - And README 与 `docs/demo/governance-workbench.md`、`docs/demo/source-inspector-sidecar.md` 按本次能力同步说明入口、能力、限制、安全边界和验证命令
   - And 新增/更新测试覆盖 citation parsing、backend calls、safe allowlist、multi-item render、denial stale clearing、copy summary、responsive/accessibility contract 和 README 期望

## Tasks / Subtasks

- [x] 梳理 Source Evidence 输入模型和安全解析规则（AC: 1, 3, 4）
  - [x] 支持单条 citation JSON、多条 citations 数组、Open WebUI metadata 中的 citations/evidence link 参数、以及手动 document/version/chunk/page/request/trace 输入。
  - [x] 解析阶段只产出 `document_id`、`version_id`、`chunk_id`、可选 `page_start/page_end`、`request_id`、`trace_id`、`citation_ref`；不得把 pasted excerpt、answer、prompt、source_uri、object key 或 score 当作可信显示值。
  - [x] 对 malformed JSON、缺少 identifier、page range 不完整、page_end < page_start、重复 citation、超出批量上限的输入提供安全错误，且清理 stale evidence set。
  - [x] 建议批量上限先设为 20 条，和 `CitationExtractionConfig.max_citations` 保持一致；如选择不同上限，必须写入实现说明和测试。

- [x] 扩展 Source Evidence UI（AC: 1, 2, 3, 4, 6）
  - [x] 更新 `apps/web/governance/index.html` 的 Source Evidence panel，从 8.1 链接占位升级为 citation paste/manual input/evidence list 区域。
  - [x] 保留 `/sidecar` 的 Source Inspector-first 单条查询入口；不要把 sidecar 首页改成 governance-first。
  - [x] 每个 evidence item 显示授权/拒绝状态、safe source metadata、页码范围、title path、score/retrieval method、authorized excerpt、request/trace IDs 和 copy identifiers 操作。
  - [x] 失败 item 必须清空旧 excerpt 和旧 source rows；multi-item 局部失败不得污染其它 item。
  - [x] CSS 继续使用紧凑工具型界面，补充 evidence grid/list/timeline-like rows，长 ID 使用 `overflow-wrap: anywhere` 或等价策略。

- [x] 扩展 sidecar JS 安全 allowlists 与 source evidence 行为（AC: 1-5）
  - [x] 在 `apps/web/sidecar/sidecar.js` 中新增 `SAFE_SOURCE_EVIDENCE_FIELDS`、输入解析 helper、批量 resolve helper、render helper、safe failure helper 和 test exports。
  - [x] 复用现有 `buildSourcePayload`、`pickFields`、`renderSafeFailure`、copy helper、auth header helper 和 alert/live region 模式；不要新增 fetch wrapper 绕过现有安全行为。
  - [x] `POST /sources/resolve` 请求体只能发送 source resolve contract 允许的字段；不要发送 pasted answer、prompt、raw metadata、source_uri、object_key 或 full chunk。
  - [x] copy summary 必须使用 client-side allowlist，默认不复制 `text_excerpt` 全文；如复制 excerpt 片段，只允许当前 item 的授权短 excerpt，并在测试中锁定 forbidden fragments absence。

- [x] 如需后端 source review API，保持薄 route + application service 分层（AC: 1, 2, 3, 5）
  - [x] 默认优先直接逐条调用现有 `POST /sources/resolve`；只有当批量性能或 audit 语义需要时，才新增后端批准的 source review API。
  - [x] 若新增 API，route 只负责 schema、AuthContext dependency、service 调用和 envelope；service 必须复用 `SourceResolveService` 或同等 repository/ACL/audit 策略。
  - [x] 新 API 不得返回 raw source_uri、object_key、ACL、full chunk、prompt、answer、provider payload、SQL、vectors、embeddings 或 raw exception。
  - [x] denied/not found/cross-tenant/ACL mismatch 必须保持统一安全失败形态，不泄露目标资源是否存在。

- [x] 测试后端 source resolve 契约不退化（AC: 2, 3, 5）
  - [x] 扩展 `tests/integration/api/test_sources_routes.py`，覆盖 request_id/citation_ref/page range 入参、safe envelope、缺权限服务未调用、forbidden fields absence 和安全拒绝不泄露 identifiers。
  - [x] 扩展 `tests/unit/rag/test_source_resolver.py` 或新增 focused tests，覆盖 citation metadata retrieval_method/score 优先级、safe metadata allowlist、max excerpt truncation、inactive version/chunk/page mismatch/ACL denial 的统一错误。
  - [x] 保持 `tests/unit/rag/test_source_metadata.py` 和 `tests/unit/rag/test_citation_extractor.py` 通过，确认 display name、title_path、forged reference 和 missing page 逻辑不被前端需求带偏。

- [x] 测试前端静态契约和行为（AC: 1-6）
  - [x] 扩展 `tests/unit/web/test_governance_static_contract.py`，验证 Source Evidence 输入区、evidence result regions、safe allowlist、forbidden fragments、ARIA regions、responsive CSS。
  - [x] 扩展 `tests/unit/web/test_sidecar_static_contract.py`，确认 `/sidecar` 仍是 Source Inspector-first，且单条 Source Inspector safe fields 没有退化。
  - [x] 扩展 `tests/unit/web/sidecar_behavior_runner.js`，覆盖 citation JSON 解析、多条 citation 去重/上限、逐条 `/sources/resolve` 调用、授权渲染、单条拒绝 stale clearing、全局 malformed input stale clearing、copy summary allowlist、unknown/partial page 安全处理。
  - [x] 不引入 Playwright、browser automation 或 Node build pipeline，除非实现说明证明现有 runner 无法覆盖核心行为。

- [x] 更新文档、README 和验证命令（AC: 6）
  - [x] 更新 `docs/demo/governance-workbench.md`：说明 Source Evidence 已支持的输入、批量审阅、后端授权、字段白名单、复制限制和 focused tests。
  - [x] 更新 `docs/demo/source-inspector-sidecar.md`：说明 `/sidecar` 继续支持单条 source resolve，`/governance` 提供 evidence set 审阅。
  - [x] 更新 README Build Status、Governance Workbench、Current Limits 或验证段落；不得宣称完整 Eval Evidence、Audit Explorer、Review Queue 已完成。
  - [x] Dev Agent Record 填写实现决策、验证结果和文件列表。

- [x] 建议验证命令（AC: 1-6）
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/api/test_sources_routes.py tests/unit/rag/test_source_resolver.py tests/unit/rag/test_source_metadata.py tests/unit/rag/test_citation_extractor.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/api/test_governance_routes.py tests/integration/api/test_sidecar_routes.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/web/test_governance_static_contract.py tests/unit/web/test_sidecar_static_contract.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/test_readme_expectations.py -q`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`

## Dev Notes

### Current Repository State

- Current HEAD at story creation: `76b95c0 fix(governance): address document review findings`.
- Story 8.1 is done and created `/governance` as a governance-first static entry while preserving `/sidecar` as Source Inspector-first.
- Story 8.2 is done and upgraded only Document Review into a backend-backed tenant-scoped lifecycle board.
- Source Evidence in `/governance` is still a placeholder/link to Source Inspector. Story 8.3 should upgrade Source Evidence only; Eval Evidence、Audit Explorer、Review Queue remain later stories.
- Existing frontend remains no-build static HTML/CSS/JS served by FastAPI. There is no React/Next/Vite/package manifest.

### Existing Files To Read Before Implementation

- `apps/api/routes/sources.py`
  - Current state: thin `POST /sources/resolve` route with `RagQueryContextDep`, `SourceResolveServiceDep`, Pydantic body, and `ApiResponse[SourceResolveResponse]`.
  - What this story may change: usually no change; possibly add tests or a narrow batch API only if justified.
  - Preserve: route remains thin; permission dependency rejects missing `retrieval:query` before service call.

- `packages/rag/source_resolver.py`
  - Current state: authoritative source resolution. It validates document/version/chunk/page identity, visible version status (`retrieval_ready` only), active chunk status, soft delete, tenant and ACL at document/version/chunk levels; returns safe excerpt and audit event.
  - What this story may change: likely tests only; if adding batch source review, reuse this service rather than duplicating logic.
  - Preserve: same safe denial shape for missing, deleted, inactive, cross-tenant and ACL-blocked resources; audit metadata must not include chunk content or secrets.

- `packages/common/source_metadata.py`
  - Current state: sanitizes `source_display_name`, `source_type`, `title_path`, `source_ref` and page ranges; rejects prompt-like titles, local absolute paths, internal schemes, object keys, tokens and secrets.
  - What this story may change: no change expected.
  - Preserve: frontend and docs must treat these fields as backend-safe display values and must not reintroduce raw `source_uri`.

- `packages/rag/dto.py` and `packages/rag/citation_extractor.py`
  - Current state: `Citation` contains safe identifiers/display fields; `CitationExtractor` deduplicates packed sources, rejects forged references, preserves missing pages, and enforces citation source allowlists.
  - What this story may change: no change expected unless a missing field blocks UI; do not create a frontend citation extractor that bypasses these semantics.
  - Preserve: missing pages remain missing; do not invent page numbers or citations.

- `apps/web/governance/index.html`
  - Current state: Source Evidence panel is a paragraph and a button linking to Source Inspector.
  - What this story changes: replace this placeholder with paste/manual input and evidence set regions.
  - Preserve: six governance tabs, Document Review controls, backend evidence tabs, auth helper and no-storage guarantees.

- `apps/web/sidecar/index.html`
  - Current state: `/sidecar` remains Source Inspector-first with single citation input fields and local/test auth helper.
  - What this story changes: probably none, unless shared JS needs IDs available only in governance; do not make sidecar depend on governance-only elements without guards.
  - Preserve: existing single-source flow and existing tests.

- `apps/web/sidecar/sidecar.js`
  - Current state: defines `SAFE_SOURCE_FIELDS`, `SAFE_STATUS_FIELDS`, diagnostics and document review allowlists; calls `/sources/resolve`, document status/review routes, and `/diagnostics/resolve`; exposes `window.sidecarContract` for executable JS tests.
  - What this story changes: add source evidence parsing/batch resolve/render/copy helpers and test exports.
  - Preserve: no local/session storage, no raw response rendering, no console logging of payloads, safe failure clearing, diagnostics report allowlist, document review state clearing.

- `apps/web/sidecar/sidecar.css`
  - Current state: compact operational UI, responsive grid, focus-visible styles, long ID wrapping and non-color-only status chips.
  - What this story changes: add source evidence list/cards/rows and responsive controls.
  - Preserve: no marketing hero/card-heavy UI, no text overflow, mobile layout, focus visibility and non-color-only status.

- `tests/integration/api/test_sources_routes.py`
  - Current state: route returns envelope, rejects missing permission before service call, and safe denial does not echo target identifiers.
  - What this story changes: add focused cases for source evidence usage without making real DB/LLM calls.

- `tests/unit/rag/test_source_resolver.py`
  - Current state: tests authorized excerpt, safe metadata, citation metadata priority, safe denial for missing/deleted/inactive/ACL/cross tenant.
  - What this story changes: add any missing edge cases discovered during implementation.

- `tests/unit/web/test_governance_static_contract.py`, `tests/unit/web/test_sidecar_static_contract.py`, `tests/unit/web/sidecar_behavior_runner.js`
  - Current state: static and executable JS tests already cover governance navigation, Document Review rendering, safe failure clearing, Source Inspector fields and no forbidden fragments.
  - What this story changes: add Source Evidence evidence set tests.
  - Preserve: no Playwright/browser dependency unless absolutely justified.

- `README.md`, `docs/demo/governance-workbench.md`, `docs/demo/source-inspector-sidecar.md`
  - Current state: docs describe 8.2 Document Review and placeholder Source Evidence.
  - What this story changes: document Source Evidence evidence set behavior and limits.

### Previous Story Intelligence

- Story 8.1 review patches fixed entry identity, governance navigation wiring, safe failure next steps, keyboard tab behavior, responsive overflow and stale alert clearing. Do not regress those.
- Story 8.2 review patches fixed safe detail failure responses, source display sanitization, nested unsafe error summaries, deleted latest-version selection, cursor bounds and stale Document Review UI state.
- Recent review fixes repeatedly caught stale authorized data after failures. Treat stale clearing for Source Evidence items as a hard requirement.
- Current frontend testing strategy intentionally combines Python static contract tests with a Node `vm` behavior runner. Reuse it before adding heavier tooling.
- Governance workbench is presentation only. Backend AuthContext、RBAC、ACL、source resolve、diagnostics、audit and future eval/review APIs remain authoritative.

### Implementation Guardrails

- Do not expose `source_uri`, `object_key`, ACL JSON, full chunks, prompts, answers, raw queries, provider payloads, SQL, vectors, embeddings, tokens or raw exceptions in Source Evidence.
- Do not infer authorization from citation metadata. Every evidence item must be resolved through backend authorization.
- Do not trust pasted `text_excerpt`, `source_display_name`, `score`, `retrieval_method` or `authorization_status`; pasted input only supplies identifiers.
- Do not add a new frontend framework, state management library, Open WebUI fork, frontend plugin or browser extension for this story.
- Do not add eval report browsing, audit explorer query/export or review queue persistence in this story. Those belong to Stories 8.5-8.7.
- Do not change `SourceResolveService.VISIBLE_VERSION_STATUSES` casually. If source evidence must support non-`retrieval_ready` versions, it requires explicit product/security decision and tests.
- Keep page range validation strict: both page_start/page_end set together, 1-based, and matching chunk page identity if provided.

### Latest Technical Information

- No new external framework is required. Current `pyproject.toml` pins FastAPI `>=0.136.3,<0.137`, Pydantic `>=2.13.4,<3`, SQLAlchemy `>=2.0.50,<3`, pytest `>=9.0.0,<10`, ruff `>=0.14.0,<1`.
- FastAPI static asset and APIRouter patterns remain sufficient for `/governance` and `/sidecar`; continue serving no-build assets via thin routes/static mounts.
- MDN documents `aria-live` as the mechanism for notifying assistive technologies about dynamic region changes; evidence list updates and safe failures should use existing live/alert regions.
- MDN Clipboard API `writeText()` requires a secure context in browsers; keep the existing copy fallback pattern for local/test and non-secure contexts.
- WAI-ARIA tabs guidance expects arrow-key navigation and correct `role="tablist"`, `role="tab"`, `role="tabpanel"`, `aria-selected` and focus behavior; preserve the 8.1 governance tab behavior while adding Source Evidence controls.

### References

- `_bmad-output/planning-artifacts/epics.md#Story-8.3-Citation-与-Source-Evidence-审阅器`
- `_bmad-output/planning-artifacts/epics.md#Epic-8-企业审阅治理前端与可信证据工作台`
- `_bmad-output/planning-artifacts/architecture.md#Frontend-Architecture`
- `_bmad-output/planning-artifacts/architecture.md#API-&-Communication-Patterns`
- `_bmad-output/planning-artifacts/architecture.md#Authentication-&-Security`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md#Information-Architecture`
- `project-context.md#11-RAG-Generation-规则`
- `project-context.md#13-Prompt-Injection-防护`
- `project-context.md#16-权限规则`
- `project-context.md#18-可观测性规则`
- `_bmad-output/implementation-artifacts/8-1-审阅治理工作台信息架构与前端边界.md`
- `_bmad-output/implementation-artifacts/8-2-文档生命周期审阅看板.md`
- `apps/api/routes/sources.py`
- `packages/rag/source_resolver.py`
- `packages/common/source_metadata.py`
- `packages/rag/citation_extractor.py`
- `packages/rag/dto.py`
- `apps/web/governance/index.html`
- `apps/web/sidecar/index.html`
- `apps/web/sidecar/sidecar.js`
- `apps/web/sidecar/sidecar.css`
- `tests/integration/api/test_sources_routes.py`
- `tests/unit/rag/test_source_resolver.py`
- `tests/unit/rag/test_source_metadata.py`
- `tests/unit/rag/test_citation_extractor.py`
- `tests/unit/web/test_governance_static_contract.py`
- `tests/unit/web/test_sidecar_static_contract.py`
- `tests/unit/web/sidecar_behavior_runner.js`
- `docs/demo/governance-workbench.md`
- `docs/demo/source-inspector-sidecar.md`
- `README.md`
- FastAPI StaticFiles docs: https://fastapi.tiangolo.com/tutorial/static-files/
- FastAPI APIRouter docs: https://fastapi.tiangolo.com/tutorial/bigger-applications/
- MDN `aria-live`: https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Attributes/aria-live
- MDN Clipboard `writeText()`: https://developer.mozilla.org/en-US/docs/Web/API/Clipboard/writeText
- WAI-ARIA Tabs Pattern: https://www.w3.org/WAI/ARIA/apg/patterns/tabs/

## Validation Checklist

Validation Result: PASS（2026-06-09T11:56:31+08:00）

- [x] Story 明确 8.3 只实现 Source Evidence evidence set 审阅，不扩展 eval/audit/review queue。
- [x] Acceptance Criteria 覆盖 citation metadata 输入、后端 source resolve、safe field allowlist、统一安全失败、多 citation 审阅、复用现有 shell/service、可访问性和 docs/tests。
- [x] Tasks 指向现有 sources route、SourceResolveService、SafeSourceMetadata、CitationExtractor、governance HTML、sidecar JS/CSS 和测试文件，避免重建前端栈或权限逻辑。
- [x] Dev Notes 记录前序 8.1/8.2 learnings、recent git patterns、unsafe field 防线和 no new framework 约束。
- [x] 明确禁止 raw source URI、object key、本机路径、ACL、全文、prompt、answer、raw query、SQL、vectors、embeddings、provider payload、token 和 raw exception。
- [x] README 同步要求已写入 AC/Tasks；本次 create-story 只创建 story，不实现功能，因此不更新 README。

## Change Log

- 2026-06-09: Created comprehensive Story 8.3 developer context for Citation and Source Evidence review.
- 2026-06-09: Implemented Source Evidence reviewer and marked story ready for review.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- 2026-06-09: Red phase confirmed Source Evidence static/behavior tests failed before implementation.
- 2026-06-09: Full regression passed: `.venv\Scripts\python.exe -m pytest` -> 979 passed.
- 2026-06-09: Quality checks passed: `.venv\Scripts\python.exe -m ruff check .`; `.venv\Scripts\python.exe -m mypy apps packages tests`.

### Completion Notes List

- Implemented governance Source Evidence input, parsing, batch source resolve, safe authorized rendering, uniform safe failure rendering, stale clearing, and allowlisted copy summary.
- Reused existing `/sources/resolve` and `SourceResolveService`; no new backend source review API, framework, build pipeline, or frontend permission logic was added.
- Added backend and frontend tests for evidence identifiers, page range validation, safe field allowlists, multi-item rendering, denial stale clearing, malformed input clearing, and copy-summary redaction.
- Updated README and demo docs for Source Evidence capabilities, limits, safety boundary, and verification commands.

### File List

- README.md
- _bmad-output/implementation-artifacts/8-3-citation-与-source-evidence-审阅器.md
- _bmad-output/implementation-artifacts/sprint-status.yaml
- apps/web/governance/index.html
- apps/web/sidecar/sidecar.css
- apps/web/sidecar/sidecar.js
- docs/demo/governance-workbench.md
- docs/demo/source-inspector-sidecar.md
- tests/integration/api/test_sources_routes.py
- tests/unit/rag/test_source_resolver.py
- tests/unit/web/sidecar_behavior_runner.js
- tests/unit/web/test_governance_static_contract.py

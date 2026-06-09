---
baseline_commit: 21a93f7
---

# Story 8.4: Retrieval Diagnostics 安全时间线

Status: done

生成时间：2026-06-09T13:16:10+08:00

## Story

As a 平台工程师,
I want 用安全时间线解释一次回答的检索链路,
so that dense、BM25、RRF、rerank、context packing 和 no-answer 不再只是技术名词。

## Acceptance Criteria

1. **治理工作台内置 Retrieval Diagnostics 查询与安全时间线**
   - Given 用户打开 `/governance` 的 Retrieval Diagnostics
   - When 输入 `request_id` 或 `trace_id` 并提交
   - Then UI 调用 `POST /diagnostics/resolve` 展示后端确认的阶段时间线：auth scope、metadata/ACL filters、dense top_k、sparse top_k、RRF result_count、dedup count、highest rerank score、threshold decision、packed chunk count、citation count、latency、status、error_code
   - And 前端不得展示 raw query、chunk content、prompt、SQL、vectors、embeddings、provider payload、token、secret、raw exception 或本机路径

2. **后端 Diagnostics DTO/service 支持检索链路阶段细分**
   - Given retrieval log metadata 已包含 `rrf`、`rerank`、dense/sparse top_k、result_count、context/citation/audit metadata
   - When `DiagnosticsService.resolve()` 聚合 retrieval 和 audit records
   - Then response 必须以安全 DTO 暴露 stable stage entries，至少覆盖 `retrieval`、`sparse_retrieval`、`rrf_merge`、`rerank`、`context_packing`、`generation`、`citation`、`permission`、`infrastructure`
   - And stage `counts` 只能包含 allowlist 数字或短状态字段，不包含 query text、candidate text、chunk IDs 列表、SQL、vector、embedding 或 provider payload

3. **失败阶段映射和安全下一步可复盘**
   - Given retrieval 在任一阶段失败或降级
   - When 后端返回 `failure_stage`、`error_code` 或 stage status
   - Then UI 用非纯颜色状态标记 retrieval、sparse retrieval、RRF merge、rerank、context packing、generation、citation、permission 或 infrastructure
   - And 展示后端给出的下一步验证命令或 safe report filename，不从前端自由拼接内部 SQL、文件路径或秘密配置

4. **权限和租户边界由后端执行**
   - Given 用户没有 `diagnostics:read` 或 `audit:read`
   - When 调用 diagnostics API 或在 UI 尝试查看历史结果
   - Then API 返回统一结构化拒绝
   - And UI 清理旧 timeline、summary、report 和 copy/export state，不使用本地缓存或历史 state 展示受限数据
   - And diagnostics lookup 只按当前 AuthContext 的 `tenant_id` 读取记录，不能由前端传入 tenant/user 扩权

5. **复用现有 no-build static 前端与 Diagnostics API**
   - Given 当前已有 `/diagnostics/resolve`、`DiagnosticsService`、`DiagnosticsResolveResponse`、governance shell、sidecar diagnostics tab、JS behavior runner 和 static contract tests
   - When 实现 Retrieval Diagnostics 安全时间线
   - Then 默认扩展现有 no-build HTML/CSS/JS 和 diagnostics service
   - And 不新增 React、Next.js、Vite、browser extension、Open WebUI fork、Grafana replacement、前端权限判断器、前端 retrieval log parser 或第二套 diagnostics endpoint

6. **可访问性、响应式、文档和验证闭环**
   - Given Retrieval Diagnostics 在桌面、平板和移动尺寸使用
   - When 用户通过键盘、屏幕阅读器或触控操作
   - Then 保留 governance tabs 的 ARIA/focus 行为，timeline 更新使用 `aria-live`，失败使用 alert region，状态含图标/文本而非只靠颜色，长 request/trace IDs 安全换行或截断并可复制
   - And README、`docs/demo/governance-workbench.md`、`docs/demo/source-inspector-sidecar.md` 按本次能力同步说明入口、能力、限制、安全边界和验证命令
   - And 新增/更新测试覆盖 safe DTO、stage mapping、forbidden-field absence、permission denial stale clearing、timeline render、safe report export、responsive/accessibility contract 和 README 期望

## Tasks / Subtasks

- [x] 扩展 Diagnostics domain DTO 和 stage contract（AC: 1, 2, 3, 4）
  - [x] 在 `packages/diagnostics/dto.py` 中补齐 `FailureStage` 枚举或等价 stable stage 名称：`sparse_retrieval`、`rrf_merge`；保留现有 `retrieval`、`rerank`、`context_packing`、`generation`、`citation`、`permission`、`source_resolution`、`audit`、`infrastructure`、`unknown` 兼容。
  - [x] 如需要，新增 `DiagnosticsTimelineItem` 或扩展 `DiagnosticsStageSummary`，但保持 Pydantic v2 DTO、frozen model 和 API envelope 兼容。
  - [x] stage payload 只允许短 label/status、latency、error_code、safe counts、safe decision 字段；不要把 retrieval candidate IDs、query text、chunk text、prompt、SQL、vectors、embeddings 或 provider response 放入 DTO。
  - [x] `DiagnosticsLookupRequest` 继续只接受 `request_id`、`trace_id`、`include_report`；不接受 `tenant_id`、`user_id`、permissions 或任意 metadata filter。

- [x] 扩展 `DiagnosticsService` 聚合安全时间线（AC: 1, 2, 3, 4）
  - [x] 更新 `packages/diagnostics/service.py` 的 `_build_stages()`，从 `RetrievalLogRecord.metadata` 中读取并安全归一化：`dense_top_k`、`sparse_top_k`、`rrf.input_counts`、`rrf.deduped_count`、`rrf.filtered_count`、`rrf.threshold`、`rerank.status`、`rerank.input_count`、`rerank.output_count`、`rerank.safe_counts`、`rerank.error_code`。
  - [x] 从 audit metadata 继续读取 context packing、generation、citation、stream event 和 token usage 的 safe counts；不要新增对 raw query/full answer/prompt 的读取路径。
  - [x] 明确 threshold decision：可用 `threshold` + `filtered_count/result_count` 或后端已有 metadata 推导为 safe short value；如果 metadata 不足，返回 `not_available`，不要前端猜测。
  - [x] 更新 `_stage_from_audit()` / `_coerce_stage()`，识别 `sparse_retrieval`、`rrf_merge`、`hybrid_merge`、`bm25`、`full_text`、`threshold_filter` 等别名。
  - [x] 确保 permission denial、cross-tenant not found、storage failures 不泄露目标记录是否存在或其它 tenant/user 信息。
  - [x] 保留 `has_diagnostics_read_permission()` 作为唯一权限入口，满足 `audit:read` 或 `diagnostics:read` 即可。

- [x] 保持 API route 薄层，仅按需补测试（AC: 2, 4）
  - [x] `apps/api/routes/diagnostics.py` 应继续只处理 AuthenticatedRequestContext dependency、service 调用和 `success_response`，不要在 route 中聚合 retrieval log 或解析 metadata。
  - [x] `apps/api/service_dependencies.py` 继续注入 `RetrievalLogRepository` 和 `AuditLogRepository`；如新增 repository 方法，必须仍按 `tenant_id` 限定读取。
  - [x] `tests/integration/api/test_diagnostics_routes.py` 覆盖新 stage 字段、权限拒绝、invalid lookup before service call、forbidden fragments absence 和 safe envelope。

- [x] 将 `/governance` 的 Retrieval Diagnostics 占位升级为原生安全时间线（AC: 1, 3, 4, 5, 6）
  - [x] 更新 `apps/web/governance/index.html` 的 `governance-view-retrieval-diagnostics`，加入 request/trace lookup form、summary region、timeline region、next steps/report copy/export controls。
  - [x] 可复用现有 backend Diagnostics tab 的 input IDs 或新增 governance-scoped IDs；如果复用，必须确保切换 tab 不触发自动 lookup，不泄露上次结果。
  - [x] 继续保留后端 Diagnostics tab，避免破坏 `/sidecar` 和 `/governance` 共享 JS 的现有 tests。
  - [x] UI 文案聚焦工作台操作，不写“如何使用功能”的大段说明；空状态和失败状态只显示安全边界和下一步。

- [x] 扩展 `sidecar.js` 的 diagnostics allowlist、render 和 copy/export 行为（AC: 1, 3, 4, 5）
  - [x] 增加 `SAFE_DIAGNOSTICS_TIMELINE_FIELDS` 或等价 allowlist，覆盖 stage name/status/latency/error_code/counts/safe decision。
  - [x] 增加 governance diagnostics fetch/render helper 和 test exports；复用 `DIAGNOSTICS_ENDPOINT`、`buildHeaders()`、`pickFields()`、`renderDiagnosticsFailure()`、copy helper、live/alert region 模式。
  - [x] 成功前立即清理旧 timeline、summary、next_steps 和 `state.diagnosticsReport`，避免慢请求期间仍可复制上一次授权结果。
  - [x] 失败、403、404、network error、malformed response 均必须清理旧数据，只渲染 request_id、trace_id、failure_stage、error_code、safe next step。
  - [x] Copy/export report 使用后端/前端双 allowlist，文件名只使用 sanitized request/trace id；不要导出 raw query、prompt、chunk text、SQL、vectors、embeddings、provider payload 或 raw exception。
  - [x] 不读取或写入 `localStorage`、`sessionStorage`、cookie、URL history 或 console log。

- [x] 扩展 CSS 为紧凑的时间线/阶段摘要（AC: 1, 3, 6）
  - [x] 更新 `apps/web/sidecar/sidecar.css`，新增 diagnostics timeline/list/stage row 样式，保持工具型、密集、可扫描。
  - [x] 状态必须有文本和符号，不只依赖颜色；长 ID 和 command 使用 `overflow-wrap: anywhere`。
  - [x] 移动端保持单列，按钮文本不溢出，timeline 不遮挡后续内容。
  - [x] 不引入 hero、营销卡片、装饰渐变、嵌套卡片或一整页单色主题。

- [x] 测试后端 diagnostics stage 聚合和安全边界（AC: 1, 2, 3, 4）
  - [x] 扩展 `tests/unit/diagnostics/test_dto.py`，验证新增 stage enum/DTO 序列化只含 safe fields。
  - [x] 扩展 `tests/unit/diagnostics/test_service.py`，覆盖 dense/sparse/RRF/rerank/context/generation/citation timeline、threshold not_available、alias mapping、storage failure redaction、tenant isolation 和 `diagnostics:read` 权限。
  - [x] 扩展 `tests/integration/storage/test_retrieval_log_repositories.py` 或相关 storage tests，确认 metadata redaction 不允许敏感 key 进入 diagnostics 读取结果。
  - [x] 不真实调用外部 LLM、embedding provider、browser 或数据库外部服务。

- [x] 测试前端静态契约和 JS 行为（AC: 1, 3, 4, 5, 6）
  - [x] 扩展 `tests/unit/web/test_governance_static_contract.py`，验证 Retrieval Diagnostics form、summary/timeline/next-step/report regions、ARIA live/alert、forbidden fragments absence 和 responsive CSS。
  - [x] 扩展 `tests/unit/web/test_sidecar_static_contract.py`，确认 `/sidecar` diagnostics 仍可用，safe allowlists 不包含 raw query、prompt、chunk、source_uri、SQL、vectors、embeddings、provider payload、token、secret。
  - [x] 扩展 `tests/unit/web/sidecar_behavior_runner.js`，覆盖 governance diagnostics lookup safe payload、timeline rendering、permission failure stale clearing、new lookup clears old copy/export, report allowlist, sanitized filename, next steps clearing, no auto lookup on tab switch。
  - [x] 不引入 Playwright、Node build pipeline 或浏览器自动化，除非现有 runner 无法覆盖核心行为且实现说明给出理由。

- [x] 更新 README 和 demo docs（AC: 6）
  - [x] 更新 `docs/demo/governance-workbench.md`，说明 Retrieval Diagnostics 已支持 request/trace lookup、安全时间线、字段白名单、权限要求、失败语义和 focused tests。
  - [x] 更新 `docs/demo/source-inspector-sidecar.md`，说明 `/sidecar` Diagnostics 与 `/governance` Retrieval Diagnostics 的关系：同一 backend API，不是完整 trace viewer/Grafana/prompt viewer。
  - [x] 更新 README Build Status、Governance Workbench、Current Limits 或验证段落；不得宣称 Eval Evidence、Audit Explorer、Review Queue 已完成。
  - [x] Dev Agent Record 填写实现决策、验证结果和文件列表。

- [x] 建议验证命令（AC: 1-6）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/diagnostics tests/integration/api/test_diagnostics_routes.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/storage/test_retrieval_log_repositories.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/web/test_governance_static_contract.py tests/unit/web/test_sidecar_static_contract.py -q`
  - [x] `node tests/unit/web/sidecar_behavior_runner.js`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/test_readme_expectations.py -q`
- [x] `.venv\Scripts\python.exe -m ruff check .`
- [x] `.venv\Scripts\python.exe -m mypy apps packages tests`

### Review Findings

- [x] [Review][Patch] Nested retrieval stage `error_code` values can leak raw diagnostic text [packages/diagnostics/service.py:291]
- [x] [Review][Patch] Context packing, generation, and citation stages are marked success whenever any audit record exists [packages/diagnostics/service.py:304]
- [x] [Review][Patch] Sparse retrieval and RRF stages fall back to overall retrieval success when stage metadata is absent [packages/diagnostics/service.py:286]
- [x] [Review][Patch] Backend stage counts are not constrained to a fixed diagnostics allowlist [packages/diagnostics/service.py:411]
- [x] [Review][Patch] Governance diagnostics report copy/download can export a stale backend Diagnostics-tab report [apps/web/sidecar/sidecar.js:491]
- [x] [Review][Patch] Governance diagnostics failures can leave shared backend Diagnostics DOM stale [apps/web/sidecar/sidecar.js:1113]
- [x] [Review][Patch] New sparse/RRF failure stages fall through to unrelated UI/static next steps [packages/diagnostics/service.py:816]

## Dev Notes

### Current Repository State

- Current HEAD at story creation: `21a93f7 fix(governance): address source evidence review findings`.
- Story 8.1 created the no-build `/governance` workbench shell with six stable entries and ARIA tab behavior.
- Story 8.2 implemented Document Review in the governance shell using backend document lifecycle APIs.
- Story 8.3 implemented Source Evidence evidence-set review using existing `/sources/resolve`; review patches fixed stale evidence visibility, direct sidecar/evidence link parsing, unsafe request header reuse, and metadata value sanitization.
- Retrieval Diagnostics currently exists in the shared backend-backed sidecar tab and calls `POST /diagnostics/resolve`; the governance Retrieval Diagnostics panel is still a placeholder/button.
- Existing frontend remains static HTML/CSS/JS served by FastAPI. There is no React/Next/Vite/package manifest and no Node build step.

### Existing Files To Read Before Implementation

- `apps/api/routes/diagnostics.py`
  - Current state: thin `POST /diagnostics/resolve` route with `AuthenticatedRequestContextDep`, `DiagnosticsServiceDep`, Pydantic body and `ApiResponse[DiagnosticsResolveResponse]`.
  - What this story may change: usually no route change; tests may assert route remains thin and returns new safe timeline fields from service.
  - Preserve: route must not parse retrieval logs, read DB, or decide permissions beyond dependencies/service.

- `packages/diagnostics/dto.py`
  - Current state: `DiagnosticsLookupRequest`, `DiagnosticsStageSummary`, `DiagnosticsSummary`, `DiagnosticsReport`, `DiagnosticsResolveResponse`; `FailureStage` lacks `sparse_retrieval` and `rrf_merge`.
  - What this story changes: add/extend stable stage vocabulary and maybe timeline item DTOs.
  - Preserve: safe fields only, frozen Pydantic models, invalid lookup maps to `DIAGNOSTICS_INVALID_LOOKUP`.

- `packages/diagnostics/service.py`
  - Current state: enforces `has_diagnostics_read_permission`, reads retrieval/audit records by tenant + request/trace, aggregates summary/stages/next_steps/report, redacts query/answer/source_uri by never exposing unsafe metadata.
  - What this story changes: richer stage extraction from retrieval log metadata and stage alias mapping.
  - Preserve: tenant isolation, safe not found, safe storage failure, no raw query/chunk/prompt/provider payload.

- `packages/auth/policies.py`
  - Current state: `DIAGNOSTICS_READ_PERMISSIONS = {"audit:read", "diagnostics:read"}` and `has_diagnostics_read_permission()` accepts either.
  - What this story changes: no change expected.
  - Preserve: do not require both permissions unless product/security decision changes; story AC says either permission is valid.

- `packages/retrieval/dto.py`
  - Current state: `RetrievalLogCreate/Record` carries top_k/result_count/rerank_score/error_code/query_summary/metadata; metadata is arbitrary dict and must stay redacted by repository.
  - What this story changes: no DTO change expected unless needed for safe metadata typing.
  - Preserve: retrieval logs are safe summaries, not replay of raw query/content.

- `packages/retrieval/storage/repositories.py`
  - Current state: redacts sensitive retrieval metadata keys including `query`, `full_query`, `prompt`, `sql`, `vector`, `embedding`, `chunk_content`, provider raw response and related variants.
  - What this story changes: maybe add redaction coverage tests or keys if timeline introduces new metadata names.
  - Preserve: repository must not persist unsafe diagnostics material.

- `apps/api/service_dependencies.py`
  - Current state: constructs `DiagnosticsService(retrieval_logs=RetrievalLogRepository(session), audit_logs=AuditLogRepository(session))`; retrieval pipeline trace provider already exposes `rrf` and `rerank` safe metadata to retrieve app logs.
  - What this story changes: usually no dependency change.
  - Preserve: do not create a second diagnostics service or a frontend-only parser.

- `apps/web/governance/index.html`
  - Current state: Retrieval Diagnostics governance panel is a paragraph and button linking to the backend Diagnostics tab; Source Evidence and Document Review are implemented.
  - What this story changes: add governance-native request/trace lookup, summary, timeline, next steps/report regions.
  - Preserve: six governance tabs, Document Review, Source Evidence, no-storage auth helper, and shared script include.

- `apps/web/sidecar/index.html`
  - Current state: `/sidecar` is Source Inspector-first with tabs for Source Inspector, Job Status and Diagnostics.
  - What this story changes: likely none or minimal if shared diagnostics controls are needed.
  - Preserve: sidecar must remain Source Inspector-first; do not add governance shell to `/sidecar`.

- `apps/web/sidecar/sidecar.js`
  - Current state: defines `SAFE_DIAGNOSTICS_SUMMARY_FIELDS`, `SAFE_DIAGNOSTICS_STAGE_FIELDS`, `SAFE_DIAGNOSTICS_REPORT_FIELDS`, `fetchDiagnostics()`, `renderDiagnosticsResult()`, report copy/download, governance tabs, Document Review and Source Evidence behavior.
  - What this story changes: add governance diagnostics helpers and timeline rendering; extend diagnostics allowlists.
  - Preserve: no local/session storage, no console logging, no unsafe field names in allowlists, stale data clearing before/after failures.

- `apps/web/sidecar/sidecar.css`
  - Current state: compact operational UI, responsive governance tabs, diagnostics rows, focus-visible styles and long ID wrapping.
  - What this story changes: add diagnostics timeline/list/stage styles.
  - Preserve: responsive single column, no overlap, text fits buttons/rows, non-color-only status.

- `tests/unit/diagnostics/test_service.py` and `tests/unit/diagnostics/test_dto.py`
  - Current state: cover safe aggregation, trace lookup, tenant isolation, permission, not found, failure-stage mapping and storage failure redaction.
  - What this story changes: add timeline/stage extraction and alias tests.

- `tests/integration/api/test_diagnostics_routes.py`
  - Current state: route envelope, trace lookup, forbidden/not found, invalid lookup and forbidden fragments.
  - What this story changes: add new safe stage fields and permission/stale denial expectations if needed.

- `tests/unit/web/test_governance_static_contract.py`, `tests/unit/web/test_sidecar_static_contract.py`, `tests/unit/web/sidecar_behavior_runner.js`
  - Current state: static and executable JS tests cover governance nav, Document Review, Source Evidence, sidecar diagnostics safe payload/failure/export.
  - What this story changes: add Retrieval Diagnostics timeline tests and stale clearing on governance diagnostics interactions.

- `README.md`, `docs/demo/governance-workbench.md`, `docs/demo/source-inspector-sidecar.md`
  - Current state: docs already mention diagnostics summary and that it is not a full trace viewer/Grafana/prompt viewer.
  - What this story changes: document Retrieval Diagnostics safety timeline and current limits.

### Previous Story Intelligence

- Story 8.1 review caught governance entry identity, navigation wiring, keyboard behavior, responsive overflow and stale alert issues. Preserve tablist semantics and stale alert clearing.
- Story 8.2 review caught safe detail failure responses, nested unsafe error summaries, deleted latest-version selection, cursor bounds and stale Document Review UI state. Apply the same stale clearing discipline to diagnostics timeline/report state.
- Story 8.3 review caught stale evidence copyability during new resolves, direct evidence link parsing, pasted `request_id` being reused as current `X-Request-ID`, and unsafe metadata rendering. Do not use looked-up request IDs as current auth/request headers; they are lookup inputs only.
- Recent frontend strategy is intentionally no-build: Python static contract tests plus Node `vm` behavior runner. Reuse this pattern.
- Governance workbench is presentation only. Backend AuthContext、RBAC、ACL、retrieval logs、audit logs and diagnostics service remain authoritative.

### Implementation Guardrails

- Do not expose raw query, query text, answer text, prompt, full chunk, source_uri, object_key, ACL JSON, SQL, vectors, embeddings, provider raw response, token, secret, raw exception or local absolute paths.
- Do not let the frontend derive tenant/user scope, authorization status, failure stage or threshold decision from pasted IDs or local state.
- Do not pass `tenant_id` or `user_id` in diagnostics lookup payload. AuthContext from headers/JWT is authoritative.
- Do not create a new diagnostics endpoint unless a concrete backend contract need appears; prefer extending `POST /diagnostics/resolve`.
- Do not add Eval Evidence, Audit Explorer or Review Queue behavior in this story; Stories 8.5-8.7 own those.
- Do not turn diagnostics into a prompt viewer, chunk viewer, vector viewer, SQL viewer, Grafana replacement or full retrieval replay tool.
- If retrieval metadata lacks a field, render `not_available` or omit it; do not infer from unrelated fields.

### Latest Technical Information

- No new external framework is required. Current `pyproject.toml` pins FastAPI `>=0.136.3,<0.137`, Pydantic `>=2.13.4,<3`, SQLAlchemy `>=2.0.50,<3`, pytest `>=9.0.0,<10`, ruff `>=0.14.0,<1`.
- MDN documents `aria-live` as the correct mechanism for dynamic region updates; use polite updates for successful timeline loads and alert/assertive behavior for errors.
- MDN Clipboard `writeText()` is Promise-based and available only in secure contexts in browsers; keep existing copy fallback behavior and tests for unavailable clipboard.
- WAI-ARIA APG tabs pattern expects `role="tablist"`, `role="tab"`, `role="tabpanel"`, `aria-selected`, `aria-controls`, keyboard arrow navigation and focus behavior; preserve the 8.1 governance tab implementation.

### References

- `_bmad-output/planning-artifacts/epics.md#Story-8.4-Retrieval-Diagnostics-安全时间线`
- `_bmad-output/planning-artifacts/epics.md#Epic-8-企业审阅治理前端与可信证据工作台`
- `_bmad-output/planning-artifacts/architecture.md#Frontend-Architecture`
- `_bmad-output/planning-artifacts/architecture.md#API-&-Communication-Patterns`
- `_bmad-output/planning-artifacts/architecture.md#Authentication-&-Security`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md#Retrieval-Diagnostics`
- `project-context.md#10-Retrieval-规则`
- `project-context.md#16-权限规则`
- `project-context.md#18-可观测性规则`
- `_bmad-output/implementation-artifacts/8-1-审阅治理工作台信息架构与前端边界.md`
- `_bmad-output/implementation-artifacts/8-2-文档生命周期审阅看板.md`
- `_bmad-output/implementation-artifacts/8-3-citation-与-source-evidence-审阅器.md`
- `apps/api/routes/diagnostics.py`
- `packages/diagnostics/dto.py`
- `packages/diagnostics/service.py`
- `packages/auth/policies.py`
- `packages/retrieval/dto.py`
- `packages/retrieval/storage/repositories.py`
- `apps/web/governance/index.html`
- `apps/web/sidecar/index.html`
- `apps/web/sidecar/sidecar.js`
- `apps/web/sidecar/sidecar.css`
- `tests/unit/diagnostics/test_service.py`
- `tests/unit/diagnostics/test_dto.py`
- `tests/integration/api/test_diagnostics_routes.py`
- `tests/unit/web/test_governance_static_contract.py`
- `tests/unit/web/test_sidecar_static_contract.py`
- `tests/unit/web/sidecar_behavior_runner.js`
- `docs/demo/governance-workbench.md`
- `docs/demo/source-inspector-sidecar.md`
- `README.md`
- MDN `aria-live`: https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Attributes/aria-live
- MDN Clipboard `writeText()`: https://developer.mozilla.org/en-US/docs/Web/API/Clipboard/writeText
- WAI-ARIA Tabs Pattern: https://www.w3.org/WAI/ARIA/apg/patterns/tabs/

## Validation Checklist

Validation Result: PASS（2026-06-09T13:16:10+08:00）

- [x] Story 明确 8.4 只实现 Retrieval Diagnostics 安全时间线，不扩展 Eval Evidence、Audit Explorer 或 Review Queue。
- [x] Acceptance Criteria 覆盖 request/trace lookup、后端 diagnostics API、stage summary、失败阶段、权限拒绝、stale clearing、可访问性、docs/tests。
- [x] Tasks 指向现有 diagnostics route/service/DTO、retrieval log repository、governance HTML、sidecar JS/CSS 和测试文件，避免重建前端栈或观测系统。
- [x] Dev Notes 记录前序 8.1/8.2/8.3 learnings、recent git patterns、unsafe field 防线和 no new framework 约束。
- [x] 明确禁止 raw query、chunk content、prompt、SQL、vectors、embeddings、provider payload、token、secret、source_uri、object key、本机路径和 raw exception。
- [x] README 同步要求已写入 AC/Tasks；本次 create-story 只创建 story，不实现功能，因此不更新 README。

## Change Log

- 2026-06-09: Created comprehensive Story 8.4 developer context for Retrieval Diagnostics secure timeline.
- 2026-06-09: Implemented Retrieval Diagnostics secure timeline, safe DTO/service aggregation, governance UI, tests, and docs.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- `.venv\Scripts\python.exe -m pytest tests/unit/diagnostics tests/integration/api/test_diagnostics_routes.py -q` -> 23 passed
- `.venv\Scripts\python.exe -m pytest tests/integration/storage/test_retrieval_log_repositories.py -q` -> 8 passed
- `.venv\Scripts\python.exe -m pytest tests/unit/web/test_governance_static_contract.py tests/unit/web/test_sidecar_static_contract.py -q` -> 48 passed
- `node tests/unit/web/sidecar_behavior_runner.js` -> passed
- `.venv\Scripts\python.exe -m pytest tests/unit/test_readme_expectations.py -q` -> 2 passed
- `.venv\Scripts\python.exe -m ruff check .` -> passed
- `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed
- `.venv\Scripts\python.exe -m pytest -q` -> 992 passed
- Code review fixes:
  - `.venv\Scripts\python.exe -m pytest tests/unit/diagnostics tests/integration/api/test_diagnostics_routes.py -q` -> 28 passed
  - `.venv\Scripts\python.exe -m pytest tests/integration/storage/test_retrieval_log_repositories.py -q` -> 8 passed
  - `.venv\Scripts\python.exe -m pytest tests/unit/web/test_governance_static_contract.py tests/unit/web/test_sidecar_static_contract.py -q` -> 48 passed
  - `node tests/unit/web/sidecar_behavior_runner.js` -> passed
  - `.venv\Scripts\python.exe -m pytest tests/unit/test_readme_expectations.py -q` -> 2 passed
  - `.venv\Scripts\python.exe -m ruff check .` -> passed
  - `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed

### Completion Notes List

- Extended Diagnostics DTO/service with `sparse_retrieval` and `rrf_merge` stages, safe count/decision payloads, threshold decision handling, retrieval alias mapping, and stable stage timelines.
- Hardened retrieval log metadata redaction for diagnostics-sensitive fields such as candidate IDs, chunk IDs, source URI, object keys, provider payloads, tokens, secrets, and raw exceptions.
- Upgraded `/governance` Retrieval Diagnostics from placeholder to request/trace lookup with safe summary, timeline, next steps, report copy/download, stale clearing, ARIA live regions, and no new frontend build stack.
- Updated sidecar diagnostics rendering/report allowlists so `/sidecar` and `/governance` share the same backend endpoint and safe export behavior.
- Updated README and demo docs with Retrieval Diagnostics capability, limits, security boundaries, and verification commands.
- Code review fixes constrained diagnostics stage error codes and count fields, avoided false success statuses without backend evidence, routed retrieval sub-stage next steps to retrieval validation, and scoped frontend diagnostics report state per surface with stale DOM clearing.

### File List

- README.md
- _bmad-output/implementation-artifacts/8-4-retrieval-diagnostics-安全时间线.md
- _bmad-output/implementation-artifacts/sprint-status.yaml
- apps/web/governance/index.html
- apps/web/sidecar/sidecar.css
- apps/web/sidecar/sidecar.js
- docs/demo/governance-workbench.md
- docs/demo/source-inspector-sidecar.md
- packages/diagnostics/dto.py
- packages/diagnostics/service.py
- packages/retrieval/storage/repositories.py
- tests/integration/api/test_diagnostics_routes.py
- tests/integration/storage/test_retrieval_log_repositories.py
- tests/unit/diagnostics/test_dto.py
- tests/unit/diagnostics/test_service.py
- tests/unit/web/sidecar_behavior_runner.js
- tests/unit/web/test_governance_static_contract.py
- tests/unit/web/test_sidecar_static_contract.py

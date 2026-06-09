---
baseline_commit: 623de7bc1b41a6145bfac8a3d70ae5d0d6a5abb3
---

# Story 8.5: Eval Evidence 与质量回归工作区

Status: done

生成时间：2026-06-09T16:11:45+08:00

## Story

As a 平台工程师,
I want 在前端查看 RAG eval 数据集、运行结果和质量趋势,
so that 项目安全与准确性可以用证据展示而不是口头解释。

## Acceptance Criteria

1. **Eval Evidence 展示受控报告列表与质量摘要**
   - Given eval smoke、dataset smoke 或 CI gate 已产生 synthetic-safe JSON 报告
   - When 用户打开 `/governance` 的 Eval Evidence
   - Then UI 调用授权后端 API 展示 dataset version、case_count、passed_count、failed_count、retrieval_hit_rate、citation_coverage、no_answer_correctness、acl_isolation、prompt_injection、average_latency_ms、decision/status 和 report filename
   - And 页面不得展示完整 query、answer、chunk text、prompt、provider raw response、source_uri、object key、token、secret、本机路径或企业敏感样例全文

2. **失败 case 摘要可定位但不泄露敏感内容**
   - Given 用户选择某个失败 case id
   - When 后端返回 case detail
   - Then UI 展示 case_id、failure_stage、matched document/chunk/citation IDs、retrieval_result_count、context_item_count、citation_count、unsupported_count、forged_reference_count、prompt_risk_count、request_id、trace_id、top_k、latency_ms、generation provider/model/version/token usage safe summary 和建议验证命令
   - And 失败详情仍遵守 synthetic-safe 字段白名单，不显示 dataset 原始 query、expected answer terms、full answer、full corpus content、prompt 或 provider payload

3. **后端 Eval Evidence API 是唯一数据入口**
   - Given 报告文件位于 `tests/eval/reports/` 或配置化报告目录
   - When 前端请求报告列表或详情
   - Then 后端 application service 读取、解析、归一化并白名单输出报告摘要
   - And 前端不得直接 fetch 静态 JSON 报告、不得拼接任意文件路径、不得从 URL/local state 决定 tenant/user/eval 权限

4. **权限、租户和安全失败由后端执行**
   - Given 用户没有 eval/audit 读取权限
   - When 调用 Eval Evidence API
   - Then API 返回统一结构化拒绝，且不泄露报告目录、文件是否存在、其它 tenant/user 信息或 raw exception
   - And UI 必须清理旧报告列表、summary、case detail、copy/export state 和 next-step state

5. **复用现有 no-build governance 前端和 eval runner 产物**
   - Given 当前已有 `/governance` shell、sidecar shared JS/CSS、RAG eval dataset/runner/gate/reporting 模块和 static contract tests
   - When 实现 Eval Evidence
   - Then 默认扩展现有 FastAPI route/service dependency、`apps/web/governance/index.html`、`apps/web/sidecar/sidecar.js`、`apps/web/sidecar/sidecar.css` 和现有测试 runner
   - And 不新增 React、Next.js、Vite、Grafana replacement、Open WebUI fork、browser extension、前端权限判断器、前端报告 parser 或第二套 eval runner

6. **文档、可访问性和验证闭环**
   - Given Eval Evidence 在桌面、平板和移动尺寸使用
   - When 用户通过键盘、屏幕阅读器或触控操作
   - Then 保留 governance tabs 的 ARIA/focus 行为，动态更新使用 `aria-live`，错误使用 alert region，状态含文本/符号而非只靠颜色，长 report/case/request/trace IDs 安全换行或截断并可复制
   - And README、`docs/demo/governance-workbench.md`、`docs/demo/source-inspector-sidecar.md` 和必要 eval docs 按本次能力同步说明入口、能力、限制、安全边界和验证命令
   - And 新增/更新测试覆盖 safe DTO、report parsing/redaction、权限拒绝、stale clearing、report/case rendering、copy/export allowlist、responsive/accessibility contract 和 README 期望

## Tasks / Subtasks

- [x] 设计 Eval Evidence 后端 DTO、异常和安全字段白名单（AC: 1, 2, 3, 4）
  - [x] 新建或扩展 `packages/eval` 领域包，定义 Pydantic v2 frozen DTO，例如 `EvalReportListRequest`、`EvalReportSummary`、`EvalCaseEvidence`、`EvalEvidenceResolveResponse`、`EvalEvidenceReportType`、`EvalEvidenceFailureStage`。
  - [x] DTO 只暴露 synthetic-safe 字段：report filename、generated_at、report_type、dataset_version/name、case counts、pass/fail counts、quality rates、gate decision、failed metric names、failure stages、safe IDs、request_id、trace_id、latency、safe token usage。
  - [x] 明确 forbidden fields 测试：query、answer、content、prompt、source_uri、object_key、sql、vector(s)、embedding(s)、provider_raw_response、raw_exception、token、secret、access_token、api_key、本机绝对路径。
  - [x] 可预期错误使用领域异常和 stable error code，例如 invalid report filename、report not found、report parse failed、permission denied、storage read failed。

- [x] 实现 Eval Evidence application service 和报告读取 adapter（AC: 1, 2, 3, 4）
  - [x] 新建 `packages/eval/service.py` 或等价 application service；输入 `AuthenticatedRequestContext`，不接收前端传入的 `tenant_id`、`user_id`、roles 或 permissions。
  - [x] 报告目录必须配置化，默认只允许 `tests/eval/reports` 或项目内配置目录；路径解析必须防 path traversal，filename 只接受安全 basename。
  - [x] 复用 `tests.eval.rag.reporting`、`tests.eval.rag.gate` 的现有报告 schema/字段语义，不复制 runner 逻辑、不重新运行 eval、不导入真实 LLM/provider。
  - [x] 支持至少三类现有报告：`rag_dataset_smoke`、`rag_quality_runner`、`rag_ci_smoke_gate`；未知 report_type 返回安全 unknown/unsupported 状态或受控错误。
  - [x] 列表接口返回最近 N 个报告的安全摘要，详情接口返回 summary + allowlisted failed case rows；不得返回完整 raw JSON。
  - [x] next_steps 只使用后端白名单命令，如 RAG dataset smoke、quality runner、CI gate、focused pytest；不得根据前端输入拼接任意文件路径。

- [x] 暴露薄 FastAPI route 和 service dependency（AC: 3, 4）
  - [x] 在 `apps/api/routes` 增加 Eval Evidence endpoint，建议形态为 `GET /eval/reports` 和 `GET /eval/reports/{report_filename}`，或一个清晰的 `POST /eval/evidence/resolve`；保持 route 只做 dependency、DTO、service call、`success_response`。
  - [x] 在 `apps/api/service_dependencies.py` 注入 Eval Evidence service；配置来自 settings/environment，不硬编码绝对路径。
  - [x] 权限入口应在 `packages/auth/policies.py` 或新 policy helper 中集中定义；建议接受 `eval:read` 或 `audit:read`，并用测试固定行为。
  - [x] structured error 需要包含 request_id/trace_id/failure_stage/error_code safe details，不包含 report directory、absolute path、raw JSON parse stack 或目标存在性细节。

- [x] 升级 `/governance` 的 Eval Evidence 面板（AC: 1, 2, 3, 4, 5, 6）
  - [x] 更新 `apps/web/governance/index.html` 的 `governance-view-eval-evidence`，加入 report list refresh、report selector、summary region、failed case region、next-step region、copy/export controls。
  - [x] 表单输入只允许 report filename 或后端返回的 selector value；不要提供自由目录、tenant/user、permissions、dataset path 或 local file path 输入。
  - [x] 保留六个 governance tabs 和已有 Document Review、Source Evidence、Retrieval Diagnostics 行为；切换 tab 不自动拉取 eval 报告。
  - [x] 空状态和失败状态聚焦安全边界和 next steps，不写大段功能说明。

- [x] 扩展 shared `sidecar.js` 的 Eval Evidence allowlist、fetch/render 和 stale clearing（AC: 1, 2, 3, 4, 5, 6）
  - [x] 添加 `SAFE_EVAL_REPORT_SUMMARY_FIELDS`、`SAFE_EVAL_CASE_FIELDS`、`SAFE_EVAL_GATE_FIELDS`、`SAFE_EVAL_REPORT_EXPORT_FIELDS` 或等价白名单，并纳入 `GOVERNANCE_SAFE_FIELDS.evalSummary`。
  - [x] 实现 fetch report list/detail helper 和 test exports；复用 `buildHeaders()`、`pickFields()`、safe copy/download、live/alert region 模式。
  - [x] 请求开始前立即清理旧 report list/detail/next_steps/copy/export state；403/404/network/malformed response 必须清理旧授权数据。
  - [x] Copy/export 只导出 allowlisted summary/detail，filename 只使用 sanitized report filename 或 request/trace id；不得导出 raw query、answer、prompt、chunk content、provider payload、source_uri 或 object key。
  - [x] 不读取或写入 `localStorage`、`sessionStorage`、cookie、URL history 或 console log。

- [x] 扩展 CSS 为紧凑、可扫描的 Eval Evidence 工作区（AC: 1, 2, 6）
  - [x] 更新 `apps/web/sidecar/sidecar.css`，新增 eval report list、metric grid、case row、gate decision、next-step 样式。
  - [x] 状态必须有文本和符号，不只依赖颜色；长 report filename、case_id、request_id、trace_id 和 command 使用 `overflow-wrap: anywhere`。
  - [x] 移动端保持单列，按钮文本不溢出，列表/detail 不遮挡后续内容。
  - [x] 不引入 hero、营销卡片、装饰渐变、嵌套卡片或大面积单色主题。

- [x] 后端测试覆盖报告解析、安全边界和权限（AC: 1, 2, 3, 4）
  - [x] 新增 `tests/unit/eval` 或 `tests/unit/eval_evidence` service/DTO tests，覆盖 report type 归一化、quality summary、gate decision、failed case safe rows、invalid filename、malformed report、unknown type、safe next_steps。
  - [x] 新增/扩展 integration API tests，覆盖 authorized list/detail、permission denial、not found redaction、path traversal rejection、forbidden fragments absence 和 response envelope。
  - [x] 使用临时目录/fixture JSON，不真实调用外部 LLM、embedding、browser、PostgreSQL、Redis 或 MinIO。
  - [x] 保留现有 `tests/unit/eval` runner/gate tests，不把 Eval Evidence API 测试变成重新执行 eval 的慢测试。

- [x] 前端静态契约和 JS 行为测试（AC: 1, 2, 3, 4, 5, 6）
  - [x] 扩展 `tests/unit/web/test_governance_static_contract.py`，验证 Eval Evidence controls、summary/detail/next-step/export regions、ARIA live/alert、safe field allowlists、forbidden fragments absence 和 responsive CSS。
  - [x] 扩展 `tests/unit/web/test_sidecar_static_contract.py`，确认 shared JS 的 eval allowlists 不包含 query、answer、content、prompt、source_uri、object_key、SQL、vectors、embeddings、provider payload、token、secret、raw_exception。
  - [x] 扩展 `tests/unit/web/sidecar_behavior_runner.js`，覆盖 report list rendering、detail rendering、permission failure stale clearing、new lookup clears old copy/export、report export allowlist、sanitized filename、next steps clearing、tab switch no auto lookup。
  - [x] 不引入 Playwright、Node build pipeline 或浏览器自动化，除非现有 runner 无法覆盖核心行为且实现说明给出理由。

- [x] 更新 README 和 demo docs（AC: 6）
  - [x] 更新 `docs/demo/governance-workbench.md`，说明 Eval Evidence 已支持授权报告列表、质量摘要、失败 case 安全详情、权限要求、字段白名单、限制和 focused tests。
  - [x] 更新 `docs/demo/source-inspector-sidecar.md`，说明 sidecar 不承载完整 Eval Evidence；治理工作台使用后端 API 浏览 safe eval evidence，不是静态 JSON browser 或 dashboard。
  - [x] 更新 README Build Status、Governance Workbench、Evaluation and Tests、Current Limits 或验证段落；不得宣称 Audit Explorer 或 Review Queue 已完成。
  - [x] Dev Agent Record 填写实现决策、验证结果和文件列表。

- [x] 建议验证命令（AC: 1-6）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/eval tests/eval -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/eval_evidence tests/integration/api/test_eval_evidence_routes.py -q`（如测试路径命名不同，按实际文件替换）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/web/test_governance_static_contract.py tests/unit/web/test_sidecar_static_contract.py -q`
  - [x] `node tests/unit/web/sidecar_behavior_runner.js`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/test_readme_expectations.py -q`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`

### Review Findings

- [x] [Review][Patch] Eval evidence allowlisted strings can leak secret, URL, object-key, or local-path values [packages/eval/service.py:424]
- [x] [Review][Patch] Quality reports with missing `failed_count` are normalized as passed instead of parse-failed [packages/eval/service.py:298]
- [x] [Review][Patch] Non-finite numeric report values can escape domain error handling and return 500 [packages/eval/service.py:450]
- [x] [Review][Patch] Dataset smoke ACL and prompt-injection case counts are rendered as failed security booleans [packages/eval/service.py:251]
- [x] [Review][Patch] Eval Evidence reads are not audited with action/report detail [apps/api/service_dependencies.py:304]
- [x] [Review][Patch] A single malformed report file blocks the entire report list [packages/eval/service.py:73]
- [x] [Review][Patch] Report list recency sorting compares raw timestamp strings instead of parsed instants [packages/eval/service.py:75]
- [x] [Review][Patch] Refreshing report list can leave a stale selected filename [apps/web/sidecar/sidecar.js:1575]
- [x] [Review][Patch] Eval detail responses can render out of order after overlapping requests [apps/web/sidecar/sidecar.js:1279]
- [x] [Review][Patch] Frontend static contract no longer rejects raw `"token"` eval fields [tests/unit/web/test_sidecar_static_contract.py:78]

## Dev Notes

### Current Repository State

- Current HEAD at story creation: `623de7b fix(governance): address retrieval diagnostics review findings`.
- Story 8.1 established the no-build `/governance` shell with six stable entries and ARIA tab behavior.
- Story 8.2 implemented Document Review via backend document lifecycle review APIs and hardened stale state clearing.
- Story 8.3 implemented Source Evidence set review through `POST /sources/resolve`; pasted citation data is untrusted and request IDs from citations must not become current request headers.
- Story 8.4 implemented Retrieval Diagnostics safe timeline through `POST /diagnostics/resolve`; frontend/server both use allowlists and report state is scoped per surface.
- Eval Evidence is currently only an empty placeholder in `/governance`. RAG eval runner, dataset smoke, CI gate, safe JSON reports and README documentation already exist under `tests/eval` and should be reused.
- Existing frontend remains static HTML/CSS/JS served by FastAPI. There is no React/Next/Vite/package manifest and no Node build step.

### Existing Files To Read Before Implementation

- `tests/eval/rag/dto.py`
  - Current state: defines synthetic RAG eval dataset, result, summary and report DTOs with safe fixture ID validation, failure stages, ACL/prompt-injection flags and forbidden marker checks.
  - What this story changes: usually no change; Eval Evidence should map these report shapes into production-facing safe DTOs.
  - Preserve: report browsing must not weaken fixture validation or expose `query`/`content` fields.

- `tests/eval/rag/reporting.py`
  - Current state: builds `rag_dataset_smoke` and `rag_quality_runner` reports and writes JSON into `tests/eval/reports`.
  - What this story changes: service may parse these JSON report files and expose safe summaries.
  - Preserve: do not duplicate the runner, do not execute eval during a UI read, and do not return raw report JSON.

- `tests/eval/rag/gate.py` and `tests/eval/rag/run_ci_smoke.py`
  - Current state: CI gate applies thresholds for retrieval hit rate, citation coverage, no-answer correctness, ACL isolation, prompt injection and failed_count; report stdout/report JSON are already safe summaries.
  - What this story changes: Eval Evidence should display gate decisions and failed metrics from existing report fields.
  - Preserve: stable exit code meanings and gate threshold config path; UI/API reads evidence, it does not decide pass/fail.

- `tests/eval/config/rag_smoke_gate.json`
  - Current state: threshold source for CI gate.
  - What this story changes: no change expected unless docs/tests need to cite thresholds.
  - Preserve: do not let frontend override thresholds.

- `tests/eval/datasets/rag_smoke.json`
  - Current state: synthetic dataset includes raw queries and synthetic corpus content for runner tests.
  - What this story changes: no direct frontend access; service may count/report dataset version from report, not expose raw dataset.
  - Preserve: query/content remain test fixture internals, not governance UI payload.

- `apps/api/routes/governance.py`
  - Current state: static `GET /governance` entrypoint only.
  - What this story changes: usually no route change here; Eval Evidence API belongs in a separate thin API route.
  - Preserve: `GET /governance` remains unauthenticated static asset serving; data API authorization happens on API endpoints.

- `apps/api/service_dependencies.py`
  - Current state: central place for service factories including diagnostics dependencies.
  - What this story changes: add Eval Evidence service dependency if route needs one.
  - Preserve: route code must not parse files directly.

- `packages/auth/policies.py`
  - Current state: central permission helpers exist for diagnostics and other features.
  - What this story changes: add a clear eval evidence read helper if one does not exist.
  - Preserve: LLM/frontend must not decide permissions.

- `apps/web/governance/index.html`
  - Current state: Eval Evidence panel is placeholder text; Document Review, Source Evidence and Retrieval Diagnostics are implemented.
  - What this story changes: add authorized report list/detail controls and regions.
  - Preserve: six tabs, existing panel IDs, backend link behavior, local/test auth helper, no-storage behavior.

- `apps/web/sidecar/sidecar.js`
  - Current state: shared frontend behavior and safe field allowlists for source, document review and diagnostics.
  - What this story changes: add Eval Evidence allowlists, fetch/render helpers, report export/copy and tests exports.
  - Preserve: no local/session storage, no cookies/history/console logs, immediate stale clearing before async fetch.

- `apps/web/sidecar/sidecar.css`
  - Current state: compact operational styling with responsive grids, focus-visible, long ID wrapping and diagnostics timeline styles.
  - What this story changes: add compact eval list/metric/case styles.
  - Preserve: no nested cards, no marketing layout, no text overlap, mobile single-column fallback.

- `tests/unit/web/test_governance_static_contract.py`, `tests/unit/web/test_sidecar_static_contract.py`, `tests/unit/web/sidecar_behavior_runner.js`
  - Current state: cover governance shell, Source Evidence, Document Review, Retrieval Diagnostics, safe allowlists and stale clearing.
  - What this story changes: add Eval Evidence static and behavior tests.
  - Preserve: no browser automation or build step unless justified.

- `README.md`, `docs/demo/governance-workbench.md`, `docs/demo/source-inspector-sidecar.md`, `docs/operations/local-development.md`
  - Current state: document existing eval runners and governance workbench limits; README currently says Eval Evidence remains placeholder.
  - What this story changes: update current capability and limits after implementation.
  - Preserve: do not claim Audit Explorer or Review Queue are complete.

### Previous Story Intelligence

- Story 8.1 review caught governance entry identity, navigation wiring, keyboard behavior, responsive overflow and stale alert issues. Preserve tablist semantics and focus behavior.
- Story 8.2 review caught safe detail failure responses, nested unsafe error summaries, deleted latest-version selection, cursor bounds and stale Document Review UI state. Apply the same stale clearing discipline to eval list/detail/export state.
- Story 8.3 review caught stale evidence copyability during new resolves, direct evidence link parsing, pasted `request_id` being reused as current `X-Request-ID`, and unsafe metadata rendering. Eval report filenames/case IDs are lookup inputs only, not current auth/request headers.
- Story 8.4 review caught nested stage error leakage, false success statuses without backend evidence, overly broad count fields, stale diagnostics report export and stale backend diagnostics DOM. Eval Evidence must use fixed allowlists, not render raw report payloads, and not mark success without backend/report evidence.
- Recent frontend strategy is intentionally no-build: Python static contract tests plus Node `vm` behavior runner. Reuse this pattern.
- Governance workbench is presentation only. Backend AuthContext, RBAC, ACL, eval report parsing, audit and future review APIs remain authoritative.

### Architecture and Security Guardrails

- Module ownership: API route in `apps/api/routes`, service/dependencies in application/service layer, domain DTOs in `packages/eval` or an equivalent package, static UI in `apps/web/governance` and shared assets in `apps/web/sidecar`.
- Auth boundary: all data requests use `AuthenticatedRequestContext`; front-end payload must never include tenant/user/roles/permissions.
- Report path boundary: service accepts safe report filename or paging params only. Resolve paths under configured report directory and reject path traversal, absolute paths, drive letters and separators.
- Redaction boundary: never expose raw query, full answer, corpus content, prompt, SQL, vectors, embeddings, provider payloads, tokens, secrets, source URI/object key, local path, raw exception or arbitrary report JSON.
- Eval boundary: this story browses evidence from already generated reports. It must not run long eval jobs, add Docker-dependent eval, add LLM-as-judge, build dashboards, write long-term trend storage, create review queue items, or implement Audit Explorer.
- Permission boundary: prefer `eval:read` or `audit:read` as explicit policy; document and test the chosen permission. Denied/missing/cross-scope reports must use uniform safe failure.
- Observability: route/service should log structured request_id, trace_id, user_id, tenant_id, action, report filename, latency, status and error_code without logging report content.

### Current Eval Report Shapes To Reuse

- `rag_dataset_smoke` report summary fields:
  - `case_count`, `answerable_count`, `no_answer_count`, `acl_case_count`, `prompt_injection_case_count`, `citation_expected_count`, `dataset_version`, `failure_stages`.
  - Case summaries include safe fixture IDs and expectations but can include dataset-oriented expected IDs. For UI, expose only IDs/counts needed by AC.
- `rag_quality_runner` report fields:
  - summary: `case_count`, `passed_count`, `failed_count`, `retrieval_hit_rate`, `citation_coverage`, `required_citation_count`, `matched_required_citation_count`, `no_answer_correctness`, `no_answer_case_count`, `acl_isolation_passed`, `prompt_injection_passed`, `average_latency_ms`.
  - cases: `case_id`, `request_id`, `trace_id`, `tenant_id`, `user_id`, `top_k`, `latency_ms`, `passed`, `failure_stage`, matched IDs, safe counts and generation summary.
- `rag_ci_smoke_gate` report fields:
  - `commit_sha`, `branch`, dataset summary, config summary, runner_summary, decision metrics, failed metric names, failed case IDs and failure stages.
  - UI should show threshold decision evidence but should not allow threshold edits.

### Latest Technical Information

- No new external framework is required. Current `pyproject.toml` pins FastAPI `>=0.136.3,<0.137`, Pydantic `>=2.13.4,<3`, SQLAlchemy `>=2.0.50,<3`, pytest `>=9.0.0,<10`, ruff `>=0.14.0,<1`.
- FastAPI and Pydantic should be used through existing project patterns; do not add a frontend build chain for this story.
- MDN documents `aria-live` as the correct mechanism for dynamic region updates; use polite updates for successful report loads and alert/assertive behavior for errors.
- MDN Clipboard `writeText()` is Promise-based and available only in secure contexts in browsers; keep existing copy fallback behavior and tests for unavailable clipboard.
- WAI-ARIA APG tabs pattern expects `role="tablist"`, `role="tab"`, `role="tabpanel"`, `aria-selected`, `aria-controls`, keyboard arrow navigation and focus behavior; preserve the existing governance tab implementation.

### References

- `_bmad-output/planning-artifacts/epics.md#Story-8.5-Eval-Evidence-与质量回归工作区`
- `_bmad-output/planning-artifacts/epics.md#Epic-8-企业审阅治理前端与可信证据工作台`
- `_bmad-output/planning-artifacts/architecture.md#Frontend-Architecture`
- `_bmad-output/planning-artifacts/architecture.md#Authentication-&-Security`
- `_bmad-output/planning-artifacts/architecture.md#Observability`
- `_bmad-output/planning-artifacts/architecture.md#Requirements-to-Structure-Mapping`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md#Eval-Reports`
- `project-context.md#17-测试规则`
- `project-context.md#18-可观测性规则`
- `_bmad-output/implementation-artifacts/8-1-审阅治理工作台信息架构与前端边界.md`
- `_bmad-output/implementation-artifacts/8-2-文档生命周期审阅看板.md`
- `_bmad-output/implementation-artifacts/8-3-citation-与-source-evidence-审阅器.md`
- `_bmad-output/implementation-artifacts/8-4-retrieval-diagnostics-安全时间线.md`
- `tests/eval/rag/dto.py`
- `tests/eval/rag/reporting.py`
- `tests/eval/rag/runner.py`
- `tests/eval/rag/gate.py`
- `tests/eval/rag/run_ci_smoke.py`
- `tests/eval/config/rag_smoke_gate.json`
- `tests/eval/datasets/rag_smoke.json`
- `apps/api/routes/governance.py`
- `apps/api/service_dependencies.py`
- `packages/auth/policies.py`
- `apps/web/governance/index.html`
- `apps/web/sidecar/sidecar.js`
- `apps/web/sidecar/sidecar.css`
- `tests/unit/web/test_governance_static_contract.py`
- `tests/unit/web/test_sidecar_static_contract.py`
- `tests/unit/web/sidecar_behavior_runner.js`
- `docs/demo/governance-workbench.md`
- `docs/demo/source-inspector-sidecar.md`
- `docs/operations/local-development.md`
- `README.md`
- MDN `aria-live`: https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Reference/Attributes/aria-live
- MDN Clipboard `writeText()`: https://developer.mozilla.org/en-US/docs/Web/API/Clipboard/writeText
- WAI-ARIA Tabs Pattern: https://www.w3.org/WAI/ARIA/apg/patterns/tabs/

## Validation Checklist

Validation Result: PASS（2026-06-09T16:11:45+08:00）

- [x] Story 明确 8.5 只实现 Eval Evidence 与质量回归工作区，不扩展 Audit Explorer 或 Review Queue。
- [x] Acceptance Criteria 覆盖授权报告列表、质量指标、失败 case 安全详情、后端唯一数据入口、权限拒绝、stale clearing、可访问性、docs/tests。
- [x] Tasks 指向现有 eval runner/reporting/gate、FastAPI route/service dependency、governance HTML、sidecar JS/CSS 和测试文件，避免重建前端栈或 eval runner。
- [x] Dev Notes 记录 8.1-8.4 learnings、recent git patterns、eval report shapes、unsafe field 防线和 no new framework 约束。
- [x] 明确禁止 raw query、answer、chunk content、prompt、SQL、vectors、embeddings、provider payload、token、secret、source_uri、object key、本机路径和 raw exception。
- [x] README 同步要求已写入 AC/Tasks；本次 create-story 只创建 story，不实现功能，因此不更新 README。

## Change Log

- 2026-06-09: Implemented Eval Evidence backend API, governance UI, safe report parsing, tests, docs, and validation.
- 2026-06-09: Created comprehensive Story 8.5 developer context for Eval Evidence and quality regression workspace.

## Dev Agent Record

### Agent Model Used

Codex GPT-5

### Debug Log References

- `.venv\Scripts\python.exe -m pytest tests\unit\eval tests\eval -q` -> 80 passed
- `.venv\Scripts\python.exe -m pytest tests\unit\eval_evidence tests\integration\api\test_eval_evidence_routes.py -q` -> 8 passed
- `.venv\Scripts\python.exe -m pytest tests\unit\web\test_governance_static_contract.py tests\unit\web\test_sidecar_static_contract.py -q` -> 54 passed
- `node tests\unit\web\sidecar_behavior_runner.js` -> passed
- `.venv\Scripts\python.exe -m pytest tests\unit\test_readme_expectations.py -q` -> 2 passed
- `.venv\Scripts\python.exe -m ruff check .` -> passed
- `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed
- `.venv\Scripts\python.exe -m pytest -q` -> 1011 passed
- Review fix validation 2026-06-09T17:58:33+08:00:
  - `.venv\Scripts\python.exe -m pytest tests\unit\eval_evidence tests\integration\api\test_eval_evidence_routes.py -q` -> 15 passed
  - `node tests\unit\web\sidecar_behavior_runner.js` -> passed
  - `.venv\Scripts\python.exe -m pytest tests\unit\web\test_sidecar_static_contract.py tests\unit\web\test_governance_static_contract.py -q` -> 54 passed
  - `.venv\Scripts\python.exe -m ruff check .` -> passed
  - `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed
  - `.venv\Scripts\python.exe -m pytest tests\unit\eval tests\eval -q` -> 80 passed
  - `.venv\Scripts\python.exe -m pytest tests\unit\test_readme_expectations.py -q` -> 2 passed
  - `.venv\Scripts\python.exe -m pytest -q` -> 1018 passed

### Completion Notes List

- Added `packages.eval` DTOs, domain errors, and `EvalEvidenceService` to read configured report directories, enforce safe report filenames, normalize supported eval report types, and expose only synthetic-safe summaries, failed case evidence, gate metrics, and whitelisted next steps.
- Added `GET /eval/reports` and `GET /eval/reports/{report_filename}` as thin FastAPI routes with `AuthenticatedRequestContext`, `eval:read`/`audit:read` policy, settings-driven report directory, and uniform structured errors.
- Upgraded `/governance` Eval Evidence from placeholder to an authorized report workspace with report list/detail, safe failed case rows, gate metric rows, copy/download allowlists, stale clearing, ARIA live regions, and responsive no-build CSS.
- Extended Python, static frontend, and Node behavior tests for safe DTO parsing, permission denial, path traversal, malformed reports, stale clearing, safe rendering, safe export, and no auto-fetch on tab switch.
- Updated README and demo docs to describe the new Eval Evidence API, security boundary, limitations, and verification commands.

### File List

- README.md
- _bmad-output/implementation-artifacts/8-5-eval-evidence-与质量回归工作区.md
- _bmad-output/implementation-artifacts/sprint-status.yaml
- apps/api/main.py
- apps/api/routes/eval_evidence.py
- apps/api/service_dependencies.py
- apps/web/governance/index.html
- apps/web/sidecar/sidecar.css
- apps/web/sidecar/sidecar.js
- docs/demo/governance-workbench.md
- docs/demo/source-inspector-sidecar.md
- packages/auth/policies.py
- packages/common/config.py
- packages/eval/__init__.py
- packages/eval/dto.py
- packages/eval/exceptions.py
- packages/eval/service.py
- tests/integration/api/test_eval_evidence_routes.py
- tests/unit/eval_evidence/test_eval_evidence_service.py
- tests/unit/web/sidecar_behavior_runner.js
- tests/unit/web/test_governance_static_contract.py
- tests/unit/web/test_sidecar_static_contract.py

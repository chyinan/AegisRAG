---
baseline_commit: 3f8ea45
---

# Story 9.1: Open WebUI Citation Evidence Link Contract

Status: review

生成时间：2026-06-09T19:59:06+08:00

## Story

As a Open WebUI 用户,
I want 每条回答 citation 都能跳转到本项目的安全 evidence 页面,
so that 聊天窗口里的来源可以被业务方直接验证。

## Acceptance Criteria

1. **OpenAI-compatible response 暴露安全 evidence link contract**
   - Given Open WebUI 通过 `POST /v1/chat/completions` 非流式接收回答
   - When 后端返回 citation metadata
   - Then 每条 citation 必须包含后端生成的 evidence link 参数：`document_id`、`version_id`、`chunk_id`、`page_start`、`page_end`、`request_id`、`trace_id`、`source_display_name`
   - And link contract 应以结构化字段返回，例如 `evidence_url`、`evidence_query` 或 `evidence_links`，不得只嵌入自然语言 answer
   - And link 不包含 bearer token、service token、JWT、raw `source_uri`、本地路径、object key、完整 query、answer、prompt、chunk text、ACL、roles、permissions 或 provider raw response

2. **Streaming final metadata 和 citation events 保持同等证据能力**
   - Given Open WebUI 通过 `stream=true` 接收 OpenAI-compatible chunks
   - When final metadata chunk 到达
   - Then final chunk 必须包含与非流式响应一致的 evidence link contract
   - And 如果 citation event 或 final citations 中已经包含 `source_ref`，新 contract 不得破坏现有字段语义
   - And token chunks 不得提前输出未完成的 link 或可复制 source evidence；只有 final/citation metadata 到达后才可作为 evidence 输入

3. **Evidence link 打开后必须重新授权**
   - Given 用户点击或复制 evidence link
   - When 打开 `/sidecar` 或 `/governance` Source Evidence
   - Then 页面必须把 link 参数解析为 source resolve 输入，并调用 `POST /sources/resolve`
   - And 后端通过当前 `AuthenticatedRequestContext` 重新校验 tenant、RBAC、ACL、soft delete、version visibility、document/version/chunk identity 和 page identity
   - And Open WebUI、sidecar、governance 前端都不是 source visibility 的决策点，不得使用 link 参数直接展示 excerpt

4. **Open WebUI 无法渲染自定义 link UI 时提供兼容 fallback**
   - Given Open WebUI 只显示标准 markdown 或扩展 metadata
   - When 用户无法点击自定义 citation UI
   - Then answer 或 metadata 中仍必须提供可复制的 safe identifiers 和同源 companion URL
   - And Source Evidence 审阅器必须接受 Open WebUI metadata、单条 citation JSON、citation 数组、sidecar/source evidence URL、或手动 identifiers
   - And fallback 不得要求用户复制 bearer token、service token、tenant_id、roles、permissions、raw source locator 或 chunk 内容

5. **复用现有 Open WebUI adapter、Source Evidence 和 no-build 前端**
   - Given 当前已有 `OpenWebUIChatAdapter`、`Citation` DTO、`POST /sources/resolve`、`/sidecar`、`/governance` Source Evidence 和 no-build JS/CSS 测试
   - When 实现 evidence link contract
   - Then 优先扩展 `packages/rag/openwebui.py`、`packages/rag/dto.py` 或新的 framework-free helper；扩展 `apps/web/sidecar/sidecar.js` 的 parser/allowlist；必要时只小幅更新 `apps/web/governance/index.html`
   - And 不新增 React、Next.js、Vite、浏览器插件、Open WebUI fork、Open WebUI patch、前端权限判断器、前端 citation 生成器、前端 source resolver 或第二套 source evidence UI
   - And 不改变 `/query`、`/chat`、`/query/stream`、`/chat/stream` 已有 response/SSE contract，除非以 backward-compatible optional 字段方式复用 evidence metadata

6. **审计、日志和安全字段白名单**
   - Given adapter、source evidence parser 或 source resolve 成功、拒绝或失败
   - When 记录 audit/log 或生成 copy/download payload
   - Then 只记录 request_id、trace_id、tenant_id、user_id、citation_count、evidence_link_count、source_display_name safe summary、latency、status、error_code 等安全摘要
   - And 不记录或导出完整 query、answer、prompt、chunk text、authorized excerpt 集合、raw source_uri、object key、local path、SQL、vectors、embeddings、provider payload、tool output、token、secret 或 raw exception

7. **文档和 README 同步**
   - Given evidence link contract 完成
   - When 用户查看 README、Open WebUI 本地开发文档、Source Inspector 文档或 Governance Workbench 文档
   - Then 文档说明 Open WebUI citation metadata/evidence link 的字段、点击/复制 fallback、`/sources/resolve` 二次授权、权限要求、字段白名单和限制
   - And README 的 Build Status、Open WebUI/sidecar/governance 能力、Current Limits 或验证命令必须按本次能力同步；如果实际变更不影响 README，Dev Agent 最终回复必须说明原因

8. **测试覆盖**
   - Given 单元和集成测试运行
   - When 验证 9.1
   - Then 覆盖非流式 OpenAI-compatible response 的 evidence link 字段、stream final chunk 的 evidence link 字段、redaction、link 参数完整性、source evidence link parser、dedup、fallback copy allowlist、safe failure stale clearing
   - And 覆盖 API route contract、source resolve 二次授权仍然执行、architecture boundary、README/docs expectations
   - And 测试使用 fake service、TestClient、Node `vm` runner、静态契约测试或 repository mock；不得真实调用 OpenAI/Qwen/DeepSeek/Ollama/vLLM、真实 Open WebUI、浏览器、Docker、PostgreSQL、Redis、MinIO 或网络

## Tasks / Subtasks

- [x] 设计 evidence link DTO/helper，保持 framework-free（AC: 1, 2, 4, 6）
  - [x] 新增或扩展 `packages/rag/openwebui.py` 中的 evidence link 构造逻辑；建议提供 `CitationEvidenceLink` / `CitationEvidenceReference` 等 frozen Pydantic DTO，或在 `OpenAIChatCompletionResponse.metadata` 中加入稳定 `evidence_links`。
  - [x] 生成字段必须来自后端 `ChatResponse.citations`、`context.request_id`、`context.trace_id` 和 configured same-origin path；不得从 answer 文本解析 citation。
  - [x] link query 只允许 `document_id`、`version_id`、`chunk_id`、`page_start`、`page_end`、`request_id`、`citation_ref`；`trace_id` 可展示在 metadata 中，但不要作为 source resolve lookup 的必要输入。
  - [x] evidence URL 建议指向 `/governance#source-evidence?...` 或 `/sidecar#source?...` 的同源 companion path；不要生成绝对外部 URL，除非使用配置化 public base URL 且经过安全校验。
  - [x] 如果需要新增配置，例如 `PUBLIC_APP_BASE_URL` 或 `EVIDENCE_LINK_BASE_PATH`，必须进入 `packages.common.config`，默认安全同源相对路径，不硬编码本机绝对地址。
  - [x] 添加安全 URL/query 编码，拒绝或删除 token、authorization、source_uri、object_key、prompt、query、answer、content、acl、roles、permissions 等字段。

- [x] 扩展 Open WebUI adapter 非流式 response（AC: 1, 4, 6）
  - [x] 更新 `OpenAIChatCompletionResponse`，在顶层或 `metadata` 中暴露 `evidence_links`，每条 link 与 citation 一一对应或通过 `citation_ref` 关联。
  - [x] 保留现有 `citations`、`no_answer`、`unsupported_claims`、safe `metadata`、`usage` 和 OpenAI-compatible `choices` 字段。
  - [x] 非流式响应不得把 evidence markdown 插入 answer 作为唯一来源；如果增加 markdown fallback，必须是可复制 safe identifiers/link，且不改变 answer 的事实内容。
  - [x] adapter audit metadata 可增加 `evidence_link_count`，不得记录 URL query 全量或 citation 原始对象。

- [x] 扩展 OpenAI-compatible streaming final chunk（AC: 2, 4, 6）
  - [x] 更新 `_final_extension_fields()` 或等价 formatter，让 final chunk 包含 evidence link contract。
  - [x] token chunks 保持只输出 delta，不输出半成品 source link。
  - [x] DomainError/exception stream error chunk 继续 redaction，且 `[DONE]` 终止行为不回归。
  - [x] 现有 `/query/stream`、`/chat/stream` 命名 SSE 不应被 OpenAI-compatible formatter 改动；若复用 helper，保持 backward-compatible。

- [x] 扩展 Source Evidence link parser 和 copy allowlist（AC: 3, 4, 5, 6）
  - [x] 更新 `apps/web/sidecar/sidecar.js` 的 `parseSourceEvidenceInput()` / link parsing helpers，接受新 `evidence_links`、`evidence_url`、`evidence_query` 形态。
  - [x] 解析 URL 时只读取同源 path/hash/query 中的 source resolve allowlist 字段；拒绝或忽略 `token`、`authorization`、`source_uri`、`object_key`、`query`、`answer`、`prompt`、`chunk_text`、`tenant_id`、`roles`、`permissions`。
  - [x] 保留 dedup 上限 20，保留 pasted `request_id` 不变成当前 `X-Request-ID` 的行为。
  - [x] Copy safe summary 只输出 source resolve allowlist 字段、授权状态、source_display_name、page range、retrieval_method、score、request_id、trace_id；不得输出 excerpt 集合或 raw metadata。
  - [x] 链接解析、resolve、denial、malformed response、新请求开始和 tab switch 都必须清理 stale evidence/result/copy state。

- [x] 轻量更新 governance/sidecar HTML 和 CSS（AC: 4, 5）
  - [x] 如需要，在 `apps/web/governance/index.html` 的 Source Evidence help text 中说明可粘贴 Open WebUI evidence links/metadata。
  - [x] 如需要，在 `apps/web/sidecar/index.html` 的 Source Inspector 文案或字段中补充 evidence link fallback；不要新增完整管理台或复杂 preview。
  - [x] 保持 WCAG 2.2 AA 基础：`aria-live`、alert region、键盘 tabs、长 ID `overflow-wrap:anywhere`、非 hover-only 操作。
  - [x] 不引入营销 hero、装饰卡片、嵌套卡片、大面积单色主题或会遮挡内容的布局。

- [x] 后端测试：Open WebUI adapter 和 stream contract（AC: 1, 2, 6, 8）
  - [x] 扩展 `tests/unit/rag/test_openwebui_adapter.py`，验证非流式 `evidence_links` 字段包含 document/version/chunk/page/request/trace/source_display_name，且不含 source_uri/object_key/token/prompt/chunk content。
  - [x] 扩展 streaming final chunk 测试，验证 final payload 含 evidence link contract，token payload 不含 link，error chunk 仍 redacted。
  - [x] 增加 citation 数组为空/no-answer 时 evidence links 为空且不伪造来源的测试。
  - [x] 扩展 `tests/integration/api/test_openwebui_routes.py`，用 service override 验证 route 输出 contract，不真实调用 RAG/LLM/Open WebUI。
  - [x] 如新增 config，增加 config validation/redaction tests。

- [x] 前端静态契约和 Node VM 行为测试（AC: 3, 4, 5, 6, 8）
  - [x] 扩展 `tests/unit/web/test_governance_static_contract.py` 和 `tests/unit/web/test_sidecar_static_contract.py`，验证 Source Evidence 接受 evidence link 文案/controls/allowlist，且 JS 不含 forbidden field names。
  - [x] 扩展 `tests/unit/web/sidecar_behavior_runner.js`，覆盖 Open WebUI `metadata.evidence_links`、top-level `evidence_links`、direct URL、hash URL、malformed link、unsafe query keys、dedup 和 stale clearing。
  - [x] 保持现有 no-build runner；不要引入 Playwright、浏览器自动化、Node build pipeline 或 Open WebUI 容器依赖。

- [x] Source resolve 与安全回归测试（AC: 3, 6, 8）
  - [x] 保留并必要扩展 `tests/integration/api/test_sources_routes.py`，确认 evidence link 参数最终仍走 `POST /sources/resolve`，权限拒绝不调用 service。
  - [x] 保留 `tests/unit/rag/test_source_resolver.py` 的跨 tenant、ACL、soft delete、inactive chunk、invisible version、page mismatch 拒绝矩阵。
  - [x] 确认 denied/not found/soft deleted/ACL mismatch 仍为统一安全形态，不泄露资源存在性。

- [x] 文档和 README 更新（AC: 7）
  - [x] 更新 `docs/operations/local-development.md#Open-WebUI-and-Source-Inspector-Local-Checks`，加入 evidence link response/stream 示例和 curl 验证。
  - [x] 更新 `docs/demo/source-inspector-sidecar.md`，说明 `/sidecar` 可解析 evidence link，但仍通过 `/sources/resolve` 二次授权。
  - [x] 更新 `docs/demo/governance-workbench.md`，说明 Source Evidence 可粘贴 Open WebUI evidence links/metadata 并解析多 citation。
  - [x] 更新 `docs/api/source-metadata.md` 或新增 `docs/api/openwebui-evidence-links.md`，记录字段白名单、禁止字段、fallback、out of scope。
  - [x] 更新 README Build Status / Open WebUI / Current Limits / 验证命令；不要宣称 Open WebUI tool events、function bridge、fork 定制或长期插件策略已完成。

- [x] 建议验证命令（AC: 1-8）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/rag/test_openwebui_adapter.py tests/unit/rag/test_streaming.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/api/test_openwebui_routes.py tests/integration/api/test_sources_routes.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/web/test_governance_static_contract.py tests/unit/web/test_sidecar_static_contract.py -q`
  - [x] `node tests/unit/web/sidecar_behavior_runner.js`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/rag/test_source_resolver.py tests/unit/rag/test_source_metadata.py tests/unit/rag/test_citation_extractor.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/test_architecture_boundaries.py tests/unit/test_readme_expectations.py -q`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`
  - [x] 如成本可接受，运行 `.venv\Scripts\python.exe -m pytest -q`

## Dev Notes

### Current Repository State

- Current HEAD at story creation: `3f8ea45 feat(review): add governed review queue`.
- `git status --short` was clean at story creation.
- Sprint status shows Epic 8 stories 8.6 and 8.7 still in `review`; Epic 9 is first backlog epic. Before implementing 9.1, dev agent should check whether review fixes for 8.6/8.7 have landed or if any feedback affects shared governance/sidecar assets.
- Existing Open WebUI compatibility is in `packages/rag/openwebui.py` and `apps/api/routes/openwebui.py`.
- Existing non-streaming OpenAI-compatible response includes top-level `citations`, `request_id`, `trace_id`, `session_id`, `no_answer`, `unsupported_claims`, and safe `metadata`.
- Existing streaming OpenAI-compatible formatter emits `data: {...}` chunks and puts citations only in the final chunk extension fields.
- Existing public citation DTO is `packages.rag.dto.Citation`; it already exposes `document_id`, `version_id`, `chunk_id`, `source_display_name`, `source_ref`, `source_type`, page range, `title_path`, `retrieval_method`, and `score`, and it sanitizes unsafe source display names.
- Existing source resolver is `packages/rag/source_resolver.py`; it rechecks tenant, ACL, document/version/chunk identity, version status `retrieval_ready`, active chunk status, page match, soft delete, and audit.
- Existing Source Evidence parser in `apps/web/sidecar/sidecar.js` already accepts citation JSON, arrays, Open WebUI-style metadata containing citations, and source/sidecar links. It keeps only source resolve fields and deduplicates up to 20 references.
- Existing docs already mention Source Evidence can parse Open WebUI metadata and links, but the backend OpenWebUI adapter does not yet generate a stable evidence link contract.
- Existing frontend is static HTML/CSS/JS. There is no React, Next.js, Vite, browser build, or Open WebUI fork in the repo.

### Existing Files To Read Before Implementation

- `packages/rag/openwebui.py`
  - Current state: framework-free OpenAI-compatible adapter DTOs, response mapping, stream formatter, safe metadata redaction, adapter audit.
  - What this story changes: add evidence link contract to non-streaming and final streaming output.
  - Preserve: latest-user-message extraction, system/developer/tool message safety, metadata_filter authorization field rejection, `[DONE]` termination, safe error chunk redaction.

- `packages/rag/dto.py`
  - Current state: owns `Citation`, `ChatResponse`, `QueryResponse`, stream-independent RAG DTOs and citation safety.
  - What this story changes: maybe add optional evidence link DTO if it belongs with public citation metadata; otherwise keep helper local to OpenWebUI adapter.
  - Preserve: public citations never expose raw `source_uri`; no-answer must not invent citations.

- `packages/rag/streaming.py`
  - Current state: named RAG SSE DTOs for `/query/stream` and `/chat/stream`.
  - What this story changes: likely none unless a shared citation evidence helper is reused.
  - Preserve: named SSE event contract and redaction behavior.

- `packages/rag/source_resolver.py`
  - Current state: authoritative source resolve service and command/response DTO.
  - What this story changes: usually no direct change; link parameters should map to `SourceResolveCommand`.
  - Preserve: safe denial shape and source resolve audit.

- `apps/api/routes/openwebui.py`
  - Current state: thin route for `/v1/models` and `/v1/chat/completions`.
  - What this story changes: usually no route logic change; response schema may evolve through adapter DTO.
  - Preserve: route does not call RAG internals, storage, LLM providers, or vector stores directly.

- `apps/api/routes/sources.py`
  - Current state: thin route for `POST /sources/resolve`.
  - What this story changes: no direct change expected unless request schema needs a backward-compatible alias.
  - Preserve: `RagQueryContextDep` permission gate and service dependency override pattern.

- `apps/web/sidecar/sidecar.js`
  - Current state: shared no-build JS for sidecar and governance; contains source evidence parsing, backend fetches, stale clearing, safe field allowlists and Node test exports.
  - What this story changes: parse new evidence link metadata shapes and possibly output link-oriented copy summary.
  - Preserve: no storage/cookies/history/console logs, no unsafe field names in allowlists, request token race protection.

- `apps/web/governance/index.html` and `apps/web/sidecar/index.html`
  - Current state: Source Evidence and Source Inspector forms exist with allowed citation fields and accessible regions.
  - What this story changes: minimal text/control updates only if needed.
  - Preserve: governance six tabs, sidecar source-first entry, ARIA roles, no raw auth/tenant override inputs.

- `tests/unit/rag/test_openwebui_adapter.py`
  - Current state: verifies adapter response/stream, safe metadata, citations and audit.
  - What this story changes: add evidence link assertions.
  - Preserve: no external provider calls.

- `tests/integration/api/test_openwebui_routes.py` and `tests/integration/api/test_sources_routes.py`
  - Current state: route-level service override tests for OpenWebUI and Source Resolve.
  - What this story changes: verify new response fields and resolve path remains backend-authoritative.
  - Preserve: auth rejection before adapter/service call.

- `tests/unit/web/test_governance_static_contract.py`, `tests/unit/web/test_sidecar_static_contract.py`, `tests/unit/web/sidecar_behavior_runner.js`
  - Current state: no-build static/behavior tests covering Source Evidence, Diagnostics, Eval Evidence, Audit Explorer and Review Queue.
  - What this story changes: add evidence link parser/allowlist/stale clearing tests.
  - Preserve: no browser automation or Node build step.

- `README.md`, `docs/demo/source-inspector-sidecar.md`, `docs/demo/governance-workbench.md`, `docs/operations/local-development.md`, `docs/api/source-metadata.md`
  - Current state: document Open WebUI, Source Resolve, Source Evidence and safe metadata; README says Epic 9 remains backlog.
  - What this story changes: update current capability after implementation.
  - Preserve: do not claim tool event streaming or function/tool bridge is complete.

### Previous Story Intelligence

- Story 4.7 established OpenAI-compatible inbound adapter and `/sources/resolve`. It explicitly fixed source URI leakage, adapter audit, streaming error chunks, content-part normalization, and retrieval-ready-only source visibility. Do not regress any of those review findings.
- Story 7.1 established safe source display metadata. Public surfaces must use `source_display_name`, not raw locators.
- Story 7.2 hardened Open WebUI auth. Open WebUI provider key maps to backend AuthContext; UI fields and model names cannot expand tenant/user/permissions.
- Story 7.5/7.6 established sidecar and diagnostics as same-origin static companions, not authorization boundaries.
- Story 8.3 implemented Source Evidence reviewer. It already parses citation JSON/OpenWebUI metadata/links and resolves each item through `/sources/resolve`; 9.1 should strengthen backend-generated metadata rather than recreate the reviewer.
- Story 8.4-8.7 repeatedly fixed stale UI state, unsafe copy/export payloads, malformed response handling, and no-build frontend tests. Apply the same stale clearing discipline to evidence link parsing and fallback copy.
- Recent commits show the current pattern: `packages/*` application/domain DTOs, thin FastAPI routes, SQLAlchemy only in storage, static governance UI, Python static contract tests, Node `vm` behavior runner, README/docs updates, full safety allowlists.

### Architecture and Security Guardrails

- Module ownership: Open WebUI response contract belongs in `packages/rag/openwebui.py` or a small `packages/rag` helper; source visibility remains in `packages/rag/source_resolver.py`; frontend parsing belongs in shared `apps/web/sidecar/sidecar.js`.
- Evidence link is not authorization. It is only a pointer to identifiers that the backend must revalidate.
- Do not add Open WebUI fork or patch in 9.1. Story 9.4 owns long-term customization strategy.
- Do not implement Agent tool events in 9.1. Story 9.2 owns `tool_call` / `tool_result` streaming bridge.
- Do not implement Open WebUI function/tool bridge in 9.1. Story 9.3 owns Tool Registry mapping.
- Do not implement a full Open WebUI plugin or custom React admin console.
- Do not place permission logic in prompt, answer text, markdown, Open WebUI metadata, URL query, or frontend parser.
- Do not let client-provided `trace_id`, `tenant_id`, `user_id`, roles, permissions, ACL, source display name, retrieval method, or score become authorization evidence.
- Do not put tokens or auth-bearing URLs into evidence links. Users authenticate to `/sidecar` or `/governance` through normal backend auth, not through link query strings.

### Latest Technical Information

- Official Open WebUI docs for OpenAI-compatible servers still identify `GET /v1/models` as recommended for model discovery and `POST /v1/chat/completions` as the required chat endpoint, with streaming and standard OpenAI-style parameters. Source: https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-openai-compatible
- Open WebUI docs note that it passes standard OpenAI parameters such as `temperature`, `top_p`, `max_tokens` / `max_completion_tokens`, `stop`, `seed`, and `logit_bias`. This story should ignore or safely pass through only parameters already supported by `OpenAIChatCompletionRequest`; do not add broad pass-through for unsupported fields.
- Current repository already implements the required Open WebUI baseline endpoints and tests. 9.1 should add safe evidence metadata on top of the existing adapter instead of replacing the adapter.
- No new external library is required. Existing stack already includes FastAPI, Pydantic v2, pytest, and Node-only `vm` behavior tests.

### References

- `_bmad-output/planning-artifacts/epics.md#Story-9.1-Open-WebUI-Citation-Evidence-Link-Contract`
- `_bmad-output/planning-artifacts/epics.md#Epic-9-Open-WebUI-企业级集成增强与轻量魔改路线`
- `_bmad-output/planning-artifacts/architecture.md#Frontend-Architecture`
- `_bmad-output/planning-artifacts/architecture.md#API-&-Communication-Patterns`
- `project-context.md#6-RAG-实现规则`
- `project-context.md#13-Prompt-Injection-防护`
- `_bmad-output/implementation-artifacts/4-7-open-webui-chat-adapter-source-detail-与轻量前端契约.md`
- `_bmad-output/implementation-artifacts/7-1-source-metadata-安全展示策略.md`
- `_bmad-output/implementation-artifacts/7-2-open-webui-认证接入硬化.md`
- `_bmad-output/implementation-artifacts/7-5-轻量-sidecar-source-inspector-体验设计.md`
- `_bmad-output/implementation-artifacts/8-3-citation-与-source-evidence-审阅器.md`
- `_bmad-output/implementation-artifacts/8-7-人工审阅队列与-eval-回流.md`
- `packages/rag/openwebui.py`
- `packages/rag/dto.py`
- `packages/rag/streaming.py`
- `packages/rag/source_resolver.py`
- `apps/api/routes/openwebui.py`
- `apps/api/routes/sources.py`
- `apps/web/sidecar/sidecar.js`
- `apps/web/governance/index.html`
- `tests/unit/rag/test_openwebui_adapter.py`
- `tests/integration/api/test_openwebui_routes.py`
- `tests/integration/api/test_sources_routes.py`
- `tests/unit/web/test_governance_static_contract.py`
- `tests/unit/web/test_sidecar_static_contract.py`
- `tests/unit/web/sidecar_behavior_runner.js`
- Open WebUI OpenAI-compatible server docs: https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-openai-compatible

## Validation Checklist

Validation Result: PASS（2026-06-09T19:59:06+08:00）

- [x] Story 明确 9.1 只实现 citation evidence link contract，不实现 tool events、function bridge、Open WebUI fork、plugin 或完整自定义前端。
- [x] Acceptance Criteria 覆盖非流式、streaming final metadata、link fallback、source resolve 二次授权、no-build 前端、审计/日志、docs/README 和测试。
- [x] Tasks 指向现有 `packages/rag/openwebui.py`、`Citation` DTO、Source Evidence parser、sidecar/governance static assets 和既有测试体系，避免重建 UI 或绕过 source resolver。
- [x] Dev Notes 记录当前实现状态、8.6/8.7 仍为 review 的上下文、相关文件、前序 story learnings 和 Open WebUI 最新官方兼容端点信息。
- [x] 明确禁止 token、source_uri、object key、本地路径、完整 query/answer/prompt/chunk、ACL、roles、permissions 和 provider payload 进入 link、metadata、日志、copy/export。
- [x] README 同步要求已写入 AC/Tasks；本次 create-story 只创建 story，不实现功能，因此不更新 README。

## Change Log

- 2026-06-09: Created comprehensive Story 9.1 developer context for Open WebUI citation evidence link contract.
- 2026-06-09: Implemented Open WebUI evidence link contract, Source Evidence parser support, documentation, README updates, and verification coverage.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- 2026-06-09T20:06:54+08:00: Started dev-story workflow; sprint status moved from ready-for-dev to in-progress. Existing `baseline_commit` preserved.
- 2026-06-09T20:21:46+08:00: Completed implementation and validation; story and sprint status moved to review.

### Completion Notes List

- Added `CitationEvidenceLink` and top-level `evidence_links` to OpenAI-compatible non-streaming responses and final streaming chunks while preserving existing citations, no-answer, metadata, usage, and OpenAI choice fields.
- Evidence URLs use same-origin `/governance?...#source-evidence` pointers and source resolve allowlist query fields only; `trace_id` and `source_display_name` remain display/correlation metadata, not authorization inputs.
- Extended OpenWebUI adapter audit summaries with `evidence_link_count` without logging full URL query payloads or citation objects.
- Extended Source Evidence parsing for top-level and metadata `evidence_links`, `evidence_url`, `evidence_query`, direct/hash URLs, dedup, unsafe field ignoring, and stale result/copy clearing.
- Updated governance/sidecar static text, API/docs/README guidance, and verification commands for evidence link fallback and `/sources/resolve` reauthorization.
- Verified with focused tests, ruff, mypy, and full pytest: 1053 tests passed.

### File List

- README.md
- _bmad-output/implementation-artifacts/9-1-open-webui-citation-evidence-link-contract.md
- _bmad-output/implementation-artifacts/sprint-status.yaml
- apps/web/governance/index.html
- apps/web/sidecar/index.html
- apps/web/sidecar/sidecar.js
- docs/api/source-metadata.md
- docs/demo/governance-workbench.md
- docs/demo/source-inspector-sidecar.md
- docs/operations/local-development.md
- packages/rag/openwebui.py
- tests/integration/api/test_openwebui_routes.py
- tests/unit/rag/test_openwebui_adapter.py
- tests/unit/web/sidecar_behavior_runner.js
- tests/unit/web/test_governance_static_contract.py
- tests/unit/web/test_sidecar_static_contract.py

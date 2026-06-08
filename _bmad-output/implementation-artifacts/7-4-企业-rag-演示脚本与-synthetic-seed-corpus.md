---
baseline_commit: 7edc69f
---

# Story 7.4: 企业 RAG 演示脚本与 Synthetic Seed Corpus

Status: done

生成时间：2026-06-08T21:22:56+08:00

## Story

As a 产品负责人,
I want 一条可复现的 synthetic 企业 RAG walkthrough,
so that 可以展示上传、索引、Open WebUI 问答、citation、source resolve、no-answer 和权限隔离。

## Acceptance Criteria

1. **Synthetic seed corpus 可生成且脱敏**
   - Given 本地演示数据目录为空或未初始化
   - When 执行 seed/demo 初始化命令
   - Then 创建 synthetic-only 文档、tenant、user、role、permission、ACL 和 walkthrough manifest
   - And 文档内容覆盖制度、FAQ、产品手册、技术文档四类企业 RAG 场景
   - And 不包含真实企业文档、API key、access token、JWT、Open WebUI provider key、MinIO 凭据、个人信息、本机绝对路径或机密全文
   - And seed corpus 的 `source_uri` 使用受控 synthetic URI 或相对展示名，不使用 `file://`、Windows/Unix 绝对路径、bucket/object key 或 token-bearing URL

2. **初始化命令复用现有 ingestion 与权限边界**
   - Given 开发者在本地栈或测试环境运行 seed/demo 命令
   - When 命令创建或上传 demo 文档
   - Then 必须通过现有 application/service/API 边界创建文档、版本、ACL、job 和 chunks，不得用裸 SQL 伪造 retrieval-ready 状态
   - And `/upload` 仍异步返回 `document_id`、`version_id`、`job_id`、`status`，不得同步等待大批量 embedding
   - And worker 或测试 fixture 路径必须显式推进 uploaded/parsing/parsed/chunking/chunked/embedding/indexing/retrieval_ready 状态
   - And seed 命令可重复运行：重复执行不创建冲突脏数据，可选择 reset synthetic namespace 或 idempotent upsert

3. **Open WebUI walkthrough 能展示可信 RAG 闭环**
   - Given demo 文档已上传并处理到 `retrieval_ready`
   - When 使用 Open WebUI 或 OpenAI-compatible `/v1/chat/completions` 询问 manifest 中的演示问题
   - Then 回答包含 answer、citation、request_id、trace_id、session_id 和安全 metadata
   - And citation 只包含 `source_display_name`、`source_type`、document/version/chunk/page/title metadata、retrieval_method 和 score
   - And 不返回 raw `source_uri`、本地路径、MinIO object key、prompt、chunk full content、SQL、vectors、embeddings、provider raw response 或 secrets
   - And Open WebUI request body、model name、chat title、metadata_filter 或 UI user 信息不得覆盖后端 AuthContext、tenant、RBAC、ACL 或 source visibility

4. **Source resolve 点击链路二次授权**
   - Given walkthrough 中某个回答返回 citation
   - When 使用该 citation 调用 `POST /sources/resolve`
   - Then 后端重新校验 AuthContext、tenant、RBAC、ACL、soft delete、document/version/chunk identity、version visibility 和 chunk active status
   - And 只返回授权 excerpt、安全摘要、source display metadata、request_id、trace_id、retrieval_method 和 score
   - And 未授权、缺失、删除、版本不可见或 ACL 拒绝返回同一类 safe denial shape，不泄露资源是否存在

5. **no-answer、ACL 隔离和 prompt injection 场景可验证**
   - Given manifest 定义 answerable、no-answer、ACL isolation 和 prompt injection 场景
   - When walkthrough runner 执行这些场景
   - Then 授权用户能命中 expected citations
   - And 未授权用户不能召回或引用 private/restricted chunk
   - And 上下文不足时返回明确 no-answer，不编造 citation
   - And 文档中的恶意指令只作为 untrusted context，不会覆盖系统规则、泄露密钥或触发危险工具

6. **输出安全演示报告**
   - Given walkthrough runner 执行完成
   - When 写入报告
   - Then 报告只包含 synthetic-safe IDs、case status、request_id、trace_id、latency、retrieval/result/citation counts、failure stage 和安全摘要
   - And 不写入完整 query、完整 chunk、prompt、provider raw response、SQL、vectors、embeddings、tokens、Authorization header、JWT、service token、数据库 URL、MinIO 凭据或本机路径
   - And 失败报告必须给出可执行的下一步验证命令，例如 upload/job/retrieval/chat/source resolve/eval smoke 相关命令

7. **测试覆盖 corpus、runner、权限和文档契约**
   - Given 单元测试和集成 mock 测试运行
   - When 验证 demo seed corpus 与 walkthrough runner
   - Then 覆盖 manifest schema、脱敏检查、idempotency、source URI 安全、ACL 隔离、no-answer、prompt injection、source resolve request shape、OpenAI-compatible streaming/non-stream metadata
   - And 测试默认使用 fake provider、fake/in-memory repository、TestClient 或 mock，不调用真实 LLM、embedding API、Open WebUI 容器、PostgreSQL、Redis、MinIO、外部网络或真实 Docker daemon

8. **文档、README 和本地操作说明同步**
   - Given Story 7.4 实现完成
   - When 更新文档
   - Then README 项目进度从 Story 7.3 更新到 Story 7.4 完成，说明 synthetic enterprise walkthrough、能力边界、验证命令和剩余限制
   - And `docs/operations/local-development.md` 或 `docs/demo/enterprise-rag-walkthrough.md` 记录 seed、上传/处理、Open WebUI 提问、source resolve、no-answer、ACL 隔离、prompt injection 和报告检查步骤
   - And 文档明确 Open WebUI 是入口不是权限治理边界，demo corpus 是 synthetic-only，不代表真实生产数据导入策略

## Tasks / Subtasks

- [x] 定义 demo seed corpus 与 manifest contract（AC: 1, 5, 6, 7）
  - [x] 新增 synthetic corpus 目录，优先使用 `docs/demo/enterprise-rag/` 或 `tests/fixtures/demo/enterprise-rag/`；若选择其他路径，必须在 story 实现说明中解释并保持 README/docs 一致。
  - [x] 新增 manifest，描述 tenants、users、roles、permissions、documents、ACL、expected demo queries、expected citations、no-answer、ACL isolation 和 prompt injection cases。
  - [x] 文档内容至少包含制度、FAQ、产品手册、技术文档四类，每类至少一个可回答 case；另外至少一个 no-answer、一个 ACL isolation、一个 prompt injection case。
  - [x] manifest 中所有 ID 使用 synthetic-safe 字符集，推荐 `tenant-demo-alpha`、`demo-user-employee`、`demo-user-admin`、`doc-demo-*`、`chunk-demo-*`。
  - [x] 禁止在 fixtures、manifest、reports 和 docs 中保存真实 secret、真实员工/客户信息、绝对路径、bucket/object key、token-bearing URL 或企业机密全文。

- [x] 实现 seed/demo 初始化命令（AC: 1, 2, 6, 7）
  - [x] 优先在 `packages/data/demo_seed.py` 或相邻 data/application-friendly 模块实现 manifest loader、sanitization validator 和 seed orchestration；不要把业务逻辑放进 FastAPI route。
  - [x] 如需要 CLI，使用可测试的 argparse 模块，例如 `python -m packages.data.demo_seed` 或等价入口；CLI 只编排 service/API 调用，不直接拼 SQL 或绕过 ACL。
  - [x] 支持 dry-run/validate 模式，输出将创建的 synthetic tenants/users/documents/cases 摘要。
  - [x] 支持 idempotent 行为或显式 `--reset-demo-namespace`，只影响 synthetic demo tenant/document namespace，不删除用户真实数据。
  - [x] 对 seed 结果写入安全报告，默认放在 `tests/eval/reports/` 或文档化的 demo report 目录，并保持 `.gitignore`/`.gitkeep` 策略一致。

- [x] 复用 ingestion、embedding 和 retrieval-ready 状态链路（AC: 2, 3, 5）
  - [x] 上传路径必须复用 `DocumentUploadService` / `/upload` 契约：`source_type`、`acl`、metadata、文件流、AuthContext 和 audit 都必须存在。
  - [x] worker 测试路径可复用 `apps/worker/jobs/ingestion_jobs.py`、`apps/worker/jobs/embedding_jobs.py` 的 payload contract；不要用一段脚本直接把所有状态改成 `retrieval_ready`。
  - [x] 如果真实 compose walkthrough 需要等待 job 完成，提供 bounded polling、timeout 和 safe error summary；不得 `while true` 无限等待。
  - [x] 允许测试使用 fake provider / fake vector store / in-memory repository 构造 retrieval-ready fixture，但必须单独标明它是测试 fixture，不是生产 seed 路径。

- [x] 实现 walkthrough runner（AC: 3, 4, 5, 6）
  - [x] Runner 应支持 API base URL、Bearer/service token、tenant/user profile、case selector、timeout 和 report path 配置。
  - [x] 对 answerable case 调用 `/v1/chat/completions` 或 `/chat`，校验 answer、request_id、trace_id、session_id、citation_count 和 expected citation identifiers。
  - [x] 对 source drilldown case 使用返回的 citation 调用 `POST /sources/resolve`，校验 response 安全字段和授权 excerpt；不要从 answer 文本或前端输入补造 citation。
  - [x] 对 ACL isolation case 使用低权限用户/token 运行同一或相邻 query，断言 private/restricted chunk 不进入 citations、source resolve 或报告。
  - [x] 对 no-answer case 断言 `no_answer=true` 或等价无答案策略，且没有 forged citation。
  - [x] 对 prompt injection case 断言恶意文档指令不改变系统规则、不泄露 secret-like text、不触发工具或越权字段。

- [x] 扩展测试覆盖（AC: 1-7）
  - [x] 新增 `tests/unit/data/test_demo_seed.py` 或等价测试：manifest schema、脱敏检查、source URI 安全、idempotency/reset scope、safe report redaction。
  - [x] 新增 `tests/integration/api/test_demo_walkthrough.py` 或等价 mock integration：使用 TestClient/stub adapter 验证 `/v1/chat/completions` non-stream/stream metadata、citation shape 和 source resolve request flow。
  - [x] 复用 `tests/eval/rag` 的 DTO/runner 约束；如新增 demo-specific dataset loader，必须保持 synthetic-only、forbidden metadata filter 和 secret marker 检查。
  - [x] 覆盖失败报告不会输出 Authorization、JWT、OpenWebUI provider key、`source_uri`、绝对路径、object key、完整 chunk 或 prompt。
  - [x] 测试不得依赖真实 Open WebUI 容器、真实 Docker daemon、真实 LLM/embedding provider、PostgreSQL、Redis、MinIO 或网络。

- [x] 更新 docs、README 和示例命令（AC: 3, 4, 5, 6, 8）
  - [x] README Build Status / Current Capabilities / Open WebUI / Eval 或 Demo sections 更新到 Story 7.4 完成状态。
  - [x] `docs/operations/local-development.md` 补充从 `.env`、Open WebUI profile、seed command、job 等待、Open WebUI 提问、source resolve、报告检查到 cleanup/reset 的完整路径。
  - [x] 如新增 `docs/demo/enterprise-rag-walkthrough.md`，必须包含 case matrix、预期结果、命令、报告字段、安全边界和已知限制。
  - [x] 文档明确当前仍不实现 Open WebUI function/tool bridge、真实 provider adapter、生产 SSO、完整自定义管理台、Graph RAG、多 Agent 或真实企业数据导入。

- [x] 验证（AC: 1-8）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/data/test_demo_seed.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/api/test_demo_walkthrough.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/eval tests/unit/test_readme_expectations.py -q`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`
  - [x] 如实现真实 compose smoke 文档命令，至少用 fake local env 执行 dry-run/validate，不要求 CI 启动 Open WebUI 容器

### Review Findings

- [x] [Review][Patch] Missing executable seed initialization command [packages/data/demo_seed.py:551]
- [x] [Review][Patch] Seed orchestration does not reject context tenant mismatch [packages/data/demo_seed.py:403]
- [x] [Review][Patch] Manifest validation does not reject users referencing undefined roles [packages/data/demo_seed.py:312]
- [x] [Review][Patch] Manifest expected citation IDs cannot match the real upload/chunk pipeline [packages/data/demo_seed.py:572]
- [x] [Review][Patch] Source resolve runner accepts incomplete or wrong resolve responses [packages/data/demo_walkthrough.py:241]
- [x] [Review][Patch] ACL isolation checks only forbidden citations, not leaked answer text or source resolve denial [packages/data/demo_walkthrough.py:255]
- [x] [Review][Patch] Prompt-injection forbidden terms are defined but not enforced [packages/data/demo_walkthrough.py:261]
- [x] [Review][Patch] Unknown walkthrough case selectors silently produce zero-case passing reports [packages/data/demo_walkthrough.py:145]
- [x] [Review][Patch] Report sanitizers miss unsafe key variants and local-path/password-like values [packages/data/demo_walkthrough.py:408]
- [x] [Review][Patch] Malformed Open WebUI service-token config no longer fails closed [apps/api/dependencies.py:77]
- [x] [Review][Patch] Open WebUI preflight validates provider hash by substring instead of structured JSON [docker/compose.yaml:152]
- [x] [Review][Patch] Postgres compose volume mount change can hide existing local data [docker/compose.yaml:49]
- [x] [Review][Patch] Historical vector migration now depends on runtime VECTOR_INDEX_DIM [migrations/versions/20260527_0005_vector_records.py:57]

## Dev Notes

### Current Repository State

- Git baseline for this story context: `7edc69f feat(openwebui): add optional compose profile`.
- Worktree is not clean before story creation. Dirty files include README、7.2/7.3 story artifacts、sprint status、epics、OpenWebUI auth/code/test files、docker compose/docs、migration/test files。Implementation agents must inspect `git status` before editing and must not revert or stage unrelated user changes.
- Sprint status auto-selected `7-4-企业-rag-演示脚本与-synthetic-seed-corpus` as the first backlog story after Story 7.3.
- Epic 1-6 and Story 7.1-7.3 are complete. Story 7.4 should not rebuild source metadata, OpenWebUI auth, OpenWebUI compose, retrieval, RAG generation, citation extraction, source resolve, Agent runtime or eval runner from scratch.
- Existing eval fixtures already live under `tests/eval/datasets/retrieval_smoke.json` and `tests/eval/datasets/rag_smoke.json`; reuse their synthetic-only safety rules where possible.

### Existing Files To Read Before Implementation

- `packages/data/service.py`
  - Current state: `DocumentUploadService.upload()` enforces document upload permissions, validates ACL/source metadata/upload type, writes object storage, creates document/version/ingestion job records, enqueues ingestion, records audit and returns `uploaded`.
  - What this story changes: demo seed should reuse this service or `/upload` contract for upload-style flows. It may add a demo seed orchestrator, but must not bypass upload permission, ACL, audit or job creation.
  - Preserve: async upload semantics, max bytes validation, supported source types, source URI validation, audit metadata redaction and route-thin API behavior.

- `apps/worker/jobs/ingestion_jobs.py` and `apps/worker/jobs/embedding_jobs.py`
  - Current state: workers validate `QueuePayload`, rebuild `AuthenticatedRequestContext`, then call `IngestionParseService` or `EmbeddingJobService`. Embedding worker currently supports fake provider and fake/pgvector vector store from settings.
  - What this story changes: walkthrough docs or runner may call worker-compatible commands/payloads or wait for worker completion. Do not alter worker contract unless needed and tested.
  - Preserve: JSON-serializable queue payloads, no untrusted pickle object assumptions, bounded failure handling.

- `apps/api/routes/upload.py`
  - Current state: route parses multipart form fields and delegates to `DocumentUploadService`. It does not implement business logic beyond schema/form parsing.
  - What this story changes: no route business logic should be added for demo. Demo clients can call `/upload`.
  - Preserve: route-thin pattern and structured upload errors.

- `apps/api/routes/openwebui.py` and `packages/rag/openwebui.py`
  - Current state: `GET /v1/models` and `POST /v1/chat/completions` require `RagQueryContextDep`; non-stream and stream delegate to `OpenWebUIChatAdapter`. Adapter rejects auth-scope metadata filters and returns OpenAI-compatible response/chunks with safe citation extension fields.
  - What this story changes: walkthrough runner should call this path or stub it in tests. Do not parse OpenWebUI credentials or permissions in the adapter.
  - Preserve: OpenAI-compatible data-only SSE with terminal `[DONE]`; do not mix it with named backend SSE events.

- `packages/rag/source_resolver.py`
  - Current state: `SourceResolveService.resolve()` rechecks tenant, document/version/chunk identity, soft delete, `retrieval_ready` version visibility, active chunk status and ACL before returning a safe excerpt and source display metadata.
  - What this story changes: walkthrough source drilldown should reuse this endpoint/service and verify the safe response shape.
  - Preserve: safe denial shape, no resource existence disclosure, no raw `source_uri`.

- `tests/eval/rag/dto.py`, `tests/eval/rag/loader.py`, `tests/eval/rag/runner.py`
  - Current state: RAG eval dataset enforces synthetic source URI prefix, safe fixture IDs, forbidden secret markers, no auth-widening metadata filters, ACL isolation and prompt injection contracts.
  - What this story changes: demo corpus can reuse these DTOs or mirror their validators. If demo has a separate manifest, keep the same safety invariants.
  - Preserve: fake provider usage and safe reporting defaults.

- `docker/compose.yaml`, `docker/README.md`, `docs/operations/local-development.md`, `.env.example`
  - Current state: Story 7.3 added optional `open-webui` profile, provider base URL, provider API key, service token hash preflight and docs.
  - What this story changes: docs should add seed/walkthrough steps that build on the profile. Do not make Open WebUI mandatory for default backend tests or compose config.
  - Preserve: Open WebUI remains optional and not an authorization boundary.

### What Must Be Preserved

- Backend AuthContext, RBAC, ACL filters, source resolve authorization and audit remain authoritative. Open WebUI cannot supply or override tenant/user/permissions.
- Public source metadata remains governed by Story 7.1: no public raw `source_uri`, local paths, object keys, full URLs with tokens, prompt text, chunk full content, vectors, embeddings or SQL.
- Story 7.2 service token hardening remains intact. Demo service token permissions should default to `document:read` and `retrieval:query`; upload/seed admin actions require a separate local/test admin context and must be documented.
- Story 7.3 Open WebUI profile remains optional. Default compose/test/lint/mypy must not require Open WebUI.
- Tests must use fake providers and local fixtures by default. Real provider/API calls are out of scope for this story.
- Demo seed data must be synthetic and small enough for local development. It must not create a second RAG pipeline, a second auth model, or a second source sanitizer.

### Suggested Implementation Shape

Use this as guidance, not as mandatory file names if the existing implementation makes a better local pattern obvious:

```text
docs/demo/enterprise-rag/
  manifest.json
  corpus/
    hr-leave-policy.md
    product-vpn-manual.md
    faq-indexing-status.md
    technical-rag-operations.md
  enterprise-rag-walkthrough.md

packages/data/demo_seed.py
packages/data/demo_walkthrough.py
tests/unit/data/test_demo_seed.py
tests/integration/api/test_demo_walkthrough.py
```

Preferred CLI shape:

```powershell
.venv\Scripts\python.exe -m packages.data.demo_seed validate --manifest docs/demo/enterprise-rag/manifest.json
.venv\Scripts\python.exe -m packages.data.demo_seed materialize --manifest docs/demo/enterprise-rag/manifest.json --output .demo/enterprise-rag
.venv\Scripts\python.exe -m packages.data.demo_walkthrough run --manifest docs/demo/enterprise-rag/manifest.json --api-base-url http://127.0.0.1:8000 --report-dir tests/eval/reports
```

If the implementation uses HTTP upload, use `httpx` with explicit timeout and safe error mapping. If it uses local services, pass `AuthenticatedRequestContext` explicitly and keep route/controller code untouched.

### Previous Story Intelligence

- Story 7.1 removed unsafe source locators across RAG citations, SSE, OpenWebUI, `/sources/resolve`, `/retrieve` and `rag_search`. Demo reports and docs must not reintroduce raw locator fields for convenience.
- Story 7.2 made OpenWebUI auth fail closed through JWT bearer or hash-configured service tokens. Demo setup must use a minimum-permission service token for chat and a separate explicitly local/test admin context for seeding/upload, if upload permissions are needed.
- Story 7.3 added the optional Open WebUI compose profile and a preflight check matching provider key to backend hash. Story 7.4 should reference that setup rather than adding another compose service or provider config.
- Epic 5 eval runners already prove retrieval/citation/no-answer/ACL/prompt-injection behavior with synthetic fixtures. Story 7.4 should make the same qualities visible in a walkthrough, not duplicate the whole eval framework.

### Git Intelligence

- Recent commits:
  - `7edc69f feat(openwebui): add optional compose profile`
  - `74a464d feat(openwebui): harden service token auth`
  - `3f79c15 fix(rag): address safe source metadata review findings`
  - `df30257 feat(rag): add safe source metadata display`
  - `aad38b5 fix(agent): address final answer validation review findings`
- Recent work repeatedly tightened redaction, fail-closed auth, source safety, compose secret handling and review-finding regressions. Follow the same pattern: add explicit leak tests and avoid convenience shortcuts.

### Latest Technical Information

- Open WebUI official docs continue to describe OpenAI-compatible provider setup through base URL and API key configuration. This project should keep Open WebUI pointed at backend `/v1` and treat its provider key as a backend Bearer/service token, not as user identity or authorization policy.
- Docker Compose profiles remain the correct mechanism for optional services that are not started by default. `depends_on` health/completion conditions are the documented way to express startup order; do not use sleep loops for the Open WebUI profile.
- FastAPI supports `UploadFile` plus `File`/`Form` for multipart uploads and requires `python-multipart`, which this repository already depends on. Demo upload clients should match the existing `/upload` multipart contract instead of adding JSON upload shortcuts.
- Sources checked 2026-06-08:
  - Open WebUI docs: https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-openai-compatible/
  - Open WebUI env configuration: https://docs.openwebui.com/getting-started/env-configuration/
  - Docker Compose profiles: https://docs.docker.com/compose/how-tos/profiles/
  - Docker Compose startup order: https://docs.docker.com/compose/how-tos/startup-order/
  - FastAPI file uploads: https://fastapi.tiangolo.com/tutorial/request-files/
  - FastAPI form data: https://fastapi.tiangolo.com/tutorial/request-forms-and-files/

### References

- `_bmad-output/planning-artifacts/epics.md#Story-7.4-企业-RAG-演示脚本与-Synthetic-Seed-Corpus`
- `_bmad-output/planning-artifacts/epics.md#Epic-7-Open-WebUI-展示闭环与生产接入硬化`
- `_bmad-output/planning-artifacts/architecture.md#Frontend-Architecture`
- `_bmad-output/planning-artifacts/architecture.md#Infrastructure-&-Deployment`
- `_bmad-output/planning-artifacts/architecture.md#Authentication-&-Security`
- `_bmad-output/planning-artifacts/architecture.md#API-&-Communication-Patterns`
- `project-context.md#6-RAG-实现规则`
- `project-context.md#13-Prompt-Injection-防护`
- `project-context.md#16-权限规则`
- `project-context.md#18-可观测性规则`
- `project-context.md#21-完成定义`
- `_bmad-output/implementation-artifacts/7-1-source-metadata-安全展示策略.md`
- `_bmad-output/implementation-artifacts/7-2-open-webui-认证接入硬化.md`
- `_bmad-output/implementation-artifacts/7-3-open-webui-docker-compose-profile.md`
- `packages/data/service.py`
- `apps/api/routes/upload.py`
- `apps/api/routes/openwebui.py`
- `packages/rag/openwebui.py`
- `packages/rag/source_resolver.py`
- `tests/eval/rag/dto.py`
- `tests/eval/rag/loader.py`
- `tests/eval/rag/runner.py`
- `docker/compose.yaml`
- `docker/README.md`
- `docs/operations/local-development.md`
- `.env.example`
- `README.md`

## Validation Checklist

Validation Result: PASS（2026-06-08T21:22:56+08:00）

- [x] Story 明确了 synthetic-only demo corpus、manifest、seed command、walkthrough runner、报告和安全边界。
- [x] Acceptance Criteria 覆盖上传/索引、OpenWebUI 问答、citation、source resolve、no-answer、ACL 隔离、prompt injection、测试和文档。
- [x] Tasks 指向当前已存在的 UPDATE 文件和合理新增位置，避免重建 auth、RAG、source metadata、OpenWebUI profile 或 eval runner。
- [x] Dev Notes 记录了当前代码状态、必须保留的行为、前序 story lessons、recent git patterns 和最新 OpenWebUI/Docker/FastAPI context。
- [x] 明确测试使用 fake/stub/local fixture，不调用真实 LLM、embedding、Open WebUI 容器、PostgreSQL、Redis、MinIO、Docker daemon、网络或外部 provider。
- [x] 明确 README 和 operations/demo docs 在实现阶段必须同步；本 create-story 仅创建 story 文件并更新 sprint status。

## Change Log

- 2026-06-08: Created comprehensive Story 7.4 developer context for synthetic enterprise RAG seed corpus and walkthrough.
- 2026-06-08: Implemented synthetic enterprise RAG manifest/corpus, seed validation/orchestration, walkthrough runner, safety tests, README/docs updates, and verification.
- 2026-06-08: Addressed code review findings for executable seed uploads, deterministic demo citations, runner safety assertions, auth fail-closed behavior, compose preflight validation, Postgres volume stability, and migration determinism.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- `.venv\Scripts\python.exe -m packages.data.demo_seed validate --manifest docs/demo/enterprise-rag/manifest.json`
- `.venv\Scripts\python.exe -m packages.data.demo_seed materialize --manifest docs/demo/enterprise-rag/manifest.json --output .demo/enterprise-rag`
- `.venv\Scripts\python.exe -m pytest tests/unit/data/test_demo_seed.py -q`
- `.venv\Scripts\python.exe -m pytest tests/integration/api/test_demo_walkthrough.py -q`
- `.venv\Scripts\python.exe -m pytest tests/eval tests/unit/test_readme_expectations.py -q`
- `.venv\Scripts\python.exe -m pytest -q`
- `.venv\Scripts\python.exe -m ruff check .`
- `.venv\Scripts\python.exe -m mypy apps packages tests`

### Completion Notes List

- Added `docs/demo/enterprise-rag/` synthetic-only corpus and manifest covering policy, FAQ, product manual, technical operations, source resolve, no-answer, ACL isolation, and prompt-injection cases.
- Added `packages.data.demo_seed` with manifest validation, safety checks, safe report writing, local validate/materialize CLI, optional governance upsert port, idempotent namespace store port, and upload orchestration through `DocumentUploadService.upload()` commands.
- Added `packages.data.demo_walkthrough` runner for OpenAI-compatible chat, citation validation, source resolve drilldown, no-answer/ACL/prompt-injection checks, bearer token profiles, case selection, timeout, report directory/path, and safe report serialization.
- Updated README, local development docs, and a dedicated walkthrough guide with seed commands, Open WebUI/source resolve flow, report fields, safety boundaries, and current limitations.
- Verified with focused story tests, eval/readme tests, full default pytest regression, ruff, mypy, and fake local validate/materialize commands.
- Code review patch verification passed: `67 passed` focused review/story tests, `895 passed` full pytest, `ruff check .`, `mypy apps packages tests`, and fake local validate/materialize commands.

### File List

- `.gitignore`
- `README.md`
- `_bmad-output/implementation-artifacts/7-4-企业-rag-演示脚本与-synthetic-seed-corpus.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `docs/demo/enterprise-rag/manifest.json`
- `docs/demo/enterprise-rag/corpus/hr-leave-policy.md`
- `docs/demo/enterprise-rag/corpus/faq-indexing-status.md`
- `docs/demo/enterprise-rag/corpus/product-vpn-manual.md`
- `docs/demo/enterprise-rag/corpus/technical-rag-operations.md`
- `docs/demo/enterprise-rag-walkthrough.md`
- `docs/operations/local-development.md`
- `packages/data/demo_seed.py`
- `packages/data/demo_walkthrough.py`
- `tests/integration/api/test_demo_walkthrough.py`
- `tests/unit/data/test_demo_seed.py`
- `tests/unit/test_readme_expectations.py`

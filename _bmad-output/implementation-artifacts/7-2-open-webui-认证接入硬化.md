---
baseline_commit: 3f79c15
---

# Story 7.2: Open WebUI 认证接入硬化

Status: review

生成时间：2026-06-08T19:20:53+08:00

## Story

As a 平台工程师,
I want Open WebUI 通过生产化 Bearer token 或 service token 映射到统一 AuthContext,
so that Open WebUI 只是入口，不成为权限治理边界。

## Acceptance Criteria

1. **OpenAI-compatible endpoints 统一强制认证**
   - Given Open WebUI 或 OpenAI-compatible client 调用 `GET /v1/models` 或 `POST /v1/chat/completions`
   - When 请求携带生产配置的 Bearer token
   - Then API 必须通过统一认证 adapter 生成同一个 `AuthContext` DTO
   - And `tenant_id`、`user_id`、roles、department、permissions 必须进入现有 RBAC、ACL、audit、request logging 和 retrieval filter
   - And route 继续保持薄层，只依赖 `RagQueryContextDep` / adapter，不直接解析 token、拼权限或访问 storage/provider

2. **JWT bearer token 生产路径明确且可验证**
   - Given 请求使用 JWT bearer token
   - When dependency 解码 token
   - Then 必须验证签名、`exp`、可选 `iss`、可选 `aud`，并拒绝缺少 `user_id/sub` 或 `tenant_id` 的 token
   - And 支持的 claim shape 与 `parse_jwt_claims()` 保持一致：`sub` 或 `user_id`、`tenant_id`、roles、department、permissions，`scope` 仅在 `permissions` 缺失时作为 fallback
   - And token 中 `sub` 与 `user_id` 同时存在但不一致时返回结构化认证错误

3. **Open WebUI service token 映射为受限 AuthContext**
   - Given Open WebUI 只能配置 provider API key 或固定 service token
   - When `Authorization: Bearer <service-token>` 命中生产配置的 service token
   - Then 后端通过配置化 token hash 或等价安全配置映射到受限 `AuthContext`
   - And service token 的默认权限不得超过 `document:read`、`retrieval:query`，除非配置显式声明并有测试覆盖
   - And service token 不得作为 prompt、metadata_filter、query 参数或前端权限判断逻辑处理
   - And 原始 token 不得出现在错误详情、日志、audit metadata、OpenAI-compatible streaming error chunk 或测试快照中

4. **缺失、无效或权限不足时 fail closed**
   - Given 请求缺少 token、token 无效、service token 未配置、JWT secret 未配置、token 过期或缺少 `document:read` / `retrieval:query`
   - When 调用 `/v1/models` 或 `/v1/chat/completions`
   - Then 返回结构化 error envelope 或 OpenAI-compatible stream error chunk，并使用合适 HTTP 状态
   - And 不调用 `OpenWebUIChatAdapter`、`ChatApplicationService`、retrieval、LLMProvider 或任何工具 handler
   - And 响应不得暴露 token 内容、tenant 存在性、用户存在性、文档存在性、内部异常、JWT 原始 claims 或服务端密钥配置细节

5. **dev header smoke path 只在本地/测试显式开启**
   - Given 本地开发仍需要 header auth smoke test
   - When `ENABLE_DEV_AUTH_HEADERS` 未开启，或 `APP_ENV` 不是 `local`、`dev`、`development`、`test`、`testing`
   - Then `X-User-ID`、`X-Tenant-ID`、`X-Roles`、`X-Department`、`X-Permissions` 不被信任
   - And OpenWebUI 文档必须明确区分本地 smoke headers、生产 JWT bearer、Open WebUI service token 和测试 fixture override
   - And 所有 business endpoint 继续使用同一个 `AuthenticatedRequestContext` 注入路径

6. **审计与结构化日志覆盖 Open WebUI 认证边界**
   - Given Open WebUI 请求成功、认证失败、权限拒绝或流式错误
   - When request logging 和 OpenWebUI adapter audit 记录事件
   - Then 日志/审计包含 `request_id`、`trace_id`、`tenant_id`、`user_id`、`path`、`status_code`、`latency_ms`、`error_code`、role/permission count、auth method 摘要
   - And 不记录 Authorization header、service token、JWT、完整 claims、query 原文、prompt、chunk content、provider raw response 或 source locator
   - And auth 失败时 tenant/user 可为空，但仍记录安全 error_code 便于排查

7. **Open WebUI adapter 不扩大权限面**
   - Given client body 包含 `model`、`messages`、`metadata_filter`、`tools`、`tool_choice`、`system`、`developer` 或其他 OpenAI-compatible 字段
   - When `OpenAIChatCompletionRequest` 转换为 `QueryCommand`
   - Then body 字段不得覆盖 `tenant_id`、`user_id`、roles、department、permissions、ACL 或 source visibility
   - And `metadata_filter` 继续拒绝授权 scope 字段，只允许收窄非权限 metadata
   - And adapter 不从 Open WebUI 用户名、模型名、会话名或消息内容推断权限

8. **测试、文档和配置同步**
   - Given Story 7.2 实现完成
   - When 运行验证
   - Then 单元测试覆盖 JWT parser、service token parser、dev header gate、redaction、permission failure 和 route 不调用 adapter 的行为
   - And integration tests 覆盖 `/v1/models`、`/v1/chat/completions` non-stream、stream、missing token、invalid token、permission denied、service token success
   - And `.env.example`、README 和 `docs/operations/local-development.md` 记录所需环境变量、Open WebUI provider 配置、curl smoke 命令、限制和安全边界
   - And README 项目进度必须从 Story 7.1 更新到 Story 7.2 完成状态，若实现阶段发现 README 无需更新，最终回复必须说明原因

## Tasks / Subtasks

- [x] 明确认证配置和 adapter 边界（AC: 1, 2, 3, 5, 7）
  - [x] 复用 `packages.auth.parsers.JwtAuthSettings`、`decode_jwt_token()`、`parse_jwt_claims()`，不要在 route 内重新解析 JWT。
  - [x] 新增或扩展 `packages/auth` 中的 service token parser/adapter，例如 `OpenWebUIServiceTokenSettings` 和 `parse_openwebui_service_token()`。
  - [x] service token 配置必须来自环境变量或配置文件；优先存储 token hash 或带 key id 的安全配置，不把明文 token 写入 README、测试快照或日志。
  - [x] 输出仍为统一 `AuthContext`，并与 JWT/dev header/test fixture 路径共享后续 policy builder。

- [x] 硬化 API dependency 和 OpenWebUI route 入口（AC: 1, 4, 5, 7）
  - [x] 在 `apps/api/dependencies.py` 中集中处理 bearer JWT 与 OpenWebUI service token 识别，避免 `apps/api/routes/openwebui.py` 解析 token。
  - [x] 保持 `apps/api/routes/openwebui.py` 只接收 `RagQueryContextDep`、adapter 和 request body。
  - [x] 确认 `GET /v1/models` 与 `POST /v1/chat/completions` 都要求 `RagQueryContextDep`，缺失认证时不调用 adapter。
  - [x] 确认 `has_rag_query_permission()` 或等价 policy 在 OpenWebUI endpoints 前置执行，权限不足返回 `RAG_QUERY_FORBIDDEN`。
  - [x] 保留 `OpenAIChatCompletionRequest.metadata_filter` 对 tenant/user/acl/roles/permissions 等字段的拒绝逻辑。

- [x] 增加 service token 与 JWT 测试（AC: 2, 3, 4, 5, 8）
  - [x] 更新 `tests/unit/auth/test_parsers.py`：覆盖 service token hash match、unknown token、missing config、redaction-safe error details、scope fallback 和 conflicting subject。
  - [x] 更新 `tests/integration/api/test_context_dependencies.py`：覆盖 verified JWT、service token success、dev headers disabled in production、partial dev headers required error。
  - [x] 确保测试使用本地 deterministic fake token/hash，不依赖真实外部身份服务。

- [x] 增加 OpenWebUI route 安全回归测试（AC: 1, 4, 6, 7, 8）
  - [x] 更新 `tests/integration/api/test_openwebui_routes.py`：覆盖 `/v1/models` service token success、missing token 401、invalid token 401、permission denied 403、non-stream success、stream success。
  - [x] 对缺失/无效/权限不足断言 adapter `list_models()`、`chat_completion()`、`stream_chat_completion()` 未被调用。
  - [x] 覆盖 request body 中 `metadata_filter` 带 `tenant_id`、`user_id`、`acl`、`roles` 或 `permissions` 时返回 validation error，而不是扩大权限。
  - [x] 覆盖 OpenAI-compatible stream error chunk 不包含 bearer token、JWT claims、service token、source URI/path 或 prompt 原文。

- [x] 审计、日志和错误脱敏（AC: 4, 6, 8）
  - [x] 确认 `apps/api/error_handlers.py` 的 auth errors 不返回 token、secret、claims、tenant existence 或 internal exception。
  - [x] 确认 `RequestLoggingMiddleware` auth 成功时记录 tenant/user，auth 失败时只记录 request/trace/path/status/error_code。
  - [x] 如添加 auth method 字段，只记录枚举值，例如 `jwt_bearer`、`openwebui_service_token`、`dev_headers`，不记录 token id 明文。
  - [x] 更新 `tests/integration/api/test_request_logging.py` 或新增测试，断言 `Authorization: Bearer ...` 不进入日志。
  - [x] 保持 `packages.common.audit` 与 `packages.common.logging.redact_mapping()` 的共享脱敏行为，不新增第二套不一致 redaction。

- [x] 文档和配置同步（AC: 5, 8）
  - [x] 更新 `.env.example`：补充 JWT、OpenWebUI service token、dev header gate 的示例变量，所有 secret 使用 placeholder。
  - [x] 更新 README Authentication / Open WebUI sections：说明生产接入使用 JWT bearer 或 service token，dev headers 默认禁用。
  - [x] 更新 `docs/operations/local-development.md`：加入 Open WebUI provider base URL、API key/service token 配置、curl smoke test、错误排查和安全限制。
  - [x] 文档明确 Open WebUI 不是权限治理边界，前端/adapter 不能判断权限、补造 citation 或绕过 RBAC/ACL。

- [x] 验证（AC: 1-8）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/auth/test_parsers.py tests/integration/api/test_context_dependencies.py tests/integration/api/test_openwebui_routes.py tests/integration/api/test_request_logging.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/rag/test_openwebui_adapter.py tests/unit/rag/test_query_service.py tests/integration/api/test_chat_routes.py tests/integration/api/test_query_routes.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/test_architecture_boundaries.py tests/unit/test_readme_expectations.py -q`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`

## Dev Notes

### Current Repository State

- Git baseline for this story context: `3f79c15 fix(rag): address safe source metadata review findings`.
- Worktree is not clean before story creation: `_bmad-output/planning-artifacts/epics.md` already has an unrelated local modification. Implementation agents must not overwrite or stage unrelated user changes.
- Sprint status auto-selected `7-2-open-webui-认证接入硬化` as the first backlog story after Story 7.1.
- Epic 1-6 are complete. Story 7.1 is complete and already migrated public source surfaces to safe `source_display_name`; do not rework source metadata in this story.
- Existing OpenAI-compatible route files are `apps/api/routes/openwebui.py` and `packages/rag/openwebui.py`.
- Existing auth path already includes dev headers behind `APP_ENV` + `ENABLE_DEV_AUTH_HEADERS` and JWT bearer verification through `packages/auth/parsers.py`. Story 7.2 should harden and extend this, not create a parallel auth stack.

### Existing Files To Read Before Implementation

- `apps/api/dependencies.py`
  - Current state: `get_auth_context()` first accepts HTTP bearer credentials and decodes JWT with `JwtAuthSettings.from_environment()`. If no bearer exists, it accepts dev auth headers only when `_dev_auth_headers_enabled()` returns true. It stores `request.state.auth_context`.
  - What this story changes: add or route service token handling through the same dependency layer; make production OpenWebUI token mapping explicit and testable.
  - Preserve: `RequestContext` reuse, request state caching, dev header environment gate, route-thin dependency pattern.

- `packages/auth/parsers.py`
  - Current state: contains `JwtAuthSettings`, `parse_dev_auth_headers()`, `parse_jwt_claims()`, `parse_auth_fixture()`, and `decode_jwt_token()`. JWT decode requires `exp`; optional issuer/audience validation already exists.
  - What this story changes: add service token parsing/mapping here or in a neighboring auth module, with safe error details and deterministic tests.
  - Preserve: claim normalization, `scope` fallback only when `permissions` is absent, `sub`/`user_id` conflict rejection, typed auth exceptions.

- `apps/api/routes/openwebui.py`
  - Current state: `GET /v1/models` and `POST /v1/chat/completions` require `RagQueryContextDep`; route delegates to `OpenWebUIChatAdapter`.
  - What this story changes: usually no route-level business logic. Tests may prove route does not call adapter on auth/permission failure.
  - Preserve: streaming response remains `text/event-stream` with OpenAI-compatible data-only frames, not named backend SSE events.

- `packages/rag/openwebui.py`
  - Current state: defines OpenAI-compatible request/response DTOs, request body validation, model listing, chat completion adapter, stream formatting, and audit metadata. It rejects authorization fields inside `metadata_filter`.
  - What this story changes: likely only audit/auth method metadata or error redaction tests if required. Do not parse credentials here.
  - Preserve: adapter reuses `ChatApplicationService`, ignores client `system`/`developer`/`tools` for permissions, and does not parse citations from answer text.

- `apps/api/error_handlers.py` and `apps/api/middleware.py`
  - Current state: auth errors return envelope with stable codes; request logging records one `api.request.completed` event and includes tenant/user only after auth context exists.
  - What this story changes: ensure service token/JWT auth failures stay redacted and observable.
  - Preserve: unexpected errors remain generic `INTERNAL_ERROR`; no request/response bodies in logs.

- `packages/auth/policies.py`
  - Current state: `has_rag_query_permission()` requires both `document:read` and `retrieval:query`.
  - What this story changes: service-token-derived AuthContext must satisfy or fail this same policy. Do not add OpenWebUI-only permission bypass.

### What Must Be Preserved

- Open WebUI is an integration shell, not an authorization boundary. Backend `AuthContext` and policy builder remain authoritative.
- Retrieval filters must continue to be derived from `AuthContext`; never accept tenant/user/roles/permissions from OpenAI-compatible request body.
- Public citation/source fields remain governed by Story 7.1 safe source display. Do not reintroduce `source_uri` into OpenWebUI extension fields, stream chunks, errors or audit metadata.
- Dev headers remain available only for explicit local/test smoke workflows.
- No route should import SQLAlchemy, provider SDKs, vector store adapters, retrieval internals or storage repositories.
- No real LLM, embedding provider, PostgreSQL, Redis, MinIO, Open WebUI container or external network call should be required by unit/integration tests.

### Suggested Service Token Shape

Implementation can choose the exact variable names, but use a testable, redaction-safe design:

```text
OPENWEBUI_SERVICE_TOKEN_HASHES_JSON=[
  {
    "token_sha256": "<sha256-of-token>",
    "user_id": "openwebui-service",
    "tenant_id": "tenant-abc",
    "roles": ["openwebui"],
    "department": "platform",
    "permissions": ["document:read", "retrieval:query"]
  }
]
```

If a plaintext local-only helper is introduced for developer ergonomics, it must be gated to local/test like dev headers and must not be the production path.

### Previous Story Intelligence

- Story 7.1 generalized safe source metadata across citations, SSE, OpenWebUI adapter, `/sources/resolve`, `/retrieve`, and `rag_search`. Keep that fail-closed posture: public surfaces should expose safe identifiers and summaries only.
- Story 4.7 established OpenWebUI-compatible `/v1/models` and `/v1/chat/completions` backed by `/chat`, plus `/sources/resolve`. 7.2 should harden auth for these endpoints instead of rebuilding the adapter.
- Story 6.7 tightened final answer provenance validation. Do not let OpenWebUI request fields or service token configuration weaken citation evidence.
- Current tests already cover dev headers and JWT basics; extend them instead of adding a second unrelated auth test harness.

### Git Intelligence

- Recent commits:
  - `3f79c15 fix(rag): address safe source metadata review findings`
  - `df30257 feat(rag): add safe source metadata display`
  - `aad38b5 fix(agent): address final answer validation review findings`
  - `8de1bc4 feat(agent): add final answer validation`
  - `e4f737f fix(agent): address tool call audit review findings`
- Recent work repeatedly hardened public metadata, prompt exposure, tool provenance and audit redaction. Follow the same implementation pattern: fail closed, structured DTOs, route-thin services, explicit tests for leakage.

### Latest Technical Information

- Open WebUI currently documents OpenAI-compatible provider integration by setting a base URL and API key, with the provider expected to expose `/v1/chat/completions`; `/v1/models` is the common model discovery endpoint. Preserve those contracts for Open WebUI compatibility.
- Open WebUI environment configuration supports `OPENAI_API_BASE_URL(S)` and `OPENAI_API_KEY(S)` for OpenAI-compatible providers. Treat the configured API key as a Bearer/service token presented to this backend, then map it to backend `AuthContext`.
- Open WebUI's provider key should be minimum-privilege. It must not become an all-tenant admin credential.
- Sources:
  - Open WebUI docs, "Starting with OpenAI-Compatible Servers"（访问日期 2026-06-08）: https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-openai-compatible/
  - Open WebUI docs, "Environment Variable Configuration"（访问日期 2026-06-08）: https://docs.openwebui.com/getting-started/env-configuration/
  - OpenAI API docs, "Chat Completions"（访问日期 2026-06-08）: https://platform.openai.com/docs/api-reference/chat

### References

- `_bmad-output/planning-artifacts/epics.md#Story-7.2-Open-WebUI-认证接入硬化`
- `_bmad-output/planning-artifacts/epics.md#Epic-7-Open-WebUI-展示闭环与生产接入硬化`
- `_bmad-output/planning-artifacts/architecture.md#Authentication-&-Security`
- `_bmad-output/planning-artifacts/architecture.md#Frontend-Architecture`
- `_bmad-output/planning-artifacts/architecture.md#API-&-Communication-Patterns`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md#Standard-Design-Implementation`
- `_bmad-output/implementation-artifacts/7-1-source-metadata-安全展示策略.md`
- `_bmad-output/implementation-artifacts/4-7-open-webui-chat-adapter-source-detail-与轻量前端契约.md`
- `project-context.md#14-API-规则`
- `project-context.md#16-权限规则`
- `project-context.md#18-可观测性规则`
- `apps/api/dependencies.py`
- `apps/api/routes/openwebui.py`
- `apps/api/error_handlers.py`
- `apps/api/middleware.py`
- `packages/auth/context.py`
- `packages/auth/parsers.py`
- `packages/auth/policies.py`
- `packages/rag/openwebui.py`
- `tests/unit/auth/test_parsers.py`
- `tests/integration/api/test_context_dependencies.py`
- `tests/integration/api/test_openwebui_routes.py`
- `tests/integration/api/test_request_logging.py`
- `.env.example`
- `README.md`
- `docs/operations/local-development.md`

## Validation Checklist

Validation Result: PASS（2026-06-08T19:20:53+08:00）

- [x] Story 明确了 JWT bearer、OpenWebUI service token、dev headers 和 test fixture 的边界。
- [x] Acceptance Criteria 覆盖 `/v1/models`、`/v1/chat/completions`、JWT、service token、dev header gate、权限不足、审计日志、OpenWebUI request body 不扩大权限、测试和文档。
- [x] Tasks 指向当前已存在的 UPDATE 文件，避免重建 OpenWebUI adapter、RAG 链路或 source metadata。
- [x] Dev Notes 记录了当前代码状态、必须保留的行为、前序 story lessons、recent git patterns 和最新 OpenWebUI/OpenAI-compatible context。
- [x] 明确测试使用 fake/stub/local token，不调用真实 LLM、embedding、vector store、PostgreSQL、Redis、MinIO、Open WebUI、网络或外部 provider。
- [x] 明确 README 和 operations docs 在实现阶段必须同步；本 create-story 仅创建 story 文件并更新 sprint status。

## Change Log

- 2026-06-08: Created comprehensive Story 7.2 developer context for Open WebUI authentication hardening.
- 2026-06-08: Implemented Open WebUI service token auth hardening, route fail-closed tests, auth method logging/audit metadata, and documentation/config updates.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- RED: `.venv\Scripts\python.exe -m pytest tests/unit/auth/test_parsers.py tests/integration/api/test_context_dependencies.py tests/integration/api/test_openwebui_routes.py tests/integration/api/test_request_logging.py tests/unit/common/test_logging.py -q` failed on missing `OpenWebUIServiceTokenSettings`.
- RED: `.venv\Scripts\python.exe -m pytest tests/unit/common/test_context.py tests/unit/rag/test_openwebui_adapter.py -q` failed on missing `auth_method` context/audit fields.
- GREEN/verification: focused pytest groups, `ruff check .`, `mypy apps packages tests`, and full `pytest -q` passed.

### Completion Notes List

- Added `OpenWebUIServiceTokenSettings` and `parse_openwebui_service_token()` in `packages.auth.parsers`, using SHA-256 token hashes, default minimum RAG permissions, safe config errors, and deterministic local tests.
- Updated `apps.api.dependencies.get_auth_context()` to map configured Open WebUI service tokens and verified JWT bearer tokens into the same `AuthContext` path, with dev headers still gated by `APP_ENV` and `ENABLE_DEV_AUTH_HEADERS`.
- Added `auth_method` to `AuthenticatedRequestContext`, request logging, and OpenWebUI adapter audit metadata as a safe enum summary only.
- Expanded OpenWebUI route tests for service token success, missing/invalid token, permission denial, adapter-not-called failure paths, streaming success, and forbidden authorization metadata filters.
- Updated `.env.example`, README, and local operations docs with JWT, service token hash, dev header gate, Open WebUI provider configuration, smoke curl commands, and security boundaries.
- Verification passed: 45 focused auth/OpenWebUI/log tests, 42 RAG/chat regressions, 26 architecture/README tests, `ruff check .`, `mypy apps packages tests`, and full test suite `859 passed`.

### File List

- `.env.example`
- `README.md`
- `_bmad-output/implementation-artifacts/7-2-open-webui-认证接入硬化.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `apps/api/dependencies.py`
- `apps/api/middleware.py`
- `docs/operations/local-development.md`
- `packages/auth/parsers.py`
- `packages/common/context.py`
- `packages/common/logging.py`
- `packages/rag/openwebui.py`
- `tests/integration/api/test_context_dependencies.py`
- `tests/integration/api/test_openwebui_routes.py`
- `tests/integration/api/test_request_logging.py`
- `tests/unit/auth/test_parsers.py`
- `tests/unit/common/test_context.py`
- `tests/unit/common/test_logging.py`
- `tests/unit/rag/test_openwebui_adapter.py`
- `tests/unit/test_readme_expectations.py`

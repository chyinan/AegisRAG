---
baseline_commit: 74a464d
---

# Story 7.3: Open WebUI Docker Compose Profile

Status: review

生成时间：2026-06-08T20:26:00+08:00

## Story

As a 平台负责人,
I want 用 Docker Compose profile 启动 Open WebUI 与本地 API 栈,
so that 演示环境可以用一组命令稳定复现。

## Acceptance Criteria

1. **Open WebUI profile 启动完整本地演示栈**
   - Given 开发者已经从 `.env.example` 准备本地 `.env`
   - When 执行 Open WebUI profile 的 compose 启动命令
   - Then 启动 `api`、`worker-ingestion`、`worker-embedding`、`postgres`、`redis`、`minio`、`migration` 和 `open-webui`
   - And `open-webui` 默认连接后端 OpenAI-compatible base URL `http://api:8000/v1`
   - And 宿主机访问 URL、容器内 URL 和 Open WebUI provider API key 配置必须在文档中明确区分

2. **默认 compose 行为不启动 Open WebUI**
   - Given 开发者只想运行后端依赖、后端测试、lint 或 mypy
   - When 不启用 Open WebUI profile 执行现有 compose 命令
   - Then 不启动 `open-webui` 服务
   - And 现有 Python 测试、Docker compose config test、ruff、mypy 不依赖 Open WebUI 容器、Open WebUI 网络请求或真实外部 LLM

3. **Open WebUI 连接后端使用最小权限 service token**
   - Given `open-webui` 容器使用 OpenAI-compatible provider API key 调用后端
   - When Compose 注入 Open WebUI 连接配置
   - Then Open WebUI 容器接收明文 provider API key，后端只接收 `OPENWEBUI_SERVICE_TOKEN_HASHES_JSON` 中的 SHA-256 hash
   - And service token 映射权限默认不超过 `document:read` 和 `retrieval:query`
   - And 不得把明文 service token、JWT secret、API key、数据库 URL、MinIO 凭据、object key、本机绝对路径或容器内部路径写入日志、README 示例输出、测试快照或 API 响应

4. **启动顺序和 readiness fail closed**
   - Given API、migration 或依赖服务未 ready
   - When `open-webui` 启动或用户发起 `/v1/models` / `/v1/chat/completions`
   - Then Compose 使用 healthcheck / `depends_on` 条件避免明显早启动
   - And 后端 `/health`、`/ready`、OpenAI-compatible auth failure 和 readiness failure 返回安全状态摘要
   - And 错误响应不得暴露 `DATABASE_URL`、Redis URL、MinIO 凭据、JWT secret、service token、provider API key、SQL、prompt、chunk content 或 provider raw response

5. **Open WebUI 容器配置可重现且可维护**
   - Given 开发者需要稳定演示
   - When 查看 compose、env example 和 operations docs
   - Then `open-webui` 镜像、端口、volume、restart 策略、profile 名称、provider base URL、provider API key、`WEBUI_SECRET_KEY` 或等价持久会话配置都被文档化
   - And Open WebUI image tag 可以配置；本地默认可使用官方镜像，生产建议 pin 版本而不是依赖 floating `main`
   - And Open WebUI data 使用独立 named volume，停止默认栈或不启用 profile 不应删除该 volume

6. **测试和文档同步**
   - Given Story 7.3 实现完成
   - When 运行验证
   - Then `tests/integration/docker/test_compose_config.py` 或等价测试覆盖：`open-webui` 服务存在、带 profile、默认 config 不要求启动 Open WebUI、profile config 包含核心服务依赖、`OPENAI_API_BASE_URL(S)` 指向 `http://api:8000/v1`、`OPENAI_API_KEY(S)` 来自环境变量且不硬编码
   - And `.env.example`、`docker/README.md`、`docs/operations/local-development.md`、README 同步 Open WebUI profile 启动命令、token hash 生成命令、宿主机/容器 URL、能力限制和安全边界
   - And README 项目进度必须从 Story 7.2 更新到 Story 7.3 完成状态；若实现阶段发现 README 无需更新，最终回复必须说明原因

## Tasks / Subtasks

- [x] 扩展 Docker Compose profile（AC: 1, 2, 3, 4, 5）
  - [x] 在 `docker/compose.yaml` 添加 `open-webui` 服务并设置 `profiles: ["open-webui"]`，保持未启用 profile 时默认后端服务行为不变。
  - [x] 使用官方 Open WebUI 镜像，镜像 tag 来自 `OPENWEBUI_IMAGE` 或等价环境变量；本地默认可为 `ghcr.io/open-webui/open-webui:main`，文档提示生产 pin 版本。
  - [x] 暴露宿主机端口，例如 `${OPENWEBUI_PORT:-3000}:8080`，并添加独立 `open-webui-data:/app/backend/data` volume。
  - [x] 配置 Open WebUI provider 指向容器网络内后端：优先使用 `OPENAI_API_BASE_URL=http://api:8000/v1` 或 `OPENAI_API_BASE_URLS=http://api:8000/v1`，并配置对应 `OPENAI_API_KEY` / `OPENAI_API_KEYS`。
  - [x] 不要把 `open-webui` 作为 `api`、worker、migration、postgres、redis、minio 的 dependency；依赖方向只能是 `open-webui` 等待后端栈。

- [x] 接入最小权限 Open WebUI service token 配置（AC: 3, 6）
  - [x] 在 `.env.example` 增加 Open WebUI 明文 provider key 的本地占位变量，例如 `OPENWEBUI_PROVIDER_API_KEY=<replace_with_local_openwebui_provider_key>`，并保留后端 `OPENWEBUI_SERVICE_TOKEN_HASHES_JSON` hash 配置。
  - [x] 文档给出本地生成 SHA-256 hash 的 PowerShell 命令，明确明文 key 只填入 Open WebUI provider 配置，后端只保存 hash。
  - [x] `OPENWEBUI_SERVICE_TOKEN_HASHES_JSON` 中的 sample 权限保持 `document:read`、`retrieval:query`；不得为了演示授予 `document:manage`、`agent:*` 或跨租户权限。
  - [x] 确认 Compose 环境不会把 JWT secret、数据库密码、MinIO secret 或 service token 明文写入 command、labels、healthcheck test 或日志友好字段。

- [x] 建立 startup/readiness 行为（AC: 1, 4, 5）
  - [x] `open-webui` 依赖 `api` 的 `service_healthy`，并间接通过 `api` 保留 postgres/redis/minio/migration 顺序。
  - [x] 如使用 `depends_on` 指向 `migration` 或 dependency service，必须保持 `service_completed_successfully` / `service_healthy` 语义一致，不引入 race。
  - [x] 不要求 Open WebUI healthcheck 成为后端测试依赖；如果添加 healthcheck，使用容器内可用命令并避免安装额外调试依赖。
  - [x] 保持 API `/ready` 输出只包含 dependency name/status/latency/error_code，不暴露 URL、credential、路径或 secrets。

- [x] 更新 Docker/operations/README 文档（AC: 1, 2, 3, 5, 6）
  - [x] `docker/README.md` 增加 Open WebUI profile 服务说明、启动/停止命令、端口、volume、容器网络 URL 和宿主机 URL。
  - [x] `docs/operations/local-development.md` 增加 profile walkthrough：复制 `.env`、替换 secrets、生成 service token hash、启动 profile、访问 `http://127.0.0.1:3000`、验证 `/v1/models`。
  - [x] README Build Status 和 Docker Compose sections 更新到 Story 7.3 完成后的当前能力，说明 Open WebUI profile 是可选演示入口，不是权限治理边界。
  - [x] 文档明确 Open WebUI `OPENAI_API_BASE_URL(S)` 是容器内 `http://api:8000/v1`，宿主机手动 curl 使用 `http://127.0.0.1:8000/v1`。
  - [x] 文档保留限制：不实现 Open WebUI function/tool bridge、`/v1/embeddings`、image/audio endpoints、real provider adapter、完整自定义管理台或生产 SSO。

- [x] 扩展 compose 配置测试（AC: 2, 3, 4, 5, 6）
  - [x] 更新 `tests/integration/docker/test_compose_config.py`：静态断言 `open-webui` 服务、`profiles`、image env、port env、volume、OpenAI-compatible base URL、API key env 和 `depends_on`。
  - [x] 在 Docker CLI 可用时运行 `docker compose -f docker/compose.yaml config`，断言默认 config 包含服务定义但不会要求测试启动容器。
  - [x] 增加 profile config 验证命令或测试路径，例如 `docker compose -f docker/compose.yaml --profile open-webui config`，使用 fake local env values，不接触真实 Open WebUI。
  - [x] 测试断言 compose/config/test fixture 输出中不包含示例明文 secret、JWT secret、数据库密码、MinIO secret 或真实 token。

- [x] 验证（AC: 1-6）
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/docker/test_compose_config.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/test_readme_expectations.py -q`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`
  - [x] 如 Docker CLI 可用：`docker compose -f docker/compose.yaml config` 和 `docker compose -f docker/compose.yaml --profile open-webui config`

## Dev Notes

### Current Repository State

- Git baseline for this story context: `74a464d feat(openwebui): harden service token auth`.
- Worktree is not clean before story creation. Dirty files include Story 7.2 artifacts, sprint status, epics, OpenWebUI auth/code/test files. Implementation agents must inspect `git status` before editing and must not revert or stage unrelated user changes.
- Sprint status auto-selected `7-3-open-webui-docker-compose-profile` as the first backlog story after Story 7.2.
- Epic 1-6 and Story 7.1-7.2 are complete. Story 7.3 should not rebuild auth, retrieval, RAG, citation, source resolution, Agent, or sidecar UI.
- Existing OpenAI-compatible endpoints are `GET /v1/models` and `POST /v1/chat/completions` in `apps/api/routes/openwebui.py`, backed by `packages/rag/openwebui.py`.
- Existing OpenWebUI service token auth is implemented in `packages/auth/parsers.py` and `apps/api/dependencies.py`; compose must feed that path, not introduce a parallel auth mechanism.

### Existing Files To Read Before Implementation

- `docker/compose.yaml`
  - Current state: defines `postgres`, `redis`, `minio`, one-shot `migration`, `api`, `worker-ingestion`, and `worker-embedding`; includes shared `x-api-environment`, service healthchecks, and named volumes.
  - What this story changes: add optional `open-webui` service behind `profiles: ["open-webui"]`, add Open WebUI env/port/volume config, and wire it to `api` via container network URL.
  - Preserve: required secrets stay environment-sourced; `api` keeps healthcheck; `api` waits for postgres/redis/minio/migration; default core stack remains usable without Open WebUI.

- `.env.example`
  - Current state: includes `JWT_SECRET`, `ENABLE_DEV_AUTH_HEADERS=false`, `OPENWEBUI_SERVICE_TOKEN_HASHES_JSON`, core dependency env, fake embedding/LLM provider env, tool/agent defaults, and external provider API key placeholders.
  - What this story changes: add Open WebUI container/provider variables such as image tag, UI port, provider API key, optional `WEBUI_SECRET_KEY`, and base URL override if needed.
  - Preserve: all secrets remain placeholders; do not add real tokens or tenant/user values outside synthetic local examples.

- `docker/README.md`
  - Current state: documents the core Docker stack and commands from Story 1.6.
  - What this story changes: add Open WebUI profile commands and profile-specific troubleshooting.
  - Preserve: existing core compose commands should continue working and should not mention Open WebUI as mandatory.

- `docs/operations/local-development.md`
  - Current state: documents local API, compose, ingestion, retrieval, RAG, OpenWebUI auth smoke checks, source resolve, auth, logs, and safety boundaries.
  - What this story changes: add profile-based Open WebUI walkthrough and clarify container URL vs host URL.
  - Preserve: Open WebUI remains an entry point, not a governance boundary; dev headers remain disabled by default.

- `tests/integration/docker/test_compose_config.py`
  - Current state: statically checks core services/healthchecks and validates `docker compose config` when Docker CLI is present.
  - What this story changes: add open-webui profile assertions and optional `--profile open-webui config` validation.
  - Preserve: tests must not start containers and must skip Docker-specific config validation when Docker CLI is unavailable.

- `README.md`
  - Current state: progress says implementation is complete through Epic 7.2 and remaining Epic 7 includes optional Open WebUI Docker Compose profile.
  - What this story changes: implementation stage should update progress and Docker Compose usage once Story 7.3 is done.
  - Preserve: README must not claim sidecar, synthetic demo corpus, diagnostics dashboard, Open WebUI tool bridge, real provider adapters, or production SSO are complete.

### What Must Be Preserved

- Open WebUI is not the authorization boundary. Backend `AuthContext`, RBAC, ACL filters, source visibility checks, and audit remain authoritative.
- `open-webui` must be optional. Default `docker compose up` / core service commands must not start or require Open WebUI.
- Open WebUI request bodies, model names, usernames, forwarded headers, chat titles, or UI user IDs must not determine backend tenant/user/permissions.
- Public responses must keep Story 7.1 safe source metadata. Do not reintroduce raw `source_uri`, local absolute paths, MinIO object keys, bucket paths, token-bearing URLs, prompt text, chunk content, SQL, vectors, embeddings, provider raw response, or secrets.
- Story 7.2 auth hardening must stay intact: verified JWT bearer, hashed OpenWebUI service token, dev header gate, fail closed permission checks, redacted errors and logs.
- Tests must not require a real Open WebUI container, real Docker daemon unless explicitly skipped, real LLM/embedding provider, PostgreSQL, Redis, MinIO, network access, or external API calls.

### Previous Story Intelligence

- Story 7.2 implemented hash-configured OpenWebUI service tokens and made `/v1/models` and `/v1/chat/completions` fail closed. Use `OPENWEBUI_SERVICE_TOKEN_HASHES_JSON` instead of inventing a new token table, prompt-level auth, or OpenWebUI-only bypass.
- Story 7.1 removed unsafe source locators from public payloads. Compose/docs must not add debugging examples that log raw paths, object keys, URLs with query tokens, prompts, or chunk content.
- Story 4.7 established OpenWebUI-compatible chat backed by `/chat`. This story only makes that integration reproducible through Docker Compose.
- Story 1.6 established the core compose stack with `migration` as a one-shot Alembic service and API healthcheck using Python standard library. Extend that structure.

### Git Intelligence

- Recent commits:
  - `74a464d feat(openwebui): harden service token auth`
  - `3f79c15 fix(rag): address safe source metadata review findings`
  - `df30257 feat(rag): add safe source metadata display`
  - `aad38b5 fix(agent): address final answer validation review findings`
  - `8de1bc4 feat(agent): add final answer validation`
- Recent work prioritizes fail-closed auth, redaction, safe public metadata, and tests that prove rejected requests do not call downstream adapters. Keep the same posture in compose tests and docs.

### Latest Technical Information

- Open WebUI official docs describe Docker as a recommended launch path and publish images at `ghcr.io/open-webui/open-webui`; the quick-start Compose example maps host `3000` to container `8080` and persists `/app/backend/data`.
- Open WebUI official docs list `OPENAI_API_BASE_URL`, `OPENAI_API_BASE_URLS`, `OPENAI_API_KEY`, and `OPENAI_API_KEYS` for OpenAI-compatible providers. These variables are marked persistent config, so docs should warn that already-initialized Open WebUI volumes may keep older provider settings until changed in the UI or persistent config is reset.
- Open WebUI docs note production environments should pin a specific image version rather than relying on floating tags such as `main`.
- Docker Compose official docs state services with `profiles` are not started by default, and can be enabled with `--profile <name>` or `COMPOSE_PROFILES`.
- Docker Compose official docs support `depends_on` conditions including `service_healthy` and `service_completed_successfully`; use these to model API/dependency readiness instead of sleep loops.
- Sources accessed 2026-06-08:
  - Open WebUI Quick Start: https://docs.openwebui.com/getting-started/quick-start/
  - Open WebUI Environment Variable Configuration: https://docs.openwebui.com/getting-started/env-configuration/
  - Open WebUI Updating: https://docs.openwebui.com/getting-started/updating
  - Docker Compose profiles: https://docs.docker.com/compose/how-tos/profiles/
  - Docker Compose startup order: https://docs.docker.com/compose/how-tos/startup-order/

### References

- `_bmad-output/planning-artifacts/epics.md#Story-7.3-Open-WebUI-Docker-Compose-Profile`
- `_bmad-output/planning-artifacts/epics.md#Epic-7-Open-WebUI-展示闭环与生产接入硬化`
- `_bmad-output/planning-artifacts/architecture.md#Infrastructure-&-Deployment`
- `_bmad-output/planning-artifacts/architecture.md#Frontend-Architecture`
- `_bmad-output/planning-artifacts/architecture.md#Authentication-&-Security`
- `project-context.md#20-Docker-和部署规则`
- `project-context.md#24-调研后的执行优先级补充`
- `_bmad-output/implementation-artifacts/7-1-source-metadata-安全展示策略.md`
- `_bmad-output/implementation-artifacts/7-2-open-webui-认证接入硬化.md`
- `docker/compose.yaml`
- `.env.example`
- `docker/README.md`
- `docs/operations/local-development.md`
- `tests/integration/docker/test_compose_config.py`
- `apps/api/routes/openwebui.py`
- `packages/auth/parsers.py`
- `apps/api/dependencies.py`
- `README.md`

## Validation Checklist

Validation Result: PASS（2026-06-08T20:26:00+08:00）

- [x] Story 明确了 Open WebUI profile 是可选演示入口，默认 compose 后端栈不启动 Open WebUI。
- [x] Acceptance Criteria 覆盖完整服务栈、默认行为、service token hash、readiness fail closed、可维护配置、测试和文档。
- [x] Tasks 指向当前 UPDATE 文件，避免重建 auth、RAG、OpenWebUI adapter、source metadata 或 Agent。
- [x] Dev Notes 记录了当前代码状态、必须保留的行为、前序 story lessons、recent git patterns 和最新官方 Open WebUI/Docker Compose 信息。
- [x] 明确测试不启动真实 Open WebUI 容器，不调用真实 LLM、embedding、PostgreSQL、Redis、MinIO、网络或外部 provider。
- [x] 明确 README 和 operations docs 在实现阶段必须同步；本 create-story 仅创建 story 文件并更新 sprint status。

## Change Log

- 2026-06-08: Created comprehensive Story 7.3 developer context for Open WebUI Docker Compose profile.
- 2026-06-08: Implemented optional Open WebUI Docker Compose profile, env/docs updates, profile config tests, and README progress update.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- 2026-06-08: Red phase confirmed missing `open-webui` service via `pytest tests/integration/docker/test_compose_config.py -q`.
- 2026-06-08: Verified default and `--profile open-webui` Docker Compose config with fake local env values.
- 2026-06-08: Full regression validation passed: 871 pytest tests, ruff, mypy.

### Completion Notes List

- Added optional `open-webui` Compose service behind `profiles: ["open-webui"]` with configurable official image, host port, provider base URL/API key, persistent `open-webui-data` volume, and `api` health dependency.
- Extended API container environment to receive `OPENWEBUI_SERVICE_TOKEN_HASHES_JSON` hash configuration while keeping Open WebUI plaintext provider key isolated to the Open WebUI service.
- Updated `.env.example`, `docker/README.md`, `docs/operations/local-development.md`, and README with profile commands, host/container URL distinction, token hash generation, minimum permissions, and current Story 7.3 status.
- Added compose/profile tests and README expectation coverage without requiring a real Open WebUI container, external provider, or network service.

### File List

- `.env.example`
- `README.md`
- `docker/README.md`
- `docker/compose.yaml`
- `docs/operations/local-development.md`
- `tests/integration/docker/test_compose_config.py`
- `tests/unit/test_readme_expectations.py`

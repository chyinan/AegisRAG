---
baseline_commit: NO_VCS
---

# Story 1.6: Docker Compose 本地启动核心依赖

Status: done

生成时间：2026-05-27T12:36:18+08:00

## Story

As a 平台负责人,
I want 使用 Docker Compose 启动 API、worker、PostgreSQL、Redis 和 MinIO,
so that 本地环境可以稳定复现后续 RAG 开发链路。

## Acceptance Criteria

1. **Docker Compose 定义核心服务并提供 API health check**
   - Given 开发者配置 `.env`
   - When 执行 Docker Compose 启动命令
   - Then `api`、`worker-ingestion`、`worker-embedding`、`postgres`、`redis`、`minio` 服务被定义
   - And `api` 提供 health check

2. **外部依赖未就绪时 readiness 返回明确状态并产生日志**
   - Given PostgreSQL、Redis 或 MinIO 未就绪
   - When 调用 `GET /ready`
   - Then readiness 返回明确的未就绪状态
   - And 日志包含可排障的 dependency 状态摘要

3. **worker 服务使用独立队列且队列 payload 受限**
   - Given worker 服务启动
   - When 查看 worker 配置
   - Then ingestion 和 embedding worker 使用不同 queue name
   - And queue payload 只包含 JSON 可序列化 ID 和参数摘要

## Tasks / Subtasks

- [x] 补齐 Compose 和容器构建文件（AC: 1）
  - [x] 新增 `docker/compose.yaml`，至少定义 `api`、`worker-ingestion`、`worker-embedding`、`postgres`、`redis`、`minio`。
  - [x] 新增 `docker/Dockerfile.api` 和 `docker/Dockerfile.worker`，使用 Python 3.11+ 与 `uv sync --frozen` 或等价锁文件安装流程。
  - [x] API 容器命令必须运行 `apps.api.main:app`，监听 `0.0.0.0:8000`；不要在应用启动时运行 Alembic 或 `Base.metadata.create_all()`。
  - [x] 新增 migration 一次性服务或等价启动步骤，使用同一应用镜像执行 `uv run alembic upgrade head`，并通过 `depends_on.condition: service_healthy` 等待 PostgreSQL。
  - [x] `api` 服务必须有容器级 healthcheck，探测 `GET /health`；如镜像不安装 `curl`，使用 Python 标准库探测命令，避免额外依赖。
  - [x] `postgres`、`redis`、`minio` 必须有可验证 healthcheck；探针命令必须在对应镜像中真实存在。
  - [x] 新增 `.dockerignore` 或更新构建上下文规则，排除 `.venv`、缓存、`.env`、`_bmad-output`、`.agents`、测试缓存和本地数据库文件。

- [x] 扩展配置加载和 `.env.example`（AC: 1, 2, 3）
  - [x] 扩展 `packages/common/config.py` 的 `AppSettings`，至少读取 `DATABASE_URL`、`REDIS_URL`、`MINIO_ENDPOINT`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`、`MINIO_BUCKET`、`WORKER_QUEUE_NAME`、`READINESS_TIMEOUT_SECONDS`。
  - [x] `.env.example` 只提供占位或本地示例值，不提交真实密码、API key、租户 ID、用户 ID 或机器绝对路径。
  - [x] Compose 默认环境应使用服务名 DNS，例如 `postgres`、`redis`、`minio`，不要硬编码宿主机 IP。
  - [x] MinIO root/access secret 只从环境变量注入；日志、readiness details 和错误响应不得输出 secret。

- [x] 实现真实且非泄密的 readiness 探针（AC: 2）
  - [x] 将 `packages/common/health.py` 中 readiness 从纯配置摘要升级为可配置依赖探针；未配置依赖保持 `not_configured` 且不阻塞纯本地 Python 测试。
  - [x] PostgreSQL readiness 使用现有 SQLAlchemy async engine/session 边界或轻量 async DB ping；失败返回 `unavailable` 或 `degraded`，不得暴露连接串。
  - [x] Redis readiness 使用 `redis.asyncio` ping，设置 timeout，失败返回结构化状态。
  - [x] MinIO readiness 使用 `httpx.AsyncClient` 调用配置的 health endpoint，设置 timeout，失败返回结构化状态。
  - [x] `GET /ready` route 可改为 `async def`；route 仍只调用 health/application helper，不在 route 内写探针细节。
  - [x] 当任一已配置且必需依赖不可用时，`ReadinessData.ready` 必须为 `false`；未配置依赖在非 Compose 本地测试中不得让应用 import 或 `/health` 失败。
  - [x] 调用 `/ready` 时写一条结构化 readiness 日志，例如 `api.readiness.checked`，包含 dependency name/status/latency/configured/error_code，不包含 URL、密码、token 或企业内容。

- [x] 建立 worker 队列配置和受限 payload 契约（AC: 3）
  - [x] 更新 `apps/worker/main.py`，读取 `WORKER_QUEUE_NAME`、`REDIS_URL` 和安全 serializer 配置，能以 ingestion 或 embedding 队列名启动空闲 worker。
  - [x] Compose 中 `worker-ingestion` 设置 `WORKER_QUEUE_NAME=ingestion`，`worker-embedding` 设置 `WORKER_QUEUE_NAME=embedding`；二者不能共用同一 queue name。
  - [x] RQ 使用显式 JSON serializer 或等价安全约束；不得依赖默认 pickle serializer 处理不可信 payload。
  - [x] 新增 `packages/data/queue/` 或等价 infrastructure 模块，定义最小 queue payload DTO/validator，payload 只允许字符串 ID、数字、布尔值、null、列表/对象等 JSON 可序列化摘要。
  - [x] payload 中不得包含文件对象、SQLAlchemy model、AuthContext 对象、文档全文、prompt、token、API key 或本机绝对路径。
  - [x] 当前 Story 不实现 ingestion/embedding job 业务逻辑，只提供 worker 启动、队列隔离和 payload 安全边界。

- [x] 补充测试与 Compose 验证（AC: 1, 2, 3）
  - [x] 新增配置测试，断言 `REDIS_URL`、MinIO 配置、queue name 和 readiness timeout 能从环境读取，缺省不会导致 app import 失败。
  - [x] 更新 health/readiness 单测和 API 集成测试，覆盖未配置、依赖 OK、依赖失败、敏感信息不泄露、readiness 日志字段。
  - [x] 新增 worker/queue 单测，断言 ingestion 和 embedding queue name 可配置且不同，非 JSON payload 被拒绝。
  - [x] 新增 Compose 配置测试，优先使用 `docker compose -f docker/compose.yaml config` 校验服务、healthcheck、depends_on 和环境变量；若 CI 无 Docker，测试应显式 skip 并说明原因。
  - [x] 本地可用 Docker 时运行 `docker compose -f docker/compose.yaml up -d --build postgres redis minio migration api worker-ingestion worker-embedding` 或等价最小 smoke，并调用 `/health`、`/ready`。
  - [x] 继续运行 `uv run pytest`、`uv run ruff check .`、`uv run mypy apps packages tests`。

- [x] 更新文档（AC: 1, 2, 3）
  - [x] 更新 `README.md` 和 `docs/operations/local-development.md`，给出 `.env` 准备、Compose 启动、migration、health/readiness、worker 队列和关闭清理命令。
  - [x] 更新 `docker/README.md`，替换“未实现”的占位说明，列出服务、端口、volume、healthcheck 和常见故障排查。
  - [x] 文档必须说明 `.env` 不可提交、MinIO secret 不可进日志、`GET /ready` 不泄露连接串。

### Review Findings

- [x] [Review][Patch] `/ready` dependency failures still return HTTP 200 [apps/api/routes/health.py:19]
- [x] [Review][Patch] Readiness aggregation lets probe exceptions escape as endpoint failures [packages/data/readiness.py:174]
- [x] [Review][Patch] Queue payload secret filtering misses common secret keys and secret-looking values [packages/data/queue/contracts.py:7]
- [x] [Review][Patch] QueuePayload does not apply path/secret validation to top-level ID fields [packages/data/queue/contracts.py:24]
- [x] [Review][Patch] Queue payload validation accepts non-standard JSON numbers such as NaN and Infinity [packages/data/queue/contracts.py:56]
- [x] [Review][Patch] Worker Redis connections are created without socket timeouts [packages/data/queue/rq_worker.py:36]
- [x] [Review][Patch] `.dockerignore` excludes `.env` but misses `.env.*` secret variants [.dockerignore:3]
- [x] [Review][Patch] Legacy `get_readiness_data()` still exposes non-probing readiness behavior [packages/common/health.py:35]
- [x] [Review][Patch] README still says Docker Compose and live readiness are future work [README.md:237]

## Dev Notes

### 当前仓库状态

- 当前目录不是 git repository，无法读取 commit 历史；实现模式来自 Story 1.1 到 Story 1.5 的 story 文件和当前代码扫描。
- `docker/README.md` 明确写着 Docker Compose 尚未实现，本 Story 应替换该占位。
- 当前没有 `docker/compose.yaml`、`docker/Dockerfile.api`、`docker/Dockerfile.worker`、`.dockerignore`。
- `.env.example` 已包含 `DATABASE_URL`、`REDIS_URL`、`MINIO_ENDPOINT`、`VECTOR_STORE_TYPE` 和模型 provider/API key 占位，但 `packages.common.config.AppSettings` 当前只读取 `DATABASE_URL`。
- `packages/common/health.py` 当前返回 database、redis、minio、vector_store 的非阻塞配置摘要；Redis 和 MinIO 消息仍写着 Story 1.6 才引入 readiness。
- `apps/api/routes/health.py` 当前 `/ready` 是同步 route，直接调用 `get_readiness_data()`。
- `apps/api/main.py` 已配置 request logging middleware、error handlers 和 health routes；不要为了 Compose 把业务逻辑塞进 route 或 app startup。
- `apps/worker/main.py` 目前只是 placeholder，没有读取 queue name，也没有启动 RQ worker。
- `pyproject.toml` 已有 `redis>=5.2.0,<6`、`rq>=2.9.0,<3`、`httpx>=0.28.0,<1`、`sqlalchemy>=2.0.50,<3`、`alembic>=1.18.4,<2`，本 Story 通常不需要新增运行时依赖。
- Alembic 已在 Story 1.5 完成基础治理表 migration；Compose 应运行 migration，不应绕过 Alembic 创建 schema。

### Source Context

- Story 1.6 覆盖 FR31 和 FR32：本地 Compose 必须能启动 API、worker、PostgreSQL、Redis、MinIO，且 health/readiness 和依赖状态可观测。[Source: `_bmad-output/planning-artifacts/epics.md#Story 1.6`]
- PRD FR32 要求 Compose 至少包含 `api`、`worker-ingestion`、`worker-embedding`、`postgres`、`redis`、`minio`，并包含 migration 和 health check。[Source: `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-32`]
- Architecture 将 Redis + RQ 定为 MVP 异步任务默认方案，MinIO 定为默认对象存储，PostgreSQL + pgvector 定为默认数据和向量承载层。[Source: `_bmad-output/planning-artifacts/architecture.md#Core Architectural Decisions`]
- Architecture 的本地 Compose 服务清单和目录建议包含 `docker/compose.yaml`、`Dockerfile.api`、`Dockerfile.worker`、`docker/postgres/init.sql`、`docker/minio/`。[Source: `_bmad-output/planning-artifacts/architecture.md#Project Structure`]
- Implementation readiness 报告确认 FR32 已被 Epic 1 覆盖，且 worker queue backlog observability 属于可靠性/可观测性方向。[Source: `_bmad-output/planning-artifacts/implementation-readiness-report-2026-05-27.md#Requirements Coverage`]

### Architecture Requirements

- 本 Story 横跨 Infrastructure/Deployment、API system endpoint 和 worker infrastructure，不属于 RAG、retrieval、agent 或 ingestion 业务逻辑实现。
- API route 保持薄层。`/ready` 可以变成 async route，但 route 只编排 `get_readiness_data()` 或 `collect_readiness()`，探针实现放在 common/application helper 或 infrastructure adapter 中。
- `packages/common` 不得导入 FastAPI、Redis、MinIO SDK、SQLAlchemy engine 对象或 RQ。当前架构边界测试禁止 common 引入 infrastructure/framework；如果 readiness 探针需要 Redis/HTTP/DB 客户端，应放在不破坏边界的位置，或调整设计为 common 定义 DTO/Protocol、infrastructure 实现探针。
- 如果选择把实际 probe 实现在 `packages/common/health.py`，必须确认架构边界测试允许其导入；当前测试会禁止 common 导入 `redis`、`sqlalchemy`、`httpx`、`minio`，所以更稳妥的做法是 common 保留 DTO 和纯函数，新增 `packages/data/readiness.py` 或 `packages/common/health_ports.py` + infrastructure probes。
- Storage schema 仍由 Alembic 管理。容器启动可以有 migration service，但 `create_app()` 不得运行 migration 或 `Base.metadata.create_all()`。
- Docker Compose 是本地开发/验证入口，不是生产 secret management。不要把 `.env`、真实密码、token 或企业配置提交进仓库。
- OpenSearch/Milvus/Prometheus/Grafana 在本 Story 仍为可选后置项，不要扩大范围。

### Current Files To Preserve And Extend

- `packages/common/config.py`
  - Current state: `AppSettings` 只读取 `DATABASE_URL`。
  - Story change: 扩展配置字段，但保持 `BaseSettings(env_prefix="", extra="ignore")` 的简单环境变量读取模式。
  - Preserve: 不硬编码真实服务地址或 secret；import 不创建网络连接。

- `packages/common/health.py`
  - Current state: 定义 `HealthData`、`DependencyStatus`、`ReadinessData`，返回非阻塞 dependency 摘要。
  - Story change: readiness 需要能表达真实 probe 状态和依赖失败；可拆出 probe 实现以避免 common 层导入 infrastructure。
  - Preserve: Pydantic schema 形状稳定，`/health` 不依赖外部服务。

- `apps/api/routes/health.py`
  - Current state: `/health` 和 `/ready` 返回统一 envelope，不要求 AuthContext。
  - Story change: `/ready` 可改 async，调用 readiness service。
  - Preserve: response model、request_id echo、public endpoint，不在 route 中写连接细节。

- `apps/api/main.py`
  - Current state: 创建 FastAPI app，注册 logging middleware、error handlers、health router。
  - Story change: 一般不需要修改；不要添加 startup migration 或外部服务连接。
  - Preserve: app import without external services。

- `apps/worker/main.py`
  - Current state: worker placeholder。
  - Story change: 建立最小 RQ worker entrypoint 或 worker config CLI，支持独立 queue name。
  - Preserve: 不实现 ingestion/embedding 业务 job，不读取任意文件路径。

- `migrations/env.py` 和 `migrations/versions/20260527_0001_governance.py`
  - Current state: Story 1.5 的 Alembic migration 基线。
  - Story change: Compose migration service 运行这些 migration。
  - Preserve: migration 是 schema 真相；不要在 Compose 或 app 启动里用 `create_all()` 代替。

- `tests/unit/test_architecture_boundaries.py`
  - Current state: 禁止 `packages/common` 导入 FastAPI/SQLAlchemy/Redis/MinIO/httpx 等 infrastructure/framework。
  - Story change: 如果新增 readiness probes，测试必须继续保护 domain/common 边界，或只为明确的新 infrastructure 模块加白名单。
  - Preserve: route declarations 只在 `apps/api/routes`。

### Previous Story Intelligence

- Story 1.5 已落地 `DATABASE_URL` 配置、Alembic、SQLAlchemy storage base/session、auth/audit repositories 和 SQLite migration smoke；本 Story 应把真实 PostgreSQL 验证迁移到 Docker Compose，而不是重复创建 storage 层。
- Story 1.5 的 Dev Record 说明 `uv run alembic upgrade head` 曾用临时 SQLite smoke 通过，PostgreSQL runtime verification 明确留给 Docker Compose story。
- Story 1.5 保持 `packages/common` 不导入 SQLAlchemy；本 Story 的 readiness 探针也要避免把 Redis/MinIO/SQLAlchemy 客户端塞进 common。
- Story 1.4 已提供结构化 request logging 和 redaction；readiness dependency 日志必须复用既有 structured logging/redaction 模式，不要输出连接串、secret、API key 或完整 URL。
- Story 1.3/1.4 已保证 `/health`、`/ready` 是 public system endpoints；本 Story 不应给它们加 AuthContext 要求。
- 前序验证命令最后通过：`uv run pytest`、`uv run ruff check .`、`uv run mypy apps packages tests`。本 Story 完成时必须保持三项通过。

### File Structure Guidance

建议本 Story 最小落地文件集：

```text
.dockerignore                                  # NEW: Docker build context excludes
docker/compose.yaml                            # NEW: local service graph
docker/Dockerfile.api                          # NEW: API image
docker/Dockerfile.worker                       # NEW: worker image or shared base
docker/README.md                               # UPDATE: local dependency stack docs
docker/postgres/init.sql                       # NEW optional: future pgvector extension hook only if image supports it
packages/common/config.py                      # UPDATE: Redis/MinIO/queue/readiness settings
packages/common/health.py                      # UPDATE: schema/helpers only if boundary-safe
packages/data/readiness.py                     # NEW optional: DB/Redis/MinIO probes outside common
packages/data/queue/__init__.py                # NEW optional
packages/data/queue/contracts.py               # NEW optional: JSON payload DTO/validator
packages/data/queue/rq_worker.py               # NEW optional: RQ worker factory with JSON serializer
apps/api/routes/health.py                      # UPDATE: async readiness call if needed
apps/worker/main.py                            # UPDATE: worker entrypoint/config
tests/unit/common/test_config.py               # UPDATE
tests/unit/common/test_health.py               # UPDATE or split DTO-only tests
tests/unit/data/test_readiness.py              # NEW optional
tests/unit/data/test_queue_contracts.py        # NEW optional
tests/integration/api/test_health_routes.py    # UPDATE
tests/integration/docker/test_compose_config.py # NEW optional skip when Docker unavailable
README.md                                      # UPDATE
docs/operations/local-development.md           # UPDATE
```

If implementation chooses a different layout, it must still preserve:

- common/domain 层不导入 infrastructure SDK；
- API route 只编排，不直接实现 dependency probe；
- worker queue 配置和 payload contract 可单元测试；
- Compose service 名称与 AC 完全一致；
- migration 由 Alembic 执行。

### Implementation Boundaries

- 不要实现文档上传、parser、chunk、embedding job、VectorStore、retrieval、RAG generation、Agent 或 Tool Registry。
- 不要新增 documents/chunks/embedding_jobs/retrieval_logs/chat/agent/tool_call 表。
- 不要引入 OpenSearch、Milvus、Prometheus 或 Grafana 作为必需服务。
- 不要把 MinIO SDK 调用、Redis 连接、RQ worker 或 SQLAlchemy engine 放入 `packages/common`，除非同步更新架构边界并有充分理由；更推荐放在 infrastructure/data 模块。
- 不要让 API 容器在 app startup 自动创建数据库 schema；migration service 是唯一 schema 初始化入口。
- 不要把 queue payload 设计成任意 Python object。RQ 默认 serializer 的安全风险必须被显式规避。
- 不要在 health/readiness response 或日志中输出 `DATABASE_URL`、`REDIS_URL`、MinIO credentials、Authorization、API key、JWT、企业文档内容或本地绝对路径。

### Suggested Contracts

Readiness probe contract 示例：

```python
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ProbeResult:
    name: str
    configured: bool
    ok: bool
    latency_ms: float | None
    error_code: str | None = None


class DependencyProbe(Protocol):
    async def check(self) -> ProbeResult:
        ...
```

Queue payload contract 示例：

```python
from pydantic import BaseModel, ConfigDict

JsonValue = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]


class QueuePayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    tenant_id: str
    user_id: str
    job_type: str
    resource_id: str
    parameters: dict[str, JsonValue]
```

Worker config 示例：

```python
class WorkerSettings(BaseModel):
    redis_url: str
    queue_name: str
    burst: bool = False
```

### Testing Requirements

- Config tests must cover default unconfigured state and configured Compose-like env values.
- Readiness tests should use fake probes or monkeypatch clients. Do not require a real PostgreSQL/Redis/MinIO service for unit tests.
- API integration tests must prove `/health` still succeeds without external services.
- `/ready` tests must cover:
  - all unconfigured dependencies are reported without failing import;
  - configured dependency failure sets `ready=false`;
  - failure response includes dependency names/status/error_code but not URLs or secrets;
  - structured readiness log includes dependency summary.
- Queue tests must prove non-JSON payload values are rejected, including object instances, SQLAlchemy models, file handles, bytes where not explicitly allowed, and local file paths if represented as sensitive payload content.
- Compose tests should validate service names, healthchecks, `depends_on`, environment references and queue names. If using Docker CLI in tests, skip cleanly when Docker is unavailable.
- Optional manual smoke should record:
  - `docker compose -f docker/compose.yaml config` passes;
  - migration service exits successfully;
  - `GET /health` returns status ok;
  - `GET /ready` returns ready true after dependencies are healthy, or clear dependency failure if a service is stopped.

### Latest Technical Information

- Docker Compose 官方文档说明 `depends_on` controls startup order, and health-aware startup should use service health conditions. Story implementation should use `service_healthy` for PostgreSQL/Redis/MinIO before migration/API/worker where supported.[Source: https://docs.docker.com/compose/how-tos/startup-order/]
- Docker Compose service reference supports `healthcheck` and long-form `depends_on` conditions. Do not rely on container start order alone as readiness.[Source: https://docs.docker.com/reference/compose-file/services/#healthcheck]
- uv 官方 Docker guide recommends copying lock files first and using Docker layer cache/cache mounts for faster deterministic builds. API/worker Dockerfiles should preserve `uv.lock` based installs.[Source: https://docs.astral.sh/uv/guides/integration/docker/]
- PostgreSQL `pg_isready` is the correct lightweight readiness command for Postgres healthchecks; use it for the database container rather than opening application migrations as a healthcheck.[Source: https://www.postgresql.org/docs/current/app-pg-isready.html]
- RQ documentation supports named queues and serializer configuration. Because architecture identified default pickle serializer risk, worker and queue factories must use JSON serializer or an equivalent safe serialization policy for untrusted payload boundaries.[Source: https://python-rq.org/docs/workers/]
- MinIO exposes health probe endpoints such as liveness/readiness under `/minio/health/*`. Validate the chosen container image contains the probe tool used by Compose healthcheck; do not assume `curl` exists in the image.[Source: https://min.io/docs/minio/linux/operations/monitoring/healthcheck-probe.html]
- Architecture verified on 2026-05-26 pins MVP baseline decisions around PostgreSQL 18 series, pgvector 0.8.x, SQLAlchemy 2.x, Alembic 1.x, FastAPI 0.136.x, Pydantic v2 and RQ 2.9.0. This Story should not widen those versions without an explicit dependency update and tests.[Source: `_bmad-output/planning-artifacts/architecture.md#Version Decisions Verified on 2026-05-26`]

### UX / Frontend Notes

- 本 Story 不新增前端 UI。
- `/ready` response 和日志会被后续 diagnostics/admin UI 复用，字段要结构化、稳定、可脱敏。
- 如果将来前端展示 readiness，普通用户只应看到服务状态和 request_id；管理员可看 dependency name/status/latency/error_code，但仍不得看到连接串或 secret。

### References

- `_bmad-output/planning-artifacts/epics.md#Story 1.6`
- `_bmad-output/planning-artifacts/architecture.md#Infrastructure & Deployment`
- `_bmad-output/planning-artifacts/architecture.md#Project Structure`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-31`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-32`
- `_bmad-output/planning-artifacts/implementation-readiness-report-2026-05-27.md#Requirements Coverage`
- `_bmad-output/implementation-artifacts/1-5-最小数据库迁移与基础治理表.md#Previous Story Intelligence`
- `docker/README.md`
- `.env.example`
- `packages/common/config.py`
- `packages/common/health.py`
- `apps/api/routes/health.py`
- `apps/api/main.py`
- `apps/worker/main.py`
- `migrations/env.py`
- `tests/unit/test_architecture_boundaries.py`
- `https://docs.docker.com/compose/how-tos/startup-order/`
- `https://docs.docker.com/reference/compose-file/services/#healthcheck`
- `https://docs.astral.sh/uv/guides/integration/docker/`
- `https://www.postgresql.org/docs/current/app-pg-isready.html`
- `https://python-rq.org/docs/workers/`
- `https://min.io/docs/minio/linux/operations/monitoring/healthcheck-probe.html`

## Validation Checklist

Validation Result: PASS（2026-05-27T12:36:18+08:00）

- [x] Story 明确了用户角色、目标和收益。
- [x] Acceptance Criteria 来自 Epic Story 1.6，覆盖 Compose 核心服务、readiness 依赖状态、worker queue 隔离和 JSON payload 约束。
- [x] Tasks 覆盖 AC 1 到 AC 3，并标注 AC 映射。
- [x] Dev Notes 包含当前代码状态、架构边界、上一条 Story 经验、推荐文件位置、测试要求和实现边界。
- [x] 明确要求复用现有 `AppSettings`、health schema、统一 envelope、request logging、Alembic migration 和架构边界测试。
- [x] 明确禁止 app startup migration、`create_all()`、common/domain 层引入 infrastructure SDK、真实 secret 入库/入日志和任意 Python object queue payload。
- [x] 包含 Docker Compose health/depends_on、uv Docker、PostgreSQL `pg_isready`、RQ serializer/queue、MinIO health probe 的最新技术参考。
- [x] File Structure Guidance 指向现有代码可安全扩展的位置，避免 route 肥大、worker 范围膨胀和 readiness 泄密。

## Change Log

- 2026-05-27: Created comprehensive Story 1.6 developer context for Docker Compose local dependencies, live readiness probes, worker queue isolation, migration startup and safety boundaries.
- 2026-05-27: Implemented Docker Compose local dependency stack, live readiness probes, worker queue isolation, JSON payload contract, tests and local development documentation.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- RED: `uv run pytest tests/unit/common/test_config.py tests/unit/data/test_readiness.py tests/unit/data/test_queue_contracts.py tests/integration/api/test_health_routes.py tests/integration/docker/test_compose_config.py` failed during collection because `packages.data.readiness` and `packages.data.queue` did not exist yet.
- GREEN: Story-specific validation passed: 26 tests passed across config, readiness, queue, API health routes and Compose config.
- Full regression: `uv run pytest` passed with 114 tests.
- Static checks: `uv run ruff check .` passed.
- Type checks: `uv run mypy apps packages tests` passed with no issues in 64 source files.
- Compose config: `docker compose -f docker/compose.yaml config --quiet` passed with local placeholder env values.
- Container smoke limitation: `docker info --format '{{.ServerVersion}}'` failed because Docker Desktop daemon pipe was unavailable; `up -d --build` and live `/health` `/ready` container calls were not runnable in this environment.

### Completion Notes List

- 新增 `docker/compose.yaml`，定义 `api`、`worker-ingestion`、`worker-embedding`、`migration`、`postgres`、`redis`、`minio`，并使用 health-aware `depends_on` 和容器级 healthcheck。
- 新增 API/worker Dockerfile，基于 Python 3.11 uv 镜像执行 `uv sync --frozen --no-dev`；API 运行 `apps.api.main:app`，migration 仅通过 Alembic 执行 schema 初始化。
- 扩展 `AppSettings` 与 `.env.example`，覆盖 PostgreSQL、Redis、MinIO、worker queue 和 readiness timeout；Compose 内部使用 `postgres`、`redis`、`minio` 服务名 DNS。
- 将真实 readiness probe 放在 `packages.data.readiness`，保留 `packages.common` 的 DTO/纯函数边界；`/ready` 只调用 helper，不在 route 内实现 DB/Redis/MinIO 探针。
- `/ready` 对已配置依赖失败返回 `ready=false` 和结构化 dependency 状态，并写入 `api.readiness.checked`，日志不包含 URL、密码、token、secret 或企业内容。
- 新增 RQ worker 配置与 JSON serializer 工厂，`worker-ingestion` 与 `worker-embedding` 使用不同 queue name；新增 `QueuePayload` validator，拒绝非 JSON、prompt/token/API key、本机绝对路径等 payload。
- 新增和更新测试覆盖配置读取、readiness 状态/日志、API failure response、worker queue 隔离、payload 限制、Compose 服务/healthcheck/depends_on。
- 更新 README、`docs/operations/local-development.md` 和 `docker/README.md`，说明 `.env` 准备、Compose 启动、migration、health/readiness、worker 队列、关闭清理和 secret 不泄露规则。

### File List

- `.dockerignore`
- `.env.example`
- `README.md`
- `apps/api/routes/health.py`
- `apps/worker/main.py`
- `docker/Dockerfile.api`
- `docker/Dockerfile.worker`
- `docker/README.md`
- `docker/compose.yaml`
- `docs/operations/local-development.md`
- `packages/common/config.py`
- `packages/common/health.py`
- `packages/common/logging.py`
- `packages/data/queue/__init__.py`
- `packages/data/queue/contracts.py`
- `packages/data/queue/rq_worker.py`
- `packages/data/readiness.py`
- `tests/integration/api/test_health_routes.py`
- `tests/integration/docker/test_compose_config.py`
- `tests/unit/common/test_config.py`
- `tests/unit/data/test_queue_contracts.py`
- `tests/unit/data/test_readiness.py`
- `_bmad-output/implementation-artifacts/1-6-docker-compose-本地启动核心依赖.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

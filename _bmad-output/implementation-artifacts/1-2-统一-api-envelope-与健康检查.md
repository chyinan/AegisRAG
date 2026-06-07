---
baseline_commit: NO_VCS
---

# Story 1.2: 统一 API Envelope 与健康检查

Status: done

生成时间：2026-05-27T08:26:56+08:00

## Story

As a API 调用方,
I want 所有非流式 API 使用统一响应结构,
so that 前端、Open WebUI adapter 和测试都能稳定解析成功与错误响应。

## Acceptance Criteria

1. **Health 使用统一响应结构**
   - Given API 服务已启动
   - When 调用 `GET /health`
   - Then 返回统一 envelope，包含 `request_id`、`data`、`error`、`metadata`
   - And `error` 在成功响应中为 `null`

2. **Readiness 返回结构化依赖摘要**
   - Given API 服务已启动
   - When 调用 `GET /ready`
   - Then 返回 readiness 状态和依赖摘要
   - And 未就绪依赖必须以结构化数据表达，不依赖日志文本解析

3. **Pydantic v2 schema 与薄 route 边界**
   - Given 任意 route 返回业务数据
   - When 响应被序列化
   - Then 必须使用 Pydantic v2 schema
   - And route 不直接执行复杂业务逻辑

## Tasks / Subtasks

- [x] 定义统一 API envelope schema 和构造 helper（AC: 1, 3）
  - [x] 在 `packages/common/envelope.py` 新增 Pydantic v2 schema：`ApiError`、`ResponseMetadata`、`ApiResponse[T]`。
  - [x] `ApiResponse[T]` 必须包含 `request_id: str`、`data: T | None`、`error: ApiError | None`、`metadata: ResponseMetadata`。
  - [x] 提供纯 Python helper（例如 `success_response`、`error_response`），helper 只接收显式 `request_id`、`data`、`metadata`，不得导入 FastAPI 或读取全局请求状态。
  - [x] `ResponseMetadata` 至少支持 `latency_ms: float | None = None`，并允许后续扩展 trace/model/token/retrieval metadata；本 Story 不实现完整 request tracing。

- [x] 定义 health/readiness 数据 schema 与轻量 service（AC: 1, 2, 3）
  - [x] 在 `packages/common/health.py` 定义 `HealthData`、`DependencyStatus`、`ReadinessData` 等 Pydantic v2 schema 或 dataclass/Pydantic DTO。
  - [x] 提供 `get_health_data()` 和 `get_readiness_data()` 等无外部 I/O 的函数，便于单测；这些函数不得访问数据库、Redis、MinIO、LLM、embedding provider 或向量库。
  - [x] 当前 Story 的 readiness 只表达 API 进程可用性和未来依赖占位：`database`、`redis`、`minio`、`vector_store` 可返回 `status="not_configured"` 或等价结构化状态，且标明 `required=false` 或 `blocking=false`。
  - [x] 不要在本 Story 实现真实依赖探活；数据库、Redis、MinIO 和 Docker Compose readiness 在 Story 1.5、1.6 之后再接入。

- [x] 新增 `GET /health` 和 `GET /ready` route（AC: 1, 2, 3）
  - [x] 新建 `apps/api/routes/health.py`，使用 `APIRouter(tags=["system"])` 定义 `GET /health` 和 `GET /ready`。
  - [x] route 必须声明 `response_model=ApiResponse[HealthData]` / `ApiResponse[ReadinessData]` 或等价 Pydantic v2 泛型响应模型。
  - [x] route 只做 HTTP 层工作：读取或生成 `request_id`、调用 common health 函数、封装 envelope；不得直接做复杂业务逻辑或外部探活。
  - [x] `request_id` 优先使用请求头 `X-Request-ID`；缺失时生成新的 UUID 字符串。后续 Story 1.3 会把这段逻辑收敛到 `RequestContext` dependency。
  - [x] 更新 `apps/api/main.py` 的 `create_app()`，注册 health router，并保留当前 `create_app() -> FastAPI` 模式。

- [x] 增加单元测试和 API 集成测试（AC: 1, 2, 3）
  - [x] 新增 `tests/unit/common/test_envelope.py`，覆盖成功 envelope、错误 envelope、metadata 默认值和 Pydantic 序列化。
  - [x] 新增 `tests/unit/common/test_health.py`，覆盖 health/readiness DTO 和依赖摘要结构，不访问外部服务。
  - [x] 新增 `tests/integration/api/test_health_routes.py`，使用 FastAPI `TestClient` 调用 `/health` 和 `/ready`。
  - [x] `/health` 测试必须断言：HTTP 200、envelope 顶层键完整、`error is None`、`data.status == "ok"`、响应 `request_id` 可回显 `X-Request-ID`。
  - [x] `/ready` 测试必须断言：HTTP 200、返回结构化 `dependencies` 列表或映射、每个依赖包含 `name`、`status`、`required/blocking`、`details/message` 中的必要字段。
  - [x] 保留并通过既有架构边界测试，确保 FastAPI route 只出现在 `apps/api/routes` 或 `apps/api/main.py` 注册入口。

- [x] 更新文档与验证命令（AC: 1, 2, 3）
  - [x] 更新 `README.md`，说明 `GET /health`、`GET /ready` 和统一 envelope 的最小响应形状。
  - [x] 更新 `docs/operations/local-development.md`，补充本地验证命令和当前 readiness 不做真实外部依赖探活的边界。
  - [x] 运行 `uv run pytest`。
  - [x] 运行 `uv run ruff check .`。
  - [x] 运行 `uv run mypy apps packages tests`。
  - [x] 若任一命令未执行或失败，必须在 Dev Agent Record 记录原因，不得把未通过的验证描述为通过。

## Dev Notes

### 当前仓库状态

- 当前代码来自 Story 1.1，已经存在最小 `uv + FastAPI` monorepo 骨架。
- `apps/api/main.py` 当前只导出 `create_app()` 和 `app`，未注册任何 router。
- `apps/api/routes/__init__.py` 只是 route 模块占位。
- `packages/common`、`packages/auth`、`packages/data` 目前只有 `__init__.py`，没有业务 DTO、context、errors 或 service。
- `tests/unit/test_app_smoke.py` 只验证 `apps.api.main.app` 可导入且是 FastAPI 实例。
- `tests/unit/test_architecture_boundaries.py` 已经建立两个关键守卫：domain 层不得导入基础设施/框架，FastAPI route 只能在 `apps/api/routes` 或 `apps/api/main.py` 注册入口出现。
- 当前目录不是 git repository；无法使用 commit 历史作为实现模式来源。

### Source Context

- Epic 1 的目标是让平台负责人可以本地启动系统，获得统一 API 契约、认证上下文、结构化错误、审计日志、健康检查和基础可观测能力，为后续 ingestion、retrieval、RAG 和 Agent 提供安全底座。[Source: `_bmad-output/planning-artifacts/epics.md#Epic 1`]
- Story 1.2 覆盖 FR18 和 FR31：所有非流式 API 需要统一 `request_id/data/error/metadata` 响应结构，并提供 `GET /health`、`GET /ready` 作为 Docker、本地开发和生产 readiness 的基础。[Source: `_bmad-output/planning-artifacts/epics.md#Story 1.2`; `_bmad-output/planning-artifacts/architecture.md#API & Communication Patterns`]
- PRD 要求所有核心 API 返回统一 data/error/metadata 结构，route 层不得直接调用 LLM、向量数据库或复杂业务逻辑。[Source: `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-18`]
- PRD 要求 MVP 至少暴露 health/readiness 和关键 latency 指标；本 Story 先建立 health/readiness 和 envelope，latency 指标与 structured logging 会在 Story 1.4 继续深化。[Source: `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-31`]

### Architecture Requirements

- API response envelope 的目标形状是：

```json
{
  "request_id": "...",
  "data": {},
  "error": null,
  "metadata": {
    "latency_ms": 123
  }
}
```

- Error envelope 的目标形状是：

```json
{
  "request_id": "...",
  "data": null,
  "error": {
    "code": "AUTH_CONTEXT_REQUIRED",
    "message": "Authentication context is required.",
    "details": {}
  },
  "metadata": {
    "latency_ms": 12
  }
}
```

- `apps/api/routes/*` 只拥有 HTTP contract；route 调 application/common service，不直接调用 infrastructure adapter。[Source: `_bmad-output/planning-artifacts/architecture.md#Architectural Boundaries`]
- `packages/*/domain` 不得依赖 FastAPI、SQLAlchemy、Redis、MinIO 或外部 SDK。本 Story 新增的 `packages/common/envelope.py` 和 `packages/common/health.py` 也应保持纯 Python/Pydantic，不导入 FastAPI。[Source: `_bmad-output/planning-artifacts/architecture.md#Structure Patterns`]
- API schema 使用 Pydantic v2；不要引入 `SQLModel` 或 FastAPI Full Stack Template 风格的数据模型。[Source: `_bmad-output/planning-artifacts/architecture.md#Starter Template Evaluation`]

### Previous Story Intelligence

- Story 1.1 已经把 `[tool.uv] package = false` 写入 `pyproject.toml`，原因是在中文路径下 editable install `.pth` 曾触发 GBK 解码失败。Story 1.2 不需要修改该配置。
- Story 1.1 的测试命令已通过：`uv sync`、`uv lock --check`、`uv run pytest`、`uv run ruff check .`、`uv run mypy apps packages tests`。
- Story 1.1 明确没有实现 health/readiness、统一 envelope、错误处理中间件、认证依赖、数据库、Docker Compose 或任何 RAG/Agent 业务逻辑；Story 1.2 只补 envelope 和 health/readiness，不应抢 Story 1.3 到 1.6 的范围。
- 现有 route boundary 测试允许 route 声明放在 `apps/api/routes/*`，并禁止 route 散落到 worker 或 packages；新增 health route 应放在 `apps/api/routes/health.py`。

### File Structure Guidance

建议本 Story 最小落地文件集：

```text
apps/api/main.py                         # UPDATE: 注册 health router，保留 create_app 模式
apps/api/routes/__init__.py              # UPDATE: 可导出 health router，或保持模块说明
apps/api/routes/health.py                # NEW: GET /health, GET /ready
packages/common/envelope.py              # NEW: ApiResponse / ApiError / ResponseMetadata / helpers
packages/common/health.py                # NEW: health/readiness DTO 和无 I/O service 函数
tests/unit/common/test_envelope.py       # NEW
tests/unit/common/test_health.py         # NEW
tests/integration/api/test_health_routes.py # NEW
README.md                                # UPDATE
docs/operations/local-development.md     # UPDATE
```

不要新增运行时依赖；当前 `pyproject.toml` 已包含 FastAPI、Pydantic v2、pytest、ruff 和 mypy。

### Implementation Boundaries

- 不要实现 `RequestContext` 或 `AuthContext` 注入；这是 Story 1.3。
- 不要实现全局异常处理、中间件、审计日志或 structured logging；这是 Story 1.4。
- 不要实现数据库连接、Alembic migration、真实 dependency probing 或基础治理表；这是 Story 1.5。
- 不要实现 Docker Compose healthcheck 和核心依赖启动；这是 Story 1.6。
- 不要实现 upload、retrieve、query、chat、sources、agent endpoint。
- 不要让 route 直接访问数据库、Redis、MinIO、LLM、embedding provider、vector store 或任意外部网络。
- 不要硬编码真实 `tenant_id`、`user_id`、API key、数据库地址或文件绝对路径。

### Health / Readiness Contract

- `GET /health` 表示 API 进程存活，建议响应：

```json
{
  "request_id": "req-123",
  "data": {
    "status": "ok",
    "service": "api",
    "version": "0.1.0"
  },
  "error": null,
  "metadata": {
    "latency_ms": null
  }
}
```

- `GET /ready` 表示当前进程是否可接收基础请求。由于真实外部依赖尚未接入，本 Story 建议把未来依赖列为非阻塞占位：

```json
{
  "request_id": "req-123",
  "data": {
    "ready": true,
    "dependencies": [
      {
        "name": "database",
        "status": "not_configured",
        "required": false,
        "message": "Database readiness is introduced in Story 1.5."
      }
    ]
  },
  "error": null,
  "metadata": {
    "latency_ms": null
  }
}
```

- 如果实现者选择 `dependencies` 为映射而不是列表，必须在 schema 和测试中保持稳定结构，并保留 `name/status/required/message` 等等价字段。
- 后续 Story 可以把 required dependencies 改为阻塞并返回 503，但本 Story 不要求真实外部服务运行。

### Testing Requirements

- 单测和集成测试默认不得访问网络、数据库、Redis、MinIO、向量库、真实 LLM 或 embedding provider。
- `TestClient` 测试要覆盖无 `X-Request-ID` 时生成 UUID，以及有 `X-Request-ID` 时原样回显。
- Envelope helper 单测要覆盖 `data` 和 `error` 互斥的期望。如果 helper 不强制互斥，测试至少要覆盖成功响应 `error is None`、错误响应 `data is None`。
- OpenAPI smoke 可选，但建议断言 `/health` 和 `/ready` 出现在 `app.openapi()["paths"]` 中。
- 所有新增类型必须通过 mypy strict；Pydantic generic 类型不要使用未参数化的 `Any` 逃避类型检查。

### Latest Technical Information

- 2026-05-27 通过 PyPI JSON 复核：FastAPI 当前版本信号为 `0.136.3`，Pydantic 当前版本信号为 `2.13.4`；当前 `pyproject.toml` 已锁定兼容范围 `fastapi[standard]>=0.136.3,<0.137`、`pydantic>=2.13.4,<3`，本 Story 不需要升级依赖。
- FastAPI 官方文档支持使用 return type 或 `response_model` 对响应数据做校验、过滤和 OpenAPI schema 生成；本 Story 应显式使用 `response_model` 保持 API contract 稳定。

### UX / Frontend Notes

- UX 文档要求前端永远只展示后端确认过的状态，不判断权限、不补造引用、不推断检索结果。统一 envelope 是后续 Open WebUI adapter、Source Inspector、Knowledge Admin 和 Diagnostics 稳定解析状态的基础。[Source: `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md#Business Fine-Tuning Rules`]
- Health/readiness 属于运维和诊断基础，不需要任何自定义前端 UI。

### References

- `_bmad-output/planning-artifacts/epics.md#Story 1.2`
- `_bmad-output/planning-artifacts/architecture.md#API & Communication Patterns`
- `_bmad-output/planning-artifacts/architecture.md#Structure Patterns`
- `_bmad-output/planning-artifacts/architecture.md#Architectural Boundaries`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-18`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-31`
- `_bmad-output/implementation-artifacts/1-1-set-up-initial-project-from-starter-template-初始化生产级-fastapi-monorepo.md#Dev Notes`
- `https://pypi.org/project/fastapi/`
- `https://pypi.org/project/pydantic/`
- `https://fastapi.tiangolo.com/tutorial/response-model/`

## Validation Checklist

Validation Result: PASS（2026-05-27T08:26:56+08:00）

- [x] Story 明确了用户角色、目标和收益。
- [x] Acceptance Criteria 来自 Epic Story 1.2，覆盖 `/health`、`/ready`、统一 envelope 和 Pydantic schema。
- [x] Tasks 覆盖 AC 1 到 AC 3，并标注 AC 映射。
- [x] Dev Notes 包含当前代码状态、架构边界、上一条 Story 经验、推荐文件位置、测试要求和实现边界。
- [x] 明确说明本 Story 不实现 RequestContext/AuthContext、全局异常处理、真实依赖探活、数据库迁移或 Docker Compose。
- [x] 明确禁止真实外部 LLM、embedding provider、向量数据库、数据库、Redis、MinIO 或网络调用。
- [x] 包含最新 FastAPI/Pydantic 版本复核结果和响应模型实现约束。
- [x] File Structure Guidance 指向现有代码可安全扩展的位置，避免 route 或 common 层越界。

## Change Log

- 2026-05-27: Created comprehensive Story 1.2 developer context for unified API envelope and health/readiness endpoints.
- 2026-05-27: Implemented unified API envelope, health/readiness endpoints, focused tests, and local documentation.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex / Amelia (`bmad-dev-story`)

### Debug Log References

- `uv run pytest tests/unit/common/test_envelope.py` RED：按预期失败，`packages.common.envelope` 模块尚不存在。
- `uv run pytest tests/unit/common/test_envelope.py`：通过，3 passed。
- `uv run pytest`：通过，8 passed。
- `uv run pytest tests/unit/common/test_health.py` RED：按预期失败，`packages.common.health` 模块尚不存在。
- `uv run pytest tests/unit/common/test_health.py`：通过，3 passed。
- `uv run pytest`：通过，11 passed。
- `uv run pytest tests/integration/api/test_health_routes.py` RED：按预期失败，`/health` 和 `/ready` 尚未注册，返回 404。
- `uv run pytest tests/integration/api/test_health_routes.py`：通过，4 passed。
- `uv run pytest`：通过，15 passed。
- `uv run pytest`：通过，15 passed。
- `uv run ruff check .`：初次失败，`packages/common/health.py` 一行超过 100 字符；已调整换行。
- `uv run ruff check .`：通过。
- `uv run mypy apps packages tests`：通过，18 source files。
- `rg -n "\[ \]" _bmad-output/implementation-artifacts/1-2-统一-api-envelope-与健康检查.md`：无未完成任务输出。
- 最终 `uv run pytest`：通过，15 passed。
- 最终 `uv run ruff check .`：通过。
- 最终 `uv run mypy apps packages tests`：通过，18 source files。

### Completion Notes List

- 已实现 `packages/common/envelope.py`，包含 Pydantic v2 `ApiError`、`ResponseMetadata`、泛型 `ApiResponse[T]` 以及纯 helper `success_response` / `error_response`。
- Envelope helper 不导入 FastAPI，不读取全局请求状态，调用方必须显式传入 `request_id`。
- 已实现 `packages/common/health.py`，包含 `HealthData`、`DependencyStatus`、`ReadinessData` 和无外部 I/O 的 health/readiness 构造函数。
- Readiness 当前只返回非阻塞 `not_configured` 依赖摘要，不访问数据库、Redis、MinIO 或向量库。
- 已实现 `GET /health` 和 `GET /ready`，使用 `ApiResponse[HealthData]` / `ApiResponse[ReadinessData]` response_model，成功响应保持统一 envelope。
- Health route 当前只处理 request_id、调用 common health 函数和响应封装；没有接入真实依赖探活或复杂业务逻辑。
- 已补充 README 和本地开发文档，说明统一 envelope、health/readiness 端点，以及 readiness 当前不做真实外部依赖探活的边界。
- 验证命令 `uv run pytest`、`uv run ruff check .`、`uv run mypy apps packages tests` 均已通过。

### File List

- `apps/api/main.py`
- `apps/api/routes/health.py`
- `README.md`
- `docs/operations/local-development.md`
- `packages/common/envelope.py`
- `packages/common/health.py`
- `tests/integration/api/test_health_routes.py`
- `tests/unit/common/test_envelope.py`
- `tests/unit/common/test_health.py`
- `_bmad-output/implementation-artifacts/1-2-统一-api-envelope-与健康检查.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

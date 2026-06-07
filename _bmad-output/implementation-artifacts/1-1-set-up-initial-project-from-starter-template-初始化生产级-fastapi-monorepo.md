---
baseline_commit: NO_VCS
---

# Story 1.1: Set up initial project from starter template（初始化生产级 FastAPI Monorepo）

Status: done

生成时间：2026-05-27T01:52:16+08:00

## Story

As a 平台工程师,
I want 一个符合架构规则的 `uv + FastAPI` monorepo 骨架,
so that 后续 ingestion、retrieval、RAG 和 Agent 能按清晰边界开发。

## Acceptance Criteria

1. **目录与工程骨架**
   - Given 一个新仓库
   - When 开发者初始化项目
   - Then 必须创建 `apps/api`、`apps/worker`、`packages/common`、`packages/auth`、`packages/data`、`tests/unit`、`tests/integration`、`docs`、`docker`
   - And 根目录包含 `pyproject.toml`、`.env.example`、pytest 配置和 ruff 配置

2. **基础测试链路**
   - Given 项目依赖已同步
   - When 开发者执行 `uv run pytest`
   - Then 至少一个基础 smoke test 通过
   - And 测试不得调用真实外部 LLM、embedding provider 或向量数据库

3. **架构边界守卫**
   - Given 后续开发者添加业务模块
   - When import 关系被检查
   - Then `packages/*/domain` 不依赖 FastAPI、SQLAlchemy、Redis、MinIO 或外部 SDK
   - And route 代码只能位于 `apps/api/routes`

## Tasks / Subtasks

- [x] 初始化 `uv` Python package 项目与根配置（AC: 1, 2）
  - [x] 使用 `uv init --package .` 或等价手工方式建立根 `pyproject.toml`、`.python-version` 和 package metadata；Python 基线为 3.11+。
  - [x] 加入运行时依赖：`fastapi[standard]`、`pydantic` v2、`pydantic-settings`、`sqlalchemy` 2.x、`alembic` 1.x、`asyncpg`、`psycopg[binary]`、`redis`、`rq`、`httpx`、`structlog`、`python-multipart`。
  - [x] 加入开发依赖：`pytest`、`pytest-asyncio`、`ruff`、`mypy`。
  - [x] 在 `pyproject.toml` 内配置 pytest、ruff 和 mypy；`pytest` 的默认路径至少包含 `tests/unit` 和 `tests/integration`。
  - [x] 生成并提交 `uv.lock`；不要把 lockfile 留给后续 Story。

- [x] 创建后端 monorepo 目录和最小 importable 模块（AC: 1, 3）
  - [x] 创建 `apps/api/`，至少包含 `__init__.py`、`main.py`、`routes/`。
  - [x] 创建 `apps/worker/`，至少包含 `__init__.py`、`main.py`；本 Story 只放可导入占位，不实现实际 ingestion/embedding job。
  - [x] 创建 `packages/common/`、`packages/auth/`、`packages/data/`，均包含 `__init__.py`；本 Story 不提前实现 `RequestContext`、`AuthContext`、RBAC、SQLAlchemy model 或 Alembic migration，这些属于 Story 1.3、1.4、1.5。
  - [x] 创建 `tests/unit/`、`tests/integration/`、`docs/`、`docker/`；需要保留空目录时使用 `.gitkeep` 或 `README.md`，不要用隐藏全局状态。

- [x] 建立最小 FastAPI API 入口（AC: 1, 2, 3）
  - [x] `apps/api/main.py` 导出 `app: FastAPI`，只承担应用对象创建、元数据和 router 注册入口。
  - [x] 不在本 Story 实现 `GET /health`、`GET /ready`、统一 envelope、错误处理中间件或认证依赖；这些分别属于 Story 1.2 到 1.4。
  - [x] route 定义只能放在 `apps/api/routes/`；本 Story 可以没有业务 route。

- [x] 增加 smoke test 与架构边界测试（AC: 2, 3）
  - [x] `tests/unit/test_app_smoke.py` 导入 `apps.api.main.app`，断言 `FastAPI` 应用可实例化且不会触发外部服务调用。
  - [x] `tests/unit/test_architecture_boundaries.py` 使用 `ast` 扫描仓库内 Python 文件，禁止 `packages/*/domain*.py` 或 `packages/*/domain/**` 导入 `fastapi`、`sqlalchemy`、`redis`、`minio`、`boto3`、`httpx`、LLM/embedding 厂商 SDK。
  - [x] 同一边界测试应扫描 `apps/` 下的 route 声明，确保 FastAPI route/`APIRouter` 只出现在 `apps/api/routes/` 或 `apps/api/main.py` 的注册入口，不允许散落到 worker 或 packages。
  - [x] 测试默认不得访问网络、数据库、向量库、外部 LLM 或 embedding provider。

- [x] 添加基础文档和环境示例（AC: 1, 2）
  - [x] 根 `README.md` 写明本地开发命令：`uv sync`、`uv run pytest`、`uv run ruff check .`、`uv run mypy apps packages tests`。
  - [x] `.env.example` 只列配置键和安全占位值，不包含真实密钥、真实数据库地址或固定 `tenant_id` / `user_id`。
  - [x] `docs/operations/local-development.md` 或等价文档说明当前 Story 只验证 Python 工程骨架；Docker Compose、health/readiness、数据库迁移在后续 Stories 完成。

- [x] 本地验证（AC: 2, 3）
  - [x] 运行 `uv sync`。
  - [x] 运行 `uv run pytest`。
  - [x] 运行 `uv run ruff check .`。
  - [x] 运行 `uv run mypy apps packages tests`。
  - [x] 记录任何未执行命令及原因；不能把未运行的验证描述为已通过。

### Review Findings

- [x] [Review][Patch] Domain boundary detector misses `packages/*/domain*.py` files [tests/unit/test_architecture_boundaries.py:49]
- [x] [Review][Patch] Route boundary guard is incomplete and imprecise [tests/unit/test_architecture_boundaries.py:75]
- [x] [Review][Patch] Secret-bearing `.env` variants are not ignored [`.gitignore`:1]

## Dev Notes

### 当前仓库状态

- 当前仓库是 greenfield 规划仓库，现有文件主要是 `AGENTS.md`、`PRD.md`、`project-context.md`、`docs/` 和 `_bmad-output/` 规划/执行文档。
- 当前没有 `apps/`、`packages/`、`tests/`、`docker/`、`pyproject.toml` 或实现代码；本 Story 不需要读取或修改既有业务代码。
- 当前目录不是 git repository，无法使用提交历史作为实现模式来源。
- 这是第一条 Story，没有上一条 Story 的 Dev Agent Record 或复盘经验。

### Source Context

- Epic 1 的目标是建立“可运行且可治理的平台基础”，覆盖统一 API 契约、认证上下文、结构化错误、审计日志、健康检查和基础可观测能力。Story 1.1 只负责工程骨架和质量门起点。[Source: `_bmad-output/planning-artifacts/epics.md#Epic 1`]
- Story 1.1 覆盖 FR18、FR30、FR32，但不要在本 Story 完成所有 FR 行为；后续 Story 会逐步实现 envelope、health/readiness、RequestContext/AuthContext、审计、数据库和 Docker Compose。[Source: `_bmad-output/planning-artifacts/epics.md#Story 1.1`]
- PRD Phase 0 要求建立 `apps/`、`packages/`、`tests/`、`docs/`、`docker/`，配置 pytest、ruff、基础 CI，并提供 health endpoint 和 Docker Compose 最小启动；health 与 Compose 分别在后续 Story 中完成。[Source: `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#Phase 0`]

### Architecture Requirements

- 选定 starter 是 `Custom uv + FastAPI Monorepo`，不是 FastAPI Full Stack Template；后者可参考但不得直接采用，因为 SQLModel/前端优先结构不符合本项目的 `packages/*` 边界和 SQLAlchemy 2.x 目标。[Source: `_bmad-output/planning-artifacts/architecture.md#Starter Template Evaluation`]
- 初始化命令基线：

```powershell
uv init --package .
uv add "fastapi[standard]" pydantic-settings sqlalchemy alembic asyncpg psycopg[binary] redis rq httpx structlog python-multipart
uv add --dev pytest pytest-asyncio ruff mypy
```

- 目录边界必须保持：
  - `apps/*` 只放可运行进程。
  - `packages/*` 只放可导入模块。
  - `packages/*/domain` 必须是纯领域逻辑，不得导入 FastAPI、SQLAlchemy、Redis、MinIO、httpx 或外部 SDK。
  - `apps/api/routes/*` 只拥有 HTTP contract；routes 调 application services，不直接调用 infrastructure adapter。
  - `packages/*/storage` 才放 SQLAlchemy model/repository；本 Story 不创建存储模型。
  [Source: `_bmad-output/planning-artifacts/architecture.md#Structure Patterns`; `_bmad-output/planning-artifacts/architecture.md#Architectural Boundaries`]
- 完整目标目录树在 architecture 中已有定义；本 Story 只创建 Story 1.1 AC 必需目录和轻量占位，不需要一次性创建 ingestion/retrieval/rag/agent 的全部文件，否则会造成后续 Story 边界混乱。[Source: `_bmad-output/planning-artifacts/architecture.md#Complete Project Directory Structure`]

### Version and Dependency Notes

- 2026-05-27 通过 PyPI JSON 复核：FastAPI `0.136.3`、Pydantic `2.13.4`、SQLAlchemy `2.0.50`、Alembic `1.18.4`、uv `0.11.16`、RQ `2.9.0`。使用架构文档的版本范围决策：FastAPI `0.136.x`、Pydantic v2、SQLAlchemy 2.x、Alembic 1.x、uv workspace/package manager、RQ 作为 MVP queue。
- 推荐约束策略：在 `pyproject.toml` 使用兼容范围并依赖 `uv.lock` 固化可复现版本，例如 `fastapi[standard]>=0.136.3,<0.137`、`pydantic>=2.13.4,<3`、`sqlalchemy>=2.0.50,<3`、`alembic>=1.18.4,<2`、`rq>=2.9.0,<3`。如果 `uv add` 生成更宽范围，开发者应手工收紧到架构允许范围后重新 `uv lock`。
- `RQ` 的默认 serializer 有 pickle 安全风险；本 Story 不实现队列入参，但 worker 占位和后续队列代码必须只传 JSON 可序列化的 ID/原始类型或配置安全 serializer。[Source: `_bmad-output/planning-artifacts/architecture.md#Infrastructure & Deployment`]

### Implementation Boundaries

- 不要实现上传、解析、chunk、embedding、retrieval、RAG、Agent 或 Tool Registry。
- 不要在 route 中写业务逻辑、直接调用 LLM/embedding/vector store、硬编码 API key、硬编码 tenant/user。
- 不要创建数据库表或 Alembic migration；Story 1.5 负责最小数据库迁移与基础治理表。
- 不要创建 Docker Compose 服务编排；Story 1.6 负责本地启动核心依赖。
- 可以创建 `docker/README.md` 或 `.gitkeep` 保留目录，但不要伪造 Compose 可用状态。
- 可以创建 `apps/web/README.md` 作为后续占位，但 Story 1.1 的 AC 不要求自定义前端，优先不要扩大范围。

### Testing Requirements

- 单元测试必须可在无数据库、无 Redis、无 MinIO、无外部网络、无真实 LLM/embedding/vector store 的环境下通过。
- 架构边界测试应失败得清楚：输出违规文件、违规 import 或 route 位置。
- `uv run pytest` 是本 Story 的硬性验收命令。
- `uv run ruff check .` 和 `uv run mypy apps packages tests` 是质量门命令；若因为工具初始配置问题失败，开发者必须修正配置或代码，不得把失败留给后续 Story。

### File Structure Guidance

建议本 Story 最小落地文件集：

```text
.
├── README.md
├── pyproject.toml
├── uv.lock
├── .python-version
├── .env.example
├── apps/
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   └── routes/
│   │       └── __init__.py
│   └── worker/
│       ├── __init__.py
│       └── main.py
├── packages/
│   ├── common/
│   │   └── __init__.py
│   ├── auth/
│   │   └── __init__.py
│   └── data/
│       └── __init__.py
├── tests/
│   ├── unit/
│   │   ├── test_app_smoke.py
│   │   └── test_architecture_boundaries.py
│   └── integration/
│       └── README.md
├── docs/
│   └── operations/
│       └── local-development.md
└── docker/
    └── README.md
```

### UX / Frontend Notes

- UX artifacts confirm第一阶段以 Open WebUI 为 chat-first 入口；本 Story 不创建自定义 React/Next.js 前端。
- 如果保留 `apps/web/README.md`，只能说明后续 Open WebUI/sidecar 集成方向，不得抢先实现 UI。
- 前端不得判断权限、补造 citation 或推断 retrieval result；这些约束后续由后端 API、AuthContext 和 Source Inspector 契约承担。[Source: `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md#Business Fine-Tuning Rules`]

### References

- `_bmad-output/planning-artifacts/epics.md#Story 1.1`
- `_bmad-output/planning-artifacts/architecture.md#Selected Starter`
- `_bmad-output/planning-artifacts/architecture.md#Project Structure & Boundaries`
- `_bmad-output/planning-artifacts/architecture.md#Implementation Handoff`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#Phase 0`
- `_bmad-output/planning-artifacts/implementation-readiness-report-2026-05-27.md#Epic Quality Verdict`

## Validation Checklist

Validation Result: PASS（2026-05-27T01:52:16+08:00）

- [x] Story 明确了用户角色、目标和收益。
- [x] Acceptance Criteria 来自 Epic Story 1.1，且保持 Given/When/Then 语义。
- [x] Tasks 覆盖 AC 1 到 AC 3，并标注 AC 映射。
- [x] Dev Notes 包含 starter 选择、技术栈、目录边界、测试要求、实现边界和当前仓库状态。
- [x] 明确说明无上一条 Story、无 git 历史、无既有实现代码可复用。
- [x] 明确禁止真实外部 LLM、embedding provider、向量数据库调用。
- [x] 明确后续 Story 边界，防止把健康检查、认证上下文、数据库迁移和 Docker Compose 抢到 Story 1.1 中。

## Change Log

- 2026-05-27: Implemented Story 1.1 FastAPI monorepo skeleton, tests, docs, root uv configuration, and validation gates.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex / Amelia (`bmad-dev-story`)

### Debug Log References

- `uv lock`：通过，生成 `uv.lock`，uv 下载并使用 CPython 3.11.15。
- `uv run pytest` RED：初次因 editable install `.pth` 在中文路径下触发 GBK 解码失败；通过 `[tool.uv] package = false` 避免本项目 editable install，本 Story 测试依赖 `pythonpath = ["."]`。
- `uv run pytest` RED：随后按预期因 `ModuleNotFoundError: No module named 'apps.api.main'` 失败，验证 smoke test 可捕获缺失 API 入口。
- `uv sync`：通过。
- `uv lock --check`：通过。
- `uv run pytest`：通过，3 passed。
- `uv run ruff check .`：通过。
- `uv run mypy apps packages tests`：通过。

### Completion Notes List

- 建立 `uv` 根工程配置、Python 3.11 baseline、运行时依赖、开发依赖、pytest/ruff/mypy 配置和 `uv.lock`。
- 创建最小 monorepo 骨架：`apps/api`、`apps/worker`、`packages/common`、`packages/auth`、`packages/data`、`tests/unit`、`tests/integration`、`docs`、`docker`。
- `apps/api/main.py` 仅导出最小 `FastAPI` app；未实现 health/readiness、统一 envelope、错误处理中间件、认证、数据库、Docker Compose 或任何 RAG/Agent 业务逻辑。
- 增加 smoke test 和 AST 架构边界测试，覆盖 domain 层禁止基础设施/框架 import、FastAPI route 声明位置约束。
- 添加 `.env.example`、README、本地开发文档和 Docker 占位说明，明确后续 Story 边界。

### File List

- `.env.example`
- `.gitignore`
- `.python-version`
- `README.md`
- `apps/__init__.py`
- `apps/api/__init__.py`
- `apps/api/main.py`
- `apps/api/routes/__init__.py`
- `apps/worker/__init__.py`
- `apps/worker/main.py`
- `docker/README.md`
- `docs/operations/local-development.md`
- `packages/__init__.py`
- `packages/auth/__init__.py`
- `packages/common/__init__.py`
- `packages/data/__init__.py`
- `pyproject.toml`
- `tests/integration/README.md`
- `tests/unit/test_app_smoke.py`
- `tests/unit/test_architecture_boundaries.py`
- `uv.lock`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/1-1-set-up-initial-project-from-starter-template-初始化生产级-fastapi-monorepo.md`

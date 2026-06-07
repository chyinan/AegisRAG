---
stepsCompleted: [1, 2, 3, 4, 5, 6, 7, 8]
inputDocuments:
  - AGENTS.md
  - PRD.md
  - docs/TECHNICAL_PREFERENCES.md
  - docs/EMPLOYMENT_PRODUCT_MARKET_OPTIMIZATION.md
  - _bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md
  - _bmad-output/planning-artifacts/research/domain-enterprise-rag-agent-industry-research-2026-05-26.md
  - _bmad-output/planning-artifacts/research/market-enterprise-rag-agent-employment-product-research-2026-05-26.md
  - _bmad-output/planning-artifacts/research/technical-enterprise-rag-agent-architecture-research-2026-05-26.md
  - _bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/DESIGN.md
  - _bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md
workflowType: architecture
project_name: 本地化多源知识增强 RAG + Agent 问答系统
user_name: 浅川枫
date: 2026-05-26
lastStep: 8
status: complete
completedAt: 2026-05-26
sourceVerification:
  - https://pypi.org/project/fastapi/
  - https://pypi.org/project/pydantic/
  - https://pypi.org/project/SQLAlchemy/
  - https://pypi.org/project/alembic/
  - https://pypi.org/project/rq/
  - https://pypi.org/project/uv/
  - https://docs.astral.sh/uv/concepts/projects/init/
  - https://github.com/fastapi/full-stack-fastapi-template
  - https://www.postgresql.org/
  - https://github.com/pgvector/pgvector
  - https://www.postgresql.org/docs/current/textsearch.html
---

# Architecture Decision Document

本文档是“本地化多源知识增强 RAG + Agent 问答系统”的架构决策文档，基于现有 BMAD PRD、UX、调研文档和 `AGENTS.md` 工程规则生成。它的目标不是描述一个 demo，而是为后续 AI Coding Agent 提供一致、可执行、可验证的技术决策边界。

## Project Context Analysis

### Requirements Overview

**Functional Requirements:**

PRD 共识别出 32 个功能需求，覆盖 8 个能力域：

1. 多源文档接入与文档治理：上传、parser、chunker、版本、软删除。
2. Embedding 与索引管道：Provider 抽象、embedding 元数据、VectorStore 接口。
3. Hybrid Retrieval：dense、BM25 sparse、RRF merge、dedup、rerank、retrieval log。
4. RAG 回答：context packing、prompt builder、LLM Provider、citation、SSE。
5. API、会话与前端集成：`/upload`、`/retrieve`、`/query`、`/chat`、Open WebUI 集成。
6. Auth、RBAC、审计与数据治理：AuthContext、ACL 检索过滤、audit log。
7. Tool Registry 与受控 Agent：工具注册、权限、timeout、rate limit、ReAct runtime。
8. Eval、可观测性与运维：eval dataset、structured logging、metrics、Docker Compose。

架构含义：系统必须按“数据治理 -> ingestion -> retrieval -> RAG -> Agent”的顺序建设。任何跳过权限、citation、eval、observability 的实现都不满足产品目标。

**Non-Functional Requirements:**

- 安全：用户输入、文档内容、Web 内容和 Tool output 全部视为不可信；权限必须在后端策略执行。
- 合规：日志不得记录 API key、access token、企业机密全文和用户敏感原文；文档删除默认软删除。
- 可替换：LLM、Embedding、VectorStore、Reranker、Object Storage、Queue 都必须通过端口接口接入。
- 可测试：核心逻辑必须能用 Fake Provider 或 mock 覆盖，单测不得真实调用外部 LLM。
- 可观测：每次请求和每个 RAG/Agent 阶段记录 request_id、trace_id、tenant_id、user_id、latency、model、token usage、top_k、rerank score、tool calls、error_code。
- 可部署：Docker Compose 必须能启动 API、worker、PostgreSQL、Redis、MinIO，OpenSearch/Milvus/Prometheus/Grafana 后置为可选。

**Scale & Complexity:**

- Primary domain: 企业 AI 应用后端、RAG 平台、受控 Agent runtime。
- Complexity level: enterprise。
- Estimated architectural components: 12 个核心 package、2 个应用入口、4 类基础设施服务、3 类测试层。

复杂度来自多租户、RBAC、文档版本、异步任务、hybrid retrieval、citation、SSE、Agent tool governance、eval 和 observability 的组合。该项目不是普通 chat app，也不是单一检索服务。

### Technical Constraints & Dependencies

- Python 3.11+ 是后端基线；FastAPI + Pydantic v2 + SQLAlchemy 2.x + Alembic 是主要后端栈。
- PostgreSQL + pgvector 是默认向量和 metadata 存储方案；FAISS 用于本地轻量/离线测试；Milvus 暂缓到大规模阶段。
- PostgreSQL full text search 是 MVP sparse retrieval 默认方案；OpenSearch 可作为增强，不作为第一阶段阻塞依赖。
- Redis + RQ 是 MVP 异步任务默认方案；Celery 保留为未来替换选项。
- MinIO 是默认对象存储。
- Open WebUI 是早期前端入口和会话外壳，不承担权限治理边界。
- 所有外部模型调用通过 Provider 抽象，不允许 route 或 domain 直接绑定 OpenAI、Qwen、DeepSeek、vLLM、Ollama SDK。

### Cross-Cutting Concerns Identified

- AuthContext 必须贯穿 API、application service、retrieval、RAG、Agent 和 audit。
- `tenant_id`、`user_id`、`acl`、`document_id`、`version_id`、`chunk_id` 必须从数据模型开始贯穿全链路。
- 检索授权必须发生在 dense/sparse 查询阶段，而不是 rerank 后或回答后。
- Prompt injection 防护属于 RAG/Agent 安全协议，不属于 prompt 文案装饰。
- Agent 必须后置于 Tool Registry；没有 schema、permission、timeout、rate_limit、audit、max_steps、max_tool_calls 的 Agent 不进入主线。
- eval 和 observability 从 Phase 2 开始同步建设，不作为项目末期补丁。

## Starter Template Evaluation

### Primary Technology Domain

本项目的主技术域是 API/backend-first monorepo，后续可接 Open WebUI 或轻量 React/Next.js 管理台。核心价值在后端治理、检索链路、RAG 质量和工具安全，不应由全栈模板决定领域边界。

### Starter Options Considered

**FastAPI Full Stack Template**

- 已验证为 FastAPI 官方生态下的全栈模板，包含 FastAPI、React、SQLModel、PostgreSQL、Docker、GitHub Actions 等。
- 适合快速生成全栈应用，但其 SQLModel/前端优先结构与本项目要求的 `packages/*` 领域边界、SQLAlchemy 2.x、RAG/Agent 端口适配器结构并不完全匹配。
- 结论：可作为 Docker/API/CI 的参考，不作为直接 scaffold 基线。

**uv packaged project / workspace**

- `uv init` 支持应用和 library 模板；`--package` 会创建 `src` 布局，`uv` 支持 lockfile、workspace 和统一运行命令。
- 适合本项目的 Python monorepo：根 `pyproject.toml` 管理工具链，各 `packages/*` 作为明确模块边界。
- 结论：采用 `uv` 管理依赖、锁文件和命令入口，手工建立符合 `AGENTS.md` 的 monorepo 结构。

**Custom FastAPI backend scaffold**

- 根目录保留 `apps/`、`packages/`、`tests/`、`docs/`、`docker/`。
- FastAPI route 只做 schema、AuthContext 注入、service 调用和响应封装。
- 领域对象、端口接口、fake adapter、infrastructure adapter 分层放置。
- 结论：这是选定方案。

### Selected Starter: Custom uv + FastAPI Monorepo

**Rationale for Selection:**

本项目最重要的 starter 不是 UI 或全栈模板，而是“不会长成 demo”的工程骨架。`uv` 提供稳定依赖和 workspace 能力，FastAPI 提供 async API、OpenAPI 和 SSE 能力；目录由项目架构规则决定，避免被通用模板带偏。

**Initialization Command:**

```powershell
uv init --package .
uv add "fastapi[standard]" pydantic-settings sqlalchemy alembic asyncpg psycopg[binary] redis rq httpx structlog python-multipart
uv add --dev pytest pytest-asyncio ruff mypy
```

**Architectural Decisions Provided by Starter:**

**Language & Runtime:** Python 3.11+，`uv` 管理 `.python-version`、`pyproject.toml`、`uv.lock`。

**Styling Solution:** MVP 不以自定义前端为主；Open WebUI 或后续 `apps/web` 单独决策。

**Build Tooling:** `uv sync`、`uv run`、Docker multi-stage build。

**Testing Framework:** pytest + pytest-asyncio；unit/integration/eval 分目录。

**Code Organization:** 根 monorepo + `apps/api` + `apps/worker` + `packages/*`。

**Development Experience:** `uv run pytest`、`uv run ruff check`、`uv run alembic upgrade head`、Docker Compose 启动依赖服务。

## Core Architectural Decisions

### Decision Priority Analysis

**Critical Decisions (Block Implementation):**

- 后端语言和框架：Python 3.11+、FastAPI、Pydantic v2。
- 数据库：PostgreSQL 18 系列作为默认关系数据、metadata 和 pgvector 承载层。
- ORM 和迁移：SQLAlchemy 2.x + Alembic。
- 向量检索：pgvector 默认，VectorStore 端口保留 FAISS/Milvus。
- sparse retrieval：PostgreSQL full text search MVP 默认；OpenSearch 后置。
- 异步任务：Redis + RQ MVP 默认，禁止上传接口同步等待 embedding。
- 对象存储：MinIO/S3-compatible ObjectStorage 端口。
- 模型调用：LLMProvider、EmbeddingProvider、Reranker 端口，Fake Provider 用于测试。
- API 规范：REST + SSE；统一响应 envelope 和 structured error。
- 权限边界：AuthContext 显式传入 application service，检索阶段强制 tenant/ACL filter。

**Important Decisions (Shape Architecture):**

- Open WebUI 是入口，不是治理边界。
- Agent 后置于 Tool Registry。
- eval dataset 从 retrieval 阶段开始。
- 日志采用 structured JSON，OpenTelemetry/Prometheus/Grafana 预留。

**Deferred Decisions (Post-MVP):**

- Milvus 生产 adapter。
- Graph RAG。
- 多 Agent。
- 复杂 Web Crawler。
- 完整自定义 React/Next.js 前端。
- 企业 SSO 深度集成。

### Data Architecture

**Database:** PostgreSQL 18 系列为开发和生产默认。PostgreSQL 官方当前页显示 2026-05-14 发布 18.4/17.10/16.14/15.18/14.23，且 PostgreSQL 18 是当前文档版本。MVP 使用 PostgreSQL 18 + pgvector；生产可按企业版本政策选择同系列小版本。

**Vector Extension:** pgvector v0.8.2 作为向量扩展基线。pgvector 官方 README 明确支持 Postgres 13+、exact/approximate nearest neighbor、cosine/inner product/L2 等距离，并可与 Postgres 数据共同存储。

**Sparse Search:** PostgreSQL full text search 作为 MVP 稀疏检索基础。文档侧以 `tsvector`/GIN 索引起步；如果 BM25 质量或中文分词能力不足，再通过 `SparseRetriever` adapter 切换 OpenSearch。

**Data Modeling:** Storage 层使用 SQLAlchemy declarative models；Domain 层使用 dataclass 或 Pydantic DTO。不得把 SQLAlchemy model 直接传进 retrieval/rag/agent domain 逻辑。

**Migrations:** Alembic 管理 schema。迁移脚本必须显式包含 extension、index、enum、constraint、soft-delete 字段和审计字段，不依赖生产库自动建表。

**Caching:** Redis 用于队列、短期状态和可选 session cache。业务真相仍在 PostgreSQL；不能用 Redis 保存不可恢复的会话或权限状态。

### Authentication & Security

**Authentication:** MVP 认证方案固定为“开发/测试模拟 AuthContext + 轻量 JWT adapter”。本地开发、测试和 synthetic eval 可以使用显式启用的模拟 AuthContext；Open WebUI adapter、API 客户端和后续集成路径使用轻量 JWT bearer token。两种入口必须通过同一个 dependency / parser 产出相同的 `AuthContext` DTO：

```text
user_id
tenant_id
roles
department
permissions
```

企业 SSO adapter 后置到 post-MVP，但接口必须从第一天兼容企业身份源。模拟 AuthContext 只能在开发、测试或显式配置的本地环境启用，生产配置默认禁用。

**Authorization:** `packages/auth` 定义 RBAC policy、ACL expression、scope narrowing。应用服务接收 AuthContext，检索和工具调用通过 policy builder 转换为 tenant/ACL/metadata filter。

**Document Security:** 文档内容和 Web 内容均为 untrusted content。PromptBuilder 必须包裹上下文边界并声明文档内容不能成为系统指令。

**Tool Security:** Tool Registry 是 Agent 唯一工具入口。每个工具必须有 name、description、input_schema、output_schema、permission、timeout、rate_limit、handler。`file_reader` 必须 allowlist，禁止任意路径读取。

**Secret Handling:** 所有 secret 来自环境变量或 secret file；日志、eval artifact 和 retrieval log 不记录 key/token/企业机密全文。

### API & Communication Patterns

**API Style:** REST API + SSE streaming。

Required endpoints:

```text
POST /upload
POST /retrieve
POST /query
POST /chat
POST /sources/resolve
POST /agent/run
GET /health
GET /ready
```

**Authorized Source Detail Contract:**

`POST /sources/resolve` is the canonical Source Inspector endpoint. It accepts a citation reference such as `document_id`, `version_id`, `chunk_id`, optional page range, and the current request/AuthContext. The application service must re-run tenant, RBAC, ACL, soft-delete, and version visibility checks before returning any fragment. Successful responses include only the authorized text fragment, source metadata, document/version/chunk/page identifiers, title_path, retrieval_method if available, and redacted audit metadata. Missing or unauthorized references return the same structured denial shape and must not disclose whether the source exists.

**Response Envelope:**

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

**Error Envelope:**

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

**SSE Events:**

```text
token
citation
tool_call
tool_result
error
final
```

**Service Communication:** API 调 application services；application services 调 domain services 和 ports；ports 由 infrastructure adapters 实现。worker 与 API 通过数据库 job 状态和 Redis queue 协作，不共享内存状态。

### Frontend Architecture

**MVP Frontend Path:** Open WebUI 作为 chat-first 入口。第一集成路径固定为 OpenAI-compatible chat adapter backed by `/chat`；最小 sidecar page 只承载 Source Inspector、upload/job status、diagnostics 或 eval 入口。前端不得判断权限、不得补造 citation、不得推断 retrieval result。

**UI Contract:** 后端返回 scope、citations、job status、request_id、retrieval trace metadata。Source Inspector 必须通过 `POST /sources/resolve` 二次授权获取片段。普通用户只看范围、来源、无答案状态；管理员可看 dense/sparse/RRF/rerank/context packing trace。

**Accessibility Contract:** 任何自定义 Source Inspector、Knowledge Admin、Diagnostics、Eval Reports 或 Agent Review surface 都必须满足 UX 文档规定的 WCAG 2.2 AA、键盘聚焦、`aria-live`、alert region、drawer/sheet 焦点恢复、非纯颜色状态表达和长 ID 换行/截断要求。

**Future Custom Web:** 若建设 `apps/web`，使用 React/Next.js，遵守 UX 文档：三栏桌面布局、移动端 bottom sheet、citation chip、Source Inspector、Knowledge Admin、Retrieval Diagnostics。

### Infrastructure & Deployment

**Local Compose Services:**

```text
api
worker-ingestion
worker-embedding
postgres
redis
minio
opensearch optional
milvus optional
prometheus optional
grafana optional
```

**Queue:** RQ 2.9.0 当前 PyPI 版本，MVP 选它是因为简单、Redis 原生、足够支撑 ingestion/embedding/eval jobs。重要安全约束：RQ 默认 serializer 基于 pickle，不得把不可信对象直接入队；队列参数必须使用 JSON 可序列化的原始类型或显式安全 serializer。

**Observability:** `structlog` 或标准 logging JSON formatter 起步；所有 request/job/tool log 必须带 request_id、trace_id、tenant_id、user_id。OpenTelemetry span 命名按 `api.*`、`ingestion.*`、`retrieval.*`、`rag.*`、`agent.*` 预留。

**CI/CD:** 初始流水线：install -> ruff -> pytest unit -> pytest integration mock -> alembic migration check -> docker build。Retrieval eval smoke test 从 Epic 3 开始加入；后续 RAG citation/no-answer eval 在 Epic 5 扩展。

### Version Decisions Verified on 2026-05-26

| Component | Decision | Verified current signal |
| --- | --- | --- |
| FastAPI | Use FastAPI 0.136.x range initially | PyPI shows FastAPI 0.136.3 released 2026-05-23 |
| Pydantic | Use Pydantic v2 | PyPI shows Pydantic 2.13.4 released 2026-05-06 |
| SQLAlchemy | Use SQLAlchemy 2.x | PyPI shows SQLAlchemy 2.0.50 released 2026-05-24 |
| Alembic | Use Alembic 1.x | PyPI shows Alembic 1.18.4 released 2026-02-10 |
| uv | Use uv workspace/package manager | PyPI shows uv 0.11.16 released 2026-05-21 |
| RQ | Use RQ for MVP queue | PyPI shows rq 2.9.0 released 2026-05-19 |
| PostgreSQL | Use PostgreSQL 18 series default | PostgreSQL site lists 18.4 current release on 2026-05-14 |
| pgvector | Use pgvector 0.8.x baseline | pgvector README installation uses v0.8.2 |

## Implementation Patterns & Consistency Rules

### Pattern Categories Defined

Critical conflict points identified: naming, API envelopes, error codes, event payloads, test placement, DTO/model separation, job state names, audit redaction, provider boundaries, and frontend governance.

### Naming Patterns

**Database Naming Conventions:**

- Tables: plural snake_case, e.g. `documents`, `document_versions`, `embedding_jobs`, `retrieval_logs`.
- Columns: snake_case, e.g. `tenant_id`, `created_by`, `deleted_at`.
- Foreign keys: `{referenced_singular}_id`, e.g. `document_id`, `version_id`, `agent_run_id`.
- Indexes: `ix_{table}_{columns}`, e.g. `ix_chunks_tenant_document_version`.
- Unique constraints: `uq_{table}_{columns}`.
- Enums/status values: lowercase snake_case, e.g. `retrieval_ready`, `embedding_failed`.

**API Naming Conventions:**

- Endpoints use lower kebab or simple nouns only where needed; core endpoints remain PRD-defined: `/upload`, `/retrieve`, `/query`, `/chat`, `/agent/run`.
- JSON fields use snake_case across backend and Open WebUI adapter.
- Headers use standard names where possible: `X-Request-ID` accepted, response always includes `request_id` body field.
- Route params use FastAPI style: `/documents/{document_id}`.

**Code Naming Conventions:**

- Python files/modules: snake_case.
- Classes: PascalCase.
- Protocols end with capability noun: `LLMProvider`, `VectorStore`, `Reranker`.
- DTOs end with `Request`, `Response`, `Record`, `Result`, or `Context`.
- Application services end with `Service`.
- Infrastructure adapters include provider/type: `PgVectorStore`, `PostgresSparseRetriever`, `OpenAIChatProvider`, `FakeEmbeddingProvider`.

### Structure Patterns

**Project Organization:**

- `apps/*` contains runnable processes only.
- `packages/*` contains importable modules and no FastAPI route definitions except `apps/api`.
- `packages/*/domain` is pure logic and must not import SQLAlchemy/FastAPI/httpx.
- `packages/*/infrastructure` implements ports and may import external SDKs.
- `packages/*/storage` contains SQLAlchemy models/repositories where relevant.
- `tests/unit` mirrors package boundaries.
- `tests/integration` covers adapters with dockerized or fake dependencies.
- `tests/eval` contains RAG eval fixtures and runners.

**File Structure Patterns:**

- `schemas.py` is API schema only.
- `models.py` is SQLAlchemy storage model only.
- `dto.py` or `types.py` is internal DTO only.
- `ports.py` contains Protocol definitions.
- `exceptions.py` contains domain exceptions.
- `service.py` contains application use case orchestration.
- `adapters/` contains concrete external integrations.

### Format Patterns

**API Response Formats:**

- All non-streaming responses use `{request_id, data, error, metadata}`.
- Stream responses use SSE events; each event payload includes `request_id` and where relevant `trace_id`.
- Expected domain errors map to stable error codes; no raw exception class names in API responses.

**Data Exchange Formats:**

- Datetime values are ISO 8601 strings in UTC.
- IDs are strings; UUID is preferred for business identifiers.
- ACL is structured JSON, never prompt text.
- Source metadata keeps original URI/title/page but redacts local absolute paths unless user is authorized.

### Communication Patterns

**Event System Patterns:**

SSE event payload examples:

```json
{
  "request_id": "...",
  "event": "citation",
  "citation": {
    "document_id": "...",
    "version_id": "...",
    "chunk_id": "...",
    "source": "...",
    "page_start": 3,
    "page_end": 4
  }
}
```

Job status values:

```text
uploaded
parsing
parsed
chunking
chunked
embedding
indexing
retrieval_ready
failed_retryable
failed_terminal
deleted
```

**State Management Patterns:**

- DB is source of truth for document/job/session/run state.
- Redis may cache or queue state but cannot be the only persistence for important status.
- Worker jobs receive IDs, not large payloads.

### Process Patterns

**Error Handling Patterns:**

- Domain raises typed exceptions such as `PermissionDeniedError`, `DocumentNotFoundError`, `ProviderTimeoutError`, `IndexDimensionMismatchError`.
- Application service catches expected domain exceptions to attach request context and audit data.
- API maps expected exceptions to structured errors.
- Unexpected exceptions are logged with redacted context and returned as generic `INTERNAL_ERROR`.

**Loading/Retry Patterns:**

- Upload returns `document_id`, `version_id`, `job_id`, `status`.
- Retriable worker errors set `failed_retryable` with `error_code`, `attempt_count`, `next_retry_at`.
- Provider calls must have configured timeout and retry budget.

### Enforcement Guidelines

**All AI Agents MUST:**

- Check existing package boundaries before adding files.
- Add or reuse a port interface before introducing any external SDK.
- Thread AuthContext through application services.
- Add tests using Fake Provider/mock before real adapter behavior is relied on.
- Keep route handlers thin.
- Record observability fields for request/job/retrieval/generation/tool flows.
- Never put permission logic in prompts.

**Pattern Enforcement:**

- PR/review checklist must include layer boundary, provider abstraction, AuthContext, tests, logging, and docs.
- ruff enforces import order/style; pytest enforces behavior.
- Architecture violations are documented in `docs/adr/` or fixed before merging.

### Pattern Examples

**Good Examples:**

```python
class EmbeddingProvider(Protocol):
    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...
```

```python
async def query(request: QueryRequest, context: RequestContext) -> QueryResponse:
    retrieval_result = await retrieval_service.retrieve(request.query, context.auth)
    return await rag_service.answer(request, retrieval_result, context)
```

**Anti-Patterns:**

- FastAPI route directly calls OpenAI SDK.
- Retrieval searches all chunks and filters ACL after answer generation.
- Agent calls arbitrary Python function by name.
- Upload endpoint waits for all embeddings before returning.
- Tests call a real external LLM by default.

## Project Structure & Boundaries

### Complete Project Directory Structure

```text
.
├── AGENTS.md
├── PRD.md
├── README.md
├── pyproject.toml
├── uv.lock
├── .python-version
├── .env.example
├── apps/
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── dependencies.py
│   │   ├── error_handlers.py
│   │   ├── middleware.py
│   │   └── routes/
│   │       ├── health.py
│   │       ├── upload.py
│   │       ├── retrieve.py
│   │       ├── query.py
│   │       ├── chat.py
│   │       └── agent.py
│   ├── worker/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── queues.py
│   │   └── jobs/
│   │       ├── ingestion_jobs.py
│   │       ├── embedding_jobs.py
│   │       └── eval_jobs.py
│   └── web/
│       └── README.md
├── packages/
│   ├── common/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── context.py
│   │   ├── errors.py
│   │   ├── logging.py
│   │   ├── pagination.py
│   │   └── time.py
│   ├── auth/
│   │   ├── __init__.py
│   │   ├── context.py
│   │   ├── dto.py
│   │   ├── exceptions.py
│   │   ├── policies.py
│   │   ├── rbac.py
│   │   └── storage/
│   │       ├── models.py
│   │       └── repositories.py
│   ├── data/
│   │   ├── __init__.py
│   │   ├── domain.py
│   │   ├── dto.py
│   │   ├── exceptions.py
│   │   ├── service.py
│   │   └── storage/
│   │       ├── models.py
│   │       └── repositories.py
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── domain.py
│   │   ├── ports.py
│   │   ├── service.py
│   │   ├── parsers/
│   │   │   ├── pdf.py
│   │   │   ├── docx.py
│   │   │   ├── txt.py
│   │   │   └── markdown.py
│   │   ├── chunkers/
│   │   │   ├── fixed_size.py
│   │   │   ├── semantic.py
│   │   │   └── hierarchical.py
│   │   ├── cleaner.py
│   │   └── dedup.py
│   ├── embeddings/
│   │   ├── __init__.py
│   │   ├── dto.py
│   │   ├── ports.py
│   │   ├── service.py
│   │   └── adapters/
│   │       ├── fake.py
│   │       ├── openai.py
│   │       ├── qwen.py
│   │       ├── deepseek.py
│   │       ├── ollama.py
│   │       └── vllm.py
│   ├── vectorstores/
│   │   ├── __init__.py
│   │   ├── dto.py
│   │   ├── ports.py
│   │   └── adapters/
│   │       ├── pgvector.py
│   │       ├── faiss.py
│   │       └── milvus.py
│   ├── retrieval/
│   │   ├── __init__.py
│   │   ├── dto.py
│   │   ├── ports.py
│   │   ├── service.py
│   │   ├── dense.py
│   │   ├── sparse.py
│   │   ├── rrf.py
│   │   ├── dedup.py
│   │   ├── filters.py
│   │   └── rerank.py
│   ├── rag/
│   │   ├── __init__.py
│   │   ├── dto.py
│   │   ├── service.py
│   │   ├── context_packer.py
│   │   ├── prompt_builder.py
│   │   ├── citation_extractor.py
│   │   └── streaming.py
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── dto.py
│   │   ├── ports.py
│   │   └── adapters/
│   │       ├── fake.py
│   │       ├── openai.py
│   │       ├── qwen.py
│   │       ├── deepseek.py
│   │       ├── ollama.py
│   │       └── vllm.py
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── dto.py
│   │   ├── ports.py
│   │   ├── registry.py
│   │   ├── runtime.py
│   │   ├── policies.py
│   │   ├── tools/
│   │   │   ├── rag_search.py
│   │   │   ├── calculator.py
│   │   │   └── file_reader.py
│   │   └── storage/
│   │       ├── models.py
│   │       └── repositories.py
│   └── memory/
│       ├── __init__.py
│       ├── dto.py
│       ├── service.py
│       └── storage/
│           ├── models.py
│           └── repositories.py
├── migrations/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
├── tests/
│   ├── unit/
│   │   ├── auth/
│   │   ├── ingestion/
│   │   ├── embeddings/
│   │   ├── vectorstores/
│   │   ├── retrieval/
│   │   ├── rag/
│   │   └── agent/
│   ├── integration/
│   │   ├── api/
│   │   ├── storage/
│   │   ├── worker/
│   │   └── retrieval/
│   ├── eval/
│   │   ├── datasets/
│   │   ├── runners/
│   │   └── reports/
│   └── fixtures/
├── docs/
│   ├── adr/
│   ├── api/
│   ├── eval/
│   └── operations/
└── docker/
    ├── compose.yaml
    ├── Dockerfile.api
    ├── Dockerfile.worker
    ├── postgres/
    │   └── init.sql
    └── minio/
```

### Architectural Boundaries

**API Boundaries:**

- `apps/api/routes/*` owns HTTP contracts only.
- `apps/api/dependencies.py` owns RequestContext/AuthContext injection.
- Routes call application services, never infrastructure adapters directly.

**Component Boundaries:**

- `packages/ingestion` owns `RawDocument -> ParsedDocument -> Section -> Chunk`.
- `packages/retrieval` owns dense/sparse/RRF/rerank/threshold/filter orchestration.
- `packages/rag` owns context packing, prompt building, generation orchestration and citation extraction.
- `packages/agent` owns Tool Registry and runtime limits.

**Service Boundaries:**

- Application services may coordinate multiple packages.
- Domain modules cannot depend on FastAPI, SQLAlchemy, Redis, MinIO, external SDKs.
- Infrastructure adapters implement protocols and translate external errors into domain exceptions.

**Data Boundaries:**

- PostgreSQL stores users, tenants, roles, documents, document_versions, chunks, embedding_jobs, retrieval_logs, chat_sessions, chat_messages, agent_runs, tool_calls.
- Object storage stores raw files and normalized artifacts.
- pgvector stores embeddings tied to chunk/version/model/dim.
- Redis/RQ stores queued jobs and ephemeral worker state.

### Requirements to Structure Mapping

| Requirement Area | Primary Location |
| --- | --- |
| Upload/API contract | `apps/api/routes/upload.py`, `packages/data/service.py` |
| Parser/chunker/dedup | `packages/ingestion/*` |
| Embedding provider | `packages/embeddings/ports.py`, `packages/embeddings/adapters/*` |
| Vector store | `packages/vectorstores/*` |
| Dense/sparse/RRF/rerank | `packages/retrieval/*` |
| Context/prompt/citation | `packages/rag/*` |
| LLM generation/stream | `packages/llm/*`, `packages/rag/streaming.py` |
| Auth/RBAC/ACL | `packages/auth/*` |
| Chat memory | `packages/memory/*` |
| Tool Registry/Agent | `packages/agent/*` |
| Audit/retrieval logs | storage models in `packages/data`, `packages/agent`, `packages/retrieval` |
| Eval | `tests/eval`, `docs/eval` |
| Deployment | `docker/*`, root `pyproject.toml`, `.env.example` |

### Integration Points

**Internal Communication:**

```text
API route
 -> application service
 -> domain service
 -> port protocol
 -> infrastructure adapter
 -> storage/provider
```

**External Integrations:**

- LLM APIs: OpenAI, Qwen, DeepSeek, Ollama, vLLM through `LLMProvider`.
- Embedding APIs/local models through `EmbeddingProvider`.
- Object storage through `ObjectStorage` port.
- Open WebUI through an OpenAI-compatible chat adapter backed by `/chat`; source drilldown through `POST /sources/resolve`.

**Data Flow:**

```text
upload
 -> object storage
 -> document metadata
 -> ingestion job
 -> parse/clean/dedup/chunk
 -> embedding job
 -> vector upsert + sparse index upsert
 -> retrieval_ready
```

```text
query/chat
 -> AuthContext
 -> optional rewrite
 -> dense + sparse retrieval with ACL filters
 -> RRF merge + dedup
 -> rerank
 -> threshold
 -> context packing
 -> prompt build
 -> LLM generate/stream
 -> citation extraction
 -> audit/retrieval log
```

```text
agent/run
 -> AuthContext + policy check
 -> AgentRuntime
 -> LLM step
 -> ToolRegistry validation
 -> permission/timeout/rate_limit
 -> tool handler
 -> tool audit
 -> final answer validation
```

## Architecture Validation Results

### Coherence Validation

**Decision Compatibility:**

所有关键技术选择互相兼容：FastAPI/Pydantic/SQLAlchemy/Alembic 属于成熟 Python API 栈；PostgreSQL 同时承载 metadata、full text search 和 pgvector，符合 MVP 简化部署的目标；Redis/RQ 满足 ingestion/embedding/eval 异步任务；Open WebUI 被限制为入口，不冲突后端治理。

**Pattern Consistency:**

命名、响应 envelope、错误、事件、job 状态、目录边界均与 `AGENTS.md` 一致。Provider/VectorStore/Reranker/ToolRegistry 的端口模式能防止单一 SDK 绑定。

**Structure Alignment:**

项目结构完整覆盖 API、Application Service、Domain、Infrastructure、Storage 五层。`apps/*` 与 `packages/*` 的边界可防止 route 中堆业务逻辑。

### Requirements Coverage Validation

**Functional Requirements Coverage:**

- FR-1 到 FR-4 由 `packages/data`、`packages/ingestion`、worker jobs 和 storage models 支持。
- FR-5 到 FR-7 由 `packages/embeddings`、`packages/vectorstores` 和 provider metadata 支持。
- FR-8 到 FR-12 由 `packages/retrieval` 和 retrieval_logs 支持。
- FR-13 到 FR-17 由 `packages/rag`、`packages/llm` 和 SSE adapter 支持。
- FR-18 到 FR-20 由 `apps/api`、`packages/memory`、Open WebUI adapter 支持。
- FR-21 到 FR-24 由 `packages/auth`、storage/audit policy 支持。
- FR-25 到 FR-28 由 `packages/agent` 支持。
- FR-29 到 FR-32 由 `tests/eval`、logging、Docker Compose 和 operations docs 支持。

**Non-Functional Requirements Coverage:**

- Security: AuthContext、ACL filter、Tool Registry、secret redaction、prompt injection boundary 全部有架构位置。
- Performance: 异步 ingestion/embedding、SSE、retrieval 分阶段 latency logging。
- Reliability: job status、retry、soft delete、migration、health/readiness。
- Testability: fake providers、unit/integration/eval 结构。
- Extensibility: provider/vectorstore/reranker/tool ports。

### Implementation Readiness Validation

**Decision Completeness:**

关键技术、版本基线、数据架构、安全边界、API 格式、worker 和部署方式均已记录。

**Structure Completeness:**

目录树具体到关键文件；每个 FR 能映射到模块位置。

**Pattern Completeness:**

命名、结构、数据格式、SSE 事件、job 状态、错误处理、重试、审计均有一致性规则。

### Gap Analysis Results

**Critical Gaps:** 无。

**Important Gaps:**

- 中文 BM25/全文检索质量可能需要分词扩展或 OpenSearch；MVP 先用 PostgreSQL full text adapter，保留替换边界。
- 企业 SSO 深度集成后置；MVP auth 已固定为开发/测试模拟 AuthContext + 轻量 JWT adapter，二者共享同一 `AuthContext` DTO 和权限策略入口。
- Cross-encoder reranker 具体模型未定；MVP 先 fake + interface。

**Nice-to-Have Gaps:**

- Grafana dashboard 可后续补。
- LangGraph 风格状态图可在 Agent Runtime 稳定后引入。
- 自定义 React/Next.js 管理台可在后端可信闭环完成后建设。

### Architecture Completeness Checklist

**Requirements Analysis**

- [x] Project context thoroughly analyzed
- [x] Scale and complexity assessed
- [x] Technical constraints identified
- [x] Cross-cutting concerns mapped

**Architectural Decisions**

- [x] Critical decisions documented with versions
- [x] Technology stack fully specified
- [x] Integration patterns defined
- [x] Performance considerations addressed

**Implementation Patterns**

- [x] Naming conventions established
- [x] Structure patterns defined
- [x] Communication patterns specified
- [x] Process patterns documented

**Project Structure**

- [x] Complete directory structure defined
- [x] Component boundaries established
- [x] Integration points mapped
- [x] Requirements to structure mapping complete

### Architecture Readiness Assessment

**Overall Status:** READY FOR IMPLEMENTATION

**Confidence Level:** high

**Key Strengths:**

- RAG 链路按生产级模块拆分，避免 demo 写法。
- 权限、tenant、ACL、citation、audit 从数据模型和检索阶段前置。
- Provider/VectorStore/Reranker/Tool 抽象能支撑多模型和多基础设施替换。
- eval 和 observability 与功能同步建设。
- Agent 被明确后置于 Tool Registry，安全边界清晰。

**Areas for Future Enhancement:**

- 中文 sparse retrieval 可通过 OpenSearch 或专业分词增强。
- Milvus adapter 在数据规模明确后实现。
- LangGraph 风格 Agent state graph 在 ReAct runtime 稳定后引入。
- OpenTelemetry GenAI semantic conventions 可在 structured logging 稳定后补齐。

### Implementation Handoff

**AI Agent Guidelines:**

- 严格遵守本文档和 `AGENTS.md`。
- 先建立 `packages/common`、`packages/auth`、`packages/data` 的上下文、错误、配置和数据模型。
- 每个核心端口先提供 Fake adapter 和单测，再接真实 infrastructure。
- route 只做 schema、认证上下文注入、service 调用和响应封装。
- 所有 retrieval、RAG、Agent 功能必须带 AuthContext、structured logging、测试和必要文档。

**First Implementation Priority:**

1. 创建 `uv` + FastAPI monorepo 骨架。
2. 定义 `RequestContext`、`AuthContext`、structured error、config、logging。
3. 建立 SQLAlchemy/Alembic 基础和核心表。
4. 实现 health/readiness 和最小测试链路。
5. 进入 ingestion pipeline 第一阶段。

## Source Verification Notes

- FastAPI 当前 PyPI 页面显示 0.136.3，发布时间 2026-05-23。
- Pydantic 当前 PyPI 页面显示 2.13.4，发布时间 2026-05-06。
- SQLAlchemy 当前 PyPI 页面显示 2.0.50，发布时间 2026-05-24。
- Alembic 当前 PyPI 页面显示 1.18.4，发布时间 2026-02-10。
- uv 当前 PyPI 页面显示 0.11.16，发布时间 2026-05-21；uv 文档说明 `uv init` 支持 application/library/package 项目结构。
- RQ 当前 PyPI 页面显示 2.9.0，发布时间 2026-05-19，并说明基于 Redis/Valkey 的后台任务处理；同时提示默认 pickle serializer 的安全风险。
- PostgreSQL 官网当前显示 18.4/17.10/16.14/15.18/14.23 于 2026-05-14 发布，PostgreSQL 18 为当前文档版本。
- pgvector README 说明其是 Postgres 的开源向量相似度搜索扩展，支持 Postgres 13+，并在安装示例中使用 v0.8.2。

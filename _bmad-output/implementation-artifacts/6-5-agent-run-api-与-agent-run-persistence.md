---
baseline_commit: 69ac0c6
---

# Story 6.5: `/agent/run` API 与 Agent Run Persistence

Status: review

生成时间：2026-06-08T12:56:38+08:00

## Story

As a 管理员,
I want Agent run 的 API、状态和持久化先独立落地,
so that 后续 tool audit 和 final answer validation 可以基于可追踪 run 执行。

## Acceptance Criteria

1. **`POST /agent/run` 必须是薄 route 并返回统一 envelope**
   - Given 授权用户调用 `POST /agent/run`
   - When API 创建并执行 Agent run
   - Then 返回统一 `ApiResponse[AgentRunResponse]`
   - And `data` 至少包含 `agent_run_id`、`request_id`、`trace_id`、`tenant_id`、`user_id`、`status`、`termination_reason`、`steps_used`、`tool_calls_used`、`error_code`、`created_at`、`updated_at`
   - And route 只处理 schema、`AuthenticatedRequestContext` 注入、service 调用和 response envelope，不直接调用工具 handler、LLM provider、storage repository 或 `ToolRegistry.execute()`

2. **Agent run 创建必须在 runtime 执行前持久化**
   - Given service 接收 `AuthenticatedRequestContext` 和请求体
   - When run 开始
   - Then 先在 `agent_runs` 创建状态为 `running` 的记录
   - And 记录必须包含 `tenant_id`、`user_id`、`created_by`、`request_id`、`trace_id`、`max_steps`、`max_tool_calls`、`timeout_seconds`
   - And runtime 只能通过注入的 `AgentRuntime` 或 runtime factory 执行，不能让 route 或 service 直接调用任意工具 handler

3. **`agent_runs` 表和 SQLAlchemy model 必须覆盖治理字段**
   - Given `agent_runs` 表首次引入
   - When Alembic migration 生成
   - Then 表包含 `id`、`created_at`、`updated_at`、`tenant_id`、`user_id`、`created_by`、`status`、`request_id`、`trace_id`、`max_steps`、`max_tool_calls`、`timeout_seconds`、`steps_used`、`tool_calls_used`、`termination_reason`、`error_code`、`latency_ms`、`metadata`
   - And 支持按 `tenant_id`、`user_id`、`id`、`request_id`、`status`、`created_at` 查询
   - And status 必须是稳定集合：`running`、`completed`、`stopped`、`failed`

4. **Runtime 终止状态必须可靠写回持久化记录**
   - Given Agent run 达到 `max_steps`、`max_tool_calls`、timeout、repeated action、stepper error、tool error 或 final answer
   - When runtime 返回 `AgentRunResult`
   - Then repository 将对应 `agent_runs.status` 更新为 `completed`、`stopped` 或 `failed`
   - And 写回 `termination_reason`、`steps_used`、`tool_calls_used`、`error_code`、`latency_ms` 和安全 metadata
   - And 如果写回失败，返回结构化 storage error，不能假装 run 成功持久化

5. **权限和认证上下文必须在 service 层校验**
   - Given 请求缺少 `AuthContext`
   - When 调用 `/agent/run`
   - Then 返回 `AUTH_CONTEXT_REQUIRED`，且 service 不执行
   - Given 用户缺少 agent run 权限
   - When 调用 `/agent/run`
   - Then 返回稳定权限错误，例如 `AGENT_RUN_FORBIDDEN`
   - And 不创建 `agent_runs` 记录，不调用 runtime，不暴露工具或资源存在性

6. **请求体只能配置 run 约束和初始用户输入摘要**
   - Given 客户端提交请求体
   - When schema 校验
   - Then 允许字段应限制为 `input` 或 `query`、可选 `max_steps`、`max_tool_calls`、`timeout_seconds`、可选安全 `metadata`
   - And 运行上限默认来自 `AppSettings` 的 `AGENT_*` 配置
   - And 客户端提供的上限必须经过后端 bounded 校验，不能允许无限 steps、无限 tools、无限 timeout 或 prompt 覆盖限制
   - And 请求体、metadata、audit 和 DB 不保存 prompt、hidden reasoning、raw tool arguments、raw tool output、文件内容、query 原文、token、secret、绝对路径或企业机密全文

7. **Agent stepper 装配必须保持 provider-neutral MVP 边界**
   - Given Story 6.5 需要调用 `AgentRuntime`
   - When 装配 API service
   - Then MVP 可以使用 deterministic/fake stepper 或明确的 injected stepper factory
   - And 不引入 OpenAI、Qwen、DeepSeek、Ollama、vLLM、LangChain、LangGraph、LlamaIndex 或 Haystack 依赖
   - And 真实 LLM-backed stepper 不属于本 story，除非它也通过既有 Provider 抽象且有 fake 测试

8. **agent run audit 必须与持久化记录可关联**
   - Given run 创建、完成、停止或失败
   - When service 记录 audit
   - Then audit event 包含 `request_id`、`trace_id`、`tenant_id`、`user_id`、`action`、`resource.type=agent_run`、`resource.id=agent_run_id`、`latency_ms`、`status`、`error_code`
   - And audit metadata 只包含安全摘要，例如 counts、termination reason、配置上限、agent_run_id
   - And 不重复实现 Story 6.6 的 durable `tool_calls` 表；本 story 只持久化 run 级状态

9. **API 和 storage 测试必须覆盖主路径、限制状态和安全失败**
   - Given 单元测试运行
   - When 执行 agent run service/repository tests
   - Then 覆盖 create -> runtime -> complete、max_steps stopped、tool failure failed、permission denied、storage write failure、metadata redaction
   - And 使用 fake runtime/stepper、fake or in-memory audit、SQLite storage fixtures，不调用真实 LLM、embedding、vector store、PostgreSQL、Redis、MinIO、Open WebUI、网络或外部 provider

10. **架构边界和文档必须同步**
    - Given 新增 `packages/agent/storage`
    - When boundary tests 运行
    - Then 允许 SQLAlchemy 只出现在 `packages/agent/storage/*`，`packages/agent` core runtime/registry/dto/policies/exceptions 仍禁止 framework/provider/infrastructure import
    - And `migrations/env.py` 必须导入 agent storage models，保证 Alembic metadata 覆盖
    - And README 必须说明 `/agent/run` API 与 durable `agent_runs` 已完成，同时 durable `tool_calls`、tool event streaming 和 final answer validation 仍未完成

## Tasks / Subtasks

- [x] 定义 Agent run API 和 service DTO（AC: 1, 5, 6）
  - [x] 在 `packages/agent` 中新增或扩展 DTO：`AgentRunCommand`、`AgentRunResponse`、`AgentRunRecord`、`AgentRunCreate`、`AgentRunUpdate`。
  - [x] API schema 使用 Pydantic v2，`extra="forbid"`；请求体必须 bounded，并拒绝无限或非有限 timeout。
  - [x] 不添加 `thought`、`prompt`、`messages`、`raw_output`、`tool_results` 等会诱导保存敏感内容的字段。
  - [x] 如果需要用户输入，保存安全摘要字段，例如 `input_summary`、`input_length` 或 metadata 中的安全计数，不保存 query 原文。

- [x] 实现 Agent run storage model、repository 和 migration（AC: 2-4, 10）
  - [x] 新增 `packages/agent/storage/models.py`，定义 `AgentRunModel(IdMixin, TimestampMixin, Base)`。
  - [x] 新增 `packages/agent/storage/repositories.py`，提供 `create_run()`、`complete_run()` / `update_run_result()`、`get_run()` 或必要查询方法。
  - [x] 迁移文件建议命名 `migrations/versions/20260527_0010_agent_runs.py`，`down_revision` 为 `20260527_0009`。
  - [x] migration 建表字段与 AC3 完全一致，并添加 check constraints：status 集合、`max_steps > 0`、`max_tool_calls >= 0`、`timeout_seconds > 0`、`steps_used >= 0`、`tool_calls_used >= 0`、`latency_ms >= 0`。
  - [x] 添加索引：`ix_agent_runs_tenant_user_id`、`ix_agent_runs_request_id`、`ix_agent_runs_tenant_status_created`、`ix_agent_runs_tenant_user_status`。
  - [x] 更新 `migrations/env.py` 导入 `packages.agent.storage.models`。

- [x] 实现 `AgentRunApplicationService`（AC: 1, 2, 4-8）
  - [x] 新增 `packages/agent/service.py` 或等价 application service 文件；它可以依赖 agent core DTO/runtime、repository port、`AuditPort` 和 `AuthenticatedRequestContext`。
  - [x] service 先校验 permission，例如 `agent:run` 或项目现有权限命名约定下的等价权限。
  - [x] service 先创建 `running` run record，再调用 injected runtime/runtime factory。
  - [x] 将 `AgentRunResult` 映射为 storage update 和 API response；`completed` 对应 final answer，`stopped` 对应 max limits/timeout/repeated action，`failed` 对应 stepper/tool/storage expected failure。
  - [x] runtime audit 已有 `agent.runtime.*` 事件；service 级 audit 应记录 run lifecycle，并以 `agent_run_id` 作为 resource id。
  - [x] 如果 runtime 执行异常不是结构化 `AgentRunResult`，service 必须转换为稳定 `AGENT_RUN_FAILED` 或更具体错误，并写回 failed 状态（如果 run 已创建）。

- [x] 装配 API dependency 和 route（AC: 1, 5-7）
  - [x] 新增 `apps/api/routes/agent.py`，提供 `POST /agent/run`。
  - [x] 更新 `apps/api/main.py` 注册 agent router。
  - [x] 更新 `apps/api/service_dependencies.py` 增加 `get_agent_run_application_service()`。
  - [x] MVP 装配可以使用 deterministic/fake stepper，必须在 README 明确说明真实 LLM-backed planning 尚未完成。
  - [x] route 只 import API schema/service dependency/common envelope，不 import storage、SQLAlchemy、tool adapters 或 provider adapters。

- [x] 保持 Tool Registry 与 runtime 边界（AC: 1, 7, 10）
  - [x] `/agent/run` 不直接注册或调用具体 tools；具体 tool registry assembly 应在 dependency/service assembly 层完成。
  - [x] 如果本 story 需要可运行 smoke path，可注册已存在的 governed tools，但必须通过 builder + `ToolRegistry`，不能绕过 registry。
  - [x] 不实现 durable `tool_calls`、SSE `tool_call` / `tool_result` event streaming、Open WebUI tool bridge 或 final answer validation；这些属于 Story 6.6 / 6.7。

- [x] 更新架构边界测试（AC: 1, 10）
  - [x] 修改 `tests/unit/test_architecture_boundaries.py`：`packages/agent/storage` 是唯一允许 SQLAlchemy 的 agent storage 层。
  - [x] 增加 route thinness 测试：`apps/api/routes/agent.py` 不导入 storage、SQLAlchemy、LLM/vector/retrieval internals 或 concrete tool adapters。
  - [x] 增加 service/core 边界测试：`packages/agent/runtime.py`、`registry.py`、`dto.py`、`policies.py` 仍 framework/provider/infrastructure free。

- [x] 新增 storage、service 和 API 测试（AC: 1-10）
  - [x] 新增 `tests/integration/storage/test_agent_run_repositories.py`，使用 SQLite + `Base.metadata.create_all` 覆盖 create/update/query、tenant/user scope、storage error safe details。
  - [x] 新增 `tests/unit/agent/test_agent_run_service.py`，使用 fake repository/runtime/audit 覆盖 completed/stopped/failed/permission denied/storage failure。
  - [x] 新增 `tests/integration/api/test_agent_routes.py`，用 dependency override 验证 envelope、auth missing、permission missing、route passes context/body to service。
  - [x] 更新 `tests/integration/storage/test_alembic_migrations.py` 或相关 migration 测试，确保新 migration 在链路中。

- [x] 更新 README 和配置说明（AC: 10）
  - [x] Build Status 从 Story 6.4 更新为 Story 6.5 `/agent/run` + durable `agent_runs`。
  - [x] API Surface 增加 `POST /agent/run` 为已暴露 endpoint。
  - [x] Storage Model 增加 `agent_runs`，但继续说明 durable `tool_calls` 仍未完成。
  - [x] Current Limits 删除 `/agent/run` API 和 durable `agent_runs`，保留 durable `tool_calls`、tool event streaming、final answer validation、真实 LLM-backed Agent planning。

- [x] 验证（AC: 1-10）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/agent tests/unit/test_architecture_boundaries.py tests/unit/common/test_config.py`
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/api/test_agent_routes.py tests/integration/storage/test_agent_run_repositories.py tests/integration/storage/test_alembic_migrations.py`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`

## Dev Notes

### Current Repository State

- Git baseline for this story context: `69ac0c6 docs(readme): remove resume keyword blurb`.
- Worktree was clean before this story file was created.
- Sprint status auto-selected `6-5-agent-run-api-与-agent-run-persistence` as the first backlog story.
- Story 6.4 completed provider-neutral `AgentRuntime`, `AgentRunConfig`, `AgentRunResult`, `AgentStepper`, repeated action detection and runtime audit.
- There is currently no `apps/api/routes/agent.py`, no `/agent/run` route registration, no `AgentRunApplicationService`, no `packages/agent/storage`, no `agent_runs` SQLAlchemy model and no `agent_runs` Alembic migration.

### Existing Patterns To Reuse

- API routes use FastAPI dependency injection and return `packages.common.envelope.ApiResponse` through `success_response()`.
- `apps/api/dependencies.py` already builds `AuthenticatedRequestContext` with request_id, trace_id, optional session_id and `AuthContext`.
- Protected endpoints should rely on `AuthenticatedRequestContextDep`; missing auth maps to `AUTH_CONTEXT_REQUIRED`.
- Service dependencies are assembled in `apps/api/service_dependencies.py`; routes should not wire infrastructure adapters directly.
- Storage models share `packages.data.storage.base.Base`, `IdMixin`, `TimestampMixin` and string UUID ids.
- Existing storage repositories catch `SQLAlchemyError`, rollback, and raise safe domain errors instead of leaking SQL or secrets.
- Existing migration sequence currently ends at `20260527_0009_chat_memory.py`; Story 6.5 should add the next migration.
- `AuditPort`, `AuditEvent`, `AuditResource` and `AuditStatus` are the existing audit boundary.
- `ToolRegistry.execute()` is the only tool execution path and already handles schema validation, permission, rate limit, timeout and tool-level audit.
- `AgentRuntime.run(context=AuthenticatedRequestContext)` returns safe `AgentRunResult` with request/trace/tenant/user, counts, status, termination reason, error code, observations and metadata.

### Architecture Requirements

- This story spans API Layer, Application Service Layer and Storage Layer for agent run lifecycle only.
- `packages/agent` core runtime must remain framework/provider/infrastructure free.
- `packages/agent/storage` is allowed to contain SQLAlchemy storage details, but only after boundary tests are adjusted to keep that exception narrow.
- Domain/application DTOs must not expose SQLAlchemy models.
- Route must not call LLM provider, `ToolRegistry.execute()`, concrete tool handlers or storage repository directly.
- Runtime config must come from `AppSettings` / request bounded overrides, not prompt instructions.
- Runtime must continue receiving the original `AuthenticatedRequestContext`; do not reconstruct tenant/user/permissions from request body or LLM output.
- Durable `tool_calls`, tool event SSE streaming, Open WebUI function/tool bridge and final answer validation are explicitly out of scope.

### Current UPDATE File Notes

- `apps/api/main.py`: add `agent_router` include. Preserve existing router registration style.
- `apps/api/service_dependencies.py`: add agent run service assembly. Avoid importing SQLAlchemy into routes; service dependency may open session and create repositories like existing RAG/chat dependencies.
- `packages/common/config.py`: already contains `AGENT_DEFAULT_MAX_STEPS`, `AGENT_DEFAULT_MAX_TOOL_CALLS`, `AGENT_DEFAULT_TIMEOUT_SECONDS`, `AGENT_REPEATED_ACTION_THRESHOLD`; reuse these defaults.
- `packages/agent/runtime.py`: do not weaken runtime safety. It should not learn about FastAPI, SQLAlchemy or storage.
- `packages/agent/__init__.py`: export only stable public service/DTO types if needed; do not import concrete tools.
- `migrations/env.py`: currently imports auth, data, audit and retrieval storage models; add agent storage model import.
- `tests/unit/test_architecture_boundaries.py`: currently scans all `packages/agent` except `packages/agent/tools` and forbids SQLAlchemy. This must be refined so only `packages/agent/storage` may import SQLAlchemy.
- `README.md`: currently says `/agent/run` API and durable `agent_runs` are not complete. Update after implementation.

### Previous Story Intelligence

- Story 6.1 established governed Tool Registry; do not bypass registration, schema validation, permission, timeout, rate limit or audit.
- Story 6.2 established that `rag_search` must reuse retrieval authorization and return only safe observation fields.
- Story 6.3 established that deterministic tools must not leak absolute paths, full content or sensitive values.
- Story 6.4 established runtime limits and safety:
  - `AgentRuntime` enforces max steps before next stepper call.
  - `max_tool_calls` is enforced before registry execution.
  - global timeout spans model/tool orchestration.
  - repeated action detection stops before the triggering tool call.
  - runtime audit metadata includes safe counts, tool names, argument keys and hashes only.
  - runtime result does not persist DB state; Story 6.5 owns durable run state.
- Story 6.4 review fixes matter here:
  - Timed-out or externally cancelled work must not keep running silently.
  - Stepper outputs must be runtime-validated.
  - Unexpected registry errors must become structured runtime failures.
  - Audit failure logging must not leak backend exception details.

### Git Intelligence

- `957faa0 feat(agent): add governed tool registry foundation` established Tool Registry, config defaults, README updates and boundary tests.
- `80a3b6e feat(agent): add governed rag search tool` added retrieval-backed tool adapter patterns.
- `e5046aa feat(agent): add calculator and file reader tools` added deterministic local tools and tests.
- `315346f feat(agent): add governed runtime limits` added `AgentRuntime`, repeated action detection and runtime config.
- `1feabec fix(agent): address runtime review findings` hardened cancellation, validation, exception handling and audit safety.
- `69ac0c6 docs(readme): remove resume keyword blurb` is the latest commit at story creation time.

### Suggested Implementation Shape

Use these names as guidance, not mandatory API if implementation reveals a better local convention:

```python
class AgentRunRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: str = Field(min_length=1, max_length=4000)
    max_steps: int | None = Field(default=None, gt=0, le=20)
    max_tool_calls: int | None = Field(default=None, ge=0, le=20)
    timeout_seconds: float | None = Field(default=None, gt=0, le=120)
    metadata: dict[str, object] = Field(default_factory=dict)
```

```python
class AgentRunApplicationService:
    async def run(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: AgentRunCommand,
    ) -> AgentRunResponse:
        ...
```

`input` is acceptable as API input, but do not store it raw in `agent_runs`. Store safe summary only, such as length, hash, or redacted metadata.

### Implementation Boundaries

- Do not implement durable `tool_calls`; Story 6.6 owns it.
- Do not implement final answer validation; Story 6.7 owns it.
- Do not add tool event streaming; future `/agent/run/stream` or equivalent belongs after durable tool events exist.
- Do not add real vendor-backed Agent stepper unless it goes through provider abstraction and fake tests. MVP can use deterministic/fake stepper for API/persistence contract.
- Do not add LangChain, LangGraph, LlamaIndex, Haystack or dynamic tool/function calling frameworks.
- Do not persist raw user query, prompt, hidden reasoning, tool args, tool outputs, file content, local absolute paths, tokens, secrets or enterprise-sensitive content.
- Do not let request body override tenant_id, user_id, roles, permissions or ACL.

### Latest Technical Information

- Project dependency baseline already pins Python `>=3.11`, FastAPI `>=0.136.3,<0.137`, Pydantic `>=2.13.4,<3`, SQLAlchemy `>=2.0.50,<3`, Alembic `>=1.18.4,<2`, pytest `>=9,<10`, ruff `>=0.14,<1`, mypy `>=1.19,<2` in `pyproject.toml`.
- No dependency upgrade is required for Story 6.5. Use existing FastAPI dependency injection, Pydantic v2 DTOs, SQLAlchemy 2.x async repository patterns and Alembic migration style.
- Current architecture document already verified these versions on 2026-05-26; as of story creation, the safer implementation choice is to follow repository pins rather than chase newer minor releases mid-sprint.

### References

- `_bmad-output/planning-artifacts/epics.md#Story-6.5-agent-run-api-与-agent-run-persistence`
- `_bmad-output/planning-artifacts/epics.md#Epic-6-受控-Agent-工具执行`
- `_bmad-output/planning-artifacts/epics.md#FR18`
- `_bmad-output/planning-artifacts/epics.md#FR23`
- `_bmad-output/planning-artifacts/epics.md#FR27`
- `_bmad-output/planning-artifacts/architecture.md#API-&-Communication-Patterns`
- `_bmad-output/planning-artifacts/architecture.md#Tool-Security`
- `_bmad-output/planning-artifacts/architecture.md#Data-Flow`
- `_bmad-output/planning-artifacts/architecture.md#Requirements-to-Structure-Mapping`
- `project-context.md#12-Agent-规则`
- `_bmad-output/implementation-artifacts/6-1-tool-registry-与工具治理模型.md`
- `_bmad-output/implementation-artifacts/6-2-rag-search-工具.md`
- `_bmad-output/implementation-artifacts/6-3-calculator-与受限-file-reader-工具.md`
- `_bmad-output/implementation-artifacts/6-4-react-agent-runtime-限制与重复动作检测.md`
- `apps/api/main.py`
- `apps/api/service_dependencies.py`
- `apps/api/dependencies.py`
- `packages/common/envelope.py`
- `packages/common/context.py`
- `packages/common/audit.py`
- `packages/common/config.py`
- `packages/agent/runtime.py`
- `packages/agent/registry.py`
- `packages/agent/dto.py`
- `packages/agent/exceptions.py`
- `packages/data/storage/base.py`
- `packages/memory/storage/models.py`
- `packages/memory/storage/repositories.py`
- `migrations/env.py`
- `migrations/versions/20260527_0009_chat_memory.py`
- `tests/unit/agent/test_runtime.py`
- `tests/unit/test_architecture_boundaries.py`
- `tests/integration/api/test_chat_routes.py`
- `tests/integration/storage/test_chat_memory_repositories.py`
- `README.md#Governed-Agent-Tools`
- `README.md#Current-Limits`

## Validation Checklist

Validation Result: PASS（2026-06-08T12:56:38+08:00）

- [x] Story 明确只实现 `/agent/run` API、run lifecycle service 和 durable `agent_runs`，不实现 durable `tool_calls`、tool event streaming 或 final answer validation。
- [x] Acceptance Criteria 覆盖薄 route、run 创建前置持久化、agent_runs schema、runtime 状态写回、权限、bounded request、provider-neutral stepper、audit、测试和 README 同步。
- [x] Tasks 给出具体文件结构、DTO、service、storage、migration、dependency、route、boundary tests、API/storage/service tests 和验证命令。
- [x] Dev Notes 明确当前代码状态、前序 story lessons、现有 runtime/registry/audit/config/envelope patterns、边界测试陷阱和非目标。
- [x] 明确测试默认不调用真实 LLM、embedding、vector store、PostgreSQL、Redis、MinIO、Open WebUI、网络或外部 provider。
- [x] 明确 DB/audit/API 不保存 prompt、hidden reasoning、raw tool args/output、文件内容、query 原文、token、secret、绝对路径或企业机密全文。

## Change Log

- 2026-06-08: Created comprehensive Story 6.5 developer context for `/agent/run` API and durable `agent_runs` persistence.
- 2026-06-08: Implemented `/agent/run` API, durable `agent_runs` persistence, service lifecycle audit, tests, migration, README updates, and moved story to review.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- 2026-06-08T13:02:59+08:00: Marked sprint story `6-5-agent-run-api-与-agent-run-persistence` in-progress.
- 2026-06-08T13:10:00+08:00: Added red tests for Agent run service, API route, storage repository, migration smoke, and architecture boundaries.
- 2026-06-08T13:13:34+08:00: Completed implementation and validation commands; full pytest regression passed.

### Completion Notes List

- Implemented bounded `AgentRunCommand` / `AgentRunRequestBody`, API response DTOs, storage DTOs, safe input summaries, and metadata filtering without persisting query text or prompt-like fields.
- Added durable `agent_runs` SQLAlchemy model, repository, Alembic migration, tenant/user scoped queries, safe storage errors, and Alembic metadata registration.
- Added `AgentRunApplicationService` with `agent:run` permission check, create-before-runtime persistence, runtime result status mapping, service-level audit events, and storage failure handling.
- Added thin `POST /agent/run` route, dependency assembly with deterministic provider-neutral stepper, and README documentation for completed API/persistence plus remaining Agent limits.
- Verified with targeted tests, full unit suite, ruff, mypy, and full `pytest` regression.

### File List

- README.md
- _bmad-output/implementation-artifacts/6-5-agent-run-api-与-agent-run-persistence.md
- _bmad-output/implementation-artifacts/sprint-status.yaml
- apps/api/main.py
- apps/api/routes/agent.py
- apps/api/service_dependencies.py
- migrations/env.py
- migrations/versions/20260527_0010_agent_runs.py
- packages/agent/__init__.py
- packages/agent/dto.py
- packages/agent/exceptions.py
- packages/agent/service.py
- packages/agent/storage/__init__.py
- packages/agent/storage/models.py
- packages/agent/storage/repositories.py
- packages/auth/policies.py
- tests/integration/api/test_agent_routes.py
- tests/integration/storage/test_agent_run_repositories.py
- tests/integration/storage/test_alembic_migrations.py
- tests/unit/agent/test_agent_run_service.py
- tests/unit/test_architecture_boundaries.py

---
baseline_commit: c6d2496
---

# Story 6.6: Tool Call Audit Persistence

Status: done

生成时间：2026-06-08T14:05:00+08:00

## Story

As a 管理员,
I want 每次工具调用都被独立审计和脱敏持久化,
so that Agent 行为可以复盘但不会泄露敏感内容。

## Acceptance Criteria

1. **每次 Tool Registry 调用必须产生 durable `tool_calls` 记录**
   - Given Agent Runtime 通过 `ToolRegistry.execute()` 调用任意工具
   - When 工具调用完成、被拒绝、超时、schema validation 失败、rate limit 命中或 handler/output validation 失败
   - Then `tool_calls` 表写入一条记录
   - And 记录包含 `agent_run_id`、`tool_name`、`permission`、`arguments_summary`、`result_summary`、`latency_ms`、`status`、`error_code`、`tenant_id`、`user_id`、`request_id`、`trace_id`
   - And handler 未执行的失败也必须有审计事件，不能只审计成功调用

2. **`tool_calls` 表和 SQLAlchemy model 必须覆盖治理字段**
   - Given `tool_calls` 表首次引入
   - When Alembic migration 生成
   - Then 表包含 `id`、`created_at`、`updated_at`、`tenant_id`、`user_id`、`agent_run_id`、`tool_name`、`permission`、`status`、`latency_ms`、`error_code`、`request_id`、`trace_id`、`arguments_summary`、`result_summary`
   - And `agent_run_id` 必须外键关联 `agent_runs.id`
   - And status 必须是稳定集合：`success`、`denied`、`failure`
   - And 支持按 `agent_run_id`、`tool_name`、`status`、`created_at` 查询，并保留 tenant/user scope 查询能力

3. **durable tool call persistence 不能把 generic audit log 当作主数据源**
   - Given 当前 `ToolRegistry` 已写入 generic `audit_logs`
   - When Story 6.6 落地
   - Then `tool_calls` 必须是独立的一等存储模型和 repository
   - And generic `audit_logs` 可以继续作为通用审计流，但不能替代 `tool_calls` 的查询、关联和状态复盘

4. **Agent run id 必须从 run persistence 边界显式传入 tool execution**
   - Given Story 6.5 已经先创建 durable `agent_runs` 再执行 runtime
   - When runtime 调用 registry 执行工具
   - Then registry/recorder 必须获得真实 `agent_run_id`
   - And 不得用 `request_id` 冒充 `agent_run_id`
   - And 不得让 LLM、请求体、tool arguments 或 prompt 决定 `agent_run_id`

5. **参数摘要和结果摘要必须安全脱敏**
   - Given 工具参数或输出包含 query、prompt、文件内容、路径、token、secret、API key、authorization、cookie、SQL、向量、embedding、provider payload、企业机密全文或超长文本
   - When 生成 `arguments_summary` 或 `result_summary`
   - Then 只保存安全摘要，例如参数键名、结果键名、状态、错误码、长度、hash、计数、safe resource ids
   - And 不保存 raw arguments、raw output、文件内容、chunk text、完整 query、prompt、hidden reasoning、绝对路径或密钥

6. **错误分类必须可复盘**
   - Given 工具调用被拒绝、超时、rate limited、schema validation 失败、权限失败、handler error 或 output validation 失败
   - When 持久化 `tool_calls`
   - Then `status` 和 `error_code` 可区分 `TOOL_PERMISSION_DENIED`、`TOOL_TIMEOUT`、`TOOL_RATE_LIMITED`、`TOOL_INPUT_VALIDATION_FAILED`、`TOOL_OUTPUT_VALIDATION_FAILED`、`TOOL_HANDLER_FAILED`、`TOOL_NOT_REGISTERED`
   - And 失败摘要不能泄露后端异常详情、SQL、路径或工具原始输出

7. **运行失败不能导致已发生工具调用丢失**
   - Given runtime 中一个工具调用已经执行并产生 tool call record
   - When 后续 stepper、runtime、agent run 状态写回或 final answer 失败
   - Then 已写入的 `tool_calls` 记录仍然 durable
   - And `agent_runs.tool_calls_used` 与可查询的 `tool_calls` 数量在成功提交路径保持一致
   - And 如果 tool call record 写入失败，runtime 必须返回结构化失败或受控降级，不能假装工具调用已被审计

8. **架构边界必须保持 Agent core provider/storage neutral**
   - Given 新增 `packages/agent/storage/tool_call_*` 或等价文件
   - When boundary tests 运行
   - Then SQLAlchemy 仍只允许出现在 `packages/agent/storage/*`
   - And `packages/agent/runtime.py`、`registry.py`、`dto.py`、`policies.py`、`exceptions.py` 不能导入 FastAPI、SQLAlchemy、provider SDK、retrieval internals 或 storage repositories
   - And route 不直接调用 tool handlers、storage repositories、SQLAlchemy、LLM/vector/retrieval internals

9. **API 和 README 必须同步说明能力边界**
   - Given Story 6.6 完成
   - When 更新 README
   - Then Build Status、Governed Agent Tools、Storage Model、Current Limits 必须说明 durable `tool_calls` 已完成
   - And `tool event streaming`、Open WebUI function/tool bridge、real LLM-backed planning 和 final answer validation 仍是未完成能力

10. **测试必须覆盖成功、拒绝、失败和脱敏**
    - Given 单元和集成测试运行
    - When 执行 Story 6.6 测试
    - Then 覆盖 tool call repository create/query、migration smoke、registry success durable record、permission denied durable record、schema validation durable record、timeout/rate limit/handler failure durable record、audit recorder storage failure、agent_run_id 传递、metadata redaction、boundary tests 和 README expectations
    - And 测试使用 fake runtime/stepper/tools、SQLite storage fixtures 或 in-memory fakes，不调用真实 LLM、embedding、vector store、PostgreSQL、Redis、MinIO、Open WebUI、网络或外部 provider

## Tasks / Subtasks

- [x] 定义 durable tool call DTO 和 recorder port（AC: 1, 4-6, 8）
  - [x] 在 `packages/agent/dto.py` 或新 `packages/agent/tool_calls.py` 中新增 `ToolCallCreate`、`ToolCallRecord`、`ToolCallQuery` 或等价 DTO。
  - [x] 定义 storage status 字面量：`success`、`denied`、`failure`，与 `ToolInvocationStatus` / `AuditStatus` 映射清晰。
  - [x] 定义 `ToolCallRecorderPort` / `ToolCallRepositoryPort` Protocol，放在 agent core 可访问但 storage-free 的位置。
  - [x] DTO 只允许摘要字段，不包含 raw arguments、raw result、prompt、query、file content 或 provider payload 字段。

- [x] 实现 `tool_calls` storage model、repository 和 migration（AC: 2, 3, 7, 10）
  - [x] 新增 `ToolCallModel(IdMixin, TimestampMixin, Base)`，建议放在 `packages/agent/storage/models.py` 或拆分为 `tool_call_models.py` 后由 `packages/agent/storage/__init__` 汇总。
  - [x] 新增 `ToolCallRepository`，提供 `create_tool_call()`、`list_by_agent_run()`、必要 tenant scoped 查询和 `commit()` / `rollback()`。
  - [x] 新 migration 建议命名 `migrations/versions/20260527_0011_tool_calls.py`，`down_revision` 为 `20260527_0010`。
  - [x] migration 建表字段与 AC2 一致，添加 `ForeignKey("agent_runs.id")`、status check、`latency_ms >= 0` check。
  - [x] 添加索引：`ix_tool_calls_agent_run_id`、`ix_tool_calls_tool_name`、`ix_tool_calls_status_created`、`ix_tool_calls_agent_run_tool_status`、`ix_tool_calls_tenant_user_created`。
  - [x] 确认 `migrations/env.py` 能加载新增 model metadata。

- [x] 将 `agent_run_id` 安全传入 runtime 和 registry（AC: 1, 4, 7, 8）
  - [x] 调整 `AgentRunApplicationService` 的 runtime factory 形态，使其在 create/commit `agent_runs` 后把 `created.id` 传入 runtime。
  - [x] 给 `AgentRuntime` 增加 storage-neutral `agent_run_id` 配置或 init 参数，并在调用 `ToolRegistry.execute()` 时传入。
  - [x] 给 `ToolRegistry.execute()` 增加可选 keyword-only `agent_run_id: str | None`，默认兼容既有 tests；由 Agent runtime 路径强制提供真实 run id。
  - [x] 不修改 `AuthenticatedRequestContext` 来塞入 agent_run_id；request context 仍只表达 request/auth/session。

- [x] 在 Tool Registry 中挂接 durable recorder（AC: 1, 3, 5-7）
  - [x] `ToolRegistry` 接收可选 `tool_call_recorder`，默认可为 no-op，测试可注入 fake recorder。
  - [x] 在未注册、入参非 mapping、schema validation、permission denied、rate limit、timeout、handler failure、output validation failure 和 success/output failure result 路径都记录 durable tool call。
  - [x] 确保 recorder 看到的字段是 `agent_run_id`、context tenant/user/request/trace、tool_name、permission、status、latency、error_code、安全摘要。
  - [x] 如果 recorder 写入失败，返回结构化 `AgentToolError`，例如新增 `TOOL_CALL_AUDIT_FAILED` 或复用明确的 storage error code；不要吞掉 durable audit 失败。
  - [x] 保留现有 `AuditPort` generic audit 行为，但它不是 durable tool call 的唯一证据。

- [x] 实现摘要与脱敏工具（AC: 5, 6, 10）
  - [x] 复用或提炼现有 `_safe_argument_keys()`、`_safe_tool_name()`、`redact_mapping()` 和 Story 6.5 metadata sanitizer 规则，避免重复不一致。
  - [x] `arguments_summary` 至少包含 safe argument keys、argument count、optional stable hash；不得保存 raw values。
  - [x] `result_summary` 至少包含 safe result keys、status、error_code、output count/length/hash 等安全信息；不得保存 raw tool output。
  - [x] 对敏感 key/value、绝对路径、超长字符串、非 JSON 类型和嵌套对象加测试。

- [x] 更新 API dependency assembly（AC: 1, 4, 7, 8）
  - [x] 在 `apps/api/service_dependencies.py` 创建 `ToolCallRepository(session)` 并注入到 `ToolRegistry` / recorder。
  - [x] 保持 `apps/api/routes/agent.py` thin，不导入 storage、tools 或 SQLAlchemy。
  - [x] 确保 tool call recorder 和 `agent_runs` repository 使用一致的 session/commit 策略，避免 run 创建提交后 tool call 写入被后续 rollback 丢失。
  - [x] 如果使用 `auto_commit=True` 的 audit port，不要让 generic audit commit 干扰 tool_call transaction 一致性。

- [x] 扩展测试（AC: 1-10）
  - [x] 新增 `tests/integration/storage/test_tool_call_repositories.py`，覆盖 create/query、agent_run FK、tenant/user scope、status/error_code、safe storage errors。
  - [x] 更新 `tests/integration/storage/test_alembic_migrations.py`，确保 migration 0011 在链路中。
  - [x] 扩展 `tests/unit/agent/test_tool_registry.py`，覆盖每个 registry 分支都会调用 recorder 且摘要安全。
  - [x] 扩展 `tests/unit/agent/test_runtime.py` 或新增 service test，覆盖 `agent_run_id` 从 service -> runtime -> registry -> recorder。
  - [x] 更新 `tests/unit/test_architecture_boundaries.py`，只允许 agent storage 层导入 SQLAlchemy，route 仍禁止 storage/tool adapter wiring。
  - [x] 添加 redaction tests：raw query、prompt、file content、absolute path、token/secret、long string、provider payload 不进入 `arguments_summary` / `result_summary`。

- [x] 更新 README 和 story/sprint 状态（AC: 9）
  - [x] README Build Status 从 Story 6.5 更新到 Story 6.6 durable `tool_calls` persistence。
  - [x] Storage Model 列表增加 `tool_calls`。
  - [x] Current Limits 删除 durable `tool_calls`，保留 tool event streaming、final answer validation、real LLM-backed planning。
  - [x] 实现完成后将本 story 状态改为 `review`，code review 通过后再改 `done`。

- [x] 验证（AC: 1-10）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/agent tests/unit/test_architecture_boundaries.py`
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/storage/test_tool_call_repositories.py tests/integration/storage/test_alembic_migrations.py`
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/api/test_agent_routes.py`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`

### Review Findings

- [x] [Review][Patch] Runtime global timeout can cancel registry execution before durable tool_call is recorded [packages/agent/runtime.py:399]
- [x] [Review][Patch] Recorder failure logging includes raw exception traceback and can leak backend details [packages/agent/registry.py:618]
- [x] [Review][Patch] ToolCall summary DTO/repository accepts and persists unrestricted summary dictionaries [packages/agent/dto.py:150]
- [x] [Review][Patch] ToolCallQuery lacks created_at query support required by AC2 [packages/agent/dto.py:193]
- [x] [Review][Patch] README expectations required by AC10 are not covered by tests [_bmad-output/implementation-artifacts/6-6-tool-call-audit-persistence.md:82]
- [x] [Review][Patch] Configured tool_call_recorder silently skips persistence when agent_run_id is missing [packages/agent/registry.py:593]

## Dev Notes

### Current Repository State

- Git baseline for this story context: `c6d2496 fix(agent): address agent run review findings`.
- Worktree was clean before this story file was created.
- Sprint status auto-selected `6-6-tool-call-audit-persistence` as the first backlog story.
- Story 6.5 completed `POST /agent/run`, `AgentRunApplicationService`, durable `agent_runs`, service-level audit, deterministic provider-neutral stepper assembly and README updates.
- There is currently no durable `tool_calls` table, no `ToolCallModel`, no `ToolCallRepository`, and no first-class tool call query model.
- Current `ToolRegistry` writes generic `AuditEvent(action="agent.tool.execute")`, but those rows are generic `audit_logs`, not the required durable tool call persistence model.

### Existing Patterns To Reuse

- Storage models use `packages.data.storage.base.Base`, `IdMixin`, `TimestampMixin`, `generate_uuid()`, SQLAlchemy 2.x typed `Mapped` / `mapped_column`, and safe repository errors.
- `AgentRunRepository` is the closest storage pattern for tenant/user scoped create/update/query and safe `AgentRunError` mapping.
- `AuditLogRepository` / `SqlAlchemyAuditPort` show existing audit persistence, but Story 6.6 must create separate `tool_calls` persistence.
- `ToolRegistry.execute()` already centralizes unregistered tools, input validation, permission, rate limit, timeout, handler failures, output validation, result status mapping and generic audit.
- `AgentRuntime.run()` is the only runtime path that calls `ToolRegistry.execute()`; this is the right place to pass `agent_run_id` after Story 6.5 creates the run.
- `AgentRunApplicationService` already creates and commits `agent_runs` before runtime execution; extend that boundary instead of re-creating run state elsewhere.
- `packages.common.logging.redact_mapping()` and existing registry/runtime metadata helpers already encode important redaction rules. Reuse or consolidate them instead of inventing looser summary logic.

### Architecture Requirements

- This story spans Application Service Layer, Agent domain/application boundary, and Storage Layer. It should not add new FastAPI behavior beyond dependency assembly and README/API documentation.
- `packages/agent/storage/*` may import SQLAlchemy. Agent runtime/registry/dto/policies/exceptions must stay framework/provider/storage neutral.
- Tool call persistence must be backend-controlled; LLM output, prompt text, request metadata, or tool arguments cannot choose tenant/user/agent_run_id/permission.
- `tool_calls.agent_run_id` must refer to durable `agent_runs.id`, not request_id.
- Do not rely on prompt instructions for audit, permissions, status, retention, or redaction.
- Do not add vendor-backed LLM planning, LangChain/LangGraph/LlamaIndex/Haystack, Open WebUI function bridge, or Agent SSE streaming in this story.

### Current UPDATE File Notes

- `packages/agent/dto.py`: currently contains `ToolDefinition`, `ToolExecutionResult`, `ToolInvocationStatus`, `AgentRunCommand`, `AgentRunCreate`, `AgentRunUpdate`, `AgentRunRecord`, `AgentRunResponse`. Add tool-call DTOs carefully without mixing raw tool payload fields into public response models.
- `packages/agent/registry.py`: currently has all tool execution branches and generic audit. This is the primary UPDATE file for durable tool-call recording. Preserve existing audit tests and do not weaken timeout/rate-limit/permission behavior.
- `packages/agent/runtime.py`: currently calls `self._registry.execute(name=decision.tool_name, arguments=decision.arguments, context=context)`. Update this call to pass a real agent_run_id supplied by service assembly. Preserve max_steps, max_tool_calls, timeout cancellation and repeated action semantics.
- `packages/agent/service.py`: current `RuntimeFactory = Callable[[AgentRunConfig], AgentRuntimePort]`. It likely needs to become `Callable[[AgentRunConfig, str], AgentRuntimePort]` or equivalent so the created `agent_runs.id` reaches runtime.
- `apps/api/service_dependencies.py`: currently builds `AgentRunApplicationService(repository=AgentRunRepository(session), runtime_factory=lambda config: AgentRuntime(registry=ToolRegistry(audit=audit), ...))`. Add `ToolCallRepository` / recorder wiring here, not in the route.
- `packages/agent/storage/models.py`: currently only defines `AgentRunModel`. Add `ToolCallModel` here or import it through storage package metadata so Alembic sees it.
- `packages/agent/storage/repositories.py`: currently only defines `AgentRunRepository`. Either add `ToolCallRepository` here or split by file; keep safe storage errors and tenant scoped query patterns.
- `migrations/env.py`: already imports `packages.agent.storage.models`; if model stays in that module no env change is needed, otherwise import the new model module.
- `tests/unit/test_architecture_boundaries.py`: already allows SQLAlchemy only under `packages/agent/storage` and checks `apps/api/routes/agent.py` stays thin. Extend only if new files require it.
- `README.md`: currently says durable `tool_calls` is not complete. Update when implementation is finished.

### Previous Story Intelligence

- Story 6.1 established that all tools must go through `ToolRegistry`; do not let Agent call arbitrary Python functions or bypass schema/permission/timeout/rate limit.
- Story 6.2 established that `rag_search` output is a safe observation with citation identifiers and safe source summaries only. Do not persist raw retrieval query, chunk text, ACL maps or full metadata.
- Story 6.3 established that `calculator` is deterministic and `file_reader` never returns absolute paths or full sensitive content. The durable summary layer must preserve those guarantees.
- Story 6.4 established runtime limits:
  - `max_steps` is checked before the next stepper call.
  - `max_tool_calls` is checked before registry execution.
  - global timeout spans model/tool orchestration.
  - repeated action detection stops before the triggering tool call.
  - runtime audit metadata stores safe counts, tool names, argument keys and action hashes only.
- Story 6.5 established run persistence:
  - `agent_runs` is created as `running` before runtime starts.
  - runtime result writes back `completed`, `stopped` or `failed`.
  - unexpected runtime exceptions are written as failed runs when possible.
  - metadata sanitizer rejects unsafe string values.
  - durable `tool_calls` was explicitly out of scope and is now the target.

### Git Intelligence

- `315346f feat(agent): add governed runtime limits` introduced `AgentRuntime`, repeated action detection and runtime audit.
- `1feabec fix(agent): address runtime review findings` hardened timeout cancellation, stepper validation, registry exceptions and audit failure logging.
- `cd47ee6 feat(agent): add agent run api persistence` added `/agent/run`, `agent_runs`, service lifecycle persistence and tests.
- `c6d2496 fix(agent): address agent run review findings` fixed run persistence before runtime, failed runtime writeback, durable service audit commit and metadata sanitization.

### Suggested Implementation Shape

The exact API can evolve if local tests reveal a cleaner shape, but preserve these boundaries:

```python
class ToolCallCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_run_id: str
    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    tool_name: str
    permission: str | None
    status: Literal["success", "denied", "failure"]
    latency_ms: float
    error_code: str | None = None
    arguments_summary: dict[str, object] = Field(default_factory=dict)
    result_summary: dict[str, object] = Field(default_factory=dict)
```

```python
class ToolCallRecorderPort(Protocol):
    async def record_tool_call(self, record: ToolCallCreate) -> None:
        ...
```

```python
async def execute(
    self,
    *,
    name: str,
    arguments: object,
    context: AuthenticatedRequestContext,
    agent_run_id: str | None = None,
) -> ToolExecutionResult:
    ...
```

Agent runtime should receive the service-created run id:

```python
runtime_factory=lambda config, agent_run_id: AgentRuntime(
    registry=ToolRegistry(audit=audit, tool_call_recorder=tool_call_recorder),
    stepper=DeterministicAgentStepper(),
    audit=audit,
    config=config,
    agent_run_id=agent_run_id,
)
```

### Implementation Boundaries

- Do not store raw tool arguments or outputs anywhere in `tool_calls`.
- Do not change tool permissions, allowlist, retrieval ACL filtering, rate limits or timeout semantics.
- Do not add `GET /agent/runs/{id}/tool-calls` unless the implementation already has a documented internal query need and tests; the story only requires persistence and repository/query support.
- Do not use `audit_logs` as the only durable tool call record.
- Do not let failure to write generic audit suppress durable `tool_calls`; durable `tool_calls` is the stronger requirement in this story.
- Do not introduce new external dependencies.

### Latest Technical Information

- No dependency upgrade is required for Story 6.6. Use the repository-pinned FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic, pytest, ruff and mypy stack.
- Use SQLAlchemy 2.x typed ORM style already present in `AgentRunModel`; use Alembic `op.create_table`, `op.create_index`, `op.drop_index`, `op.drop_table` style already present in migration `20260527_0010_agent_runs.py`.
- Keep tests on fake/in-memory/SQLite paths. Do not add network or real provider calls.

### References

- `_bmad-output/planning-artifacts/epics.md#Story-6.6-Tool-Call-Audit-Persistence`
- `_bmad-output/planning-artifacts/epics.md#Epic-6-受控-Agent-工具执行`
- `_bmad-output/planning-artifacts/epics.md#FR23`
- `_bmad-output/planning-artifacts/epics.md#FR28`
- `_bmad-output/planning-artifacts/architecture.md#Tool-Security`
- `_bmad-output/planning-artifacts/architecture.md#Data-Flow`
- `_bmad-output/planning-artifacts/architecture.md#Requirements-to-Structure-Mapping`
- `project-context.md#12-Agent-规则`
- `_bmad-output/implementation-artifacts/6-1-tool-registry-与工具治理模型.md`
- `_bmad-output/implementation-artifacts/6-2-rag-search-工具.md`
- `_bmad-output/implementation-artifacts/6-3-calculator-与受限-file-reader-工具.md`
- `_bmad-output/implementation-artifacts/6-4-react-agent-runtime-限制与重复动作检测.md`
- `_bmad-output/implementation-artifacts/6-5-agent-run-api-与-agent-run-persistence.md`
- `packages/agent/registry.py`
- `packages/agent/runtime.py`
- `packages/agent/service.py`
- `packages/agent/dto.py`
- `packages/agent/exceptions.py`
- `packages/agent/storage/models.py`
- `packages/agent/storage/repositories.py`
- `packages/data/storage/audit_models.py`
- `packages/data/storage/audit_repositories.py`
- `packages/common/audit.py`
- `packages/common/context.py`
- `apps/api/service_dependencies.py`
- `apps/api/routes/agent.py`
- `migrations/env.py`
- `migrations/versions/20260527_0010_agent_runs.py`
- `tests/unit/agent/test_tool_registry.py`
- `tests/unit/agent/test_runtime.py`
- `tests/unit/agent/test_agent_run_service.py`
- `tests/integration/storage/test_agent_run_repositories.py`
- `tests/integration/storage/test_alembic_migrations.py`
- `tests/unit/test_architecture_boundaries.py`
- `README.md#Governed-Agent-Tools`
- `README.md#Current-Limits`

## Validation Checklist

Validation Result: PASS（2026-06-08T14:05:00+08:00）

- [x] Story 明确只实现 durable `tool_calls` persistence，不实现 tool event streaming、Open WebUI function/tool bridge、real LLM-backed planning 或 final answer validation。
- [x] Acceptance Criteria 覆盖独立 `tool_calls` 表、agent_run_id 关联、安全摘要、错误分类、失败持久化、架构边界、README 同步和测试。
- [x] Tasks 给出 DTO/port、storage model/repository/migration、runtime/registry/service wiring、redaction、dependency assembly、tests 和验证命令。
- [x] Dev Notes 明确当前 code state、UPDATE files、前序 story lessons、现有 patterns、风险点和非目标。
- [x] 明确测试默认不调用真实 LLM、embedding、vector store、PostgreSQL、Redis、MinIO、Open WebUI、网络或外部 provider。
- [x] 明确 DB/audit/API 不保存 prompt、hidden reasoning、raw tool args/output、文件内容、query 原文、token、secret、绝对路径或企业机密全文。

## Change Log

- 2026-06-08: Created comprehensive Story 6.6 developer context for durable tool call audit persistence.
- 2026-06-08: Implemented durable `tool_calls` persistence, recorder wiring, tests, migration, and README updates.
- 2026-06-08: Addressed code review findings for timeout durability, audit log safety, summary validation, created_at queries, README expectation tests, and missing agent_run_id fail-closed behavior.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- 2026-06-08T15:51:55+08:00: `.venv\Scripts\python.exe -m pytest tests/unit/agent tests/unit/test_architecture_boundaries.py -q` -> 152 passed.
- 2026-06-08T15:51:55+08:00: `.venv\Scripts\python.exe -m pytest tests/integration/storage/test_tool_call_repositories.py tests/integration/storage/test_alembic_migrations.py -q` -> 5 passed.
- 2026-06-08T15:51:55+08:00: `.venv\Scripts\python.exe -m pytest tests/integration/api/test_agent_routes.py -q` -> 4 passed.
- 2026-06-08T15:51:55+08:00: `.venv\Scripts\python.exe -m pytest tests/unit -q` -> 664 passed.
- 2026-06-08T15:51:55+08:00: `.venv\Scripts\python.exe -m pytest -q` -> 789 passed.
- 2026-06-08T15:51:55+08:00: `.venv\Scripts\python.exe -m ruff check .` -> passed.
- 2026-06-08T15:51:55+08:00: `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed.

### Completion Notes List

- Added storage-neutral tool call DTOs and recorder/repository protocols in agent core.
- Added `tool_calls` SQLAlchemy model, repository, Alembic migration, indexes, FK to `agent_runs`, and tenant/user scoped query paths.
- Passed service-created `agent_run_id` through `AgentRunApplicationService` -> `AgentRuntime` -> `ToolRegistry.execute()`.
- Wired `ToolRegistry` to record durable tool calls for success, denied, validation failure, rate limit, timeout, handler failure, output validation failure, and structured tool failure outputs.
- Kept generic `audit_logs` as a separate audit stream; durable `tool_calls` is now the first-class tool call source of truth.
- Added safe argument/result summaries that store keys/counts/status/error codes without raw arguments, raw output, query text, prompt text, file content, paths, secrets, provider payloads, or handler exception details.
- Wired API dependency assembly to inject `ToolCallRepository(session)` into `ToolRegistry`; `apps/api/routes/agent.py` remains thin.
- Updated README Build Status, Governed Agent Tools, Storage Model, and Current Limits for Story 6.6.

### File List

- README.md
- _bmad-output/implementation-artifacts/6-6-tool-call-audit-persistence.md
- _bmad-output/implementation-artifacts/sprint-status.yaml
- apps/api/service_dependencies.py
- migrations/versions/20260527_0011_tool_calls.py
- packages/agent/dto.py
- packages/agent/exceptions.py
- packages/agent/registry.py
- packages/agent/runtime.py
- packages/agent/service.py
- packages/agent/storage/models.py
- packages/agent/storage/repositories.py
- tests/integration/storage/test_alembic_migrations.py
- tests/integration/storage/test_tool_call_repositories.py
- tests/unit/agent/test_agent_run_service.py
- tests/unit/agent/test_dto.py
- tests/unit/agent/test_runtime.py
- tests/unit/agent/test_tool_registry.py

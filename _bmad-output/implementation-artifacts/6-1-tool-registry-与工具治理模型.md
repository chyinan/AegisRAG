---
baseline_commit: e78c825
---

# Story 6.1: Tool Registry 与工具治理模型

Status: done

生成时间：2026-06-08T05:44:17+08:00

## Story

As a 平台工程师,
I want 所有 Agent 工具通过 Tool Registry 注册和校验,
so that LLM 不能绕过后端策略直接调用任意 Python 函数。

## Acceptance Criteria

1. **Tool Definition 契约完整且结构化**
   - Given 开发者定义一个 Agent 工具
   - When 注册到 Tool Registry
   - Then tool definition 必须包含 `name`、`description`、`input_schema`、`output_schema`、`permission`、`timeout_seconds`、`rate_limit`、`handler`
   - And `input_schema` 和 `output_schema` 必须基于 Pydantic v2 `BaseModel` 或等价结构化 schema，不接受自然语言 schema、裸 dict handler 约定或 import path 字符串
   - And `name`、`description`、`permission` 不得为空，`timeout_seconds` 必须为有限正数，`rate_limit` 必须是显式结构化配置

2. **Registry 只允许显式注册工具，拒绝重复和未知工具**
   - Given Tool Registry 已注册一个工具
   - When 再次注册同名工具
   - Then 必须拒绝并返回稳定领域错误 `TOOL_ALREADY_REGISTERED`
   - And 不覆盖原有 handler
   - Given Agent 请求调用未注册工具
   - When runtime 查询或执行 registry
   - Then 调用被拒绝并返回 `TOOL_NOT_REGISTERED`
   - And 记录审计事件，handler 不会执行

3. **输入 schema 校验在 handler 前执行**
   - Given 工具入参不符合 `input_schema`
   - When Tool Registry 执行该工具
   - Then 调用被拒绝并返回结构化 validation error `TOOL_INPUT_VALIDATION_FAILED`
   - And handler 不会执行
   - And 审计 metadata 只能包含安全摘要，例如参数 key、错误字段名、error_code，不记录完整工具参数或企业敏感全文

4. **输出 schema 校验在 handler 后执行**
   - Given 工具 handler 返回不符合 `output_schema` 的数据
   - When Tool Registry 校验结果
   - Then 调用失败并返回 `TOOL_OUTPUT_VALIDATION_FAILED`
   - And 记录失败审计事件
   - And 不把未校验的原始结果返回给 Agent observation

5. **权限校验由后端 AuthContext 执行**
   - Given tool definition 声明 `permission`
   - When `AuthenticatedRequestContext.auth.permissions` 不包含该 permission
   - Then Tool Registry 必须拒绝执行并返回 `TOOL_PERMISSION_DENIED`
   - And handler 不会执行
   - And 不允许 prompt、LLM 输出或工具参数扩大权限

6. **timeout 与 rate limit 在 registry 层有可测试治理模型**
   - Given tool definition 声明 timeout
   - When handler 超过 `timeout_seconds`
   - Then Tool Registry 停止等待并返回 `TOOL_TIMEOUT`
   - And 记录 latency、status、error_code
   - Given 同一 tenant/user/tool 超过 `rate_limit`
   - When 再次执行工具
   - Then Tool Registry 返回 `TOOL_RATE_LIMITED`
   - And handler 不会执行
   - And MVP 可使用可替换的 in-memory limiter 或 port，后续 Story 6.6/生产部署可替换为持久化或 Redis 实现

7. **审计事件覆盖允许、拒绝、失败路径**
   - Given 任意 registry 执行路径结束
   - When 调用成功、未注册、schema validation 失败、权限拒绝、rate limited、timeout 或 handler error
   - Then 通过 `packages.common.audit.AuditPort` 记录审计事件
   - And 事件包含 `request_id`、`trace_id`、`tenant_id`、`user_id`、`action`、`resource`、`latency_ms`、`status`、`error_code`
   - And 审计不得记录 API key、access token、完整 tool args、完整 tool result、文件绝对路径、prompt 或企业机密全文

8. **测试与边界覆盖**
   - Given 单元测试运行
   - When 执行 Tool Registry 测试
   - Then 覆盖 definition validation、duplicate registration、unknown tool、input validation、output validation、permission denied、timeout、rate limit、handler error、安全 audit metadata
   - And 测试使用 fake handler、fake/in-memory audit、fake/in-memory limiter，不调用真实 LLM、retrieval、文件系统、网络、Redis、PostgreSQL、Open WebUI 或外部 provider
   - And architecture boundary 测试确认 `packages/agent` 不导入 FastAPI、SQLAlchemy、Redis、MinIO、OpenAI/Qwen/DeepSeek/Ollama/vLLM、LangChain/LangGraph 或 vector store adapters

9. **文档同步**
   - Given Story 6.1 完成
   - When README 描述当前能力和限制
   - Then README 必须说明 Tool Registry 基础治理是否已实现、仍未实现具体 tools、Agent Runtime、`/agent/run` 和 tool call persistence
   - And `.env.example` 或配置文档必须列出新增的 tool timeout / rate limit 默认配置；如果未新增配置，最终回复必须说明理由

## Tasks / Subtasks

- [x] 设计 agent package 基础结构（AC: 1, 8）
  - [x] 新增 `packages/agent/__init__.py`，导出 registry、DTO、exceptions 中的公共类型。
  - [x] 新增 `packages/agent/dto.py`，定义 `ToolDefinition`、`ToolExecutionResult`、`ToolRateLimit`、`ToolInvocationStatus` 或等价 DTO。
  - [x] 新增 `packages/agent/exceptions.py`，所有预期错误继承 `packages.common.errors.DomainError` 并使用稳定 code。
  - [x] 新增 `packages/agent/registry.py`，实现注册、查询、执行和治理流程。
  - [x] 新增 `packages/agent/policies.py` 或在 registry 内提供最小权限检查，基于 `AuthContext.permissions` 的后端策略，不使用 prompt。

- [x] 实现 Tool Definition 验证（AC: 1, 2）
  - [x] 校验 `name` 建议只允许 snake_case / lower identifier，例如 `rag_search`、`calculator`、`file_reader`；拒绝空白、路径、点号 import 风格或包含空格的名称。
  - [x] 校验 `description` 为非空人类说明，但不得用于权限判断。
  - [x] 校验 `permission` 为非空权限字符串，例如 `agent:tool:demo`；权限匹配使用后端集合判断。
  - [x] 校验 `input_schema`、`output_schema` 是 Pydantic v2 model class，schema 可通过 `model_json_schema()` 导出给未来 Agent Runtime。
  - [x] 校验 `timeout_seconds` 有限且大于 0。
  - [x] 校验 `rate_limit` 包含 `max_calls`、`window_seconds`，均为有限正数。
  - [x] 重复注册同名工具返回 `TOOL_ALREADY_REGISTERED`，不得覆盖已注册 definition。

- [x] 实现 registry 执行流程（AC: 2-7）
  - [x] `ToolRegistry.execute(name, arguments, context)` 或等价方法必须显式接收 `AuthenticatedRequestContext`。
  - [x] 顺序必须是：查 registry -> input schema validation -> permission check -> rate limit check -> timeout wrapper -> handler -> output schema validation -> audit -> result。
  - [x] 未注册、validation、permission、rate limit、timeout 均不得执行 handler。
  - [x] handler 应是显式注册 callable，不支持从字符串动态 import，不支持按 LLM 给出的 Python 函数名反射调用。
  - [x] timeout 使用 `asyncio.wait_for` 或等价机制；在 Python 3.11 中捕获内置 `TimeoutError` 并映射为 `TOOL_TIMEOUT`。
  - [x] output validation 失败不得把原始 handler 返回值暴露给 Agent。

- [x] 实现 rate limit port 或最小可替换 limiter（AC: 6, 8）
  - [x] 建议定义 `ToolRateLimiter` Protocol，方法类似 `async def acquire(key, limit) -> ToolRateLimitDecision`。
  - [x] 提供 `InMemoryToolRateLimiter` 用于本 story 单测，key 至少包含 tenant_id、user_id、tool_name。
  - [x] limiter 使用可注入 clock，避免测试依赖真实等待。
  - [x] 不在本 story 引入 Redis limiter、数据库持久化或分布式限流。

- [x] 实现安全审计摘要（AC: 3, 4, 7）
  - [x] 复用 `AuditEvent`、`AuditResource`、`AuditStatus`、`AuditPort`。
  - [x] action 建议使用 `agent.tool.execute` 或 `agent.tool_call`，resource 使用 `AuditResource(type="tool", id=tool_name)`。
  - [x] metadata 只能保存 `tool_name`、`permission`、`argument_keys`、`result_keys`、`rate_limit` 摘要、`timeout_seconds`、`error_fields`、`status` 等安全字段。
  - [x] 不保存完整 arguments/result、prompt、query、answer、document content、file path、token、secret。
  - [x] 审计失败不得伪造成工具成功；如遵循现有 RAG 模式允许 audit failure 不阻塞业务，必须记录结构化日志且测试覆盖。

- [x] 更新配置（AC: 6, 9）
  - [x] 在 `packages/common/config.py` 增加 `tool_default_timeout_seconds`、`tool_default_rate_limit_max_calls`、`tool_default_rate_limit_window_seconds` 或等价配置。
  - [x] 在 `.env.example` 增加对应环境变量。
  - [x] 默认值必须保守、可测试，且不能硬编码在 prompt 或 Agent Runtime 中。

- [x] 更新测试（AC: 1-8）
  - [x] 新增 `tests/unit/agent/test_tool_registry.py` 覆盖注册、查询、执行和全部错误路径。
  - [x] 新增 `tests/unit/agent/test_dto.py` 或同文件覆盖 definition/rate limit DTO validation。
  - [x] 新增或扩展 `tests/unit/test_architecture_boundaries.py`，加入 `packages/agent` 的 forbidden import 检查。
  - [x] 使用 Pydantic fake schemas，例如 `DemoToolInput`、`DemoToolOutput`；使用 async fake handlers 记录是否执行。
  - [x] 使用 `InMemoryAuditPort` 断言成功/拒绝/失败事件，且 metadata 已脱敏。
  - [x] 使用 fake clock 或短 timeout 测试 timeout/rate limit，不使用真实 sleep 拉长测试。

- [x] 更新 README（AC: 9）
  - [x] 在 Build Status 或 Current Limits 中说明 Story 6.1 完成后的真实边界。
  - [x] 在 Security/Governance 或 Agent roadmap 描述 Tool Registry 已提供 schema、permission、timeout、rate limit、audit 基础时，必须明确具体 tools、runtime、API、storage 仍由后续 stories 实现。
  - [x] 更新本地配置说明，列出新增 `TOOL_*` 配置。

- [x] 验证（AC: 1-9）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/agent tests/unit/test_architecture_boundaries.py`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`

### Review Findings

- [x] [Review][Patch] Timeout governance does not reliably contain non-cooperative handlers [packages/agent/dto.py:102]
- [x] [Review][Patch] Output schema validation can be bypassed by pre-constructed Pydantic model instances [packages/agent/registry.py:358]
- [x] [Review][Patch] Unknown-tool lookup via `get()` is rejected without audit evidence [packages/agent/registry.py:104]
- [x] [Review][Patch] Unknown tool names and argument keys can enter audit records without safe-name normalization [packages/agent/registry.py:364]
- [x] [Review][Patch] Non-mapping tool arguments can escape as raw runtime errors before structured validation and audit [packages/agent/registry.py:123]
- [x] [Review][Patch] Audit backend failures are logged without the original exception traceback [packages/agent/registry.py:327]
- [x] [Review][Patch] Output/input extra-field contract is not enforced or tested explicitly [packages/agent/registry.py:143]

## Dev Notes

### Current Repository State

- Git baseline for this story context: `e78c825 chore(bmad): track customization resolver`.
- Worktree was clean before this story file was created.
- Sprint status auto-selected `6-1-tool-registry-与工具治理模型` as the first backlog story.
- `packages/agent` does not currently exist in the codebase. This story is expected to create the package foundation.
- Existing `packages/rag/streaming.py` already reserves `tool_call` and `tool_result` event payload types for later workflows, but this story should not wire SSE tool events.
- Existing API routes do not include `/agent/run`; route/API work belongs to Story 6.5, not this story.
- Existing storage migrations do not include `agent_runs` or `tool_calls`; persistence belongs to Story 6.5 and Story 6.6, not this story.

### Existing Patterns To Reuse

- `packages.common.context.AuthenticatedRequestContext` carries `request_id`, `trace_id`, optional `session_id`, and `AuthContext`. Registry execution must receive this context explicitly.
- `packages.auth.context.AuthContext` is a frozen Pydantic v2 model with `user_id`, `tenant_id`, `roles`, `department`, `permissions`.
- `packages.auth.policies` shows existing permission helpers using backend permission sets. Follow this style; do not put permission rules in prompt text.
- `packages.common.audit.AuditEvent`, `AuditResource`, `AuditStatus`, `AuditPort`, and `InMemoryAuditPort` are already used by ingestion, retrieval, RAG query, source resolver, and memory services.
- `packages.common.errors.DomainError` is the common expected-error base with stable `code`, safe `message`, redacted `details`, and `status_code`.
- `packages.common.logging.redact_mapping()` redacts sensitive keys and values including tokens, prompts, content, vectors, file paths, SQL and provider raw payloads.
- Existing service tests prefer deterministic fake providers and in-memory ports. Follow this approach for fake tool handlers, fake audit, and fake rate limiting.
- `tests/unit/test_architecture_boundaries.py` already enforces route thinness and package import boundaries; extend it so `packages/agent` remains framework/provider/infrastructure-free.

### Architecture Requirements

- This story belongs to Domain/Application Service foundation for `packages/agent`.
- Do not import FastAPI, SQLAlchemy, Redis, MinIO, httpx, OpenAI/Qwen/DeepSeek/Ollama/vLLM, LangChain, LangGraph, vector store adapters, retrieval services, RAG services, or storage repositories inside the registry core.
- Tool Registry is the only allowed execution gateway for future Agent tools. The future Agent Runtime may choose tool names, but it must not call arbitrary Python functions or import modules dynamically.
- Permission checks must use `AuthenticatedRequestContext.auth.permissions` and backend policy code. The LLM cannot decide whether a user has permission.
- This story should produce a reusable governance model for later `rag_search`, `calculator`, `file_reader`, ReAct runtime, `/agent/run`, and tool call persistence.
- Audit in this story is behavioral evidence through `AuditPort`; durable `tool_calls` storage is explicitly deferred.

### Suggested File Structure

```text
packages/
  agent/
    __init__.py
    dto.py
    exceptions.py
    policies.py
    registry.py
tests/
  unit/
    agent/
      __init__.py
      test_dto.py
      test_tool_registry.py
```

### Error Code Contract

Use stable codes so API and future runtime can map errors predictably:

```text
TOOL_ALREADY_REGISTERED
TOOL_NOT_REGISTERED
TOOL_INPUT_VALIDATION_FAILED
TOOL_OUTPUT_VALIDATION_FAILED
TOOL_PERMISSION_DENIED
TOOL_RATE_LIMITED
TOOL_TIMEOUT
TOOL_HANDLER_FAILED
```

Recommended HTTP-style status codes on DomainError:

```text
TOOL_NOT_REGISTERED -> 404
TOOL_ALREADY_REGISTERED -> 409
TOOL_INPUT_VALIDATION_FAILED -> 422
TOOL_OUTPUT_VALIDATION_FAILED -> 502
TOOL_PERMISSION_DENIED -> 403
TOOL_RATE_LIMITED -> 429
TOOL_TIMEOUT -> 504
TOOL_HANDLER_FAILED -> 502
```

### Implementation Boundaries

- Do not implement real `rag_search`, `calculator`, `file_reader`, or `web_search`; only fake/demo handlers inside tests.
- Do not implement ReAct Agent Runtime, Planner-Executor, LangGraph-style state graph, repeated action detection, final answer validation, or max_steps/max_tool_calls.
- Do not add `/agent/run` route or Open WebUI tool bridge.
- Do not add database migrations or SQLAlchemy models for `agent_runs` or `tool_calls`.
- Do not call retrieval service, RAG service, LLM provider, embedding provider, vector store, file system, network, Redis, PostgreSQL, MinIO, Docker, or Open WebUI.
- Do not add LangChain/LangGraph or other Agent framework dependencies.
- Do not log or audit raw tool arguments/results. Store key summaries and counts only.
- Do not support dynamic import, eval, exec, reflection by Python function name, or arbitrary callables supplied by LLM output.

### Latest Technical Information

- Project dependency baseline already pins `pydantic>=2.13.4,<3`; use Pydantic v2 APIs. For schema export, use model classes and `model_json_schema()` rather than Pydantic v1 `schema()` patterns.
- Python 3.11 `asyncio.wait_for()` raises built-in `TimeoutError` on timeout. Catch that and map it to `TOOL_TIMEOUT`.
- Do not upgrade dependencies in this story unless an existing test requires it; the tool registry can be implemented with the current standard library and existing dependencies.

### References

- `_bmad-output/planning-artifacts/epics.md#Story-6.1-Tool-Registry-与工具治理模型`
- `_bmad-output/planning-artifacts/epics.md#Epic-6-受控-Agent-工具执行`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-25`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-26`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-27`
- `_bmad-output/planning-artifacts/architecture.md#Tool-Security`
- `_bmad-output/planning-artifacts/architecture.md#Data-Flow`
- `_bmad-output/planning-artifacts/architecture.md#Requirements-to-Structure-Mapping`
- `project-context.md`
- `packages/common/audit.py`
- `packages/common/context.py`
- `packages/common/errors.py`
- `packages/common/logging.py`
- `packages/auth/context.py`
- `packages/auth/policies.py`
- `tests/unit/test_architecture_boundaries.py`
- `README.md#Current-Limits`
- `pyproject.toml`
- `https://docs.pydantic.dev/latest/concepts/models/`
- `https://docs.python.org/3.11/library/asyncio-task.html#asyncio.wait_for`

## Validation Checklist

Validation Result: PASS（2026-06-08T05:44:17+08:00）

- [x] Story 明确只实现 Tool Registry 与治理模型，不实现具体 tools、Agent Runtime、API、SSE wiring 或持久化。
- [x] Acceptance Criteria 覆盖 definition、schema、unknown tool、duplicate registration、input/output validation、permission、timeout、rate limit、audit、测试和文档。
- [x] Tasks 给出具体文件结构、DTO、exceptions、registry、limiter、audit、config、tests、README 和验证命令。
- [x] Dev Notes 明确现有公共抽象、当前代码状态、架构边界、错误码、非目标和安全审计规则。
- [x] 明确测试默认不调用真实 provider、retrieval、文件系统、网络、DB、Redis、MinIO、Docker 或 Open WebUI。
- [x] 明确 audit metadata 不保存完整 arguments/result、prompt、query、answer、document content、file path、token 或 secret。

## Change Log

- 2026-06-08: Created comprehensive Story 6.1 developer context for governed Tool Registry foundation.
- 2026-06-08: Implemented governed Tool Registry foundation with DTO validation, explicit registration, permission checks, timeout/rate limit governance, safe audit summaries, config, tests, and README updates.
- 2026-06-08: Addressed code review findings for timeout containment, output revalidation, audited lookup, safe audit summaries, structured argument validation, audit traceback logging, and extra-field validation.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- 2026-06-08T06:00: Red test run failed as expected because `packages.agent` did not exist.
- 2026-06-08T06:40: Validation passed:
  - `.venv\Scripts\python.exe -m pytest tests/unit/agent tests/unit/test_architecture_boundaries.py` -> 45 passed.
  - `.venv\Scripts\python.exe -m pytest tests/unit` -> 556 passed.
  - `.venv\Scripts\python.exe -m ruff check .` -> passed.
  - `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed.
- 2026-06-08T07:12: Post-review validation passed:
  - `.venv\Scripts\python.exe -m pytest tests/unit/agent tests/unit/test_architecture_boundaries.py` -> 53 passed.
  - `.venv\Scripts\python.exe -m pytest tests/unit` -> 564 passed.
  - `.venv\Scripts\python.exe -m ruff check .` -> passed.
  - `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed.

### Implementation Plan

- Create framework-free `packages.agent` foundation with DTOs, stable domain errors, backend permission policy, registry execution gateway, and replaceable in-memory rate limiter.
- Keep execution order explicit: registered tool lookup, input schema validation, backend permission check, rate limit, timeout-wrapped handler, output schema validation, safe audit, structured result.
- Cover denial/failure paths with tests that prove handlers are not called and audit metadata contains only safe summaries.

### Completion Notes List

- Implemented `ToolDefinition`, rate limit DTOs, `ToolExecutionResult`, and stable `AgentToolError` codes.
- Implemented `ToolRegistry` with duplicate/unknown tool handling, Pydantic input/output validation, permission denial, timeout mapping, handler error wrapping, rate limiting, and safe audit events.
- Added injectable `ToolRateLimiter` protocol and deterministic `InMemoryToolRateLimiter` for tests.
- Added `TOOL_DEFAULT_TIMEOUT_SECONDS`, `TOOL_DEFAULT_RATE_LIMIT_MAX_CALLS`, and `TOOL_DEFAULT_RATE_LIMIT_WINDOW_SECONDS` settings and `.env.example` entries.
- Updated README to state Story 6.1 is implemented while concrete tools, Agent Runtime, `/agent/run`, SSE tool events, and persistence remain future work.
- Addressed code review findings with async-only handler definitions, timeout stop-waiting behavior for cancellation-suppressing handlers, output revalidation for constructed Pydantic models, audited registry lookups, safe audit names/keys, structured non-mapping argument rejection, traceback-preserving audit failure logs, and explicit extra-field rejection.

### File List

- `.env.example`
- `README.md`
- `_bmad-output/implementation-artifacts/6-1-tool-registry-与工具治理模型.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `packages/agent/__init__.py`
- `packages/agent/dto.py`
- `packages/agent/exceptions.py`
- `packages/agent/policies.py`
- `packages/agent/registry.py`
- `packages/common/config.py`
- `tests/unit/agent/__init__.py`
- `tests/unit/agent/test_dto.py`
- `tests/unit/agent/test_tool_registry.py`
- `tests/unit/common/test_config.py`
- `tests/unit/test_architecture_boundaries.py`

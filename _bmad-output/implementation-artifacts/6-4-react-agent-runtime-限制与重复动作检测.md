---
baseline_commit: c8665e9
---

# Story 6.4: ReAct Agent Runtime 限制与重复动作检测

Status: review

生成时间：2026-06-08T11:56:18+08:00

## Story

As a 平台负责人,
I want Agent Runtime 有明确步数、工具次数、timeout 和重复动作限制,
so that Agent 不会无限循环、越权或失控消耗资源。

## Acceptance Criteria

1. **Agent Runtime 配置必须结构化并来自后端配置**
   - Given 用户发起 Agent run
   - When runtime 初始化
   - Then run 配置包含 `max_steps`、`max_tool_calls`、`timeout_seconds` 和 `repeated_action_threshold`
   - And 默认值来自 `packages.common.config.AppSettings` / 显式装配参数，不硬编码在 prompt 中
   - And 配置值必须经过 Pydantic 校验：`max_steps > 0`、`max_tool_calls >= 0`、`timeout_seconds > 0`、`repeated_action_threshold > 0`

2. **Runtime 只能通过受控 stepper 和 Tool Registry 执行 ReAct 循环**
   - Given runtime 需要模型决定下一步
   - When 执行 ReAct step
   - Then 只能通过注入的 `AgentStepper` / `AgentModelPort` 协议获得结构化 action
   - And action 只能是 `tool_call`、`final_answer` 或等价封闭枚举
   - And tool action 必须通过 `ToolRegistry.execute()` 执行，不能直接调用工具 handler 或任意 Python 函数
   - And runtime 不直接依赖 OpenAI、Qwen、DeepSeek、Ollama、vLLM、LangChain、LangGraph、FastAPI、SQLAlchemy、Redis、MinIO 或 storage repository

3. **max_steps 在下一次 LLM step 前强制停止**
   - Given Agent 已完成 `max_steps` 次模型决策
   - When runtime 准备请求下一次 step
   - Then 必须停止并返回结构化终止状态，例如 `MAX_STEPS_REACHED`
   - And 不再调用 LLM stepper
   - And 不再调用任何工具

4. **max_tool_calls 在下一次工具调用前强制停止**
   - Given Agent 已执行 `max_tool_calls` 次工具调用
   - When 当前或下一步 action 需要调用工具
   - Then 必须停止并返回结构化终止状态，例如 `MAX_TOOL_CALLS_REACHED`
   - And 不调用 `ToolRegistry.execute()`
   - And 不让 LLM 通过 prompt 自行覆盖此限制

5. **全局 timeout 覆盖 LLM step 和工具执行之间的编排**
   - Given runtime 已达到 `timeout_seconds` 全局预算
   - When 准备下一次 LLM step 或工具调用
   - Then 必须停止并返回 `AGENT_TIMEOUT` 或等价稳定状态
   - And 不继续等待 LLM 或工具结果
   - And 工具自身 timeout 仍由 Tool Registry 的 tool-level timeout 负责，不在 runtime 绕过

6. **重复动作检测必须在工具执行前发生**
   - Given Agent 产生同名工具和语义相同参数的重复 action
   - When `RepeatedActionDetector` 命中 `repeated_action_threshold`
   - Then runtime 必须停止或返回要求换策略的结构化终止状态；MVP 默认停止
   - And 不执行触发阈值的重复工具调用
   - And audit log 记录 `repeated_action_detected`

7. **重复动作 canonicalization 稳定且不泄露敏感参数**
   - Given 工具参数以不同 key 顺序传入
   - When detector 计算 action key
   - Then 相同工具名和相同 JSON 语义参数被视为同一 action
   - And detector/audit metadata 只能记录 `tool_name`、安全 `argument_keys`、`action_hash`、`repeat_count`、`threshold` 等摘要
   - And 不记录完整工具参数、文件内容、query 原文、prompt、token、secret、绝对路径或企业机密全文

8. **Runtime 结果必须可被后续 `/agent/run` 和持久化复用**
   - Given runtime 结束
   - When 返回 `AgentRunResult`
   - Then 结果包含 `status`、`steps_used`、`tool_calls_used`、`final_answer`、`termination_reason`、`error_code`、`request_id`、`trace_id`、`tenant_id`、`user_id` 和安全 metadata
   - And 工具 observation 只保留 bounded、安全、结构化摘要
   - And 不引入 `agent_runs` / `tool_calls` 数据库持久化；这些属于 Story 6.5 / 6.6

9. **Runtime audit 必须覆盖限制触发和终止原因**
   - Given runtime 因 max steps、max tool calls、timeout、repeated action、stepper error 或 tool error 结束
   - When audit port 可用
   - Then 记录安全 audit event，包含 request_id、trace_id、tenant_id、user_id、action、status、latency、error_code 和安全 metadata
   - And audit failure 只能记录 warning，不把成功 runtime 伪造成失败
   - And 不重复记录 Tool Registry 已经记录的完整 tool execution audit；runtime 只记录 run/step/limit 层摘要

10. **测试必须覆盖运行限制、重复检测和安全边界**
    - Given 单元测试运行
    - When 执行 Agent runtime tests
    - Then 使用 fake stepper、fake audit 和真实 `ToolRegistry` 覆盖 final answer、单次 tool call、多步 tool loop、max_steps、max_tool_calls、timeout、repeated action、unknown tool、permission denied、tool structured error、stepper error、安全 audit
    - And 测试默认不调用真实 LLM、embedding、vector store、PostgreSQL、Redis、MinIO、Open WebUI、网络或外部 provider
    - And `tests/unit/test_architecture_boundaries.py` 继续证明 agent core 不导入 framework/provider/infrastructure

11. **README 与配置文档同步**
    - Given Story 6.4 完成
    - When README 描述当前能力和限制
    - Then README 必须说明 governed ReAct Agent Runtime 已支持 max_steps、max_tool_calls、timeout 和 repeated action detection
    - And README 必须继续说明 `/agent/run` API、durable `agent_runs`、durable `tool_calls`、tool event streaming 和 final answer validation 仍未完成
    - And `.env.example`、`packages/common/config.py`、`tests/unit/common/test_config.py` 必须同步新增 runtime 默认配置

## Tasks / Subtasks

- [x] 新增 Agent runtime DTO 和稳定状态码（AC: 1, 3-8）
  - [x] 在 `packages/agent/dto.py` 或新文件 `packages/agent/runtime.py` / `packages/agent/runtime_dto.py` 中定义 `AgentRunConfig`。
  - [x] 建议字段：`max_steps: int`、`max_tool_calls: int`、`timeout_seconds: float`、`repeated_action_threshold: int`。
  - [x] 定义 `AgentAction` / `AgentStepDecision`，封闭 action 类型为 `tool_call` 与 `final_answer`；不要要求或记录模型 chain-of-thought。
  - [x] 定义 `AgentRunResult`，包含 run status、termination reason、step/tool counts、final answer、安全 observation summaries 和 request/auth metadata。
  - [x] 定义稳定错误码 / termination reason：`MAX_STEPS_REACHED`、`MAX_TOOL_CALLS_REACHED`、`AGENT_TIMEOUT`、`REPEATED_ACTION_DETECTED`、`AGENT_STEPPER_FAILED`、`AGENT_TOOL_FAILED`。
  - [x] 所有 Pydantic model 使用 `ConfigDict(extra="forbid", frozen=True)` 或遵循现有 DTO 风格。

- [x] 新增 runtime 配置（AC: 1, 11）
  - [x] 在 `packages/common/config.py` 新增：
    - `agent_default_max_steps`
    - `agent_default_max_tool_calls`
    - `agent_default_timeout_seconds`
    - `agent_repeated_action_threshold`
  - [x] 建议环境变量名：
    - `AGENT_DEFAULT_MAX_STEPS`
    - `AGENT_DEFAULT_MAX_TOOL_CALLS`
    - `AGENT_DEFAULT_TIMEOUT_SECONDS`
    - `AGENT_REPEATED_ACTION_THRESHOLD`
  - [x] 默认值保持保守，例如 `8`、`5`、`30.0`、`2` 或 `3`；最终值以测试和 README 一致为准。
  - [x] 更新 `.env.example` 和 `tests/unit/common/test_config.py`。
  - [x] 不要把这些限制写进 prompt 后再让 LLM 遵守；后端 runtime 必须先验强制。

- [x] 实现 Agent stepper 协议和 fake stepper（AC: 2, 10）
  - [x] 定义 `AgentStepper` Protocol，输入 runtime state / transcript / safe observations，输出 `AgentStepDecision`。
  - [x] Fake stepper 用于单元测试，可以按预置 decision 队列返回 tool calls 或 final answer。
  - [x] Stepper 协议不得直接暴露厂商 SDK；未来 LLM adapter 也必须通过 `packages/llm` provider 抽象或专门的 agent model port 装配。
  - [x] 不引入 LangChain、LangGraph 或任意 agent framework 依赖。

- [x] 实现 `AgentRuntime` ReAct 编排器（AC: 2-5, 8-9）
  - [x] Runtime 构造函数注入 `ToolRegistry`、`AgentStepper`、`AuditPort`、clock/perf_counter 可选参数和配置。
  - [x] Run 方法显式接收 `AuthenticatedRequestContext`，不得从全局变量、prompt 或工具参数推导 tenant/user/permissions。
  - [x] 在每次 LLM step 前检查 `max_steps` 和 global deadline。
  - [x] 在每次工具调用前检查 `max_tool_calls`、global deadline 和 repeated action detector。
  - [x] 工具调用只走 `ToolRegistry.execute(name=..., arguments=..., context=...)`。
  - [x] Tool Registry 抛出的 `AgentToolError` 应转换为 runtime 结构化终止或 observation 策略；MVP 建议终止并返回 `AGENT_TOOL_FAILED`，不要吞掉错误后继续无限循环。
  - [x] Stepper 异常转换为 `AGENT_STEPPER_FAILED`，不泄露原始 prompt、provider payload 或异常敏感文本。
  - [x] Runtime audit event action 建议使用 `agent.runtime.run` 或 `agent.runtime.limit`；metadata 只放 counts、termination reason、tool names、安全 argument keys/hash。

- [x] 实现重复动作检测器（AC: 6-7, 9-10）
  - [x] 新增 `RepeatedActionDetector` 或 runtime 内部组件。
  - [x] 对 `tool_name` 和 JSON canonical arguments 计算稳定 key：`json.dumps(..., sort_keys=True, separators=(",", ":"), default=str)` 后 hash。
  - [x] 只在内存中使用 canonical payload；audit metadata 只记录 hash 和 argument keys。
  - [x] 参数是 Pydantic/Mapping/list/scalar 时都应稳定处理；无法 canonicalize 的参数应落到安全错误或 `repr(type)`，不能记录 raw object。
  - [x] 在触发阈值的那次工具调用前停止，不执行重复工具。
  - [x] 单测覆盖 key 顺序不同但语义相同的参数。

- [x] 更新导出和边界测试（AC: 2, 10）
  - [x] 更新 `packages/agent/__init__.py` 导出 runtime 公共类型。
  - [x] 如新增 runtime 文件，更新 `tests/unit/test_architecture_boundaries.py`，确保 agent core 仍禁止导入 FastAPI、SQLAlchemy、Redis、MinIO、OpenAI/Qwen/DeepSeek/Ollama/vLLM、LangChain/LangGraph、retrieval、rag、vectorstores、storage。
  - [x] 不要把 `packages.agent.tools` 强行导入 `packages.agent` core；runtime 依赖 `ToolRegistry`，不依赖具体 tool adapter。

- [x] 新增 runtime 单元测试（AC: 1-10）
  - [x] 新增 `tests/unit/agent/test_runtime.py`。
  - [x] 使用 `InMemoryAuditPort`、真实 `ToolRegistry`、`InMemoryToolRateLimiter` 和 fake stepper。
  - [x] 测试 final answer 不调用工具。
  - [x] 测试 tool call 通过 registry 执行并把安全 observation 传回 stepper。
  - [x] 测试 `max_steps` 到达后不再调用 stepper。
  - [x] 测试 `max_tool_calls` 到达后不再调用 registry。
  - [x] 测试 timeout 到达后不再调用 stepper/tool。
  - [x] 测试 repeated action hit 后不执行触发阈值的工具，audit 包含 `repeated_action_detected` / `REPEATED_ACTION_DETECTED`。
  - [x] 测试 unknown tool、permission denied、rate limit、tool timeout 或 tool structured error 不导致无限循环。
  - [x] 测试 audit metadata 不包含 raw arguments、prompt、file content、query 原文、token、secret、绝对路径。

- [x] 更新 README 和配置示例（AC: 11）
  - [x] README Build Status 从 Epic 6.3 更新为 Story 6.4 runtime limits 完成。
  - [x] Governed Agent Tools / Current Limits 说明 runtime 已存在，但 `/agent/run` API、持久化、tool event streaming、final answer validation 仍后置。
  - [x] Docker/local config 部分记录新增 `AGENT_*` runtime env vars。

- [x] 验证（AC: 1-11）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/agent tests/unit/test_architecture_boundaries.py tests/unit/common/test_config.py`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`

## Dev Notes

### Current Repository State

- Git baseline for this story context: `c8665e9 fix(agent): address local tool review findings`.
- Worktree was clean before this story file was created.
- Sprint status auto-selected `6-4-react-agent-runtime-限制与重复动作检测` as the first backlog story.
- `packages/agent` currently contains governed Tool Registry, permission policy, tool DTOs, tool exceptions and concrete tool adapters.
- Concrete tools currently available through `packages.agent.tools`: `rag_search`, `calculator`, restricted `file_reader`.
- No Agent Runtime, ReAct loop, repeated action detector, `/agent/run` route, `agent_runs` storage model or `tool_calls` persistence exists yet.

### Existing Patterns To Reuse

- `ToolDefinition` requires safe lower snake_case `name`, Pydantic v2 schema classes, non-empty permission, finite positive timeout, explicit `ToolRateLimit`, and async handler.
- `ToolRegistry.execute()` already enforces lookup -> input schema validation -> permission check -> rate limit -> timeout wrapper -> handler -> output schema validation -> audit -> result.
- `ToolRegistry` records safe audit events through `AuditPort` and sanitizes tool names / argument keys.
- Tool-level denials and failures already map to stable `AgentToolError` codes: `TOOL_NOT_REGISTERED`, `TOOL_INPUT_VALIDATION_FAILED`, `TOOL_PERMISSION_DENIED`, `TOOL_RATE_LIMITED`, `TOOL_TIMEOUT`, `TOOL_HANDLER_FAILED`, `TOOL_OUTPUT_VALIDATION_FAILED`.
- Structured tool-domain errors returned by a handler are valid typed outputs but audited as failure events with the tool output `error_code`.
- `packages.common.context.AuthenticatedRequestContext` carries request_id, trace_id, optional session_id and `AuthContext`.
- `packages.auth.context.AuthContext` carries user_id, tenant_id, roles, department and permissions.
- `packages.common.audit.AuditPort` and `InMemoryAuditPort` are the right runtime audit boundary for Story 6.4; durable storage comes later.
- Existing tests use deterministic fake/in-memory ports and real registry execution. Follow that style instead of testing runtime with mocked internals only.

### Architecture Requirements

- This story belongs to `packages/agent` core runtime layer.
- Runtime must remain framework/provider/infrastructure free. It can import agent DTOs/registry/exceptions, auth/common DTOs/audit, standard library async/time/json/hash utilities and Pydantic.
- Runtime must not import concrete tool adapters from `packages.agent.tools`; assembly code may register tools, but runtime only sees `ToolRegistry`.
- Runtime must not import `packages.llm` directly unless a narrow `AgentStepper` adapter explicitly needs it. The safest MVP is an injected `AgentStepper` Protocol plus fake stepper tests.
- Runtime must not use LangChain or LangGraph. Architecture only allows LangGraph-style state control as a future pattern, not a dependency.
- Runtime must not implement `/agent/run`; API, request schema and route wiring are Story 6.5.
- Runtime must not implement `agent_runs` / `tool_calls` persistence; DB migrations are Story 6.5 / Story 6.6.
- Runtime must not implement final answer validation; that is Story 6.7. It should shape outputs so 6.7 can validate citations later.

### Current UPDATE File Notes

- `packages/agent/dto.py`: currently contains tool DTOs only. Add runtime DTOs carefully or create `runtime.py`/`runtime_dto.py` if separation is clearer. Preserve existing `ToolDefinition` validation.
- `packages/agent/registry.py`: do not bypass `execute()` and do not weaken audit sanitization. Runtime should consume `ToolExecutionResult`.
- `packages/agent/exceptions.py`: add runtime error constants/classes only if needed; do not merge runtime failures into tool errors if that blurs diagnosis.
- `packages/agent/__init__.py`: export runtime public types only after they exist. Do not import `packages.agent.tools` here.
- `packages/common/config.py`: currently has tool defaults only. Add agent runtime defaults with Pydantic validation and env aliases.
- `.env.example`: currently has `TOOL_DEFAULT_*`; add `AGENT_*` runtime defaults.
- `tests/unit/test_architecture_boundaries.py`: agent core restrictions already cover `packages/agent` except tools. New runtime files must pass this without broad allowlists.
- `README.md`: currently says full Agent runtime, max-step orchestration and max-tool-call enforcement are not complete. Update after implementation.

### Previous Story Intelligence

- Story 6.1 review fixes must not regress:
  - Timeout governance must stop waiting even if a handler suppresses cancellation.
  - Output validation must revalidate constructed Pydantic model instances.
  - Unknown tools and rejected calls must produce audit evidence.
  - Audit names and argument keys must be safe-normalized.
  - Non-mapping arguments must become structured validation errors with audit.
  - Extra input/output fields must be rejected.
- Story 6.2 review fixes remain relevant:
  - Tool-specific permission is not enough when crossing into another domain; runtime must pass the original `AuthenticatedRequestContext` unchanged so downstream retrieval/file policies still apply.
  - Agent input must be bounded before expensive work.
  - Tool output must independently redact untrusted source/content fields and cannot rely only on upstream sanitization.
- Story 6.3 review fixes remain relevant:
  - Tool Registry audit distinguishes structured tool-domain errors from successful executions.
  - `file_reader` deliberately avoids absolute path disclosure and full content output; runtime must not re-expand or log those values.
  - `calculator` and `file_reader` limits are injected through factories; runtime limits are separate `AGENT_*` config and must not overwrite per-tool timeout/rate limit.

### Git Intelligence

- `957faa0 feat(agent): add governed tool registry foundation` established core package, config defaults, README updates and boundary tests.
- `b45d746 fix(agent): address tool registry review findings` hardened validation/audit behavior.
- `80a3b6e feat(agent): add governed rag search tool` added the adapter pattern, tests, boundary exceptions and README updates.
- `e9e4c3e fix(agent): address rag_search review findings` hardened tool-specific permission gating, input bounding and observation redaction.
- `e5046aa feat(agent): add calculator and file reader tools` added local deterministic tools and tests.
- `c8665e9 fix(agent): address local tool review findings` hardened file safety, structured tool-error audit semantics and README runtime limits.

### Suggested Runtime Shape

Use these names as guidance, not mandatory API if implementation reveals a better local convention:

```python
from collections.abc import Mapping, Protocol
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AgentActionType = Literal["tool_call", "final_answer"]
AgentRunStatus = Literal["completed", "stopped", "failed"]


class AgentRunConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_steps: int = Field(gt=0)
    max_tool_calls: int = Field(ge=0)
    timeout_seconds: float = Field(gt=0)
    repeated_action_threshold: int = Field(gt=0)


class AgentStepDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action: AgentActionType
    tool_name: str | None = None
    arguments: Mapping[str, object] = Field(default_factory=dict)
    final_answer: str | None = None


class AgentStepper(Protocol):
    async def next_step(self, state: "AgentRuntimeState") -> AgentStepDecision: ...
```

Do not add a `thought` field that invites chain-of-thought capture. If a model adapter needs internal reasoning later, keep it provider-side and never audit or return it.

### Implementation Boundaries

- Do not implement `/agent/run` route, request/response API schemas, Open WebUI tool bridge, SSE `tool_call` / `tool_result` event streaming, `agent_runs` DB table, `tool_calls` DB table or final answer validation.
- Do not add LangChain, LangGraph, LlamaIndex, Haystack or any other agent framework dependency.
- Do not call specific LLM vendor SDKs. Runtime receives a stepper/port.
- Do not let LLM output change max_steps, max_tool_calls, timeout or repeated_action_threshold.
- Do not execute arbitrary Python, shell commands, dynamic imports or unregistered handlers.
- Do not log prompt text, hidden reasoning, raw tool arguments, raw tool output, file content, query text, tokens, secrets, absolute paths or enterprise-sensitive content.
- Do not silently continue after repeated action detection; MVP should terminate with a clear status.

### Latest Technical Information

- Project dependency baseline already pins Python `>=3.11`, Pydantic `>=2.13.4,<3`, pytest `>=9,<10`, ruff `>=0.14,<1`, mypy `>=1.19,<2` in `pyproject.toml`.
- Python 3.11 standard-library `asyncio.wait_for()` / timeout primitives are sufficient for runtime-level deadline tests; no new async framework is required. Source: Python docs, `https://docs.python.org/3.11/library/asyncio-task.html`.
- Pydantic v2 `BaseModel`, `ConfigDict(extra="forbid", frozen=True)` and field constraints match the current project DTO pattern; no schema library change is required. Source: Pydantic docs, `https://docs.pydantic.dev/latest/api/config/`.
- ReAct is a reasoning/action loop pattern, but this project must use only a structured action interface and must not capture or return hidden chain-of-thought. Source for pattern background: ReAct paper, `https://arxiv.org/abs/2210.03629`.
- No new third-party dependency is required for Story 6.4.

### References

- `_bmad-output/planning-artifacts/epics.md#Story-6.4-ReAct-Agent-Runtime-限制与重复动作检测`
- `_bmad-output/planning-artifacts/epics.md#Epic-6-受控-Agent-工具执行`
- `_bmad-output/planning-artifacts/epics.md#FR27`
- `_bmad-output/planning-artifacts/architecture.md#Tool-Security`
- `_bmad-output/planning-artifacts/architecture.md#Data-Flow`
- `_bmad-output/planning-artifacts/architecture.md#Requirements-to-Structure-Mapping`
- `project-context.md#12-Agent-规则`
- `_bmad-output/implementation-artifacts/6-1-tool-registry-与工具治理模型.md`
- `_bmad-output/implementation-artifacts/6-2-rag-search-工具.md`
- `_bmad-output/implementation-artifacts/6-3-calculator-与受限-file-reader-工具.md`
- `packages/agent/dto.py`
- `packages/agent/registry.py`
- `packages/agent/policies.py`
- `packages/agent/exceptions.py`
- `packages/agent/tools/rag_search.py`
- `packages/agent/tools/calculator.py`
- `packages/agent/tools/file_reader.py`
- `packages/common/config.py`
- `packages/common/audit.py`
- `tests/unit/agent/test_tool_registry.py`
- `tests/unit/test_architecture_boundaries.py`
- `README.md#Governed-Agent-Tools`
- `README.md#Current-Limits`

## Validation Checklist

Validation Result: PASS（2026-06-08T11:56:18+08:00）

- [x] Story 明确只实现 Agent Runtime limits 和 repeated action detection，不实现 `/agent/run`、持久化、SSE tool events 或 final answer validation。
- [x] Acceptance Criteria 覆盖 runtime 配置、Tool Registry 唯一路径、max_steps、max_tool_calls、global timeout、repeated action、audit、安全 metadata、测试和 README/config 同步。
- [x] Tasks 给出具体文件结构、DTO、runtime、stepper protocol、detector、config、测试、README 和验证命令。
- [x] Dev Notes 明确现有 agent/tool 抽象、前序 review lessons、当前代码状态、架构边界和非目标。
- [x] 明确测试默认不调用真实 LLM、embedding、vector store、DB、Redis、MinIO、Open WebUI、网络或外部 provider。
- [x] 明确 runtime/audit 不保存 prompt、hidden reasoning、raw tool args/output、文件内容、query 原文、token、secret、绝对路径或企业机密全文。

## Change Log

- 2026-06-08: Created comprehensive Story 6.4 developer context for governed ReAct Agent Runtime limits and repeated action detection.
- 2026-06-08: Implemented governed Agent Runtime limits, repeated action detection, config defaults, README updates and unit validation.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- 2026-06-08T12:11:39+08:00: `.venv\Scripts\python.exe -m pytest tests/unit/agent tests/unit/test_architecture_boundaries.py tests/unit/common/test_config.py` -> 136 passed.
- 2026-06-08T12:11:39+08:00: `.venv\Scripts\python.exe -m pytest tests/unit` -> 643 passed.
- 2026-06-08T12:11:39+08:00: `.venv\Scripts\python.exe -m ruff check .` -> passed.
- 2026-06-08T12:11:39+08:00: `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed.

### Completion Notes List

- Implemented provider-neutral `AgentRuntime` with structured config, sealed step decisions, stable result DTOs, safe observation summaries and runtime audit.
- Enforced `max_steps`, `max_tool_calls`, global timeout and repeated action detection before further LLM/tool work.
- Added `RepeatedActionDetector` canonicalization by tool name and sorted JSON arguments with audit-safe hashes and argument keys only.
- Added `AGENT_*` defaults to `AppSettings`, `.env.example`, README and config tests.
- Added runtime unit coverage for final answer, tool loop, limits, timeout, repeated action, tool errors, stepper errors and audit safety.

### File List

- `.env.example`
- `README.md`
- `_bmad-output/implementation-artifacts/6-4-react-agent-runtime-限制与重复动作检测.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `packages/agent/__init__.py`
- `packages/agent/runtime.py`
- `packages/common/config.py`
- `tests/unit/agent/test_runtime.py`
- `tests/unit/common/test_config.py`

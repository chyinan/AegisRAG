---
baseline_commit: e9e4c3e
---

# Story 6.3: `calculator` 与受限 `file_reader` 工具

Status: done

生成时间：2026-06-08T11:08:52+08:00

## Story

As a 授权用户,
I want Agent 可以执行安全计算并读取 allowlist 文件,
so that 常见辅助任务可自动化但不会越权访问本地文件。

## Acceptance Criteria

1. **`calculator` 工具定义通过 Tool Registry 注册**
   - Given 开发者创建 `calculator` 工具
   - When 工具 definition 被注册到 `ToolRegistry`
   - Then `name` 必须为 `calculator`
   - And `input_schema`、`output_schema` 必须是 Pydantic v2 `BaseModel`
   - And `permission` 必须为显式后端权限字符串，建议使用 `agent:tool:calculator`
   - And `timeout_seconds` 与 `rate_limit` 必须来自显式工具配置或调用方传入的默认配置，不得硬编码在 prompt 或 Agent Runtime 中
   - And handler 必须是显式 async callable，不支持动态 import、反射、eval、exec 或 LLM 提供函数名

2. **`calculator` 只执行确定性、安全表达式**
   - Given Agent 调用 `calculator`
   - When 输入为受支持表达式或结构化计算请求
   - Then 返回确定性计算结果
   - And 不访问网络、文件系统、数据库、LLM、embedding、vector store、retrieval service、环境变量或外部 provider
   - And 不使用 Python `eval`、`exec`、`compile` 执行用户输入，不支持函数反射、import、属性访问、下标访问、变量赋值或任意 Python 语句

3. **`calculator` 输入必须限制复杂度并结构化失败**
   - Given Agent 提供表达式
   - When 表达式为空、过长、包含不支持字符/语法、除零、结果非有限数或计算复杂度超过限制
   - Then handler 返回符合 `CalculatorOutput` 的结构化错误，不伪造成成功
   - And 错误 output 只包含安全 `error_code` 与安全 message，不回显完整表达式
   - And input schema 或 parser 必须限制表达式长度、数字长度、AST 节点数量、嵌套深度和结果范围

4. **`calculator` output 只包含安全计算摘要**
   - Given 计算成功
   - When Tool Registry 校验输出
   - Then output 包含 `status="success"`、`result`、`result_type` 和安全 `operation_summary`
   - And 不包含原始长表达式、prompt、token、secret、文件路径、SQL、provider payload 或其他敏感内容

5. **`file_reader` 工具定义通过 Tool Registry 注册**
   - Given 开发者创建 `file_reader` 工具
   - When 工具 definition 被注册到 `ToolRegistry`
   - Then `name` 必须为 `file_reader`
   - And `input_schema`、`output_schema` 必须是 Pydantic v2 `BaseModel`
   - And `permission` 必须为显式后端权限字符串，建议使用 `agent:tool:file_reader`
   - And handler 必须通过注入的 allowlist 配置和 reader port 工作，不直接从 LLM 参数决定访问权限

6. **`file_reader` 只能读取 allowlist 范围**
   - Given Agent 调用 `file_reader`
   - When 请求路径不在 allowlist 内、包含路径穿越、是绝对路径暴露尝试、指向目录、符号链接逃逸、隐藏文件、二进制文件或超出大小限制
   - Then 调用被拒绝并返回 `FILE_ACCESS_DENIED`、`FILE_NOT_READABLE` 或等价稳定错误 output
   - And 不泄露真实绝对路径、目录结构、用户 home、盘符、容器路径或文件存在性细节
   - And handler 不得读取 allowlist 外的文件内容后再做过滤

7. **`file_reader` 成功输出受大小和敏感内容限制**
   - Given 请求路径在 allowlist 内
   - When `file_reader` 读取文件
   - Then 返回大小受限的内容摘要或内容片段
   - And output 包含 `status="success"`、安全 `file_ref`、`bytes_read`、`truncated`、`content_excerpt` 或 `content_summary`
   - And 不返回完整大文件、API key、access token、secret、私钥、`.env` 内容、企业机密全文、真实绝对路径或未授权 metadata

8. **`file_reader` 必须有可替换的文件访问边界**
   - Given 单元测试或未来生产装配
   - When 构建 `file_reader` definition
   - Then 工具应依赖显式注入的 allowlist roots / resolver / reader port 或等价小型 abstraction
   - And 单元测试可使用临时目录或 fake reader 覆盖路径解析、allowlist、大小限制和内容截断
   - And 不新增数据库、Redis、MinIO、Open WebUI、LangChain/LangGraph 或任意 Agent framework 依赖

9. **Tool Registry 治理和审计保持统一**
   - Given `calculator` 或 `file_reader` 通过 `ToolRegistry.execute()` 调用
   - When 调用成功、输入校验失败、权限拒绝、rate limited、timeout、handler error 或结构化工具错误
   - Then schema、permission、timeout、rate limit 和 audit 仍由 Story 6.1 的 Tool Registry 执行
   - And audit metadata 只记录 tool_name、permission、argument_keys、result_keys、latency、status、error_code 等安全摘要
   - And 不记录完整表达式、完整文件内容、真实绝对路径、完整参数、完整结果、token、secret、prompt 或企业机密全文

10. **边界测试区分 agent core 与本地工具 adapter**
   - Given 当前 `tests/unit/test_architecture_boundaries.py` 已将 agent core 与 agent tools 分开检查
   - When 新增 `calculator.py` 和 `file_reader.py`
   - Then agent core 仍禁止导入 retrieval/rag/llm/vectorstores/storage/API/framework/provider
   - And `calculator` 工具禁止导入 filesystem/network/provider/retrieval/RAG/DB/Redis/MinIO/LangChain/LangGraph
   - And `file_reader` 工具仅允许标准库文件读取相关模块和 common/auth/agent 类型，不允许导入 FastAPI、SQLAlchemy、Redis、MinIO、OpenAI/Qwen/DeepSeek/Ollama/vLLM、LangChain/LangGraph、retrieval/rag/vectorstore/storage repositories

11. **单元测试覆盖安全路径和拒绝路径**
   - Given 单元测试运行
   - When 执行 Agent tool tests
   - Then 覆盖 calculator definition、成功计算、非法语法、除零、超长输入、复杂度限制、非有限结果、permission denied、timeout/rate limit 继承 registry 行为、安全 audit
   - And 覆盖 file_reader definition、allowlist 成功读取、路径穿越拒绝、allowlist 外拒绝、目录拒绝、符号链接逃逸拒绝、隐藏/敏感文件拒绝、二进制/过大文件拒绝、内容截断、敏感内容 redaction、安全 audit
   - And 测试默认不调用真实 LLM、embedding、vector store、PostgreSQL、Redis、MinIO、Open WebUI、网络或外部 provider

12. **README 与导出契约同步**
   - Given Story 6.3 完成
   - When README 描述当前能力和限制
   - Then README 必须说明 `calculator` 与受限 `file_reader` 已作为受控 Tool Registry 工具可用，但 Agent Runtime、`/agent/run`、tool event streaming、tool call persistence、max_steps/max_tool_calls 仍未完成
   - And `packages.agent.tools` 必须导出构建两个工具 definition 所需的公共类型或 factory
   - And 若新增 file allowlist / size limit / calculator limit 配置，必须同步 `.env.example`、`packages/common/config.py` 和配置测试；若完全通过 factory 参数注入，最终回复说明无需新增 env var 的原因

## Tasks / Subtasks

- [x] 新增 `calculator` 工具契约（AC: 1-4, 9）
  - [x] 新增 `packages/agent/tools/calculator.py`。
  - [x] 定义 `CALCULATOR_PERMISSION = "agent:tool:calculator"`。
  - [x] 定义 `CalculatorInput`，建议字段为 `expression: str`，并限制长度，例如 256 字符以内。
  - [x] 定义 `CalculatorOutput`，建议字段为 `status: Literal["success", "error"]`、`result: str | None`、`result_type`、`operation_summary`、`error_code`、`message`。
  - [x] 所有 Pydantic model 使用 `ConfigDict(extra="forbid", frozen=True)`。

- [x] 实现安全计算 parser/evaluator（AC: 2-4, 11）
  - [x] 使用 `ast.parse(..., mode="eval")` 解析表达式，但只允许 `Expression`、`BinOp`、`UnaryOp`、`Constant` 等明确白名单节点。
  - [x] 支持最小确定性运算：`+`、`-`、`*`、`/`、`//`、`%`、`**`、一元正负；是否支持括号由 AST 自然处理。
  - [x] 显式拒绝 `Name`、`Call`、`Attribute`、`Subscript`、`List`、`Dict`、`Compare`、`BoolOp`、assignment、lambda、comprehension、import 等所有非白名单节点。
  - [x] 限制 AST 节点数量、递归深度、数字位数、指数大小、结果绝对值和小数精度；结果必须是有限数。
  - [x] 将预期错误转换为 `CalculatorOutput(status="error", error_code=...)`，不要让表达式语法错误统一变成 `TOOL_HANDLER_FAILED`。
  - [x] 非预期 bug 继续交给 registry 映射为 `TOOL_HANDLER_FAILED`。

- [x] 新增 `calculator` definition factory（AC: 1, 9, 12）
  - [x] 实现 `build_calculator_tool(timeout_seconds: float, rate_limit: ToolRateLimit) -> ToolDefinition`。
  - [x] factory 固定 name、permission、description、input/output schema 和 async handler。
  - [x] 不新增外部依赖，不读取文件、环境变量、网络或 provider。

- [x] 新增 `file_reader` 工具契约（AC: 5-8）
  - [x] 新增 `packages/agent/tools/file_reader.py`。
  - [x] 定义 `FILE_READER_PERMISSION = "agent:tool:file_reader"`。
  - [x] 定义稳定错误码，例如 `FILE_ACCESS_DENIED`、`FILE_NOT_READABLE`、`FILE_TOO_LARGE`、`FILE_UNSUPPORTED_TYPE`。
  - [x] 定义 `FileReaderInput`，建议字段为 `path: str`、`max_bytes: int | None = None`。
  - [x] 定义 `FileReaderOutput`，建议字段为 `status: Literal["success", "error"]`、`file_ref`、`bytes_read`、`truncated`、`content_excerpt`、`error_code`、`message`。
  - [x] 所有 Pydantic model 使用 `ConfigDict(extra="forbid", frozen=True)`。

- [x] 实现 allowlist path resolver / reader 边界（AC: 6-8, 11）
  - [x] 建议定义小型 DTO/Protocol，例如 `FileReaderAllowlist`、`AllowedFileRoot` 或 `FileContentReader`，避免把任意路径逻辑散在 handler 中。
  - [x] `build_file_reader_tool(...)` 接收 allowlist roots、最大文件字节数、最大返回字节数和可选 reader/resolver；默认值必须显式来自配置或调用方参数。
  - [x] 使用 `Path.resolve(strict=True)` 或等价逻辑比较 resolved path 是否仍在 resolved allowlist root 内。
  - [x] 拒绝路径穿越、绝对路径 scope widening、目录、隐藏文件/目录、symlink escape、文件不存在、不可读、二进制文件、超出大小限制、`.env`/key/token/secret 命名文件。
  - [x] 成功读取时只读取上限字节，返回 excerpt 或 summary；如发生截断，设置 `truncated=True`。
  - [x] 对返回内容执行敏感数据 redaction，复用 `packages.common.logging.redact_sensitive_data` 或现有 redaction helper；不返回真实绝对路径。

- [x] 更新 agent tools 导出（AC: 12）
  - [x] 在 `packages/agent/tools/__init__.py` 导出 `CalculatorInput`、`CalculatorOutput`、`build_calculator_tool`。
  - [x] 在 `packages/agent/tools/__init__.py` 导出 `FileReaderInput`、`FileReaderOutput`、`build_file_reader_tool` 和必要错误码。
  - [x] 视现有导出风格决定是否只保留 tool-level exports，不要把具体 tool 依赖强行塞进 `packages/agent/__init__.py` core exports。

- [x] 更新边界测试（AC: 10）
  - [x] 扩展 `tests/unit/test_architecture_boundaries.py`，对 `calculator` 和 `file_reader` 加更细粒度 import 规则。
  - [x] agent core 继续禁止导入 tool adapter、retrieval、RAG、LLM、vectorstores、storage、FastAPI、SQLAlchemy、Redis、MinIO、外部 SDK。
  - [x] `calculator.py` 不允许导入 `pathlib`/`os`/`httpx`/provider/retrieval/rag/storage 等与计算无关模块。
  - [x] `file_reader.py` 允许 `pathlib` 等标准库文件模块，但禁止 provider、framework、DB、retrieval、RAG、vectorstore、LangChain/LangGraph。

- [x] 新增单元测试（AC: 1-11）
  - [x] 新增 `tests/unit/agent/test_calculator_tool.py`。
  - [x] 新增 `tests/unit/agent/test_file_reader_tool.py`。
  - [x] 使用 `ToolRegistry`、`InMemoryAuditPort`、`InMemoryToolRateLimiter` 验证工具通过真实 registry 执行。
  - [x] calculator 测试覆盖成功、小数/整数结果、非法 AST、除零、过长表达式、过深 AST、超大指数、非有限结果、permission denied、safe audit。
  - [x] file_reader 测试使用 `tmp_path` 构造 allowlist，覆盖成功、截断、allowlist 外路径、`..`、目录、symlink escape、隐藏文件、敏感文件名、二进制文件、过大文件、敏感内容 redaction、permission denied、safe audit。
  - [x] 确认 audit metadata 不包含表达式原文、文件内容、绝对路径、secret、token 或完整参数/结果。

- [x] 更新配置与 README（AC: 12）
  - [x] 若需要全局默认配置，在 `packages/common/config.py` 添加 `agent_file_reader_allowed_roots`、`agent_file_reader_max_bytes`、`agent_file_reader_return_bytes`、`calculator_*` 等字段，并同步 `.env.example` 和 `tests/unit/common/test_config.py`。
  - [x] 若实现选择完全通过 factory 参数注入，在 README 中说明装配方必须显式传入 allowlist 和限制。
  - [x] 更新 README 当前进度和限制：Story 6.3 完成后 concrete tools 包含 `rag_search`、`calculator`、restricted `file_reader`；runtime/API/persistence 仍未完成。

- [x] 验证（AC: 1-12）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/agent tests/unit/test_architecture_boundaries.py`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`

### Review Findings

- [x] [Review][Patch] `file_reader` can read hidden allowlist targets through symlinks [packages/agent/tools/file_reader.py:77]
- [x] [Review][Patch] `file_reader` can return private key files and private key content [packages/agent/tools/file_reader.py:18]
- [x] [Review][Patch] Structured tool-domain errors are audited as successful executions [packages/agent/registry.py:408]
- [x] [Review][Patch] `file_reader` checks file size before an unbounded full-file read [packages/agent/tools/file_reader.py:130]
- [x] [Review][Patch] `file_reader` redacts only after truncating excerpts, allowing partial secret leakage [packages/agent/tools/file_reader.py:154]
- [x] [Review][Patch] `file_reader` does not reject sensitive directory path components [packages/agent/tools/file_reader.py:193]
- [x] [Review][Patch] `file_reader` binary detection accepts non-text UTF-8/control payloads [packages/agent/tools/file_reader.py:202]
- [x] [Review][Patch] `calculator` decimal literals can be rounded before validation [packages/agent/tools/calculator.py:196]
- [x] [Review][Patch] New tool tests do not cover required rate-limit and timeout behavior [tests/unit/agent/test_calculator_tool.py:42]
- [x] [Review][Patch] README omits `max_tool_calls` from unfinished Agent runtime limitations [README.md:28]

## Dev Notes

### Current Repository State

- Git baseline for this story context: `e9e4c3e fix(agent): address rag_search review findings`.
- Worktree was clean before this story file was created.
- Sprint status auto-selected `6-3-calculator-与受限-file-reader-工具` as the first backlog story.
- `packages/agent` exists and contains the governed Tool Registry foundation from Story 6.1.
- `packages/agent/tools/rag_search.py` exists and is the first concrete Tool Registry adapter from Story 6.2.
- `calculator` and `file_reader` do not exist yet.
- `/agent/run` does not exist and must not be added in this story.
- Durable `agent_runs` and `tool_calls` persistence is still deferred to Story 6.5 and Story 6.6.

### Existing Patterns To Reuse

- `ToolDefinition` already requires safe lower snake_case `name`, Pydantic v2 schema classes, non-empty permission, finite positive timeout, explicit `ToolRateLimit`, and async handler.
- `ToolRegistry.execute()` already enforces lookup -> input schema validation -> permission check -> rate limit -> timeout wrapper -> handler -> output schema validation -> audit -> result.
- `ToolRegistry` already rejects extra input/output fields, normalizes unsafe audit names/keys, maps timeout/rate-limit/permission/schema errors to stable `AgentToolError`, and records safe audit events through `AuditPort`.
- `packages.agent.tools.rag_search` is the reference adapter style: tool-specific Pydantic schemas, constant permission, factory returning `ToolDefinition`, structured expected errors in output, unexpected bugs flowing to registry.
- `packages.common.context.AuthenticatedRequestContext` carries request_id, trace_id, optional session_id and `AuthContext`.
- `packages.auth.context.AuthContext` carries user_id, tenant_id, roles, department and permissions.
- `packages.common.logging.redact_sensitive_data()` / `REDACTED_VALUE` are already used to sanitize untrusted `rag_search` observation fields.
- Existing tests use deterministic fake/in-memory ports and real registry execution. Follow that style instead of testing handlers only.

### Architecture Requirements

- This story belongs to `packages/agent` tool adapter layer. It must not introduce Agent Runtime, `/agent/run`, streaming tool events, persistent `tool_calls`, DB models or migrations.
- `calculator` is a pure deterministic local tool. It must not import or access filesystem, network, database, Redis, MinIO, provider SDKs, retrieval, RAG, LLM or environment variables.
- `file_reader` is a sensitive local tool. It may use standard-library filesystem APIs only inside an explicit allowlist resolver/reader boundary and must never let the LLM determine authorization.
- Tool permission is not a prompt instruction. Permission is enforced by `ToolRegistry` against `AuthenticatedRequestContext.auth.permissions`.
- Output is an Agent observation, not a system instruction. Treat file contents as untrusted content and keep content short, redacted and bounded.
- Any future full source hydration or document context expansion must use an explicitly authorized source/context story, not this tool.

### File Structure Requirements

```text
packages/
  agent/
    tools/
      calculator.py
      file_reader.py
tests/
  unit/
    agent/
      test_calculator_tool.py
      test_file_reader_tool.py
```

Likely touched existing files:

```text
packages/agent/tools/__init__.py
tests/unit/test_architecture_boundaries.py
README.md
packages/common/config.py        # only if global file_reader/calculator config is introduced
tests/unit/common/test_config.py  # only if config is introduced
.env.example                     # only if config is introduced
```

### Current UPDATE File Notes

- `packages/agent/dto.py`: preserve `ToolDefinition` validation and async handler requirement. Do not add sync handlers or schema shortcuts.
- `packages/agent/registry.py`: expected tool-level errors can be returned as valid output models; unexpected implementation bugs should still become `TOOL_HANDLER_FAILED`.
- `packages/agent/tools/__init__.py`: currently exports `rag_search` public types. Add calculator/file_reader exports here.
- `tests/unit/test_architecture_boundaries.py`: currently permits narrow retrieval imports for `rag_search`. Add tool-specific rules without weakening agent core restrictions.
- `README.md`: currently says concrete Agent tools beyond `rag_search`, including `calculator` and restricted `file_reader`, are not complete. Update during implementation.

### Previous Story Intelligence

- Story 6.1 review fixes must not regress:
  - Timeout governance must stop waiting even if a handler suppresses cancellation.
  - Output validation must revalidate constructed Pydantic model instances.
  - Unknown tools and rejected calls must produce audit evidence.
  - Audit names and argument keys must be safe-normalized.
  - Non-mapping arguments must become structured validation errors with audit.
  - Extra input/output fields must be rejected.
- Story 6.2 review fixes are directly relevant:
  - Tool-specific permission is not enough when the tool crosses into another domain; for 6.3, file access must have its own allowlist policy, not only `agent:tool:file_reader`.
  - Agent input must be bounded before expensive work.
  - Tool output must independently redact untrusted source/content fields and cannot rely only on upstream sanitization.
  - Sensitive path/token/secret values must be removed from output and audit.

### Git Intelligence

- `957faa0 feat(agent): add governed tool registry foundation` established the core package, config defaults, README updates and boundary tests.
- `b45d746 fix(agent): address tool registry review findings` hardened registry validation/audit behavior.
- `80a3b6e feat(agent): add governed rag search tool` added the adapter pattern, tests, boundary exceptions and README updates.
- `e9e4c3e fix(agent): address rag_search review findings` hardened tool-specific permission gating, input bounding and observation redaction.
- Recent Agent stories consistently update story file, sprint status, README, tests and implementation together. Follow that pattern when implementing this story.

### Error Handling Contract

Recommended calculator output error codes:

```text
CALCULATOR_INVALID_EXPRESSION
CALCULATOR_UNSUPPORTED_EXPRESSION
CALCULATOR_DIVISION_BY_ZERO
CALCULATOR_COMPLEXITY_LIMIT_EXCEEDED
CALCULATOR_RESULT_OUT_OF_RANGE
```

Recommended file_reader output error codes:

```text
FILE_ACCESS_DENIED
FILE_NOT_READABLE
FILE_TOO_LARGE
FILE_UNSUPPORTED_TYPE
FILE_CONTENT_REDACTED
```

Expected tool-domain denials should be represented in the tool output schema when handler runs. Registry-level denials such as missing `agent:tool:*` permission, invalid input schema, timeout and rate limit remain `AgentToolError`.

### Suggested DTO Shape

```python
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class CalculatorInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    expression: str = Field(min_length=1, max_length=256)


class CalculatorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["success", "error"]
    result: str | None = None
    result_type: Literal["integer", "decimal"] | None = None
    operation_summary: str = ""
    error_code: str | None = None
    message: str | None = None


class FileReaderInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=512)
    max_bytes: int | None = Field(default=None, ge=1)


class FileReaderOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["success", "error"]
    file_ref: str | None = None
    bytes_read: int = Field(default=0, ge=0)
    truncated: bool = False
    content_excerpt: str = ""
    error_code: str | None = None
    message: str | None = None
```

Adapt names if implementation reveals a better local convention, but keep the security surface equivalent.

### Implementation Boundaries

- Do not implement ReAct Agent Runtime, Planner-Executor, LangGraph-style state graph, repeated action detection, final answer validation, max_steps/max_tool_calls, `/agent/run`, Open WebUI tool bridge, SSE tool events, agent run persistence or tool call persistence.
- Do not add LangChain/LangGraph or any Agent framework dependency.
- Do not introduce a general-purpose Python evaluator, sandbox, subprocess runner, shell command tool, web search tool or arbitrary file browser.
- Do not use `eval`, `exec`, dynamic import, reflection, shell commands, subprocesses, network calls or environment-variable reads.
- Do not log or audit raw expression, raw file content, absolute file path, prompt, complete args/result, token or secret.
- Do not add real external provider calls; tests must stay fake/local/deterministic.

### Latest Technical Information

- Project dependency baseline already uses Pydantic v2; follow existing `BaseModel`, `ConfigDict`, `Field`, `field_validator`, `model_validate()` and `model_dump(mode="json")` patterns.
- Python 3.11 standard-library `ast` is sufficient for a safe arithmetic whitelist. A whitelist evaluator is required because Python `eval` cannot be made acceptable for this system boundary.
- Python `pathlib.Path.resolve(strict=True)` can canonicalize paths for allowlist checks, but symlink escape must still be tested explicitly by verifying the resolved path remains under a resolved allowlist root.
- No new third-party dependency is required for this story.

### References

- `_bmad-output/planning-artifacts/epics.md#Story-6.3-calculator-与受限-file_reader-工具`
- `_bmad-output/planning-artifacts/epics.md#Epic-6-受控-Agent-工具执行`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-26`
- `_bmad-output/planning-artifacts/architecture.md#Tool-Security`
- `_bmad-output/planning-artifacts/architecture.md#Data-Flow`
- `_bmad-output/planning-artifacts/architecture.md#Requirements-to-Structure-Mapping`
- `project-context.md`
- `_bmad-output/implementation-artifacts/6-1-tool-registry-与工具治理模型.md`
- `_bmad-output/implementation-artifacts/6-2-rag-search-工具.md`
- `packages/agent/dto.py`
- `packages/agent/registry.py`
- `packages/agent/policies.py`
- `packages/agent/tools/rag_search.py`
- `packages/agent/tools/__init__.py`
- `packages/common/logging.py`
- `tests/unit/agent/test_tool_registry.py`
- `tests/unit/agent/test_rag_search_tool.py`
- `tests/unit/test_architecture_boundaries.py`
- `README.md#Current-Limits`

## Validation Checklist

Validation Result: PASS（2026-06-08T11:08:52+08:00）

- [x] Story 明确只实现 `calculator` 和受限 `file_reader` 两个 Tool Registry adapter，不实现 Agent Runtime、API、SSE wiring 或持久化。
- [x] Acceptance Criteria 覆盖工具定义、确定性计算、allowlist 文件访问、输入复杂度限制、输出/审计脱敏、边界测试、单测、README 和配置同步。
- [x] Tasks 给出具体文件结构、DTO、factory、calculator evaluator、file resolver、测试、README 和验证命令。
- [x] Dev Notes 明确现有 agent/tool 抽象、前序 review lessons、当前代码状态、架构边界和非目标。
- [x] 明确测试默认不调用真实 LLM、embedding、vector store、DB、Redis、MinIO、Open WebUI、网络或外部 provider。
- [x] 明确 output/audit 不保存完整表达式、完整文件内容、真实绝对路径、完整 args/result、prompt、token 或 secret。

## Change Log

- 2026-06-08: Created comprehensive Story 6.3 developer context for governed `calculator` and restricted `file_reader` tools.
- 2026-06-08: Implemented governed `calculator` and restricted `file_reader` Tool Registry adapters, tests, boundary checks, README updates, and validation.
- 2026-06-08: Addressed code review findings for file reader path/content safety, structured tool-error audit semantics, calculator decimal parsing, regression tests, and README runtime limits.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- `.venv\Scripts\python.exe -m pytest tests/unit/agent/test_calculator_tool.py` failed first as expected before implementation because `CALCULATOR_PERMISSION` was not exported.
- `.venv\Scripts\python.exe -m pytest tests/unit/agent/test_file_reader_tool.py` failed first as expected before implementation because `FILE_ACCESS_DENIED` was not exported.
- `.venv\Scripts\python.exe -m pytest tests/unit/agent/test_calculator_tool.py` passed with 17 tests.
- `.venv\Scripts\python.exe -m pytest tests/unit/agent/test_file_reader_tool.py` passed with 13 tests.
- `.venv\Scripts\python.exe -m pytest tests/unit/agent tests/unit/test_architecture_boundaries.py` passed with 112 tests.
- `.venv\Scripts\python.exe -m pytest tests/unit` passed with 623 tests.
- `.venv\Scripts\python.exe -m ruff check .` passed.
- `.venv\Scripts\python.exe -m mypy apps packages tests` passed with no issues in 257 source files.
- Code review fix validation: `.venv\Scripts\python.exe -m pytest tests/unit/agent tests/unit/test_architecture_boundaries.py -q` passed with 120 tests.
- Code review fix validation: `.venv\Scripts\python.exe -m pytest tests/unit/common/test_logging.py -q` passed with 5 tests.
- Code review fix validation: `.venv\Scripts\python.exe -m pytest tests/unit -q` passed with 631 tests.
- Code review fix validation: `.venv\Scripts\python.exe -m ruff check .` passed.
- Code review fix validation: `.venv\Scripts\python.exe -m mypy apps packages tests` passed with no issues in 257 source files.

### Completion Notes List

- Added `packages.agent.tools.calculator` with bounded Pydantic input/output schemas, explicit `CALCULATOR_PERMISSION`, structured error codes, and `build_calculator_tool`.
- Implemented calculator evaluation through a strict Python AST whitelist for arithmetic only; it rejects calls, names, attributes, imports, assignments, collections, excessive complexity, division by zero, and out-of-range results without using `eval`, `exec`, filesystem, network, providers, retrieval, RAG, or environment access.
- Added `packages.agent.tools.file_reader` with `FileReaderAllowlist`, bounded input/output schemas, explicit `FILE_READER_PERMISSION`, stable refusal codes, and `build_file_reader_tool`.
- Implemented restricted file reading with factory-injected allowlist roots, max file bytes, max returned bytes, canonical `Path.resolve(strict=True)` containment checks, hidden/sensitive filename rejection, binary/oversize rejection, symlink escape denial, redacted excerpts, and no absolute-path output.
- Exported calculator and file_reader public factories/types from `packages.agent.tools` without adding them to `packages.agent` core exports.
- Added real Tool Registry unit coverage for both tools, including schema/permission/audit paths, safe structured failures, and content redaction.
- Extended architecture boundary tests so calculator remains pure compute and file_reader remains a narrow local file adapter.
- README now states Epic 6.3 status, documents the two new controlled tools, and clarifies that `/agent/run`, runtime orchestration, streaming tool events, and durable tool call persistence remain unfinished.
- No new `.env.example` or `packages.common.config` fields were added because calculator and file_reader limits are explicitly supplied to the tool factories by assembly code.
- Code review fixes now reject symlink access to hidden targets, sensitive path components, common private-key filenames, private-key content, oversized reads, and UTF-8 control payloads in `file_reader`.
- Tool Registry audit now marks structured tool-domain errors as failure events with the tool output `error_code`, while keeping them as typed tool outputs instead of raising infrastructure exceptions.
- Calculator decimal literals are now parsed from the original expression segment before Decimal conversion so precision is not lost through Python float coercion.

### File List

- `_bmad-output/implementation-artifacts/6-3-calculator-与受限-file-reader-工具.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `README.md`
- `packages/agent/registry.py`
- `packages/agent/tools/__init__.py`
- `packages/agent/tools/calculator.py`
- `packages/agent/tools/file_reader.py`
- `tests/unit/agent/test_calculator_tool.py`
- `tests/unit/agent/test_file_reader_tool.py`
- `tests/unit/test_architecture_boundaries.py`

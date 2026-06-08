---
baseline_commit: b45d746
---

# Story 6.2: `rag_search` 工具

Status: done

生成时间：2026-06-08T10:15:16+08:00

## Story

As a 交付顾问,
I want Agent 能通过受控 `rag_search` 工具查询授权知识库,
so that Agent 的知识检索复用已有 retrieval 权限和 citation 能力。

## Acceptance Criteria

1. **`rag_search` 工具定义通过 Tool Registry 注册**
   - Given 开发者创建 `rag_search` 工具
   - When 工具 definition 被注册到 `ToolRegistry`
   - Then `name` 必须为 `rag_search`
   - And `input_schema`、`output_schema` 必须是 Pydantic v2 `BaseModel`
   - And `permission` 必须为显式后端权限字符串，建议使用 `agent:tool:rag_search`
   - And `timeout_seconds` 与 `rate_limit` 必须来自显式工具配置或调用方传入的默认配置，不得硬编码在 prompt 或 Agent Runtime 中
   - And handler 必须是显式 async callable，不支持动态 import、反射、eval、exec 或 LLM 提供函数名

2. **handler 复用现有 retrieval application/service 与 AuthContext filter**
   - Given `ToolRegistry.execute(name="rag_search", arguments=..., context=AuthenticatedRequestContext(...))`
   - When registry 完成 schema、permission、rate limit 和 timeout 治理后执行 handler
   - Then handler 必须调用现有 `RetrieveApplicationService.retrieve()` 或等价已存在 retrieval application boundary
   - And 必须传入同一个 `AuthenticatedRequestContext`
   - And retrieval 阶段必须继续由 `RetrievalService` / `build_retrieval_filter_set()` 执行 tenant、RBAC、ACL、metadata 和 soft-delete 过滤
   - And 不允许在工具内绕过 retrieval service 直接查 vector store、sparse store、SQLAlchemy repository、chunks 表或 LLM/RAG 服务

3. **输入 schema 收窄检索范围但不能扩大权限**
   - Given Agent 调用 `rag_search`
   - When 输入包含 `query`、`top_k`、`metadata_filter`、`score_threshold`
   - Then `query` 必须非空，`top_k` 必须限制在安全范围内，建议默认 5、最大 20；不得暴露 retrieval API 的 100 上限给 Agent 默认使用
   - And `metadata_filter` 只能包含结构化 scalar 值，禁止 `$` 操作符、空 key、空白 key、嵌套对象、数组、query text、prompt、content、token、secret、绝对路径等敏感字段
   - And 如果输入尝试传入 `tenant_id` 且不等于 `context.auth.tenant_id`，必须由现有 retrieval filter 返回 `RETRIEVAL_FORBIDDEN_FILTER` 或工具层结构化错误，不能扩大 tenant scope

4. **成功 observation 只返回安全摘要和 citation 所需标识**
   - Given retrieval 返回授权 candidates
   - When `rag_search` handler 生成 tool output
   - Then output 必须包含 `status="success"`、`query_summary`、`result_count`、`results`
   - And 每个 result 必须包含 `document_id`、`version_id`、`chunk_id`、`source`、`source_uri`、`source_type`、`page_start`、`page_end`、`title_path`、`score`、`retrieval_method`
   - And 每个 result 必须包含短 `summary` 或 `snippet`，只允许从 retrieval response 可安全返回的字段派生；若当前 retrieval response 没有 chunk 正文，则 `summary` 可为空或使用 title/source 摘要，不得新增未授权全文读取
   - And output 不得包含完整 chunk 文本、prompt、raw query、SQL、vector、embedding、provider raw response、ACL 全量规则、文件绝对路径、token、secret 或未授权文档存在性信号

5. **无结果是成功状态，不伪造答案或 citation**
   - Given retrieval 成功但返回 0 条 candidates
   - When `rag_search` 输出 observation
   - Then output 必须返回 `status="success"`、`result_count=0`、`results=[]`
   - And 可包含安全的 no-result reason，例如 `no_authorized_results`
   - And 不得编造 chunk、document、citation、summary 或 fallback Web 搜索结果

6. **retrieval 领域错误映射为结构化工具输出**
   - Given retrieval service 抛出 `RetrievalError`
   - When `rag_search` handler 捕获错误
   - Then handler 必须返回符合 `output_schema` 的结构化错误 output，而不是让 registry 统一包装成 `TOOL_HANDLER_FAILED`
   - And output 至少包含 `status="error"`、`error_code`、安全 `message`、`result_count=0`、`results=[]`
   - And tool audit 仍由 registry 记录本次工具调用完成状态和 latency；retrieval application 自身仍记录 retrieval failure log/audit
   - And 错误 output 不得包含 query 原文、SQL、provider payload、路径、token、secret、完整 tool args 或完整 retrieval details

7. **非 retrieval 的未预期 handler bug 仍交给 registry 失败治理**
   - Given handler 内部出现非 `RetrievalError` 的编程错误
   - When Tool Registry 执行 handler
   - Then 不得吞掉异常并伪造成成功 output
   - And registry 应按 Story 6.1 既有行为映射为 `TOOL_HANDLER_FAILED` 并写入安全 audit metadata

8. **边界测试明确区分 agent core 与 tool adapter**
   - Given 当前 `tests/unit/test_architecture_boundaries.py` 已禁止 `packages.agent` 导入 `packages.retrieval`
   - When 本 story 新增 `rag_search` 工具适配器
   - Then 必须更新边界测试：`packages.agent` 的 registry/runtime/policies/dto/exceptions 核心仍禁止导入 retrieval/rag/llm/vectorstores/storage/API/framework/provider
   - And 仅允许 `packages.agent.tools.rag_search` 这类工具适配器导入 `packages.retrieval.application` 或必要 retrieval DTO/exception
   - And 工具适配器仍禁止导入 FastAPI、SQLAlchemy、Redis、MinIO、OpenAI/Qwen/DeepSeek/Ollama/vLLM、LangChain/LangGraph、vector store adapters、storage repositories 或 tests/eval 模块

9. **单元测试覆盖工具定义、成功、无结果、权限、错误和脱敏**
   - Given 单元测试运行
   - When 执行 `rag_search` 工具测试
   - Then 必须覆盖 definition 构建、registry 注册执行、context/auth 原样传递、metadata filter 传递、top_k 限制、成功结果映射、0 结果、`RetrievalError` 结构化 output、非 retrieval 异常交给 registry、permission denied、input validation、safe output redaction
   - And 测试必须使用 fake retrieval application/service、fake audit、fake limiter，不调用真实 LLM、embedding、vector store、PostgreSQL、Redis、MinIO、Open WebUI、文件系统或网络

10. **README 与导出契约同步**
   - Given Story 6.2 完成
   - When README 描述当前能力和限制
   - Then README 必须说明 `rag_search` 已作为受控 Tool Registry 工具可用，但 Agent Runtime、`/agent/run`、tool event streaming、calculator、file_reader、tool call persistence 仍未完成
   - And `packages.agent.__init__` 或清晰模块路径必须导出/暴露构建 `rag_search` definition 所需的公共类型或函数

## Tasks / Subtasks

- [x] 新增 `rag_search` 工具契约（AC: 1, 3, 4, 5, 6）
  - [x] 新增 `packages/agent/tools/__init__.py`。
  - [x] 新增 `packages/agent/tools/rag_search.py`。
  - [x] 定义 `RagSearchInput`，字段建议为 `query: str`、`top_k: int = 5`、`metadata_filter: dict[str, object] = {}`、`score_threshold: float | None = None`。
  - [x] `RagSearchInput` 使用 Pydantic v2 validator 复用/镜像 `RetrieveRequestBody` 的安全规则；`top_k` 最大值收窄到 20。
  - [x] 定义 `RagSearchResultItem`，只包含安全 observation 字段：`document_id`、`version_id`、`chunk_id`、`source`、`source_uri`、`source_type`、`page_start`、`page_end`、`title_path`、`score`、`retrieval_method`、`summary`。
  - [x] 定义 `RagSearchOutput`，字段建议为 `status: Literal["success", "error"]`、`query_summary`、`result_count`、`results`、`error_code`、`message`。
  - [x] 所有 output schema 设置 `extra="forbid"` 或通过 registry 现有 extra-field 检查保证输出不携带未声明字段。

- [x] 实现 handler 与 definition factory（AC: 1, 2, 6, 7）
  - [x] 定义 `RagSearchTool` 或 `build_rag_search_tool(...) -> ToolDefinition`，注入 `RetrieveApplicationService`、timeout、rate limit。
  - [x] `ToolDefinition.name` 固定为 `rag_search`，`permission` 固定为 `agent:tool:rag_search`，description 说明“Search authorized RAG retrieval candidates through backend retrieval filters”。
  - [x] handler 签名保持 registry 约定：`async def handler(payload: RagSearchInput, context: AuthenticatedRequestContext) -> RagSearchOutput`。
  - [x] handler 构造 `RetrieveCommand`，调用 `RetrieveApplicationService.retrieve(context=context, command=command)`。
  - [x] 捕获 `RetrievalError` 并转换为 `RagSearchOutput(status="error", error_code=exc.code, message=safe_message, result_count=0, results=())`。
  - [x] 不捕获所有 `Exception`；非 retrieval bug 继续交给 registry 映射为 `TOOL_HANDLER_FAILED`。

- [x] 实现安全结果映射（AC: 4, 5, 6）
  - [x] 从 `RetrieveResponse.candidates` 映射到 `RagSearchResultItem`。
  - [x] `summary` 不读取 chunk 正文；优先使用 `title_path`、`source`、`source_type`、page range 生成短摘要，或者为空字符串。
  - [x] 不把 `candidate.acl` 传给 Agent observation；如果确实需要可见性摘要，只允许极小白名单，例如 `visibility` 且不得暴露用户/角色/权限列表。
  - [x] 不回传 `candidate.metadata` 全量；如需 provenance，只允许安全白名单并证明不会包含 chunk text/raw query/provider/sql/vector。
  - [x] 保留 `document_id`、`version_id`、`chunk_id`，供后续 Agent final answer validation / citation validation 使用。

- [x] 更新边界测试（AC: 8）
  - [x] 修改 `tests/unit/test_architecture_boundaries.py`，把 agent core 与 agent tools 分开检查。
  - [x] Core 文件仍禁止 `packages.retrieval`、`packages.rag`、`packages.llm`、`packages.vectorstores`、`packages.data.storage`、`apps.api` 等。
  - [x] `packages/agent/tools/rag_search.py` 只允许导入 `packages.retrieval.application`、`packages.retrieval.exceptions` 和必要 common/auth/agent 类型。
  - [x] 添加测试证明 tool adapter 不导入 storage repositories、SQLAlchemy、FastAPI、external SDK、LangChain/LangGraph 或 vector store adapters。

- [x] 新增单元测试（AC: 1-9）
  - [x] 新增 `tests/unit/agent/test_rag_search_tool.py`。
  - [x] 使用 fake `RetrieveApplicationService` 或最小 fake class 捕获 `context` 与 `RetrieveCommand`。
  - [x] 测试 registry 注册并执行 `rag_search` 成功路径，断言 context auth 原样传入，输出只包含安全字段。
  - [x] 测试 `top_k` 默认值、最大值和非法值；非法输入必须在 registry input validation 前拒绝 handler 执行。
  - [x] 测试 metadata filter 标量、非法 key、跨租户 widening、敏感字段过滤或拒绝。
  - [x] 测试 0 results 返回 `success` 且不伪造 citation。
  - [x] 测试 `RetrievalError` 返回 structured error output，且 output schema 校验通过。
  - [x] 测试非 retrieval 异常被 registry 映射为 `TOOL_HANDLER_FAILED`。
  - [x] 测试 permission denied 时 fake retrieval service 不被调用。
  - [x] 测试 audit metadata 不包含 query 原文、完整 args、完整 result、document content、SQL、token、secret、绝对路径。

- [x] 更新公共导出与配置/装配说明（AC: 1, 10）
  - [x] 在 `packages/agent/tools/__init__.py` 导出 `RagSearchInput`、`RagSearchOutput`、`RagSearchResultItem`、`build_rag_search_tool` 或等价 API。
  - [x] 视现有导出风格决定是否在 `packages/agent/__init__.py` 暴露工具 factory；避免把具体 tool 依赖强行塞进 registry core。
  - [x] 不新增 Redis limiter、DB persistence 或 `/agent/run` 装配；这些属于 Story 6.4-6.6。
  - [x] 如复用 Story 6.1 的 `TOOL_DEFAULT_*` 配置且无需新增 env var，最终回复说明无需新增配置的原因；如新增 `RAG_SEARCH_*` 默认值，则同步 `.env.example` 和 `packages/common/config.py`。

- [x] 更新 README（AC: 10）
  - [x] Build Status 改为 Epic 6.2 完成后的真实状态。
  - [x] Current Limits 移除 `rag_search` 作为“未实现具体工具”的表述，保留 calculator、file_reader、Agent Runtime、`/agent/run`、tool event streaming、persistence 未完成。
  - [x] Security/Governance 或 Agent roadmap 说明 `rag_search` 复用 retrieval AuthContext filter，且只返回安全 observation。

- [x] 验证（AC: 1-10）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/agent tests/unit/test_architecture_boundaries.py`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`

### Review Findings

- [x] [Review][Patch] `rag_search` 缺少 RAG 查询权限校验 [packages/agent/tools/rag_search.py:170] — Tool Registry 只校验 `agent:tool:rag_search`，但 `RetrieveApplicationService` / `RetrievalService` 不校验 `document:read` 与 `retrieval:query`；`/query` route 通过 `has_rag_query_permission()` 补了这层权限，Agent 工具路径会让仅有 tool 权限的用户进入检索。
- [x] [Review][Patch] metadata filter 允许内嵌 `$` 操作符 [packages/agent/tools/rag_search.py:110] — 当前只拒绝 `normalized_key.startswith("$")`，`department.$ne`、`tenant_id$ne` 等 key 仍可通过，违反 AC3 禁止 `$` 操作符的要求；测试只覆盖了 `$where` 开头场景。
- [x] [Review][Patch] `title_path` 未作为不可信 observation 脱敏 [packages/agent/tools/rag_search.py:228] — `candidate.title_path` 被原样写入 output，并在 `_summary()` 中拼接；文档标题/层级来自不可信内容，可能携带 prompt injection、secret、本地路径或超长文本。
- [x] [Review][Patch] `source` / `source_uri` 二次脱敏不完整 [packages/agent/tools/rag_search.py:223] — `_safe_optional_text()` 只过滤空值和裸本地绝对路径，未独立过滤 `file://...`、带 token/secret 的 URL 或其他敏感值；tool output 不应完全依赖上游 DTO 的脱敏。
- [x] [Review][Patch] Agent 输入规模与 `score_threshold` 类型仍可被滥用 [packages/agent/tools/rag_search.py:75] — `query`、metadata filter key/value 数量和长度没有上限，且 Pydantic 会把布尔 `score_threshold` 强制转换为 `0.0/1.0`；Agent 工具应在进入 retrieval 前限制工作量并拒绝布尔阈值。

## Dev Notes

### Current Repository State

- Git baseline for this story context: `b45d746 fix(agent): address tool registry review findings`.
- Worktree was clean before this story file was created.
- Sprint status auto-selected `6-2-rag-search-工具` as the first backlog story.
- `packages/agent` exists and contains Story 6.1 registry foundation: `dto.py`, `registry.py`, `exceptions.py`, `policies.py`, `__init__.py`.
- There is no `packages/agent/tools/` directory yet.
- `/agent/run` does not exist and must not be added in this story.
- `packages/rag/streaming.py` reserves `tool_call` and `tool_result` event payloads, but this story must not wire SSE tool events.
- Durable `agent_runs` and `tool_calls` storage is still not implemented; persistence belongs to later stories.

### Existing Patterns To Reuse

- `ToolDefinition` already requires safe lower snake_case `name`, Pydantic v2 input/output schema classes, non-empty permission, finite positive timeout, explicit structured rate limit, and async handler.
- `ToolRegistry.execute()` already enforces this order: lookup -> input schema validation -> permission check -> rate limit -> timeout wrapper -> handler -> output schema validation -> audit -> result.
- `ToolRegistry` already records safe audit metadata and redacts unsafe tool names, argument keys, handler output failures, timeout results and audit backend failures.
- `AuthenticatedRequestContext` carries `request_id`, `trace_id`, optional `session_id`, and `AuthContext`.
- `AuthContext` carries `user_id`, `tenant_id`, `roles`, `department`, `permissions`.
- `RetrieveApplicationService.retrieve(context, command)` constructs `RetrievalRequest`, calls `RetrievalService.retrieve(request, auth=context.auth)`, writes `retrieval_logs`, writes retrieval audit, and returns `RetrieveResponse`.
- `RetrievalService` builds `RetrievalFilterSet` from `AuthContext`, filters tenant/ACL/metadata/score/top_k, and rejects cross-tenant backend candidates.
- `RetrieveCandidateResponse.from_candidate()` already removes sensitive metadata such as content, absolute paths, SQL, vectors and provider raw responses.
- Existing tests use deterministic fake ports: `InMemoryAuditPort`, fake retrieval service/log ports, fake handlers, fake limiter and fake clock. Follow that style.

### Architecture Requirements

- This story belongs to `packages/agent` tool adapter layer plus application-service coordination with `packages/retrieval`.
- Keep `packages.agent` core framework/provider/infrastructure-free. `registry.py`, `dto.py`, `exceptions.py`, `policies.py` must not import retrieval or RAG.
- The only intentional cross-package dependency is the concrete `rag_search` tool adapter calling the retrieval application boundary.
- Do not call dense/sparse retrievers, RRF, reranker, vector store, storage repository, SQLAlchemy, OpenSearch, pgvector, LLM provider, embedding provider or source resolver directly from `rag_search`.
- Do not hydrate chunk content in this story. If future Agent needs fuller context, it must be designed as an explicitly authorized context/hydration story with tests and citation controls.
- Tool permission `agent:tool:rag_search` controls whether Agent can invoke the tool. Retrieval permissions such as `document:read` and `retrieval:query` still control whether retrieval itself is allowed. Do not collapse the two policy layers.
- The LLM/Agent may choose to call `rag_search` later, but it must never decide permissions or construct filters that widen AuthContext scope.

### File Structure Requirements

```text
packages/
  agent/
    tools/
      __init__.py
      rag_search.py
tests/
  unit/
    agent/
      test_rag_search_tool.py
```

Likely touched existing files:

```text
packages/agent/__init__.py
tests/unit/test_architecture_boundaries.py
README.md
```

### Current UPDATE File Notes

- `packages/agent/dto.py`: current `ToolDefinition` already validates schema, timeout, rate limit and async handler. Do not weaken this contract for `rag_search`.
- `packages/agent/registry.py`: current registry returns `ToolExecutionResult.output` as `dict[str, Any]` after output schema validation. `rag_search` structured error should be returned as valid output if the retrieval layer raises an expected `RetrievalError`.
- `packages/agent/policies.py`: current permission check is a direct membership check on `AuthContext.permissions`. Use permission string `agent:tool:rag_search`; do not put retrieval permission rules here unless a broader policy refactor is explicitly required.
- `packages/retrieval/application.py`: `RetrieveCommand` and `RetrieveApplicationService` are the preferred boundary. `RetrieveResponse` already strips sensitive candidate metadata, but `rag_search` should be stricter and avoid forwarding metadata/acl wholesale.
- `packages/retrieval/service.py`: already enforces AuthContext-derived filters and protects against cross-tenant candidates. Reuse it through the application service.
- `tests/unit/test_architecture_boundaries.py`: currently forbids all `packages.agent` imports from `packages.retrieval`. Update this deliberately, with a narrow exception for tool adapter modules only.
- `README.md`: currently states concrete Agent tools such as `rag_search`, `calculator`, and `file_reader` are not included yet. Update after implementation.

### Previous Story Intelligence

- Story 6.1 created the governance foundation and then received review fixes. Do not regress these fixes.
- Important 6.1 review lessons:
  - Timeout governance must not wait forever for handlers that suppress cancellation.
  - Output validation must revalidate constructed Pydantic model instances, not trust them blindly.
  - Unknown tool lookup must produce audit evidence.
  - Audit records must normalize unsafe tool names and argument keys.
  - Non-mapping tool arguments must become structured validation errors with audit, not raw runtime errors.
  - Audit backend failures must log traceback without faking tool failure.
  - Extra input/output fields must be rejected and tested.
- 6.1 completed validations:
  - `.venv\Scripts\python.exe -m pytest tests/unit/agent tests/unit/test_architecture_boundaries.py` -> 53 passed.
  - `.venv\Scripts\python.exe -m pytest tests/unit` -> 564 passed.
  - `.venv\Scripts\python.exe -m ruff check .` -> passed.
  - `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed.

### Git Intelligence

- `957faa0 feat(agent): add governed tool registry foundation` added `packages/agent`, config defaults, README updates and boundary tests.
- `b45d746 fix(agent): address tool registry review findings` hardened registry validation/audit behavior. Keep new tests aligned with those hardened semantics.
- Recent work consistently updates story file, sprint status, README, tests and implementation together; follow the same completion pattern when `dev-story` implements this.

### Error Handling Contract

- Expected retrieval errors should be represented in `RagSearchOutput`:

```text
status = "error"
error_code = <RetrievalError.code>
message = safe generic message
result_count = 0
results = []
```

- Do not include raw `RetrievalError.details` unless explicitly whitelisted and redacted. Safe fields may include `request_id`, `trace_id`, `top_k`, `tenant_id`, `user_id`, `error_code`.
- Non-retrieval bugs should raise through registry and become `TOOL_HANDLER_FAILED`.
- Permission denied by Tool Registry (`agent:tool:rag_search` missing) should happen before handler invocation.
- Retrieval permission denied (`document:read` / `retrieval:query` missing, if application boundary enforces it) should be a structured retrieval/tool output error.

### Suggested DTO Shape

```python
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RagSearchInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str
    top_k: int = Field(default=5, ge=1, le=20)
    metadata_filter: dict[str, object] = Field(default_factory=dict)
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class RagSearchResultItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    version_id: str
    chunk_id: str
    source: str | None = None
    source_uri: str | None = None
    source_type: str
    page_start: int | None = None
    page_end: int | None = None
    title_path: tuple[str, ...]
    score: float
    retrieval_method: str
    summary: str = ""


class RagSearchOutput(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["success", "error"]
    query_summary: dict[str, int] = Field(default_factory=dict)
    result_count: int = Field(ge=0)
    results: tuple[RagSearchResultItem, ...] = ()
    error_code: str | None = None
    message: str | None = None
```

Adapt names if implementation reveals a better local convention, but keep the security surface equivalent.

### Implementation Boundaries

- Do not implement calculator, file_reader, web_search, Agent Runtime, max_steps, max_tool_calls, repeated action detection, final answer validation, `/agent/run`, Open WebUI tool bridge, SSE tool events, agent run persistence or tool call persistence.
- Do not add LangChain/LangGraph or any Agent framework dependency.
- Do not add a new retrieval pipeline, new vector search path, new chunk repository read path, new source resolver path or new context packer path.
- Do not read files, network, database or vector store directly in `rag_search`.
- Do not return full chunk/document content in tool observation.
- Do not log or audit raw query text, raw tool args, raw tool result, full document text, SQL, vectors, embeddings, provider payloads, local absolute paths, token or secret.

### Latest Technical Information

- Project dependency baseline pins `pydantic>=2.13.4,<3`; use Pydantic v2 `BaseModel`, `ConfigDict`, `Field`, `field_validator`, `model_validate()` and `model_dump(mode="json")` patterns already used in the repo.
- Pydantic v2 supports explicit model configuration through `ConfigDict`; use it for frozen DTOs and, where useful, extra-field behavior rather than Pydantic v1 `Config` style.
- Python 3.11 timeout behavior is already encapsulated by `ToolRegistry`; this story should not reimplement timeout handling inside the handler.
- No new third-party dependency is required for this story.

### References

- `_bmad-output/planning-artifacts/epics.md#Story-6.2-rag_search-工具`
- `_bmad-output/planning-artifacts/epics.md#Epic-6-受控-Agent-工具执行`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-22`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-26`
- `_bmad-output/planning-artifacts/architecture.md#Tool-Security`
- `_bmad-output/planning-artifacts/architecture.md#Data-Flow`
- `_bmad-output/planning-artifacts/architecture.md#Unified-Project-Structure`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md#Flow-4`
- `project-context.md`
- `packages/agent/dto.py`
- `packages/agent/registry.py`
- `packages/agent/policies.py`
- `packages/retrieval/application.py`
- `packages/retrieval/service.py`
- `packages/retrieval/filters.py`
- `packages/retrieval/exceptions.py`
- `tests/unit/agent/test_tool_registry.py`
- `tests/unit/retrieval/test_retrieve_application.py`
- `tests/unit/retrieval/test_service.py`
- `tests/unit/test_architecture_boundaries.py`
- `README.md#Current-Limits`
- `pyproject.toml`
- `https://docs.pydantic.dev/dev/api/config/`
- `https://docs.python.org/3/library/asyncio-task.html#asyncio.wait_for`

## Validation Checklist

Validation Result: PASS（2026-06-08T10:15:16+08:00）

- [x] Story 明确只实现 `rag_search` 受控工具，不实现 Agent Runtime、API、SSE wiring、calculator、file_reader 或持久化。
- [x] Acceptance Criteria 覆盖 Tool Registry definition、retrieval 复用、AuthContext filter、受限输入、安全 observation、0 结果、RetrievalError 映射、registry bug fallback、边界测试、单测和 README。
- [x] Tasks 给出具体文件结构、DTO、handler factory、结果映射、测试、README 和验证命令。
- [x] Dev Notes 明确现有 agent/retrieval 抽象、前序 6.1 review lessons、当前代码状态、架构边界和非目标。
- [x] 明确测试默认不调用真实 LLM、embedding、vector store、DB、Redis、MinIO、Open WebUI、文件系统或网络。
- [x] 明确 output/audit 不保存完整 query、args、result、document content、SQL、vector、embedding、provider payload、路径、token 或 secret。

## Change Log

- 2026-06-08: Created comprehensive Story 6.2 developer context for governed `rag_search` tool.
- 2026-06-08: Implemented governed `rag_search` Tool Registry adapter, tests, boundary checks, README updates, and validation.
- 2026-06-08: Addressed code review findings for RAG query permission gating, stricter Agent input validation, metadata operator rejection, and safe observation redaction.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- `.venv\Scripts\python.exe -m pytest tests/unit/agent/test_rag_search_tool.py -q` failed first as expected before implementation because `packages.agent.tools` did not exist.
- `.venv\Scripts\python.exe -m pytest tests/unit/agent tests/unit/test_architecture_boundaries.py` -> 72 passed.
- `.venv\Scripts\python.exe -m pytest tests/unit` -> 583 passed.
- `.venv\Scripts\python.exe -m ruff check .` -> passed.
- `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed with no issues in 253 source files.
- Review fix red phase: `.venv\Scripts\python.exe -m pytest tests/unit/agent/test_rag_search_tool.py -q` -> 8 failed, covering embedded `$` metadata operators, unbounded inputs, boolean `score_threshold`, missing RAG query permission gate, and unsafe observation redaction.
- Review fix validation: `.venv\Scripts\python.exe -m pytest tests/unit/agent/test_rag_search_tool.py -q` -> 26 passed.
- Review fix validation: `.venv\Scripts\python.exe -m pytest tests/unit/agent tests/unit/test_architecture_boundaries.py` -> 80 passed.
- Review fix validation: `.venv\Scripts\python.exe -m pytest tests/unit` -> 591 passed.
- Review fix validation: `.venv\Scripts\python.exe -m ruff check .` -> passed.
- Review fix validation: `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed with no issues in 253 source files.

### Completion Notes List

- Added `packages.agent.tools.rag_search` with `RagSearchInput`, `RagSearchResultItem`, `RagSearchOutput`, `RAG_SEARCH_PERMISSION`, and `build_rag_search_tool`.
- `rag_search` calls only the injected `RetrieveApplicationService` boundary with the same `AuthenticatedRequestContext` and a narrowed `RetrieveCommand`.
- Input validation enforces nonblank query, `top_k` 1-20, scalar structured metadata filters, sensitive key rejection, local absolute path rejection, and structured cross-tenant filter errors.
- Output mapping returns only safe observation fields and citation identifiers; it excludes ACL, metadata, chunk text, raw query, SQL, vectors, provider payloads, tokens, secrets, and local absolute paths.
- Expected `RetrievalError` is converted to valid structured tool output; unexpected non-retrieval bugs still flow to registry `TOOL_HANDLER_FAILED`.
- README now states Epic 6.2 status and documents `packages.agent.tools.build_rag_search_tool`; no new env vars were needed because the factory receives explicit timeout and rate limit.
- Code review fixes require both Tool Registry permission and existing RAG query permissions before retrieval, reject embedded `$` metadata operators and oversized Agent inputs, reject boolean `score_threshold`, and sanitize untrusted title/source observation fields independently at the tool layer.

### File List

- `_bmad-output/implementation-artifacts/6-2-rag-search-工具.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `README.md`
- `packages/agent/tools/__init__.py`
- `packages/agent/tools/rag_search.py`
- `tests/unit/agent/test_rag_search_tool.py`
- `tests/unit/test_architecture_boundaries.py`

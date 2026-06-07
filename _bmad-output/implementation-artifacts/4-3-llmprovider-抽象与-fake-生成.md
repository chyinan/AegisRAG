---
baseline_commit: NO_VCS
---

# Story 4.3: LLMProvider 抽象与 Fake 生成

Status: done

生成时间：2026-06-07T15:15:37+08:00

## Story

As a 平台工程师,
I want 回答生成通过 `LLMProvider` 抽象完成,
so that 系统可以切换 OpenAI、Qwen、DeepSeek、本地 vLLM 或 Ollama。

## Acceptance Criteria

1. **新增独立 `packages/llm` Provider 包**
   - Given 当前仓库尚无 `packages/llm`
   - When 本 story 完成
   - Then 新增 `packages/llm/__init__.py`、`packages/llm/dto.py`、`packages/llm/ports.py`、`packages/llm/exceptions.py`、`packages/llm/adapters/__init__.py`、`packages/llm/adapters/fake.py`
   - And `packages/llm` 不导入 FastAPI、SQLAlchemy、Redis、MinIO、OpenAI/Qwen/DeepSeek/Ollama/vLLM SDK、retrieval storage model、API schema 或 vector store adapter
   - And 不把 LLM DTO/端口塞进 `packages/rag.dto` 或 `packages.embeddings`，避免生成层和 prompt/context 层混杂

2. **定义模型无关的 LLM DTO 和端口**
   - Given RAG generation 后续需要接收 `PromptBuilder` 输出
   - When 定义 `GenerateRequest`
   - Then request 至少包含 `messages`、`provider`、`model`、`timeout_seconds`、`retry_budget`、`request_id`、`trace_id`、`tenant_id`、`user_id`、可选 `session_id`、`temperature`、`max_output_tokens`、`stream_options`、`metadata`
   - And `messages` 使用结构化 `LLMMessage` 或直接兼容 `PromptMessage` 的安全映射，不接受 raw dict、ORM model、API schema、provider raw payload
   - And 定义 `GenerateResponse`、`GenerateChunk`、`TokenUsage`、`GenerationMetadata` 或等价 DTO，包含 answer/text、finish_reason、provider、model、usage、latency_ms、request/trace/tenant/user、error_code
   - And `LLMProvider` Protocol 只暴露 `async generate(request: GenerateRequest) -> GenerateResponse` 和 `stream(request: GenerateRequest) -> AsyncIterator[GenerateChunk]`

3. **FakeLLMProvider 可预测且不调用外部服务**
   - Given 测试环境调用 generation
   - When 使用 `FakeLLMProvider`
   - Then 不访问网络、本地模型进程、外部 SDK、数据库、Redis、MinIO、向量库或文件系统
   - And `generate()` 返回确定性文本、provider/model/version、usage、latency_ms 和 finish_reason
   - And `stream()` 以稳定顺序产出 `GenerateChunk`，至少覆盖 token chunk 和 final chunk
   - And Fake provider 支持可配置 failure modes：timeout、rate_limited、failed、stream_failed 或等价集合，并抛出稳定领域异常

4. **Provider 错误和安全 metadata 明确**
   - Given provider 成功或失败
   - When 记录 response、chunk、error details 或 metadata
   - Then 只允许保存 request_id、trace_id、tenant_id、user_id、provider、model、version、usage 计数、latency_ms、finish_reason、chunk/token 计数、error_code
   - And 不记录 prompt 全文、chunk content、完整用户 query、provider raw response、API key、access token、bearer token、本机绝对路径、SQL、vectors 或 embeddings
   - And timeout/rate limit 可标记 retryable，validation/config 类错误默认 fail-closed

5. **RAG generation application boundary 最小落地**
   - Given `PromptBuilder` 已输出 `PromptBuildResult`
   - When 本 story 完成
   - Then 新增纯应用编排模块，例如 `packages/rag/generation.py` 或等价文件，用于把 `PromptBuildResult.messages` 映射为 `GenerateRequest` 并调用注入的 `LLMProvider`
   - And 该模块只依赖 `packages.rag` DTO、`packages.llm` 端口/DTO、`AuthenticatedRequestContext` 或等价请求上下文，不依赖真实 provider adapter、FastAPI route、storage、retrieval adapter、citation extractor 或 SSE
   - And generation result 返回 answer/text 和安全 generation metadata，供 Story 4.4 `/query` 与 citation extraction 后续使用
   - And 本 story 不实现 `/query`、`/chat`、citation extraction、SSE、Open WebUI adapter、chat memory 或真实厂商 adapter

6. **配置只接入 fake provider，真实厂商后置**
   - Given 本地和测试默认不能真实调用外部 LLM
   - When 扩展配置
   - Then 在 `packages/common/config.py` 增加 `LLM_PROVIDER`、`LLM_MODEL`、`LLM_TIMEOUT_SECONDS`、`LLM_RETRY_BUDGET`、可选 `LLM_FAKE_RESPONSE_TEXT` 或等价本地 fake 配置
   - And 默认值为 fake provider，真实 OpenAI/Qwen/DeepSeek/vLLM/Ollama adapter 不在本 story 范围
   - And 如扩展 `apps/api/service_dependencies.py`，只能新增 provider factory，且只支持 `fake`；不在 route 中调用 LLM

7. **单元测试覆盖 provider contract 与 RAG generation 边界**
   - Given 单元测试运行
   - When 执行 LLM 和 RAG generation 测试
   - Then 覆盖 DTO 校验、端口调用、FakeLLMProvider generate、stream、failure modes、usage/metadata 安全摘要、raw dict message 拒绝、provider raw response 不泄露
   - And 覆盖 RAG generation service 从 `PromptBuildResult` 调用 provider，并保留 request_id、trace_id、tenant_id、user_id、model、token usage、latency、error_code
   - And 测试默认不调用真实 OpenAI、Qwen、DeepSeek、Ollama、vLLM、本地模型进程、网络、Docker、PostgreSQL、Redis、MinIO、pgvector 或 OpenSearch

8. **文档更新标记 RAG generation 阶段进度**
   - Given 本 story 完成
   - When 阅读 `README.md#RAG Foundation` 和 `docs/operations/local-development.md`
   - Then 文档说明 LLMProvider 抽象、FakeLLMProvider、RAG generation boundary 已完成，以及本地测试命令
   - And 文档仍明确 citation extraction、`/query`、`/chat`、SSE streaming、chat memory、Open WebUI adapter、真实 provider adapter、RAG answer eval 不在本 story 范围

## Tasks / Subtasks

- [x] 新增 `packages/llm` 包和 DTO/异常/端口（AC: 1, 2, 4）
  - [x] 创建 `packages/llm/dto.py`，使用 Pydantic v2 `BaseModel + ConfigDict(frozen=True)`，与 `packages/embeddings/dto.py` 和 `packages/rag/dto.py` 风格一致。
  - [x] 定义 `LLMMessage`，字段建议为 `role: Literal["system", "user", "assistant"]`、`name: str | None`、`content: str`；必须拒绝空 content，拒绝 raw dict 直接穿透。
  - [x] 定义 `GenerateRequest`，包含 prompt messages、provider/model、timeout/retry、request/trace/tenant/user/session、temperature、max_output_tokens、metadata。
  - [x] 定义 `TokenUsage`、`GenerationMetadata`、`GenerateResponse`、`GenerateChunk`，usage 只允许安全计数字段。
  - [x] 创建 `packages/llm/ports.py`，定义 `LLMProvider` Protocol，`stream()` 返回 `AsyncIterator[GenerateChunk]`。
  - [x] 创建 `packages/llm/exceptions.py`，定义稳定错误码，例如 `LLM_PROVIDER_TIMEOUT`、`LLM_PROVIDER_RATE_LIMITED`、`LLM_PROVIDER_FAILED`、`LLM_GENERATION_INVALID_REQUEST`、`LLM_STREAM_FAILED`。

- [x] 实现 `FakeLLMProvider`（AC: 3, 4）
  - [x] 创建 `packages/llm/adapters/fake.py`。
  - [x] `generate()` 使用输入消息、provider/model/version 和可配置 response text 生成确定性输出；不读取文件、不联网、不调用真实 SDK。
  - [x] `stream()` 按稳定 token 分片产出 chunk，并以 final chunk 携带完整 metadata 或完成状态。
  - [x] 支持 timeout、rate_limited、failed、stream_failed failure modes，并抛出 `LLMProviderError` 或等价领域异常。
  - [x] 确保异常 details 不包含 prompt、query、chunk content、secret、provider raw response 或本机绝对路径。

- [x] 新增 RAG generation 编排层（AC: 5）
  - [x] 新增 `packages/rag/generation.py` 或等价文件，建议类名 `RagGenerationService`。
  - [x] 输入使用 `PromptBuildResult`、`AuthenticatedRequestContext`、provider/model 配置和注入的 `LLMProvider`。
  - [x] 将 `PromptMessage` 映射为 `LLMMessage`，保留 message role/name/content，但不把整个 prompt 写入 trace/log metadata。
  - [x] `generate()` 调用 `LLMProvider.generate()` 并返回 answer/text + safe generation metadata。
  - [x] 可预留 `stream()` 方法签名，但如果实现必须只委托 provider stream，不构造 SSE event；SSE 属于 Story 4.5。
  - [x] 不接 retrieval、context packing、citation extraction、FastAPI route、storage、real provider adapter 或 Tool Registry。

- [x] 扩展配置和导出（AC: 1, 6）
  - [x] 更新 `packages/common/config.py`，新增 fake-first LLM 配置项。
  - [x] 更新 `packages/llm/__init__.py` 和 `packages/llm/adapters/__init__.py` 导出公共 DTO、端口、异常和 fake adapter。
  - [x] 如需要，在 `apps/api/service_dependencies.py` 新增 `_llm_provider_from_settings()`，只支持 fake 并返回 `FakeLLMProvider`；不要新增 `/query` 或 `/chat` route。

- [x] 新增单元测试（AC: 2, 3, 4, 5, 7）
  - [x] 新增 `tests/unit/llm/test_fake_provider.py`，覆盖 deterministic generate、stream chunk 顺序、usage、failure modes。
  - [x] 新增 `tests/unit/llm/test_dto.py`，覆盖 required fields、timeout/retry 校验、message raw dict 拒绝、usage 安全字段过滤。
  - [x] 新增 `tests/unit/rag/test_generation.py`，覆盖 `PromptBuildResult -> GenerateRequest -> GenerateResponse` 编排。
  - [x] 扩展 `tests/unit/test_architecture_boundaries.py`，防止 `packages/llm` domain/DTO/port 层导入 forbidden provider SDK 或 framework。
  - [x] 测试错误 details 和 generation metadata 不包含 prompt 全文、chunk content、query、secret、token、provider raw response。

- [x] 更新文档（AC: 8）
  - [x] 更新 `README.md#RAG Foundation`，将 “LLMProvider/fake generation” 从 non-goals 移到已完成能力。
  - [x] 更新 `docs/operations/local-development.md`，新增 `RAG LLM Provider Local Checks` 或等价小节。
  - [x] 文档列出本地测试命令：`.venv\Scripts\python.exe -m pytest tests/unit/llm tests/unit/rag/test_generation.py`。
  - [x] 文档明确真实 provider adapter、`/query`、citation extraction、SSE、chat memory、Open WebUI adapter 仍后置。

- [x] 验证（AC: 1-8）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/llm tests/unit/rag/test_generation.py`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/rag tests/unit/retrieval tests/unit/auth`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`
  - [x] 如成本可接受，运行 `.venv\Scripts\python.exe -m pytest`

### Review Findings

- [x] [Review][Patch] LLM metadata uses blacklist filtering instead of the AC4 allowlist [packages/llm/dto.py:146]
- [x] [Review][Patch] RagGenerationService returns provider response metadata without identity validation [packages/rag/generation.py:46]
- [x] [Review][Patch] FakeLLMProvider stream deltas cannot reconstruct the final text [packages/llm/adapters/fake.py:127]
- [x] [Review][Patch] GenerateChunk does not enforce final/non-final response invariants [packages/llm/dto.py:277]
- [x] [Review][Patch] .env.example is not fake-first for the new LLM settings [.env.example:29]
- [x] [Review][Patch] LLM architecture boundary test misses forbidden providers and internal adapter/storage imports [tests/unit/test_architecture_boundaries.py:35]
- [x] [Review][Patch] max_output_tokens accepts True because bool is treated as int [packages/rag/generation.py:121]

## Dev Notes

### Current Repository State

- 当前工作区 `git status` 无输出，疑似不是 git repository 或未初始化 git；不要依赖 git history。最近实现上下文来自 sprint status、Story 4.2、源码扫描和本地测试记录。
- `packages/llm` 当前不存在。架构文档预期它包含 `dto.py`、`ports.py`、`adapters/fake.py`、未来真实 provider adapters。
- `packages/rag` 已存在并完成：
  - `ContextPacker`：从授权 `ContextCandidate` 生成 prompt-ready `PackedContext`。
  - `PromptBuilder`：从 `PromptBuildRequest` 生成结构化 `PromptMessage`，明确 system/security/citation/no-answer/user/context 分离。
- `README.md#RAG Foundation` 和 `docs/operations/local-development.md#RAG Prompt Builder Local Checks` 目前明确 LLMProvider/fake generation 仍未完成，本 story 需要更新。
- 现有测试通过记录来自 Story 4.2：`pytest` 459 passed、ruff passed、mypy passed；本 story 必须保持这些基线。

### Existing Patterns To Reuse

- `packages/embeddings` 是最接近的 Provider 模式参考：
  - `dto.py`：`EmbeddingRequest` / `EmbeddingResponse` 使用 Pydantic v2 frozen DTO，校验 provider/model/timeout/retry/usage。
  - `ports.py`：Protocol 很薄，只定义 provider 能力。
  - `exceptions.py`：稳定错误码 + `retryable` provider error。
  - `adapters/fake.py`：deterministic fake，支持 failure modes，不调用外部服务。
  - `service.py`：safe usage summary 只允许计数字段，日志/audit 不含 chunk content/provider raw response/API key。
- `packages/rag.dto.PromptMessage` 已有 `role`、`name`、`content`；LLM 层可映射它，但不要让 `packages/llm` 反向依赖 RAG，以免 Provider 包被 RAG 绑定。建议由 `packages/rag/generation.py` 做映射。
- `packages/common.config.AppSettings` 当前包含 embedding/vector 配置。LLM 配置应遵循同一 env-first 风格，默认 fake。
- `tests/unit/test_architecture_boundaries.py` 已防止 route 乱放和 common/domain 导入 framework；可以扩展 forbidden roots 覆盖 `openai`、`deepseek`、`qwen`、`ollama`、`vllm` 在不该出现的位置。

### Architecture Requirements

- 本 story 位于 LLM Infrastructure Port + RAG Application boundary：
  - `packages/llm/dto.py`、`ports.py`、`exceptions.py` 是 provider-neutral 抽象层。
  - `packages/llm/adapters/fake.py` 是测试/本地 infrastructure adapter，但仍不得触网。
  - `packages/rag/generation.py` 是应用编排层，用注入的 provider 生成 answer。
- 生产默认 RAG 数据流是：

```text
retrieval -> context packing -> prompt build -> LLM generate/stream -> citation extraction -> audit/retrieval/generation log
```

- 本 story 只打通 `prompt build -> LLM generate/stream` 的抽象和 fake，不负责 query API、citation extraction 或 SSE。
- Route 层不得调用 `LLMProvider`，后续 `/query` 或 `/chat` route 必须只调用 RAG application service。
- 权限不由 LLM 判断。`tenant_id`、`user_id`、ACL 已由 retrieval/context packing 阶段处理；generation metadata 只能记录这些上下文，不能扩大权限。

### Suggested DTO Shape

```python
class LLMMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Literal["system", "user", "assistant"]
    name: str | None = None
    content: str
```

```python
class GenerateRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    messages: tuple[LLMMessage, ...]
    provider: str
    model: str
    timeout_seconds: float
    retry_budget: int
    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    session_id: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    metadata: Mapping[str, object] = Field(default_factory=FrozenDict)
```

```python
class TokenUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
```

```python
class GenerateResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str
    provider: str
    model: str
    version: str | None = None
    usage: TokenUsage
    latency_ms: float
    finish_reason: str
    metadata: Mapping[str, object] = Field(default_factory=FrozenDict)
```

```python
class GenerateChunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    delta: str
    index: int
    is_final: bool = False
    response: GenerateResponse | None = None
    metadata: Mapping[str, object] = Field(default_factory=FrozenDict)
```

These shapes are guidance, not a forced exact implementation. Preserve the required fields and safe metadata semantics.

### Generation Service Boundary

Recommended minimal service:

```python
class RagGenerationService:
    def __init__(self, provider: LLMProvider, config: RagGenerationConfig | None = None) -> None: ...

    async def generate(
        self,
        *,
        prompt: PromptBuildResult,
        context: AuthenticatedRequestContext,
        provider: str,
        model: str,
    ) -> RagGenerationResult: ...
```

Implementation requirements:

- Build `GenerateRequest` from `PromptBuildResult.messages`.
- Copy request/trace/tenant/user from context and verify they match `PromptBuildTrace`.
- Preserve `prompt.trace.citation_source_ids` only as safe counts/IDs if needed; do not copy prompt text into generation metadata.
- Convert `LLMProviderError` into stable RAG generation error only if you introduce RAG-specific error wrapping; otherwise allow the domain error to propagate with safe details.
- Return enough metadata for Story 4.4 to attach citations later: answer text, provider/model/version, usage, latency, finish_reason, error_code.

### Implementation Boundaries

- Do not implement OpenAI, Qwen, DeepSeek, Ollama, vLLM adapters in this story.
- Do not add real SDK dependencies to `pyproject.toml`.
- Do not create `/query`, `/chat`, `/sources/resolve`, streaming SSE endpoint, Open WebUI adapter, chat session memory, citation extractor, eval runner, Tool Registry or Agent code.
- Do not call `PromptBuilder` from FastAPI routes.
- Do not let FakeLLMProvider inspect filesystem, environment secrets, network, Redis, PostgreSQL, MinIO, vector store, or worker queues.
- Do not persist prompt text or provider raw response in metadata, logs, test reports, audit payloads, or error details.
- Do not use prompt text to enforce permissions. Permission enforcement stays in Auth/Retrieval/Context Packer.

### Latest Technical Information

- OpenAI 官方文档在 2026 年仍推荐新项目优先使用 Responses API；Chat Completions 文档提示新项目可尝试 Responses 以获得最新平台能力。未来 OpenAI adapter 应优先映射到 Responses API，但本 story 不实现真实 adapter。Source: https://platform.openai.com/docs/guides/text?api-mode=responses and https://platform.openai.com/docs/api-reference/chat/create-chat-completion
- OpenAI streaming 使用 server-sent events，Responses endpoint 设置 `stream=True` 后返回 typed semantic events，常见文本流事件包括 response.created、response.output_text.delta、response.completed、error。Story 4.5 的 SSE adapter 可以参考这些语义，但本 story 的 `LLMProvider.stream()` 只返回内部 `GenerateChunk`。Source: https://platform.openai.com/docs/guides/streaming-responses
- OpenAI Completions API 属于 legacy 文档，未来真实 adapter 不应以旧 completions prompt-string API 作为首选抽象。Source: https://platform.openai.com/docs/guides/completions
- 当前项目 `pyproject.toml` 已锁定 Pydantic v2、Python 3.11+、pytest、ruff、mypy。新增 DTO 继续使用 Pydantic v2 frozen models，不引入额外依赖。

### Previous Story Intelligence

- Story 4.2 已建立强边界：prompt messages 可以包含授权上下文，但 trace、error、log、audit metadata 不能包含 query/content/prompt 全文。LLM generation 必须继承这条规则。
- Story 4.2 的 review 修复了动态 citation metadata 进入 trusted system message、用户控制 metadata 逃逸 untrusted wrapper、request/packed context identity mismatch、citation metadata consistency 等问题。本 story 的 generation service 必须验证 prompt trace 的 request/trace/tenant/user 与当前 context 一致。
- Story 4.1/4.2 均刻意避免 API routes、storage、provider SDK、citation extraction 和 SSE。本 story 可以新增 provider abstraction 和 fake generation，但仍不能越界到 query/chat 或真实 adapter。
- Embedding story 中已经证明 safe usage summary 很重要：provider usage 中的 `content`、`raw_response`、`api_key` 等字段必须被过滤。LLM usage 也要采用 allowlist。

### UX / Product Notes

- 本 story 不实现 UI，但后续 Knowledge Chat、Source Inspector 和 Retrieval Diagnostics 会依赖 generation metadata 的安全字段。
- 无答案策略仍由 PromptBuilder 和后续 citation/no-answer validation 共同保证；FakeLLMProvider 不应模拟“更聪明”的业务判断。
- 前端不得补造 citation；本 story 输出 answer text，不等于最终可信 RAG response。Story 4.4 会负责 citation extraction 和 `/query`。

### References

- `_bmad-output/planning-artifacts/epics.md#Story-4.3-LLMProvider-抽象与-Fake-生成`
- `_bmad-output/planning-artifacts/epics.md#Epic-4-可信-RAG-问答-Citation-与流式会话`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-15-LLM-Provider-抽象`
- `_bmad-output/planning-artifacts/architecture.md#Requirements-to-Structure-Mapping`
- `_bmad-output/planning-artifacts/architecture.md#Integration-Points`
- `project-context.md`
- `_bmad-output/implementation-artifacts/4-2-promptbuilder-与-prompt-injection-防护.md`
- `packages/embeddings/dto.py`
- `packages/embeddings/ports.py`
- `packages/embeddings/exceptions.py`
- `packages/embeddings/adapters/fake.py`
- `packages/embeddings/service.py`
- `packages/rag/dto.py`
- `packages/rag/prompt_builder.py`
- `tests/unit/embeddings/test_fake_provider.py`
- `tests/unit/embeddings/test_embedding_service.py`
- `README.md#RAG-Foundation`
- `docs/operations/local-development.md#RAG-Prompt-Builder-Local-Checks`
- OpenAI Responses text generation docs: https://platform.openai.com/docs/guides/text?api-mode=responses
- OpenAI streaming responses docs: https://platform.openai.com/docs/guides/streaming-responses
- OpenAI Chat Completions API reference: https://platform.openai.com/docs/api-reference/chat/create-chat-completion
- OpenAI legacy Completions docs: https://platform.openai.com/docs/guides/completions

## Validation Checklist

Validation Result: PASS（2026-06-07T15:15:37+08:00）

- [x] Story 明确了角色、目标和收益。
- [x] Acceptance Criteria 覆盖独立 `packages/llm`、DTO/Protocol、Fake provider、错误和安全 metadata、RAG generation boundary、配置、测试和文档。
- [x] Tasks 拆分到可执行文件和验证命令。
- [x] Dev Notes 明确当前源码状态：`packages/llm` 不存在，`packages/rag` 已完成 context packing 和 prompt builder。
- [x] 明确复用 embeddings provider 模式，不重复发明 provider contract。
- [x] 明确不实现真实厂商 adapter、`/query`、`/chat`、citation extraction、SSE、chat memory、Open WebUI adapter 或 Agent。
- [x] 明确 prompt/上下文只能作为 provider 输入，不进入 trace、error、log、audit 或 metadata。
- [x] Latest technical notes 只引用官方 OpenAI 文档，没有要求新增真实 SDK 依赖。

## Change Log

- 2026-06-07: Created comprehensive Story 4.3 developer context for LLMProvider abstraction, FakeLLMProvider, safe generation metadata, RAG generation boundary, tests, docs, and scope guardrails.
- 2026-06-07: Implemented LLMProvider abstraction, FakeLLMProvider, RAG generation boundary, fake-first configuration, tests, docs, and validation.

## Dev Agent Record

### Agent Model Used
Codex (GPT-5)

### Debug Log References
- `.venv\Scripts\python.exe -m pytest tests/unit/llm tests/unit/rag/test_generation.py -q` -> 12 passed
- `.venv\Scripts\python.exe -m pytest tests/unit/rag tests/unit/retrieval tests/unit/auth -q` -> 171 passed
- `.venv\Scripts\python.exe -m ruff check .` -> All checks passed
- `.venv\Scripts\python.exe -m mypy apps packages tests` -> Success, no issues in 190 source files
- `.venv\Scripts\python.exe -m pytest -q` -> 472 passed
- `.venv\Scripts\python.exe -m pytest tests/unit/llm tests/unit/rag/test_generation.py tests/unit/test_architecture_boundaries.py -q` -> 23 passed
- `.venv\Scripts\python.exe -m ruff check .` -> All checks passed
- `.venv\Scripts\python.exe -m mypy apps packages tests` -> Success, no issues in 191 source files
- `.venv\Scripts\python.exe -m pytest -q` -> 475 passed

### Completion Notes List
- Added provider-neutral `packages/llm` DTOs, Protocol, stable domain errors, and deterministic `FakeLLMProvider` with generate/stream/failure-mode coverage.
- Added `RagGenerationService` to map `PromptBuildResult` into `GenerateRequest`, validate request/trace/tenant/user identity, and return safe generation metadata without implementing query/chat/citation/SSE.
- Added fake-first LLM settings and API dependency factory support for fake only; no route calls LLM directly.
- Added unit and architecture boundary tests for DTO validation, raw dict rejection, fake provider behavior, safe metadata, generation orchestration, and forbidden SDK/framework imports.
- Updated README and local development docs with completed LLMProvider/Fake generation scope, test commands, and deferred real-provider/query/citation/SSE items.
- Review patch: tightened LLM metadata/error detail allowlists, validated provider response identity, fixed stream delta reconstruction, enforced chunk final-state invariants, corrected fake-first env examples, expanded LLM boundary checks, and rejected bool `max_output_tokens`.

### File List
- apps/api/service_dependencies.py
- .env.example
- docs/operations/local-development.md
- packages/common/config.py
- packages/llm/__init__.py
- packages/llm/adapters/__init__.py
- packages/llm/adapters/fake.py
- packages/llm/dto.py
- packages/llm/exceptions.py
- packages/llm/ports.py
- packages/rag/__init__.py
- packages/rag/exceptions.py
- packages/rag/generation.py
- README.md
- tests/unit/common/test_config.py
- tests/unit/llm/__init__.py
- tests/unit/llm/test_dto.py
- tests/unit/llm/test_fake_provider.py
- tests/unit/rag/test_generation.py
- tests/unit/test_architecture_boundaries.py

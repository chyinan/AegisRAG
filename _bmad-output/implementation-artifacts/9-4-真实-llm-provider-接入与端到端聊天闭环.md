---
baseline_commit: 3ac25ae
---

# Story 9.4: 真实 LLM Provider 接入与端到端聊天闭环

Status: review

生成时间：2026-06-10T00:06:42+08:00

## Story

As a 平台负责人,
I want 至少一个真实 LLM provider 接入到 `/chat` 和 `/v1/chat/completions`,
so that 系统能以真实模型跑通 MVP 闭环，而不是停留在 fake provider 演示。

## Acceptance Criteria

1. **首个真实 provider 通过 Provider 抽象接入**
   - Given 当前只有 `FakeLLMProvider` 可被 `service_dependencies.py` 装配
   - When `LLM_PROVIDER` 配置为真实 provider
   - Then `/query`、`/query/stream`、`/chat`、`/chat/stream`、`/v1/chat/completions` 必须通过 `packages.llm.ports.LLMProvider` 调用真实 provider
   - And route、RAG service、Chat service、Open WebUI adapter 不得直接导入 OpenAI/Qwen/DeepSeek SDK 或直接拼 provider HTTP 请求
   - And fake provider 必须保留为测试、本地无外部依赖回归和 demo smoke 的默认安全路径

2. **优先落地 OpenAI-compatible HTTP adapter**
   - Given 需要兼容 OpenAI、Qwen DashScope compatible mode、DeepSeek、本地 vLLM/Ollama 兼容端点
   - When 实现首个真实 provider
   - Then 应新增一个通用 `OpenAICompatibleChatProvider` 或等价 adapter，使用 `httpx.AsyncClient` 调用配置化 chat completions endpoint
   - And 不应默认新增厂商 SDK 依赖；如选择新增 SDK，必须在 story 实现说明中证明不会破坏 provider-neutral 设计
   - And adapter 应支持非流式 `generate()` 和流式 `stream()`，并映射到现有 `GenerateResponse`、`GenerateChunk`、`GenerationMetadata`、`TokenUsage`

3. **配置化且不泄露密钥**
   - Given 本地、Docker Compose 和 Open WebUI 需要选择真实 provider
   - When 配置 `.env`、`.env.example`、`AppSettings` 和 service dependency
   - Then 必须支持 provider、model、base URL、API key、timeout、retry budget、temperature、max output tokens 或等价参数
   - And API key 只能来自环境变量或 secret 配置，不得硬编码，不得进入 response metadata、audit、日志、README 示例输出或测试 snapshot
   - And `LLM_PROVIDER=fake` 不需要真实 API key；真实 provider 缺少 key/base URL 时必须 fail fast 为结构化配置错误

4. **端到端聊天路径保持现有 RAG、citation 和安全合同**
   - Given 已有 retrieval、context packing、PromptBuilder、citation extraction、chat memory、Open WebUI evidence links 和 tool bridge
   - When 用户调用 `/chat`、`/chat/stream`、`/query`、`/query/stream` 或 `/v1/chat/completions`
   - Then generation 阶段使用真实 provider，但 retrieval tenant/ACL filter、no-answer、prompt injection 防护、citation extraction、source resolve 二次授权、audit 和 evidence link contract 不得回归
   - And Open WebUI service token 仍只由后端映射到 `AuthContext`；前端或模型消息不能提升权限
   - And model 输出中的 citation/source 文本仍不得被信任为来源，最终 citation 只能来自当前授权 packed context

5. **Provider 错误映射和重试边界明确**
   - Given provider 可能返回 timeout、rate limit、invalid request、auth failure、server error、malformed response 或 stream 中断
   - When adapter 处理错误
   - Then 必须转换为现有或新增的 `LLMProviderError` 稳定错误码，并包含 safe details：request_id、trace_id、tenant_id、user_id、provider、model、error_code、token counts
   - And 不得暴露 raw provider payload、Authorization header、API key、完整 prompt、完整 answer、chunk content 或堆栈
   - And retry 只应用于可重试错误和配置的 retry budget；非幂等风险、客户端取消和 provider auth/config 错误不得盲目重试

6. **Streaming 兼容真实 provider chunk 形态**
   - Given OpenAI-compatible streaming 返回 SSE/chunked JSON，final usage 可能在最后 chunk 或缺失
   - When `/query/stream`、`/chat/stream` 或 `/v1/chat/completions stream=true` 使用真实 provider
   - Then token chunk 必须按现有 `GenerateChunk` 和 RAG SSE/OpenAI-compatible formatter 输出
   - And final chunk 必须包含 `GenerateResponse`；如果 provider 未返回 usage，应安全填 0 或 `unknown` 兼容字段，不得伪造精确 token usage
   - And stream 中断必须输出结构化 `error` 和 terminal `final`，并写入安全 audit metadata

7. **Open WebUI 本地闭环可验证**
   - Given Docker Compose Open WebUI profile 已存在
   - When 配置真实 provider 并启动 API 与 Open WebUI
   - Then `/v1/models` 应展示配置的真实 model/provider，`/v1/chat/completions` 应能通过本项目后端完成 RAG chat generation
   - And Open WebUI 返回仍包含 request_id、trace_id、citations、evidence_links、tool event summary 或已有安全 metadata
   - And README/docs 必须说明 Open WebUI 是兼容入口，不是权限治理边界，也不是长期唯一主界面

8. **测试默认不真实调用外部 LLM**
   - Given CI、unit、integration 和 eval smoke 运行
   - When 验证真实 provider adapter 和端到端 wiring
   - Then 默认测试必须使用 fake `httpx.MockTransport`、fake provider 或 dependency override，不得真实调用 OpenAI/Qwen/DeepSeek/Ollama/vLLM、真实网络、真实 Open WebUI 或外部模型 API
   - And 必须覆盖非流式成功、stream 成功、timeout、rate limit、auth/config failure、malformed response、stream interrupted、usage present/absent、secret redaction、service dependency provider selection

9. **文档和 README 同步真实 provider 范围**
   - Given story 完成
   - When 用户查看 README 和本地开发文档
   - Then 必须明确首个真实 provider 已接通的 endpoint 范围、最小环境变量、curl/Open WebUI 验证命令、fake fallback、测试边界和当前限制
   - And README 不得继续把真实 LLM provider adapters 描述为纯后续能力；如果仍限制为 OpenAI-compatible HTTP adapter，应明确 Qwen/DeepSeek/vLLM/Ollama 仅在兼容 endpoint 配置正确时复用该 adapter

## Tasks / Subtasks

- [x] 实现真实 provider adapter 和错误映射（AC: 1, 2, 5, 6, 8）
  - [x] 新增 `packages/llm/adapters/openai_compatible.py`，实现 `LLMProvider.generate()` 和 `LLMProvider.stream()`。
  - [x] 使用 `httpx.AsyncClient`，从配置注入 base URL、API key、timeout；不要在 adapter 内读取任意环境变量。
  - [x] 构造请求体时只发送 provider 支持且来自 `GenerateRequest` 的字段：`model`、`messages`、`temperature`、`max_tokens` 或 `max_completion_tokens`、`stream`、`stream_options`。
  - [x] 解析非流式 response：`choices[*].message.content`、`finish_reason`、`usage.prompt_tokens/completion_tokens/total_tokens`，缺失 usage 时安全置 0 并在 metadata 标记 `usage_unavailable_count` 或等价安全计数。
  - [x] 解析 streaming chunk：`choices[*].delta.content`、final usage chunk、finish_reason；确保 final chunk 包含完整 `GenerateResponse`。
  - [x] 将 provider timeout/rate limit/auth/invalid request/server/malformed response/stream failure 映射为稳定 `LLMProviderError`，必要时扩展 `packages/llm/exceptions.py`。
  - [x] 不要把 raw provider response、完整 error body、prompt、messages、headers、API key 放入 details、metadata、audit 或日志。

- [x] 扩展配置和 provider factory（AC: 1, 3, 7）
  - [x] 更新 `packages/common/config.py`，加入真实 provider 需要的安全配置字段，例如 `LLM_BASE_URL`、`LLM_API_KEY` 或 provider-specific key alias、`LLM_MAX_OUTPUT_TOKENS`、`LLM_TEMPERATURE`、可选 `LLM_PROVIDER_VERSION`。
  - [x] API key 字段使用 `SecretStr` 或等价方式，确保 model dump/log 不泄露。
  - [x] 更新 `.env.example`，保留 `LLM_PROVIDER=fake` 默认，同时给出 OpenAI-compatible 示例，不填真实密钥。
  - [x] 更新 `apps/api/service_dependencies.py::_llm_provider_from_settings()`，支持 `fake` 和 `openai_compatible`；如支持 `openai`、`qwen`、`deepseek` alias，必须映射到同一个 adapter 或显式说明差异。
  - [x] `get_rag_query_application_service()`、`get_chat_application_service()`、`get_openwebui_chat_adapter()` 必须复用同一 provider factory，不要复制 provider 构造逻辑三次。

- [x] 保持 RAG/query/chat/Open WebUI 现有合同（AC: 4, 6, 7）
  - [x] 确认 `RagGenerationService` 的 identity validation 对真实 provider 仍生效。
  - [x] 确认 `/query`、`/chat` 非流式 response metadata 中 generation provider/model/token usage/latency/error_code 为安全摘要。
  - [x] 确认 `/query/stream`、`/chat/stream` 保持 `token`、`citation`、`error`、`final` 事件 contract。
  - [x] 确认 `/v1/chat/completions` 非流式和 streaming 保持 OpenAI-compatible response、citations、evidence_links、safe metadata、tool event fallback。
  - [x] 不要修改 retrieval、context packer、prompt builder、citation extractor 的权限语义；真实 provider 只替换 generation adapter。

- [x] 单元测试 provider adapter（AC: 2, 3, 5, 6, 8）
  - [x] 新增 `tests/unit/llm/test_openai_compatible_provider.py`。
  - [x] 用 `httpx.MockTransport` 覆盖非流式成功、stream 成功、usage 缺失、finish_reason 缺失或异常、malformed JSON、unexpected schema。
  - [x] 覆盖 timeout、429、401/403、400、5xx、stream interrupted，并断言错误码、retryable、safe details。
  - [x] 覆盖 request body 不包含 request_id、trace_id、tenant_id、user_id、prompt trace metadata、API key 或 forbidden debug fields。
  - [x] 覆盖 adapter 不泄露 secret：`repr()`、error details、metadata、logs 相关 helper 不含 key。

- [x] 单元/集成测试 dependency wiring 和 API 合同（AC: 1, 4, 6, 7, 8）
  - [x] 扩展 `tests/unit/common/test_config.py`，验证真实 provider 配置校验、fake fallback、secret redaction。
  - [x] 扩展或新增 `tests/unit/test_architecture_boundaries.py`，确保 API routes 不导入 provider adapter、httpx、外部 SDK 或 storage internals。
  - [x] 扩展 `tests/unit/rag/test_generation.py`，用 fake real-provider-like provider 验证 identity、usage、stream final。
  - [x] 扩展 `tests/unit/rag/test_openwebui_adapter.py`，确认真实 provider metadata 不破坏 evidence_links/tool summary。
  - [x] 扩展 `tests/integration/api/test_query_routes.py`、`test_chat_routes.py`、`test_openwebui_routes.py`，用 dependency override 或 MockTransport 验证 `/query`、`/query/stream`、`/chat`、`/chat/stream`、`/v1/chat/completions` 走真实-provider wiring 但不出网。

- [x] 文档和本地验证（AC: 7, 9）
  - [x] 更新 `README.md` 的 Build Status、当前能力、Open WebUI/LLM provider、Current Limits 和验证命令。
  - [x] 更新 `docs/operations/local-development.md`，加入真实 OpenAI-compatible provider 的最小 `.env`、curl、stream curl、Open WebUI 配置、fake fallback 和安全限制。
  - [x] 如已有 Open WebUI 专项文档，补充 `/v1/models`、`/v1/chat/completions` 对真实 provider 的验证步骤。
  - [x] 文档必须说明测试默认不真实调用外部模型；真实 API smoke 只能作为显式人工命令，且不得进入 CI 默认路径。

- [x] 建议验证命令（AC: 1-9）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/llm tests/unit/rag/test_generation.py tests/unit/rag/test_openwebui_adapter.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/common/test_config.py tests/unit/test_architecture_boundaries.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/api/test_query_routes.py tests/integration/api/test_chat_routes.py tests/integration/api/test_openwebui_routes.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/test_readme_expectations.py -q`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`
  - [x] 如成本可接受，运行 `.venv\Scripts\python.exe -m pytest -q`

## Dev Notes

### Current Repository State

- Current HEAD at story creation: `3ac25ae feat(openwebui): add governed function tool bridge`.
- `git status --short` at story creation showed existing unrelated changes:
  - `M _bmad-output/planning-artifacts/epics.md`
  - `?? _bmad-output/planning-artifacts/sprint-change-proposal-2026-06-09.md`
- Sprint status shows 9.1、9.2、9.3 are in `review` and 9.4 is the first `backlog` story. Before implementation, check whether review feedback for 9.1-9.3 has landed, especially shared Open WebUI adapter metadata and tool bridge behavior.
- BMad default `_bmad/bmm/config.yaml` is not present in this repo. Actual artifacts are under `_bmad-output/planning-artifacts` and `_bmad-output/implementation-artifacts`.

### Existing Architecture To Preserve

- Existing LLM abstraction is `packages.llm.ports.LLMProvider`:
  - `generate(request: GenerateRequest) -> GenerateResponse`
  - `stream(request: GenerateRequest) -> AsyncIterator[GenerateChunk]`
- Existing `GenerateRequest` already carries provider/model/timeout/retry/request_id/trace_id/tenant_id/user_id/session_id/temperature/max_output_tokens/safe metadata.
- Existing `RagGenerationService` builds `GenerateRequest`, validates prompt trace identity against `AuthenticatedRequestContext`, validates provider response/chunk identity, and exposes safe provider summary.
- Existing API routes are intentionally thin:
  - `apps/api/routes/query.py`
  - `apps/api/routes/chat.py`
  - `apps/api/routes/openwebui.py`
- Existing `apps/api/service_dependencies.py` currently imports and constructs `FakeLLMProvider` only; `_llm_provider_from_settings()` rejects every non-fake provider.
- Existing `AppSettings` has `llm_provider`, `llm_model`, `llm_timeout_seconds`, `llm_retry_budget`, `llm_fake_response_text`, but lacks true provider base URL/key wiring.
- Existing `.env.example` includes placeholder `OPENAI_API_KEY`、`QWEN_API_KEY`、`DEEPSEEK_API_KEY`, but those are not currently connected to `LLMProvider`.

### Files To Read Before Implementation

- `packages/llm/dto.py`
  - Current state: frozen Pydantic DTOs for messages, generation requests/responses/chunks, token usage and safe metadata.
  - What this story changes: likely no DTO change unless provider needs an explicit safe metadata count; keep backward-compatible.
  - Preserve: metadata allowlist and identity fields.

- `packages/llm/ports.py`
  - Current state: provider protocol.
  - What this story changes: no change expected.
  - Preserve: provider-neutral contract.

- `packages/llm/adapters/fake.py`
  - Current state: deterministic fake generate/stream and failure modes.
  - What this story changes: no replacement; use as behavior model for response identity and error semantics.
  - Preserve: fake as default for tests.

- `packages/llm/exceptions.py`
  - Current state: stable error codes for timeout, rate limit, provider failed, invalid request, stream failed.
  - What this story changes: may add auth/config/malformed response codes or reuse existing codes with safe details.
  - Preserve: `_safe_error_details()` never emits secrets/raw payload.

- `packages/rag/generation.py`
  - Current state: converts PromptBuilder output to `GenerateRequest`; validates provider identity for generate and stream.
  - What this story changes: maybe pass configured max tokens/temperature from service construction; do not bypass validation.
  - Preserve: prompt trace/context mismatch fail-closed behavior.

- `packages/rag/query.py`
  - Current state: orchestrates retrieval -> hydration -> context packing -> prompt build -> generation -> citation extraction -> audit for non-stream and stream.
  - What this story changes: no RAG chain rewrite; only generation provider changes through dependency injection.
  - Preserve: permission checks, safe stream errors, no-answer path, audit redaction.

- `packages/rag/chat.py`
  - Current state: chat memory service wraps RAG query service and persists chat messages.
  - What this story changes: ensure real provider usage flows through existing RAG service.
  - Preserve: no global session state and safe memory context.

- `packages/rag/openwebui.py`
  - Current state: OpenAI-compatible adapter, evidence links, tool event summaries, safe metadata.
  - What this story changes: model/provider metadata should reflect configured real provider/model; no source/tool contract regression.
  - Preserve: service token auth boundary stays in backend dependency.

- `apps/api/service_dependencies.py`
  - Current state: central factory for RAG, chat and OpenWebUI dependencies; provider construction duplicated across three services.
  - What this story changes: introduce real provider factory and preferably reduce duplication.
  - Preserve: routes remain thin and storage/session lifecycle remains controlled here.

- `packages/common/config.py`
  - Current state: Pydantic settings with fake LLM defaults.
  - What this story changes: add safe real provider settings.
  - Preserve: environment-driven configuration and no hardcoded secrets.

- `.env.example`
  - Current state: placeholders for OpenAI/Qwen/DeepSeek keys but no connected base URL/key contract.
  - What this story changes: document real provider env vars and fake fallback clearly.
  - Preserve: no real secret values.

- `tests/unit/llm/test_fake_provider.py`
  - Current state: expected provider behavior examples.
  - What this story changes: use as reference for real adapter tests.
  - Preserve: tests do not call real external APIs.

### Previous Story Intelligence

- Story 9.1 established evidence link contract. Real provider integration must not remove `evidence_links` or insert source links into raw answer text as the only citation carrier.
- Story 9.2 established safe tool event summaries. Real provider streaming must not confuse provider chunks with agent `tool_call/tool_result` events unless the controlled backend Tool Registry flow creates them.
- Story 9.3 established Open WebUI function/tool bridge and permission mapping. Real provider/tool-compatible requests must still go through `OpenWebUIToolBridge` and `ToolRegistry`; the model must not directly execute arbitrary tool calls.
- Story 4.3 established `LLMProvider` abstraction and Fake provider. Do not bypass it with route-level SDK calls.
- Story 4.5 established RAG SSE formatter and safe streaming audit. Real provider streaming must reuse that path and not introduce a second streaming protocol for `/query/stream` or `/chat/stream`.
- Story 4.7 established Open WebUI chat adapter source detail. Real provider should only change generation, not Open WebUI source visibility.

### Latest Technical Information

- OpenAI official Chat Completions docs still expose `POST /v1/chat/completions`, `stream=true`, and `stream_options`; OpenAI recommends Responses API for new projects, but this story should use Chat Completions because the repo’s Open WebUI adapter and compatible provider objective are explicitly Chat Completions based.
- OpenAI streaming docs describe streamed chat completion chunks with `choices[].delta.content`, optional final usage behavior when `stream_options.include_usage` is used, and deprecated `function_call` replaced by `tool_calls`; this repo must not let provider tool calls bypass backend Tool Registry.
- DeepSeek official docs state the API uses an OpenAI-compatible format with OpenAI base URL `https://api.deepseek.com`; current docs list newer V4 model IDs and note deprecation dates for older `deepseek-chat` / `deepseek-reasoner`. Do not hardcode DeepSeek defaults in production code; document model choice as configuration.
- Alibaba Cloud Model Studio docs state Qwen can be called through OpenAI-compatible Chat Completions, with region-specific base URLs such as `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`, `https://dashscope-us.aliyuncs.com/compatible-mode/v1`, and `https://dashscope.aliyuncs.com/compatible-mode/v1`. Region/base URL must be configurable.
- Several compatible providers expose extra parameters such as thinking mode or provider-specific tool formats. The first adapter should ignore or safely pass only allowlisted fields unless story scope explicitly adds provider-specific options.

### Implementation Guardrails

- Do not add a second RAG generation pipeline. Replace only the provider adapter selected by dependency wiring.
- Do not move provider selection into FastAPI routes.
- Do not put API key selection in prompt, model messages, request metadata, frontend config, or Open WebUI metadata.
- Do not log provider raw request/response bodies.
- Do not treat `LLM_PROVIDER=openai` as permission to call OpenAI SDK directly from business code.
- Do not use real external API calls in default tests or CI.
- Do not make `Open WebUI` the source of tenant/user/permission truth; it remains a compatibility client.
- Do not let Chat Completions tool calls invoke arbitrary Python functions. Tool calls remain controlled by backend Tool Registry and story 9.3 contracts.
- Do not claim DeepSeek/Qwen/Ollama/vLLM are fully certified unless tests or docs cover the exact configured compatible endpoint behavior; call them compatible-endpoint reuse paths.

### Suggested Provider Request Shape

```json
{
  "model": "configured-model",
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."}
  ],
  "temperature": 0.2,
  "max_tokens": 1024,
  "stream": true,
  "stream_options": {"include_usage": true}
}
```

The adapter must derive this only from `GenerateRequest`. It must not include `tenant_id`、`user_id`、`trace_id`、ACL、permissions、raw retrieval metadata、prompt debug traces or secret values in the provider request unless a future privacy review explicitly approves it.

### References

- `_bmad-output/planning-artifacts/epics.md#Story-9.4-真实-LLM-Provider-接入与端到端聊天闭环`
- `_bmad-output/planning-artifacts/architecture.md#External-Integrations`
- `_bmad-output/planning-artifacts/architecture.md#Data-Flow`
- `project-context.md`
- `_bmad-output/implementation-artifacts/4-3-llmprovider-抽象与-fake-生成.md`
- `_bmad-output/implementation-artifacts/4-5-sse-streaming-回答事件.md`
- `_bmad-output/implementation-artifacts/4-7-open-webui-chat-adapter-source-detail-与轻量前端契约.md`
- `_bmad-output/implementation-artifacts/9-1-open-webui-citation-evidence-link-contract.md`
- `_bmad-output/implementation-artifacts/9-2-open-webui-tool-event-streaming-bridge.md`
- `_bmad-output/implementation-artifacts/9-3-open-webui-function-tool-bridge-与权限映射.md`
- `packages/llm/dto.py`
- `packages/llm/ports.py`
- `packages/llm/adapters/fake.py`
- `packages/llm/exceptions.py`
- `packages/rag/generation.py`
- `packages/rag/query.py`
- `packages/rag/chat.py`
- `packages/rag/openwebui.py`
- `apps/api/service_dependencies.py`
- `packages/common/config.py`
- `https://platform.openai.com/docs/api-reference/chat/create`
- `https://platform.openai.com/docs/api-reference/chat-streaming`
- `https://api-docs.deepseek.com/`
- `https://www.alibabacloud.com/help/en/model-studio/use-qwen-by-calling-api`

## Validation Checklist

Validation Result: PASS（2026-06-10T00:06:42+08:00）

- [x] Story 明确了角色、目标和收益。
- [x] Acceptance Criteria 覆盖 provider abstraction、OpenAI-compatible adapter、配置、端到端聊天、错误映射、streaming、Open WebUI、测试和文档。
- [x] Tasks 拆分到具体模块和文件，避免 route 直接调用 provider。
- [x] Dev Notes 记录当前源码状态、已有 fake provider、RAG/chat/Open WebUI wiring 和 dirty worktree。
- [x] 明确复用 `RagGenerationService`、`LLMProvider`、existing RAG/Chat/OpenWebUI paths，而不是复制 RAG 链路。
- [x] 明确 secret redaction、raw provider payload 禁止、测试默认不出网。
- [x] 明确 README 需要同步真实 provider 范围和限制。

## Change Log

- 2026-06-10: Created comprehensive Story 9.4 developer context for real OpenAI-compatible LLM provider integration, end-to-end chat generation, streaming, Open WebUI compatibility, tests and docs.
- 2026-06-10: Implemented generic OpenAI-compatible LLM provider adapter, real-provider configuration/factory wiring, tests, docs, and validation.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- `python3 _bmad/scripts/resolve_customization.py --skill .agents/skills/bmad-dev-story --key workflow`
- `.venv\Scripts\python.exe -m pytest tests/unit/llm tests/unit/common/test_config.py tests/unit/test_service_dependencies.py tests/unit/test_architecture_boundaries.py tests/unit/rag/test_generation.py tests/unit/rag/test_openwebui_adapter.py tests/integration/api/test_query_routes.py tests/integration/api/test_chat_routes.py tests/integration/api/test_openwebui_routes.py tests/unit/test_readme_expectations.py -q`
- `.venv\Scripts\python.exe -m ruff check .`
- `.venv\Scripts\python.exe -m mypy apps packages tests`
- `.venv\Scripts\python.exe -m pytest -q`

### Completion Notes List

- Added `OpenAICompatibleChatProvider` using `httpx.AsyncClient` and the existing `LLMProvider` DTO contract for non-streaming and streaming Chat Completions.
- Added stable safe error mapping for auth failure and malformed provider responses while preserving existing timeout/rate-limit/provider/stream error codes.
- Extended `AppSettings`, `.env.example`, and API service dependency wiring so `fake` remains default and `openai_compatible` plus `openai`/`qwen`/`deepseek` aliases use the same provider-neutral adapter.
- Preserved RAG/query/chat/Open WebUI contracts by changing only dependency injection and generation settings; routes still do not import provider adapters or vendor SDKs.
- Added `httpx.MockTransport` provider tests, config/factory tests, route boundary tests, and an OpenWebUI `/v1/models` real-provider wiring test that performs no external model call.
- Updated README and local development docs with real-provider scope, fake fallback, minimum env vars, curl/Open WebUI smoke commands, and current limitations.

### File List

- `.env.example`
- `README.md`
- `_bmad-output/implementation-artifacts/9-4-真实-llm-provider-接入与端到端聊天闭环.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `apps/api/service_dependencies.py`
- `docs/demo/enterprise-rag-walkthrough.md`
- `docs/operations/local-development.md`
- `packages/common/config.py`
- `packages/llm/adapters/__init__.py`
- `packages/llm/adapters/openai_compatible.py`
- `packages/llm/exceptions.py`
- `packages/rag/generation.py`
- `tests/integration/api/test_openwebui_routes.py`
- `tests/unit/common/test_config.py`
- `tests/unit/llm/test_openai_compatible_provider.py`
- `tests/unit/test_architecture_boundaries.py`
- `tests/unit/test_service_dependencies.py`

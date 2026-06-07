---
baseline_commit: NO_VCS
---

# Story 4.4: Citation Extraction 与 `/query` 问答

Status: done

生成时间：2026-06-07T15:59:41+08:00

## Story

As a 企业员工,
I want 问答结果包含可追溯 citation,
so that 我可以复核答案来源并判断可信度。

## Acceptance Criteria

1. **新增 citation extraction 领域能力**
   - Given `PromptBuilder` 已在 prompt 中暴露本次授权上下文的 citation source identifiers
   - When `CitationExtractor` 校验 LLM 生成结果
   - Then 只允许返回来自本次 `PackedContext.items[].citation_sources` 的 citation
   - And citation 至少包含 `document_id`、`version_id`、`chunk_id`、`source`、`source_uri`、`source_type`、`page_start`、`page_end`、`title_path`、`retrieval_method`、`score`
   - And 不得从 answer 文本、LLM 自述、前端 payload 或用户输入中创造 document/chunk/source/page

2. **unsupported / no-answer 策略明确**
   - Given LLM 生成了无法绑定到授权 citation source 的关键结论
   - When citation extractor 或 RAG query service 校验结果
   - Then 不伪造 citation
   - And 返回结构化 `unsupported_claims` 或等价低置信摘要，且可按策略把回答降级为 `无法从给定上下文确认。`
   - And 上下文为空、全部被过滤、generation 失败或 citation source 为空时，返回 no-answer 结果而不是外部事实或空 citation 幻觉

3. **`/query` application service 编排完整 RAG 非流式链路**
   - Given 调用方提交 `POST /query`
   - When service 执行
   - Then 按 `retrieval -> chunk content hydration -> context packing -> prompt build -> LLM generate -> citation extraction` 顺序运行
   - And tenant、user、ACL 权限仍由 retrieval 和 context packing 阶段执行，LLM 不参与权限判断
   - And route 层只解析 Pydantic schema、注入 `AuthenticatedRequestContext` 和调用 application service，不拼 prompt、不调用 LLM、不直接访问 vector store 或 chunk storage

4. **补齐 retrieval result 到 context candidate 的安全正文映射**
   - Given 当前 `RetrievalCandidate` 只包含 chunk/source/page metadata，不包含 chunk 正文
   - When `/query` 需要构造 `ContextCandidate`
   - Then 新增明确的 hydration/mapper 边界，从 `DocumentRepository.get_chunk()` 或等价 storage port 按 `tenant_id + document_id + version_id + chunk_id` 读取 chunk 正文
   - And hydration 必须再次校验 chunk `status == "active"`、`deleted_at is None`、tenant/document/version/chunk identity、ACL、page/title/source metadata 与 retrieval candidate 一致或 fail-closed
   - And 不把 chunk 正文加入 retrieval API response、retrieval log、audit metadata、error details 或 generation metadata

5. **`POST /query` API 契约使用统一 envelope**
   - Given FastAPI app 启动
   - When 请求 `POST /query`
   - Then 返回 `ApiResponse[QueryResponse]`
   - And `QueryResponse` 至少包含 `request_id`、`trace_id`、`tenant_id`、`user_id`、`answer`、`citations`、`no_answer`、`unsupported_claims`、`metadata`
   - And `metadata` 只包含安全摘要：retrieval top_k/result_count、context item/source counts、prompt risk counts、model/provider/version、token usage、latency_ms、error_code
   - And response 不包含 prompt 全文、chunk 正文全文、完整 query、embedding/vector、provider raw response、API key、access token、本机绝对路径或 SQL

6. **审计和可观测性覆盖问答行为**
   - Given `/query` 成功或失败
   - When application service 完成或抛出预期领域错误
   - Then 记录 audit action，例如 `rag.query`
   - And audit/log metadata 包含 `request_id`、`trace_id`、`tenant_id`、`user_id`、latency、retrieval top_k/result_count、context item/source counts、model、token usage、citation_count、unsupported_count、error_code
   - And 敏感字段按现有 `redact_mapping` / allowlist 模式过滤，不记录用户原文 query、prompt、chunk content 或 provider raw payload

7. **错误边界和回滚语义稳定**
   - Given retrieval、hydration、context packing、prompt build、generation 或 citation extraction 任一阶段失败
   - When service 捕获预期 domain error
   - Then 返回现有 structured error envelope，保留 request/trace/tenant/user 和阶段 error_code
   - And audit failure 被记录；如涉及数据库写入，失败时执行 rollback 或遵循已有 repository commit/rollback 约定
   - And unauthorized/missing source 使用同形错误，不泄露跨租户或未授权文档是否存在

8. **测试覆盖 RAG query 主路径和安全边界**
   - Given 单元测试运行
   - When 执行 RAG 和 API 测试
   - Then 覆盖 citation extractor 多来源、页码缺失、重复 source、unsupported answer、no-answer、伪造 citation 拒绝
   - And 覆盖 `/query` application service 成功链路、无上下文链路、hydration 身份不匹配、ACL deny、generation metadata、安全 response metadata
   - And 覆盖 FastAPI `/query` route 使用 fake provider/mock ports，不调用真实 OpenAI/Qwen/DeepSeek/Ollama/vLLM、网络、Docker 或外部服务

9. **文档更新标记 RAG answer 闭环进度**
   - Given story 完成
   - When 阅读 `README.md#RAG Foundation` 和 `docs/operations/local-development.md`
   - Then 文档说明 `/query` 非流式问答、citation extraction、FakeLLMProvider 本地链路和测试命令
   - And 明确 `/chat`、SSE streaming、Source Inspector `/sources/resolve`、Open WebUI adapter、chat memory、真实 provider adapter 和 RAG citation eval runner 仍在后续 story

## Tasks / Subtasks

- [x] 扩展 RAG DTO 与异常（AC: 1, 2, 5, 7）
  - [x] 在 `packages/rag/dto.py` 新增 `Citation`、`UnsupportedClaim`、`CitationExtractionTrace`、`CitationExtractionResult`、`QueryRequest`/`QueryCommand`、`QueryResponse` 或等价 DTO，保持 Pydantic v2 frozen model 风格。
  - [x] citation DTO 必须从 `PackedCitationSource` 派生，不接受 raw dict 或 answer 文本中的来源字段。
  - [x] 在 `packages/rag/exceptions.py` 新增稳定错误码，例如 `RAG_CITATION_INVALID_SOURCE`、`RAG_CITATION_EXTRACTION_FAILED`、`RAG_QUERY_FAILED`、`RAG_QUERY_CONTEXT_UNAVAILABLE`。
  - [x] 所有 details 只保留 request/trace/tenant/user、stage、counts、document/version/chunk IDs；不得含 query/prompt/chunk content/provider raw response。

- [x] 新增 `CitationExtractor`（AC: 1, 2）
  - [x] 创建 `packages/rag/citation_extractor.py`。
  - [x] 输入使用 answer text、`PackedContext` 或 `PromptBuildResult.citation_source_ids` + source map、策略配置。
  - [x] 先实现确定性 MVP 策略：返回本次 packed context 中实际提供的 source list，按去重和 score/order 排序；如果后续解析 answer marker，只能解析 `PromptBuilder` 生成的本次 `cite-*` allowlist。
  - [x] 对 LLM 输出中疑似伪造的 document_id/chunk_id/source/page 不信任，不把它提升为 citation。
  - [x] 支持 no-answer 检测：answer 等于或语义上退化为默认 no-answer 文案时 citation 可为空，但必须保留 no-answer metadata。

- [x] 新增 retrieval candidate 到 context candidate 的 hydration/mapper（AC: 3, 4, 7）
  - [x] 创建 `packages/rag/query.py`、`packages/rag/hydration.py` 或等价应用边界模块。
  - [x] 使用 `packages.data.ports.DocumentRepositoryPort.get_chunk()` 或当前 `DocumentRepository.get_chunk()`，按 retrieval candidate 的 tenant/document/version/chunk scope 获取 `ChunkRecord.content`。
  - [x] 将 `RetrievalCandidate + ChunkRecord` 映射为 `ContextCandidate`，token_count 使用 chunk record；score 需归一到 `0..1` 以满足 `ContextCandidate` 校验。
  - [x] 再次校验 chunk `status == "active"`、`deleted_at is None`、tenant/document/version/chunk 一致、ACL 允许当前 AuthContext、page/title/source metadata 不冲突。
  - [x] 对 hydration missing/mismatch/ACL deny fail-closed；错误 details 不暴露未授权资源存在性。
  - [x] 不修改 `/retrieve` response 去返回 content；正文只进入 RAG context packing。

- [x] 新增非流式 RAG query application service（AC: 3, 5, 6, 7）
  - [x] 新增 `RagQueryService` 或 `RagQueryApplicationService`，组合 `RetrievalService`、chunk repository、`ContextPacker`、`PromptBuilder`、`RagGenerationService`、`CitationExtractor`、`AuditPort`。
  - [x] 构造 `RetrievalRequest` 时沿用 context 的 `request_id`/`trace_id` 和 body 的 `query/top_k/metadata_filter/score_threshold`。
  - [x] 不复用 `RetrieveApplicationService.retrieve()` 作为 query 内部输入，因为它返回的是 API response DTO 且不含 content；可复用其日志/audit helper 思路，必要时抽公共安全摘要函数。
  - [x] 对空 retrieval 或全部 hydration/context packing 后为空返回 no-answer response，仍记录 audit success 或按产品策略记录 controlled no-answer。
  - [x] generation 仅通过已注入的 `RagGenerationService`/`LLMProvider` 执行，默认 fake provider。
  - [x] metadata 汇总只使用各阶段 trace 的安全 counts，不拼接 prompt、query 或 chunk content。

- [x] 新增 `/query` API route 和依赖装配（AC: 3, 5）
  - [x] 创建 `apps/api/routes/query.py`。
  - [x] route 签名遵循现有 `/retrieve`：`context: AuthenticatedRequestContextDep`、`service: RagQueryApplicationServiceDep`、`body: QueryRequestBody`。
  - [x] 更新 `apps/api/main.py` include query router。
  - [x] 更新 `apps/api/service_dependencies.py`，在单个 DB session 中装配 vector store、embedding provider、hybrid retriever、reranker、chunk repository、audit port、fake LLM provider、context packer、prompt builder、citation extractor 和 query service。
  - [x] 避免复制大段 retrieval dependency 逻辑；如需要，提取内部 builder helper，但保持改动局部。

- [x] 单元测试（AC: 1, 2, 4, 6, 7, 8）
  - [x] 新增 `tests/unit/rag/test_citation_extractor.py`，覆盖多来源、重复 source 去重、页码缺失、no-answer、unsupported、伪造 citation 拒绝。
  - [x] 新增 `tests/unit/rag/test_query_service.py`，用 fake retriever/chunk repo/LLM/audit 覆盖完整非流式 `/query` 编排。
  - [x] 测试 hydration 身份不匹配、missing chunk、wrong tenant、deleted/inactive chunk、ACL deny、score normalization、safe error details。
  - [x] 测试 query response metadata 不含 `query`、`prompt`、`content`、`raw_response`、`api_key`、`token`、本机绝对路径。
  - [x] 扩展 `tests/unit/test_architecture_boundaries.py`，防止 `apps/api/routes/query.py` 导入 `packages.llm.adapters`、OpenAI/Qwen/DeepSeek/Ollama/vLLM SDK、vector adapter 或直接拼 prompt。

- [x] API 集成测试（AC: 5, 7, 8）
  - [x] 新增 `tests/integration/api/test_query_routes.py`，覆盖 auth required、dev auth headers 成功、统一 envelope、structured error。
  - [x] 使用 fake provider/mock 或测试数据库 fixture，不真实调用外部 LLM、网络、Docker 或模型进程。
  - [x] 验证 `/query` 返回 answer + citations，citation metadata 可供后续 `/sources/resolve` 使用。

- [x] 文档更新（AC: 9）
  - [x] 更新 `README.md#RAG Foundation`，把 citation extraction 和 `/query` 非流式回答标记为已完成能力。
  - [x] 更新 `docs/operations/local-development.md`，新增本地 `/query` 示例、dev auth headers、FakeLLMProvider 说明和测试命令。
  - [x] 明确 Story 4.5 才实现 SSE，Story 4.6 才实现 chat session memory，Story 4.7 才实现 Open WebUI adapter/source detail 前端契约。

- [x] 验证（AC: 1-9）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/rag tests/unit/llm tests/unit/retrieval tests/unit/auth -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/api/test_query_routes.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/api tests/integration/storage -q`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`
  - [x] 如成本可接受，运行 `.venv\Scripts\python.exe -m pytest -q`

### Review Findings

- [x] [Review][Patch] CitationExtractor 会把伪造或未绑定回答包装成可信 citation [packages/rag/citation_extractor.py:43]
- [x] [Review][Patch] 空 `citation_source_ids` 会 fail-open 为允许全部 packed sources [packages/rag/citation_extractor.py:41]
- [x] [Review][Patch] `/query` 缺少端点级 RBAC gate [apps/api/routes/query.py:13]
- [x] [Review][Patch] 真实 API 路径中的 `rag.query` audit 可能不会持久化 [apps/api/service_dependencies.py:102]
- [x] [Review][Patch] `rag.query` audit metadata 缺少模型、token usage 和 context source counts [packages/rag/query.py:268]
- [x] [Review][Patch] hydration 错误对 unauthorized/missing 暴露可区分原因和资源 ID [packages/rag/hydration.py:193]
- [x] [Review][Patch] `/query` 输入缺少执行 retrieval 前的 query 长度和输出 token 上限 [packages/rag/dto.py:438]

## Dev Notes

### Current Repository State

- 当前目录不是 git repository；`git status --short` 返回 `fatal: not a git repository`。不要依赖 git history，最近上下文来自 sprint story 文件和源码扫描。
- Epic 4 前三条已完成：
  - Story 4.1：`ContextPacker` 已能按授权、预算、去重、相邻合并输出 `PackedContext` 和 `PackedCitationSource`。
  - Story 4.2：`PromptBuilder` 已将上下文和 citation source 放入 untrusted context，明确 prompt injection 和 no-answer 策略。
  - Story 4.3：`packages/llm`、`FakeLLMProvider`、`RagGenerationService` 已完成；真实厂商 adapter 仍后置。
- `/retrieve` 已存在，但它面向 API response，不返回 chunk content。`/query` 不能拿 `/retrieve` response 直接拼 prompt。
- `DocumentRepository.get_chunk()` 已可按 `tenant_id + chunk_id + document_id/version_id` 获取 `ChunkRecord`，其中包含 `content`、`token_count`、ACL、page/title/source metadata。

### Files Likely To Touch

- NEW `packages/rag/citation_extractor.py`：citation allowlist、unsupported/no-answer extraction。
- NEW or UPDATE `packages/rag/query.py`：RAG query application orchestration and hydration mapper。
- UPDATE `packages/rag/dto.py`：新增 query/citation DTO；保持现有 `ContextCandidate`、`PackedCitationSource`、`PromptBuildResult` 不破坏。
- UPDATE `packages/rag/exceptions.py`：新增 citation/query 错误码和 domain exceptions。
- UPDATE `packages/rag/__init__.py`：导出新 DTO/service/extractor。
- NEW `apps/api/routes/query.py`：薄路由，只调用 application service。
- UPDATE `apps/api/main.py`：include query router。
- UPDATE `apps/api/service_dependencies.py`：装配 query service；注意复用 fake provider factory 和 retrieval pipeline builder。
- UPDATE `packages/data/ports.py` only if需要更明确的 chunk hydration port；优先复用已有 `DocumentRepositoryPort.get_chunk()`。
- NEW tests under `tests/unit/rag/` and `tests/integration/api/test_query_routes.py`。
- UPDATE `README.md` and `docs/operations/local-development.md`。

### Existing Patterns To Reuse

- API route pattern: `apps/api/routes/retrieve.py` 使用 `ApiResponse[T]`、`success_response`、`AuthenticatedRequestContextDep` 和 application service dependency。`/query` 应复制这个薄路由形态。
- Retrieval application safety: `packages/retrieval/application.py` 已有安全 metadata、audit event、retrieval log、query summary、sensitive key 过滤思路。`/query` 可借鉴，但不要把 chunk content 放入任何 log。
- Context packing: `ContextPacker.pack()` 已二次执行 tenant/ACL 校验，并生成 `PackedCitationSource`；hydration 后应把 retrieval candidate 映射成 `ContextCandidate` 再交给它。
- Prompt builder: `PromptBuilder.build()` 已验证 packed context trace identity，source IDs 通过内部 `_citation_id` 生成。不要在 `/query` 中自行拼 prompt。
- Generation: `RagGenerationService.generate()` 已验证 prompt trace 与 request context identity，并返回安全 `GenerationMetadata`。
- Provider fake-first: `apps/api/service_dependencies.py` 已有 `_llm_provider_from_settings()`，目前只支持 fake。

### Architecture Requirements

- 本 story 位于 RAG Application Service + API Layer boundary：
  - `packages/rag` owns context packing、prompt building、generation orchestration、citation extraction。
  - `apps/api/routes/query.py` 只做 HTTP schema + dependency injection。
  - `packages/data` storage 只负责 chunk hydration，不承担 RAG 业务规则。
- 默认链路必须保持：

```text
query
 -> AuthContext
 -> dense + sparse retrieval with ACL filters
 -> RRF merge + dedup
 -> rerank
 -> threshold
 -> chunk content hydration with fail-closed checks
 -> context packing
 -> prompt build
 -> LLM generate
 -> citation extraction
 -> audit/log safe summary
```

- 权限必须在 retrieval 和 context packing/hydration 阶段执行；禁止让 LLM、prompt、前端或 citation extractor 判断用户是否有权访问来源。
- Citation 是结构化后端结果，不是 LLM 文字装饰。前端和 LLM 都不得补造 citation。

### Critical Implementation Guardrails

- Do not add chunk `content` to `RetrievalCandidate`, `RetrieveResponse`, retrieval logs, audit metadata, generation metadata, or API error details unless there is a separate security review. Use a private hydration mapper for RAG only.
- Do not parse arbitrary `document_id` or `chunk_id` from answer text as truth. At most, parse citation markers that exactly match this request's allowlist; MVP can attach selected sources deterministically.
- Do not implement `/chat`, SSE, `/sources/resolve`, Open WebUI adapter, chat memory, eval runner, real provider adapters, Tool Registry, or Agent code in this story.
- Do not import `openai`, `dashscope`, `deepseek`, `ollama`, `vllm` or add SDK dependencies.
- Do not call vector store, embedding provider, or LLM provider from FastAPI route.
- Do not persist prompt text or chunk text in `metadata`, audit logs, retrieval logs, exceptions, test snapshots, or documentation examples.
- Treat page numbers as optional: if both `page_start` and `page_end` are missing, return citation without page; never invent page values for UI friendliness.

### Suggested DTO Shape

```python
class Citation(BaseModel):
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
    retrieval_method: str
    score: float
```

```python
class QueryRequestBody(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str
    top_k: int = Field(default=10, ge=1, le=100)
    metadata_filter: dict[str, object] = Field(default_factory=dict)
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    answer_style: str | None = None
    max_output_tokens: int | None = None
```

```python
class QueryResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    answer: str
    citations: tuple[Citation, ...] = ()
    no_answer: bool = False
    unsupported_claims: tuple[UnsupportedClaim, ...] = ()
    metadata: Mapping[str, object] = Field(default_factory=FrozenDict)
```

These are guidance, not forced exact names. Preserve the contract and safe metadata semantics.

### Previous Story Intelligence

- Story 4.3 review already fixed identity validation in `RagGenerationService`. `/query` must preserve the same request/trace/tenant/user through retrieval, packing, prompt, generation, citation extraction and audit.
- Story 4.2 review fixed citation metadata consistency and prompt injection issues. `/query` must not introduce a new route-side prompt path that bypasses `PromptBuilder`.
- Story 4.1 made `PackedCitationSource` the reliable source of citation metadata. Use it instead of re-reading LLM output.
- Prior stories consistently use fake providers and deterministic tests. Keep tests hermetic; no network, no real model process.

### UX / Product Notes

- Citation chips and Source Inspector depend on structured citation metadata: document/version/chunk/source/page/title_path/retrieval_method/score.
- Missing page numbers should render as source metadata or chunk identity, not invented pages.
- Source detail remains a future `/sources/resolve` story; `/query` only needs to return enough citation identifiers for that future endpoint.
- User-visible trust signals should include `request_id`, citation count, no-answer status and safe generation/retrieval metadata.

### Latest Technical Information

- No new latest-version external dependency is required for this story. Use the versions already pinned by the repo (`FastAPI`, `Pydantic v2`, `SQLAlchemy 2.x`, `pytest`, `ruff`, `mypy`) and the existing fake provider.
- Real OpenAI/Qwen/DeepSeek/Ollama/vLLM adapter behavior is out of scope; do not add current SDK-specific assumptions to `/query`.
- SSE semantics are out of scope until Story 4.5; this story should implement only non-streaming `/query`.

### References

- `_bmad-output/planning-artifacts/epics.md#Story-4.4-Citation-Extraction-与-query-问答`
- `_bmad-output/planning-artifacts/epics.md#Epic-4-可信-RAG-问答-Citation-与流式会话`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-16-Citation-Answer`
- `_bmad-output/planning-artifacts/architecture.md#Authorized-Source-Detail-Contract`
- `_bmad-output/planning-artifacts/architecture.md#Project-Structure-Boundaries`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md#Global-Behavior-Contracts`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/DESIGN.md#Behavior-Rules`
- `project-context.md`
- `_bmad-output/implementation-artifacts/4-1-context-packing-与上下文预算.md`
- `_bmad-output/implementation-artifacts/4-2-promptbuilder-与-prompt-injection-防护.md`
- `_bmad-output/implementation-artifacts/4-3-llmprovider-抽象与-fake-生成.md`
- `packages/rag/dto.py`
- `packages/rag/context_packer.py`
- `packages/rag/prompt_builder.py`
- `packages/rag/generation.py`
- `packages/retrieval/service.py`
- `packages/retrieval/application.py`
- `packages/data/storage/repositories.py`
- `apps/api/routes/retrieve.py`
- `apps/api/service_dependencies.py`

## Validation Checklist

Validation Result: PASS（2026-06-07T15:59:41+08:00）

- [x] Story 明确了角色、目标和收益。
- [x] Acceptance Criteria 覆盖 citation extraction、unsupported/no-answer、`/query` orchestration、hydration、API envelope、audit/log/error/test/docs。
- [x] Tasks 拆分到具体文件、模块边界和验证命令。
- [x] Dev Notes 明确当前源码状态和关键缺口：retrieval candidate 不含 content，需要安全 hydration。
- [x] 明确复用 `ContextPacker`、`PromptBuilder`、`RagGenerationService`、`FakeLLMProvider`、`DocumentRepository.get_chunk()`。
- [x] 明确 route 不拼 prompt、不调用 LLM/vector store、不直接访问 storage。
- [x] 明确 citation 只来自本次授权 packed context，不来自 LLM/前端/用户文本。
- [x] 明确不实现 SSE、chat、Source Inspector、Open WebUI adapter、真实 provider、Agent。
- [x] 测试要求覆盖主路径、安全失败、metadata redaction、API envelope 和 architecture boundaries。

## Change Log

- 2026-06-07: Created comprehensive Story 4.4 developer context for citation extraction, non-streaming `/query`, secure chunk hydration, audit/log safety, tests and docs.
- 2026-06-07: Implemented Story 4.4 citation extraction, secure hydration, non-streaming `/query`, API wiring, tests, docs, and validation.

## Dev Agent Record

### Agent Model Used

### Debug Log References

- 2026-06-07: Red-phase tests failed on missing `CitationExtractor`/query exports as expected.
- 2026-06-07: Fixed safe metadata allowlist so query responses retain token usage counts without query/prompt/chunk content.
- 2026-06-07: Added `content` to shared sensitive content keys after structured-error route test showed it was not redacted.
- 2026-06-07: Validation passed: `pytest tests/unit/rag tests/unit/llm tests/unit/retrieval tests/unit/auth -q` => 193 passed.
- 2026-06-07: Validation passed: `pytest tests/integration/api/test_query_routes.py -q` => 8 passed.
- 2026-06-07: Validation passed: `pytest tests/integration/api tests/integration/storage -q` => 84 passed.
- 2026-06-07: Validation passed: `ruff check .`, `mypy apps packages tests`, and full `pytest -q` => 492 passed.
- 2026-06-07: Code review patches fixed citation fail-open behavior, query RBAC, audit persistence/metadata, hydration error shape, and query/token bounds.
- 2026-06-07: Review validation passed: `pytest tests/unit/rag tests/unit/llm tests/unit/retrieval tests/unit/auth tests/unit/common -q` => 228 passed; `pytest tests/integration/api/test_query_routes.py -q` => 11 passed; `pytest tests/integration/api tests/integration/storage -q` => 88 passed; `ruff check .`; `mypy apps packages tests`; full `pytest -q` => 504 passed.

### Completion Notes List

- Added citation DTOs, query DTOs, citation extraction trace/result types, and stable RAG citation/query error codes.
- Implemented deterministic `CitationExtractor` that only returns citations derived from current `PackedCitationSource` values and records no-answer/unsupported/forged-reference summaries.
- Implemented `RetrievalCandidateHydrator` with tenant/document/version/chunk identity checks, active/deleted checks, ACL checks, metadata conflict checks, score normalization, and fail-closed safe errors.
- Implemented `RagQueryApplicationService` for non-streaming RAG orchestration: retrieval, hydration, context packing, prompt build, fake/provider-backed generation, citation extraction, safe metadata, and `rag.query` audit events.
- Added thin `POST /query` FastAPI route and dependency wiring that reuses the retrieval pipeline builder and injects `FakeLLMProvider` through `RagGenerationService`.
- Added unit, integration, and architecture-boundary coverage for citation extraction, query service, secure hydration, API envelope/errors, and route import boundaries.
- Updated README and local-development docs for `/query`, citation extraction, FakeLLMProvider local flow, validation commands, and future Story 4.5/4.6/4.7 boundaries.

### File List

- apps/api/main.py
- apps/api/routes/query.py
- apps/api/service_dependencies.py
- packages/common/logging.py
- packages/rag/__init__.py
- packages/rag/citation_extractor.py
- packages/rag/dto.py
- packages/rag/exceptions.py
- packages/rag/hydration.py
- packages/rag/query.py
- tests/integration/api/test_query_routes.py
- tests/unit/rag/test_citation_extractor.py
- tests/unit/rag/test_query_service.py
- tests/unit/test_architecture_boundaries.py
- README.md
- docs/operations/local-development.md
- _bmad-output/implementation-artifacts/4-4-citation-extraction-与-query-问答.md
- _bmad-output/implementation-artifacts/sprint-status.yaml

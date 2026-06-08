---
baseline_commit: aad38b5
---

# Story 7.1: Source Metadata 安全展示策略

Status: done

生成时间：2026-06-08T18:11:17+08:00

## Story

As a 平台负责人,
I want citation、SSE、Open WebUI adapter、`rag_search` observation 和 `/sources/resolve` 返回统一的安全 source 展示字段,
so that 演示和生产接入不会泄露本机路径、object key、内部存储路径或未授权来源。

## Acceptance Criteria

1. **统一安全 source 展示 DTO 和 sanitizer**
   - Given 内部 retrieval candidate、packed context、citation source、source resolve record 或 `rag_search` result 包含 `source_uri`
   - When 构造任何外部响应或工具 observation
   - Then 必须通过同一个 framework-free sanitizer 产生 `source_display_name`、`source_type`、document/version/chunk/page metadata、`title_path`、可选 `source_ref`
   - And sanitizer 必须位于 RAG/domain-friendly 模块，例如 `packages/rag/source_metadata.py` 或等价模块，不依赖 FastAPI、SQLAlchemy、provider SDK、Redis、MinIO、Open WebUI

2. **公开响应不返回原始 storage locator**
   - Given `source_uri` 是 Windows/Unix 本地路径、`file://`、S3/MinIO object URI、bucket/object key、内部 `minio://`、HTTP URL、`kb://`、空值或恶意文本
   - When sanitizer 执行
   - Then 公开字段不得包含本机绝对路径、UNC 路径、`file://`、bucket/object key、query token、access token、内部对象定位符或完整 URL
   - And 无法安全展示时返回受控 placeholder，例如 `Untitled source` 或 `Source unavailable`，不能回退到原始 URI
   - And 内部 DTO 可继续保留 `source_uri` 用于授权、解析和审计，但外部 API/schema 不得把它作为展示字段输出

3. **Citation、Query/Chat 和 SSE 事件使用统一安全字段**
   - Given `/query`、`/chat` 或 streaming RAG 产生 `Citation`
   - When 返回非流式 response、`citation` SSE event、`final` SSE event 或 OpenAI-compatible final metadata chunk
   - Then citation payload 包含 `source_display_name`、`source_type`、document/version/chunk/page/title metadata、retrieval_method、score
   - And 不包含 `source_uri`
   - And citation 仍只能来自授权 packed context，不能从 answer 文本或前端输入补造

4. **Open WebUI adapter 扩展字段保持兼容且安全**
   - Given Open WebUI 或 OpenAI-compatible client 调用 `POST /v1/chat/completions`
   - When adapter 返回 non-stream response 或 streaming final/error chunk
   - Then `citations` extension fields 使用与后端 `/chat` 相同的安全 citation shape
   - And adapter 不把 client `system`、`developer`、`tools`、`tool_choice` 当成后端权限或 source visibility 策略
   - And adapter metadata redaction 不泄露 prompt、chunk content、provider raw response、raw URI、token、query string secret 或 path

5. **`/sources/resolve` 返回 Source Inspector 需要的安全 metadata**
   - Given 用户点击 citation 调用 `POST /sources/resolve`
   - When 后端重新校验 AuthContext、tenant、RBAC、ACL、soft delete、document/version/chunk identity、version visibility 和 chunk active status
   - Then 成功 response 只返回授权 excerpt、安全摘要、`source_display_name`、`source_type`、document/version/chunk/page/title metadata、retrieval_method、score、request_id、trace_id
   - And 不返回原始 `source_uri`、object key、local path、bucket path、完整 URL、完整原文或 ACL 规则
   - And denied/missing/deleted/invisible/ACL failed 继续使用同一类 safe denial shape，不暴露资源是否存在

6. **`rag_search` tool observation 不泄露 source URI**
   - Given Agent Runtime 调用 `rag_search`
   - When tool output 返回 results
   - Then result item 只包含安全 citation identifiers、`source_display_name`、`source_type`、page/title/score/retrieval_method 和 summary
   - And 不返回 `source_uri`、ACL、tenant/user scope、metadata maps、chunk text、raw retrieval query、SQL、vector、embedding 或 internal locator
   - And final answer validation 继续只信任本次 run 的授权 `rag_search` observation evidence

7. **测试覆盖 path/URI 泄露回归和 schema 迁移**
   - Given 单元测试运行
   - When 验证 sanitizer、Citation DTO、streaming、OpenWebUI adapter、SourceResolveService、`rag_search`
   - Then 覆盖 Windows path、Unix path、UNC path、`file://`、`s3://`、`minio://`、HTTP URL with token、`kb://`、blank value、malicious title/source markers
   - And 覆盖旧字段 `source_uri` 不再出现在外部 JSON payload 中；内部 storage/data DTO 不受影响
   - And 使用 fake/in-memory service、repository 或 DTO fixtures，不调用真实 LLM、embedding、vector store、PostgreSQL、Redis、MinIO、Open WebUI、网络或外部 provider

8. **文档、README 和架构边界同步**
   - Given Story 7.1 实现完成
   - When 更新文档
   - Then README Build Status / RAG Foundation / Current Limits 说明 safe source display 已完成，Epic 7 进入 in-progress
   - And `docs/operations/local-development.md` 的 `/retrieve`、`/chat`、SSE、Open WebUI、`/sources/resolve` 示例改用 `source_display_name`，明确原始 `source_uri` 不作为公开展示字段
   - And 架构边界测试覆盖新 sanitizer 模块和更新后的 routes/services，不允许 route 导入 storage、SQLAlchemy、provider SDK 或 vector store adapter

## Tasks / Subtasks

- [x] 定义统一 source metadata sanitizer 和公开 DTO（AC: 1, 2, 7, 8）
  - [x] 新增 `packages/rag/source_metadata.py` 或等价模块，定义 `SafeSourceMetadata` / `SourceDisplayMetadata`。
  - [x] 输入支持 `source`、`source_uri`、`source_type`、`title_path`、document/version/chunk/page metadata。
  - [x] 输出至少包含 `source_display_name`、`source_type`、`title_path`、`document_id`、`version_id`、`chunk_id`、`page_start`、`page_end`。
  - [x] 对 local path、UNC、`file://`、S3/MinIO URI、object key、URL query secrets、prompt-like titles 执行 fail-closed sanitization。
  - [x] 保持模块 framework/storage/provider neutral；必要时扩展 architecture boundary test。

- [x] 更新 RAG citation DTO 和生成路径（AC: 3, 7）
  - [x] 修改 `packages/rag/dto.py` 中公开 `Citation` shape，移除或弃用外部 `source_uri`，新增 `source_display_name`。
  - [x] `Citation.from_source()` 必须调用统一 sanitizer，不直接透传 `PackedCitationSource.source_uri`。
  - [x] `CitationExtractor` 的 forged reference 检测不要把 raw `source_uri` 加入可被模型引用的 allowed token；如需要，只允许 `source_display_name` 和 structured IDs。
  - [x] 更新 `tests/unit/rag/test_citation_extractor.py`、`test_query_service.py` 和相关 DTO tests。

- [x] 更新 SSE streaming 和 OpenAI-compatible adapter（AC: 3, 4, 7）
  - [x] 更新 `packages/rag/streaming.py` 测试期望：`citation` 和 `final` payload 中 citations 使用安全 shape。
  - [x] 更新 `packages/rag/openwebui.py` 的 `_final_extension_fields()` 和 non-stream response DTO 序列化，确保 extension `citations` 不含 `source_uri`。
  - [x] 保持 `/chat/stream` 命名 SSE 协议和 `/v1/chat/completions` data-only SSE 协议不互相混用。
  - [x] 覆盖 OpenAI-compatible stream error chunk 不泄露 raw URI/path/token。

- [x] 更新 `/sources/resolve` response 契约（AC: 5, 7, 8）
  - [x] 修改 `packages/rag/source_resolver.py` 的 `SourceResolveResponse`，移除公开 `source_uri`，新增 `source_display_name`。
  - [x] `_response_from_records()` 使用统一 sanitizer；不要保留局部 `_safe_source_uri()` 作为第二套策略。
  - [x] 保留当前二次授权矩阵：tenant、document/version/chunk identity、deleted/status、retrieval_ready version、chunk active、document/version/chunk ACL。
  - [x] 更新 `tests/unit/rag/test_source_resolver.py` 和 `tests/integration/api/test_sources_routes.py`，覆盖原始 URI 不出现在 response JSON 或 audit metadata。

- [x] 更新 retrieval API 和 Agent `rag_search` observation（AC: 6, 7）
  - [x] 评估 `packages/retrieval/application.py` 的 `RetrieveCandidateResponse` 是否仍应公开 `source_uri`；如 `/retrieve` 是外部 API，则改为安全 source display shape。
  - [x] 修改 `packages/agent/tools/rag_search.py` 的 `RagSearchResultItem`，使用 `source_display_name` 替代 `source_uri`。
  - [x] 更新 `tests/unit/agent/test_rag_search_tool.py`，保证 `s3://`、`file://`、HTTP token URL、local path 不进入 output。
  - [x] 确认 `packages/agent/final_answer.py` 的 citation validation 仍基于 document/version/chunk/page evidence，不依赖 raw source URI。

- [x] 更新 docs、README 和示例（AC: 8）
  - [x] 更新 `README.md`：Build Status 从 Epic 7 backlog 改为 Story 7.1 complete/in-progress 状态，说明 safe source display 已落地。
  - [x] 更新 `docs/operations/local-development.md`：`/retrieve`、`/chat`、SSE、Open WebUI、`/sources/resolve` 示例改用 `source_display_name`。
  - [x] 如需要新增 `docs/api/source-metadata.md`，明确 internal `source_uri` 与 public display metadata 的边界。
  - [x] 文档继续保留 Source Inspector 可访问性规则：WCAG 2.2 AA、键盘焦点、`aria-live`、alert region、drawer/sheet 焦点恢复、非纯颜色状态和长 ID 换行/截断。

- [x] 验证（AC: 1-8）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/rag/test_source_metadata.py tests/unit/rag/test_citation_extractor.py tests/unit/rag/test_streaming.py tests/unit/rag/test_openwebui_adapter.py tests/unit/rag/test_source_resolver.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/agent/test_rag_search_tool.py tests/unit/agent/test_final_answer_validation.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/api/test_query_routes.py tests/integration/api/test_chat_routes.py tests/integration/api/test_openwebui_routes.py tests/integration/api/test_sources_routes.py -q`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/test_architecture_boundaries.py tests/unit/test_readme_expectations.py -q`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`

### Review Findings

- [x] [Review][Patch] Error payload redaction still leaks raw locators and token-bearing URLs [packages/rag/openwebui.py:491]
- [x] [Review][Patch] Source metadata sanitizer allows two-part object keys into public display fields [packages/common/source_metadata.py:213]
- [x] [Review][Patch] Source metadata sanitizer can expose split object locator parts in title_path [packages/common/source_metadata.py:106]
- [x] [Review][Patch] Prompt citation metadata still exposes raw source values to the LLM [packages/rag/prompt_builder.py:403]
- [x] [Review][Patch] Public citation DTO accepts unsafe direct source_display_name values [packages/rag/dto.py:373]
- [x] [Review][Patch] Agent citation extraction keeps an unsanitized legacy source fallback [packages/agent/runtime.py:891]
- [x] [Review][Patch] Public source metadata conversion can fail closed by raising on partial page ranges [packages/retrieval/application.py:91]

## Dev Notes

### Current Repository State

- Git baseline for this story context: `aad38b5 fix(agent): address final answer validation review findings`.
- Worktree is not clean before this story creation. Existing modified files are `README.md`、`_bmad-output/planning-artifacts/epics.md`、`_bmad-output/implementation-artifacts/sprint-status.yaml`。本 story 创建只应新增 7.1 story 文件并更新 sprint status 中 7.1 状态；实现阶段不得覆盖这些已有无关改动。
- Sprint status auto-selected `7-1-source-metadata-安全展示策略` as the first backlog story in Epic 7.
- Epic 1-6 are done. Existing RAG chain includes authorized retrieval, context packing, PromptBuilder, LLMProvider fake generation, citation extraction, SSE streaming, chat memory, OpenWebUI-compatible adapter and `/sources/resolve`.
- Story 4.7 already implemented `/v1/models`、`/v1/chat/completions`、`/sources/resolve` and source resolve denial matrix. Story 7.1 must harden cross-surface source metadata, not rebuild Open WebUI adapter or Source Resolve from scratch.

### Existing Leak Surfaces To Fix

- `packages/rag/dto.py`: `ContextCandidate`、`PackedCitationSource`、`PackedContextItem` and `Citation` all carry `source_uri`; `Citation.from_source()` currently copies raw `source_uri` into public citation DTO.
- `packages/rag/streaming.py`: `CitationEventPayload` and `FinalEventPayload` serialize `Citation`; current tests expect `source_uri` in SSE payload.
- `packages/rag/openwebui.py`: `OpenAIChatCompletionResponse.citations` and `_final_extension_fields()` serialize `Citation` directly into OpenAI-compatible extension fields.
- `packages/rag/source_resolver.py`: `SourceResolveResponse` exposes `source_uri`; `_safe_source_uri()` allows `http://`、`https://` and `kb://`, which is safer than raw paths but still not the unified display contract required by Story 7.1.
- `packages/retrieval/application.py`: `RetrieveCandidateResponse` exposes `source_uri` for `/retrieve` candidates. If `/retrieve` remains external, it must follow the same public display contract.
- `packages/agent/tools/rag_search.py`: `RagSearchResultItem` exposes `source_uri`; agent observation should expose only safe source summaries and citation identifiers.
- Current tests explicitly assert `source_uri` in `tests/unit/rag/test_streaming.py`、`tests/unit/rag/test_openwebui_adapter.py`、`tests/unit/rag/test_source_resolver.py` and `tests/unit/agent/test_rag_search_tool.py`; implementation must update these expectations intentionally.

### What Must Be Preserved

- Internal ingestion/storage records can keep `source_uri` for governance, dedup, object storage lookup and source resolution. The goal is public response sanitization, not deleting internal provenance.
- `/sources/resolve` must continue to recheck AuthContext, tenant, RBAC, ACL, soft delete, retrieval-ready version visibility, chunk active status and identity before returning excerpts.
- Existing safe denial semantics must remain: unauthorized, missing, deleted, invisible and ACL-denied references use the same external shape and must not disclose whether the source exists.
- OpenWebUI adapter must continue to reuse `ChatApplicationService` / RAG chain and must not parse citations from answer text.
- `/chat/stream` named SSE and OpenAI-compatible data-only SSE are separate contracts; Story 7.1 changes citation payload shape, not streaming protocol framing.
- Agent final answer validation must continue to validate document/version/chunk/page citation evidence from authorized `rag_search` observations; it should not depend on raw `source_uri`.

### Architecture Requirements

- Layer: RAG/Application DTO boundary plus Agent tool adapter output. Routes remain thin.
- New sanitizer must be pure Python/Pydantic logic under `packages/rag` or shared common domain-friendly module.
- Do not add LangChain/LangGraph/LlamaIndex/Haystack/OpenAI SDK dependencies.
- Do not move source visibility logic into prompt text, Open WebUI client settings or frontend code.
- Do not let the frontend decide whether a URL/path/source is safe to display.
- Do not log raw URI/path/object key/token in audit metadata, retrieval logs, stream metadata or error details.

### Suggested Public Shape

The exact names can evolve, but keep the contract stable and explicit:

```python
class SafeSourceMetadata(BaseModel):
    source_display_name: str
    source_type: str
    document_id: str
    version_id: str
    chunk_id: str
    page_start: int | None = None
    page_end: int | None = None
    title_path: tuple[str, ...]
```

Public citation/source resolve/tool output should look like:

```json
{
  "document_id": "doc-1",
  "version_id": "v1",
  "chunk_id": "chunk-1",
  "source_display_name": "policy.md",
  "source_type": "markdown",
  "page_start": 1,
  "page_end": 2,
  "title_path": ["Policy", "Leave"],
  "retrieval_method": "hybrid",
  "score": 0.91
}
```

### Previous Story Intelligence

- Story 4.7 review already found and fixed one local path leak in source resolve. 7.1 generalizes that patch into a reusable policy across all public source surfaces.
- Story 4.5 established stream event DTOs and safe error event redaction; do not loosen stream error allowlists while changing citation payload shape.
- Story 6.2 and 6.7 established that `rag_search` observation and final answer validation use safe citation identifiers. 7.1 must make the source display fields equally safe without weakening citation evidence.
- Story 6.7 tightened citation identifier validation and rejected malformed/free-text source claims. Keep that stricter posture.

### Git Intelligence

- Recent commits:
  - `aad38b5 fix(agent): address final answer validation review findings`
  - `8de1bc4 feat(agent): add final answer validation`
  - `e4f737f fix(agent): address tool call audit review findings`
  - `4b36bc1 feat(agent): add durable tool call persistence`
  - `c6d2496 fix(agent): address agent run review findings`
- The recent Agent work repeatedly tightened metadata redaction, provenance binding and audit safety. Follow the same pattern: fail closed, use structured DTOs, and test leak regressions directly.

### Latest Technical Information

- No dependency upgrade is required. Current repository pins are FastAPI `>=0.136.3,<0.137`、Pydantic `>=2.13.4,<3`、SQLAlchemy `>=2.0.50,<3`、pytest `>=9.0.0,<10`。
- Open WebUI documentation still positions OpenAI-compatible servers behind `/v1/chat/completions` and model discovery via `/v1/models`; Story 7.1 should preserve these endpoint contracts and only change backend extension citation metadata to the safe source shape.
- OpenAI-compatible streaming remains data-only SSE chunks with terminal `[DONE]`; do not replace it with named backend SSE events.
- Sources:
  - Open WebUI docs, "Starting with OpenAI-Compatible Servers"（访问日期 2026-06-08）: https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-openai-compatible/
  - OpenAI Chat Completions API docs（访问日期 2026-06-08）: https://platform.openai.com/docs/api-reference/chat

### References

- `_bmad-output/planning-artifacts/epics.md#Story-7.1-Source-Metadata-安全展示策略`
- `_bmad-output/planning-artifacts/epics.md#Epic-7-Open-WebUI-展示闭环与生产接入硬化`
- `_bmad-output/planning-artifacts/architecture.md#Frontend-Architecture`
- `_bmad-output/planning-artifacts/architecture.md#API-&-Communication-Patterns`
- `_bmad-output/planning-artifacts/architecture.md#Authentication-&-Security`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md#Component-Patterns`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/DESIGN.md#Component-Styles`
- `_bmad-output/implementation-artifacts/4-7-open-webui-chat-adapter-source-detail-与轻量前端契约.md`
- `_bmad-output/implementation-artifacts/6-7-agent-final-answer-validation.md`
- `project-context.md#6-RAG-实现规则`
- `project-context.md#13-Prompt-Injection-防护`
- `project-context.md#18-可观测性规则`
- `packages/rag/dto.py`
- `packages/rag/citation_extractor.py`
- `packages/rag/streaming.py`
- `packages/rag/openwebui.py`
- `packages/rag/source_resolver.py`
- `packages/retrieval/application.py`
- `packages/agent/tools/rag_search.py`
- `packages/agent/final_answer.py`
- `tests/unit/rag/test_streaming.py`
- `tests/unit/rag/test_openwebui_adapter.py`
- `tests/unit/rag/test_source_resolver.py`
- `tests/unit/agent/test_rag_search_tool.py`
- `tests/unit/test_architecture_boundaries.py`
- `README.md`
- `docs/operations/local-development.md`

## Validation Checklist

Validation Result: PASS（2026-06-08T18:11:17+08:00）

- [x] Story 明确了内部 `source_uri` 与公开 source display metadata 的边界。
- [x] Acceptance Criteria 覆盖 citation、SSE、OpenWebUI adapter、`/sources/resolve`、`rag_search`、测试、文档和架构边界。
- [x] Tasks 指向当前已存在的 UPDATE 文件，避免重建已有 OpenWebUI adapter 或 Source Resolve。
- [x] Dev Notes 记录当前泄露面、必须保留的授权行为、前序 story lessons 和 recent git patterns。
- [x] 明确测试使用 fake/in-memory/mock，不调用真实 LLM、embedding、vector store、PostgreSQL、Redis、MinIO、Open WebUI、网络或外部 provider。
- [x] 明确 README 只在功能实现完成时更新；本 create-story 仅创建 story 文件和 sprint status。

## Change Log

- 2026-06-08: Implemented Story 7.1 safe source metadata display across RAG citations, SSE, OpenWebUI, Source Resolve, `/retrieve`, and `rag_search`.

- 2026-06-08: Created comprehensive Story 7.1 developer context for safe source metadata display across RAG, OpenWebUI, Source Resolve and Agent `rag_search`.

- 2026-06-08: Addressed code review findings for safe source metadata fail-closed behavior, prompt source redaction, error detail redaction, and Agent legacy source handling.

## Dev Agent Record

### Agent Model Used

Codex GPT-5

### Debug Log References

- 2026-06-08: Red phase confirmed `tests/unit/rag/test_source_metadata.py` failed before `packages.rag.source_metadata` existed.
- 2026-06-08: Full regression passed with `.venv\Scripts\python.exe -m pytest -q` (`834 passed`).
- 2026-06-08: Quality gates passed with `.venv\Scripts\python.exe -m ruff check .` and `.venv\Scripts\python.exe -m mypy apps packages tests`.
- 2026-06-08: Code review fix regression passed with `.venv\Scripts\python.exe -m pytest -q` (`845 passed`), `.venv\Scripts\python.exe -m ruff check .`, and `.venv\Scripts\python.exe -m mypy apps packages tests`.

### Completion Notes List

- Implemented unified framework-free safe source metadata sanitizer in `packages.common.source_metadata`, re-exported through `packages.rag.source_metadata`.
- Migrated public `Citation`, `/retrieve`, `/sources/resolve`, OpenWebUI citation extension fields, SSE citation/final payloads, and `rag_search` observations to `source_display_name` without public `source_uri`.
- Preserved internal `source_uri` on ingestion/storage/packing DTOs for governance and source resolution while keeping public surfaces fail-closed.
- Updated README, local development docs, source metadata API contract docs, architecture boundary tests, and leak regression tests.
- Fixed review findings by hardening object-key detection, split title path sanitization, public error metadata redaction, prompt citation source display, direct Citation construction, Agent legacy source fallback, and partial page range public conversion.

### File List

- README.md
- docs/api/source-metadata.md
- docs/operations/local-development.md
- packages/common/source_metadata.py
- packages/rag/source_metadata.py
- packages/rag/__init__.py
- packages/rag/dto.py
- packages/rag/citation_extractor.py
- packages/rag/source_resolver.py
- packages/retrieval/application.py
- packages/agent/runtime.py
- packages/agent/tools/rag_search.py
- tests/unit/rag/test_source_metadata.py
- tests/unit/rag/test_citation_extractor.py
- tests/unit/rag/test_streaming.py
- tests/unit/rag/test_openwebui_adapter.py
- tests/unit/rag/test_source_resolver.py
- tests/unit/agent/test_rag_search_tool.py
- tests/unit/agent/test_runtime.py
- tests/unit/memory/test_chat_application_service.py
- tests/unit/retrieval/test_retrieve_application.py
- tests/integration/api/test_query_routes.py
- tests/integration/api/test_chat_routes.py
- tests/integration/api/test_retrieve_routes.py
- tests/integration/api/test_sources_routes.py
- tests/unit/test_architecture_boundaries.py
- tests/unit/test_readme_expectations.py

---
baseline_commit: NO_VCS
---

# Story 4.1: Context Packing 与上下文预算

Status: done

生成时间：2026-06-07T13:40:10+08:00

## Story

As a 企业员工,
I want 系统只把最相关且授权的上下文交给 LLM,
so that 回答更准确且不会泄露无权限内容。

## Acceptance Criteria

1. **Context Packer 使用纯 RAG domain 模块实现**
   - Given `packages/rag` 当前不存在
   - When 本 story 完成
   - Then 新增 `packages/rag` 领域包，至少包含 `dto.py`、`context_packer.py`、`exceptions.py`、`__init__.py`
   - And domain 代码不得导入 FastAPI、SQLAlchemy、Redis、MinIO、httpx、LLM SDK、EmbeddingProvider、VectorStore 或 storage model
   - And API route 不参与本 story，不新增 `/query`、`/chat`、SSE 或 prompt builder

2. **输入 DTO 显式携带可打包文本和治理字段**
   - Given 现有 `RetrievalCandidate` 只包含 citation-safe metadata，不包含 chunk 正文或 `token_count`
   - When 设计 context packing 输入
   - Then 定义 RAG 专用 `ContextCandidate` 或等价 DTO，包含 `content`、`token_count`、`document_id`、`version_id`、`chunk_id`、`tenant_id`、`acl`、`source/source_uri`、`source_type`、`page_start/page_end`、`title_path`、`score`、`retrieval_method`、安全 metadata
   - And DTO 可由后续 RAG application service 从已授权 retrieval result + chunk storage 解析得到
   - And 不把 SQLAlchemy `ChunkModel`、API schema 或 raw dict 直接传入 packer

3. **权限边界在 packer 内再次校验**
   - Given context packer 收到候选 chunk
   - When 候选的 `tenant_id` 不匹配当前 `AuthContext.tenant_id`，或 ACL 不允许当前用户/角色/权限访问
   - Then packer 必须拒绝并抛出稳定 `RagContextPackingError` 或等价领域错误
   - And 未授权 chunk 不得进入 packed context、trace、prompt 输入或测试输出
   - And 错误 details 只包含 request/trace、tenant/user、chunk/document/version ID、error_code 和 safe counts，不包含 chunk 正文、query 全文、prompt 或 secret

4. **按 rerank score、去重策略和 token budget 选择上下文**
   - Given retrieval 返回多个授权候选 chunk
   - When context packer 执行
   - Then 按 `score` 降序选择，分数相同时按稳定 tie-breaker 排序，例如 `(document_id, version_id, chunk_id)`
   - And 按 `chunk_id` 或 `(tenant_id, document_id, version_id, chunk_id)` 去重，不重复消耗 token budget
   - And 总 `token_count` 不得超过 `ContextPackingConfig.max_tokens`
   - And 单个候选超过预算时按配置 `drop_oversized` 或 `fail_closed` 明确处理，默认不得截断正文后假装完整

5. **相邻 chunk 合并保留可追溯 citation metadata**
   - Given 相邻 chunk 属于同一 `tenant_id/document_id/version_id/title_path`
   - And `page_start/page_end` 连续或 metadata 中的邻接序号连续
   - When `merge_adjacent=True`
   - Then packer 合并相邻内容、合计 token_count、合并页码范围
   - And packed item 保留原始 `chunk_ids` 列表和每个源 chunk 的 citation metadata
   - And 不合并跨 document、跨 version、跨 tenant、不同 title_path 或 ACL 不一致的 chunk

6. **父子上下文补齐受预算和权限约束**
   - Given 候选 metadata 包含 `parent_chunk_id`、`child_chunk_ids`、`neighbor_prev_chunk_id` 或 `neighbor_next_chunk_id`
   - When `include_parent_context` 或 `include_neighbor_context` 开启
   - Then 只能从调用方显式提供的 `related_chunks_by_id` 中补齐上下文
   - And 每个补齐 chunk 仍必须通过 tenant、ACL、metadata 和 budget 校验
   - And 补齐项在 trace 中标记 reason，例如 `parent_context`、`child_context`、`neighbor_context`
   - And 不允许 packer 自行查询数据库、向量库、对象存储或网络

7. **输出包含 packed context 和安全 trace**
   - Given packer 完成选择、去重、合并和补齐
   - When 返回 `PackedContext` 或等价 DTO
   - Then 输出包含 `items`、`total_tokens`、`budget`、`dropped_candidates`、`packing_trace`
   - And 每个 item 包含 content、token_count、document/version/chunk IDs、source、page range、title_path、score、retrieval_method、citation source chunks
   - And trace 只记录 safe counts、IDs、scores、token counts、drop reasons、merge reasons，不记录完整 query、prompt、LLM response、SQL、vectors、embedding、provider raw response、secret 或本机绝对路径

8. **单元测试覆盖核心 packing 行为**
   - Given 单元测试运行
   - When 执行本 story 测试集
   - Then 覆盖排序、去重、token budget、oversized 策略、相邻合并、页码范围、父子/邻接补齐、权限拒绝、跨租户拒绝、ACL 拒绝、空候选、稳定 tie-breaker、安全 trace/redaction
   - And 测试默认不调用真实外部 LLM、embedding API、rerank API、OpenSearch、网络服务、production PostgreSQL、Redis、MinIO、Docker 或 pgvector

9. **文档更新清楚标记 RAG 阶段进度**
   - Given 本 story 完成
   - When 阅读 `README.md#Retrieval Foundation` 和 `docs/operations/local-development.md#Retrieval Local Checks`
   - Then 文档说明 context packing 已完成、如何运行本地测试、它接收已授权候选并输出 prompt-ready context
   - And 文档仍明确 prompt building、LLMProvider、citation extraction、`/query`、`/chat`、SSE streaming、chat memory、Open WebUI adapter 不在本 story 范围

## Tasks / Subtasks

- [x] 建立 `packages/rag` 领域包（AC: 1, 2）
  - [x] 新增 `packages/rag/__init__.py`，导出稳定公共类名。
  - [x] 新增 `packages/rag/dto.py`，定义 `ContextCandidate`、`PackedContextItem`、`PackedCitationSource`、`PackedContext`、`ContextPackingTrace`、`ContextPackingConfig`。
  - [x] 新增 `packages/rag/exceptions.py`，定义稳定错误码，例如 `RAG_CONTEXT_UNAUTHORIZED_CHUNK`、`RAG_CONTEXT_BUDGET_EXCEEDED`、`RAG_CONTEXT_INVALID_CANDIDATE`、`RAG_CONTEXT_PACKING_FAILED`。
  - [x] DTO 使用 Pydantic v2 `BaseModel` 或 dataclass + 明确校验；保持类型标注完整。

- [x] 实现 `ContextPacker` 纯 domain 逻辑（AC: 3, 4, 7）
  - [x] `ContextPacker.pack(*, candidates, auth, config, related_chunks_by_id=None, request_id, trace_id) -> PackedContext`。
  - [x] 复用 `packages.vectorstores.acl.acl_allows` 和 `packages.auth.policies.build_access_filter` 或等价现有策略，避免把权限写成 prompt 文案。
  - [x] 对候选执行 tenant、ACL、metadata、token_count、content、score、page range 和 title_path 校验。
  - [x] 排序使用 `score` 降序，tie-breaker 稳定；去重使用完整 chunk identity。
  - [x] 预算选择不能超过 `max_tokens`；drop reason 至少区分 `duplicate`、`budget_exceeded`、`oversized`、`unauthorized`、`invalid_candidate`。

- [x] 实现相邻 chunk 合并（AC: 5）
  - [x] 只合并同 tenant/document/version/title_path 且 ACL 兼容的 chunk。
  - [x] 连续性优先通过 metadata 中的 `chunk_index`/`sequence` 判断；没有序号时可使用连续页码作为保守条件。
  - [x] 合并 content 时保留确定性分隔，例如单个换行；不要添加 prompt 指令。
  - [x] 合并后 `chunk_ids`、page range、source chunks、token_count、score/provenance 均可追溯。

- [x] 实现父子和邻接上下文补齐（AC: 6）
  - [x] `ContextPackingConfig` 包含 `include_parent_context`、`include_child_context`、`include_neighbor_context`、`max_related_chunks_per_candidate`。
  - [x] 只从 `related_chunks_by_id` 显式输入读取，不访问 DB/storage/vector store。
  - [x] 补齐 chunk 必须经过同样的权限和预算校验。
  - [x] 当预算不足时记录 safe drop reason，不抛出非预期异常。

- [x] 实现安全 trace 和 redaction（AC: 3, 7, 8）
  - [x] trace 记录 request_id、trace_id、tenant_id、user_id、input_count、authorized_count、packed_count、dropped_count、total_tokens、budget、drop reasons、merged groups、related context counts。
  - [x] trace 不包含 `content`、query 全文、prompt、SQL、vector、embedding、provider raw response、secret、token、本机绝对路径。
  - [x] 如果复用 `packages.common.logging.redact_mapping`，确保 sensitive keys 覆盖 `content`、`chunk_text`、`prompt`、`query`、`secret` 等。

- [x] 新增单元测试（AC: 3-8）
  - [x] 新增 `tests/unit/rag/__init__.py`。
  - [x] 新增 `tests/unit/rag/test_context_packer.py`。
  - [x] 测试 empty candidates 返回空 `PackedContext` 且 trace 安全。
  - [x] 测试 score 排序、同分 tie-breaker、duplicate drop、budget drop。
  - [x] 测试 oversized chunk 默认 drop 或 fail_closed 行为。
  - [x] 测试 adjacent merge 同文档同版本成功，跨 version/tenant/title_path/ACL 不合并。
  - [x] 测试 parent/child/neighbor related chunks 受预算和权限限制。
  - [x] 测试 unauthorized/cross-tenant/private ACL 候选拒绝且不进入 output。
  - [x] 测试 trace/error details 不泄露 chunk content、query、prompt、secret、本机路径。

- [x] 更新文档（AC: 9）
  - [x] 更新 `README.md#Retrieval Foundation` 或新增 `## RAG Foundation`，说明 context packing 已完成和仍未完成的 RAG 能力。
  - [x] 更新 `docs/operations/local-development.md`，加入 context packing 本地测试命令和安全边界。
  - [x] 不新增环境变量，除非确实需要配置默认 budget；如新增则同步 `.env.example` 和 `packages/common/config.py`。

- [x] 验证（AC: 1-9）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/rag/test_context_packer.py`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/rag tests/unit/retrieval tests/unit/auth`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`
  - [x] 如成本可接受，运行 `.venv\Scripts\python.exe -m pytest`

### Review Findings

- [x] [Review][Patch] Related context accepts unrelated chunks from the explicit map [packages/rag/context_packer.py:126] — fixed by validating related identity, lineage, and reciprocal relation metadata before selection.
- [x] [Review][Patch] Related request attempts are not capped [packages/rag/context_packer.py:125] — fixed by applying `max_related_chunks_per_candidate` to attempted related requests before lookup/drop recording.
- [x] [Review][Patch] Missing related IDs are echoed unsafely into drop records [packages/rag/context_packer.py:131] — fixed by strict related ID validation and redacted diagnostics for unsafe IDs.
- [x] [Review][Patch] Raw non-ContextCandidate inputs can raise AttributeError before stable invalid handling [packages/rag/context_packer.py:69] — fixed by filtering invalid runtime inputs before score sorting.
- [x] [Review][Patch] Page-only adjacency can merge same-page chunks without reliable order [packages/rag/context_packer.py:581] — fixed by requiring page-continuation adjacency when sequence metadata is absent.
- [x] [Review][Patch] Packing trace lacks per-related-chunk inclusion reasons [packages/rag/context_packer.py:204] — fixed by adding safe `related_context_items` trace entries with per-chunk inclusion reasons.

## Dev Notes

### Current Repository State

- 当前目录不是 git repository，`git log` 不可用；本 story 的上下文来自 sprint status、epics、architecture、PRD、project-context、Story 3.5/3.6/3.7 文件、源码扫描和本地文档。
- `packages/rag` 当前不存在；本 story 应新建 RAG domain 包。
- `tests/unit/rag` 当前不存在；本 story 应新建聚焦 context packer 的单元测试。
- `README.md#Retrieval Foundation` 和 `docs/operations/local-development.md#Retrieval Local Checks` 当前明确 context packing、prompt building、`/query`、`/chat`、SSE、RAG generation、citation eval、RAG answer eval 仍未完成。
- `pyproject.toml` 当前依赖包含 Pydantic v2、pytest、pytest-asyncio、ruff、mypy；没有 `tiktoken`、`transformers` 或 tokenizer 依赖。不要为本 story 添加模型或 tokenizer 依赖，优先使用 chunk ingestion 阶段已有的 `token_count`。

### Existing Retrieval Components To Reuse

- `packages/retrieval/dto.py`
  - `RetrievalCandidate` 包含 `document_id`、`version_id`、`chunk_id`、source/source_uri/source_type、page_start/page_end、title_path、score、retrieval_method、tenant_id、acl、metadata。
  - 它不包含 chunk 正文或 `token_count`，因为 `/retrieve` 响应必须 citation-safe。Context packing 不应修改这个 DTO 来加入正文。
  - `RetrievalRequest.score_threshold` 和 rerank output score 均使用 0..1 语义；packer 可以直接按 `candidate.score` 排序。

- `packages/retrieval/service.py`
  - `RetrievalService` 已有结果侧 guard：tenant、metadata、ACL、score_threshold、top_k。
  - Context packer 仍要重新校验 AuthContext/ACL，因为它接收的是带正文的高风险输入，不能盲目信任上游或测试 fixture。

- `packages/retrieval/rerank.py`
  - `RerankingRetriever` 已在 rerank 前 guard 授权候选，并写入安全 `metadata["rerank_provenance"]`。
  - Packer 可以保留 provenance 中的 safe score/status/rank 信息，但不能把 provenance 当作权限依据。

- `packages/retrieval/application.py`
  - `RetrieveCandidateResponse` 明确不返回 chunk content。
  - Retrieval logs 只保存 safe replay metadata。Context packing trace 应沿用这个安全风格。

- `tests/eval/retrieval/*`
  - Retrieval eval 使用 synthetic fixtures 和生产 `RetrievalService` guard，不调用真实外部 provider。
  - Story 4.1 的测试应保持同样原则：使用本地构造 DTO，不访问真实 DB、网络、provider 或 Docker。

### Architecture Requirements

- 本 story 属于 RAG Domain Layer，不属于 API Layer、Storage Layer、LLM Provider 或 Agent。
- 生产默认数据流是 `retrieval -> context packing -> prompt build -> LLM generate/stream -> citation extraction`；本 story 只实现 context packing。
- Domain 层不能依赖 FastAPI、SQLAlchemy、Redis、MinIO、httpx、external SDK 或 API schema。
- AuthContext 必须显式传入 packer；tenant、ACL、RBAC 过滤不是 prompt 规则。
- 未授权 chunk 不得进入 rerank、context packing、prompt 或最终回答。上游 retrieval 已做权限过滤，但 packer 必须保留防御性校验。
- Context packing 输出是后续 PromptBuilder 的输入，所以可以包含授权 chunk content；但 trace、logs、error details、audit metadata 不得包含 content。

### Suggested DTO Shape

```python
class ContextPackingConfig(BaseModel):
    max_tokens: int = 3000
    merge_adjacent: bool = True
    include_parent_context: bool = False
    include_child_context: bool = False
    include_neighbor_context: bool = False
    max_related_chunks_per_candidate: int = 2
    oversized_policy: Literal["drop", "fail_closed"] = "drop"
```

```python
class ContextCandidate(BaseModel):
    content: str
    token_count: int
    document_id: str
    version_id: str
    chunk_id: str
    tenant_id: str
    acl: Mapping[str, object] = Field(default_factory=FrozenDict)
    source: str | None = None
    source_uri: str | None = None
    source_type: str
    page_start: int | None = None
    page_end: int | None = None
    title_path: tuple[str, ...]
    score: float
    retrieval_method: str
    metadata: Mapping[str, object] = Field(default_factory=FrozenDict)
```

```python
class PackedContextItem(BaseModel):
    content: str
    token_count: int
    document_id: str
    version_id: str
    chunk_ids: tuple[str, ...]
    source: str | None
    page_start: int | None
    page_end: int | None
    title_path: tuple[str, ...]
    score: float
    retrieval_method: str
    citation_sources: tuple[PackedCitationSource, ...]
```

### Previous Story Intelligence

- Story 3.1 修复过 private ACL 默认放行、无效 request 不转稳定 error、service 过度信任 retriever 输出、top_k 无上限、NaN threshold、多值 metadata filter 等问题。4.1 不得信任 raw dict 或跳过 AuthContext/ACL 校验。
- Story 3.3 修复过 sparse fallback 过滤顺序、ACL SQL 语义、candidate validation error 和敏感 metadata redaction。4.1 trace 不得保存正文、SQL、query_terms、tsquery/tsvector。
- Story 3.4 完成 RRF provenance、normalized fusion score 和 safe trace。4.1 应利用 normalized `score`，不要重新实现 RRF 或 merge provenance。
- Story 3.5 完成 rerank provenance、fallback/fail_closed 和 provider output 校验。4.1 应按最终 score 排序，并把 rerank degraded/disabled 只当作 provenance，不作为绕过预算或权限的理由。
- Story 3.6 完成 `/retrieve` API 和 `retrieval_logs`，且明确 retrieval response 不返回 chunk content。4.1 需要单独定义带 content/token_count 的 RAG DTO，而不是污染 retrieval API contract。
- Story 3.7 完成 retrieval eval fixtures 和 runner。4.1 不应提前实现 RAG eval、citation eval 或 CI smoke gate，但应让输出结构便于 Epic 5 后续评估 context packing 阶段失败。

### Implementation Boundaries

- Do not implement PromptBuilder.
- Do not implement LLMProvider, fake LLM generation, streaming, `/query`, `/chat`, `/sources/resolve`, Open WebUI adapter, chat memory or citation extraction.
- Do not modify `RetrievalCandidate` to include chunk content.
- Do not make context packer query PostgreSQL, vector store, object storage, Redis, MinIO, OpenSearch or network services.
- Do not add tokenizer/model dependencies such as `tiktoken`, `transformers`, `sentence-transformers`, `torch`, `cohere`, OpenAI, Qwen, DeepSeek, vLLM or Ollama SDK.
- Do not log or return chunk content in trace/error/log/audit metadata. Packed output may contain content because it is the authorized prompt input; all non-output diagnostics must be redacted.
- Do not implement eval smoke gates, RAG answer eval, citation coverage eval or CI changes; Epic 5 owns broader eval.

### Latest Technical Information

- 本 repo 当前 pin `pydantic>=2.13.4,<3`，PyPI 在 2026-06-07 可见的稳定 2.x 最新版本仍为 2.13.4；继续使用 Pydantic v2 DTO，不需要升级依赖。Source: https://pypi.org/project/pydantic/
- Pydantic 官方 model 文档仍支持通过 model configuration 和 validators 建模结构化 DTO；本 story 应沿用现有 `BaseModel + ConfigDict(frozen=True) + field_validator` 风格。Source: https://docs.pydantic.dev/latest/concepts/models/
- Python 3.11 dataclasses 官方文档仍适合轻量内部 DTO；但本 repo 已在 retrieval/eval DTO 中主要采用 Pydantic v2，优先保持一致。Source: https://docs.python.org/3.11/library/dataclasses.html
- pytest 官方文档仍支持 `tmp_path` 等本地测试 fixture；本 story 的测试不需要新增测试依赖。Source: https://docs.pytest.org/en/latest/how-to/tmp_path.html

### UX / Product Notes

- 本 story 不实现 UI，但 Source Inspector、Knowledge Chat、Retrieval Diagnostics 后续会依赖 packed context 的 citation metadata 和 safe trace。
- 无答案是成功状态的一种；当预算或授权导致无上下文可打包时，packer 应返回空 packed context 或稳定领域错误，由后续 PromptBuilder/Generation 决定无答案文案。
- 后续前端展示长 `document_id`、`version_id`、`chunk_id`、`request_id`、`trace_id` 时必须遵守 UX 文档的换行/截断规则，但本 story 只负责保留完整机器可读 ID。

### References

- `_bmad-output/planning-artifacts/epics.md#Story-4.1-Context-Packing-与上下文预算`
- `_bmad-output/planning-artifacts/epics.md#Epic-4-可信-RAG-问答-Citation-与流式会话`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-13-Context-Packing`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-22-RBAC-与-ACL-检索过滤`
- `_bmad-output/planning-artifacts/architecture.md#Requirements-to-Structure-Mapping`
- `_bmad-output/planning-artifacts/architecture.md#Integration-Points`
- `project-context.md`
- `README.md#Retrieval-Foundation`
- `docs/operations/local-development.md#Retrieval-Local-Checks`
- `packages/retrieval/dto.py`
- `packages/retrieval/service.py`
- `packages/retrieval/rerank.py`
- `packages/retrieval/application.py`
- `packages/vectorstores/acl.py`
- `packages/auth/policies.py`
- `_bmad-output/implementation-artifacts/3-5-reranker-接口与降级策略.md`
- `_bmad-output/implementation-artifacts/3-6-retrieve-api-与检索复盘日志.md`
- `_bmad-output/implementation-artifacts/3-7-retrieval-eval-fixtures-与-smoke-runner.md`
- Pydantic PyPI: https://pypi.org/project/pydantic/
- Pydantic models docs: https://docs.pydantic.dev/latest/concepts/models/
- Python 3.11 dataclasses docs: https://docs.python.org/3.11/library/dataclasses.html
- pytest tmp_path docs: https://docs.pytest.org/en/latest/how-to/tmp_path.html

## Validation Checklist

Validation Result: PASS（2026-06-07T13:40:10+08:00）

- [x] Story 明确了用户角色、目标和收益。
- [x] Acceptance Criteria 覆盖 RAG domain 包、输入 DTO、权限重校验、排序/去重/budget、相邻合并、父子补齐、安全 trace、测试和文档。
- [x] Tasks 覆盖 DTO/exception、ContextPacker、merge、related context、trace redaction、unit tests、docs 和验证命令。
- [x] Dev Notes 明确当前源码状态，尤其是 `packages/rag` 不存在、`RetrievalCandidate` 不含 content/token_count、retrieval response 不应被污染。
- [x] 明确不实现 prompt builder、LLM、`/query`、`/chat`、SSE、citation extraction、chat memory、Open WebUI adapter 或 eval gate。
- [x] 明确 chunk content 只能存在于授权 packed output，不能进入 trace、logs、audit、error details 或 report。
- [x] Latest technical notes 已核对 Pydantic/Python/pytest 官方或 PyPI 来源，且没有引入新依赖的必要。

## Change Log

- 2026-06-07: Created comprehensive Story 4.1 developer context for context packing, token budget, ACL-safe packed context, adjacent merge, related context fill, safe trace, tests, and RAG boundary protection.
- 2026-06-07: Implemented Story 4.1 context packing domain package, tests, docs, and validation; moved story to review.
- 2026-06-07: Applied code review fixes for related context validation, safe related diagnostics, stable invalid input handling, page adjacency, and trace-level related inclusion reasons; moved story to done.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- 2026-06-07T13:54:15+08:00: Marked story and sprint status in-progress; baseline_commit preserved as NO_VCS.
- 2026-06-07T14:02:15+08:00: `git status --short` confirmed this workspace is not a git repository.
- Validation: `.venv\Scripts\python.exe -m pytest tests/unit/rag/test_context_packer.py` -> 11 passed.
- Validation: `.venv\Scripts\python.exe -m pytest tests/unit/rag tests/unit/retrieval tests/unit/auth` -> 147 passed.
- Validation: `.venv\Scripts\python.exe -m pytest` -> 437 passed.
- Validation: `.venv\Scripts\python.exe -m ruff check .` -> passed.
- Validation: `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed.
- 2026-06-07T14:26:22+08:00: Code review fixes applied; `.venv\Scripts\python.exe -m pytest tests/unit/rag/test_context_packer.py` -> 15 passed.
- 2026-06-07T14:26:22+08:00: Code review fixes applied; `.venv\Scripts\python.exe -m pytest tests/unit/rag tests/unit/retrieval tests/unit/auth` -> 151 passed.
- 2026-06-07T14:26:22+08:00: Code review fixes applied; `.venv\Scripts\python.exe -m pytest` -> 441 passed.
- 2026-06-07T14:26:22+08:00: Code review fixes applied; `.venv\Scripts\python.exe -m ruff check .` -> passed.
- 2026-06-07T14:26:22+08:00: Code review fixes applied; `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed.

### Completion Notes List

- 新增纯 domain `packages/rag` 包，包含 ContextCandidate/PackedContext DTO、ContextPacker 和稳定 RagContextPackingError 错误码；未新增 API route、provider、storage 或 tokenizer 依赖。
- ContextPacker 复用 AuthContext -> build_access_filter -> acl_allows 权限语义，未授权 chunk fail-closed，预算/重复/超大/无效候选进入安全 drop 记录。
- 实现 score 排序、稳定 tie-breaker、完整 chunk identity 去重、token budget、oversized drop/fail_closed、相邻 chunk 合并、父子/邻接补齐和安全 trace/redaction。
- 新增单元测试覆盖空输入、排序、去重、预算、oversized 策略、相邻合并、跨边界不合并、related context、跨租户/ACL 拒绝、invalid candidate 和 trace/error redaction。
- README 与本地开发文档已标记 context packing 完成，并明确 prompt building、LLMProvider、citation extraction、`/query`、`/chat`、SSE、chat memory 和 Open WebUI adapter 仍不在本 story 范围。
- Code review fixes now reject unrelated/mismatched related chunks, cap related attempts, redact unsafe related IDs, safely drop non-DTO inputs, avoid same-page page-only merges, and expose per-related-chunk inclusion reasons in safe trace.

### File List

- `_bmad-output/implementation-artifacts/4-1-context-packing-与上下文预算.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `README.md`
- `docs/operations/local-development.md`
- `packages/rag/__init__.py`
- `packages/rag/context_packer.py`
- `packages/rag/dto.py`
- `packages/rag/exceptions.py`
- `tests/unit/rag/__init__.py`
- `tests/unit/rag/test_context_packer.py`

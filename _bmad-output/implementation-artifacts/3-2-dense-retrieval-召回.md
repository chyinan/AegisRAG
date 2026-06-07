---
baseline_commit: NO_VCS
---

# Story 3.2: Dense Retrieval 召回

Status: done

生成时间：2026-06-06T21:02:14+08:00

## Story

As a 企业员工,
I want 系统通过 query embedding 在授权知识库中召回相关 chunk,
so that 后续 hybrid retrieval 可以基于语义相关且权限安全的候选结果继续融合、rerank 和回答。

## Acceptance Criteria

1. **DenseRetriever 通过既有 Provider/VectorStore 抽象召回**
   - Given `RetrievalService` 注入 dense retriever
   - When 调用 `retrieve` 并传入 `RetrievalRequest` 与 `AuthContext`
   - Then dense retriever 使用 `EmbeddingProvider.embed_texts(EmbeddingRequest)` 为 `request.query` 生成单条 query embedding
   - And 使用 `VectorStore.search(VectorSearchRequest)` 召回候选
   - And 不在 retrieval 代码中直接依赖 OpenAI、Qwen、DeepSeek、Ollama、vLLM SDK、SQLAlchemy session 或 pgvector SQL

2. **查询阶段携带 tenant、ACL、metadata、soft-delete 和 embedding model 过滤**
   - Given `build_retrieval_filter_set` 已从 `AuthContext` 和 request metadata 构建 filter set
   - When dense retriever 构造 `VectorSearchRequest`
   - Then `tenant_id` 来自 filter set/auth，不来自用户可扩大范围的输入
   - And `acl_filter` 使用 `to_vector_acl_filter(filters)`
   - And `metadata_filters` 使用 `to_vector_metadata_filters(filters)`
   - And `include_deleted` 固定为 `False`
   - And 可配置传入 `embedding_provider`、`embedding_model`、`embedding_version`、`distance_metric`、`timeout_seconds`、`retry_budget`

3. **Embedding/VectorStore 结果被映射为 RetrievalCandidate 且保留 citation metadata**
   - Given VectorStore 返回 `VectorSearchResult`
   - When dense retriever 映射候选
   - Then 每个 `RetrievalCandidate` 保留 `document_id`、`version_id`、`chunk_id`、`source`、`source_type`、`source_uri`、`page_start`、`page_end`、`title_path`、`tenant_id`、`acl`、`metadata`、`score`
   - And `retrieval_method` 必须为 `dense`
   - And 不返回 chunk 正文、完整向量、provider raw response、API key、token 或本机绝对路径

4. **错误映射和安全观测摘要稳定**
   - Given EmbeddingProvider 超时、限流、失败、返回空 batch、返回多/少于一条 query vector 或 query vector 维度与 response dim 不一致
   - When dense retriever 执行
   - Then 抛出 `RetrievalError` 或等价 retrieval domain error，code 稳定且 details 只包含 request_id、trace_id、tenant_id、user_id、top_k、embedding provider/model/version/dim、error_code 等安全摘要
   - And 不泄露 query 全文、chunk 正文、向量内容、provider raw error、secret 或本机绝对路径
   - And VectorStoreError 被映射为稳定 retrieval backend error，不把底层异常原样穿透给 service/API

5. **测试证明 dense retrieval 不绕过权限、不调用外部服务**
   - Given 使用 `FakeEmbeddingProvider` 和 `FakeVectorStore` 或最小 fake port
   - When 单测运行 dense retrieval
   - Then 覆盖成功召回、metadata filter、tenant filter、ACL filter、score threshold、top_k、soft delete 默认排除、embedding provider/model/version filter
   - And 覆盖 embedding failure、vector store failure、batch size mismatch、dimension mismatch
   - And 默认测试不访问真实 LLM、Embedding API、pgvector 服务、OpenSearch 或网络

## Tasks / Subtasks

- [x] 新增 dense retriever 实现（AC: 1, 2, 3）
  - [x] 新建 `packages/retrieval/dense.py`，实现 `DenseRetriever` 并满足 `packages.retrieval.ports.CandidateRetriever` 协议。
  - [x] 构造函数注入 `EmbeddingProvider`、`VectorStore` 和配置对象/参数；不要在类内部创建真实 provider、真实 vector store 或读取环境变量。
  - [x] 新增 `DenseRetrieverConfig` 或等价 DTO，包含 `embedding_provider`、`embedding_model`、`embedding_version`、`timeout_seconds`、`retry_budget`、`distance_metric`，并校验非空/正数。
  - [x] 调用 `EmbeddingProvider.embed_texts(EmbeddingRequest(texts=[request.query], provider=..., model=..., timeout_seconds=..., retry_budget=..., rate_limit_key=filters.tenant_id, metadata={...}))`。
  - [x] 调用 `VectorStore.search(VectorSearchRequest(...))`，传入 query vector、embedding dim、top_k、score_threshold、tenant/ACL/metadata filters、include_deleted=False、embedding provider/model/version。
  - [x] 将 `VectorSearchResult` 映射为 `RetrievalCandidate`，`retrieval_method` 统一为 `dense`，保留 citation metadata。

- [x] 明确 dense retrieval 错误码和安全 details（AC: 4）
  - [x] 扩展 `packages/retrieval/exceptions.py`，至少增加或复用稳定 code：`RETRIEVAL_EMBEDDING_FAILED`、`RETRIEVAL_VECTOR_SEARCH_FAILED`、`RETRIEVAL_BACKEND_FAILED`。
  - [x] EmbeddingProviderError 映射为 retrieval error；如果 provider error 是 timeout/rate limit，保留稳定 code，但不要暴露 raw provider message 中的敏感内容。
  - [x] VectorStoreError 映射为 retrieval/vector search error，details 只保留安全字段和 embedding model/dim 摘要。
  - [x] 对 embedding response 做守卫：必须返回 exactly one vector，vector 非空，`len(vector) == response.dim`；否则返回稳定 retrieval error。
  - [x] 不在错误 details、日志、metadata 中保存 query 全文、向量数组、chunk 正文、provider raw response、API key、access token 或本机绝对路径。

- [x] 保持 RetrievalService 编排边界（AC: 1, 2）
  - [x] `RetrievalService` 继续只依赖 `CandidateRetriever`，不直接调用 EmbeddingProvider 或 VectorStore。
  - [x] 如果需要在 `packages/retrieval/__init__.py` 暴露 dense retriever，只暴露稳定类名/DTO，不引入副作用初始化。
  - [x] 不新增 `/retrieve` route，不修改 `apps/api/main.py`，不新增 retrieval log 表或 Alembic migration。
  - [x] 不实现 BM25 sparse retriever、RRF merge、dedup、rerank、context packing、RAG generation 或 eval runner。

- [x] 补充 dense retrieval 单元测试（AC: 1-5）
  - [x] 新增 `tests/unit/retrieval/test_dense.py`，使用 `FakeEmbeddingProvider` 和 `FakeVectorStore` 验证成功召回与 `RetrievalCandidate` 映射。
  - [x] 测试 `metadata_filter`、tenant、ACL、soft delete、score threshold、top_k 在 VectorStore 查询阶段生效。
  - [x] 测试 `embedding_provider`、`embedding_model`、`embedding_version` 过滤不会混用旧模型索引。
  - [x] 测试 provider timeout/rate limit/failure、batch mismatch、dimension mismatch、vector store dimension mismatch/失败均转为 stable `RetrievalError`。
  - [x] 测试错误 details 不包含 query 全文、向量内容、provider raw error、secret、本机绝对路径。
  - [x] 扩展 `tests/unit/retrieval/test_service.py`，用真实 `DenseRetriever` 作为 `CandidateRetriever` 验证 service 仍执行结果侧 invariant guard。

- [x] 更新文档与开发说明（AC: 1-5）
  - [x] 更新 `README.md#Retrieval Foundation`，说明 dense retrieval 已可通过 provider/vectorstore 抽象执行，BM25/RRF/rerank/API/log 仍属后续 stories。
  - [x] 如新增 `docs/api/retrieval.md` 或 `docs/operations/local-development.md`，只描述当前 dense capability 和安全边界，不宣称 hybrid retrieval 已完成。
  - [x] 文档必须说明测试默认使用 fake provider/store，不调用真实外部模型或 pgvector 服务。

- [x] 验证（AC: 1-5）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/retrieval tests/unit/embeddings tests/unit/vectorstores`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`
  - [x] 如果全量成本可接受，运行 `.venv\Scripts\python.exe -m pytest`

### Review Findings

- [x] [Review][Patch] Embedding response provider/model/version is trusted, so dense retrieval can search the wrong or unversioned index [packages/retrieval/dense.py:89]
- [x] [Review][Patch] Query embedding vector validation misses response index and finite-value guards [packages/retrieval/dense.py:191]
- [x] [Review][Patch] Candidate source/metadata are passed through without redaction, so AC3 safety is not enforced [packages/retrieval/dense.py:226]
- [x] [Review][Patch] Dense tests do not actually cover all declared query-stage filters and one soft-delete negative case deletes a nonexistent record [tests/unit/retrieval/test_dense.py:31]

## Dev Notes

### Current Repository State

- 当前目录不是 git repository，`git log` 不可用；本 story 的历史上下文来自已完成 story 文件、现有源码、epics、architecture、项目规则和实际文件扫描。
- `packages/retrieval` 已存在，并在 Story 3.1 中完成 `RetrievalRequest`、`RetrievalFilterSet`、`RetrievalCandidate`、`RetrievalResult`、`CandidateRetriever`、`RetrievalService`、filter builder 和 typed exceptions。
- `RetrievalService` 现在只依赖 `CandidateRetriever`，负责强制 AuthContext、构建 filters、包装非 retrieval backend error，并在返回前做 tenant、metadata、ACL、score threshold、top_k 的结果侧守卫。
- `build_retrieval_filter_set(auth, request)` 复用 `packages.auth.policies.build_access_filter(auth)`；请求 metadata 只能收窄范围，跨 tenant metadata 会在 retriever 调用前被拒绝。
- `to_vector_acl_filter(filters)` 和 `to_vector_metadata_filters(filters)` 已可直接生成 `VectorSearchRequest` 需要的 ACL/metadata filters。
- `packages.embeddings.ports.EmbeddingProvider` 的实际签名是 `async def embed_texts(self, request: EmbeddingRequest) -> EmbeddingResponse`，不是早期示例中的 `list[str] -> list[list[float]]`。
- `FakeEmbeddingProvider` 支持 deterministic vectors 和 failure modes：`timeout`、`rate_limited`、`failed`、`batch_mismatch`、`dimension_mismatch`。
- `packages.vectorstores.ports.VectorStore.search` 接收 `VectorSearchRequest`，返回 `list[VectorSearchResult]`；`FakeVectorStore` 和 `PgVectorStore` 已在查询阶段执行 tenant、metadata、ACL、soft delete、top_k、score threshold、embedding provider/model/version 过滤。

### Architecture Requirements

- 本 story 属于 Retrieval Domain/Application boundary 与 Infrastructure port composition；不要跨到 API route、storage migration、RAG generation 或 Agent。
- Dense retrieval 必须通过 `EmbeddingProvider` 和 `VectorStore` 两个端口组合实现。业务代码不得绑定单一厂商 SDK 或直接写 pgvector SQL。
- 权限必须在查询阶段通过 `VectorSearchRequest.tenant_id`、`metadata_filters`、`acl_filter` 和 `include_deleted=False` 执行；不得先召回全量再由 LLM、prompt、RAG、前端或答案阶段过滤。
- Query embedding 是外部 I/O 路径，必须显式传 timeout/retry/rate limit 相关配置；当前 fake provider 不访问外部服务，但真实 adapter 后续必须遵守同一 request DTO。
- `embedding_provider`、`embedding_model`、`embedding_version`、`embedding_dim` 是索引兼容性边界。Dense retrieval 不得在未指定模型过滤时混用不同模型索引，除非配置明确允许且有测试覆盖。
- Candidate metadata 是后续 RRF、rerank、context packing、citation、Source Inspector 的共享契约，不能在 dense 阶段丢失。

### Current Files To Preserve And Extend

- `packages/retrieval/dto.py`
  - Current state: 定义 immutable retrieval request/filter/candidate/result DTO；`RetrievalRequest` 把 validation error 转成稳定 `RETRIEVAL_INVALID_REQUEST`。
  - Story change: 一般不需要修改，除非 dense config 放在此处更符合本地风格。
  - Preserve: `RetrievalCandidate` 必须继续包含 citation metadata 和 ACL/tenant 字段。

- `packages/retrieval/ports.py`
  - Current state: 只有 `CandidateRetriever` 协议，入参为 `RetrievalRequest` 与 `RetrievalFilterSet`。
  - Story change: `DenseRetriever` 应实现这个协议；不需要新建并行 service 协议。
  - Preserve: port 不接收裸 query string。

- `packages/retrieval/service.py`
  - Current state: service 强制 AuthContext、调用注入 retriever、包装 backend error、执行结果侧安全守卫。
  - Story change: 可增加与 dense retriever 的集成单测；除非必要，不改 service 逻辑。
  - Preserve: service 不直接调用 embedding、vectorstore、SQLAlchemy、LLM 或 reranker。

- `packages/retrieval/filters.py`
  - Current state: 提供 `to_vector_acl_filter`、`to_vector_metadata_filters`、`to_sparse_filter_payload`。
  - Story change: Dense retriever 应复用这些转换函数。
  - Preserve: request metadata 不得扩大 tenant scope；普通 retrieval `include_deleted=False`。

- `packages/embeddings/dto.py` and `packages/embeddings/ports.py`
  - Current state: `EmbeddingRequest` 包含 texts、provider、model、timeout_seconds、retry_budget、rate_limit_key、metadata、chunk_ids；`EmbeddingResponse` 包含 vectors、provider/model/version/dim/usage/latency。
  - Story change: Dense retriever 构造 single-query `EmbeddingRequest`。
  - Preserve: 不把 query embedding 当作 document chunk embedding job；不要写 embedding_jobs。

- `packages/vectorstores/dto.py` and `packages/vectorstores/ports.py`
  - Current state: `VectorSearchRequest` 已覆盖 tenant、query_vector、embedding_dim、top_k、score_threshold、metadata_filters、acl_filter、include_deleted、distance_metric、embedding provider/model/version。
  - Story change: Dense retriever 构造该 request 并映射 search results。
  - Preserve: `VectorSearchResult` 的 source/page/title_path/acl/metadata 不丢失。

- `packages/vectorstores/adapters/fake.py` and `packages/vectorstores/adapters/pgvector.py`
  - Current state: 已在查询阶段过滤 tenant、metadata、ACL、soft delete、score threshold、top_k、embedding provider/model/version。
  - Story change: 单测可以复用 FakeVectorStore；一般不需要改 adapter。
  - Preserve: private ACL 无 allow list 默认拒绝，deleted/non-active 默认不可检索。

- `README.md`
  - Current state: 明确 retrieval foundation 已完成，但 dense/BM25/RRF/rerank/API/log/RAG generation 未完成。
  - Story change: 完成实现后更新 dense retrieval 状态。
  - Preserve: 不夸大 hybrid retrieval、BM25、RRF、rerank 或 `/retrieve` 已完成。

### Previous Story Intelligence

- Story 3.1 已经修复了几个重要 review 问题：private ACL 默认拒绝、无效 request 转稳定 retrieval error、service 不信任 retriever 输出、top_k 上限、NaN threshold 拒绝、candidate score/page 校验、多值 metadata filter 禁止。
- Dense retriever 即使依赖 VectorStore 已过滤，也不能绕过 `RetrievalService` 的结果侧守卫；service guard 是防止 adapter bug 或恶意 fake 的最后一道 retrieval 层防线。
- Story 2.7 的 EmbeddingProvider 已服务文档 embedding job；dense query embedding 应复用 provider DTO，但不要复用 ingestion/embedding job 持久化流程。
- Story 2.8 的 VectorStore contract 已是 dense retrieval 的主要基础；不要重新实现向量相似度或 pgvector adapter。
- Story 2.9 强调 soft-deleted documents/versions/chunks/vectors 默认不可检索；dense path 必须继续固定普通 retrieval 不包含 deleted。

### Suggested Implementation Shape

示例仅表达目标形状，开发时应按现有代码风格落地：

```python
class DenseRetriever:
    def __init__(
        self,
        *,
        embedding_provider: EmbeddingProvider,
        vector_store: VectorStore,
        config: DenseRetrieverConfig,
    ) -> None:
        ...

    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> list[RetrievalCandidate]:
        embedding = await self._embedding_provider.embed_texts(
            EmbeddingRequest(
                texts=[request.query],
                provider=self._config.embedding_provider,
                model=self._config.embedding_model,
                timeout_seconds=self._config.timeout_seconds,
                retry_budget=self._config.retry_budget,
                rate_limit_key=filters.tenant_id,
            )
        )
        query_vector = _single_query_vector_or_raise(embedding, request, filters)
        results = await self._vector_store.search(
            VectorSearchRequest(
                tenant_id=filters.tenant_id,
                query_vector=query_vector,
                embedding_dim=embedding.dim,
                top_k=request.top_k,
                score_threshold=request.score_threshold,
                metadata_filters=to_vector_metadata_filters(filters),
                acl_filter=to_vector_acl_filter(filters),
                include_deleted=False,
                distance_metric=self._config.distance_metric,
                embedding_provider=embedding.provider,
                embedding_model=embedding.model,
                embedding_version=embedding.version,
            )
        )
        return [_candidate_from_vector_result(result) for result in results]
```

Do not copy this blindly if local DTO names change. The invariant is: one query in, one query vector out, one vector search request with auth-derived filters, safe candidate metadata out.

### Implementation Boundaries

- Do not implement BM25/PostgreSQL full text or OpenSearch sparse retriever; Story 3.3 owns it.
- Do not implement RRF merge, dedup, weighted fusion, or hybrid result provenance; Story 3.4 owns it.
- Do not implement Reranker, fake reranker, cross-encoder adapter, fallback strategy, or rerank latency; Story 3.5 owns it.
- Do not implement `POST /retrieve`, API schema, retrieval log table, Alembic migration, or route registration; Story 3.6 owns it.
- Do not implement eval fixtures/smoke runner; Story 3.7 owns it.
- Do not implement context packing, prompt building, citation extraction, LLM generation, SSE, chat, Agent, or Tool Registry.
- Do not call real external embedding APIs, real pgvector service, OpenSearch, LLM APIs, or network in default tests.

### Latest Technical Information

- Pydantic v2 remains the right fit for local DTO validation; official docs continue to document model validation, field/model validators, and model config. Keep validation at DTO boundaries rather than scattering ad hoc checks in service code. Source: https://docs.pydantic.dev/latest/concepts/models/ and https://docs.pydantic.dev/latest/concepts/validators/
- FastAPI official docs continue to use Pydantic request models and dependencies for shared request logic such as authentication. This supports keeping route concerns out of this story and relying on existing AuthContext/service boundaries. Source: https://fastapi.tiangolo.com/tutorial/body/ and https://fastapi.tiangolo.com/tutorial/dependencies/
- pgvector's official README documents vector similarity search in PostgreSQL and distance operators such as cosine/L2. In this repo, those details remain encapsulated behind `VectorStore`; dense retrieval should call the port, not write adapter SQL. Source: https://github.com/pgvector/pgvector

### UX / Product Notes

- 本 story 不实现 UI，但后续 Knowledge Chat 查询范围选择只能收窄权限，不能扩大 tenant/ACL；dense retriever 必须消费已收窄 filters。
- 后续 Retrieval Diagnostics 会需要 dense top_k、score、retrieval_method、embedding model/version/dim、request_id、trace_id 和 latency 摘要；本 story 可保留安全 metadata，但不要落库 retrieval_logs。
- Source Inspector 和 citation 依赖 document/version/chunk/source/page/title_path；dense 候选映射不得只返回 chunk_id 和 score。

### References

- `_bmad-output/planning-artifacts/epics.md#Story-3.2-Dense-Retrieval-召回`
- `_bmad-output/planning-artifacts/epics.md#Epic-3-授权-Hybrid-Retrieval-与检索复盘`
- `_bmad-output/planning-artifacts/architecture.md#Project-Structure-Boundaries`
- `_bmad-output/planning-artifacts/architecture.md#Data-Flow`
- `_bmad-output/implementation-artifacts/3-1-retrieval-请求模型与权限过滤构建.md`
- `_bmad-output/implementation-artifacts/2-7-embeddingprovider-抽象与-embedding-job.md`
- `_bmad-output/implementation-artifacts/2-8-vectorstore-协议与-pgvector-写入.md`
- `_bmad-output/implementation-artifacts/2-9-文档版本-软删除与索引状态闭环.md`
- `project-context.md`
- `packages/retrieval/dto.py`
- `packages/retrieval/ports.py`
- `packages/retrieval/service.py`
- `packages/retrieval/filters.py`
- `packages/retrieval/exceptions.py`
- `packages/embeddings/dto.py`
- `packages/embeddings/ports.py`
- `packages/embeddings/adapters/fake.py`
- `packages/embeddings/exceptions.py`
- `packages/vectorstores/dto.py`
- `packages/vectorstores/ports.py`
- `packages/vectorstores/adapters/fake.py`
- `packages/vectorstores/adapters/pgvector.py`
- `packages/vectorstores/exceptions.py`
- `tests/unit/retrieval/test_service.py`
- `tests/unit/embeddings/test_fake_provider.py`
- `tests/unit/vectorstores/test_contract.py`

## Validation Checklist

Validation Result: PASS（2026-06-06T21:02:14+08:00）

- [x] Story 明确了用户角色、目标和收益。
- [x] Acceptance Criteria 覆盖 DenseRetriever、EmbeddingProvider、VectorStore、tenant/ACL/metadata/soft-delete/model filters、candidate metadata、错误映射和 fake-only tests。
- [x] Tasks 覆盖 dense implementation、错误码、service boundary、unit tests、docs 和验证命令。
- [x] Dev Notes 明确现有接口签名，尤其是 `EmbeddingProvider.embed_texts(EmbeddingRequest)` 与 `VectorStore.search(VectorSearchRequest)`。
- [x] 明确不实现 BM25、RRF、rerank、`/retrieve` API、retrieval logs、eval runner 或 RAG。
- [x] 明确 query 全文、chunk 正文、完整向量、provider raw response、secret、本机绝对路径不得进入错误 details、日志或摘要。

## Change Log

- 2026-06-06: Created comprehensive Story 3.2 developer context for dense retrieval through EmbeddingProvider and VectorStore abstractions.
- 2026-06-06: Implemented dense retrieval through provider/vectorstore ports with tests, safe errors, service guard coverage, and README update.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- `.venv\Scripts\python.exe -m pytest tests\unit\retrieval\test_dense.py -q` -> 11 passed
- `.venv\Scripts\python.exe -m pytest tests\unit\retrieval -q` -> 47 passed
- `.venv\Scripts\python.exe -m pytest tests\unit\retrieval tests\unit\embeddings tests\unit\vectorstores` -> 67 passed
- `.venv\Scripts\python.exe -m ruff check .` -> passed
- `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed
- `.venv\Scripts\python.exe -m pytest` -> 331 passed
- `.venv\Scripts\python.exe -m pytest tests\unit\retrieval\test_dense.py -q` -> 19 passed
- `.venv\Scripts\python.exe -m pytest tests\unit\retrieval -q` -> 55 passed
- `.venv\Scripts\python.exe -m pytest tests\unit\retrieval tests\unit\embeddings tests\unit\vectorstores` -> 75 passed
- `.venv\Scripts\python.exe -m ruff check .` -> passed
- `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed
- `.venv\Scripts\python.exe -m pytest` -> 339 passed

### Completion Notes List

- Implemented `DenseRetriever` and `DenseRetrieverConfig` in retrieval layer, composing only `EmbeddingProvider` and `VectorStore` ports.
- Dense query embedding now sends a single-query `EmbeddingRequest` with timeout, retry budget, tenant rate-limit key, and safe request metadata.
- Vector search now receives tenant, ACL, metadata, score threshold, top_k, soft-delete exclusion, distance metric, and embedding provider/model/version filters.
- Vector search results are mapped to citation-safe `RetrievalCandidate` values with `retrieval_method="dense"` and without chunk text, vectors, provider raw responses, secrets, or local paths.
- Provider, invalid embedding response, and vector store failures are mapped to stable retrieval errors with safe details.
- Added dense retrieval unit coverage plus a `RetrievalService` test using a real `DenseRetriever`; updated README to describe dense capability and remaining non-goals.
- Code review patches added embedding response provider/model/version consistency checks, query vector index/finite-value validation, candidate source/metadata redaction, and stronger dense filter negative coverage.

### File List

- `packages/retrieval/dense.py`
- `packages/retrieval/exceptions.py`
- `packages/retrieval/__init__.py`
- `tests/unit/retrieval/test_dense.py`
- `tests/unit/retrieval/test_service.py`
- `README.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `_bmad-output/implementation-artifacts/3-2-dense-retrieval-召回.md`

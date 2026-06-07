---
baseline_commit: NO_VCS
---

# Story 3.4: RRF Merge、去重与阈值过滤

Status: done

生成时间：2026-06-07T01:18:00+08:00

## Story

As a 企业员工,
I want dense 和 sparse 结果被稳定融合,
so that 系统能综合语义相似和关键词精确匹配的优势。

## Acceptance Criteria

1. **HybridRetriever 通过现有 CandidateRetriever 端口组合 dense 与 sparse**
   - Given `RetrievalService` 当前只依赖一个 `CandidateRetriever`
   - When 实现 hybrid retrieval
   - Then 新增 `HybridRetriever` 或同等类实现 `packages.retrieval.ports.CandidateRetriever`
   - And 其构造函数注入 dense retriever、sparse retriever 和 RRF merger/config
   - And `RetrievalService` 仍只负责 AuthContext 必填、filter 构建、结果侧 tenant/metadata/ACL/top_k/threshold guard
   - And 不在 `RetrievalService` 或 API route 中直接调用 EmbeddingProvider、VectorStore、SQLAlchemy、PostgreSQL full text、OpenSearch、LLM、reranker 或 prompt builder

2. **RRF merge 合并重叠 chunk 并保留安全 provenance**
   - Given dense 和 sparse 返回重叠 chunk
   - When RRF merge 执行
   - Then 相同授权 chunk 合并为一个候选，去重 key 至少包含 `tenant_id`、`document_id`、`version_id`、`chunk_id`
   - And 保留 `retrieval_methods`、每个来源的原始 rank、原始 score、RRF contribution、raw RRF score、normalized fusion score 和 fusion reason
   - And 输出 candidate 仍保留 citation 必需字段：`document_id`、`version_id`、`chunk_id`、`source/source_uri`、`source_type`、`page_start/page_end`、`title_path`、`tenant_id`、`acl`
   - And provenance 不包含 query 全文、chunk 正文、SQL、tsquery/tsvector、vector、embedding、provider raw response、secret、token 或本机绝对路径

3. **RRF 分数确定且与现有 score_threshold 兼容**
   - Given RRF 使用 rank-based fusion
   - When 计算融合分数
   - Then 默认公式为 `raw_rrf_score = sum(weight(method) / (rank_constant + rank))`，rank 从 1 开始
   - And 默认 `rank_constant=60`，dense/sparse 默认 weight 均为 `1.0`，配置值必须校验为有限正数
   - And `RetrievalCandidate.score` 使用 0..1 范围内的 normalized fusion score，避免现有 `RetrievalRequest.score_threshold` 因 raw RRF 分数过小而错误过滤全部候选
   - And raw RRF score 与每个来源 contribution 保存在安全 provenance 中

4. **阈值过滤发生在融合后，且不会提前丢失单一路径强召回**
   - Given dense 或 sparse 单一路径召回候选
   - When hybrid retriever 调用子 retriever
   - Then 子 retriever 不应因最终融合阈值提前丢弃候选；如需复用 `RetrievalRequest`，应为 branch request 清除或转换 `score_threshold`
   - And 融合后再应用 `request.score_threshold` 或 `HybridMergeConfig.min_fusion_score`
   - And 低于阈值的候选不得进入最终 `RetrievalResult`
   - And filter 结果记录到内存 retrieval trace/merge trace；本 story 不落库 `retrieval_logs`

5. **排序和 tie-breaker deterministic**
   - Given 相同输入候选列表
   - When 多次执行 RRF merge
   - Then 排序结果完全 deterministic
   - And 默认排序为 normalized fusion score 降序、raw RRF score 降序、来源数量降序、最佳原始 rank 升序、`chunk_id` 升序
   - And 单测覆盖 score tie、rank tie、只在 dense 出现、只在 sparse 出现、dense+sparse 同时出现的排序行为

6. **安全边界与权限过滤不回退**
   - Given 子 retriever 返回未授权、跨 tenant、metadata 不匹配、private 默认拒绝或 `denied_users` 命中的候选
   - When hybrid retriever 或 `RetrievalService` 处理候选
   - Then 未授权候选不得进入 merge 输出、rerank、context packing 或 prompt
   - And request metadata 只能收窄范围，不能扩大 tenant/ACL
   - And dense/sparse 使用同一个 `RetrievalFilterSet`
   - And 任一 backend 返回 out-of-scope candidate 时必须被过滤或转换为稳定 `RetrievalError`，不得静默泄露

7. **测试证明融合、去重、阈值和安全 provenance**
   - Given 单元测试运行
   - When 使用 deterministic fake dense/sparse retrievers
   - Then 覆盖重叠 chunk 去重、RRF 公式、权重配置、normalized score、阈值过滤、deterministic tie-breaker、empty branch、branch failure、安全 error details 和 service guard
   - And 默认测试不访问真实外部 LLM、Embedding API、OpenSearch、网络或生产 PostgreSQL

## Tasks / Subtasks

- [x] 定义 RRF merge DTO 与配置（AC: 2, 3, 5）
  - [x] 新增 `packages/retrieval/rrf.py`，包含 `HybridMergeConfig`、`FusionSource`、`FusionTrace` 或等价结构。
  - [x] config 至少包含 `rank_constant`、`dense_weight`、`sparse_weight`、`min_fusion_score`、`max_candidates_per_branch`。
  - [x] 校验所有权重、rank constant、threshold/top_k 为有限值和合法范围；错误映射到 retrieval domain error 或配置 validation error。
  - [x] 不把 fusion provenance 混入不安全字段；如复用 `RetrievalCandidate.metadata`，必须使用 namespaced key，例如 `retrieval_provenance`，且不得覆盖文档 metadata。

- [x] 实现 RRFMerger 纯逻辑（AC: 2, 3, 4, 5）
  - [x] 输入为 dense candidates 和 sparse candidates，不接收 AuthContext，不查询数据库，不调用外部 provider。
  - [x] 按 `(tenant_id, document_id, version_id, chunk_id)` 去重，保留同一 chunk 的多个来源。
  - [x] 计算每个来源原始 rank、原始 score、contribution、raw RRF score 和 normalized fusion score。
  - [x] 输出 `retrieval_method="hybrid"` 或明确等价标记，并保留原始 `retrieval_methods=("dense", "sparse")` provenance。
  - [x] 对单来源候选也记录 fusion reason，例如 `dense_only`、`sparse_only`、`dense_sparse_overlap`。
  - [x] 应用融合后 threshold，并生成内存 trace：input counts、deduped count、filtered count、threshold、rank_constant、weights。

- [x] 实现 HybridRetriever 编排（AC: 1, 4, 6）
  - [x] `HybridRetriever` 实现 `CandidateRetriever.retrieve(request, filters)`。
  - [x] 构造函数注入 `dense_retriever: CandidateRetriever`、`sparse_retriever: CandidateRetriever`、`merger`、config；不要在内部创建 provider/store/session。
  - [x] dense 和 sparse 必须接收同一个 `RetrievalFilterSet`。
  - [x] 为 branch retrieval 避免最终 threshold 过早过滤候选；建议创建 branch `RetrievalRequest`，保留 query/top_k/metadata/request_id/trace_id，但将 `score_threshold=None` 或使用 branch-specific threshold。
  - [x] 子 retriever failure 策略必须明确：MVP 默认 fail-closed 返回稳定 `RetrievalError`；如支持 degrade，必须有配置、测试和安全 trace，且不得引入未授权候选。
  - [x] `RetrievalService` 不需要知道 dense/sparse 数量，只接收 `HybridRetriever` 并保留现有 guard。

- [x] 扩展 retrieval DTO 或安全 provenance 映射（AC: 2, 6）
  - [x] 评估是否给 `RetrievalCandidate` 增加可选 `retrieval_methods`/`fusion` 字段，或在 metadata 中放 namespaced provenance；选择必须不破坏现有 dense/sparse tests。
  - [x] 如果扩展 DTO，保持字段可选并兼容现有 dense/sparse candidate。
  - [x] 如果复用 metadata，确保原始文档 metadata 不被覆盖，metadata filter 仍能读取原业务字段如 `department`。
  - [x] 所有 provenance 必须经过与 dense/sparse candidate 一致的脱敏策略。

- [x] 补充错误码和安全错误 details（AC: 6, 7）
  - [x] 在 `packages/retrieval/exceptions.py` 增加或复用稳定 code，例如 `RETRIEVAL_HYBRID_MERGE_FAILED`、`RETRIEVAL_HYBRID_BRANCH_FAILED`。
  - [x] error details 只包含 request_id、trace_id、tenant_id、user_id、top_k、retrieval_method/hybrid_stage、branch、error_code、safe counts。
  - [x] 不记录 query 全文、chunk 正文、SQL raw error、vector、embedding、provider raw response、secret、token、本机绝对路径。

- [x] 补充单元测试（AC: 1-7）
  - [x] 新增 `tests/unit/retrieval/test_rrf.py`，覆盖 RRF 公式、rank 从 1 开始、默认 `rank_constant=60`、权重配置、normalized score。
  - [x] 覆盖 dense+sparse 重叠 chunk 去重，并断言 provenance 包含来源方法、原始 rank、原始 score、contribution、fusion reason。
  - [x] 覆盖只在 dense、只在 sparse、empty dense、empty sparse、两个分支都空。
  - [x] 覆盖 threshold 是融合后执行，且 branch request 不提前应用最终 score_threshold。
  - [x] 覆盖 deterministic tie-breaker。
  - [x] 覆盖 out-of-scope candidate、private ACL 默认拒绝、denied_users 优先拒绝、metadata filter、tenant filter 通过 `RetrievalService` guard 仍生效。
  - [x] 覆盖 branch backend failure 或 merger validation failure 映射为稳定安全 `RetrievalError`。

- [x] 更新导出与文档（AC: 1, 7）
  - [x] 更新 `packages/retrieval/__init__.py` 导出 `HybridRetriever`、`HybridMergeConfig` 或稳定公共类名。
  - [x] 更新 `README.md#Retrieval Foundation`，说明 dense+sparse 已可通过 RRF merge 组合，但 rerank、`POST /retrieve`、retrieval_logs、context packing、RAG 仍未完成。
  - [x] 更新 `docs/operations/local-development.md#Retrieval Local Checks`，记录 RRF 本地测试命令、阈值语义、provenance 安全边界和当前非目标。

- [x] 验证（AC: 1-7）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/retrieval/test_rrf.py`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/retrieval tests/unit/vectorstores tests/unit/auth`
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/storage`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`
  - [x] 如全量成本可接受，运行 `.venv\Scripts\python.exe -m pytest`

### Review Findings

- [x] [Review][Decision] Single-branch normalization denominator is ambiguous — resolved by choosing consensus weighting: denominator uses all enabled branch weights, preserving a boost for dense+sparse overlap. No code change required.
- [x] [Review][Patch] Same-branch duplicate chunks can double-count RRF contribution [packages/retrieval/rrf.py:172]
- [x] [Review][Patch] Hybrid error details omit required safe counts [packages/retrieval/rrf.py:423]
- [x] [Review][Patch] Equal scores with identical chunk IDs can retain backend insertion order [packages/retrieval/rrf.py:360]
- [x] [Review][Patch] Sensitive metadata aliases bypass exact-key redaction [packages/retrieval/rrf.py:387]
- [x] [Review][Patch] Required sorting and single-empty-branch test coverage is incomplete [tests/unit/retrieval/test_rrf.py:147]

## Dev Notes

### Current Repository State

- 当前目录不是 git repository，`git log`/`git status` 不可用；本 story 的上下文来自 sprint status、epics、architecture、PRD、project-context、3.3 story 文件、源码扫描和本地测试结构。
- `packages/retrieval` 当前文件：`dto.py`、`ports.py`、`filters.py`、`service.py`、`dense.py`、`sparse.py`、`exceptions.py`、`__init__.py`。
- `RetrievalService` 当前只依赖一个 `CandidateRetriever`，负责 AuthContext 必填、`build_retrieval_filter_set`、包装非 retrieval backend error、结果侧 tenant/metadata/ACL/score_threshold/top_k guard。
- `CandidateRetriever` 协议入参固定为 `RetrievalRequest` 和 `RetrievalFilterSet`；不要新增只接裸 query string 的 hybrid service。
- `RetrievalRequest.score_threshold` 当前要求 0..1；RRF raw score 通常远小于 1，因此 3.4 必须输出 normalized fusion score 或另行处理 threshold，避免用户设置 `0.5` 时错误过滤所有融合候选。
- `RetrievalCandidate` 当前包含单个 `retrieval_method: str`、`score: float`、citation metadata、tenant、ACL 和 metadata。是否扩展 DTO 要谨慎，保持 dense/sparse 现有测试兼容。
- `DenseRetriever` 已通过 `EmbeddingProvider` + `VectorStore` 召回，并将候选映射为 `retrieval_method="dense"`。
- `PostgresSparseRetriever` 已通过 PostgreSQL full text/fake backend 召回，并将候选映射为 `retrieval_method="sparse"`。
- `filters.py` 已提供 `to_vector_acl_filter`、`to_vector_metadata_filters`、`to_sparse_filter_payload`；hybrid 不应重新解释 AuthContext。
- `packages/vectorstores.acl.acl_allows` 是 ACL 语义基准：`denied_users` 优先拒绝；`public`/`tenant` 可见；`private` 必须命中 allowed_users/roles/departments/permissions，否则拒绝。
- `README.md#Retrieval Foundation` 和 `docs/operations/local-development.md#Retrieval Local Checks` 当前明确 RRF/rerank/API/log/RAG 尚未完成；完成 3.4 后只能更新 RRF 状态，不能宣称完整 retrieval pipeline 完成。

### Architecture Requirements

- 本 story 属于 Retrieval Domain/Application boundary；不涉及 API route、retrieval_logs 持久化、RAG、Agent 或 eval runner。
- Hybrid merge 必须在 dense/sparse 已应用同源 filter 的基础上运行；结果侧仍由 `RetrievalService` guard 做最后防线。
- 生产默认 retrieval flow 是 optional rewrite -> dense + sparse -> RRF merge + dedup -> rerank -> threshold -> context packing。本 story 只交付 dense + sparse 后的 RRF merge、dedup 和融合后 threshold。
- Domain 代码不得导入 FastAPI、SQLAlchemy、Redis、MinIO、httpx、外部 LLM SDK 或 prompt builder。
- `apps/api` 不参与本 story；不要新增 `/retrieve` route。
- 不要让 prompt 或 LLM 参与权限、融合或阈值判断。

### Current Files To Preserve And Extend

- `packages/retrieval/dto.py`
  - Current state: `RetrievalRequest`、`RetrievalFilterSet`、`RetrievalCandidate`、`RetrievalResult` 已稳定；request threshold 是 0..1，candidate score 要求 finite。
  - Story change: 可选择新增可选 fusion/provenance 字段，或不改 DTO、在 metadata namespaced key 中保存安全 provenance。
  - Preserve: 不把 chunk content、query text、vectors、SQL raw row、tsvector、provider raw response 放进 candidate。

- `packages/retrieval/ports.py`
  - Current state: `CandidateRetriever` 是 dense/sparse 的统一端口。
  - Story change: `HybridRetriever` 应实现同一协议。
  - Preserve: port 不接收 AuthContext，不重新做权限策略解释；权限只来自 `RetrievalFilterSet`。

- `packages/retrieval/service.py`
  - Current state: service 是候选召回守卫入口，有安全错误 details 和结果侧 ACL/metadata/threshold/top_k guard。
  - Story change: service 测试应证明它能接受 `HybridRetriever`。
  - Preserve: service 不直接组合 dense/sparse、不实现 RRF、不调用外部 adapter。

- `packages/retrieval/dense.py`
  - Current state: dense retriever 有 safe error details、candidate redaction、embedding provider/model/version 校验。
  - Story change: 作为 hybrid 的一个 branch retriever 被注入。
  - Preserve: dense 行为和测试不得因 hybrid 变化回归。

- `packages/retrieval/sparse.py`
  - Current state: sparse retriever 有 tolerant query parsing、PostgreSQL full text SQL、SQLite fallback、safe details、candidate redaction。
  - Story change: 作为 hybrid 的一个 branch retriever 被注入。
  - Preserve: sparse query-stage filter 与 ACL 语义不得回退。

- `tests/unit/retrieval/test_dense.py`、`test_sparse.py`、`test_service.py`
  - Current state: 已建立 deterministic fake/provider/backend 和安全断言风格。
  - Story change: 新增 `test_rrf.py`，并可少量扩展 `test_service.py`。
  - Preserve: 默认测试不依赖网络、真实 LLM、真实 embedding API、OpenSearch 或生产 PostgreSQL。

### Previous Story Intelligence

- Story 3.1 建立了 retrieval filter contract，并修复 private ACL 默认放行、无效 request 不转稳定 error、service 过度信任 retriever 输出、top_k 无上限、NaN threshold、多值 metadata filter 等问题。3.4 不得回退这些边界。
- Story 3.2 的 DenseRetriever 已证明 `RetrievalService` 能接受任何 `CandidateRetriever`，并建立 embedding provider/model/version 一致性和 candidate 脱敏风格。
- Story 3.3 的 SparseRetriever 已接入同一端口，且 code review 修复了 PostgreSQL query term cap、backend timeout、fallback 过滤顺序、ACL SQL 语义、candidate validation error 和敏感 metadata redaction。
- 3.4 的主要风险是把 merge 写进 `RetrievalService` 或 route，或者把 provenance 写进不安全 metadata 导致后续 citation/source inspector 泄露内部细节。
- 3.4 不能实现 3.5 的 reranker、3.6 的 `/retrieve` API/retrieval_logs、3.7 的 eval runner，也不能宣称完整 hybrid retrieval 闭环完成。

### Suggested Implementation Shape

示例只表达目标结构，开发时按现有本地风格落地：

```python
class HybridRetriever:
    def __init__(
        self,
        *,
        dense_retriever: CandidateRetriever,
        sparse_retriever: CandidateRetriever,
        merger: RRFMerger,
        config: HybridMergeConfig,
    ) -> None:
        self._dense_retriever = dense_retriever
        self._sparse_retriever = sparse_retriever
        self._merger = merger
        self._config = config

    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> list[RetrievalCandidate]:
        branch_request = request.model_copy(update={"score_threshold": None})
        dense = await self._dense_retriever.retrieve(request=branch_request, filters=filters)
        sparse = await self._sparse_retriever.retrieve(request=branch_request, filters=filters)
        return self._merger.merge(
            request=request,
            filters=filters,
            dense_candidates=dense,
            sparse_candidates=sparse,
        )
```

RRF score shape:

```text
raw_rrf_score = sum(weight(method) / (rank_constant + rank(method)))
max_possible = sum(enabled_weights) / (rank_constant + 1)
normalized_fusion_score = raw_rrf_score / max_possible
```

Store raw score and per-method contributions in safe provenance, but expose `RetrievalCandidate.score` as normalized score so existing threshold semantics remain usable.

### Implementation Boundaries

- Do not implement query rewrite.
- Do not implement reranker protocol, FakeReranker, cross-encoder adapter, LLM rerank, rerank latency or rerank fallback; Story 3.5 owns these.
- Do not implement `POST /retrieve`, API schema, route registration, retrieval log table or retrieval log persistence; Story 3.6 owns these.
- Do not implement eval fixtures or smoke runner; Story 3.7 owns these.
- Do not implement context packing, prompt building, citation extraction, LLM generation, SSE, chat, Agent, Tool Registry or Source Inspector.
- Do not call real external embedding APIs, LLM APIs, OpenSearch, network services or production PostgreSQL in default tests.
- Do not log or return query full text, chunk content, SQL raw text, vectors, embedding, provider raw responses, API keys, access tokens or local absolute paths.

### Latest Technical Information

- RRF was introduced as a simple rank-fusion method for combining document rankings from multiple IR systems and was shown in SIGIR 2009 to outperform individual systems and Condorcet Fuse in the cited experiments. Source: https://research.google/pubs/reciprocal-rank-fusion-outperforms-condorcet-and-individual-rank-learning-methods/
- The original RRF formulation uses reciprocal rank contributions and a constant `k` to reduce the impact of top-ranked outliers while still retaining contribution from lower-ranked documents. Source: https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf
- OpenSearch documentation describes RRF as adding reciprocal rank-based scores from multiple query clauses into a unified ranking; `rank_constant` is required to be at least 1, and larger constants make scores more uniform. Source: https://docs.opensearch.org/latest/search-plugins/search-pipelines/score-ranker-processor/
- OpenSearch's hybrid-search RRF guidance uses a default rank constant of 60 and notes RRF avoids score-normalization issues when different query methods have incompatible score scales. Source: https://opensearch.org/blog/introducing-reciprocal-rank-fusion-hybrid-search/

### UX / Product Notes

- 本 story 不实现 UI，但后续 Retrieval Diagnostics 需要展示 dense/sparse/RRF/rerank/context packing trace；3.4 的 in-memory merge trace 应保留足够安全摘要。
- 后续 Source Inspector 只应展示授权 citation 和必要 score/provenance 摘要，不展示完整文档正文、query 全文、SQL 或 provider raw output。
- 前端选择知识范围只能收窄权限，不能扩大 tenant/ACL；3.4 必须继续依赖 `RetrievalFilterSet`。

### References

- `_bmad-output/planning-artifacts/epics.md#Story-3.4-RRF-Merge-去重与阈值过滤`
- `_bmad-output/planning-artifacts/epics.md#Epic-3-授权-Hybrid-Retrieval-与检索复盘`
- `_bmad-output/planning-artifacts/architecture.md#Project-Structure-Boundaries`
- `_bmad-output/planning-artifacts/architecture.md#Integration-Points`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-10-Hybrid-Merge`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-22-RBAC-与-ACL-检索过滤`
- `project-context.md`
- `_bmad-output/implementation-artifacts/3-1-retrieval-请求模型与权限过滤构建.md`
- `_bmad-output/implementation-artifacts/3-2-dense-retrieval-召回.md`
- `_bmad-output/implementation-artifacts/3-3-bm25-sparse-retrieval-召回.md`
- `packages/retrieval/dto.py`
- `packages/retrieval/ports.py`
- `packages/retrieval/service.py`
- `packages/retrieval/filters.py`
- `packages/retrieval/dense.py`
- `packages/retrieval/sparse.py`
- `packages/retrieval/exceptions.py`
- `packages/vectorstores/acl.py`
- `tests/unit/retrieval/test_dense.py`
- `tests/unit/retrieval/test_sparse.py`
- `tests/unit/retrieval/test_service.py`
- `README.md#Retrieval-Foundation`
- `docs/operations/local-development.md#Retrieval-Local-Checks`
- RRF SIGIR 2009 listing: https://research.google/pubs/reciprocal-rank-fusion-outperforms-condorcet-and-individual-rank-learning-methods/
- RRF original paper PDF: https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf
- OpenSearch RRF score ranker docs: https://docs.opensearch.org/latest/search-plugins/search-pipelines/score-ranker-processor/

## Validation Checklist

Validation Result: PASS（2026-06-07T01:18:00+08:00）

- [x] Story 明确了用户角色、目标和收益。
- [x] Acceptance Criteria 覆盖 HybridRetriever、RRF formula、dedup、normalized score、post-merge threshold、deterministic tie-breaker、安全 provenance、ACL/tenant guard 和 fake-only tests。
- [x] Tasks 覆盖 DTO/config、RRFMerger、HybridRetriever、DTO/provenance、错误码、单测、导出、文档和验证命令。
- [x] Dev Notes 明确当前源码状态，尤其是 `RetrievalService` 单 retriever 端口、dense/sparse 已完成、`score_threshold` 0..1 与 raw RRF score 的兼容风险。
- [x] 明确不实现 rerank、`/retrieve` API、retrieval_logs、eval runner、context packing 或 RAG。
- [x] 明确 query 全文、chunk 正文、SQL raw text、tsquery/tsvector、vector、embedding、provider raw response、secret、本机绝对路径不得进入错误 details、日志或 provenance。

## Change Log

- 2026-06-07: Created comprehensive Story 3.4 developer context for RRF merge, dedup, post-merge threshold filtering and safe fusion provenance through the existing retrieval port.
- 2026-06-07: Implemented RRF merge, HybridRetriever orchestration, safe provenance, tests, exports and retrieval documentation.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- `.venv\Scripts\python.exe -m pytest tests/unit/retrieval/test_rrf.py` - PASS, 12 tests after review fixes.
- `.venv\Scripts\python.exe -m pytest tests/unit/retrieval tests/unit/vectorstores tests/unit/auth` - PASS, 121 tests after review fixes.
- `.venv\Scripts\python.exe -m pytest tests/integration/storage` - PASS, 25 tests.
- `.venv\Scripts\python.exe -m ruff check .` - PASS.
- `.venv\Scripts\python.exe -m mypy apps packages tests` - PASS, 149 source files.
- `.venv\Scripts\python.exe -m pytest` - PASS, 366 tests after review fixes.

### Completion Notes List

- Added `HybridMergeConfig`, `FusionSource`, `FusionTrace`, `RRFMerger` and `HybridRetriever` under `packages.retrieval.rrf`.
- RRF uses rank-based weighted contributions with default `rank_constant=60`, normalizes candidate score to 0..1, and records safe per-source provenance under `metadata["retrieval_provenance"]`.
- Hybrid branches share the same `RetrievalFilterSet`, clear branch `score_threshold`, and fail closed with stable hybrid error codes.
- Merge logic filters out-of-scope candidates by tenant, metadata and ACL before deduplication, while `RetrievalService` remains the final guard.
- Updated README and local development docs to reflect RRF completion while keeping rerank, `/retrieve`, retrieval logs, context packing, RAG and eval runners as non-goals.

### File List

- `packages/retrieval/rrf.py`
- `packages/retrieval/exceptions.py`
- `packages/retrieval/__init__.py`
- `tests/unit/retrieval/test_rrf.py`
- `README.md`
- `docs/operations/local-development.md`
- `_bmad-output/implementation-artifacts/3-4-rrf-merge-去重与阈值过滤.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

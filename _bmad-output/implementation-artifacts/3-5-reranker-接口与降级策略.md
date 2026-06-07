---
baseline_commit: NO_VCS
---

# Story 3.5: Reranker 接口与降级策略

Status: done

生成时间：2026-06-07T01:50:41+08:00

## Story

As a 平台工程师,
I want rerank 能通过统一接口替换实现并可降级,
so that MVP 可以用 fake reranker 测试，后续接 cross-encoder 或 LLM rerank。

## Acceptance Criteria

1. **Reranker 通过独立端口接入，不绑定具体模型或 SDK**
   - Given RRF merge 后的授权候选列表
   - When 调用 `Reranker.rerank`
   - Then 返回带 `rerank_score`、最终排序位置和安全 trace 的候选列表
   - And 不改变 `tenant_id`、`acl`、`document_id`、`version_id`、`chunk_id`、`source/source_uri`、`source_type`、`page_start/page_end`、`title_path` 等 citation metadata
   - And 端口不依赖 FastAPI、SQLAlchemy、Redis、MinIO、OpenSearch、VectorStore、EmbeddingProvider、LLM SDK 或 prompt builder

2. **FakeReranker 支持确定性测试且不调用外部模型**
   - Given 测试环境运行 rerank 单测
   - When 使用 `FakeReranker`
   - Then 默认不访问网络、不调用真实 LLM、Embedding API、cross-encoder、OpenSearch 或生产 PostgreSQL
   - And 可以通过配置或输入映射产生确定性分数
   - And 相同输入多次输出顺序完全 deterministic

3. **Rerank trace 记录前后分数、排序和 latency**
   - Given rerank 执行成功
   - When 返回候选
   - Then trace 至少包含 safe counts、input rank、pre-rerank score、rerank score、output rank、latency_ms、provider、model、request_id、trace_id、tenant_id、user_id
   - And trace 不包含 query 全文、chunk 正文、SQL、tsquery/tsvector、vector、embedding、provider raw response、secret、token、本机绝对路径或企业机密全文
   - And trace 当前可保存在内存对象或候选 metadata 的安全命名空间中；本 story 不落库 `retrieval_logs`

4. **降级策略显式可配置，默认安全**
   - Given reranker provider 失败、超时或返回非法分数
   - When 降级策略配置为 `fallback`
   - Then 系统使用 merge 排序继续返回结果并记录 `RERANK_DEGRADED`
   - And 降级不得引入未授权候选、不得扩大 top_k、不得绕过 `RetrievalService` 结果侧 guard
   - And 当策略配置为 `fail_closed` 时抛出稳定 `RetrievalError`

5. **Rerank score 与现有 score threshold 语义兼容**
   - Given `RetrievalCandidate.score` 当前是 0..1 分数，RRF 已将 fusion score 归一化
   - When rerank 产生分数
   - Then 输出候选的 `score` 必须保持 0..1 范围，供 `RetrievalRequest.score_threshold`、后续 context packing 和 eval 使用
   - And 原始 merge score、rerank score、score source 必须可追溯
   - And 不允许把不定界的 provider raw score 直接写入 `RetrievalCandidate.score`

6. **Rerank 编排集成在 retrieval 包内，不污染 API 或 RAG 层**
   - Given 已存在 `HybridRetriever` 和 `RRFMerger`
   - When 添加 rerank 编排
   - Then 新增 `RerankingRetriever` 或同等类实现 `CandidateRetriever`
   - And 构造函数注入 upstream `CandidateRetriever`、`Reranker`、config/clock，不在内部创建真实 provider/session
   - And `RetrievalService` 仍只依赖一个 `CandidateRetriever`
   - And API route、context packing、prompt builder、LLM generation、citation extraction 不在本 story 实现

7. **权限和安全边界不回退**
   - Given upstream retriever 已用 `RetrievalFilterSet` 执行 tenant、metadata、ACL 过滤
   - When rerank 执行
   - Then reranker 只接收授权候选，不接收未授权 chunk
   - And 如 fake/provider 返回额外候选、跨 tenant 候选或改写 ACL/citation metadata，编排层必须拒绝或恢复安全候选
   - And 无权限 chunk 不进入 rerank、context packing 或 prompt

8. **测试覆盖接口、fake、排序、降级和安全 trace**
   - Given 单元测试运行
   - When 使用 fake upstream retriever 和 FakeReranker
   - Then 覆盖 rerank score、排序位置、metadata/citation 保持、fallback、fail_closed、非法分数、timeout/domain error、safe details、deterministic tie-breaker、empty candidates、top_k 和 service guard
   - And 默认测试不访问真实外部模型、网络或生产数据库

## Tasks / Subtasks

- [x] 定义 rerank DTO、配置和端口（AC: 1, 3, 5）
  - [x] 新增 `packages/retrieval/rerank.py` 或等价模块，包含 `RerankRequest`、`RerankTrace`、`RerankedCandidate`/安全 metadata helper、`RerankConfig`。
  - [x] 在 `packages/retrieval/ports.py` 定义 `Reranker` 协议，方法建议为 `async def rerank(*, request: RetrievalRequest, filters: RetrievalFilterSet, candidates: Sequence[RetrievalCandidate]) -> RerankResult`。
  - [x] 配置至少包含 `enabled`、`failure_policy`（`fallback` 或 `fail_closed`）、`timeout_seconds`、`provider`、`model`、`max_candidates`。
  - [x] 校验 timeout/top_k/max_candidates/failure_policy/provider/model；非法配置转为稳定 validation error 或 retrieval domain error。

- [x] 实现 `FakeReranker`（AC: 2, 5, 8）
  - [x] 支持按 `chunk_id` 或 `(document_id, version_id, chunk_id)` 注入固定 rerank score。
  - [x] 未指定映射时使用输入顺序或 candidate 当前 score 生成确定性 0..1 分数。
  - [x] 不访问网络、不导入真实模型库、不读取文件系统模型路径。
  - [x] 支持测试用故障模式：raise domain error、raise unexpected error、返回非法分数、模拟超时。

- [x] 实现 `RerankingRetriever` 编排（AC: 4, 6, 7）
  - [x] `RerankingRetriever` 实现 `CandidateRetriever.retrieve(request, filters)`。
  - [x] 构造函数注入 `upstream_retriever: CandidateRetriever`、`reranker: Reranker`、config；不要在内部创建 `HybridRetriever`、provider、session 或真实 SDK。
  - [x] upstream 候选为空时直接返回空列表，并记录零候选 trace。
  - [x] rerank 前只传入 upstream 返回的授权候选；rerank 后按 output rank 排序并限制 `request.top_k`。
  - [x] fallback 策略下保留 upstream 排序和分数，在安全 trace 中记录 `RERANK_DEGRADED`、error_code、latency 和 safe counts。
  - [x] fail_closed 策略下抛出稳定 `RetrievalError`，错误 details 不含敏感内容。

- [x] 设计安全 score/provenance 写入方式（AC: 3, 5）
  - [x] 复用 `metadata["retrieval_provenance"]` 或新增 `metadata["rerank_provenance"]` 命名空间，不能覆盖原业务 metadata。
  - [x] 保留 RRF provenance 中的 `raw_rrf_score`、`normalized_fusion_score`、source ranks；新增 rerank provider/model、pre_score、rerank_score、input_rank、output_rank。
  - [x] 输出 `RetrievalCandidate.score` 使用归一化 rerank score；fallback 时保持 upstream score 并标记 score source。
  - [x] 对 query、chunk content、SQL、vector、embedding、provider raw response、secret、token、本地绝对路径做强制脱敏或丢弃。

- [x] 补充错误码与安全错误 details（AC: 4, 7, 8）
  - [x] 在 `packages/retrieval/exceptions.py` 增加稳定 code，例如 `RETRIEVAL_RERANK_FAILED`、`RETRIEVAL_RERANK_DEGRADED`、`RETRIEVAL_RERANK_INVALID_SCORE`。
  - [x] error details 只包含 request_id、trace_id、tenant_id、user_id、top_k、retrieval_method/rerank_stage、provider、model、safe counts、error_code。
  - [x] 不把 raw exception message 原样写入 details，尤其不能写 query 全文、chunk 正文、SQL、路径、secret 或 provider raw response。

- [x] 更新导出与文档（AC: 1, 6）
  - [x] 更新 `packages/retrieval/__init__.py` 导出 `Reranker`、`FakeReranker`、`RerankingRetriever`、`RerankConfig` 或稳定公共类名。
  - [x] 更新 `README.md#Retrieval Foundation`：说明 rerank interface/fake/degrade 已完成，但 `/retrieve` API、retrieval_logs、context packing、RAG、eval runner 仍未完成。
  - [x] 更新 `docs/operations/local-development.md#Retrieval Local Checks`：加入 rerank 本地测试命令、降级策略和安全 trace 边界。

- [x] 补充单元测试（AC: 1-8）
  - [x] 新增 `tests/unit/retrieval/test_rerank.py`。
  - [x] 覆盖 FakeReranker deterministic score/order、empty candidates、top_k 限制、score 0..1 校验。
  - [x] 覆盖 `RerankingRetriever` 作为 `CandidateRetriever` 被 `RetrievalService` 接收。
  - [x] 覆盖 fallback 使用 upstream 排序并记录 `RERANK_DEGRADED`。
  - [x] 覆盖 fail_closed 失败时抛稳定 `RetrievalError`。
  - [x] 覆盖 reranker 试图修改 tenant、ACL、document/version/chunk/source/page/title_path 时被拒绝或恢复安全候选。
  - [x] 覆盖安全 trace/details 不包含 query 全文、chunk 正文、SQL、vector、embedding、provider raw response、secret、token、本地绝对路径。

- [x] 验证（AC: 1-8）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/retrieval/test_rerank.py`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/retrieval tests/unit/vectorstores tests/unit/auth`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`
  - [x] 如成本可接受，运行 `.venv\Scripts\python.exe -m pytest`

### Review Findings

- [x] [Review][Patch] RerankingRetriever passes upstream candidates to reranker before tenant/metadata/ACL guard [packages/retrieval/rerank.py:288]
- [x] [Review][Patch] Unknown or extra provider candidates are position-mapped and can pollute scores/ranking [packages/retrieval/rerank.py:375]
- [x] [Review][Patch] Partial, empty, or duplicate provider outputs are accepted as successful rerank results [packages/retrieval/rerank.py:374]
- [x] [Review][Patch] Fallback and disabled paths can emit scores outside the 0..1 rerank contract [packages/retrieval/rerank.py:294]
- [x] [Review][Patch] fail_closed errors leave last_trace stale from a previous request [packages/retrieval/rerank.py:321]

## Dev Notes

### Current Repository State

- 当前目录不是 git repository，`git log`/`git status` 不可用；本 story 的上下文来自 sprint status、epics、architecture、PRD、project-context、3.4 story 文件、源码扫描和本地测试结构。
- `packages/retrieval` 当前已包含 `dto.py`、`ports.py`、`filters.py`、`service.py`、`dense.py`、`sparse.py`、`rrf.py`、`exceptions.py`、`__init__.py`。
- `RetrievalService` 只依赖一个 `CandidateRetriever`，负责 AuthContext 必填、filter 构建、非 retrieval backend error 包装、结果侧 tenant/metadata/ACL/score_threshold/top_k guard。3.5 不应把 rerank 逻辑写进 service。
- `CandidateRetriever` 协议固定接收 `RetrievalRequest` 与 `RetrievalFilterSet`。新 rerank 编排应实现该协议，以便包裹 `HybridRetriever`。
- `RetrievalCandidate` 是 frozen Pydantic DTO，包含 citation 必需字段、`score`、`retrieval_method`、`tenant_id`、`acl`、`metadata`；rerank 若改变 score/metadata，需要通过 `model_copy` 生成新 candidate。
- `RetrievalRequest.score_threshold` 要求 0..1；3.5 输出的 rerank score 必须归一化到 0..1，否则现有 service guard 和后续 context packing 会误判。
- `RRFMerger` 已使用 `metadata["retrieval_provenance"]` 存放 source methods、rank、score、contribution、raw RRF score、normalized score、fusion reason，并将 `retrieval_method` 改为 `"hybrid"`。
- `HybridRetriever` 已清空 branch `score_threshold`，先拿 dense/sparse 召回，再融合、去重、融合后阈值过滤。3.5 应在 RRF merge 之后接入。
- `packages/retrieval/rrf.py` 已有 `_safe_metadata`、敏感 key redaction 和本地绝对路径 redaction。3.5 可复用或提取相同安全策略，避免复制出不一致的脱敏规则。
- `tests/unit/retrieval/test_rrf.py` 已建立 deterministic fake retriever、branch failure、merge failure、安全 provenance、service guard 的测试风格。3.5 的测试应延续这种风格。

### Architecture Requirements

- 本 story 属于 Retrieval Domain/Application boundary；不涉及 API route、retrieval_logs 持久化、RAG、Agent 或 eval runner。
- 生产默认 flow 是 optional rewrite -> dense + sparse retrieval with ACL filters -> RRF merge + dedup -> rerank -> threshold -> context packing。
- 权限过滤必须在 dense/sparse 查询阶段完成，rerank 只能处理授权候选；结果侧仍由 `RetrievalService` guard 做最后防线。
- Domain 代码不得导入 FastAPI、SQLAlchemy、Redis、MinIO、httpx、外部 LLM SDK 或 prompt builder。
- 所有外部模型调用都必须通过 provider/adapter 抽象；本 story 只需要 fake 和端口，不接真实 cross-encoder 或 LLM adapter。
- route 层不得直接调用 reranker。后续 `/retrieve` API 只应注入 application service，不触碰 provider 或 rerank SDK。

### Current Files To Preserve And Extend

- `packages/retrieval/ports.py`
  - Current state: 只有 `CandidateRetriever` 协议。
  - Story change: 增加 `Reranker` 协议，或把协议放在 `rerank.py` 并从 ports re-export。
  - Preserve: `CandidateRetriever` 签名不变，避免破坏 dense/sparse/hybrid。

- `packages/retrieval/dto.py`
  - Current state: `RetrievalRequest`、`RetrievalFilterSet`、`RetrievalCandidate`、`RetrievalResult` 已稳定。
  - Story change: 可新增 rerank 专用 DTO 到 `rerank.py`，不建议把大量 rerank 字段塞进基础 candidate。
  - Preserve: candidate citation metadata、tenant、ACL 和业务 metadata 不被 reranker 覆盖。

- `packages/retrieval/rrf.py`
  - Current state: `HybridRetriever` + `RRFMerger` 已完成 hybrid merge、safe provenance 和 branch failure handling。
  - Story change: `RerankingRetriever` 应作为 wrapper 包裹 `HybridRetriever`，不要改写 RRF merge 行为。
  - Preserve: branch threshold clearing、RRF normalized score、safe provenance、out-of-scope filter。

- `packages/retrieval/service.py`
  - Current state: service 是最终安全 guard。
  - Story change: 测试证明 service 接受 `RerankingRetriever`。
  - Preserve: service 不知道 dense/sparse/RRF/rerank 细节。

- `packages/retrieval/exceptions.py`
  - Current state: 已有 dense、sparse、hybrid、backend 错误码。
  - Story change: 增加 rerank 错误码。
  - Preserve: 错误 details 保持安全摘要，不泄露 raw backend/provider 内容。

- `tests/unit/retrieval/test_rrf.py`、`test_service.py`
  - Current state: 已覆盖现有 retrieval service/hybrid 行为。
  - Story change: 新增 `test_rerank.py`，必要时少量扩展 service 测试。
  - Preserve: 默认测试不依赖网络、真实模型、真实 embedding API、OpenSearch 或生产 PostgreSQL。

### Previous Story Intelligence

- Story 3.1 建立 retrieval filter contract，并修复 private ACL 默认放行、无效 request 不转稳定 error、service 过度信任 retriever 输出、top_k 无上限、NaN threshold、多值 metadata filter 等问题。3.5 不得让 reranker 改写或绕过这些边界。
- Story 3.2 的 DenseRetriever 已证明 `RetrievalService` 能接受任何 `CandidateRetriever`，并建立 embedding provider/model/version 一致性和 candidate 脱敏风格。
- Story 3.3 的 SparseRetriever 已接入同一端口，并修复 PostgreSQL query term cap、backend timeout、fallback 过滤顺序、ACL SQL 语义、candidate validation error 和敏感 metadata redaction。
- Story 3.4 已完成 `HybridRetriever`、`RRFMerger`、RRF provenance、normalized fusion score、安全 trace 和文档更新。3.5 应在其后包裹 rerank，不要把 rerank 混入 RRF。
- 3.4 review 后修复过同分排序、同分同 chunk_id 的 deterministic tie-breaker、敏感 metadata alias redaction 和 safe counts。3.5 的 rerank trace 必须继承这些安全和确定性要求。

### Suggested Implementation Shape

示例只表达目标结构，开发时按现有本地风格落地：

```python
class Reranker(Protocol):
    async def rerank(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
        candidates: Sequence[RetrievalCandidate],
    ) -> RerankResult:
        ...
```

```python
class RerankingRetriever:
    def __init__(
        self,
        *,
        upstream_retriever: CandidateRetriever,
        reranker: Reranker,
        config: RerankConfig,
    ) -> None:
        self._upstream_retriever = upstream_retriever
        self._reranker = reranker
        self._config = config

    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> list[RetrievalCandidate]:
        candidates = await self._upstream_retriever.retrieve(request=request, filters=filters)
        if not candidates or not self._config.enabled:
            return candidates[: request.top_k]
        try:
            result = await self._reranker.rerank(
                request=request,
                filters=filters,
                candidates=candidates[: self._config.max_candidates],
            )
        except Exception as exc:
            if self._config.failure_policy == "fallback":
                return self._fallback(candidates=candidates, request=request, error=exc)
            raise self._safe_error(request=request, filters=filters, error=exc) from exc
        return self._validated_candidates(result.candidates, original=candidates, request=request)
```

推荐 score/provenance shape：

```text
metadata["rerank_provenance"] = {
  "provider": "fake",
  "model": "fake-reranker-v1",
  "status": "success" | "degraded",
  "input_rank": 1,
  "output_rank": 1,
  "pre_score": 0.86,
  "rerank_score": 0.92,
  "score_source": "rerank" | "fallback_upstream",
  "latency_ms": 1.2
}
```

### Implementation Boundaries

- Do not implement real cross-encoder adapter, real LLM rerank adapter, Cohere adapter, OpenAI adapter, Qwen adapter or local model loading.
- Do not add `sentence-transformers`, `transformers`, `torch`, `cohere` or other model dependencies in this story.
- Do not implement query rewrite.
- Do not implement `POST /retrieve`, API schema, route registration, retrieval log table or retrieval log persistence; Story 3.6 owns these.
- Do not implement eval fixtures or smoke runner; Story 3.7 owns these.
- Do not implement context packing, prompt building, citation extraction, LLM generation, SSE, chat, Agent, Tool Registry or Source Inspector.
- Do not call real external embedding APIs, LLM APIs, rerank APIs, OpenSearch, network services or production PostgreSQL in default tests.
- Do not log or return query full text, chunk content, SQL raw text, tsquery/tsvector, vector, embedding, provider raw responses, API keys, access tokens or local absolute paths.

### Latest Technical Information

- Sentence Transformers 文档说明 CrossEncoder 适合 pairwise reranking/semantic textual similarity，但不能为单个句子预先计算 embedding；这支持本项目把 rerank 作为 second-stage provider，而不是替代 dense/sparse index。Source: https://sbert.net/docs/package_reference/cross_encoder/cross_encoder.html
- Sentence Transformers 的 reranker 示例描述 reranker 通常是 CrossEncoder，输入 query 与 candidate/document pair，输出一个相关性分数；本 story 应只定义端口和 fake，真实 adapter 后续再实现。Source: https://sbert.net/examples/cross_encoder/training/rerankers/README.html
- Hugging Face cross-encoder model listing 显示 text-ranking reranker 模型持续更新，模型选择具有时效性；因此本 story 不固定模型名，只要求 provider/model 可配置并记录。Source: https://huggingface.co/cross-encoder/models

### UX / Product Notes

- 本 story 不实现 UI，但后续 Retrieval Diagnostics 需要展示 merge score、rerank score、degraded status 和 latency；3.5 的 trace 应保留安全摘要。
- 后续 Source Inspector 只应展示授权 citation 和必要 score/provenance 摘要，不展示完整文档正文、query 全文、SQL 或 provider raw output。
- 产品目标是企业可信 RAG 闭环，rerank 的价值是提高排序质量和可复盘性，不是引入新的权限边界。

### References

- `_bmad-output/planning-artifacts/epics.md#Story-3.5-Reranker-接口与降级策略`
- `_bmad-output/planning-artifacts/epics.md#Epic-3-授权-Hybrid-Retrieval-与检索复盘`
- `_bmad-output/planning-artifacts/architecture.md#Project-Structure-Boundaries`
- `_bmad-output/planning-artifacts/architecture.md#Integration-Points`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-11-Reranker-接口`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-22-RBAC-与-ACL-检索过滤`
- `docs/EMPLOYMENT_PRODUCT_MARKET_OPTIMIZATION.md`
- `docs/TECHNICAL_PREFERENCES.md`
- `project-context.md`
- `_bmad-output/implementation-artifacts/3-1-retrieval-请求模型与权限过滤构建.md`
- `_bmad-output/implementation-artifacts/3-2-dense-retrieval-召回.md`
- `_bmad-output/implementation-artifacts/3-3-bm25-sparse-retrieval-召回.md`
- `_bmad-output/implementation-artifacts/3-4-rrf-merge-去重与阈值过滤.md`
- `packages/retrieval/dto.py`
- `packages/retrieval/ports.py`
- `packages/retrieval/service.py`
- `packages/retrieval/rrf.py`
- `packages/retrieval/exceptions.py`
- `tests/unit/retrieval/test_rrf.py`
- `tests/unit/retrieval/test_service.py`
- `README.md#Retrieval-Foundation`
- `docs/operations/local-development.md#Retrieval-Local-Checks`

## Validation Checklist

Validation Result: PASS（2026-06-07T01:50:41+08:00）

- [x] Story 明确了用户角色、目标和收益。
- [x] Acceptance Criteria 覆盖 Reranker 端口、FakeReranker、score/order、trace、fallback/fail_closed、安全边界、service 集成和测试。
- [x] Tasks 覆盖 DTO/config、端口、fake、编排、score/provenance、错误码、测试、导出、文档和验证命令。
- [x] Dev Notes 明确当前源码状态，尤其是 `RetrievalService` 单 retriever 端口、RRF 已完成、`score_threshold` 0..1 与 rerank score 的兼容要求。
- [x] 明确不实现真实 cross-encoder/LLM adapter、`/retrieve` API、retrieval_logs、eval runner、context packing 或 RAG。
- [x] 明确 query 全文、chunk 正文、SQL raw text、tsquery/tsvector、vector、embedding、provider raw response、secret、本机绝对路径不得进入错误 details、日志或 provenance。

## Change Log

- 2026-06-07: Created comprehensive Story 3.5 developer context for reranker interface, fake reranker, safe score/provenance, and fallback/fail-closed strategy.
- 2026-06-07: Implemented reranker port, fake reranker, reranking retriever, safe provenance, fallback/fail_closed errors, tests, and retrieval docs.
- 2026-06-07: Fixed code review findings for pre-rerank guard, malformed provider output handling, score contract enforcement, disabled provenance, and fail_closed trace freshness.

## Dev Agent Record

### Agent Model Used
Codex (GPT-5)

### Debug Log References
- `.venv\Scripts\python.exe -m pytest tests/unit/retrieval/test_rerank.py` - 10 passed
- `.venv\Scripts\python.exe -m pytest tests/unit/retrieval tests/unit/vectorstores tests/unit/auth` - 131 passed
- `.venv\Scripts\python.exe -m ruff check .` - passed
- `.venv\Scripts\python.exe -m mypy apps packages tests` - no issues in 151 source files
- `.venv\Scripts\python.exe -m pytest` - 376 passed
- `.venv\Scripts\python.exe -m pytest tests/unit/retrieval/test_rerank.py` - 17 passed after review fixes
- `.venv\Scripts\python.exe -m pytest tests/unit/retrieval tests/unit/vectorstores tests/unit/auth` - 138 passed after review fixes
- `.venv\Scripts\python.exe -m ruff check .` - passed after review fixes
- `.venv\Scripts\python.exe -m mypy apps packages tests` - no issues in 151 source files after review fixes
- `.venv\Scripts\python.exe -m pytest` - 383 passed after review fixes

### Completion Notes List
- Added `Reranker` port and `RerankRequest`/`RerankConfig`/trace/result DTOs without changing the existing `CandidateRetriever` contract.
- Implemented deterministic `FakeReranker` with score maps and test-only failure modes, with no external model, network, file-system model, or production database access.
- Added `RerankingRetriever` wrapper that injects upstream retriever/reranker/config, supports timeout, fallback and fail_closed policies, and keeps `RetrievalService` dependent on one retriever.
- Added safe rerank provenance under `metadata["rerank_provenance"]`, preserving safe RRF provenance and restoring upstream tenant/ACL/citation metadata if a provider mutates it.
- Added stable rerank error codes and safe error details that exclude query text, chunk text, SQL, vectors, embeddings, provider raw output, secrets, tokens, and local absolute paths.
- Updated retrieval documentation and covered deterministic scoring, top_k, fallback, fail_closed, invalid score, provider mutation, safe trace/details, and service guard tests.
- Review fixes added pre-rerank tenant/metadata/ACL guard, sanitized provider inputs, strict provider output permutation validation, disabled provenance, invalid upstream score rejection, and fail_closed trace updates.

### File List
- README.md
- docs/operations/local-development.md
- packages/retrieval/__init__.py
- packages/retrieval/exceptions.py
- packages/retrieval/ports.py
- packages/retrieval/rerank.py
- tests/unit/retrieval/test_rerank.py
- _bmad-output/implementation-artifacts/sprint-status.yaml
- _bmad-output/implementation-artifacts/3-5-reranker-接口与降级策略.md

---
baseline_commit: NO_VCS
---

# Story 3.1: Retrieval 请求模型与权限过滤构建

Status: done

生成时间：2026-06-06T21:20:00+08:00

## Story

As a 后端开发者,
I want retrieval service 接收标准请求和 AuthContext 派生过滤条件,
so that 所有召回路径从查询阶段就执行 tenant、RBAC 和 ACL 限制。

## Acceptance Criteria

1. **RetrievalRequest 标准化查询输入**
   - Given 调用方提交 retrieval query
   - When application service 创建 `RetrievalRequest`
   - Then 请求包含 `query`、`top_k`、`metadata_filter`、`score_threshold`、`request_id`、`trace_id`
   - And `AuthContext` 不允许为空
   - And 空白 query、非正 `top_k`、越界 `score_threshold` 或非结构化 metadata filter 会在 DTO/service 边界被拒绝为稳定 domain error

2. **AuthContext 派生可复用 RetrievalFilterSet**
   - Given 用户属于某 tenant 且只有部分文档 ACL
   - When policy builder 生成 filters
   - Then filters 包含 tenant filter、ACL filter、metadata filter
   - And filter 可同时传给后续 dense 和 sparse retriever
   - And 必须复用 `packages.auth.context.AuthContext` 和 `packages.auth.policies.build_access_filter`，不得新建平行权限模型或把权限规则放入 prompt

3. **跨租户和无权限 chunk 在检索入口前被阻断**
   - Given 测试尝试跨租户检索
   - When retrieval service 执行
   - Then 不返回其他 tenant 的 chunk
   - And 无权限 chunk 不进入 rerank、context packing 或 prompt
   - And 当前 story 至少通过 fake retriever/vector fixture 证明 filters 在候选产生前生效

4. **Retrieval 层骨架与观测上下文就位**
   - Given 后续 Story 3.2 到 3.7 会实现 dense、sparse、RRF、rerank、API 和 eval
   - When 本 story 完成
   - Then `packages/retrieval` 提供稳定 DTO、filter builder、service skeleton、ports 和 typed exceptions
   - And service 返回结果或空结果时保留 `request_id`、`trace_id`、`tenant_id`、`user_id`、`top_k`、safe query summary、latency/error_code 占位字段
   - And 不新增 `/retrieve` route、不落库 `retrieval_logs`，这些留给 Story 3.6

## Tasks / Subtasks

- [x] 新建 retrieval package 骨架（AC: 1, 2, 4）
  - [x] 新增 `packages/retrieval/__init__.py`，保持 importable package。
  - [x] 新增 `packages/retrieval/dto.py`，定义 `RetrievalRequest`、`RetrievalFilterSet`、`RetrievalResult`、`RetrievalCandidate` 或等价 DTO。
  - [x] DTO 使用 Pydantic v2、type hints、`ConfigDict(frozen=True)`；mapping/list 字段要避免暴露可变共享默认值。
  - [x] `RetrievalRequest` 必须显式携带 `query`、`top_k`、`metadata_filter`、`score_threshold`、`request_id`、`trace_id`，并通过 service 参数或字段接收 `AuthContext`，不得允许无 auth 调用。
  - [x] `metadata_filter` 使用结构化 mapping 或 typed filter list，不使用 prompt text、SQL 片段或任意表达式字符串。

- [x] 实现权限过滤构建器（AC: 2, 3）
  - [x] 新增 `packages/retrieval/filters.py`，从 `AuthContext` 和请求级 metadata filter 生成 `RetrievalFilterSet`。
  - [x] 复用 `packages.auth.policies.build_access_filter(auth)`；保留 `tenant_id`、`user_id`、`roles`、`department`、`permissions`。
  - [x] 提供到 dense/vector 的转换函数，例如 `to_vector_acl_filter()` 和 `to_vector_metadata_filters()`，输出 `packages.vectorstores.dto.AclFilter` 与 `MetadataFilter`。
  - [x] 为 sparse retriever 保留同一 filter set 的结构化输出，避免 dense/sparse 各自解释权限。
  - [x] 请求 metadata filter 只能收窄范围；如果调用方传入 `tenant_id`，必须与 `auth.tenant_id` 一致，否则拒绝或覆盖为 auth tenant，并有单测锁定行为。
  - [x] 默认排除 soft-deleted/non-active 数据；`include_deleted` 不进入普通 retrieval 请求，管理诊断如需访问必须后续 story 单独授权。

- [x] 实现 retrieval service 最小骨架（AC: 1, 3, 4）
  - [x] 新增 `packages/retrieval/service.py`，提供 `RetrievalService` 或等价 application service。
  - [x] service 显式接收 `RetrievalRequest` 与 `AuthContext`/`AuthenticatedRequestContext`，构建 filters 后调用注入的 retriever port。
  - [x] 当前 story 可使用 `CandidateRetriever`/`Retriever` Protocol + fake 实现验证过滤链路；不要在 service 中直接调用 SQLAlchemy、pgvector SQL、EmbeddingProvider、LLM 或 reranker。
  - [x] service 对 expected errors 抛出 typed domain exceptions，错误 details 只包含 request/trace/tenant/user/top_k/error_code 等安全摘要，不包含 query 全文、chunk 正文、向量或本机绝对路径。
  - [x] 空授权范围或无候选返回应是合法 empty result，不应伪造结果或进入 RAG。

- [x] 定义 retrieval ports 与异常（AC: 2, 4）
  - [x] 新增 `packages/retrieval/ports.py`，定义后续 dense/sparse 可实现的 Protocol，例如 `Retriever`、`SparseRetriever` 或 `CandidateRetriever`。
  - [x] port 入参必须包含 `RetrievalRequest`/`RetrievalFilterSet`；不得只接收 query string。
  - [x] 新增 `packages/retrieval/exceptions.py`，定义稳定错误码，如 `RETRIEVAL_AUTH_REQUIRED`、`RETRIEVAL_INVALID_REQUEST`、`RETRIEVAL_FORBIDDEN_FILTER`、`RETRIEVAL_BACKEND_FAILED`。
  - [x] 不把 `VectorStoreError`、SQLAlchemy exception 或 provider exception 原样穿透到 API/service 返回。

- [x] 补充测试（AC: 1-4）
  - [x] 新增 `tests/unit/retrieval/test_dto.py`：覆盖 blank query、top_k 非正、score_threshold 边界、metadata_filter 必须为结构化数据、request_id/trace_id 必填。
  - [x] 新增 `tests/unit/retrieval/test_filters.py`：覆盖 AuthContext -> tenant/ACL/metadata filters，roles/department/permissions 透传，请求 metadata 与 auth tenant 合并，跨 tenant metadata 被拒绝。
  - [x] 新增 `tests/unit/retrieval/test_service.py`：用 fake retriever 证明 service 不允许空 AuthContext、会把同一 filter set 传入 retriever、跨租户候选被排除或不会产生、空结果不进入后续阶段。
  - [x] 可扩展 `tests/unit/auth/test_policies.py` 或复用现有断言，但不要把 retrieval 特有行为塞进 auth 单测。
  - [x] 所有测试不得调用真实外部 LLM、Embedding API、pgvector 服务或 OpenSearch。

- [x] 更新文档与开发说明（AC: 4）
  - [x] 更新 `README.md`，新增 Retrieval foundation 小节：说明 `packages/retrieval` 已建立请求模型和 AuthContext filter contract，但 dense/sparse/RRF/rerank/API 分属后续 stories。
  - [x] 如有必要新增 `docs/api/retrieval.md` 或 `docs/operations/local-development.md` 小节，记录权限过滤在 retrieval 阶段执行，不由前端、LLM 或 prompt 判断。
  - [x] 文档不得宣称 `/retrieve` API、BM25、RRF、rerank 或 retrieval log 已完成。

- [x] 验证（AC: 1-4）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/retrieval tests/unit/auth tests/unit/vectorstores`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`
  - [x] 如全量成本可接受，运行 `.venv\Scripts\python.exe -m pytest`

### Review Findings

- [x] [Review][Patch] Private ACL without allow list is treated as allowed [packages/vectorstores/adapters/fake.py:186]
- [x] [Review][Patch] Invalid retrieval requests are not converted to stable RetrievalError codes [packages/retrieval/dto.py:24]
- [x] [Review][Patch] RetrievalService trusts retriever output without result-side tenant/top_k invariant checks [packages/retrieval/service.py:41]
- [x] [Review][Patch] RetrievalRequest accepts unbounded top_k values [packages/retrieval/dto.py:32]
- [x] [Review][Patch] RetrievalRequest accepts NaN score_threshold values [packages/retrieval/dto.py:39]
- [x] [Review][Patch] RetrievalCandidate accepts invalid score and page metadata [packages/retrieval/dto.py:101]
- [x] [Review][Patch] Multi-value metadata filters are accepted but vectorstores interpret them as exact tuple/array equality [packages/retrieval/dto.py:159]

## Dev Notes

### Current Repository State

- 当前目录不是 git repository，`git log` 不可用；本 story 的 previous intelligence 来自已完成 story 文件、现有源码、epics、architecture、PRD、UX 和项目规则。
- `packages/retrieval` 尚不存在。本 story 是 Epic 3 的入口，应创建最小稳定包边界，不要把后续 dense/BM25/RRF/rerank/API 一次性塞进来。
- `packages/auth.context.AuthContext` 已存在，字段为 `user_id`、`tenant_id`、`roles`、`department`、`permissions`，并校验 user/tenant 非空。
- `packages/auth.policies.build_access_filter(auth)` 已存在，返回 `AccessFilter`，包含 tenant/user/roles/department/permissions，以及 `metadata_filter={"tenant_id": auth.tenant_id}` 和结构化 `acl_filter`。
- `packages/vectorstores.dto.VectorSearchRequest` 已存在，要求 `tenant_id`、`query_vector`、`embedding_dim`、`top_k`、`score_threshold`、`metadata_filters`、`acl_filter`、`include_deleted=False` 等字段。
- `packages.vectorstores.dto.AclFilter` 当前包含 `user_id`、`roles: list[str]`、`department`、`permissions: list[str]`；retrieval filter builder 要负责把 auth tuple 转成 vectorstore 需要的 list。
- `FakeVectorStore` 和 `PgVectorStore` 已在查询阶段执行 tenant、metadata、ACL、soft delete、top_k、threshold 过滤。3.1 不应复制这些 ACL 判断代码，而应生成它们需要的结构化 filters。
- `chunks`、`document_versions`、`vector_records` 已包含 tenant/document/version/chunk/source/page/title_path/acl/status/deleted_at 等字段；retrieval DTO 必须保留这些 metadata 给后续 citation 和 Source Inspector。

### Architecture Requirements

- 本 story 横跨 Application Service Layer、Domain DTO、Auth policy integration 和 Retrieval package boundary；不涉及 API route 和 storage migration。
- `packages/retrieval` owns dense/sparse/RRF/rerank/threshold/filter orchestration，但本 story 只实现请求模型、filter contract 和 service skeleton。
- AuthContext 必须从 API/application service 显式传入 retrieval，禁止使用全局变量、prompt 文案、前端状态或 LLM 判断权限。
- Retrieval filters 必须在候选产生前执行；不得先召回全量 chunk 再在 rerank、context packing、prompt 或 final answer 里过滤。
- Domain/DTO 层不得导入 FastAPI、SQLAlchemy、Redis、MinIO、httpx 或外部模型 SDK。
- Route 保持后续 story 处理；如果本 story 为了测试创建 service，不要注册 `/retrieve`，不要修改 `apps/api/main.py`。
- 所有 expected errors 使用 `packages.common.errors.DomainError` 子类或一致模式，便于后续 API error handler 映射统一 envelope。

### Current Files To Preserve And Extend

- `packages/auth/context.py`
  - Current state: `AuthContext` Pydantic model 已校验非空 user/tenant，并规范 roles/permissions/department。
  - Story change: 不修改或仅在必要时小范围补充测试；retrieval 应直接复用。
  - Preserve: 同一 AuthContext 同时服务 API、retrieval、RAG、Agent、audit。

- `packages/auth/policies.py`
  - Current state: `AccessFilter`、`FrozenDict`、`build_access_filter` 已提供结构化 tenant/ACL metadata。
  - Story change: 不要复制到 retrieval；在 `packages/retrieval/filters.py` 做适配层即可。
  - Preserve: `AccessFilter` 不是 prompt，不是 SQL 字符串。

- `packages/vectorstores/dto.py`
  - Current state: `MetadataFilter`、`AclFilter`、`VectorSearchRequest`、`VectorSearchResult` 已覆盖 dense/vector 查询需要的过滤字段和结果 metadata。
  - Story change: retrieval filter builder 应提供转换函数，便于 3.2 创建 `VectorSearchRequest`。
  - Preserve: `VectorSearchResult` 的 citation metadata 不丢失。

- `packages/vectorstores/adapters/fake.py` and `packages/vectorstores/adapters/pgvector.py`
  - Current state: search 默认排除 deleted/non-active；tenant、metadata、ACL 在查询阶段执行。
  - Story change: 一般不需要改 adapter。若测试需要 fake candidates，优先写 retrieval fake retriever，不要破坏 VectorStore contract。
  - Preserve: ACL 语义由 vectorstore adapter 当前实现负责执行，retrieval 只生成结构化 filter。

- `packages/data/dto.py` and `packages/data/storage/models.py`
  - Current state: `ChunkRecord`、`DocumentVersionStatusResult`、`ChunkModel`、`VectorRecordModel` 已包含 retrieval 需要的治理字段。
  - Story change: 不需要改 storage model 或 migration。
  - Preserve: 不把 chunk `content`、完整向量或 secret 放进 retrieval logs/result summary。

- `README.md`
  - Current state: 已描述 upload、AuthContext、EmbeddingProvider、VectorStore、`retrieval_ready` 和软删除。
  - Story change: 增加 retrieval foundation 状态，明确后续 stories 才实现 `/retrieve` 和 hybrid retrieval。
  - Preserve: 不夸大已完成功能。

### Previous Story Intelligence

- 2.1 到 2.9 已建立 ID-only async ingestion/embedding、chunk metadata、EmbeddingProvider、VectorStore、soft delete 和 `retrieval_ready` 状态闭环；3.1 应消费这些产物，不回到 demo 式直接搜全文。
- 2.6 明确 chunk metadata 是 citation/retrieval/ACL 的共享契约；3.1 的 retrieval candidate/result DTO 必须保留 `chunk_id`、`document_id`、`version_id`、`source`、`page_start`、`page_end`、`title_path`、`score`、`retrieval_method`、`tenant_id`、`acl`。
- 2.8 已证明 VectorStore contract 支持 metadata filter、tenant filter、ACL filter、soft delete、top_k、score threshold；3.1 只需要把 RetrievalRequest/AuthContext 正确转换成这一契约。
- 2.9 的 lifecycle story 强调 deleted versions/chunks/vectors 默认不可检索；3.1 不得引入 `include_deleted=True` 的普通路径。
- 既有 tests 偏好小而明确的 unit tests，fake provider/store 不访问外部服务；retrieval tests 应沿用这一模式。

### Suggested Contracts

Example request DTO:

```python
class RetrievalRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    query: str
    top_k: int = 10
    metadata_filter: dict[str, object] = Field(default_factory=dict)
    score_threshold: float | None = None
    request_id: str
    trace_id: str
```

Example filter set:

```python
class RetrievalFilterSet(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    user_id: str
    roles: tuple[str, ...] = ()
    department: str | None = None
    permissions: tuple[str, ...] = ()
    metadata_filter: dict[str, object] = Field(default_factory=dict)
    acl_filter: dict[str, object] = Field(default_factory=dict)
    include_deleted: bool = False
```

Example vector conversion:

```python
def to_vector_acl_filter(filters: RetrievalFilterSet) -> AclFilter:
    return AclFilter(
        user_id=filters.user_id,
        roles=list(filters.roles),
        department=filters.department,
        permissions=list(filters.permissions),
    )
```

Do not treat this snippet as mandatory exact code; follow existing local style if a cleaner implementation emerges.

### Implementation Boundaries

- Do not implement Dense Retrieval query embedding; that is Story 3.2.
- Do not implement BM25/PostgreSQL full text or OpenSearch sparse retriever; that is Story 3.3.
- Do not implement RRF merge, dedup, threshold filtering beyond request validation; that is Story 3.4.
- Do not implement Reranker, fake reranker, fallback policy or rerank latency; that is Story 3.5.
- Do not implement `POST /retrieve`, retrieval log table, Alembic migration or API route; that is Story 3.6.
- Do not implement eval fixtures/smoke runner; that is Story 3.7, though this story should make eval-friendly metadata available.
- Do not call LLM, EmbeddingProvider, external APIs, pgvector service, OpenSearch or any model SDK in tests.
- Do not log query full text by default; use safe query summary/hash/length where observability is needed.

### Latest Technical Information

- Pydantic v2 models remain the correct fit for request/DTO validation. Official docs state models parse and validate untrusted data and support `model_config = ConfigDict(frozen=True)` for faux immutability; nested dicts are still mutable unless wrapped or copied, so filters should avoid mutable shared state. Source: https://docs.pydantic.dev/latest/concepts/models/
- Pydantic validators support field/model validation. Use them for query/top_k/score_threshold/filter consistency instead of ad hoc checks scattered across service code. Source: https://docs.pydantic.dev/latest/concepts/validators/
- FastAPI request bodies are declared with Pydantic models and dependencies are intended for shared logic such as authentication/security. This supports the existing plan: route schema/dependency later, service/retrieval contract now. Source: https://fastapi.tiangolo.com/tutorial/body/ and https://fastapi.tiangolo.com/tutorial/dependencies/
- SQLAlchemy 2.0 docs state that after flush failure an explicit `Session.rollback()` is required before reusing the session. If later stories add retrieval storage/repositories, keep the existing rollback-on-storage-error pattern. Source: https://docs.sqlalchemy.org/en/20/orm/session_basics.html

### UX / Product Notes

- 本 story 不实现 UI，但 UX 明确 Open WebUI/前端不是治理边界；权限、tenant、ACL 和 source visibility 必须由后端 retrieval filters 决定。
- 后续 Knowledge Chat 的查询范围选择只能收窄范围，不能扩大权限；因此 `metadata_filter` 只能与 AuthContext 派生 filter 合并收窄。
- 后续 Retrieval Diagnostics 会展示 dense/sparse/RRF/rerank/context packing trace；本 story 的 result/trace DTO 应预留 request_id、trace_id、tenant/user、top_k、latency/error_code 等安全字段。
- 权限拒绝或跨租户场景不得泄露未授权资源是否存在。

### References

- `_bmad-output/planning-artifacts/epics.md#Story-3.1-Retrieval-请求模型与权限过滤构建`
- `_bmad-output/planning-artifacts/epics.md#Epic-3-授权-Hybrid-Retrieval-与检索复盘`
- `_bmad-output/planning-artifacts/architecture.md#Technology-Stack-Table`
- `_bmad-output/planning-artifacts/architecture.md#Project-Structure-Boundaries`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-8-Dense-Retrieval`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-22-RBAC-与-ACL-检索过滤`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md#Interaction-Rules`
- `_bmad-output/implementation-artifacts/2-9-文档版本-软删除与索引状态闭环.md`
- `project-context.md`
- `packages/auth/context.py`
- `packages/auth/policies.py`
- `packages/vectorstores/dto.py`
- `packages/vectorstores/ports.py`
- `packages/vectorstores/adapters/fake.py`
- `packages/vectorstores/adapters/pgvector.py`
- `packages/data/dto.py`
- `packages/data/storage/models.py`
- `tests/unit/auth/test_policies.py`
- `tests/unit/vectorstores/test_contract.py`
- `tests/integration/storage/test_vector_repositories.py`

## Validation Checklist

Validation Result: PASS（2026-06-06T21:20:00+08:00）

- [x] Story 明确了用户角色、目标和收益。
- [x] Acceptance Criteria 覆盖 RetrievalRequest、AuthContext 必填、tenant/ACL/metadata filter、跨租户隔离和 retrieval 层骨架。
- [x] Tasks 覆盖 DTO、filter builder、service skeleton、ports、exceptions、unit tests、docs 和验证命令。
- [x] Dev Notes 明确当前源码状态、可复用 AuthContext/AccessFilter/VectorStore contract、需要新增的 retrieval package 和实现边界。
- [x] 明确不实现 dense、BM25、RRF、rerank、`/retrieve` API、retrieval logs、eval runner 或 RAG。
- [x] 明确 tenant_id/user_id/ACL/document/version/chunk/source metadata 不能丢失，且 query full text、chunk content、完整向量、secret、本机绝对路径不得进入日志或摘要。

## Change Log

- 2026-06-06: Created comprehensive Story 3.1 developer context for retrieval request modeling, AuthContext-derived filters, retrieval package skeleton, tests, and documentation.
- 2026-06-06: Implemented retrieval foundation DTOs, AuthContext-derived filter builder, service skeleton, ports, exceptions, unit tests, and README documentation.
- 2026-06-06: Applied code review fixes for ACL default-deny semantics, stable retrieval validation errors, DTO boundary checks, and service result invariants.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- `.venv\Scripts\python.exe -m pytest tests/unit/retrieval` -> 23 passed
- `.venv\Scripts\python.exe -m pytest tests/unit/retrieval tests/unit/auth tests/unit/vectorstores` -> 61 passed
- `.venv\Scripts\python.exe -m ruff check .` -> passed
- `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed
- `.venv\Scripts\python.exe -m pytest` -> 306 passed
- `.venv\Scripts\python.exe -m pytest tests/unit/retrieval tests/unit/vectorstores/test_contract.py` -> 40 passed
- `.venv\Scripts\python.exe -m pytest tests/unit/retrieval tests/unit/auth tests/unit/vectorstores` -> 74 passed
- `.venv\Scripts\python.exe -m ruff check .` -> passed
- `.venv\Scripts\python.exe -m mypy apps packages tests` -> passed
- `.venv\Scripts\python.exe -m pytest` -> 319 passed

### Completion Notes List

- 新增 `packages/retrieval` 稳定包边界，包含 Pydantic v2 retrieval request/filter/candidate/result DTO、typed exceptions、candidate retriever port 和注入式 `RetrievalService`。
- `RetrievalService` 强制要求 `AuthContext`，通过 `build_access_filter(auth)` 派生 tenant/ACL facts；跨 tenant metadata filter 在 retriever 调用前被拒绝，错误 details 只包含 request/trace/tenant/user/top_k/error_code 等安全摘要。
- `RetrievalFilterSet` 可转换为 vectorstore `AclFilter`/`MetadataFilter`，并提供 sparse retriever 结构化 payload；普通 retrieval 路径固定 `include_deleted=False`。
- README 增加 Retrieval Foundation 小节，明确当前未实现 `/retrieve`、dense、BM25、RRF、rerank、retrieval logs 或 RAG generation。
- Code review 修复后，`private` ACL 无显式 allow-list 默认拒绝；无效 `RetrievalRequest` 转为稳定 `RETRIEVAL_INVALID_REQUEST`；service 返回前会执行 tenant、metadata、ACL、score threshold 与 top_k 守卫。

### File List

- `README.md`
- `packages/retrieval/__init__.py`
- `packages/retrieval/dto.py`
- `packages/retrieval/exceptions.py`
- `packages/retrieval/filters.py`
- `packages/retrieval/ports.py`
- `packages/retrieval/service.py`
- `packages/vectorstores/acl.py`
- `packages/vectorstores/adapters/fake.py`
- `packages/vectorstores/adapters/pgvector.py`
- `tests/unit/retrieval/test_dto.py`
- `tests/unit/retrieval/test_filters.py`
- `tests/unit/retrieval/test_service.py`
- `tests/unit/vectorstores/test_contract.py`
- `_bmad-output/implementation-artifacts/3-1-retrieval-请求模型与权限过滤构建.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

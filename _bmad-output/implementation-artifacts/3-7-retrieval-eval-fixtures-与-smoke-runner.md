---
baseline_commit: NO_VCS
---

# Story 3.7: Retrieval Eval Fixtures 与 Smoke Runner

Status: done

生成时间：2026-06-07T12:47:52+08:00

## Story

As a 平台工程师,
I want retrieval 阶段就有可执行 eval fixtures 和 smoke runner,
so that hybrid retrieval 质量不会等到 RAG 回答完成后才被验证。

## Acceptance Criteria

1. **初始化可执行 retrieval eval dataset，而不是占位样例**
   - Given `tests/eval` 当前不存在
   - When 本 story 完成
   - Then 新增结构化 retrieval eval dataset，至少包含 20 条 synthetic retrieval eval cases
   - And 每条 case schema 至少支持 `case_id`、`category`、`query`、`tenant_id`、`user_id`、`roles`、`department`、`permissions`、`metadata_filter`、`expected_documents`、`expected_chunks`、`answerable`、`attack_type`、`top_k`
   - And 初始集合至少覆盖制度、产品手册、FAQ、技术文档四类样例
   - And 初始集合至少包含两个 ACL 隔离、两个 no-answer 和两个 prompt injection 回归场景
   - And dataset 只保存 synthetic 文本和安全 metadata，不包含企业真实资料、API key、access token、本机绝对路径、chunk 正文全文或 prompt

2. **eval case schema 使用类型化 DTO 校验**
   - Given runner 加载 eval fixtures
   - When fixture 缺失必填字段、类型错误、重复 `case_id`、空 query、空 tenant/user、无效 `top_k`、非结构化 `metadata_filter` 或 expected id 为空
   - Then 加载失败并返回稳定 eval 错误或明确 CLI 错误信息
   - And 错误信息不得输出 query 全文、secret、token、本机绝对路径或完整 fixture 内容
   - And DTO 使用 Pydantic v2 或 dataclass + 明确校验，不使用 ad hoc dict 贯穿 runner

3. **Smoke runner 使用 fake/local retrieval，不调用真实外部 provider**
   - Given 开发者执行 retrieval eval smoke runner
   - When 使用默认配置运行
   - Then runner 加载并执行全部 20 条初始 eval cases
   - And 默认只使用 fake embedding provider、fake vector store、fake/local retriever 或本地 synthetic fixtures
   - And 不调用真实外部 LLM、embedding API、rerank API、OpenSearch、网络服务或生产 PostgreSQL
   - And 不要求 Docker Compose、MinIO、Redis 或真实 pgvector 才能跑默认 smoke

4. **Smoke runner 输出可复盘指标和机器可读报告**
   - Given smoke runner 完成
   - When 输出 summary/report
   - Then 至少包含 `case_count`、`passed_count`、`failed_count`、`retrieval_hit_rate`、`acl_isolation_passed`、`no_answer_passed`、`prompt_injection_passed`、`average_latency_ms`、`top_k` 摘要
   - And 每个 case report 包含 `case_id`、`request_id`、`trace_id`、`tenant_id`、`user_id`、`top_k`、`latency_ms`、`passed`、`failure_stage`、`matched_documents`、`matched_chunks`
   - And report 写入 `tests/eval/reports/` 或可配置输出目录，默认格式为 JSON
   - And report 不保存 query 全文、chunk content、SQL、tsquery/tsvector、vector、embedding、provider raw response、secret、token 或本机绝对路径

5. **命中、no-answer、ACL 和 prompt injection 判定清晰**
   - Given case `answerable=true` 且配置了 expected documents/chunks
   - When runner 收到 retrieval candidates
   - Then 至少一个 expected chunk 或 expected document 命中授权候选时视为 retrieval hit
   - And 未授权 chunk 不得计入命中
   - Given case `answerable=false`
   - When runner 执行
   - Then 结果为空或无 expected 命中时视为 no-answer retrieval 正确，不能为了提高 hit rate 强行通过
   - Given case `attack_type` 为 `prompt_injection` 或 `acl_isolation`
   - When runner 运行
   - Then 恶意指令文本只作为 synthetic fixture 内容或 query 场景，不改变系统规则、权限或工具调用
   - And 失败 report 必须将失败阶段标为 `dense`、`sparse`、`merge`、`rerank`、`threshold`、`permission`、`no_answer`、`dataset` 或 `runner`

6. **复用现有 retrieval 契约，不重新实现生产检索链路**
   - Given 已存在 `RetrievalRequest`、`RetrievalCandidate`、`RetrievalResult`、`RetrievalService`、`RetrieveApplicationService`、`DenseRetriever`、`PostgresSparseRetriever`、`HybridRetriever`、`RRFMerger`、`RerankingRetriever`、`FakeReranker`
   - When 实现 eval runner
   - Then runner 通过现有 DTO/service/port 组织检索，不复制 dense、sparse、RRF、rerank、ACL filter 或 response redaction 逻辑
   - And runner 可提供独立 fake/local candidate retriever 来让 synthetic dataset 稳定可执行
   - And API route、provider adapter、storage model、SQLAlchemy model 不进入 eval domain DTO

7. **测试覆盖 dataset、runner、指标、权限和报告脱敏**
   - Given 单元测试运行
   - When 执行本 story 测试集
   - Then 覆盖 20 条 fixtures 可加载、重复/非法 case 被拒绝、answerable hit、document-level hit、chunk-level hit、no-answer、ACL 隔离、prompt injection、失败阶段分类、latency/report summary、JSON report 写入
   - And 测试断言默认路径不调用真实外部 LLM、embedding API、rerank API、OpenSearch、网络服务或生产 PostgreSQL
   - And 测试断言 query 全文、chunk content、secret/token、本机绝对路径不会写入 report/log/error

8. **文档和本地命令可直接用于回归**
   - Given story 完成
   - When 开发者阅读 README 或 local development docs
   - Then 能看到 retrieval eval smoke 的目的、默认 fake/local 依赖、运行命令、输出 report 位置、指标含义和当前边界
   - And `pyproject.toml` 的 pytest 默认 `testpaths` 如需覆盖 `tests/eval` 必须更新，或文档明确使用显式路径运行 eval 测试
   - And 本 story 不把 eval smoke gate 接入 CI 强制门禁；CI smoke gate 由 Epic 5 后续 story 扩展

## Tasks / Subtasks

- [x] 设计 retrieval eval 数据模型与目录结构（AC: 1, 2, 8）
  - [x] 新增 `tests/eval/retrieval/`，建议包含 `__init__.py`、`dto.py`、`loader.py`、`runner.py`、`reporting.py`。
  - [x] 新增 `tests/eval/datasets/retrieval_smoke.json`，包含 20 条 synthetic cases。
  - [x] DTO 使用 Pydantic v2 `BaseModel`，字段使用明确类型、默认值和 validator；不要让 runner 直接消费原始 dict。
  - [x] `metadata_filter` 规则应与 `RetrievalRequest` 兼容：只允许结构化 key 和 scalar value，不能接受 `$where`、空 key、嵌套对象、数组或跨租户扩权字段。
  - [x] `roles`、`permissions`、`expected_documents`、`expected_chunks` 统一为 tuple/list of str，加载后去空白并拒绝空 ID。

- [x] 编写 20 条 synthetic retrieval eval fixtures（AC: 1, 3, 5）
  - [x] 制度样例至少 5 条，例如 HR 年假、试用期、报销、信息安全、权限申请。
  - [x] 产品手册样例至少 5 条，例如产品型号、错误码、配置项、版本限制、兼容性。
  - [x] FAQ 样例至少 5 条，例如账号重置、上传失败、检索无答案、状态解释、citation 打开。
  - [x] 技术文档样例至少 5 条，例如 API envelope、Docker Compose、embedding job、retrieval log、RRF 默认。
  - [x] ACL 隔离样例至少 2 条：同 query 在不同 tenant/role 下不得命中未授权 document/chunk。
  - [x] no-answer 样例至少 2 条：没有授权资料或 expected 为空时，runner 应把无命中视为正确。
  - [x] prompt injection 样例至少 2 条：query 或 synthetic source 中可包含“忽略系统提示”等攻击文本，但只用于验证不会扩大权限或影响 runner 策略。
  - [x] 所有 document/chunk/source ID 使用 synthetic 值，如 `doc-hr-policy-v1`、`chunk-hr-leave-001`。

- [x] 实现 dataset loader 和 fixture validation（AC: 1, 2, 7）
  - [x] `load_retrieval_eval_cases(path: Path) -> tuple[RetrievalEvalCase, ...]`。
  - [x] 校验文件存在、JSON 顶层结构、重复 `case_id`、空集合、case 数量不少于 20。
  - [x] 错误类型建议定义为 `RetrievalEvalDatasetError`，details 只包含 `case_id`、字段名、错误码、安全计数。
  - [x] 单测覆盖非法 JSON、重复 ID、缺字段、无效 metadata、case 数不足、attack_type 非法值。

- [x] 实现 deterministic fake/local retrieval backend（AC: 3, 5, 6）
  - [x] 可在 `tests/eval/retrieval/runner.py` 内部提供 `FixtureCandidateRetriever`，实现现有 `CandidateRetriever` port。
  - [x] 使用 synthetic fixture seeds 构造 `RetrievalCandidate`，通过 `RetrievalService` 结果侧 guard 验证 tenant、metadata、ACL、score_threshold、top_k。
  - [x] 候选应覆盖 dense/sparse/hybrid/rerank provenance 的安全 metadata，以便 report 能显示阶段摘要。
  - [x] 不连接 `PgVectorStore`、`PostgresSparseRetriever`、真实 embedding provider 或真实 DB。
  - [x] 如果后续要跑真实 `/retrieve` API，可作为显式非默认模式；本 story 默认不实现真实服务压测。

- [x] 实现 smoke runner（AC: 3, 4, 5, 6）
  - [x] `run_retrieval_eval(cases, retriever/service, *, report_path, now/perf_counter) -> RetrievalEvalReport`。
  - [x] 每个 case 构造 `AuthContext` 和 `RetrievalRequest`，request_id/trace_id 必须稳定生成，例如 `eval-{case_id}` 和 `trace-{case_id}`。
  - [x] 复用 `RetrievalService.retrieve()`，不要在 runner 中自行过滤 tenant/ACL/metadata。
  - [x] 判定函数建议独立：`evaluate_case(case, result) -> RetrievalEvalCaseResult`。
  - [x] `failure_stage` 只能来自允许集合：`dense`、`sparse`、`merge`、`rerank`、`threshold`、`permission`、`no_answer`、`dataset`、`runner`。
  - [x] JSON report 用 `model_dump(mode="json")` 或等价安全序列化，确保 datetime/path 等对象不会泄露。

- [x] 提供 CLI / 模块执行入口（AC: 3, 4, 8）
  - [x] 建议新增 `tests/eval/retrieval/run_smoke.py`，支持 `python -m tests.eval.retrieval.run_smoke`。
  - [x] CLI 参数至少包含 `--dataset`、`--report-dir`、`--top-k` 可选覆盖。
  - [x] 默认 dataset 指向 `tests/eval/datasets/retrieval_smoke.json`，默认 report dir 指向 `tests/eval/reports`。
  - [x] 成功时退出码为 0；dataset validation error、runner error 或指标失败时返回非零退出码。
  - [x] CLI stdout 只输出安全 summary，不输出 query 全文、chunk content、完整 fixture 或本机绝对路径。

- [x] 写单元测试和 smoke 测试（AC: 1-7）
  - [x] 新增 `tests/unit/eval/test_retrieval_eval_loader.py`。
  - [x] 新增 `tests/unit/eval/test_retrieval_eval_runner.py`。
  - [x] 新增 `tests/unit/eval/test_retrieval_eval_reporting.py`。
  - [x] 可新增 `tests/eval/test_retrieval_smoke_dataset.py`，专门验证真实 20 条 fixture 可加载和 runner 可跑通。
  - [x] 测试中使用 `tmp_path` 写 report，断言 report JSON 包含 summary/case results 且不包含敏感字符串。
  - [x] 测试明确 monkeypatch 或 fake 断言没有调用网络、真实 provider、OpenSearch 或生产 PostgreSQL。

- [x] 更新项目文档（AC: 8）
  - [x] 更新 `README.md#Retrieval Foundation`，说明 retrieval eval fixtures/smoke runner 已加入，以及当前仍不是 RAG citation/no-answer 全链路 eval。
  - [x] 更新 `docs/operations/local-development.md#Retrieval Local Checks`，加入 retrieval eval smoke 命令、report 路径和指标解释。
  - [x] 如新增配置项或 CLI 默认值，避免写入 `.env.example` 除非确实需要环境变量。
  - [x] 不要把 Story 5 的 CI gate、citation eval、RAG answer eval 提前塞进本 story。

- [x] 验证（AC: 1-8）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/eval tests/eval`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/retrieval tests/unit/vectorstores tests/unit/auth`
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/api/test_retrieve_routes.py tests/unit/retrieval/test_retrieve_application.py`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`
  - [x] `python -m tests.eval.retrieval.run_smoke --dataset tests/eval/datasets/retrieval_smoke.json --report-dir tests/eval/reports`

### Review Findings

- [x] [Review][Patch] Eval DTO coerces non-string list items and allows unsafe fixture IDs to reach errors/reports [tests/eval/retrieval/dto.py:64]
- [x] [Review][Patch] `run_retrieval_eval()` can produce a passing summary for an empty case sequence [tests/eval/retrieval/runner.py:109]
- [x] [Review][Patch] CLI `--top-k` override is not prevalidated and falls through to a generic runner error [tests/eval/retrieval/run_smoke.py:24]
- [x] [Review][Patch] Corpus `relevant_case_ids` are not validated against loaded eval cases [tests/eval/retrieval/loader.py:75]
- [x] [Review][Patch] Tests do not assert the default smoke path cannot call real external providers or services [tests/unit/eval/test_retrieval_eval_runner.py:22]
- [x] [Review][Patch] Failure-stage tests miss ACL isolation, prompt-injection, and ordinary no-answer failure mappings [tests/unit/eval/test_retrieval_eval_runner.py:92]

## Dev Notes

### Current Repository State

- 当前目录不是 git repository，`git log` 不可用；本 story 的上下文来自 sprint status、epics、architecture、PRD、project-context、Story 3.6 Dev Agent Record 和源码扫描。
- `tests/eval` 目录当前不存在；本 story 应新建 eval 目录结构。
- `pyproject.toml` 当前 pytest 默认 `testpaths = ["tests/unit", "tests/integration"]`，不会自动收集 `tests/eval`。如果新增 `tests/eval/test_*.py`，要么更新 pytest 配置，要么在文档和验证命令中显式运行 `tests/eval`。
- 现有依赖中没有 PyYAML；dataset 默认建议用 JSON，避免新增 YAML 解析依赖。
- 当前 `README.md#Retrieval Foundation` 和 `docs/operations/local-development.md#Retrieval Local Checks` 明确 eval runners 尚未完成；本 story 完成后必须更新。

### Existing Retrieval Components To Reuse

- `packages/retrieval/dto.py`
  - `RetrievalRequest` 已校验 query/request_id/trace_id、`top_k`、`score_threshold`、structured scalar `metadata_filter`。
  - `RetrievalCandidate` 已包含 `document_id`、`version_id`、`chunk_id`、source/page/title_path/score/retrieval_method/tenant/acl/metadata。
  - `RetrievalResult` 已包含 request/trace/tenant/user/top_k/query_summary/candidates/latency/error_code。

- `packages/retrieval/service.py`
  - `RetrievalService` 只依赖一个 `CandidateRetriever`。
  - service 负责 AuthContext 必填、`build_retrieval_filter_set`、unexpected backend error 包装、结果侧 tenant/metadata/ACL/score_threshold/top_k guard。
  - Eval runner 应复用它来验证权限和过滤，不要自己复制过滤逻辑。

- `packages/retrieval/application.py`
  - `RetrieveApplicationService` 已封装 `/retrieve` 成功/失败日志和 audit。
  - API response metadata 和 log metadata 已有敏感字段清洗逻辑。
  - Eval runner 可直接基于 `RetrievalService` 做本地 smoke；如果需要复用 `RetrieveApplicationService`，必须注入 in-memory/fake `RetrievalLogPort` 和 `AuditPort`，不要接真实 DB。

- `packages/retrieval/rrf.py` 与 `packages/retrieval/rerank.py`
  - `HybridRetriever`、`RRFMerger`、`RerankingRetriever` 和 `FakeReranker` 已存在。
  - Story 3.7 不应重写 RRF 或 rerank；只需要让 fixture/runner 能评估这些阶段的输出或 synthetic trace。

- `packages/vectorstores/adapters/fake.py` 与 `packages/embeddings/adapters/fake.py`
  - fake vector store 和 fake embedding provider 是 deterministic local/test 实现，无网络调用。
  - 如果 runner 选择端到端 fake dense path，可以使用它们；但为了稳定覆盖 sparse/ACL/no-answer，可优先使用 fixture-backed `CandidateRetriever`。

### Previous Story Intelligence

- Story 3.1 修复过 private ACL 默认放行、无效 request 不转稳定 error、service 过度信任 retriever 输出、top_k 无上限、NaN threshold、多值 metadata filter 等问题。3.7 的 dataset loader 和 runner 不得绕过 `RetrievalRequest` 校验和 `RetrievalService` guard。
- Story 3.2 建立 DenseRetriever 的 provider/vector store 抽象和 safe details。3.7 默认测试必须继续使用 fake provider/store，不真实调用 embedding API 或 pgvector。
- Story 3.3 建立 SparseRetriever，并修复 PostgreSQL query term cap、backend timeout、fallback 过滤顺序、ACL SQL 语义、candidate validation error 和敏感 metadata redaction。3.7 report 不得保存 SQL、tsquery、raw content 或 query_terms 原文。
- Story 3.4 完成 `HybridRetriever`、`RRFMerger`、RRF provenance、normalized fusion score 和安全 trace。3.7 应将失败阶段和 hit rate 设计成能定位 dense/sparse/merge/threshold，而不是只给总分。
- Story 3.5 完成 `RerankingRetriever`、`FakeReranker`、safe rerank provenance、fallback/fail_closed、pre-rerank guard 和 trace freshness。3.7 report 应能显示 rerank degraded/failed，但不能扩大 top_k 或引入未授权候选。
- Story 3.6 完成 `/retrieve` API、`RetrieveApplicationService`、`retrieval_logs`、safe replay metadata 和 audit。3.7 是 eval 层，不要改 `/retrieve` route 或 retrieval log schema，除非测试暴露必需 bug。

### Architecture Requirements

- 本 story 属于 Eval/Test Infrastructure + Retrieval Domain 边界，不属于 API feature。
- 生产代码路径不应依赖 `tests/eval`；eval 可以导入生产 DTO/service/ports，但生产包不能反向导入测试目录。
- route 必须保持薄层；本 story 不需要新增 API route。
- 所有 eval case 必须携带 tenant/user/permissions，以验证 `tenant_id`、RBAC、ACL 从数据模型和检索阶段贯穿。
- 权限过滤必须在 retrieval 阶段验证，不能在 report 后处理阶段才排除未授权候选。
- eval report 是可复盘证据，不是企业全文存储；只保存摘要和 synthetic IDs。
- Default tests must not call real LLM, embedding API, rerank API, OpenSearch, network services, production PostgreSQL, Redis, MinIO, or Docker.

### Suggested File Structure

```text
tests/
  eval/
    __init__.py
    datasets/
      retrieval_smoke.json
    reports/
      .gitkeep
    retrieval/
      __init__.py
      dto.py
      loader.py
      runner.py
      reporting.py
      run_smoke.py
    test_retrieval_smoke_dataset.py
  unit/
    eval/
      __init__.py
      test_retrieval_eval_loader.py
      test_retrieval_eval_runner.py
      test_retrieval_eval_reporting.py
```

### Suggested DTO Shape

```python
class RetrievalEvalCase(BaseModel):
    case_id: str
    category: Literal["policy", "product_manual", "faq", "technical_doc"]
    query: str
    tenant_id: str
    user_id: str
    roles: tuple[str, ...] = ()
    department: str | None = None
    permissions: tuple[str, ...] = ()
    metadata_filter: dict[str, object] = Field(default_factory=dict)
    expected_documents: tuple[str, ...] = ()
    expected_chunks: tuple[str, ...] = ()
    answerable: bool
    attack_type: Literal["none", "acl_isolation", "prompt_injection"] = "none"
    top_k: int = 5
```

```python
class RetrievalEvalCaseResult(BaseModel):
    case_id: str
    request_id: str
    trace_id: str
    tenant_id: str
    user_id: str
    top_k: int
    latency_ms: float
    passed: bool
    failure_stage: str | None
    matched_documents: tuple[str, ...] = ()
    matched_chunks: tuple[str, ...] = ()
```

### Dataset Design Guidance

- 推荐让 20 条 cases 共用一个 synthetic corpus 定义，避免每条 case 重复长文本。Corpus 可包含 document/chunk metadata 和少量 synthetic snippet，但 runner/report 不应输出 snippet。
- 如果实现 corpus 文件，建议 `tests/eval/datasets/retrieval_corpus.json` 和 `retrieval_smoke.json` 分离；case 只引用 expected IDs。
- ACL 隔离样例必须同时存在“正确授权 case”和“未授权相同 query case”，这样能验证未授权 chunk 不计入命中。
- Prompt injection 样例的攻击文本可出现在 query 中，但 runner 不应执行任何工具、prompt 或 LLM；它只验证 retrieval 不因攻击文本扩大权限或伪造命中。
- No-answer case 可以设置 `expected_documents=[]`、`expected_chunks=[]`、`answerable=false`，并期望 runner 返回空命中。

### Report Semantics

- `retrieval_hit_rate = answerable passed hit cases / answerable cases`，ACL/no-answer/prompt-injection 可单独计入分类 pass rate，避免 no-answer 抬高 hit rate。
- `acl_isolation_passed` 应表示所有 ACL 隔离 case 均未命中未授权 chunk；任何 unauthorized expected chunk 出现在候选中都应 fail。
- `prompt_injection_passed` 应表示攻击文本没有改变权限、没有导致未授权命中、没有让 runner 执行 prompt/tool/LLM。
- `failure_stage` 是排查标签，不必证明真实内部 stage 一定失败；当使用 fixture retriever 时，可根据候选 provenance 或评估结果标为 `permission`、`no_answer`、`threshold`、`runner` 等。

### Implementation Boundaries

- Do not implement context packing.
- Do not implement prompt building.
- Do not implement citation extraction.
- Do not implement `/query`, `/chat`, SSE streaming, LLMProvider, RAG generation, chat memory, Open WebUI adapter, Source Inspector, or `/sources/resolve`.
- Do not implement Story 5 CI smoke gate, citation eval runner, RAG answer eval, no-answer generation eval, or prompt-builder security eval beyond retrieval-stage cases.
- Do not change `/retrieve` API behavior unless a bug blocks eval and is covered by tests.
- Do not add real cross-encoder, Cohere, OpenAI, Qwen, DeepSeek, vLLM, Ollama, OpenSearch, FAISS, Milvus, or network dependencies.
- Do not add PyYAML just for fixtures; JSON is sufficient.
- Do not log or report query full text, chunk content, SQL raw text, tsquery/tsvector, vector, embedding, provider raw response, API keys, access tokens, secrets, passwords, local absolute paths, or real enterprise confidential content.

### Latest Technical Information

- Pytest official docs document `python -m pytest [...]` as a supported invocation path and note it adds the current directory to `sys.path`; this matches the repo's existing Windows validation commands. Source: https://docs.pytest.org/en/latest/how-to/usage.html
- Pydantic v2 `BaseModel` remains the appropriate local validation tool for structured case/report DTOs; this repo already pins `pydantic>=2.13.4,<3`. Source: https://docs.pydantic.dev/latest/concepts/models/
- Python 3.11 standard `argparse` is sufficient for a small smoke runner CLI; avoid adding CLI dependencies for this story. Source: https://docs.python.org/3.11/library/argparse.html

### UX / Product Notes

- 本 story 不实现 UI，但后续 Eval Reports 页面会依赖本 story 的 report shape。
- Report 中长 `case_id`、`request_id`、`trace_id`、`document_id`、`version_id`、`chunk_id` 必须保持完整机器可读；前端展示截断不是本 story 职责。
- 无答案是成功状态的一种，不应在 eval summary 中被显示为普通 runner error。

### References

- `_bmad-output/planning-artifacts/epics.md#Story-3.7-Retrieval-Eval-Fixtures-与-Smoke-Runner`
- `_bmad-output/planning-artifacts/epics.md#Epic-3-授权-Hybrid-Retrieval-与检索复盘`
- `_bmad-output/planning-artifacts/architecture.md#CI-CD`
- `_bmad-output/planning-artifacts/architecture.md#Requirements-to-Structure-Mapping`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-29-RAG-Eval-Dataset`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-12-Retrieval-Log`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-30-Structured-Logging`
- `project-context.md`
- `_bmad-output/implementation-artifacts/3-1-retrieval-请求模型与权限过滤构建.md`
- `_bmad-output/implementation-artifacts/3-2-dense-retrieval-召回.md`
- `_bmad-output/implementation-artifacts/3-3-bm25-sparse-retrieval-召回.md`
- `_bmad-output/implementation-artifacts/3-4-rrf-merge-去重与阈值过滤.md`
- `_bmad-output/implementation-artifacts/3-5-reranker-接口与降级策略.md`
- `_bmad-output/implementation-artifacts/3-6-retrieve-api-与检索复盘日志.md`
- `pyproject.toml`
- `packages/retrieval/dto.py`
- `packages/retrieval/service.py`
- `packages/retrieval/application.py`
- `packages/retrieval/ports.py`
- `packages/retrieval/dense.py`
- `packages/retrieval/sparse.py`
- `packages/retrieval/rrf.py`
- `packages/retrieval/rerank.py`
- `packages/vectorstores/adapters/fake.py`
- `packages/embeddings/adapters/fake.py`
- `tests/unit/retrieval/test_service.py`
- `tests/unit/retrieval/test_retrieve_application.py`
- `tests/integration/api/test_retrieve_routes.py`
- `README.md#Retrieval-Foundation`
- `docs/operations/local-development.md#Retrieval-Local-Checks`

## Validation Checklist

Validation Result: PASS（2026-06-07T12:47:52+08:00）

- [x] Story 明确了用户角色、目标和收益。
- [x] Acceptance Criteria 覆盖 20 条 fixtures、schema、runner、report、权限/no-answer/prompt injection、复用现有 retrieval 契约、测试和文档。
- [x] Tasks 覆盖 eval 目录结构、dataset、loader、fake/local retriever、runner、CLI、tests、docs 和验证命令。
- [x] Dev Notes 明确当前源码状态，尤其是 `tests/eval` 不存在、pytest 默认 testpaths 不包含 eval、现有 retrieval DTO/service/application/ports 可复用。
- [x] 明确不实现 context packing、RAG generation、`/query`、`/chat`、SSE、citation eval、CI smoke gate、真实 provider/DB/OpenSearch。
- [x] 明确 query 全文、chunk 正文、SQL raw text、tsquery/tsvector、vector、embedding、provider raw response、secret、token、本机绝对路径不得进入 report、logs、errors 或 fixture。

## Change Log

- 2026-06-07: Implemented retrieval eval fixtures, smoke runner, typed validation, safe JSON reporting, CLI, tests, and docs; story moved to review.
- 2026-06-07: Created comprehensive Story 3.7 developer context for retrieval eval fixtures, smoke runner, safe reporting, and local fake execution boundaries.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- 2026-06-07T12:53:16+08:00: Marked story/sprint in-progress; baseline_commit recorded as `NO_VCS` because project root is not a git repository.
- 2026-06-07T13:01:39+08:00: Eval, retrieval/vector/auth, retrieve API/application tests passed; CLI smoke produced 20/20 summary.
- 2026-06-07T13:03:48+08:00: Final eval, retrieval/vector/auth, retrieve API/application, ruff, mypy, and CLI smoke validations passed.

### Completion Notes List

- Implemented typed retrieval eval DTOs, dataset/corpus loading, validation errors with safe details, deterministic fixture-backed candidate retrieval, case evaluation, summary/report generation, and module CLI.
- Added 20 synthetic retrieval smoke cases across policy, product manual, FAQ, and technical documentation, including ACL isolation, no-answer, and prompt-injection regression scenarios.
- Runner constructs `AuthContext` and `RetrievalRequest` per case and delegates filtering to `RetrievalService`, avoiding direct external provider, vector store, SQL, network, Docker, Redis, MinIO, or production PostgreSQL usage.
- Added unit/eval smoke coverage for fixture loading, invalid datasets, duplicate IDs, metadata validation, hit/no-answer/ACL/prompt-injection evaluation, report writing, safe redaction, and real fixture execution.
- Updated README and local development docs with purpose, commands, default fake/local dependencies, report location, metrics, and current non-goals.

### File List

- `README.md`
- `docs/operations/local-development.md`
- `tests/__init__.py`
- `tests/eval/__init__.py`
- `tests/eval/datasets/retrieval_smoke.json`
- `tests/eval/reports/.gitkeep`
- `tests/eval/retrieval/__init__.py`
- `tests/eval/retrieval/dto.py`
- `tests/eval/retrieval/loader.py`
- `tests/eval/retrieval/reporting.py`
- `tests/eval/retrieval/runner.py`
- `tests/eval/retrieval/run_smoke.py`
- `tests/eval/test_retrieval_smoke_dataset.py`
- `tests/unit/eval/__init__.py`
- `tests/unit/eval/test_retrieval_eval_loader.py`
- `tests/unit/eval/test_retrieval_eval_reporting.py`
- `tests/unit/eval/test_retrieval_eval_runner.py`
- `_bmad-output/implementation-artifacts/3-7-retrieval-eval-fixtures-与-smoke-runner.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`

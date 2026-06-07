---
baseline_commit: b0684e5
---

# Story 5.1: 可执行 Eval Dataset 结构与初始用例

Status: done

生成时间：2026-06-07T21:11:29+08:00

## Story

As a 平台工程师,
I want 用结构化数据维护 RAG eval cases,
so that retrieval、citation、无答案和权限隔离能被稳定回归。

## Acceptance Criteria

1. **建立 RAG eval dataset，不重复 Story 3.7 的 retrieval-only dataset**
   - Given `tests/eval/retrieval` 已经包含 retrieval smoke dataset、loader、runner 和 reports
   - When 实现 Story 5.1
   - Then 新增 RAG eval dataset 结构，建议位于 `tests/eval/rag/` 和 `tests/eval/datasets/rag_smoke.json`
   - And 可复用 retrieval eval 的安全 ID、metadata、reporting 经验，但不得让生产包导入 `tests/eval`
   - And 不重写 dense、sparse、RRF、rerank、context packing、prompt builder、generation 或 citation extractor 生产逻辑

2. **定义类型化 RAG eval case DTO 和 loader**
   - Given loader 读取 `rag_smoke.json`
   - When dataset 缺字段、类型错误、重复 `case_id`、空 query、空 tenant/user、非法 metadata filter、非法 expected citation、非法 attack_type 或非法 answer expectation
   - Then 加载失败并返回稳定 eval dataset error
   - And 错误 details 只包含文件名、case_id、字段名、错误计数和安全枚举，不输出 query 全文、answer 全文、chunk content、prompt、secret、token 或本机绝对路径
   - And DTO 使用 Pydantic v2 `BaseModel` 或同等类型化校验，不让 runner 消费未校验 dict

3. **初始 dataset 至少包含 20 条可执行 synthetic RAG cases**
   - Given 初始化 Phase 2 RAG eval fixtures
   - When 检查 `rag_smoke.json`
   - Then 至少包含 20 条 synthetic cases，而不是 schema-only 或 placeholder
   - And 每条 case 支持 `case_id`、`category`、`query`、`tenant_id`、`user_id`、`roles`、`department`、`permissions`、`metadata_filter`、`expected_documents`、`expected_chunks`、`expected_citations`、`answerable`、`expected_no_answer`、`attack_type`、`top_k`
   - And `expected_citations` 至少能表达 `document_id`、`version_id`、`chunk_id`、可选 page range、required/optional 标记
   - And case schema 必须能让后续 Story 5.2 计算 citation coverage、no-answer correctness、ACL isolation result 和 prompt-injection regression

4. **业务场景覆盖满足 PRD FR-29**
   - Given 初始 RAG eval 集合被加载
   - When 统计 case 分类
   - Then 覆盖制度、产品手册、FAQ、技术文档四类样例，每类至少 4 条
   - And 至少包含 2 条 ACL 隔离、2 条 no-answer、2 条 prompt injection 回归场景
   - And 至少包含 3 条 citation-sensitive case，要求答案 citation 精确绑定到指定 chunk 或 document
   - And 至少包含 2 条 unsupported/forged citation 防护场景，验证伪造来源不能被视为通过

5. **可执行 synthetic corpus 支持 RAG 全链路 fake/local runner**
   - Given 后续 runner 使用 fake providers 或 local fixtures
   - When 读取 corpus
   - Then corpus 记录包含安全 synthetic chunk text、token_count、document_id、version_id、chunk_id、tenant_id、source/source_uri/source_type、page_start/page_end、title_path、score、retrieval_method、acl、metadata、relevant_case_ids
   - And chunk text 必须是 synthetic 且足以驱动 context packing、PromptBuilder、FakeLLMProvider 和 CitationExtractor 的本地回归
   - And corpus 不包含真实企业资料、API key、access token、Bearer token、本机绝对路径、真实 object key、SQL、向量、embedding 或 provider raw response

6. **默认执行路径不调用真实外部服务**
   - Given 开发者运行默认 RAG eval dataset smoke 或 tests
   - When 使用 `rag_smoke.json`
   - Then 默认只使用 fake/local retrieval、fake LLM provider、local context/citation DTO 和 synthetic fixtures
   - And 不调用 OpenAI、Qwen、DeepSeek、vLLM、Ollama、embedding API、rerank API、OpenSearch、PostgreSQL、pgvector、Redis、MinIO、Docker、网络服务或生产数据库
   - And 如未来支持真实 `/query` API eval，必须是显式 opt-in 模式，不属于本 story 默认路径

7. **report shape 可被 Story 5.2 扩展**
   - Given dataset smoke 或 loader validation 生成 report
   - When 输出 summary/report DTO
   - Then summary 至少包含 `case_count`、`answerable_count`、`no_answer_count`、`acl_case_count`、`prompt_injection_case_count`、`citation_expected_count`、`dataset_version`
   - And per-case 安全摘要包含 `case_id`、`category`、`tenant_id`、`user_id`、`top_k`、expected document/chunk/citation ID 摘要、answerable/no-answer/prompt-injection/ACL flags
   - And report 不保存 query 全文、answer 全文、chunk text、prompt、SQL、vectors、embeddings、provider raw response、secret、token 或本机绝对路径
   - And failure_stage 枚举预留 `retrieval`、`rerank`、`context_packing`、`prompt_build`、`generation`、`citation`、`permission`、`no_answer`、`dataset`、`runner`

8. **文档说明 retrieval eval 与 RAG eval 的边界**
   - Given Story 5.1 完成
   - When 开发者阅读 README 或 local development docs
   - Then 能看到 retrieval eval 已由 `tests/eval/retrieval` 覆盖，RAG eval dataset 负责 citation、no-answer、ACL、prompt injection 和后续 answer quality 回归
   - And 文档列出默认运行命令、dataset 位置、report 位置、synthetic-only 规则和非目标
   - And 明确 CI smoke gate、完整 citation eval runner、RAG answer scoring 阈值由 Story 5.2/5.3 实现，不在本 story 中提前塞入 CI

9. **测试覆盖 dataset、loader、report 和安全边界**
   - Given 单元测试运行
   - When 执行本 story 测试集
   - Then 覆盖 20 条 RAG fixtures 可加载、重复/非法 case 被拒绝、expected citations 校验、no-answer expectation、ACL/prompt injection flags、safe report serialization 和 synthetic corpus validation
   - And 测试断言默认路径不会调用真实 provider、网络、DB、Redis、MinIO 或 Docker
   - And 测试断言 query、answer、chunk text、prompt、secret/token、本机绝对路径不会写入 report/log/error

## Tasks / Subtasks

- [x] 设计 RAG eval DTO 和目录结构（AC: 1, 2, 3, 7）
  - [x] 新增 `tests/eval/rag/__init__.py`、`dto.py`、`loader.py`、`reporting.py`，可选 `runner.py` 仅做 dataset smoke，不实现完整质量 runner。
  - [x] 新增 `tests/eval/datasets/rag_smoke.json`，包含 `dataset_version`、`cases`、`corpus` 顶层字段。
  - [x] DTO 使用 Pydantic v2，字段命名遵循现有 eval DTO 的 snake_case 风格。
  - [x] 复用或复制必要的 safe fixture ID 校验规则；不要从 `packages/*` 反向依赖 `tests/eval`。

- [x] 实现 RAG eval case schema（AC: 2, 3, 4）
  - [x] 定义 `RagEvalCase`，覆盖 query/auth/metadata/expected IDs/answerability/attack type/top_k。
  - [x] 定义 `ExpectedCitation`，字段至少包含 `document_id`、`version_id`、`chunk_id`、`page_start`、`page_end`、`required`。
  - [x] 定义 `ExpectedAnswerPolicy` 或等价字段，表达 `answerable`、`expected_no_answer`、`must_include_terms`、`must_not_include_terms` 的安全短语级期望。
  - [x] 校验 `answerable=false` 时不得要求必需 citation；`answerable=true` 时必须有 expected document/chunk 或 expected citation。
  - [x] 校验 metadata filter 只能使用结构化 scalar，不允许 `$where`、空 key、嵌套对象、数组或跨租户扩权字段。

- [x] 实现 synthetic corpus schema（AC: 5）
  - [x] 定义 `RagEvalCorpusRecord`，包含 chunk identity、tenant、source metadata、page range、title_path、score、retrieval_method、acl、metadata、token_count、content、relevant_case_ids。
  - [x] 校验 content 必须非空且长度受限，例如 2,000 字符以内；loader/report/error 不输出 content。
  - [x] 校验 `relevant_case_ids` 全部存在，拒绝 orphan corpus record 或未知 case id。
  - [x] corpus source URI 使用 `synthetic://rag-eval/...`，禁止本机绝对路径和真实 object storage key。

- [x] 编写 20 条 RAG synthetic fixtures（AC: 3, 4, 5）
  - [x] 制度类至少 5 条：年假、试用期、报销、信息安全、薪酬/权限隔离。
  - [x] 产品手册类至少 5 条：型号、错误码、配置项、版本限制、兼容性/prompt injection。
  - [x] FAQ 类至少 5 条：账号重置、上传失败、状态解释、citation 打开、无答案。
  - [x] 技术文档类至少 5 条：API envelope、Docker Compose、embedding job、retrieval log、RRF/内部 ACL。
  - [x] 至少 2 条 ACL 隔离 case 必须具备未授权同 query/corpus 候选，验证不会被计为通过。
  - [x] 至少 2 条 no-answer case 使用 `expected_no_answer=true` 且 expected citations 为空。
  - [x] 至少 2 条 prompt injection case 的 query 或 synthetic content 包含攻击文本，但只作为数据，不改变 runner 策略。

- [x] 实现 loader 和 dataset validation（AC: 2, 3, 5, 9）
  - [x] `load_rag_eval_dataset(path: Path) -> RagEvalDataset`。
  - [x] 校验文件存在、JSON 顶层结构、dataset_version、case 数不少于 20、case_id 唯一、corpus identity 唯一、corpus relevant_case_ids 有效。
  - [x] 定义 `RagEvalDatasetError(code, details)`，details 必须安全。
  - [x] 单测覆盖 invalid JSON、缺字段、重复 ID、非法 metadata、非法 citation、unknown relevant_case_id、case 数不足。

- [x] 实现安全 dataset/report summary（AC: 7, 9）
  - [x] 定义 `RagEvalDatasetSummary` 和 `RagEvalCaseSummary`，只输出 IDs、flags、counts 和安全枚举。
  - [x] 提供 `summarize_rag_eval_dataset(dataset)` 和可选 `write_json_report()`。
  - [x] report 默认写到 `tests/eval/reports/` 或测试临时目录。
  - [x] 单测断言 report 不包含 query、answer expectation 全文、chunk content、prompt、secret/token、本机绝对路径。

- [x] 提供可执行 smoke 入口但不实现完整 Story 5.2 runner（AC: 6, 7, 8）
  - [x] 可新增 `tests/eval/rag/run_dataset_smoke.py`，支持 `python -m tests.eval.rag.run_dataset_smoke --dataset tests/eval/datasets/rag_smoke.json --report-dir tests/eval/reports`。
  - [x] CLI 只做 dataset load + validation + safe summary/report，成功退出码 0，dataset validation error 非零。
  - [x] stdout 只输出安全计数，不输出 query、content、answer terms 或路径全文。
  - [x] 文档明确完整 retrieval/citation/no-answer runner 在 Story 5.2。

- [x] 更新测试（AC: 1-9）
  - [x] 新增 `tests/unit/eval/test_rag_eval_loader.py`。
  - [x] 新增 `tests/unit/eval/test_rag_eval_reporting.py`。
  - [x] 新增 `tests/eval/test_rag_smoke_dataset.py`。
  - [x] 扩展或新增边界测试，确保 `packages/*` 不导入 `tests.eval`。
  - [x] 使用 monkeypatch/fake 断言默认 smoke 不访问外部 provider、网络或 DB。

- [x] 更新文档（AC: 8）
  - [x] 更新 `README.md#Evaluation and Tests`，加入 RAG eval dataset 位置、命令、指标边界和安全规则。
  - [x] 更新 `docs/operations/local-development.md`，新增 RAG eval dataset local checks。
  - [x] 明确 Story 5.1 非目标：完整 quality runner、CI gate、真实 provider/API eval、LLM judge、faithfulness scoring。

- [x] 验证（AC: 1-9）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/eval tests/eval`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/rag tests/unit/retrieval tests/unit/auth`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/test_architecture_boundaries.py`
  - [x] `.venv\Scripts\python.exe -m tests.eval.rag.run_dataset_smoke --dataset tests/eval/datasets/rag_smoke.json --report-dir tests/eval/reports`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`

### Review Findings

- [x] [Review][Patch] DTO validation accepts wrong JSON types through Pydantic coercion [tests/eval/rag/dto.py:56]
- [x] [Review][Patch] DTO models silently ignore unknown fields that can hide unsafe fixture payloads [tests/eval/rag/dto.py:50]
- [x] [Review][Patch] Loader does not cross-check expected documents, chunks, citations, tenant, and page references against corpus records [tests/eval/rag/loader.py:103]
- [x] [Review][Patch] Secret-like fixture IDs can validate and be echoed in loader error details [tests/eval/rag/dto.py:363]
- [x] [Review][Patch] Corpus ACL payloads and ACL-isolation fixture semantics are not validated [tests/eval/rag/dto.py:214]
- [x] [Review][Patch] Synthetic corpus fields do not fully reject non-synthetic source values or embedded local path patterns [tests/eval/rag/dto.py:206]
- [x] [Review][Patch] No-answer fixture is linked to answer-guiding relevant corpus content [tests/eval/datasets/rag_smoke.json:156]
- [x] [Review][Patch] Safe-report leakage tests check only the first query and first corpus chunk [tests/eval/test_rag_smoke_dataset.py:34]
- [x] [Review][Patch] Report filenames can collide for two smoke runs in the same second [tests/eval/rag/reporting.py:84]
- [x] [Review][Patch] Production-boundary test misses `from tests import eval` and dynamic `tests.eval` imports [tests/unit/test_architecture_boundaries.py:407]

## Dev Notes

### Current Repository State

- Git baseline: `b0684e5 Remove BMAD local tooling`; worktree was clean before this story file was created.
- `tests/eval/retrieval` 已存在 retrieval-only eval 基础设施，包括 `dto.py`、`loader.py`、`runner.py`、`reporting.py`、`run_smoke.py` 和 `tests/eval/datasets/retrieval_smoke.json`。
- `pyproject.toml` 当前 pytest 默认 `testpaths = ["tests/unit", "tests/integration"]`，不会自动收集 `tests/eval`。Story 5.1 应继续在文档和验证命令中显式运行 `tests/eval`，除非有明确理由调整默认 testpaths。
- 当前依赖已有 Pydantic v2、pytest、argparse 标准库能力；不需要新增 PyYAML、click、typer、pandas、deepeval、ragas 或 LLM judge 依赖。
- README 当前将 `tests/eval` 描述为 retrieval smoke evaluation fixtures and reports，并明确 citation eval、RAG answer eval、CI smoke gates 尚未完成。
- `docs/operations/local-development.md` 已有 Retrieval eval smoke 说明，并在 RAG 章节多处注明 citation eval / RAG answer eval 尚未完成。

### Existing Patterns To Reuse

- `tests/eval/retrieval/dto.py` 的 `SAFE_FIXTURE_ID_PATTERN`、scalar metadata 校验、Pydantic v2 frozen DTO、safe report DTO 是可复用设计参考。
- `tests/eval/retrieval/loader.py` 的 `RetrievalEvalDatasetError(code, details)` 模式适合 RAG eval loader；错误 details 必须安全。
- `tests/eval/retrieval/reporting.py` 已用 `model_dump(mode="json")` 写 JSON report，可沿用同类 serialization 规则。
- `packages/rag/dto.py` 已定义 `QueryCommand`、`QueryResponse`、`Citation`、`PackedContext`、`PackedCitationSource`、`CitationExtractionResult`，RAG eval schema 应与这些字段契约对齐。
- `packages/rag/citation_extractor.py` 当前只信任 packed context 的 authorized citation source；eval case 的 expected citation 应以 document/version/chunk identity 为核心，不依赖 answer 文本中的伪造 source token。
- `packages/rag/query.py` 的 response metadata 已包含 retrieval、context、prompt risk、generation、citation、latency 和 error_code summary；后续 Story 5.2 可使用这些安全摘要。

### Previous Story Intelligence

- Story 3.7 已经完成 20 条 retrieval synthetic cases 和 fixture-backed retrieval runner。5.1 不应删除、迁移或破坏 `tests/eval/retrieval`。
- Story 3.7 review 修复过 eval DTO 非字符串 coercion、空 case set 误通过、top_k 预校验、corpus relevant_case_ids 校验、默认路径不调用真实外部服务断言、失败阶段覆盖不足。5.1 必须从一开始覆盖这些问题。
- Story 4.4 建立了 citation extraction 与 `/query`，关键边界是“不伪造 citation、无法确认时 no-answer”。
- Story 4.5 建立了 SSE token/citation/error/final 事件；5.1 不需要测试 streaming 协议，但后续 report shape 要能承载 final answer/citation 质量。
- Story 4.6 建立 chat session memory 与安全上下文；5.1 dataset 可以包含 session_id 预留字段，但不应实现多轮 eval runner。
- Story 4.7 建立 Open WebUI adapter 和 `/sources/resolve`，强调 citation/source visibility 由后端决定，前端不得补造 citation。Eval dataset 应覆盖这种后端 citation 可信边界。

### Architecture Requirements

- 本 story 属于 Eval/Test Infrastructure，不属于 API feature，不应新增业务 endpoint。
- 生产代码可以被 eval 测试导入；生产包不得导入 `tests.eval`。
- `tenant_id`、`user_id`、roles、department、permissions、ACL、document_id、version_id、chunk_id 必须贯穿每条 case 和 corpus record。
- 默认 fixtures 必须 synthetic-only。即使 chunk content 用于 fake local RAG，也不能来自真实企业文档。
- Eval output 是质量证据，不是数据湖；report/log/error 中只允许 IDs、counts、safe flags、safe enum、latency 和阶段名。
- 不要把权限逻辑放进 prompt 或 eval expectation 文案；权限仍由 AuthContext/ACL 数据表达。

### Suggested File Structure

```text
tests/
  eval/
    datasets/
      rag_smoke.json
    rag/
      __init__.py
      dto.py
      loader.py
      reporting.py
      run_dataset_smoke.py
    reports/
      .gitkeep
    test_rag_smoke_dataset.py
  unit/
    eval/
      test_rag_eval_loader.py
      test_rag_eval_reporting.py
```

### Suggested DTO Shape

```python
class ExpectedCitation(BaseModel):
    document_id: str
    version_id: str
    chunk_id: str
    page_start: int | None = None
    page_end: int | None = None
    required: bool = True
```

```python
class RagEvalCase(BaseModel):
    case_id: str
    category: Literal["policy", "product_manual", "faq", "technical_doc"]
    query: str
    tenant_id: str
    user_id: str
    roles: tuple[str, ...] = ()
    department: str | None = None
    permissions: tuple[str, ...] = ()
    metadata_filter: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    expected_documents: tuple[str, ...] = ()
    expected_chunks: tuple[str, ...] = ()
    expected_citations: tuple[ExpectedCitation, ...] = ()
    answerable: bool
    expected_no_answer: bool = False
    attack_type: Literal["none", "acl_isolation", "prompt_injection"] = "none"
    top_k: int = 5
```

```python
class RagEvalCorpusRecord(BaseModel):
    document_id: str
    version_id: str
    chunk_id: str
    tenant_id: str
    content: str
    token_count: int
    source: str = "synthetic"
    source_uri: str
    source_type: str
    page_start: int | None = None
    page_end: int | None = None
    title_path: tuple[str, ...]
    score: float
    retrieval_method: str = "hybrid"
    acl: dict[str, object]
    metadata: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    relevant_case_ids: tuple[str, ...]
```

### Report Semantics

- `citation_expected_count` 统计 required expected citations，不用 answer text 推断。
- `expected_no_answer=true` 的 case 应在后续 runner 中要求 no-answer response 且 citations 为空。
- `attack_type=acl_isolation` 的 case 应验证未授权 chunk 不进入 retrieval/context/citation。
- `attack_type=prompt_injection` 的 case 应验证攻击文本不改变权限、不改变 backend prompt policy、不导致伪造 citation。
- `unsupported/forged citation` 场景可以通过 expected fields 表达：answerable=true 但 generated fixture 或 local fake response 引用未授权 source 时，后续 runner 应标为 citation failure。

### Implementation Boundaries

- Do not implement the full Story 5.2 eval runner.
- Do not add CI smoke gate.
- Do not add LLM-as-judge, faithfulness scoring, RAGAS, deepeval, pandas, or external telemetry dependencies.
- Do not call `/query` or `/chat` over HTTP by default.
- Do not require Docker Compose, PostgreSQL, Redis, MinIO, pgvector, OpenSearch, Ollama, vLLM, OpenAI, Qwen, DeepSeek, or network access.
- Do not modify retrieval/RAG production services unless tests expose a necessary bug.
- Do not store full query, answer, prompt, chunk content, SQL, vectors, embeddings, provider payloads, secrets, tokens, cookies, or local absolute paths in report/log/error.

### Latest Technical Information

- Pydantic v2 `BaseModel` remains the repo's validation tool for structured DTOs; the project pins `pydantic>=2.13.4,<3`, and official docs document `model_validate` / `model_dump` patterns suitable for loader/report DTOs. Source: https://docs.pydantic.dev/latest/concepts/models/
- Pytest supports `python -m pytest ...`, which matches this repo's existing Windows validation commands and explicit `tests/eval` invocation. Source: https://docs.pytest.org/en/latest/how-to/usage.html
- Python 3.11 standard `argparse` is sufficient for the small dataset smoke CLI; no extra CLI dependency is needed. Source: https://docs.python.org/3.11/library/argparse.html

### References

- `_bmad-output/planning-artifacts/epics.md#Story-5.1-可执行-Eval-Dataset-结构与初始用例`
- `_bmad-output/planning-artifacts/epics.md#Epic-5-RAG-质量评估与回归证据`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-29-RAG-Eval-Dataset`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#Success-Metrics`
- `_bmad-output/planning-artifacts/architecture.md#CI-CD`
- `_bmad-output/planning-artifacts/architecture.md#Requirements-to-Structure-Mapping`
- `project-context.md`
- `_bmad-output/implementation-artifacts/3-7-retrieval-eval-fixtures-与-smoke-runner.md`
- `_bmad-output/implementation-artifacts/4-4-citation-extraction-与-query-问答.md`
- `_bmad-output/implementation-artifacts/4-7-open-webui-chat-adapter-source-detail-与轻量前端契约.md`
- `tests/eval/retrieval/dto.py`
- `tests/eval/retrieval/loader.py`
- `tests/eval/retrieval/reporting.py`
- `tests/eval/datasets/retrieval_smoke.json`
- `packages/rag/dto.py`
- `packages/rag/citation_extractor.py`
- `packages/rag/query.py`
- `README.md#Evaluation-and-Tests`
- `docs/operations/local-development.md#Retrieval-Local-Checks`
- https://docs.pydantic.dev/latest/concepts/models/
- https://docs.pytest.org/en/latest/how-to/usage.html
- https://docs.python.org/3.11/library/argparse.html

## Validation Checklist

Validation Result: PASS（2026-06-07T21:11:29+08:00）

- [x] Story 明确了 RAG eval dataset 与既有 retrieval eval dataset 的边界。
- [x] Acceptance Criteria 覆盖 20 条 synthetic RAG cases、typed loader、expected citations、no-answer、ACL、prompt injection、safe corpus、safe report、tests 和 docs。
- [x] Tasks 给出具体文件结构、DTO、loader、reporting、fixtures、smoke CLI、测试、文档和验证命令。
- [x] Dev Notes 明确当前源码状态、既有 eval patterns、previous story learnings、架构边界和非目标。
- [x] 明确默认路径不调用真实外部 provider、网络、DB、Docker、Redis、MinIO 或生产服务。
- [x] 明确 report/log/error 不保存 query、answer、chunk content、prompt、SQL、vector、embedding、provider raw response、secret、token 或本机绝对路径。

## Change Log

- 2026-06-07: Created comprehensive Story 5.1 developer context for RAG eval dataset structure, synthetic cases, typed validation, safe reporting, and local-only execution boundaries.
- 2026-06-07: Implemented typed RAG eval dataset, synthetic corpus, safe loader/reporting, dataset smoke CLI, tests, docs, and validation.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- `.venv\Scripts\python.exe -m pytest tests/unit/eval/test_rag_eval_loader.py tests/unit/eval/test_rag_eval_reporting.py tests/eval/test_rag_smoke_dataset.py` failed first as expected before `tests.eval.rag` existed, then passed after implementation.
- `.venv\Scripts\python.exe -m pytest tests/unit/eval tests/eval` passed: 37 tests.
- `.venv\Scripts\python.exe -m pytest tests/unit/rag tests/unit/retrieval tests/unit/auth` passed: 227 tests.
- `.venv\Scripts\python.exe -m pytest tests/unit/test_architecture_boundaries.py` passed: 15 tests.
- `.venv\Scripts\python.exe -m tests.eval.rag.run_dataset_smoke --dataset tests/eval/datasets/rag_smoke.json --report-dir tests/eval/reports` passed with safe summary counts.
- `.venv\Scripts\python.exe -m ruff check .` passed.
- `.venv\Scripts\python.exe -m mypy apps packages tests` passed.

### Completion Notes List

- Added `tests.eval.rag` DTOs for RAG eval cases, expected citations, answer policy, synthetic corpus records, and dataset validation using Pydantic v2.
- Added `load_rag_eval_dataset()` with stable safe `RagEvalDatasetError` details that avoid query text, answer terms, chunk content, prompt text, secrets, tokens, and local absolute paths.
- Added 20 synthetic RAG smoke cases across policy, product manual, FAQ, and technical docs, including ACL isolation, no-answer, prompt injection, citation-sensitive, and forged/unsupported citation guard scenarios.
- Added safe dataset summary/reporting and a local-only smoke CLI that performs dataset validation and report writing without calling providers, network services, databases, Redis, MinIO, Docker, or production APIs.
- Added unit/eval coverage for loader validation, safe reporting, smoke execution, category distribution, no external network path, and production package boundary against importing `tests.eval`.
- Updated README and local development docs to distinguish retrieval eval from RAG eval dataset smoke and document Story 5.1 non-goals.

### File List

- `_bmad-output/implementation-artifacts/5-1-可执行-eval-dataset-结构与初始用例.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `README.md`
- `docs/operations/local-development.md`
- `tests/eval/datasets/rag_smoke.json`
- `tests/eval/rag/__init__.py`
- `tests/eval/rag/dto.py`
- `tests/eval/rag/loader.py`
- `tests/eval/rag/reporting.py`
- `tests/eval/rag/run_dataset_smoke.py`
- `tests/eval/test_rag_smoke_dataset.py`
- `tests/unit/eval/test_rag_eval_loader.py`
- `tests/unit/eval/test_rag_eval_reporting.py`
- `tests/unit/test_architecture_boundaries.py`

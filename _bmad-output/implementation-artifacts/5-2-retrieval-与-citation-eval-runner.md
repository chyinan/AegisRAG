---
baseline_commit: b0684e5
---

# Story 5.2: Retrieval 与 Citation Eval Runner

Status: review

生成时间：2026-06-07T22:14:30+08:00

## Story

As a 平台工程师,
I want 自动运行 eval 并输出核心质量指标,
so that 检索质量和 citation 质量可以量化。

## Acceptance Criteria

1. **实现完整 RAG eval runner，不停留在 dataset smoke**
   - Given Story 5.1 已建立 `tests/eval/datasets/rag_smoke.json`、RAG eval DTO、loader 和 dataset summary
   - When 运行 Story 5.2 的 eval runner
   - Then runner 必须加载 RAG dataset，并对每条 case 执行本地 retrieval -> hydration -> context packing -> prompt build -> fake generation -> citation extraction 链路
   - And 必须复用现有生产组件：`RetrievalService`、`RagQueryApplicationService`、`RetrievalCandidateHydrator`、`ContextPacker`、`PromptBuilder`、`RagGenerationService`、`CitationExtractor`
   - And 不得复制生产 RAG 业务逻辑，不得绕过 AuthContext、tenant filter、ACL filter、context packing 或 citation extractor

2. **输出核心质量指标**
   - Given eval runner 完成全部 cases
   - When 生成 report summary
   - Then summary 至少包含 `case_count`、`passed_count`、`failed_count`、`retrieval_hit_rate`、`citation_coverage`、`no_answer_correctness`、`acl_isolation_passed`、`prompt_injection_passed`、`average_latency_ms`
   - And 每个 case result 包含 `case_id`、`request_id`、`trace_id`、`tenant_id`、`user_id`、`top_k`、`latency_ms`、`passed`、`failure_stage`、matched document/chunk/citation ID 摘要
   - And 每个 case result 记录安全的 RAG 阶段摘要：retrieval result count、context item count、citation count、unsupported count、forged reference count、prompt risk count、generation provider/model/token usage 摘要

3. **按阶段识别失败原因**
   - Given 某 case 未通过
   - When report 写入 per-case failure
   - Then `failure_stage` 必须来自 RAG eval DTO 已预留枚举：`retrieval`、`rerank`、`context_packing`、`prompt_build`、`generation`、`citation`、`permission`、`no_answer`、`dataset`、`runner`
   - And answerable case 缺少 expected document/chunk hit 标记为 `retrieval`
   - And expected citation 未覆盖 required citation 标记为 `citation`
   - And `expected_no_answer=true` 但返回可回答内容或 citation 标记为 `no_answer`
   - And ACL isolation case 暴露未授权来源、跨 tenant source 或 private ACL mismatch 标记为 `permission`
   - And prompt injection case 出现 forged citation、unsupported source claim 或 policy 被攻击文本改变的安全信号标记为 `citation` 或 `prompt_build`

4. **默认执行路径只使用 fake/local fixtures**
   - Given 开发者运行默认 RAG eval runner 或测试
   - When runner 执行
   - Then 只使用 synthetic corpus、fixture-backed retriever、in-memory chunk repository、`FakeLLMProvider` 和 `InMemoryAuditPort`
   - And 不调用 OpenAI、Qwen、DeepSeek、vLLM、Ollama、embedding API、rerank API、OpenSearch、PostgreSQL、pgvector、Redis、MinIO、Docker、HTTP API、网络服务或生产数据库
   - And 不新增 RAGAS、deepeval、pandas、click、typer、LLM-as-judge 或其他外部 eval 依赖

5. **fake generation 必须可驱动 citation 与 no-answer 判定**
   - Given answerable case 有 authorized expected citation
   - When fake generation 被调用
   - Then fake answer 必须包含可被现有 `CitationExtractor` 识别的真实 packed context citation source
   - And 对 no-answer case 必须产生现有默认无答案语义且无 citations
   - And 对 forged/unsupported citation 防护 case 必须能构造本地 fake response，使 runner 检出 forged reference 或 unsupported claim，而不是把伪造来源计为通过
   - And fake provider 输出不得来自真实企业内容，不得写入 report

6. **权限与 ACL 评估不可由 prompt 或前端推断**
   - Given case 包含 `tenant_id`、`user_id`、roles、department、permissions、metadata_filter、ACL 和 expected IDs
   - When runner 执行
   - Then 必须构造 `AuthenticatedRequestContext` 和 `AuthContext`，通过同一权限路径执行 query
   - And fixture retriever 可以返回 relevant corpus 中的授权和未授权候选，但未授权候选不得进入最终 context/citation
   - And runner 必须验证返回 citations 全部来自本 case 当前 run 的授权 corpus，不得只按 answer text 判断

7. **报告与 stdout 必须安全**
   - Given report 写入 `tests/eval/reports`
   - When 序列化 summary 和 per-case result
   - Then report、stdout、error details 和日志不得包含 query 全文、answer 全文、chunk content、prompt、SQL、vectors、embeddings、provider raw response、secret、token、cookie、object key、本机绝对路径或真实企业资料
   - And error details 只包含文件名、case_id、字段名、error_count、安全 ID、安全枚举、计数和阶段名
   - And report filename 不得覆盖同秒运行结果，沿用 Story 5.1 UUID/microsecond 防碰撞习惯

8. **提供 CLI 与文档**
   - Given 开发者需要本地运行 RAG eval runner
   - When 执行命令
   - Then 新增或扩展 CLI，例如 `python -m tests.eval.rag.run_smoke --dataset tests/eval/datasets/rag_smoke.json --report-dir tests/eval/reports`
   - And 成功返回 exit code 0，dataset validation error 返回 2，runner unexpected safe failure 返回 3
   - And README 与 `docs/operations/local-development.md` 区分三个层级：retrieval eval smoke、RAG dataset smoke、RAG quality runner
   - And 文档明确 CI gate 和阈值配置属于 Story 5.3，本 story 不把 runner 接入默认 CI

9. **测试覆盖 runner、metrics、failure stages 和安全边界**
   - Given 单元和 eval 测试运行
   - When 执行 Story 5.2 测试
   - Then 覆盖 happy path、retrieval miss、citation miss、no-answer correctness、ACL isolation、prompt injection/forged citation、generation failure、safe report serialization、CLI exit codes
   - And 测试断言默认 runner 不访问真实网络、provider、DB、Redis、MinIO、Docker 或 HTTP API
   - And 测试断言 report/stdout/error 不泄露 query、answer、chunk content、prompt、secret/token、本机绝对路径

## Tasks / Subtasks

- [x] 设计 RAG eval runner DTO 与 report shape（AC: 2, 3, 7）
  - [x] 在 `tests/eval/rag/dto.py` 中新增或扩展 `RagEvalCaseResult`、`RagEvalReportSummary`、`RagEvalReport`，保持 Pydantic v2、frozen DTO、`extra="forbid"` 风格。
  - [x] 复用现有 `FailureStage` 枚举，不新增无法被 Epic 5 使用的临时阶段名。
  - [x] Result 只保存安全 ID、counts、metrics、阶段名和 provider/model/token usage 摘要。
  - [x] 明确 `citation_coverage = matched_required_citations / required_expected_citations`；无 required citation 的集合应按 1.0 处理或在 summary 中显式记录 denominator 为 0。
  - [x] 明确 `retrieval_hit_rate` 只对 answerable cases 统计 expected document/chunk 命中。
  - [x] 明确 `no_answer_correctness` 只对 `expected_no_answer=true` cases 统计。

- [x] 实现 fixture-backed RAG runner（AC: 1, 4, 5, 6）
  - [x] 新增 `tests/eval/rag/runner.py`。
  - [x] 新增 `FixtureRagCandidateRetriever`，从 `RagEvalCorpusRecord` 生成 `RetrievalCandidate`，按 `score` 降序返回，并允许 ACL isolation fixtures 包含授权和未授权候选。
  - [x] 新增 `FixtureChunkRepository`，从 synthetic corpus 返回 `ChunkRecord`，用于 `RetrievalCandidateHydrator`，不得返回 SQLAlchemy model 或 raw dict。
  - [x] 构造 `RetrievalService(retriever=FixtureRagCandidateRetriever(...))`，不要直接手写 tenant/ACL filtering。
  - [x] 构造 `RagQueryApplicationService`，注入 `RetrievalCandidateHydrator`、`ContextPacker`、`PromptBuilder`、`RagGenerationService(FakeLLMProvider(...))`、`CitationExtractor`、`InMemoryAuditPort`。
  - [x] 每个 case 使用 `AuthenticatedRequestContext(request_id=f"eval-{case.case_id}", trace_id=f"trace-{case.case_id}", auth=AuthContext(...))`。
  - [x] 使用 `QueryCommand(query=case.query, top_k=case.top_k, metadata_filter=case.metadata_filter)`，不要通过 HTTP 调 `/query` 或 `/chat`。

- [x] 实现可控 fake answer 生成策略（AC: 5）
  - [x] 为每个 case 构造只依赖 expected IDs 和 packed citation source IDs 的 fake answer。
  - [x] answerable normal case 引用第一个 required expected citation 对应的真实 source marker，使 `CitationExtractor` 输出 citation。
  - [x] 多 required citation case 尽量覆盖全部 required citation，便于计算 coverage。
  - [x] no-answer case 不调用或不依赖 LLM 输出；若链路返回无 context，应走 `RagQueryApplicationService` 现有 no-answer 分支。
  - [x] forged/unsupported citation case 可注入一个本地 fake response，包含不存在或未授权 source marker，用于验证 `CitationExtractor` 不接受伪造来源。
  - [x] 不把 query、chunk content、prompt 或完整 answer 写入 report。

- [x] 实现 case evaluation 逻辑（AC: 2, 3, 6）
  - [x] `evaluate_rag_case(case, response)` 计算 matched documents、chunks、required citations。
  - [x] `passed` 必须同时满足 retrieval/document-or-chunk expectation、required citation coverage、no-answer expectation、ACL isolation、prompt injection/forged source expectations。
  - [x] 对 `expected_no_answer=true`，要求 `response.no_answer is True` 且 `response.citations == ()`。
  - [x] 对 answerable case，要求 `response.no_answer is False` 且 required citation 覆盖满足 expectation。
  - [x] citations 必须按 `(document_id, version_id, chunk_id)` 与当前 case authorized corpus/expected citations 匹配。
  - [x] 如果 query service 抛出 `DomainError`，转换为安全 failed case result，按 error code/stage 映射 failure_stage。

- [x] 实现 report builder 与 writer（AC: 2, 7）
  - [x] 扩展 `tests/eval/rag/reporting.py`，保留 Story 5.1 dataset summary API，不破坏 `run_dataset_smoke.py`。
  - [x] 新增 `build_rag_eval_report(results, summary)` 或等价函数。
  - [x] 新增 `write_rag_eval_report()`，文件名建议 `rag-smoke-{timestamp}-{uuid}.json`。
  - [x] 保持 report JSON sorted/indented，可用于本地 review。
  - [x] 单测断言同一秒多次写入不会覆盖。

- [x] 提供 CLI（AC: 8）
  - [x] 新增 `tests/eval/rag/run_smoke.py`，保留现有 `run_dataset_smoke.py`。
  - [x] CLI 参数支持 `--dataset`、`--report-dir`、可选 `--report-path`、可选 `--top-k`。
  - [x] 成功 stdout 只打印 safe summary JSON。
  - [x] `RagEvalDatasetError` 返回 exit code 2；其他 runner error 返回 exit code 3 且不打印 raw exception message 中的敏感内容。
  - [x] 不把此命令加入默认 `uv run pytest` 或 CI gate；Story 5.3 再接入。

- [x] 更新测试（AC: 1-9）
  - [x] 新增 `tests/unit/eval/test_rag_eval_runner.py`，覆盖 `evaluate_rag_case`、metrics summary、failure stage mapping。
  - [x] 扩展 `tests/unit/eval/test_rag_eval_reporting.py`，覆盖 full runner report safe serialization。
  - [x] 新增或扩展 `tests/eval/test_rag_smoke_dataset.py`，运行真实 `rag_smoke.json` 全部 20 cases。
  - [x] 新增 CLI 测试，覆盖 success、dataset error、unexpected safe error exit codes。
  - [x] 使用 monkeypatch 禁止 socket/network/httpx/asyncpg/redis/minio/docker 访问，断言默认 runner 只使用 fake/local objects。
  - [x] 断言 `packages/*` 仍不导入 `tests.eval`。

- [x] 更新文档（AC: 8）
  - [x] 更新 `README.md#Evaluation and Tests`，新增 RAG quality runner 命令、指标定义、report shape、安全规则、非目标。
  - [x] 更新 `docs/operations/local-development.md#Retrieval Local Checks` 或 RAG eval 小节，说明 dataset smoke 与 full RAG runner 区别。
  - [x] 明确 Story 5.2 非目标：CI gate、阈值配置、真实 provider/API eval、LLM judge、faithfulness scoring、UI dashboard。

- [x] 验证（AC: 1-9）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/eval/test_rag_eval_runner.py tests/unit/eval/test_rag_eval_reporting.py tests/eval/test_rag_smoke_dataset.py`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/rag tests/unit/retrieval tests/unit/auth tests/unit/llm`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/test_architecture_boundaries.py`
  - [x] `.venv\Scripts\python.exe -m tests.eval.rag.run_smoke --dataset tests/eval/datasets/rag_smoke.json --report-dir tests/eval/reports`
  - [x] `.venv\Scripts\python.exe -m tests.eval.rag.run_dataset_smoke --dataset tests/eval/datasets/rag_smoke.json --report-dir tests/eval/reports`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`

## Dev Notes

### Current Repository State

- Git baseline: `b0684e5 Remove BMAD local tooling`.
- Worktree was already dirty before this story was created. Existing modified/untracked files are Story 5.1 implementation artifacts and docs/tests: `README.md`, `_bmad-output/implementation-artifacts/sprint-status.yaml`, `docs/operations/local-development.md`, `tests/unit/test_architecture_boundaries.py`, `tests/eval/datasets/rag_smoke.json`, `tests/eval/rag/*`, `tests/eval/test_rag_smoke_dataset.py`, `tests/unit/eval/test_rag_eval_loader.py`, `tests/unit/eval/test_rag_eval_reporting.py`, and the Story 5.1 story file.
- `pyproject.toml` keeps default pytest collection to `tests/unit` and `tests/integration`. Eval tests and eval CLIs must be run explicitly.
- Current dependency set already includes Pydantic v2, pytest/pytest-asyncio, FastAPI, SQLAlchemy, Redis/RQ, and fake provider infrastructure. Story 5.2 should not add eval framework dependencies.
- Story 5.1 added `tests/eval/rag/dto.py`, `loader.py`, `reporting.py`, `run_dataset_smoke.py`, and `tests/eval/datasets/rag_smoke.json`. These are the input contracts for this story.

### Existing Patterns To Reuse

- `tests/eval/retrieval/runner.py` shows the preferred eval pattern: fixture retriever -> production `RetrievalService` -> per-case safe result -> aggregate safe report.
- `tests/eval/retrieval/reporting.py` and `tests/eval/rag/reporting.py` show JSON report writing patterns. Prefer the Story 5.1 microsecond + UUID filename style to avoid collisions.
- `tests/unit/rag/test_query_service.py` shows how to construct `RagQueryApplicationService` with `FakeRetrievalService`, `FakeChunkRepository`, `ContextPacker`, `PromptBuilder`, `RagGenerationService(FakeLLMProvider)`, `CitationExtractor`, and `InMemoryAuditPort`.
- `packages/rag/query.py` already records safe metadata for retrieval result count, context item count, prompt risk count, generation provider/model/token usage, citation count, unsupported count, forged reference count, latency, and error code.
- `packages/rag/hydration.py` rechecks chunk identity, tenant, active/deleted status, source metadata, page range, title path, and ACL before context packing. The eval runner must use it.
- `packages/rag/citation_extractor.py` trusts only packed context citation sources. Do not build a separate citation parser in eval code.
- `packages/llm/adapters/fake.py` is deterministic and provider-neutral. If response needs to vary per case, inject a small test provider in `tests/eval/rag/runner.py` that implements the same `LLMProvider` protocol rather than changing production fake behavior unless tests require it.

### Previous Story Intelligence

- Story 3.7 established retrieval-only eval and fixed issues around Pydantic coercion, unsafe report details, top_k validation, corpus/case linkage, default no-network behavior, and failure-stage coverage. Reuse those lessons in Story 5.2 from the start.
- Story 4.4 established `/query`, citation extraction, and the rule that citations must come from authorized packed context, not from model-written source claims.
- Story 4.5 established SSE event semantics. Story 5.2 does not need to test streaming unless useful, but metrics and report shape should not conflict with `token/citation/error/final` observability.
- Story 4.6 established chat memory with safe context. Story 5.2 should not add multi-turn eval runner unless a case explicitly needs session context; keep it out of scope.
- Story 4.7 established Open WebUI adapter and Source Inspector contract. Eval report data must remain backend-confirmed; front-end or Open WebUI must not infer permission or citation correctness.
- Story 5.1 intentionally stopped at dataset validation. Do not report Story 5.2 complete unless full RAG eval runner executes cases and computes metrics.

### Architecture Requirements

- This story belongs to Eval/Test Infrastructure. It should not add API routes, database migrations, UI pages, worker jobs, or production dependencies.
- Production packages may be imported by eval tests; production packages must not import `tests.eval`.
- RAG eval runner must preserve the production flow: retrieval -> hydration -> context packing -> prompt build -> generation -> citation extraction -> audit-safe metadata.
- `tenant_id`, `user_id`, roles, department, permissions, ACL, document_id, version_id, chunk_id, request_id, trace_id, top_k and rerank score/summary must remain visible as safe IDs/metrics.
- Reports and errors must avoid full query text, answer text, chunk content, prompt text, provider payloads, SQL, vectors, embeddings, secrets, tokens and local paths.
- Evaluation must not move authorization into prompt wording. AuthContext and ACL data drive permission behavior.

### Suggested File Structure

```text
tests/
  eval/
    rag/
      runner.py              # new full RAG quality runner
      run_smoke.py           # new CLI for Story 5.2
      dto.py                 # extend with result/report DTOs
      reporting.py           # extend while preserving dataset summary
      loader.py              # reuse, do not weaken validation
    datasets/
      rag_smoke.json
    reports/
      .gitkeep
    test_rag_smoke_dataset.py
  unit/
    eval/
      test_rag_eval_runner.py
      test_rag_eval_reporting.py
```

### Report Semantics

- `retrieval_hit_rate`: answerable cases where at least one expected document or expected chunk is present in authorized response/citation context.
- `citation_coverage`: required expected citations matched by returned citations divided by required expected citations.
- `no_answer_correctness`: no-answer cases where `response.no_answer is True` and `response.citations == ()`.
- `acl_isolation_passed`: all `attack_type="acl_isolation"` cases pass permission expectations and expose no unauthorized citation/context result.
- `prompt_injection_passed`: all `attack_type="prompt_injection"` cases avoid forged citation acceptance, unsupported source claims, and policy bypass indicators.
- `matched_citations`: store only `document_id:version_id:chunk_id` strings or equivalent safe IDs.
- `generation`: store provider/model/version/token usage/finish_reason/error_code only, not generated answer text.

### Implementation Boundaries

- Do not implement Story 5.3 CI gate or thresholds.
- Do not add `tests/eval` to default pytest `testpaths`.
- Do not call `/query`, `/chat`, Open WebUI, HTTP clients, PostgreSQL, Redis, MinIO, pgvector, OpenSearch, Docker, real rerankers, real embeddings or real LLM providers.
- Do not add real provider adapters or LLM-as-judge scoring.
- Do not store prompt, query, answer, chunk content, object key, local absolute path or provider raw response in report/log/error.
- Do not change production RAG behavior unless the runner exposes a real bug that must be fixed with tests.

### Latest Technical Information

- Pydantic v2 remains the repo validation tool; use `BaseModel.model_validate()` for typed loading and `model_dump(mode="json")` for report serialization. Source: https://docs.pydantic.dev/latest/concepts/models/
- Pytest supports `python -m pytest ...`, matching this repo's Windows validation commands and explicit eval test invocation. Source: https://docs.pytest.org/en/latest/how-to/usage.html
- Python 3.11 standard `argparse` is sufficient for this small CLI; no `click` or `typer` dependency is needed. Source: https://docs.python.org/3.11/library/argparse.html

### References

- `_bmad-output/planning-artifacts/epics.md#Story-5.2-Retrieval-与-Citation-Eval-Runner`
- `_bmad-output/planning-artifacts/epics.md#Epic-5-RAG-质量评估与回归证据`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-12-Retrieval-Log`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-16-Citation`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-29-RAG-Eval-Dataset`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#Success-Metrics`
- `_bmad-output/planning-artifacts/architecture.md#Requirements-to-Structure-Mapping`
- `_bmad-output/planning-artifacts/architecture.md#CI-CD`
- `_bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md#Eval-Reports`
- `project-context.md`
- `_bmad-output/implementation-artifacts/3-7-retrieval-eval-fixtures-与-smoke-runner.md`
- `_bmad-output/implementation-artifacts/4-4-citation-extraction-与-query-问答.md`
- `_bmad-output/implementation-artifacts/4-7-open-webui-chat-adapter-source-detail-与轻量前端契约.md`
- `_bmad-output/implementation-artifacts/5-1-可执行-eval-dataset-结构与初始用例.md`
- `tests/eval/retrieval/runner.py`
- `tests/eval/retrieval/reporting.py`
- `tests/eval/rag/dto.py`
- `tests/eval/rag/loader.py`
- `tests/eval/rag/reporting.py`
- `tests/eval/rag/run_dataset_smoke.py`
- `tests/eval/datasets/rag_smoke.json`
- `packages/rag/query.py`
- `packages/rag/hydration.py`
- `packages/rag/dto.py`
- `packages/rag/citation_extractor.py`
- `packages/rag/context_packer.py`
- `packages/rag/prompt_builder.py`
- `packages/rag/generation.py`
- `packages/llm/adapters/fake.py`
- `tests/unit/rag/test_query_service.py`
- `README.md#Evaluation-and-Tests`
- `docs/operations/local-development.md#RAG-Query-Local-Checks`
- https://docs.pydantic.dev/latest/concepts/models/
- https://docs.pytest.org/en/latest/how-to/usage.html
- https://docs.python.org/3.11/library/argparse.html

## Validation Checklist

Validation Result: PASS（2026-06-07T22:14:30+08:00）

- [x] Story 明确区分了 RAG dataset smoke 和完整 RAG quality runner。
- [x] Acceptance Criteria 覆盖 retrieval hit rate、citation coverage、no-answer correctness、ACL isolation、prompt injection、safe report、fake/local execution 和 CLI。
- [x] Tasks 给出具体文件结构、runner 构造、DTO/report、fake answer、case evaluation、CLI、测试、文档和验证命令。
- [x] Dev Notes 明确当前源码状态、既有 eval/RAG patterns、上一条 story 的边界、架构约束和非目标。
- [x] 明确默认路径不调用真实 provider、网络、DB、Docker、Redis、MinIO 或生产服务。
- [x] 明确 report/log/error 不保存 query、answer、chunk content、prompt、SQL、vector、embedding、provider raw response、secret、token、object key 或本机绝对路径。

## Change Log

- 2026-06-07: Implemented full local RAG quality runner, report writer, CLI, tests, docs, and validation.
- 2026-06-07: Created comprehensive Story 5.2 developer context for full local RAG eval runner, metrics, safe reporting, fake provider execution, CLI, tests and docs.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- 2026-06-07T22:34+08:00: Full RAG runner CLI returned 20/20 passing cases with retrieval_hit_rate=1.0, citation_coverage=1.0, no_answer_correctness=1.0.
- 2026-06-07T22:36+08:00: `ruff check .` passed.
- 2026-06-07T22:36+08:00: `mypy apps packages tests` passed.
- 2026-06-07T22:36+08:00: Default pytest suite passed: 618 tests.

### Completion Notes List

- Implemented frozen RAG eval result/report DTOs with safe per-stage counts, matched ID summaries, generation provider/model/token usage summaries, and aggregate metrics.
- Implemented fixture-backed full RAG eval runner that executes local retrieval, hydration, context packing, prompt building, fake generation, and citation extraction through existing production services.
- Added controllable fake eval provider for normal, forged/citation-miss, and generation-failure paths without real provider, network, DB, Redis, MinIO, Docker, or HTTP access.
- Added full RAG smoke CLI with safe stdout, report writing, top_k override, and exit codes 0/2/3.
- Added unit/eval coverage for runner metrics, failure stages, safe serialization, CLI exit codes, and no-network behavior; updated docs for RAG dataset smoke vs quality runner.

### File List

- README.md
- docs/operations/local-development.md
- tests/eval/rag/dto.py
- tests/eval/rag/reporting.py
- tests/eval/rag/runner.py
- tests/eval/rag/run_smoke.py
- tests/eval/test_rag_smoke_dataset.py
- tests/unit/eval/test_rag_eval_cli.py
- tests/unit/eval/test_rag_eval_reporting.py
- tests/unit/eval/test_rag_eval_runner.py
- _bmad-output/implementation-artifacts/5-2-retrieval-与-citation-eval-runner.md
- _bmad-output/implementation-artifacts/sprint-status.yaml

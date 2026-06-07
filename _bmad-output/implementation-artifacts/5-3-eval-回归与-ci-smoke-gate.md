---
baseline_commit: 0f7be94
---

# Story 5.3: Eval 回归与 CI Smoke Gate

Status: done

生成时间：2026-06-07T23:51:13+08:00

## Story

As a 项目维护者,
I want 在 CI 或本地命令中运行轻量 RAG eval smoke test,
so that 核心 RAG 质量不会被无意破坏。

## Acceptance Criteria

1. **提供本地可执行 RAG eval smoke gate 命令**
   - Given Story 5.1 已提供 `tests/eval/datasets/rag_smoke.json` 和 dataset smoke，Story 5.2 已提供 `tests.eval.rag.run_smoke`
   - When 开发者运行 CI smoke gate 命令
   - Then 命令必须执行快速 synthetic RAG eval cases，默认使用 `tests/eval/datasets/rag_smoke.json`
   - And 必须复用 Story 5.2 的 `run_rag_eval()` 或等价 runner，不复制 retrieval、hydration、context packing、prompt build、generation、citation extraction 业务逻辑
   - And 成功返回 exit code `0`，阈值或 case 失败返回非零退出码，dataset validation error 与 runner unexpected error 使用稳定、可测试的退出码

2. **阈值配置不得硬编码在业务代码中**
   - Given MVP eval 阈值仍在校准阶段
   - When gate 判断 `retrieval_hit_rate`、`citation_coverage`、`no_answer_correctness`、`acl_isolation_passed`、`prompt_injection_passed`、`failed_count`
   - Then 阈值必须来自显式配置文件、CLI 参数或环境变量，建议新增 `tests/eval/config/rag_smoke_gate.json`
   - And 初始默认值应对齐 PRD Success Metrics：retrieval hit rate >= 0.80、citation coverage >= 0.90、no-answer correctness >= 0.85、permission leakage = 0
   - And 配置摘要必须写入 gate report，便于后续审计为什么某次 CI 通过或失败

3. **报告产物包含 commit/time/config 摘要**
   - Given CI 或本地 gate 执行完成
   - When 写入 report
   - Then report 必须写入 `tests/eval/reports` 或配置的输出目录
   - And report 至少包含 `generated_at`、`commit_sha`、`branch` 或可用的本地 git 摘要、dataset path 摘要、threshold config 摘要、runner summary、failed case IDs、failure stages
   - And report 文件名不得覆盖同秒运行结果，沿用 microsecond + UUID 防碰撞风格
   - And stdout 只输出安全 summary，不输出 query、answer、chunk content、prompt、provider payload、secret、token、本机绝对路径或企业机密全文

4. **CI workflow 串联 lint、unit、integration mock、eval smoke**
   - Given 仓库当前没有 `.github/workflows` 配置
   - When 实现本 story
   - Then 新增 GitHub Actions CI workflow，例如 `.github/workflows/ci.yml`
   - And workflow 至少运行 `ruff check .`、`pytest tests/unit`、`pytest tests/integration`、RAG eval smoke gate
   - And 使用 `uv.lock` 和 `.python-version` 安装依赖，优先通过 `uv sync --dev --frozen` 保证 CI 与本地依赖一致
   - And workflow 上传安全 eval report artifact，并设置短期 retention；artifact 不能包含 secrets、完整 query、完整 answer、chunk content 或本机路径

5. **默认 CI gate 不依赖真实外部服务**
   - Given CI 环境没有 OpenAI、Qwen、DeepSeek、vLLM、Ollama、PostgreSQL、Redis、MinIO、pgvector、OpenSearch 或 Docker Compose 服务
   - When 运行 RAG eval smoke gate
   - Then 默认路径必须只使用 synthetic corpus、fake/local retriever、fake generation、in-memory audit 和现有测试 fixtures
   - And 不调用真实 LLM、embedding、rerank、HTTP API、数据库、Redis、MinIO、Docker、Open WebUI 或网络服务
   - And 不新增 RAGAS、deepeval、pandas、click、typer、LLM-as-judge 或其他外部 eval 依赖

6. **失败输出可定位但不泄密**
   - Given 某个 case 或阈值失败
   - When gate 输出失败详情
   - Then 失败详情必须包含 case_id、failure_stage、安全 metric 值、阈值名、expected/actual 数值和 report path 摘要
   - And 不输出 query 全文、answer 全文、chunk content、prompt、SQL、vectors、embeddings、provider raw response、secret、token、cookie、object key、本机绝对路径或真实企业资料
   - And threshold failure 不得被吞掉；CI 必须失败

7. **测试覆盖本地命令、阈值判断、报告和 CI 配置**
   - Given 单元、集成和 eval 测试运行
   - When 执行本 story 测试
   - Then 覆盖 gate success、threshold failure、dataset validation failure、runner unexpected safe failure、report serialization、stdout safety、CLI exit codes
   - And 测试断言默认 gate 不访问真实网络、provider、DB、Redis、MinIO、Docker 或 HTTP API
   - And 测试校验 GitHub Actions workflow 包含 lint、unit、integration mock、eval smoke、artifact upload 和最小权限配置

8. **文档说明本地与 CI 的使用方式**
   - Given 开发者阅读 README 或 local development docs
   - When 查找 eval regression / CI smoke gate
   - Then 能看到本地命令、CI gate 命令、threshold config 位置、report 位置、exit code 语义、安全输出规则和 known limitations
   - And 文档明确真实 provider/API eval、LLM judge、faithfulness scoring、dashboard 和长期趋势分析不属于本 story

## Tasks / Subtasks

- [x] 设计 RAG eval gate 配置和结果 DTO（AC: 2, 3, 6）
  - [x] 新增 `tests/eval/rag/gate.py` 或等价模块，定义 `RagEvalGateThresholds`、`RagEvalGateConfig`、`RagEvalGateReport`、`RagEvalGateDecision`。
  - [x] 配置字段至少包含 `min_retrieval_hit_rate`、`min_citation_coverage`、`min_no_answer_correctness`、`require_acl_isolation_passed`、`require_prompt_injection_passed`、`max_failed_count`。
  - [x] 阈值 DTO 使用 Pydantic v2，`extra="forbid"`，拒绝布尔伪装数字、负数、超过 1 的 rate、未知字段和空配置。
  - [x] 配置加载错误必须是稳定 safe error，不输出本机绝对路径或原始 JSON 全文。

- [x] 新增可执行 CI smoke gate CLI（AC: 1, 2, 3, 5, 6）
  - [x] 新增 `tests/eval/rag/run_ci_smoke.py`，支持 `--dataset`、`--config`、`--report-dir`、`--report-path`、`--top-k`。
  - [x] CLI 默认读取 `tests/eval/datasets/rag_smoke.json` 和 `tests/eval/config/rag_smoke_gate.json`。
  - [x] CLI 必须调用 Story 5.2 的 `run_rag_eval()`，不得重新实现 RAG 链路。
  - [x] 建议 exit codes：`0` success、`1` threshold/case failure、`2` dataset/config validation error、`3` unexpected safe runner error。
  - [x] stdout 输出 compact safe JSON summary：case counts、metrics、decision、failed case IDs、report filename 摘要。

- [x] 新增阈值配置文件（AC: 2）
  - [x] 新增 `tests/eval/config/rag_smoke_gate.json`。
  - [x] 初始阈值按 PRD Success Metrics 设置：retrieval >= 0.80、citation >= 0.90、no-answer >= 0.85、ACL/prompt injection 必须通过、failed_count <= 0。
  - [x] 配置文件只包含阈值、说明性安全 ID 和 gate 名称，不包含 query、answer、chunk content、secret、token 或环境路径。

- [x] 实现 gate report writer（AC: 3, 6）
  - [x] 扩展 `tests/eval/rag/reporting.py` 或在 `gate.py` 中实现 `write_rag_eval_gate_report()`。
  - [x] report 包含 generated_at、commit_sha、branch、config 摘要、dataset basename 或 repo-relative path、runner summary、threshold decision、failed case IDs、failure stages。
  - [x] commit/branch 优先从 `GITHUB_SHA`、`GITHUB_REF_NAME` 读取；本地可用 `git rev-parse --short HEAD` 和 `git branch --show-current`，失败时写 `unknown`，不得导致 gate 失败。
  - [x] report 路径默认 `tests/eval/reports/rag-ci-smoke-{timestamp}-{uuid}.json`。

- [x] 新增 GitHub Actions CI workflow（AC: 4, 5, 7）
  - [x] 新增 `.github/workflows/ci.yml`。
  - [x] workflow 触发 `push` 和 `pull_request`。
  - [x] 设置最小权限，例如 `permissions: contents: read`。
  - [x] 使用 `.python-version`，通过 `uv sync --dev --frozen` 安装依赖。
  - [x] 顺序运行 `.venv` 或 `uv run` 下的 `ruff check .`、`pytest tests/unit`、`pytest tests/integration`、`python -m tests.eval.rag.run_ci_smoke --dataset tests/eval/datasets/rag_smoke.json --config tests/eval/config/rag_smoke_gate.json --report-dir tests/eval/reports`。
  - [x] 使用 `actions/upload-artifact` 上传 `tests/eval/reports/*.json`，artifact 名称不包含 secret，retention 建议 7 天。

- [x] 更新测试（AC: 1-7）
  - [x] 新增 `tests/unit/eval/test_rag_eval_gate.py` 覆盖 threshold pass/fail、config validation、safe report 和 stdout payload。
  - [x] 扩展或新增 CLI 测试，覆盖 exit code 0/1/2/3。
  - [x] 新增 CI workflow 结构测试，例如 `tests/unit/test_ci_workflow.py` 或扩展 architecture boundary test，使用文本/YAML 解析确认 workflow steps。
  - [x] 使用 monkeypatch 禁止 socket/httpx/asyncpg/redis/minio/docker 访问，断言默认 gate 只走 fake/local runner。
  - [x] 断言 report/stdout/error 不包含 query、answer、chunk content、prompt、secret/token、本机绝对路径。

- [x] 更新文档（AC: 8）
  - [x] 更新 `README.md#Evaluation and Tests`，新增 CI smoke gate 命令、threshold config、exit codes、report artifact 和安全输出规则。
  - [x] 更新 `docs/operations/local-development.md`，新增本地 gate 运行示例、失败排查和阈值调整说明。
  - [x] 明确非目标：真实 provider/API eval、LLM-as-judge、faithfulness scoring、dashboard、长期趋势存储、Docker Compose 依赖服务。

- [x] 验证（AC: 1-8）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/eval/test_rag_eval_gate.py tests/unit/eval/test_rag_eval_cli.py tests/eval/test_rag_smoke_dataset.py`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit tests/integration`
  - [x] `.venv\Scripts\python.exe -m tests.eval.rag.run_ci_smoke --dataset tests/eval/datasets/rag_smoke.json --config tests/eval/config/rag_smoke_gate.json --report-dir tests/eval/reports`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`

### Review Findings

- [x] [Review][Patch] Invalid `--top-k` is reported as unexpected runner error instead of validation exit code 2 (AC1, AC7) [tests/eval/rag/run_ci_smoke.py:39]
- [x] [Review][Patch] Threshold failure stdout omits threshold name and expected/actual values required for safe diagnosis (AC6) [tests/eval/rag/run_ci_smoke.py:69]
- [x] [Review][Patch] Gate CLI tests do not directly guard against real network/provider/DB/Redis/MinIO/Docker access (AC7) [tests/unit/eval/test_rag_eval_ci_cli.py:15]
- [x] [Review][Patch] Gate threshold validation accepts non-finite JSON rates such as `NaN` (rate validation safety gap) [tests/eval/rag/gate.py:48]

## Dev Notes

### Current Repository State

- Git baseline for this story context: `0f7be94 fix(eval): address rag eval review findings`.
- Sprint status auto-selected `5-3-eval-回归与-ci-smoke-gate` as the next story before this story file was created.
- Worktree already contains unrelated local changes before story creation: `.gitignore` modified, `_bmad/` untracked, `tests/unit/bmad/` untracked. Do not include those in this story implementation unless the user explicitly asks.
- `.github/workflows` does not currently exist. This story is expected to add the first CI workflow.
- `uv.lock` and `.python-version` already exist. Prefer `uv sync --dev --frozen` in CI to avoid dependency drift.
- `pyproject.toml` default pytest collection remains `tests/unit` and `tests/integration`; eval tests and eval CLIs are explicit commands.

### Existing Patterns To Reuse

- `tests/eval/rag/run_smoke.py` is the Story 5.2 full local RAG quality runner CLI. It loads `RagEvalDataset`, calls `run_rag_eval()`, writes report through the runner, prints safe summary JSON, and returns `0/2/3`.
- `tests/eval/rag/runner.py` already executes the local production chain through existing services. The gate must call this runner rather than rebuilding its own RAG logic.
- `tests/eval/rag/reporting.py` already uses timestamp + UUID report filenames for dataset and quality runner reports. Use the same collision-resistant pattern for gate reports.
- `tests/eval/rag/dto.py` already defines `RagEvalReportSummary` and `RagEvalCaseResult`; gate logic can compare thresholds against these safe metrics.
- `tests/unit/eval/test_rag_eval_cli.py`, `test_rag_eval_runner.py`, and `test_rag_eval_reporting.py` show current CLI/report/runner test patterns.
- `tests/unit/test_architecture_boundaries.py` already checks production packages do not import `tests.eval`; keep that boundary intact.

### Previous Story Intelligence

- Story 5.1 intentionally created synthetic-only dataset, typed validation, and safe reporting. Do not weaken its validation to make CI easier.
- Story 5.2 intentionally stopped before CI and thresholds. It added full local quality runner metrics but no gate decision policy. Story 5.3 owns that policy and wiring.
- Story 5.2 review findings fixed citation source markers, expected-answer evaluation, retrieval failure classification, permission fidelity, non-vacuous ACL/prompt-injection flags, and broader external-service protection tests. Preserve those protections.
- Existing runner currently reports 20/20 passing cases on the synthetic dataset. The initial gate config can require zero failed cases without breaking current fixtures.

### Architecture Requirements

- This story belongs to Eval/Test Infrastructure and CI/CD. It should not add API routes, database migrations, UI pages, worker jobs, production RAG dependencies, real provider adapters, or Docker Compose requirements.
- Production packages may be imported by eval tests; production packages must not import `tests.eval`.
- CI must not make authorization a prompt concern. Permission leakage remains evaluated by AuthContext/ACL/citation data and runner metrics.
- Reports are quality evidence, not raw traces. They may include IDs, counts, safe enum names, metrics, generated_at, commit SHA, branch, config names, and report filenames. They must not include full query text, answer text, chunk content, prompts, SQL, vectors, embeddings, provider payloads, secrets, tokens, cookies, object keys, local absolute paths, or real enterprise data.
- Keep thresholds configurable. Do not put policy numbers inside `packages/*` or hard-code them in production code.

### Suggested File Structure

```text
.github/
  workflows/
    ci.yml
tests/
  eval/
    config/
      rag_smoke_gate.json
    rag/
      gate.py
      run_ci_smoke.py
      reporting.py
      runner.py
      dto.py
  unit/
    eval/
      test_rag_eval_gate.py
      test_rag_eval_cli.py
    test_ci_workflow.py
```

### Gate Semantics

- `retrieval_hit_rate` passes when `summary.retrieval_hit_rate >= thresholds.min_retrieval_hit_rate`.
- `citation_coverage` passes when `summary.citation_coverage >= thresholds.min_citation_coverage`.
- `no_answer_correctness` passes when `summary.no_answer_correctness >= thresholds.min_no_answer_correctness`.
- `permission_leakage` passes when `summary.acl_isolation_passed is True` and every permission-related case result passes.
- `prompt_injection` passes when `summary.prompt_injection_passed is True`.
- `failed_count` passes when `summary.failed_count <= thresholds.max_failed_count`.
- Gate failure should include failed metric names and failed case IDs only. Do not copy case query text or generated answer into output.

### Implementation Boundaries

- Do not modify retrieval/RAG production services unless a test exposes a real bug needed to satisfy the gate.
- Do not add `tests/eval` to default pytest `testpaths`.
- Do not call `/query`, `/chat`, Open WebUI, HTTP clients, PostgreSQL, Redis, MinIO, pgvector, OpenSearch, Docker, real rerankers, real embeddings or real LLM providers.
- Do not add RAGAS, deepeval, pandas, click, typer, LLM-as-judge, benchmark dashboards, historical trend storage, or external telemetry dependencies.
- Do not commit generated `tests/eval/reports/*.json` unless the repo already tracks a deliberate fixture report and the story explicitly updates it. CI artifacts should upload generated reports instead.

### Latest Technical Information

- GitHub Actions should use least-privilege workflow token permissions such as `contents: read` for read-only CI jobs. Source: https://docs.github.com/en/actions/security-guides/automatic-token-authentication
- GitHub Actions artifact upload supports retention controls; use a short retention window for eval reports because they are reproducible smoke artifacts. Source: https://docs.github.com/en/actions/using-workflows/storing-workflow-data-as-artifacts
- `actions/setup-python` supports reading a Python version from `.python-version`; use it rather than duplicating the version string in multiple places. Source: https://github.com/actions/setup-python
- `uv` supports GitHub Actions workflows and frozen sync; prefer `uv sync --dev --frozen` with the checked-in `uv.lock`. Source: https://docs.astral.sh/uv/guides/integration/github/

### References

- `_bmad-output/planning-artifacts/epics.md#Story-5.3-Eval-回归与-CI-Smoke-Gate`
- `_bmad-output/planning-artifacts/epics.md#Epic-5-RAG-质量评估与回归证据`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-29`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#FR-30`
- `_bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md#Success-Metrics`
- `_bmad-output/planning-artifacts/architecture.md#CI-CD`
- `_bmad-output/planning-artifacts/architecture.md#Requirements-to-Structure-Mapping`
- `project-context.md`
- `_bmad-output/implementation-artifacts/5-1-可执行-eval-dataset-结构与初始用例.md`
- `_bmad-output/implementation-artifacts/5-2-retrieval-与-citation-eval-runner.md`
- `tests/eval/rag/run_smoke.py`
- `tests/eval/rag/runner.py`
- `tests/eval/rag/reporting.py`
- `tests/eval/rag/dto.py`
- `tests/eval/datasets/rag_smoke.json`
- `tests/unit/eval/test_rag_eval_cli.py`
- `tests/unit/eval/test_rag_eval_runner.py`
- `README.md#Evaluation-and-Tests`
- `docs/operations/local-development.md#RAG-Quality-Runner`

## Validation Checklist

Validation Result: PASS（2026-06-07T23:51:13+08:00）

- [x] Story 明确 Story 5.3 只负责 CI/local gate、threshold policy、report config summary 和 workflow wiring，不重写 RAG runner。
- [x] Acceptance Criteria 覆盖本地命令、阈值配置、commit/time/config report、CI workflow、fake/local execution、安全失败输出、测试和文档。
- [x] Tasks 给出具体文件结构、DTO、CLI、配置、report writer、GitHub Actions、测试、文档和验证命令。
- [x] Dev Notes 明确当前源码状态、现有 runner patterns、前序 story 约束、架构边界、非目标和安全输出要求。
- [x] 明确默认 gate 不调用真实 provider、网络、DB、Docker、Redis、MinIO、Open WebUI 或生产服务。
- [x] 明确 report/stdout/error 不保存 query、answer、chunk content、prompt、SQL、vector、embedding、provider raw response、secret、token、object key 或本机绝对路径。

## Change Log

- 2026-06-07: Created comprehensive Story 5.3 developer context for RAG eval regression gate, threshold config, safe reports, CI workflow, tests and docs.
- 2026-06-08: Implemented RAG eval CI smoke gate, threshold config, safe reports, GitHub Actions workflow, tests, and docs; moved story to review.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- 2026-06-08T00:04: red phase confirmed missing `tests.eval.rag.gate` and `tests.eval.rag.run_ci_smoke` modules.
- 2026-06-08T00:04: `.venv\Scripts\python.exe -m pytest tests/unit/eval/test_rag_eval_gate.py tests/unit/eval/test_rag_eval_ci_cli.py tests/unit/test_ci_workflow.py` failed during collection before implementation.
- 2026-06-08T00:04: `.venv\Scripts\python.exe -m pytest tests/unit/eval/test_rag_eval_gate.py tests/unit/eval/test_rag_eval_ci_cli.py tests/eval/test_rag_smoke_dataset.py tests/unit/test_ci_workflow.py` passed: 15 passed.
- 2026-06-08T00:04: `.venv\Scripts\python.exe -m tests.eval.rag.run_ci_smoke --dataset tests/eval/datasets/rag_smoke.json --config tests/eval/config/rag_smoke_gate.json --report-dir tests/eval/reports` passed with decision `pass`.
- 2026-06-08T00:05: `.venv\Scripts\python.exe -m ruff check .` passed.
- 2026-06-08T00:05: `.venv\Scripts\python.exe -m pytest tests/unit tests/integration` passed: 639 passed.
- 2026-06-08T00:06: `.venv\Scripts\python.exe -m mypy apps packages tests` passed.

### Completion Notes List

- Implemented strict Pydantic v2 gate threshold/config DTOs, safe config loading errors, gate decisions, and safe report writing in eval-only code.
- Added `tests.eval.rag.run_ci_smoke` CLI that reuses Story 5.2 `run_rag_eval()` and returns stable exit codes `0/1/2/3`.
- Added default PRD-aligned threshold config and a least-privilege GitHub Actions workflow that runs lint, unit, integration, eval smoke gate, and uploads short-retention reports.
- Added unit coverage for threshold pass/fail, config validation, report safety, CLI exit codes, stdout safety, and workflow structure.
- Updated README and local development docs with local/CI gate commands, report location, exit semantics, safety rules, and known limitations.

### File List

- `.github/workflows/ci.yml`
- `README.md`
- `_bmad-output/implementation-artifacts/5-3-eval-回归与-ci-smoke-gate.md`
- `_bmad-output/implementation-artifacts/sprint-status.yaml`
- `docs/operations/local-development.md`
- `tests/eval/config/rag_smoke_gate.json`
- `tests/eval/rag/gate.py`
- `tests/eval/rag/run_ci_smoke.py`
- `tests/unit/eval/test_rag_eval_ci_cli.py`
- `tests/unit/eval/test_rag_eval_gate.py`
- `tests/unit/test_ci_workflow.py`

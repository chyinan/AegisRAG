---
baseline_commit: e4f737f
---

# Story 6.7: Agent Final Answer Validation

Status: done

生成时间：2026-06-08T16:23:15+08:00

## Story

As a 授权用户,
I want Agent 最终回答在返回前经过权限、citation 和工具错误校验,
so that Agent 不会输出未授权来源、伪造引用或忽略失败工具结果。

## Acceptance Criteria

1. **Agent final answer 必须经过 backend validator 后才能返回**
   - Given Agent Runtime 得到 final answer decision
   - When final answer validation 执行
   - Then validator 检查回答是否引用未授权来源、伪造 citation、失败工具结果或无授权 observation 支撑的来源
   - And 验证失败时返回结构化错误或安全降级回答
   - And validation 逻辑在 backend runtime/application service 中执行，不能放入 prompt 或交给 LLM 自判

2. **`rag_search` citation 只能来自本次 run 的授权 observation**
   - Given final answer 引用了 `rag_search` 来源
   - When validator 校验 citation
   - Then citation 必须匹配本次 agent run 中成功 `rag_search` observation 暴露的 `document_id`、`version_id`、`chunk_id`、page/source metadata
   - And 不允许 LLM 自行编造 `document_id`、`version_id`、`chunk_id`、page 或 source
   - And citation 不得来自失败、denied、timeout、rate limited 或 schema validation failed 的工具调用

3. **失败工具结果不能被当成事实来源使用**
   - Given tool call 中存在 `failure`、`denied`、timeout、rate limit、permission denied、schema validation failed、handler failed 或 structured tool error
   - When final answer 试图引用或依赖该结果
   - Then validator 标记 unsupported 或返回安全错误
   - And 安全降级回答不得复述失败工具的 raw output、raw arguments、query、prompt、文件内容、路径或异常详情

4. **validator 必须产生结构化 validation outcome**
   - Given final answer validation 完成
   - When runtime 写入结果
   - Then outcome 至少包含 `status`、`latency_ms`、`error_code`、`validated_citation_count`、`unsupported_citation_count`、`failed_tool_reference_count`
   - And outcome metadata 只包含安全计数、工具名、citation identifiers 和错误码，不包含 raw answer、prompt、hidden reasoning、raw tool output、chunk text、文件内容、query 原文或密钥

5. **审计必须记录 final_answer_validation**
   - Given validation 成功、失败或降级
   - When audit event 写入
   - Then audit log 记录 action `agent.final_answer_validation` 或等价稳定 action
   - And 包含 request_id、trace_id、tenant_id、user_id、agent_run_id、status、latency_ms、error_code 和安全 metadata
   - And final answer validation audit 失败不能伪造业务成功；若审计失败按现有 audit 容错策略处理，但不得泄露异常详情

6. **Agent run status 和 response 必须反映 validation 结果**
   - Given final answer validation 通过
   - When `/agent/run` 返回
   - Then run 可完成为 `completed`，响应可包含 validated final answer 和 validated citations
   - And 如果当前 API 选择暂不返回 answer，README 和 story completion notes 必须明确说明 validation 已执行但响应仍是 status-only
   - Given validation 失败且无法安全降级
   - When service 更新 `agent_runs`
   - Then run 状态为 `failed` 或 `stopped` 的明确终止状态，并带稳定 error_code

7. **架构边界必须保持 Agent core storage/provider neutral**
   - Given 新增 final answer validation DTO、validator 或 policy
   - When boundary tests 运行
   - Then `packages/agent/runtime.py`、`registry.py`、`dto.py`、`policies.py`、`exceptions.py` 不导入 FastAPI、SQLAlchemy、provider SDK、retrieval internals 或 storage repositories
   - And `apps/api/routes/agent.py` 仍保持 thin，只做 schema、context、service 调用和 envelope
   - And 不新增 LangChain、LangGraph、LlamaIndex、Haystack 或真实 LLM provider 依赖

8. **README 必须同步能力和限制**
   - Given Story 6.7 完成
   - When 更新 README
   - Then Build Status、Governed Agent Tools、Auditability and Observability、Current Limits 必须说明 final answer validation 已完成
   - And `tool event streaming`、Open WebUI function/tool bridge 和 real LLM-backed planning 仍是未完成能力
   - And 如果 `/agent/run` 仍不返回 answer，README 必须明确当前 API 响应边界

9. **测试必须覆盖成功、伪造 citation、失败工具和边界**
   - Given 单元和集成测试运行
   - When 执行 Story 6.7 测试
   - Then 覆盖 validation success、invented citation、cross-run citation、failed tool reference、denied tool reference、structured tool error reference、no-answer/safe downgrade、audit event、runtime/service status mapping、metadata redaction、route thinness、README expectation
   - And 测试使用 fake stepper、fake validator、fake tools、in-memory audit 或 SQLite fixtures，不调用真实 LLM、embedding、vector store、PostgreSQL、Redis、MinIO、Open WebUI、网络或外部 provider

## Tasks / Subtasks

- [x] 定义 final answer validation DTO 和 validator port（AC: 1-4, 7）
  - [x] 在 `packages/agent/dto.py` 或新 `packages/agent/final_answer.py` 中新增 storage-neutral DTO，例如 `AgentCitationRef`、`AgentFinalAnswer`、`FinalAnswerValidationRequest`、`FinalAnswerValidationResult`。
  - [x] Citation ref 至少包含 `document_id`、`version_id`、`chunk_id`、`source`、`page_start`、`page_end`，并可选包含 `tool_name`、`observation_index`。
  - [x] 定义稳定 status，例如 `valid`、`degraded`、`invalid`。
  - [x] 定义稳定 error codes，例如 `AGENT_FINAL_ANSWER_UNSUPPORTED_CITATION`、`AGENT_FINAL_ANSWER_UNAUTHORIZED_SOURCE`、`AGENT_FINAL_ANSWER_FAILED_TOOL_REFERENCE`、`AGENT_FINAL_ANSWER_VALIDATION_FAILED`。
  - [x] DTO 禁止 raw answer 之外的 raw tool payload 字段；audit/metadata DTO 不得包含 raw answer、prompt、query、chunk text 或 file content。

- [x] 从成功 tool observation 提取可验证 citation evidence（AC: 2-4）
  - [x] 扩展 `AgentObservationSummary`，只增加安全字段，例如 `citation_refs`、`error_code`、`result_status`，不要保存 raw output。
  - [x] 在 `_summarize_tool_result()` 中从 `rag_search` 成功 output 的 `results` 提取 citation refs。
  - [x] 只接受 output schema 已验证后的 `RagSearchOutput` 字段，不从 raw tool output 或 LLM 文本中提取授权依据。
  - [x] 对非 `rag_search` 工具默认不产生 citation refs；`calculator` 不作为来源 citation，`file_reader` 如未来允许 citation 也必须有独立 allowlist evidence 规则，本 story 默认不允许。
  - [x] 保持 observation 和 audit metadata 只包含 safe identifiers、counts、tool names 和 error codes。

- [x] 在 runtime final answer 路径接入 validator（AC: 1, 3, 4, 6, 7）
  - [x] 给 `AgentRuntime` 增加可选 `final_answer_validator` 依赖；默认实现可为 strict no-op validator，但 API assembly 应注入真实 validator。
  - [x] 在 `decision.action is FINAL_ANSWER` 分支、调用 `_finish()` 前执行 validator。
  - [x] Validator 输入包含 `agent_run_id`、AuthContext/request context、final answer text、final answer citation refs、当前 run 的 observation summaries。
  - [x] Validator 输出决定 completed、degraded completed、failed 或 stopped；不要让 LLM 决定 run status。
  - [x] 如果 validator 本身 unexpected failure，返回结构化 `AGENT_FINAL_ANSWER_VALIDATION_FAILED`，不泄露异常文本。

- [x] 明确 final answer citation 输入契约（AC: 1, 2, 6）
  - [x] 保持 `AgentStepDecision.final_answer: str | None` 兼容现有 tests。
  - [x] 增加可选 `final_citations: tuple[AgentCitationRef, ...] = ()` 或等价结构化字段，使 future LLM-backed stepper 可以提交 citations。
  - [x] 如果 final answer text 中出现 source-like identifiers 但 `final_citations` 为空，validator 应保守降级或失败，避免文本伪造来源绕过结构化校验。
  - [x] 不要求在本 story 实现真实 LLM citation parser；需要的是 backend 结构化 validation boundary。

- [x] 记录 final_answer_validation audit（AC: 4, 5）
  - [x] 在 validator 或 runtime 中通过现有 `AuditPort` 记录 `agent.final_answer_validation`。
  - [x] Audit resource 使用 `agent_run`，resource metadata 可包含 `agent_run_id`。
  - [x] 成功 status 为 success，降级可为 denied 或 failure 中项目已有最接近语义，失败为 failure。
  - [x] Metadata 只包含 validation counts、safe citation identifiers、safe tool names、error_code、validation status。
  - [x] 审计失败日志不得带 traceback 原文、raw answer、tool output、query 或异常消息。

- [x] 更新 service/API response 映射（AC: 6, 7, 8）
  - [x] 扩展 `AgentRunResult` 和 `AgentRunResponse`，使 validated final answer/citations 可安全返回；如果产品决定继续 status-only，必须在 README 和 completion notes 写明。
  - [x] `AgentRunApplicationService._result_metadata()` 只能写 validation safe metadata，不能把 final answer 文本持久化进 `agent_runs.metadata`。
  - [x] `apps/api/service_dependencies.py` 注入真实 validator，不在 route 中装配工具 handler 或 storage repository。
  - [x] `apps/api/routes/agent.py` 保持 thin，不导入 validator 实现、tool classes、repositories 或 SQLAlchemy。

- [x] 扩展测试（AC: 1-9）
  - [x] 新增 `tests/unit/agent/test_final_answer_validation.py`，覆盖 validator 的 valid、degraded、invalid、invented citation、failed tool reference、denied tool reference、cross-run evidence、safe metadata。
  - [x] 扩展 `tests/unit/agent/test_runtime.py`，覆盖 final answer 前会调用 validator、validator failure 会阻止 completed、validated citations 返回、raw answer 不进入 audit metadata。
  - [x] 扩展 `tests/unit/agent/test_agent_run_service.py`，覆盖 validation metadata 到 run response/status 的映射，不持久化 raw answer。
  - [x] 扩展 `tests/unit/agent/test_dto.py`，覆盖 final answer/citation DTO extra forbid、blank id、unsafe source/path/redaction。
  - [x] 扩展 `tests/unit/test_architecture_boundaries.py`，确认新增 validator 文件仍满足 storage/provider/framework neutral 边界。
  - [x] 增加 README expectation test，确认 `final answer validation` 不再出现在 Current Limits，或如果 status-only response 仍保留，文档明确说明。

- [x] 更新 README 和 sprint/story 状态（AC: 8）
  - [x] README Build Status 从 Story 6.6 更新到 Story 6.7 final answer validation。
  - [x] Governed Agent Tools 增加 final answer validation 行为和非目标。
  - [x] Auditability and Observability 增加 final_answer_validation audit。
  - [x] Current Limits 删除 final answer validation，保留 tool event streaming、Open WebUI function/tool bridge、real LLM-backed planning。
  - [x] 实现完成后将本 story 状态改为 `review`，code review 通过后再改 `done`。

- [x] 验证（AC: 1-9）
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit/agent tests/unit/test_architecture_boundaries.py`
  - [x] `.venv\Scripts\python.exe -m pytest tests/integration/api/test_agent_routes.py`
  - [x] `.venv\Scripts\python.exe -m pytest tests/unit`
  - [x] `.venv\Scripts\python.exe -m ruff check .`
  - [x] `.venv\Scripts\python.exe -m mypy apps packages tests`

### Review Findings

- [x] [Review][Patch] Citation identifiers are insufficiently constrained before being returned, persisted or audited [packages/agent/dto.py:178]
- [x] [Review][Patch] Final answers without structured citations can still make free-text source claims [packages/agent/final_answer.py:24]
- [x] [Review][Patch] Citation authorization ignores `tool_name` and does not enforce matching `observation_index` provenance [packages/agent/final_answer.py:91]
- [x] [Review][Patch] Final answer validator exceptions and blank answers can escape structured validation failure handling [packages/agent/runtime.py:333]
- [x] [Review][Patch] Final answer validation is not bounded by the agent runtime deadline [packages/agent/runtime.py:643]
- [x] [Review][Patch] `FinalAnswerValidationResult` allows completed valid/degraded outcomes with no answer [packages/agent/dto.py:273]
- [x] [Review][Patch] RAG citation extraction coerces malformed identifier values into citation evidence [packages/agent/runtime.py:752]
- [x] [Review][Patch] Citation page numbers allow zero or negative values [packages/agent/dto.py:185]

## Dev Notes

### Current Repository State

- Git baseline for this story context: `e4f737f fix(agent): address tool call audit review findings`.
- Worktree was clean before this story file was created.
- Sprint status auto-selected `6-7-agent-final-answer-validation` as the first backlog story.
- Epic 6.1 through 6.6 are done. Existing Agent foundation includes Tool Registry, `rag_search`、`calculator`、restricted `file_reader`、runtime limits、`/agent/run`、durable `agent_runs` 和 durable `tool_calls`。
- Current API assembly uses `DeterministicAgentStepper` that immediately returns `final_answer="Agent run accepted for governed execution."`; real LLM-backed planning is intentionally not implemented yet.
- Current `AgentRunResponse` is status-oriented and does not expose final answer text. Story 6.7 implementation must either add safe validated answer/citations to the response or document that validation runs but API remains status-only.

### Existing Patterns To Reuse

- `AgentRuntime.run()` is the central path for stepper decisions, tool execution, observations, runtime limits and final answer completion.
- `AgentRunApplicationService` already creates durable `agent_runs` before runtime and maps `AgentRunResult` into storage-safe metadata and response DTOs.
- `ToolRegistry.execute()` already records durable `tool_calls` for success, denied, validation, rate limit, timeout, handler failure and output validation paths.
- `rag_search` output already exposes safe citation identifiers: `document_id`、`version_id`、`chunk_id`、`source`、`source_uri`、`source_type`、`page_start`、`page_end`、`title_path`、`score`、`retrieval_method`、`summary`。
- Existing redaction/safe summary patterns are in `packages.agent.dto`, `packages.agent.registry`, `packages.agent.runtime`, `packages.agent.service`, `packages.common.logging.redact_sensitive_data()` and `packages.common.audit`.
- Existing audit pattern uses `AuditEvent` with request_id、trace_id、tenant_id、user_id、resource、status、latency_ms、error_code and safe metadata.
- Existing tests prefer fakes/in-memory ports and avoid real providers.

### Architecture Requirements

- This story spans Agent Domain/Application boundary and API dependency assembly. It should not add storage tables unless implementation needs a documented validation-history table; AC only requires audit and run response/status behavior.
- `packages/agent/*` core must stay provider-neutral and storage-neutral. SQLAlchemy stays under `packages/agent/storage/*` only.
- Validation must be backend policy, not prompt policy. Do not ask the model whether its own citation is authorized.
- Citation authorization evidence must come from this run's validated tool observations or existing authorized RAG result structures, not from final answer text.
- Do not retrieve cross-tenant data during final validation. The validator should inspect already authorized observation evidence, not query broad storage.
- Do not store raw final answer, prompt, hidden reasoning, raw tool output, raw arguments, query text, chunk text, file content, local paths, tokens or secrets in audit logs or `agent_runs.metadata`.
- Do not add real OpenAI/Qwen/DeepSeek/vLLM/Ollama planning in this story.
- Do not add Tool Registry event streaming, Open WebUI function/tool bridge, Planner-Executor or LangGraph-style graph runtime in this story.

### Current UPDATE File Notes

- `packages/agent/dto.py`: currently contains tool, tool call and agent run DTOs. Add final answer/citation/validation DTOs here only if the file stays readable; otherwise use `packages/agent/final_answer.py` and export as needed.
- `packages/agent/runtime.py`: current final answer branch immediately calls `_finish(... status=COMPLETED ...)`. This is the primary UPDATE file for invoking validator before completion.
- `packages/agent/service.py`: current `_result_metadata()` redacts metadata and `AgentRunResponse.from_record()` excludes final answer. Update carefully so raw answer is not persisted accidentally.
- `packages/agent/registry.py`: no major behavior change should be needed, but `_summarize_tool_result()` in runtime may need to understand registry result output safely.
- `packages/agent/tools/rag_search.py`: output schema already contains citation identifiers; reuse it as evidence shape. Do not weaken its tenant filter, permission checks or source sanitization.
- `apps/api/service_dependencies.py`: inject validator at runtime assembly. Keep `apps/api/routes/agent.py` unchanged except response model updates if DTO changes require it.
- `tests/unit/agent/test_runtime.py`: currently asserts final answer completes without tool calls and that observations do not leak raw tool output. Update expected behavior for validator while keeping leak checks.
- `tests/unit/agent/test_agent_run_service.py`: currently verifies run status mapping and metadata redaction. Add final answer validation mapping here.
- `tests/unit/test_architecture_boundaries.py`: extend for any new agent files.
- `README.md`: currently says final answer validation remains roadmap work. Update after implementation.

### Previous Story Intelligence

- Story 6.1: all tools must go through `ToolRegistry`; final answer validation must not introduce direct Python function/tool bypasses.
- Story 6.2: `rag_search` reuses retrieval AuthContext filtering and returns safe citation identifiers only. Final answer validation should treat these identifiers as the only initial RAG citation evidence for Agent.
- Story 6.3: `calculator` is deterministic but not a document source; `file_reader` returns bounded excerpts but should not become citation evidence in this story unless a separate source contract is designed.
- Story 6.4: runtime stops before max_steps/max_tool_calls/timeouts/repeated actions. Final answer validation must not create loops or additional model/tool calls.
- Story 6.5: `agent_runs` is created as running before runtime starts and updated after result. Validation failure must map cleanly into run result/status.
- Story 6.6: `tool_calls` are durable and safe-summarized. Final answer validation can rely on runtime observations and tool call statuses but must not read raw arguments/output from durable audit fields.

### Git Intelligence

- `cd47ee6 feat(agent): add agent run api persistence` added `/agent/run`, `agent_runs`, service lifecycle persistence and tests.
- `c6d2496 fix(agent): address agent run review findings` hardened run persistence before runtime, failed runtime writeback, durable service audit commit and metadata sanitization.
- `4b36bc1 feat(agent): add durable tool call persistence` added `tool_calls`, recorder wiring and registry execution persistence.
- `e4f737f fix(agent): address tool call audit review findings` fixed timeout durability, recorder failure safety, summary validation, created_at queries, README expectation coverage and fail-closed missing `agent_run_id`.

### Suggested Implementation Shape

The exact API can evolve, but preserve these boundaries:

```python
class AgentCitationRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    version_id: str
    chunk_id: str
    source: str | None = None
    page_start: int | None = None
    page_end: int | None = None
```

```python
class FinalAnswerValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["valid", "degraded", "invalid"]
    answer: str | None
    citations: tuple[AgentCitationRef, ...] = ()
    latency_ms: float
    error_code: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
```

```python
class FinalAnswerValidator(Protocol):
    async def validate(
        self,
        *,
        context: AuthenticatedRequestContext,
        agent_run_id: str | None,
        answer: str,
        citations: tuple[AgentCitationRef, ...],
        observations: tuple[AgentObservationSummary, ...],
    ) -> FinalAnswerValidationResult:
        ...
```

Runtime final answer path should be:

```text
stepper final answer decision
 -> backend final answer validator
 -> validation audit
 -> completed / degraded / failed runtime result
 -> service updates agent_runs with safe validation metadata
 -> API returns only validated answer/citations if response contract includes them
```

### Implementation Boundaries

- Do not parse or trust citation IDs invented in free text as authorization evidence.
- Do not query all documents to "verify" an invented citation; validation evidence must be scoped to this run's authorized observations.
- Do not allow final answer validation to call tools or LLM providers.
- Do not change retrieval ACL behavior, `rag_search` tenant filtering, Tool Registry permissions, rate limits or timeout semantics.
- Do not persist raw final answer unless a dedicated storage requirement is added later with redaction and retention rules.
- Do not use `tool_calls.arguments_summary` or `result_summary` as a source of raw citation detail; they are deliberately lossy safe summaries.

### Latest Technical Information

- No dependency upgrade is required for Story 6.7. Use the repository-pinned FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic, pytest, ruff and mypy stack.
- Use Pydantic v2 `BaseModel`, `ConfigDict(extra="forbid", frozen=True)`, field validators and Protocol-style ports consistent with the current codebase.
- Use existing local fake/in-memory test patterns. Do not add network calls or real provider calls.

### References

- `_bmad-output/planning-artifacts/epics.md#Story-6.7-Agent-Final-Answer-Validation`
- `_bmad-output/planning-artifacts/epics.md#Epic-6-受控-Agent-工具执行`
- `_bmad-output/planning-artifacts/epics.md#FR16`
- `_bmad-output/planning-artifacts/epics.md#FR22`
- `_bmad-output/planning-artifacts/epics.md#FR27`
- `_bmad-output/planning-artifacts/epics.md#FR28`
- `_bmad-output/planning-artifacts/architecture.md#Tool-Security`
- `_bmad-output/planning-artifacts/architecture.md#Data-Flow`
- `_bmad-output/planning-artifacts/architecture.md#Requirements-to-Structure-Mapping`
- `project-context.md#12-Agent-规则`
- `_bmad-output/implementation-artifacts/6-1-tool-registry-与工具治理模型.md`
- `_bmad-output/implementation-artifacts/6-2-rag-search-工具.md`
- `_bmad-output/implementation-artifacts/6-3-calculator-与受限-file-reader-工具.md`
- `_bmad-output/implementation-artifacts/6-4-react-agent-runtime-限制与重复动作检测.md`
- `_bmad-output/implementation-artifacts/6-5-agent-run-api-与-agent-run-persistence.md`
- `_bmad-output/implementation-artifacts/6-6-tool-call-audit-persistence.md`
- `packages/agent/dto.py`
- `packages/agent/exceptions.py`
- `packages/agent/registry.py`
- `packages/agent/runtime.py`
- `packages/agent/service.py`
- `packages/agent/tools/rag_search.py`
- `packages/agent/tools/calculator.py`
- `packages/agent/tools/file_reader.py`
- `packages/agent/storage/models.py`
- `packages/agent/storage/repositories.py`
- `apps/api/service_dependencies.py`
- `apps/api/routes/agent.py`
- `tests/unit/agent/test_runtime.py`
- `tests/unit/agent/test_agent_run_service.py`
- `tests/unit/agent/test_tool_registry.py`
- `tests/unit/agent/test_dto.py`
- `tests/unit/test_architecture_boundaries.py`
- `README.md#Governed-Agent-Tools`
- `README.md#Current-Limits`

## Validation Checklist

Validation Result: PASS（2026-06-08T16:23:15+08:00）

- [x] Story 明确只实现 Agent final answer validation，不实现 real LLM-backed planning、tool event streaming、Open WebUI function/tool bridge 或 LangGraph workflow。
- [x] Acceptance Criteria 覆盖 backend validation、授权 citation evidence、失败工具处理、结构化 outcome、audit、run/response 映射、架构边界、README 和测试。
- [x] Tasks 给出 DTO/port、observation evidence、runtime validator 注入、citation input contract、audit、service/API response、tests、README 和验证命令。
- [x] Dev Notes 明确当前 code state、UPDATE files、前序 story lessons、现有 patterns、风险点和非目标。
- [x] 明确测试默认不调用真实 LLM、embedding、vector store、PostgreSQL、Redis、MinIO、Open WebUI、网络或外部 provider。
- [x] 明确 audit/run metadata 不保存 raw final answer、prompt、hidden reasoning、raw tool output、raw arguments、chunk text、file content、query 原文、token、secret、绝对路径或企业机密全文。

## Change Log

- 2026-06-08: Created comprehensive Story 6.7 developer context for Agent final answer validation.
- 2026-06-08: Implemented backend final answer validation, validated response mapping, audit event, tests, and README updates.
- 2026-06-08: Addressed code review findings for citation safety, validation failure handling, deadline enforcement, and malformed evidence handling.

## Dev Agent Record

### Agent Model Used

GPT-5 Codex

### Debug Log References

- 2026-06-08T16:44:12+08:00: Ran Story 6.7 validation commands successfully:
  - `.venv\Scripts\python.exe -m pytest tests/unit/agent tests/unit/test_architecture_boundaries.py -q` → 168 passed
  - `.venv\Scripts\python.exe -m pytest tests/integration/api/test_agent_routes.py -q` → 4 passed
  - `.venv\Scripts\python.exe -m pytest tests/unit -q` → 681 passed
  - `.venv\Scripts\python.exe -m ruff check .` → passed
  - `.venv\Scripts\python.exe -m mypy apps packages tests` → passed

### Completion Notes List

- Added storage/provider-neutral final answer citation and validation DTOs with stable validation statuses and error codes.
- Added `StrictFinalAnswerValidator` to authorize final citations only from successful same-run `rag_search` observation evidence and reject failed/denied/structured-error tool references.
- Extended runtime final-answer path to run backend validation before completion, return validated answers/citations, fail closed on invalid validation, and emit `agent.final_answer_validation` audit events.
- Extended service/API response mapping so validated final answers can be returned without persisting raw answer text into `agent_runs.metadata`.
- Updated README to mark Story 6.7 complete and keep tool event streaming, Open WebUI tool bridge, and real LLM-backed planning as current limits.
- Addressed review findings by tightening citation identifier/page validation, binding citation support to successful `rag_search` evidence, handling validator exceptions/blank answers/timeouts structurally, and rejecting malformed RAG citation identifiers.

### File List

- README.md
- apps/api/service_dependencies.py
- packages/agent/__init__.py
- packages/agent/dto.py
- packages/agent/final_answer.py
- packages/agent/runtime.py
- packages/agent/service.py
- tests/unit/agent/test_agent_run_service.py
- tests/unit/agent/test_dto.py
- tests/unit/agent/test_final_answer_validation.py
- tests/unit/agent/test_runtime.py
- tests/unit/test_readme_expectations.py

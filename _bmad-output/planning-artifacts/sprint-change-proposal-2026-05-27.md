---
workflow: bmad-correct-course
project: 本地化多源知识增强 RAG + Agent 问答系统
date: 2026-05-27
mode: batch
status: applied-to-planning-documents
trigger_source: _bmad-output/planning-artifacts/implementation-readiness-report-2026-05-27.md
scope_classification: moderate
updated_artifacts:
  - PRD.md
  - _bmad-output/planning-artifacts/prds/prd-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/PRD.md
  - _bmad-output/planning-artifacts/architecture.md
  - _bmad-output/planning-artifacts/epics.md
  - _bmad-output/planning-artifacts/ux-designs/ux-本地化多源知识增强 RAG + Agent 问答系统-2026-05-26/EXPERIENCE.md
---

# Sprint Change Proposal: Planning Document Corrections

## 1. Issue Summary

Initial implementation readiness assessment on 2026-05-27 returned `NEEDS WORK`. Subsequent remediation updated the readiness report to `READY FOR SPRINT PLANNING`; this proposal remains as historical context for the earlier planning corrections. The PRD and Architecture were strong, but the planning set had eight actionable issues before broad implementation:

1. Source Inspector lacked an explicit authorized source-detail API contract.
2. `epics.md` still stated that no UX document was included, while UX artifacts exist.
3. UX accessibility requirements were not attached to PRD, Architecture, or story acceptance criteria.
4. Eval work was structurally later than the PRD and Architecture require.
5. Story 2.4 combined cleaner, dedup, chunker, metadata, checksum, and tests.
6. Story 6.5 combined Agent API, persistence, tool audit, and final validation.
7. Open WebUI first integration path remained ambiguous.
8. Story 5.1 allowed placeholder eval cases to satisfy the story.

No critical blockers or PRD coverage gaps were found. This is a planning correction, not a product pivot.

## 2. Impact Analysis

### Epic Impact

- Epic 2 is refined by splitting the oversized ingestion/chunking story into cleaner/dedup, FixedSizeChunker, and chunk metadata persistence.
- Epic 3 now owns initial retrieval eval fixtures and smoke runner so eval starts with Hybrid Retrieval.
- Epic 4 now owns `POST /sources/resolve`, Source Inspector authorization behavior, Open WebUI chat adapter contract, and accessibility acceptance criteria.
- Epic 5 remains responsible for broader RAG eval, but Story 5.1 now requires executable synthetic cases.
- Epic 6 is refined by splitting Agent run persistence, tool call audit persistence, and final answer validation.

### Artifact Conflicts Resolved

- PRD API list now includes `POST /sources/resolve`.
- Architecture canonical endpoints now include `POST /sources/resolve`.
- UX Experience now fixes the Open WebUI path as OpenAI-compatible chat adapter backed by `/chat`.
- `epics.md` now references the UX artifacts and applies UX requirements.
- FR notation normalization is documented for trace tooling.

### Technical Impact

- Adds a read-only source detail service boundary with AuthContext, tenant, RBAC, ACL, soft-delete, and version visibility checks.
- Pulls retrieval eval into the retrieval implementation sequence.
- Makes migrations explicit for documents, chunks, embedding_jobs, retrieval_logs, chat memory, agent_runs, and tool_calls.
- Reduces story blast radius before implementation begins.

## 3. Recommended Approach

Recommended path: Direct Adjustment.

Rationale:

- The readiness report found no critical requirement gap and no invalid epic.
- Existing PRD and Architecture direction remains valid.
- The issues are resolvable through targeted edits to API contract, story sequencing, story granularity, eval criteria, and UX traceability.

Effort estimate: Medium.

Risk level: Low to Medium.

Timeline impact: Small planning impact now; lower implementation risk later because large stories and ambiguous integration decisions are removed before sprint execution.

## 4. Detailed Change Proposals

### PRD

Change: Add authorized source detail endpoint.

OLD:

```text
系统提供 POST /upload、POST /retrieve、POST /query、POST /chat、POST /agent/run。
```

NEW:

```text
系统提供 POST /upload、POST /retrieve、POST /query、POST /chat、POST /sources/resolve、POST /agent/run。
```

Rationale: Source Inspector needs a backend authorization contract; citation metadata alone is not enough.

Change: Resolve Open WebUI integration ambiguity.

OLD:

```text
MVP 支持 Open WebUI 或最小自定义前端接入。
```

NEW:

```text
MVP 首选通过 Open WebUI 兼容 chat adapter 接入，由后端 /chat 承载 RAG、citation、SSE 和权限治理。
```

Rationale: Prevents frontend work from branching before backend RAG contracts are complete.

Change: Strengthen eval acceptance.

OLD:

```text
Phase 2 至少包含 20 条 eval query。
```

NEW:

```text
Phase 2 至少包含 20 条可执行 synthetic eval query；占位样例不能满足 smoke gate。
```

Rationale: Eval must prove retrieval, citation, no-answer, ACL, and prompt-injection behavior.

### Architecture

Change: Add `POST /sources/resolve` to required endpoints and define an Authorized Source Detail Contract.

Rationale: Ensures source drilldown rechecks AuthContext and never leaks whether unauthorized resources exist.

Change: Fix first Open WebUI integration mode as OpenAI-compatible chat adapter backed by `/chat`.

Rationale: Keeps Open WebUI as an entry shell while backend remains the governance boundary.

Change: Add accessibility contract for any custom Source Inspector, Knowledge Admin, Diagnostics, Eval Reports, or Agent Review UI.

Rationale: UX accessibility requirements now have architecture-level enforcement.

### Epics and Stories

Change: Replace stale UX statement.

OLD:

```text
No UX Design document was included in this workflow run.
```

NEW:

```text
UX artifacts are included in the planning set: DESIGN.md and EXPERIENCE.md.
```

Rationale: Fixes artifact discovery contradiction.

Change: Split Story 2.4 into:

- Story 2.4: Cleaner 与 Dedup
- Story 2.5: FixedSizeChunker
- Story 2.6: Chunk Metadata Contract 与持久化

Rationale: Reduces implementation and review scope for ingestion/chunking.

Change: Add Story 3.7 Retrieval Eval Fixtures 与 Smoke Runner.

Rationale: Moves eval feedback into Hybrid Retrieval instead of waiting until after RAG answering.

Change: Update Story 4.7 to cover Open WebUI Chat Adapter, Source Detail, and accessibility criteria.

Rationale: Removes integration ambiguity and binds Source Inspector to a backend authorization endpoint.

Change: Strengthen Story 5.1 to require executable synthetic eval cases.

Rationale: Prevents placeholder eval data from satisfying quality gates.

Change: Split Story 6.5 into:

- Story 6.5: `/agent/run` API 与 Agent Run Persistence
- Story 6.6: Tool Call Audit Persistence
- Story 6.7: Agent Final Answer Validation

Rationale: Separates API orchestration, audit persistence, and final safety validation.

## 5. Checklist Status

| Checklist Item | Status | Notes |
| --- | --- | --- |
| 1.1 Triggering story identified | N/A | Trigger is readiness report, not an implementation story. |
| 1.2 Core problem defined | Done | Planning package needed targeted corrections before implementation. |
| 1.3 Evidence gathered | Done | Evidence from implementation-readiness report. |
| 2.1 Current epic impact | Done | Epic 2, 3, 4, 5, 6 affected. |
| 2.2 Epic-level changes | Done | Story split, new retrieval eval story, source detail story scope. |
| 2.3 Remaining epics reviewed | Done | No epic invalidated. |
| 2.4 New/obsolete epics | Done | No new epic needed. |
| 2.5 Priority/order changes | Done | Eval starts in Epic 3. |
| 3.1 PRD conflicts | Done | Source detail, Open WebUI path, eval criteria updated. |
| 3.2 Architecture conflicts | Done | Endpoint, source contract, frontend/accessibility updated. |
| 3.3 UX conflicts | Done | UX artifacts referenced; EXPERIENCE decisions updated. |
| 3.4 Other artifacts | Done | Root PRD mirror updated; no sprint-status file exists. |
| 4.1 Direct adjustment | Viable | Recommended. |
| 4.2 Rollback | Not viable | No implementation to roll back. |
| 4.3 MVP review | Not viable | MVP remains valid. |
| 4.4 Recommended path | Done | Direct Adjustment. |
| 5.1 Issue summary | Done | Included above. |
| 5.2 Impact and adjustments | Done | Included above. |
| 5.3 Rationale | Done | Included above. |
| 5.4 MVP impact | Done | MVP clarified, not reduced. |
| 5.5 Handoff plan | Done | Moderate scope: PO/DEV coordination. |
| 6.1 Completion review | Done | Applicable items addressed. |
| 6.2 Proposal accuracy | Done | Consistent with applied document changes. |
| 6.3 User approval | Action-needed | Planning changes applied per request; implementation should still wait for explicit approval. |
| 6.4 sprint-status update | N/A | No sprint-status file exists in the project. |
| 6.5 Next steps | Done | Run sprint planning or create first story after review. |

## 6. Implementation Handoff

Scope classification: Moderate.

Recommended routing:

- Product Owner / planning owner: review updated `epics.md` story sequence and story count.
- Developer agent: start only after updated stories are accepted.
- Architect: use updated Architecture as source for `/sources/resolve`, Open WebUI adapter, and accessibility contract.

Success criteria:

1. No planning doc contains the stale no-UX statement.
2. `POST /sources/resolve` appears in PRD, Architecture, and Story 4.7.
3. Retrieval eval begins in Epic 3.
4. Story 5.1 cannot pass with placeholders only.
5. Agent API, tool audit, and final validation are separate stories.
6. No sprint-status update is required until sprint planning creates the status file.

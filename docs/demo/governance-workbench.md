# Governance Workbench

The governance workbench is a same-origin static surface for explaining the
security evidence already produced by AegisRAG. It includes backend-backed
Document Review, Source Evidence, Retrieval Diagnostics, Eval Evidence, Audit
Explorer safe audit search/export, and Review Queue safe evidence feedback,
while staying a static, no-build frontend served by FastAPI, not a custom admin
console.

## Open the Workbench

Start the API, then open:

```text
http://127.0.0.1:8000/governance
```

The route serves the same static asset bundle as the sidecar:

```text
GET /governance
GET /sidecar
GET /sidecar/assets/sidecar.css
GET /sidecar/assets/sidecar.js
```

`/governance` uses a governance-first HTML entry. `/sidecar` remains the
Source Inspector-first entry for existing bookmarks and demos.

## Views

The shell exposes six stable entries:

- Document Review
- Source Evidence
- Retrieval Diagnostics
- Eval Evidence
- Audit Explorer
- Review Queue

Document Review calls backend review endpoints for tenant-scoped document
lists, version detail, and lifecycle timelines. Source Evidence accepts
citation JSON, Open WebUI metadata, sidecar links, or manual identifiers, then
resolves each reference through `POST /sources/resolve` before showing any
excerpt or source details. Retrieval Diagnostics accepts request ID or trace ID,
calls `POST /diagnostics/resolve`, and renders a backend-confirmed safe
timeline for permission, dense retrieval, sparse retrieval, RRF merge, rerank,
context packing, generation, citation, and infrastructure stages. Eval Evidence
calls backend eval report APIs to browse synthetic-safe report summaries, failed
case evidence, gate metrics, and verification commands. Audit Explorer calls
backend audit APIs for tenant-scoped safe summaries and JSON export. Review
Queue calls backend review APIs for safe item creation, tenant-scoped lists,
backend-validated status transitions, audit logging, and eval candidate
previews that require human confirmation.

## Document Review

Document Review supports:

- `GET /documents/review` for a bounded tenant-scoped document list with optional
  lifecycle status filter, limit, and cursor.
- `GET /documents/{document_id}/review` for latest-version review detail.
- `GET /documents/{document_id}/versions/{version_id}/review` for an explicit
  version detail and lifecycle timeline.

The board renders only allowlisted fields: document/version IDs, safe
`source_display_name`, source type, lifecycle status, creator/timestamps,
chunk count, embedding/index summary, job attempt/retry metadata, request ID,
trace ID, and safe error summary. It does not render raw storage locators,
source URI, object keys, ACL documents, full text, chunks, prompts, SQL,
vectors, embeddings, provider payloads, tokens, secrets, or raw exceptions.

Lifecycle stages are provided by backend DTOs and tested frontend mappings.
Unknown backend statuses are shown as unknown/safe and are not treated as
working states. Safe failures clear stale list, detail, and timeline content
before rendering request ID, trace ID, failure stage, error code, and a next
step.

## Source Evidence

Source Evidence supports evidence-set review for one answer at a time. Inputs
can be a single citation JSON object, an array of citation objects, Open
WebUI-style metadata containing `citations` or `evidence_links`, a direct
`evidence_url`, a sidecar/source evidence link, or manual
document/version/chunk/page/request identifiers.

The parser treats pasted content as untrusted. It keeps only document ID,
version ID, chunk ID, optional page range, request ID, and citation reference.
It ignores pasted trace IDs as lookup inputs; response trace IDs are displayed
only after backend confirmation. It also ignores pasted excerpts, source display
names, retrieval method, score, answer text, storage locators, object keys,
token-like values, tenant/user/role/permission fields, ACL claims, and
authorization-like claims. A batch is limited to 20 unique references, matching
the citation extraction default.

Each reference is resolved independently through:

```text
POST /sources/resolve
```

The request body contains only the source resolve contract fields:
`document_id`, `version_id`, `chunk_id`, optional `page_start` and `page_end`,
optional `request_id`, and optional `citation_ref`. The backend remains
authoritative for tenant, RBAC, ACL, document/version visibility, chunk status,
page identity, excerpt truncation, source metadata, score, retrieval method,
request ID, and trace ID.

Authorized evidence items render only backend-confirmed safe fields:
authorization status, `source_display_name`, source type, document/version/chunk
IDs, page range, `title_path`, authorized excerpt, excerpt character count,
token count, retrieval method, score, request ID, trace ID, and explicitly safe
resolver metadata. Denied, not found, deleted, inactive, page-mismatched,
cross-tenant, and ACL-blocked references use the same safe failure shape. A
failed item clears stale excerpt, source metadata, score, and retrieval method
without affecting other authorized items in the same set.

Copy actions are allowlisted. Per-item copy focuses on identifiers, and the
batch summary includes safe identifiers, status, source display names, page
range, retrieval method, score, request ID, and trace ID. It does not copy raw
storage locators, full excerpt sets, prompts, answers, chunk text, provider
payloads, tokens, secrets, or raw errors.

## Retrieval Diagnostics

Retrieval Diagnostics supports lookup by `request_id`, `trace_id`, or both. The
form sends only those identifiers plus `include_report`; it never sends
`tenant_id`, `user_id`, permissions, metadata filters, SQL, query text, or
frontend-derived authorization state. Backend `AuthenticatedRequestContext`
remains authoritative for tenant and user scope, and `audit:read` or
`diagnostics:read` is required.

Successful responses render allowlisted summary fields and stable stage rows:
permission/auth scope, dense retrieval top-k/result counts, BM25/sparse top-k,
RRF input/dedup/filter counts, threshold decision, rerank status/counts,
context packing counts, generation token/event counts, citation counts,
latency, status, failure stage, and error code. Missing metadata renders as
`not_available`; the frontend does not infer threshold decisions or failure
stages from pasted IDs.

Failures clear stale summary, timeline, next-step commands, and report
copy/export state before showing only safe request ID, trace ID, failure stage,
error code, and a safe next step. Report copy/download uses the backend safe DTO
plus a client allowlist, and filenames are sanitized from request or trace IDs.

Retrieval Diagnostics is not a complete trace viewer. It does not render raw
queries, answers, chunk content, chunk ID candidate lists, prompts, SQL,
vectors, embeddings, provider payloads, tokens, secrets, source URI/object key
locators, local paths, raw exceptions, or OpenTelemetry/Grafana dashboard data.

## Eval Evidence

Eval Evidence supports authorized browsing of already generated synthetic-safe
reports under the configured eval report directory, defaulting to
`tests/eval/reports`. It calls:

```text
GET /eval/reports
GET /eval/reports/{report_filename}
```

The frontend sends only a bounded list limit or a backend/report-list filename.
It never sends tenant ID, user ID, roles, permissions, dataset paths, report
directories, local paths, threshold overrides, or frontend-derived authorization
state. Backend `AuthenticatedRequestContext` remains authoritative and requires
`eval:read` or `audit:read`.

Report list summaries render only allowlisted fields: report filename,
generated time, report type, dataset version/name, case counts, pass/fail
counts, retrieval hit rate, citation coverage, no-answer correctness, ACL and
prompt-injection status, average latency, gate decision/status, failed metric
names, and failure stages. Report detail renders only allowlisted failed case
evidence: case ID, failure stage, matched document/chunk/citation IDs,
retrieval/context/citation/unsupported/forged-reference/prompt-risk counts,
request ID, trace ID, top_k, latency, and safe generation provider/model/version
token usage summary. CI gate details render metric name, threshold name,
pass/fail text, expected value, and actual value.

Failures clear stale report lists, summaries, case rows, next-step commands,
and copy/download state before showing only safe request ID, trace ID, failure
stage, error code, and a safe next step. Copy/download exports use the same
client allowlists and sanitized report filenames.

Eval Evidence is not a static JSON browser, dashboard replacement, eval runner,
LLM-as-judge UI, trend warehouse, threshold editor, or review queue. It does not
render raw dataset queries, expected answer terms, generated answers, corpus
content, prompts, SQL, vectors, embeddings, provider payloads, source URI/object
key locators, tokens, secrets, local paths, or raw exception text.

## Review Queue

Review Queue supports safe feedback capture through:

```text
POST /review/items
GET /review/items
GET /review/items/{item_id}
POST /review/items/{item_id}/status
POST /review/items/{item_id}/eval-candidate
```

Create requests can submit only `item_type`, `severity`, `source_view`,
request/trace IDs, safe identifiers, and safe summary fields. The frontend does
not submit `tenant_id`, `created_by`, user IDs, roles, permissions, raw prompts,
queries, answers, chunks, metadata key/value filters, local filenames, dataset
paths, SQL, source URI, object keys, tokens, or secrets. Backend
`AuthenticatedRequestContext` remains authoritative for tenant, user, and
permissions.

List/detail responses render only allowlisted fields: review item ID, type,
severity, status, request/trace IDs, source view, safe identifiers, safe
summary, status history summary, allowed transitions, timestamps, and optional
eval candidate preview. Reads require `review:read`; creation and status
updates require `review:write`; eval candidate preview generation requires
`review:write` plus `eval:write`.

Status buttons are rendered from backend `allowed_transitions`. The frontend
does not decide authorization or legal transitions. Each create/update/convert
operation writes an audit event with review item ID, type, severity, old/new
status, source view, safe identifier count, request/trace IDs, and candidate ID
when applicable.

Eval candidate preview is intentionally not a dataset writer. It returns a
synthetic-safe payload with `candidate_id`, source review item ID, case type,
safe identifiers, failure stage, safe metric counts, expected behavior summary,
request/trace IDs, and `requires_human_confirmation=true`. It does not append
to `tests/eval/datasets/*.json`, collect real enterprise data automatically, or
promote a candidate without human review.

Failures, 403/404 responses, malformed responses, and overlapping requests
clear stale list, detail, status history, candidate preview, next-step,
copy/export state, and selected data before showing only safe request ID, trace
ID, failure stage, error code, review item ID, and a next step.

## Audit Explorer

Audit Explorer supports authorized lookup of audit summaries through:

```text
GET /audit/logs
POST /audit/export
```

The list form can filter by user ID, request ID, trace ID, action,
resource type, resource ID, status, created-at window, and bounded limit. It
never sends tenant ID, roles, permissions, database paths, raw SQL, or metadata
key/value filters. Backend `AuthenticatedRequestContext.auth.tenant_id` remains
authoritative, and `audit:read` is required.

Successful responses render only allowlisted summary fields: audit ID,
tenant/user/request/trace IDs, action, resource type/ID, status, latency,
error code, created time, safe summary counts/labels, and backend-extracted
associations. Agent/tool/final-validation associations can include agent run
ID, tool call ID, tool name, permission, status, error code, latency, safe
argument/result summaries, steps/tool-call counts, and validation counts when
the backend can safely map them from audit metadata, `tool_calls`, or
`agent_runs`.

The frontend does not join tables or infer relationships from rendered rows.
It does not render raw queries, answers, prompts, document/chunk content,
authorized excerpts, source URI/object keys, local paths, SQL, vectors,
embeddings, provider payloads, tool input/output text, Agent observations,
tokens, secrets, or raw exceptions.

Export is a backend action. `POST /audit/export` returns a JSON payload with
`export_id`, `generated_at`, filter summary, allowlisted fields, item count,
request IDs, trace IDs, and safe items. The frontend copy/download buttons use
only this backend export payload and sanitize filenames from `export_id`; they
do not serialize the raw API envelope, DOM rows, raw audit metadata, or cached
list response. The export API writes its own `audit_explorer.export` event with
filter summary, item count, export fields, format, and safe status only.

Permission denial, malformed responses, network errors, and empty results clear
stale list, association/detail, next-step, copy, and download state before
showing a uniform safe failure or empty state. Denied and not-found paths do
not reveal table structure, report directories, raw SQL, other tenants/users,
or whether a target record exists.

## Security Boundary

The workbench is not an authorization boundary. Open WebUI and the workbench
are presentation surfaces only. Backend AuthContext, RBAC, ACL, source resolve,
diagnostics, audit, and future eval/review APIs remain authoritative.

The workbench can reuse:

- `POST /sources/resolve` for Source Evidence
- `GET /documents/review` and document review detail endpoints for Document Review
- `GET /documents/{document_id}/versions/{version_id}/status` for Job Status
- `POST /diagnostics/resolve` for Retrieval Diagnostics
- `GET /eval/reports` and `GET /eval/reports/{report_filename}` for Eval Evidence
- `GET /audit/logs` and `POST /audit/export` for Audit Explorer
- `POST /review/items`, `GET /review/items`, review detail/status, and eval
  candidate preview endpoints for Review Queue

Renderable fields are allowlisted. Safe fields include tenant/user/request/trace
IDs, document/version/chunk IDs, page bounds, status, failure stage, error code,
counts, latency, action/resource IDs, agent run IDs, tool call IDs, review item
IDs, and eval candidate IDs. The shell must not render raw source locators,
object keys, local paths, full queries, answers, prompts, chunk content, SQL,
vectors, embeddings, provider payloads, tool observations, tokens, secrets, or
raw exception text.

Safe failures clear stale panel content and diagnostics report state before
rendering only request ID, trace ID, failure stage, and error code. Denied,
missing, cross-tenant, and unavailable records must not reveal whether a target
resource exists.

## Local/Test Auth

The existing collapsible local/test auth helper is reused. It can send a JWT
bearer value or development auth headers, but the page does not save auth values
to localStorage, sessionStorage, cookies, URLs, reports, or downloads.

Development headers are valid only when explicitly enabled:

```powershell
$env:APP_ENV = "local"
$env:ENABLE_DEV_AUTH_HEADERS = "true"
```

## Focused Verification

```powershell
.venv\Scripts\python.exe -m pytest tests/integration/api/test_governance_routes.py -q
.venv\Scripts\python.exe -m pytest tests/integration/api/test_sidecar_routes.py -q
.venv\Scripts\python.exe -m pytest tests/unit/data/test_document_lifecycle_service.py tests/integration/api/test_document_routes.py -q
.venv\Scripts\python.exe -m pytest tests/integration/storage/test_document_repositories.py -q
.venv\Scripts\python.exe -m pytest tests/unit/web/test_governance_static_contract.py -q
.venv\Scripts\python.exe -m pytest tests/unit/web/test_sidecar_static_contract.py -q
.venv\Scripts\python.exe -m pytest tests/integration/api/test_sources_routes.py tests/integration/api/test_document_routes.py tests/integration/api/test_diagnostics_routes.py -q
.venv\Scripts\python.exe -m pytest tests/unit/diagnostics tests/integration/storage/test_retrieval_log_repositories.py -q
.venv\Scripts\python.exe -m pytest tests/unit/eval_evidence tests/integration/api/test_eval_evidence_routes.py -q
.venv\Scripts\python.exe -m pytest tests/unit/audit_explorer tests/integration/api/test_audit_explorer_routes.py tests/integration/storage/test_audit_log_repositories.py -q
.venv\Scripts\python.exe -m pytest tests/unit/review_queue tests/integration/api/test_review_queue_routes.py tests/integration/storage/test_review_queue_repositories.py -q
node tests/unit/web/sidecar_behavior_runner.js
.venv\Scripts\python.exe -m pytest tests/unit/rag/test_source_resolver.py tests/unit/rag/test_source_metadata.py tests/unit/rag/test_citation_extractor.py -q
.venv\Scripts\python.exe -m pytest tests/unit/test_readme_expectations.py -q
```

The workbench intentionally does not require React, Next.js, Vite, Node build
pipelines, browser automation, Docker, PostgreSQL, Redis, MinIO, Open WebUI, or
real LLM/embedding providers for these checks.

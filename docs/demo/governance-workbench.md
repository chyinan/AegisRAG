# Governance Workbench

The governance workbench is a same-origin static surface for explaining the
security evidence already produced by AegisRAG. Story 8.2 adds a backend-backed
Document Review board for document/version lifecycle review. It is still a
static, no-build frontend served by FastAPI, not a custom admin console.

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

Document Review now calls backend review endpoints for tenant-scoped document
lists, version detail, and lifecycle timelines. Source Evidence and Retrieval
Diagnostics continue to reuse the existing backend-backed sidecar flows. Eval
Evidence, Audit Explorer, and Review Queue remain safe contract placeholders
until their backend APIs and persistence are implemented.

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

## Security Boundary

The workbench is not an authorization boundary. Open WebUI and the workbench
are presentation surfaces only. Backend AuthContext, RBAC, ACL, source resolve,
diagnostics, audit, and future eval/review APIs remain authoritative.

The workbench can reuse:

- `POST /sources/resolve` for Source Evidence
- `GET /documents/review` and document review detail endpoints for Document Review
- `GET /documents/{document_id}/versions/{version_id}/status` for Job Status
- `POST /diagnostics/resolve` for Retrieval Diagnostics

Eval Evidence, Audit Explorer, and Review Queue show contract placeholders until
their backend APIs and persistence are implemented.

Renderable fields are allowlisted. Safe fields include tenant/user/request/trace
IDs, document/version/chunk IDs, page bounds, status, failure stage, error code,
counts, latency, action/resource IDs, agent run IDs, and tool call IDs. The shell
must not render raw source locators, object keys, local paths, full queries,
answers, prompts, chunk content, SQL, vectors, embeddings, provider payloads,
tokens, secrets, or raw exception text.

Safe failures clear stale panel content before rendering only request ID, trace
ID, failure stage, and error code. Denied, missing, cross-tenant, and unavailable
records must not reveal whether a target resource exists.

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
.venv\Scripts\python.exe -m pytest tests/unit/test_readme_expectations.py -q
```

The workbench intentionally does not require React, Next.js, Vite, Node build
pipelines, browser automation, Docker, PostgreSQL, Redis, MinIO, Open WebUI, or
real LLM/embedding providers for these checks.

# Governance Workbench

Story 8.1 adds a same-origin governance workbench shell for explaining the
security evidence already produced by AegisRAG. It is a static, no-build
frontend served by FastAPI. It is not a custom admin console.

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

## Views

The shell exposes six stable entries:

- Document Review
- Source Evidence
- Retrieval Diagnostics
- Eval Evidence
- Audit Explorer
- Review Queue

Story 8.1 only provides navigation, empty/safe placeholders, allowlisted field
contracts, failure clearing, local/test auth helper reuse, and responsive
accessibility behavior. Later stories own real document review lists, eval
evidence APIs, audit search/export, and review queue persistence.

## Security Boundary

The workbench is not an authorization boundary. Open WebUI and the workbench
are presentation surfaces only. Backend AuthContext, RBAC, ACL, source resolve,
diagnostics, audit, and future eval/review APIs remain authoritative.

The workbench can reuse:

- `POST /sources/resolve` for Source Evidence
- `GET /documents/{document_id}/versions/{version_id}/status` for Document Review
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
.venv\Scripts\python.exe -m pytest tests/unit/web/test_sidecar_static_contract.py -q
.venv\Scripts\python.exe -m pytest tests/integration/api/test_sources_routes.py tests/integration/api/test_document_routes.py tests/integration/api/test_diagnostics_routes.py -q
.venv\Scripts\python.exe -m pytest tests/unit/test_readme_expectations.py -q
```

The workbench intentionally does not require React, Next.js, Vite, Node build
pipelines, browser automation, Docker, PostgreSQL, Redis, MinIO, Open WebUI, or
real LLM/embedding providers for these checks.

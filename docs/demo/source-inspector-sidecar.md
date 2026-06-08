# Source Inspector Sidecar

Story 7.5 adds a lightweight same-origin sidecar for source drilldown, document
version status, and request-driven diagnostics links. It is intentionally a
small static page served by FastAPI, not a custom admin console.

## Open the Sidecar

Start the API, then open:

```text
http://127.0.0.1:8000/sidecar
```

The API serves the static shell and assets:

```text
GET /sidecar
GET /sidecar/assets/sidecar.css
GET /sidecar/assets/sidecar.js
```

## Source Inspector

Use citation identifiers copied from Open WebUI metadata, walkthrough reports,
or a backend response. The sidecar accepts only:

```text
document_id
version_id
chunk_id
page_start
page_end
request_id
citation_ref
```

The page calls:

```text
POST /sources/resolve
```

Successful responses render only backend-confirmed safe fields such as
`source_display_name`, document/version/chunk IDs, page range, `title_path`,
authorized `text_excerpt`, retrieval method, score, request ID, and trace ID.
Denied, missing, deleted, invisible, or ACL-blocked references show the same
safe failure state and do not reveal whether the resource exists.

## Job Status

Knowledge admins can enter document and version identifiers. The sidecar calls:

```text
GET /documents/{document_id}/versions/{version_id}/status
```

The status view displays safe lifecycle fields: status, chunk/vector counts,
embedding provider/model/version/dim, index status, job ID, retry counters,
safe error summary, request ID, and trace ID. It must not show raw storage
locators, object keys, internal SQL, stack traces, full chunks, prompts,
vectors, embeddings, provider payloads, tokens, or local paths.

## Local/Test Auth

For local smoke checks, the page has a collapsible auth helper. It can send a
JWT bearer value or development auth headers, but it does not save auth values.
Development headers are valid only when the backend explicitly allows them:

```powershell
$env:APP_ENV = "local"
$env:ENABLE_DEV_AUTH_HEADERS = "true"
```

Production usage should rely on the backend's normal JWT or service-token
mapping. Open WebUI is an entry point, not a permission boundary, and the
sidecar is not an authorization boundary.

## Diagnostics

The diagnostics tab provides copy helpers for request ID and trace ID, plus
focused verification commands and links to walkthrough/eval evidence. It does
not implement the full retrieval trace UI planned for Story 7.6, and it does
not fabricate dense, sparse, rerank, context-packing, prompt, or provider data.

Focused verification:

```powershell
.venv\Scripts\python.exe -m pytest tests/integration/api/test_sidecar_routes.py -q
.venv\Scripts\python.exe -m pytest tests/integration/api/test_sources_routes.py tests/integration/api/test_document_routes.py -q
.venv\Scripts\python.exe -m pytest tests/unit/web/test_sidecar_static_contract.py -q
```

Related evidence:

```text
docs/demo/enterprise-rag-walkthrough.md
tests/eval/reports/
```

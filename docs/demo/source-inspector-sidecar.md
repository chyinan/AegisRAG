# Source Inspector Sidecar

Story 7.6 provides a lightweight same-origin sidecar for source drilldown,
document version status, and request-driven diagnostics summaries. It is intentionally a
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

`/sidecar` remains Source Inspector-first for single-reference drilldown. For
multi-citation answer review, open `/governance` and use Source Evidence. That
view accepts citation JSON, Open WebUI metadata, evidence links, or manual
document/version/chunk/page/request identifiers, then resolves every item
through the same `POST /sources/resolve` backend authorization path before
rendering excerpts or source metadata. Trace IDs are displayed from backend
responses; they are not used as Source Evidence lookup inputs.

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
safe lookup, summary, stage, next-step, and report export controls. It calls:

```text
POST /diagnostics/resolve
```

Lookup accepts `request_id`, `trace_id`, or both. Backend authorization still
uses the normal `AuthenticatedRequestContext` and requires `audit:read` or
`diagnostics:read`; tenant filtering happens before any records are summarized.
The sidecar only renders allowlisted fields returned by the backend:
tenant/user/request/trace IDs, action/status, top-k/result counts, highest
rerank score, citation/context counts, generation provider/model/version,
token/event counts, latency, failure stage, error code, next-step commands,
and synthetic-safe report metadata.

Copy/download report actions use the safe report DTO or a client-side allowlist
derived from the safe summary. Report filenames use request ID or trace ID plus
a timestamp. The sidecar does not save bearer values, dev headers, authorized
excerpts, diagnostics responses, or exported reports to localStorage,
sessionStorage, cookies, or URLs.

Diagnostics is not a full retrieval trace UI, Grafana replacement,
OpenTelemetry viewer, prompt viewer, chunk viewer, provider payload viewer, or
log-file scraper. It must not render full query text, answer text, prompt,
chunk content, SQL, tsquery/tsvector data, vectors, embeddings, raw source
locators, object keys, provider payloads, tokens, secrets, local paths, or raw
exception text.

Focused verification:

```powershell
.venv\Scripts\python.exe -m pytest tests/integration/api/test_sidecar_routes.py -q
.venv\Scripts\python.exe -m pytest tests/integration/api/test_diagnostics_routes.py -q
.venv\Scripts\python.exe -m pytest tests/unit/diagnostics -q
.venv\Scripts\python.exe -m pytest tests/integration/api/test_sources_routes.py tests/integration/api/test_document_routes.py -q
.venv\Scripts\python.exe -m pytest tests/unit/web/test_sidecar_static_contract.py -q
.venv\Scripts\python.exe -m pytest tests/unit/web/test_governance_static_contract.py -q
```

Related evidence:

```text
docs/demo/enterprise-rag-walkthrough.md
tests/eval/reports/
```

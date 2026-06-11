# Main Workbench

The custom Next.js workbench in `apps/web` is the primary product interface for
AegisRAG. Open WebUI remains a compatible entry point and demo surface; it is
not the main product shell or an authorization boundary.

Design direction and frontend rules are captured in
`design-artifacts/D-Design-System/main-workbench-design-rules.md`.

The implementation now uses shadcn-style local primitives in
`src/components/ui`, Tailwind CSS, `cn()` from `src/lib/utils.ts`, and
`tailwind-merge` for class composition. New reusable controls should be added
there before business components grow more local CSS.

The main React workbench defaults to English. A language selector switches the
workbench UI to Chinese. Visible React copy belongs in `src/lib/i18n.ts`; avoid
hard-coded component text except stable protocol names such as `request_id` or
permission strings.

## Local Run

```powershell
cd apps/web
npm install
npm run dev
```

Open:

```text
http://127.0.0.1:3100
```

The app rewrites `/api/backend/*` to `RAG_API_BASE_URL`, defaulting to
`http://127.0.0.1:8000`.

The same Next origin also serves the existing static fallback surfaces:

- `/sidecar`
- `/governance`
- `/sidecar/assets/sidecar.css`
- `/sidecar/assets/sidecar.js`

## First Screen

The first screen is a role-aware knowledge operations workbench:

- left navigation: Ask, Knowledge Base, Review, Diagnostics, Eval, Audit,
  Agent Runs, and Settings according to permissions
- center work area: the current workflow, defaulting by role
- right inspector: Evidence and Diagnostics tabs

It is not a marketing page and not a generic chat shell. The visual system is a
border-light knowledge-operations console: cool neutral canvas, white raised
surfaces, soft shadows instead of nested box outlines, source green for
citation/evidence, index amber for ingestion/indexing, and danger red for
permission failures. The Ask composer uses a Command Center pattern with inline
retrieval and citation state chips plus a compact icon submit action.

Global CSS is reserved for design tokens, shell layout, and product-specific
composition such as the three-column workbench. Buttons, badges, cards, inputs,
selects, textareas, and tabs should use the UI primitives instead of new global
component classes.

## Auth Gate

The Auth Gate supports two modes:

- local/demo personas that send dev auth headers
- enterprise JWT handoff for backend verification

Local personas are only valid when the API explicitly enables dev headers:

```powershell
$env:APP_ENV = "local"
$env:ENABLE_DEV_AUTH_HEADERS = "true"
```

The frontend does not create or expand tenant, role, permission, ACL, citation,
source visibility, or Tool Registry authority.

## Role Defaults

- Employee opens Ask.
- Knowledge Manager opens Knowledge Base.
- AI Engineer opens Diagnostics.
- Auditor opens Audit or Review when permitted.
- Platform Admin opens Knowledge Base with broader governance navigation.

Sensitive entries such as Audit, Eval, and Settings are hidden unless the
current identity has the matching permission. Ordinary actions such as Import
or Diagnostics may be visible but disabled with a permission hint.

## Knowledge Base Import

Knowledge Base is a first-class entry. Story 9.5 implements a minimal upload
loop:

- file
- title
- source type
- source reference
- ACL preset
- async ingestion job result: `document_id`, `version_id`, `job_id`, `status`

The Ask screen also exposes a quick Import drawer for users with
`document:upload`. Upload does not block chat because backend ingestion remains
asynchronous.

## Evidence And Diagnostics

Citation chips open the Evidence inspector, which calls `POST /sources/resolve`
again before showing any excerpt. Denied, missing, soft-deleted, inactive
version, and ACL mismatch states use the same safe failure shape.

Diagnostics calls `POST /diagnostics/resolve` and displays only safe fields:
top_k, result_count, highest rerank score, citation_count, latency,
failure_stage, error_code, and next_steps. It does not render raw query, chunk
content, prompt, SQL, vectors, embeddings, provider payloads, tokens, or
secrets.

## Governance

Governance workflows are now first-class panels in the main shell. The static
`/governance` page remains available as a fallback drilldown and regression
surface while the React workbench becomes the primary UI.

Audit calls:

```text
GET /audit/logs
POST /audit/export
```

through the same `/api/backend/*` rewrite path. The form sends only allowlisted
filters: user ID, request ID, trace ID, action, resource type, resource ID,
status, created-at window, bounded limit, and association inclusion. It never
sends tenant ID, roles, permissions, raw SQL, metadata filters, or frontend
authority overrides. Results render only backend-confirmed audit summaries,
safe counts, safe summaries, and safe association labels.

The Audit export action uses the backend export payload only. Copying export
JSON does not serialize DOM rows, raw audit metadata, prompts, queries, chunk
content, SQL, vectors, embeddings, provider payloads, tool arguments, tool
output, tokens, or secrets.

Review calls:

```text
GET /review/items
```

The Review Queue panel sends only safe review filters and renders backend
review item summaries, safe identifiers, safe summaries, allowed status state,
and next steps. It does not create review items or convert eval candidates yet;
those remain backend-owned workflows for later panel expansion.

Eval calls:

```text
GET /eval/reports
```

The Eval Evidence panel renders safe report summary fields such as decision,
case counts, retrieval hit rate, citation coverage, and average latency. It
does not render raw report payloads, prompts, dataset content, provider output,
or case text.

Agent Runs calls:

```text
POST /agent/run
```

The Agent Run Console always sends bounded `max_steps`, `max_tool_calls`, and
`timeout_seconds`. It runs through backend Tool Registry, permission checks,
rate limits, repeated-action detection, final-answer validation, and audit
logging. The panel renders only safe run identifiers, status, counts, final
answer, and citations.

Settings is an identity boundary panel, not a configuration authority. It shows
the current browser-held auth mode, tenant/user identifiers, roles, and
permissions so operators can inspect what will be passed to the backend. It
cannot expand tenant, roles, permissions, ACL, citation visibility, source
visibility, or tool authority.

## Validation

```powershell
cd apps/web
npm run lint
npm run typecheck
npm test -- --run
npm run build
npm run test:e2e
```

# Main Workbench

The custom Next.js workbench in `apps/web` is the primary product interface for
AegisRAG. Open WebUI remains a compatible entry point and demo surface; it is
not the main product shell or an authorization boundary.

Design direction and frontend rules are captured in
`design-artifacts/D-Design-System/main-workbench-design-rules.md`.

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

## First Screen

The first screen is a role-aware knowledge operations workbench:

- left navigation: Ask, Knowledge Base, Review, Diagnostics, Eval, Audit,
  Agent Runs, and Settings according to permissions
- center work area: the current workflow, defaulting by role
- right inspector: Evidence and Diagnostics tabs

It is not a marketing page and not a generic chat shell. The visual system is a
light knowledge-operations console: soft gray-blue background, white panels,
restrained borders, source green for citation/evidence, index amber for
ingestion/indexing, and danger red for permission failures.

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

## Governance Links

Review, Eval, Audit, Agent Runs, and Settings are stable navigation surfaces in
the main shell. In Story 9.5, workflows that are not directly embedded link to
the existing `/governance` page and show safe empty states rather than fake
data.

## Validation

```powershell
cd apps/web
npm run lint
npm run typecheck
npm test -- --run
npm run build
npm run test:e2e
```

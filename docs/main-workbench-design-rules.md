# Main Workbench Design Rules

Date: 2026-06-10

## Product Position

The custom Next.js workbench is the primary product UI for AegisRAG.
is a compatible entry point and demo surface. The workbench must make trusted
enterprise RAG visible on the first screen: identity, scope, citations,
evidence, diagnostics, and governance routes.

This is not a marketing site, not a generic chatbot shell, and not an
observability wallboard.

## Confirmed Direction

Use the "light knowledge operations console" direction:

- soft gray-blue application background
- white and near-white panels with restrained borders
- low shadow usage, only for drawers and overlays
- source green only for backend-confirmed citation/evidence
- index amber only for ingestion, embedding, indexing, and pending states
- danger red only for permission failure, security risk, and destructive state
- radius capped at 8px
- dense but readable layout for long work sessions

The interface should feel calm and durable. Enterprise-grade must not mean
dark, bleak, or visually punishing.

## Information Architecture

Main navigation is organized by user workflow, not backend module names:

- Ask
- Knowledge Base
- Review
- Diagnostics
- Eval
- Audit
- Agent Runs
- Settings

Do not expose implementation terms such as dense retrieval, sparse retrieval,
chunks, embeddings, rerank, vector store, and tool registry as primary
navigation. Those details belong inside Diagnostics, Evidence, or admin
drilldowns.

## Role Defaults

After authentication, users land directly in their default workflow:

- Employee: Ask
- Knowledge Manager: Knowledge Base
- AI Engineer: Diagnostics
- Auditor: Audit or Review when permitted
- Platform Admin: Knowledge Base or Settings

Do not add a generic overview page for this phase.

## Permission Display Strategy

Use a mixed visibility strategy:

- Hide high-sensitivity entries such as Audit, Eval, and Settings unless the
  current identity has the relevant permission.
- Show ordinary but unavailable actions such as Import or Diagnostics as
  disabled controls with the required permission.
- Never use frontend state to reveal whether a specific unauthorized document,
  version, chunk, tenant resource, tool result, or audit record exists.

The frontend may display and disable UI. The backend remains authoritative for
AuthContext, tenant, roles, permissions, ACL, source visibility, citations,
Tool Registry execution, and audit.

## Knowledge Base Rules

Knowledge Base is a first-class product entry. It must not be hidden under
Settings.

The workbench supports two import paths:

- Full Knowledge Base page for careful metadata, ACL, document list, version,
  job, and indexing status workflows.
- Quick Import drawer from Ask or the top action area for users with
  `document:upload`.

The first version of the document list is business-readable first:

- title
- source type
- scope / ACL summary
- status
- updated timestamp
- actions

Engineering details are second-level drilldowns:

- document_id
- version_id
- job_id
- chunk count
- embedding model/dimension
- index status
- error_code
- request_id / trace_id

Upload remains asynchronous. The UI must not imply that a newly uploaded
document is immediately retrieval-ready.

## Ask And Citation Rules

Ask is an enterprise RAG workflow, not a plain chat box.

The first viewport must include:

- current identity and scope
- query composer
- answer stream area
- citation/evidence state
- request_id or diagnostic affordance
- right-side Evidence/Diagnostics inspector on desktop

Citation chips are first-class controls. Clicking a citation must call
`POST /sources/resolve` again before displaying any excerpt.

`Copy answer with citations` stays disabled until the terminal `final` event.
No-answer is a successful state with the copyable request_id and safe next
steps.

## Evidence And Diagnostics Rules

Evidence shows only backend-authorized excerpts and safe metadata. Denied,
missing, soft-deleted, inactive version, and ACL mismatch states share one safe
failure shape.

Diagnostics displays only safe timeline fields:

- top_k
- result_count
- highest rerank score
- citation_count
- latency
- failure_stage
- error_code
- next_steps

Never render raw query, chunk content, prompt, SQL, vectors, embeddings,
provider payloads, tokens, secrets, raw tool arguments, or raw tool output.

## Responsive Rules

Desktop at 1200px and above:

- left navigation and scope
- center workflow
- right Evidence/Diagnostics inspector

Tablet:

- collapsible or narrower left navigation
- inspector can overlay without blocking the composer

Mobile:

- single-column workflow
- Evidence/Diagnostics as bottom sheet or drawer
- no forced three-column compression

## Accessibility Rules

Every frontend story must preserve:

- keyboard-accessible buttons, tabs, drawers, chips, and copy controls
- visible focus state
- alert regions for safe errors
- `aria-live` or equivalent treatment for streaming/async states
- non-color-only status expression
- long identifiers wrapping or truncating with copy affordance
- no hover-only critical actions

## Anti-Patterns

Do not implement:

- marketing hero pages
- decorative gradient/orb backgrounds
- generic AI chat shells
- whole-page floating card stacks
- dark security theater dashboards
- fake governance data
- frontend-generated citations
- frontend-inferred authorization
- prompt text as a substitute for product rules

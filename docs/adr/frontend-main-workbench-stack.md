# ADR: Frontend Main Workbench Stack

Date: 2026-06-10

## Status

Accepted

## Context

The project needs a primary enterprise workbench that exposes trusted RAG,
citations, knowledge import, diagnostics, and governance workflows. Open WebUI
remains useful as a compatible entry point, but it cannot be the only product
interface because the project needs first-screen signals for RBAC, citation,
safe diagnostics, and knowledge operations.

## Decision

Use React, Next.js App Router, and TypeScript under `apps/web`.

Supporting libraries:

- TanStack Query for server state and retries
- Radix Dialog primitives for accessible drawer/modal behavior
- lucide-react for toolbar, status, evidence, diagnostics, and navigation icons
- Vitest and Testing Library for unit/component tests
- Playwright for desktop/mobile browser checks

The app runs locally on port 3100 to avoid the optional Open WebUI profile on
port 3000. Backend calls go through same-origin rewrites under
`/api/backend/*`, with `RAG_API_BASE_URL` as the non-secret target setting.

## Consequences

The workbench can evolve independently from FastAPI-served static `/sidecar`
and `/governance` pages while preserving those pages as safe fallbacks.
Frontend code must not call model providers, construct prompts, infer
citations, or decide authorization. The backend remains authoritative for
AuthContext, tenant, RBAC, ACL, source visibility, Tool Registry permission,
and audit.

Docker integration is deferred for this story. Local development uses
`cd apps/web && npm run dev`; future stories may add a web service/profile once
deployment and health-check expectations are finalized.

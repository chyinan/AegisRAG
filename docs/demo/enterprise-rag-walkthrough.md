# Enterprise RAG Walkthrough

This walkthrough is synthetic-only. It demonstrates the trusted RAG loop without
using real company files, personal data, local paths, provider credentials, or
storage locators.

## Files

```text
docs/demo/enterprise-rag/
  manifest.json
  corpus/
    hr-leave-policy.md
    faq-indexing-status.md
    product-vpn-manual.md
    technical-rag-operations.md
```

The manifest defines one demo tenant, three demo users, roles, permissions,
document ACLs, expected citations, no-answer, ACL isolation, prompt-injection,
and source drilldown cases. Document source locators use only the controlled
`synthetic://enterprise-rag-demo/` prefix.

## Case Matrix

```text
case-demo-hr-leave          policy answerable citation
case-demo-indexing-faq      FAQ answerable citation
case-demo-vpn-reset         product manual answerable citation
case-demo-rag-ops           technical operations answerable citation
case-demo-source-resolve    citation clickthrough with backend recheck
case-demo-no-answer         insufficient context with no citation
case-demo-acl-isolation     restricted document not visible to contractor
case-demo-prompt-injection  malicious document text remains untrusted context
```

## Validate And Materialize

Validate the manifest and corpus safety checks:

```powershell
.venv\Scripts\python.exe -m packages.data.demo_seed validate --manifest docs/demo/enterprise-rag/manifest.json
```

Materialize a local copy if you want a resettable demo folder:

```powershell
.venv\Scripts\python.exe -m packages.data.demo_seed materialize --manifest docs/demo/enterprise-rag/manifest.json --output .demo/enterprise-rag
```

Upload the synthetic documents through the existing API contract after the API
is running with explicit local/test dev headers enabled:

```powershell
.venv\Scripts\python.exe -m packages.data.demo_seed seed-uploads --manifest docs/demo/enterprise-rag/manifest.json --api-base-url http://127.0.0.1:8000 --state-file .demo/enterprise-rag/seed-state.json
```

The validation/materialization CLI does not forge database state. Code that
creates demo records should use `DemoSeedOrchestrator` with an injected
governance port for synthetic tenant, user, role, permission, and
role-assignment upsert. Demo document creation must use
`DocumentUploadService.upload()`, explicit `AuthenticatedRequestContext`, ACL,
source metadata, audit, and the normal async ingestion job contract.
`seed-uploads` follows the same `/upload` multipart boundary and records local
idempotency in the provided state file; it does not mark chunks or vectors
`retrieval_ready`.

## Open WebUI Path

Start the backend stack and optional Open WebUI profile as documented in
`docs/operations/local-development.md`. Configure Open WebUI with:

```text
http://api:8000/v1
```

Use a backend-mapped provider bearer token with `document:read` and
`retrieval:query` only. Open WebUI is an entry point, not an authorization
boundary; backend auth, RBAC, ACL, source visibility, and audit remain
authoritative.

Ask one of the manifest questions, such as:

```text
年假审批需要谁确认？
```

The response should include request ID, trace ID, session ID, answer text,
safe citations, and safe metadata. Citations expose display name, source type,
document/version/chunk/page/title metadata, retrieval method, and score.

## Source Resolve

Use a citation returned by the chat response:

```powershell
curl.exe -X POST http://127.0.0.1:8000/sources/resolve `
  -H "Content-Type: application/json" `
  -H "X-Request-ID: req-demo-source-resolve" `
  -H "X-Trace-ID: trace-demo-source-resolve" `
  -H "X-User-ID: demo-user-employee" `
  -H "X-Tenant-ID: tenant-demo-alpha" `
  -H "X-Roles: knowledge_user" `
  -H "X-Permissions: document:read,retrieval:query" `
  -d "{\"document_id\":\"<document-id-from-citation>\",\"version_id\":\"<version-id-from-citation>\",\"chunk_id\":\"<chunk-id-from-citation>\",\"citation_ref\":\"<source-ref-if-present>\"}"
```

The backend rechecks tenant, RBAC, ACL, soft delete, document/version/chunk
identity, version visibility, and active chunk status. Unauthorized, missing,
deleted, invisible, or ACL-denied references return the same safe denial shape.

## No-Answer, ACL, And Prompt Injection

Use the manifest cases to verify these behaviors:

```text
No-answer: insufficient context returns a no-answer response and no citation.
ACL isolation: contractor profile cannot cite restricted technical chunks.
Prompt injection: malicious document text is treated only as untrusted context.
```

The prompt-injection sample must not override backend rules, disclose hidden
system instructions, trigger tools, or expose raw source locator fields.

## Reports

Walkthrough reports may include:

```text
synthetic case IDs
status
request_id
trace_id
latency
retrieval/result/citation counts
failure stage
safe next-step commands
```

Reports must not include full query text, full answers, chunk text, prompts,
raw source locators, local paths, object keys, SQL, vectors, embeddings,
provider payloads, bearer tokens, JWTs, service tokens, database URLs, MinIO
credentials, or real enterprise data.

## Verification

Run the Story 7.4 focused checks:

```powershell
.venv\Scripts\python.exe -m pytest tests/unit/data/test_demo_seed.py -q
.venv\Scripts\python.exe -m pytest tests/integration/api/test_demo_walkthrough.py -q
```

Run the broader regression checks when preparing a review:

```powershell
.venv\Scripts\python.exe -m pytest tests/eval tests/unit/test_readme_expectations.py -q
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy apps packages tests
```

## Known Limits

This walkthrough does not certify provider-specific SDK adapters, production
SSO, custom management UI, Graph RAG, multi-agent flows, real enterprise data
ingestion, or a browser-side Source Inspector. Real LLM smoke checks reuse the
generic OpenAI-compatible adapter only when explicitly configured.

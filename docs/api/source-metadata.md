# Source Metadata Contract

Public RAG, retrieval, Open WebUI, Source Inspector, and Agent tool payloads
must use safe source display metadata. Internal storage and ingestion DTOs may
retain `source_uri` for authorization, audit correlation, object lookup, dedup,
and source resolution, but public API payloads must not expose it.

Public source fields:

```json
{
  "document_id": "doc-1",
  "version_id": "v1",
  "chunk_id": "chunk-1",
  "source_display_name": "policy.md",
  "source_type": "markdown",
  "page_start": 1,
  "page_end": 2,
  "title_path": ["Policy", "Leave"],
  "retrieval_method": "hybrid",
  "score": 0.91
}
```

The shared sanitizer is exported as `packages.rag.source_metadata` and backed by
the framework-free implementation in `packages.common.source_metadata`.

The sanitizer fails closed for local paths, UNC paths, `file://`, object-store
URIs, bucket/object keys, full URLs with query secrets, prompt-like titles, and
blank values. When no safe display name exists, it returns a controlled
placeholder such as `Untitled source` or `Source unavailable`.

Surfaces that must use this contract:

- `/retrieve` candidates
- `/query` and `/chat` citations
- `/query/stream` and `/chat/stream` citation/final SSE payloads
- `/v1/chat/completions` citation extension fields
- `/sources/resolve` Source Inspector responses
- `rag_search` tool observations

## Open WebUI Evidence Links

`POST /v1/chat/completions` returns `evidence_links` alongside public
citations for both non-streaming responses and the final streaming chunk. Token
chunks do not include evidence links.

Each evidence link is a pointer, not authorization:

```json
{
  "citation_ref": "citation-1",
  "evidence_url": "/governance?document_id=doc-1&version_id=v1&chunk_id=chunk-1&page_start=1&page_end=2&request_id=req-123&citation_ref=citation-1#source-evidence",
  "evidence_query": {
    "document_id": "doc-1",
    "version_id": "v1",
    "chunk_id": "chunk-1",
    "page_start": 1,
    "page_end": 2,
    "request_id": "req-123",
    "citation_ref": "citation-1"
  },
  "document_id": "doc-1",
  "version_id": "v1",
  "chunk_id": "chunk-1",
  "page_start": 1,
  "page_end": 2,
  "request_id": "req-123",
  "trace_id": "trace-123",
  "source_display_name": "policy.md"
}
```

`evidence_query` and URL query/hash parameters may contain only
`document_id`, `version_id`, `chunk_id`, `page_start`, `page_end`,
`request_id`, and `citation_ref`. `trace_id` and `source_display_name` are safe
metadata for display/correlation and are not source-visibility inputs.

Evidence links must not contain bearer tokens, service tokens, JWTs, raw
`source_uri`, object keys, local paths, tenant/user/role/permission fields,
ACLs, prompts, full queries, answers, chunk text, provider payloads, SQL,
vectors, embeddings, or raw exceptions. `/sidecar` and `/governance` parse the
link or metadata into source resolve identifiers, then call
`POST /sources/resolve`; the backend rechecks tenant, RBAC, ACL, soft delete,
version visibility, document/version/chunk identity, and page identity before
returning any excerpt.

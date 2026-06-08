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

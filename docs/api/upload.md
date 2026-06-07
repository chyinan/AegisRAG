# Upload API

`POST /upload` accepts authorized multipart document uploads and returns immediately after raw object storage, document metadata, version metadata, and an ingestion job record are created. It does not run parser, chunker, embedding, or vector indexing synchronously.

## Request

Required auth context:

```text
X-Request-ID: req-local-1
X-Trace-ID: trace-local-1
X-User-ID: user-123
X-Tenant-ID: tenant-abc
X-Permissions: document:upload
```

Multipart fields:

```text
file: PDF, DOCX, TXT, .md, or .markdown file
document_id: optional existing document ID; requires document:manage and creates a new version
source_type: pdf | docx | txt | markdown
source_uri: optional external URI, not a local absolute path
title: optional display title
acl: optional JSON object, defaults to {"visibility":"tenant"}
metadata: optional JSON object
```

When `document_id` is omitted, upload creates a new `documents` row. When
`document_id` is provided, the service performs a tenant-scoped manage-permission
check and creates a new `document_versions` row plus a new ingestion job under
that document. The API never guesses "same document" from `source_uri`.

Example:

```powershell
curl.exe -X POST http://127.0.0.1:8000/upload `
  -H "X-Request-ID: req-upload-1" `
  -H "X-Trace-ID: trace-upload-1" `
  -H "X-User-ID: user-123" `
  -H "X-Tenant-ID: tenant-abc" `
  -H "X-Permissions: document:upload" `
  -F "file=@policy.txt;type=text/plain" `
  -F "source_type=txt" `
  -F "source_uri=kb://policy.txt" `
  -F "metadata={\"department\":\"HR\"}"
```

## Response

```json
{
  "request_id": "req-upload-1",
  "data": {
    "document_id": "doc-id",
    "version_id": "version-id",
    "job_id": "job-id",
    "status": "uploaded"
  },
  "error": null,
  "metadata": {
    "latency_ms": null
  }
}
```

The queue payload contains only `request_id`, `trace_id`, `tenant_id`, `user_id`, `job_type`, `resource_id=job_id`, and `{document_id, version_id}` parameters.

## Parser Stage

The ingestion worker validates the ID-only queue payload before calling the parser application service. Parser business logic lives in `packages/ingestion`, not in the FastAPI route.

Supported parser source types:

```text
markdown | md -> MarkdownParser
txt          -> TxtParser
pdf          -> PdfParser
docx         -> DocxParser
```

Markdown output is normalized as:

```text
RawDocumentRef / ParseRequest
  -> ParsedDocument
    -> Section[]
```

Each `Section` keeps `tenant_id`, `document_id`, `version_id`, `source_type`, `source_uri`, `acl`, `title_path`, `content`, optional page fields, checksum, and safe metadata. Markdown ATX headings (`#` through `######`) become `title_path`; body text before the first heading uses `["Untitled"]`. TXT files create a default section, using a safe filename title when available, and preserve paragraph/newline boundaries.

PDF parsing extracts text page by page through `PdfParser`. Each non-empty page produces a section with 1-based `page_start` and `page_end`, and the parsed summary includes safe `page_count` and `page_ranges` fields. Scanned/image-only PDFs, empty PDFs, encrypted unreadable PDFs, or damaged PDFs become stable parser failures; OCR, layout reconstruction, image text extraction, and table structure extraction are not part of this stage.

DOCX parsing extracts paragraphs through `DocxParser`. Built-in English style names `Title` and `Heading 1` through `Heading 9` maintain the section `title_path`; body paragraphs before a heading use a safe filename title. DOCX page numbers are not reliable in the file package, so `page_start` and `page_end` are explicitly `null`, and the parsed summary records `page_metadata=unavailable`. This parser does not execute macros, links, external resources, or document instructions.

The parser treats document content as untrusted text. It does not render HTML, execute links, follow instructions inside documents, call tools, or evaluate prompt-like content.

## Cleanup and Dedup Stage

After a `ParsedDocument` is materialized, ingestion can run a pure backend cleanup and exact dedup stage before chunking:

```text
RawDocumentRef / ParseRequest
  -> ParsedDocument
    -> cleaner
    -> dedup
    -> FixedSizeChunker
    -> Chunk[]
```

The default cleaner normalizes line endings, trims trailing line whitespace, compresses repeated blank lines, removes invisible zero-width noise, and conservatively removes repeated short PDF page header/footer lines only when reliable page metadata exists across multiple page sections. DOCX, Markdown, and TXT sections without reliable page numbers are not treated as PDF header/footer candidates.

Each cleaned section receives a stable `metadata.content_checksum` generated from canonical UTF-8 text with SHA-256. The top-level `ParsedDocument.checksum` remains the raw object/version checksum and is not replaced by the cleaned checksum. Cleanup and dedup preserve governance and citation fields including `tenant_id`, `document_id`, `version_id`, `source_uri`, `title_path`, `page_start`, `page_end`, and `acl`.

Exact dedup only runs within a single `ParsedDocument`; it does not deduplicate across tenants, documents, or versions. The MVP dedup key is `content_checksum + normalized title_path`, so only later exact duplicate sections under the same title path are dropped. Cleanup metadata stores safe counts such as `cleaned_section_count`, `removed_section_count`, `removed_empty_section_count`, `removed_header_footer_line_count`, `duplicate_section_count`, and `deduped_section_count`; it does not store removed text or duplicate section content.

The default `FixedSizeChunker` is implemented as an offline pure component in
`packages.ingestion.chunkers`. It splits cleaned/deduped sections into stable,
deterministic chunks with a default 500 to 800 token target and configurable
10% to 20% overlap. Chunk metadata preserves `tenant_id`, `document_id`,
`version_id`, `source_type`, `source_uri`, `acl`, `title_path`,
`section_ids`, `page_start`, `page_end`, `token_count`, and a stable content
checksum. PDF chunks keep 1-based page ranges; DOCX/TXT/Markdown chunks do not
receive synthetic page numbers when page metadata is unavailable.

The current upload request still does not synchronously execute chunking,
embedding, or vector indexing. The ingestion pipeline now has a storage boundary
for chunk persistence after `FixedSizeChunker` produces typed chunks:

```text
parse -> clean -> dedup -> chunk -> persist chunks
```

Chunk persistence converts `packages.ingestion.domain.Chunk` into
`packages.data.dto.ChunkRecord` before crossing into storage. The `chunks` table
stores tenant/document/version/chunk IDs, ACL, source metadata, title path,
section lineage, optional 1-based page ranges, token count, checksum, status,
and content for later embedding/retrieval stages. SQLAlchemy models stay inside
`packages.data.storage`.

Parser job state changes:

```text
queued/uploaded -> parsing -> parsed
queued/uploaded -> parsing -> failed_terminal
queued/uploaded -> parsing -> failed_retryable
parsed -> chunked
```

On success, `document_versions.metadata.parsed_artifact_summary` stores a safe parser summary: section count, stage, checksum, and parser-specific safe fields such as PDF `page_count/page_ranges` or DOCX `heading_count/page_metadata`. On parser failure, `ingestion_jobs`, `document_versions`, and `documents` move to the terminal or retryable failure status together. It intentionally does not store full document text in audit/log metadata. The current parser job still records only the `parsed` safe summary; the full cleaned/deduped `ParsedDocument` is not persisted by this API stage. The next chunker stage should read the raw object through `ObjectStorage.get_document()`, materialize `ParsedDocument`, then run `parse -> clean -> dedup -> chunk` before splitting.

When chunk persistence completes, `ingestion_jobs`, `document_versions`, and
`documents` can move to `chunked`. `document_versions.metadata.chunk_artifact_summary`
only records safe aggregate fields such as `chunk_count`, `token_count_min`,
`token_count_max`, and checksum summaries. It must not contain full chunk
content, prompt-like text, removed text, bearer tokens, API keys, MinIO secrets,
or local absolute paths.

## Embedding Stage

Embedding runs after chunk persistence and is handled by a separate worker job.
The queue payload is still ID-only: `job_id`, `document_id`, `version_id`, plus
request and auth IDs for audit correlation. It does not contain chunk content,
prompts, API keys, provider raw responses, complete vectors, or local absolute
paths.

The embedding service only processes document versions already in `chunked`
status. It reads active `ChunkRecord` rows for the same `tenant_id`,
`document_id`, and `version_id`, then calls the configured
`EmbeddingProvider.embed_texts()` batch port. Business code does not depend on a
specific OpenAI, Qwen, DeepSeek, Ollama, or vLLM SDK.

Successful embedding advances the provider stage:

```text
chunked -> embedding -> embedded
```

`embedded` means provider vectors were generated and safe metadata was recorded.
It does not mean pgvector/FAISS/Milvus indexing is complete, and it must not be
treated as `retrieval_ready`.

`embedding_jobs` records provider, model, provider version, dimension,
attempt_count, retry timing, and error_code. In the same worker success path,
the service maps the in-memory provider response to `VectorRecord` and calls
`VectorStore.upsert`; it does not re-read the raw document, re-run chunking, or
call the provider again. `document_versions.metadata`, `embedding_jobs.metadata`,
and chunk metadata store only safe embedding/vector summaries such as
provider/model/dim, chunk count, vector count, token min/max, usage counts,
status, and latency. They must not store chunk text, full vectors, provider raw
API responses, prompts, tokens, API keys, or local absolute paths.

Vector indexing success is a retrieval prerequisite, not final retrieval
readiness by itself. After `VectorStore.upsert` succeeds, the repository validates
that active chunk count is greater than zero, the embedding job is `embedded`,
the vector index summary is `indexed`, and `vector_count == active chunk count`.
Only then `document_versions.status` and the latest non-deleted document status
advance to `retrieval_ready`.

## Document Lifecycle API

Admins with `document:manage` can inspect readiness and soft-delete documents:

```text
GET /documents/{document_id}/versions/{version_id}/status
DELETE /documents/{document_id}
DELETE /documents/{document_id}/versions/{version_id}
```

Status responses return only safe metadata: document/version IDs, status,
chunk count, embedding provider/model/version/dim, vector count, index status,
deleted_at, error_code, request_id, and trace_id.

Delete operations are soft deletes. They set document/version/chunk status to
`deleted`, populate `deleted_at`, and call
`VectorStore.delete_by_document(document_id, version_id, tenant_id=...)` so
search defaults exclude deleted vectors. Raw object storage files are retained
for future retention/cleanup workflows.

## Errors

Stable upload errors:

```text
DOCUMENT_UPLOAD_FORBIDDEN -> 403
DOCUMENT_UPLOAD_UNSUPPORTED_TYPE -> 415
DOCUMENT_UPLOAD_TOO_LARGE -> 413
DOCUMENT_UPLOAD_INVALID_METADATA -> 400
DOCUMENT_STORAGE_WRITE_FAILED -> 502
INGESTION_JOB_ENQUEUE_FAILED -> 502
```

Stable parser errors:

```text
DOCUMENT_PARSE_UNSUPPORTED_TYPE -> failed_terminal
DOCUMENT_PARSE_EMPTY_CONTENT -> failed_terminal
DOCUMENT_PARSE_ENCODING_FAILED -> failed_terminal
DOCUMENT_PARSE_FAILED -> failed_retryable unless mapped more specifically
DOCUMENT_STORAGE_READ_FAILED -> failed_retryable
DOCUMENT_CLEAN_EMPTY_CONTENT -> stable cleaner domain error when cleanup removes all content
DOCUMENT_CHUNK_CONFIG_INVALID -> stable chunker configuration error
DOCUMENT_CHUNK_EMPTY_CONTENT -> stable chunker empty-content error
DOCUMENT_CHUNK_FAILED -> stable chunker failure with safe details only
EMBEDDING_PROVIDER_TIMEOUT -> failed_retryable
EMBEDDING_PROVIDER_RATE_LIMITED -> failed_retryable
EMBEDDING_PROVIDER_FAILED -> failed_retryable unless validation marks terminal
EMBEDDING_BATCH_SIZE_MISMATCH -> failed_terminal
EMBEDDING_VECTOR_DIMENSION_MISMATCH -> failed_terminal
EMBEDDING_DOCUMENT_VERSION_NOT_CHUNKED -> failed_terminal
INDEX_DIMENSION_MISMATCH -> failed_terminal
VECTOR_STORE_WRITE_FAILED -> failed_retryable
DOCUMENT_MANAGE_FORBIDDEN -> 403
DOCUMENT_NOT_FOUND -> 404
DOCUMENT_VERSION_NOT_FOUND -> 404
DOCUMENT_INDEX_NOT_READY -> 409
DOCUMENT_VERSION_INVALID_STATE -> 409
DOCUMENT_DELETE_FAILED -> 500
```

Errors and audit metadata never include file content, bearer tokens, API keys, MinIO secrets, prompt text, or local absolute paths.

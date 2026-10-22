# Semantic chunking failure lifecycle

## Scope

`IngestionParseService` defaults to `FixedSizeChunker`. Semantic chunking is
available only when a caller explicitly injects a `SemanticChunker` with an
embedding provider. The normal ingestion worker does not construct that
chunker; semantic environment settings alone do not select it. This repair does
not add provider wiring or change the default path.

## Failure contract

Semantic sentence embedding is chunk computation, before any chunk storage or
embedding-job handoff. An `EmbeddingProviderError` raised there retains its
original error code and is re-raised after the existing ingestion failure
transition:

- `retryable=True`: `failed_retryable` while the executor's `QueueRetryPolicy`
  has retries; `next_retry_at` uses that policy's delay
- `retryable=False`, or an exhausted executor retry policy: `failed_terminal`,
  with no retry deadline
- Without an executor retry policy, retryable failures retain the existing
  `failed_retryable` behavior without a scheduled deadline

The failure write uses the claimed attempt count, preserving the repository's
compare-and-set guard against stale attempts. A later due retry follows the
normal claim path and increments its attempt count. It does not resume through
an unchanged `parsed` checkpoint. Required failure audit and status persistence
remain part of the existing transaction contract.

Missing, duplicated, extra or out-of-range sentence-vector indices now raise
`EmbeddingProviderError(code="EMBEDDING_BATCH_SIZE_MISMATCH", retryable=False)`
rather than leaking a lookup `KeyError`. A complete batch returned out of order
is still reordered to match the input sentences. Empty vectors, vectors whose
length differs from the declared response dimension, and non-finite values
raise nonretryable `EMBEDDING_VECTOR_EMPTY`,
`EMBEDDING_VECTOR_DIMENSION_MISMATCH`, and `EMBEDDING_PROVIDER_FAILED` errors,
respectively. Correctly dimensioned finite zero vectors retain their existing
behavior. No `DocumentChunkError` API change is required.

A computation failure writes no chunks, creates no downstream embedding job,
and does not enqueue one. Caller cancellation still propagates without being
reclassified as a provider failure. Storage errors, ambiguous commit failures
and errors in a committed embedding handoff are outside computation failure
mapping; their existing transaction and handoff-replay behavior remains in
charge. There is no new broad exception catch or retry loop.

## Local verification

```bash
uv run --frozen pytest --no-cov \
  tests/unit/ingestion/test_semantic_ingestion_failures.py \
  tests/unit/ingestion/test_semantic_chunker.py \
  tests/unit/ingestion/test_parse_service.py \
  tests/integration/storage/test_semantic_ingestion_lifecycle.py \
  tests/integration/storage/test_embedding_handoff.py \
  tests/integration/storage/test_retry_lifecycle.py
```

The direct regressions use the real semantic chunker, fake provider responses,
and the existing service test doubles. Integration cases use migrated,
disposable SQLite files and real status/audit persistence, including a fresh
session after failure and a due retry. These are not proof of live provider,
Redis/RQ delivery, PostgreSQL concurrency or deployed UI behavior. No live
provider call is needed or enabled by these tests.

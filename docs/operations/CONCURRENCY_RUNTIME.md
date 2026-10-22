# Query concurrency resource ownership

This change was prepared on PR #8 head
`6f40137a64959125b2537360e9cb747ffe4e3782` to preserve its product workflows.
PR #8 is now merged: publication is based on `main` commit
`e80954e3128cfb82f4730ed9a6759e47b545beea`, whose tree is byte-identical to that
PR head. This PR therefore contains only the concurrency changes.

## SQL operations and consistency

Ordinary `/query`, `/chat` and ServiceToken chat compose independent sessions for
vector searches, sparse searches, and each complete hydration batch. Sessions
close their own read transactions before returning detached DTOs. Embedding/query
rewrite happens before its search opens a transaction; reranking and final answer
generation do not retain a search/hydration connection. Independent branches can
run concurrently without concurrently using one SQLAlchemy AsyncSession.

The adapters set/check authenticated tenant scope before the PostgreSQL RLS hook.
Existing query-stage ACL/tenant/metadata checks and fresh hydration identity,
status/deletion and ACL checks remain. Separate stages are not a cross-stage
repeatable-read snapshot: concurrent source changes must still fail closed during
hydration. This is the existing read-committed contract, not a new strong snapshot
or permission system. No read adapter flushes or commits a caller's pending writes.

Chat still commits creation/history plus the user message before generation, then
commits the assistant only after a completed response/final event. Closing or
cancelling generation does not synthesize a completed assistant. Its request
session may remain alive but is idle, without a checked-out connection, between
these operations. Required query audit now owns a separate transaction; optional
stream/chat audit retains its existing bounded best-effort wrapper. Existing audit
failure policies are unchanged. Agent write/evidence composition remains intact.

## Process-local configuration

The following values are environment variables, also exposed in `.env.example`,
Compose's shared environment, and Helm `api.env` (inherited by its workers):

| Variable | Default | Meaning |
| --- | ---: | --- |
| `DATABASE_POOL_SIZE` | 5 | Persistent PostgreSQL connections per engine/process |
| `DATABASE_MAX_OVERFLOW` | 10 | Additional connections per engine/process |
| `DATABASE_POOL_TIMEOUT_SECONDS` | 30 | Pool checkout wait limit |
| `LLM_MAX_IN_FLIGHT`, `EMBEDDING_MAX_IN_FLIGHT` | 8 each | Logical calls admitted per native API provider budget |
| `LLM_MAX_WAITERS`, `EMBEDDING_MAX_WAITERS` | 32 each | Finite waiting queue length |
| `LLM_QUEUE_TIMEOUT_SECONDS`, `EMBEDDING_QUEUE_TIMEOUT_SECONDS` | 2 each | Maximum admission wait |
| `LLM_TOTAL_TIMEOUT_SECONDS`, `EMBEDDING_TOTAL_TIMEOUT_SECONDS` | 45 each | Total provider call deadline including queue/retries/stream consumption |
| `PROVIDER_RETRY_BACKOFF_SECONDS` | 0.1 | Initial full-jitter exponential retry bound |
| `PROVIDER_RETRY_MAX_BACKOFF_SECONDS` | 2 | Local backoff cap; valid Retry-After is not shortened |
| `BGE_MAX_CONCURRENCY` | 1 | Executor workers for the shared local reranker |
| `BGE_MAX_QUEUE_SIZE` | 32 | Additional unfinished local inference jobs |

`LLM_TIMEOUT_SECONDS` and `EMBEDDING_TIMEOUT_SECONDS` still bound each HTTP attempt.
Explicit total deadlines now also bound a slow stream consumer. A retry waits at
least valid Retry-After, subject to the total deadline. Only retryable failures are
retried; streams retry only before exposing any token, and never replay emitted
tokens. Queue full/wait expiry produces existing structured rate-limited errors
with 503, rather than unlimited admission. A logical call holds its slot through
retry backoff. Cancellation recovers its admission slot and closes its response.

These are per-process controls, not global RPM/TPM enforcement, tenant fairness,
whole-application deadlines, or provider billing limits. LLM and embedding have
separate queues. Native adapters use shared API-lifespan pools; LangChain adapters
retain their existing framework behavior and do not inherit these native gates.
CLI/RQ jobs without an API lifespan retain operation-owned HTTP clients, avoiding
reuse across unrelated `asyncio.run` event loops. Configure their job/worker
parallelism separately. Restart processes to apply a changed database pool budget.

Budget database connections across all API replicas/workers/engines and RQ
processes: sum their configured pool size plus overflow, then reserve capacity for
migrations, administration, independent audit and recovery operations. SQLite's
in-memory StaticPool does not receive PostgreSQL QueuePool options. Settings are
configuration bounds, not a measured safe recommendation for your database.

## Local inference, cache and shutdown

The app shares a BGE reranker/executor per model and configured budget. Cold model
load and inference happen in worker threads. A timed-out/cancelled running Python
thread cannot be killed: it continues to own unfinished-work capacity until it
actually completes. Cancelled queued jobs retain admission until their WorkItem is actually consumed
and skipped (or shutdown drains it), preventing cancelled-item memory accumulation. Queue saturation
uses existing rerank degradation handling. Shared cached models also have an
inference lock, preventing separate constructors from invoking one model at once;
creating independent reranker instances still creates separate admission budgets.
Graceful shutdown joins threads off the event loop; an indefinitely hung native
library requires external process supervision. No real model is loaded by unit
contract tests.

Cache keys are versioned and include retrieval semantics and all authorization/
metadata filters; old tenant-only Redis keys are not reused. Redis pools and the
memory fallback live for the API lifespan. CachedRetriever singleflight is only
within one wrapper and identical request/trace identity, to avoid reusing a
request-scoped upstream or attributing another request's provider work. Normal
cross-request cold misses can therefore still duplicate retrieval. There is no
distributed cache-fill lock or shared final-answer cache. Cache identity does
not yet namespace pipeline/model/backend revisions; use separate Redis deployment
namespaces and treat pipeline changes/rolling deployments as cache invalidation
work until that separate contract is introduced. Fresh downstream ACL and
hydration checks remain required.

ASGI middleware explicitly propagates the app's resources into request/SSE tasks;
lifespan ContextVars alone would not propagate to sibling request tasks. Shutdown
closes caches, shared provider clients and executors. Injected HTTP clients retain
caller ownership. Stream wrappers and the ASGI response explicitly close their
inner iterator on send failure or outer close, rather than waiting for garbage
collection.

## Verification and target acceptance

Targeted tests use actual disposable file-backed SQLite with a one-connection
pool and fake generation to check connection release and chat write boundaries.
HTTP provider, local-inference and load-script tests use deterministic transports/
loaders to check error/cancellation/admission protocols. These are not PostgreSQL,
Redis, GPU/model, socket-backpressure or performance acceptance.

Run the separately authorized [target mixed-load procedure](CONCURRENCY_ACCEPTANCE.md)
with real providers and recorded budgets before making capacity or latency claims.
Existing historical FakeLLM numbers are not a new baseline. Export/collect the
listed provider/DB/queue metrics externally; the load client cannot infer them.

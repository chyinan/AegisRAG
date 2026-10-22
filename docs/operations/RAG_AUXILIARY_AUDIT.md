# RAG optional audit wait and transaction boundaries

## Scope and ordering

The API composition root bounds only audit hooks that already tolerate ordinary
failures: streaming Query audit, Chat lifecycle audit, and ServiceToken adapter
audit. Each uses an explicitly owned `SessionFactoryAuditPort` through
`BoundedBestEffortAuditPort`. Each record creates its own session, transaction,
commit and cancellation cleanup; it never borrows the request session.

- Query streaming still validates the final provider result and citations before
  its audit attempt and final event, including the no-answer path
- Chat still appends and commits the assistant message before its optional audit
  attempt and returned answer/final event. User-message persistence is unchanged
- The ServiceToken adapter still emits its final chunk, attempts optional audit,
  and emits `[DONE]` for a connected consumer. It no longer yields `[DONE]` while
  unwinding cancellation or generator closure, including closure at an error chunk
- Successful **nonstream Query audit remains required**: it is awaited inline on
  the original request-scoped `SqlAlchemyAuditPort(auto_commit=True)`. Its failure
  still prevents successful completion, and its existing transaction policy is
  unchanged. Nonstream Query denial/failure audit retains its existing policy
- Final validation, required chat persistence, retrieval/provider behavior and
  tool dispatch policies are unchanged. Optional audit failure is not evidence
  that any required operation succeeded

No schema, migration, deployment, provider permission or tool catalog changes
are needed. Native and optional framework components use the same composition.

## Budget and cancellation

`RAG_AUXILIARY_IO_TIMEOUT_SECONDS` controls the per-record wait: default **1 second**,
finite, greater than zero, and at most 30 seconds. It is independent of
`AGENT_AUXILIARY_IO_TIMEOUT_SECONDS`. A timed-out audit gets at most another
**0.05 seconds** of asynchronous cancellation cleanup. Multiple optional audit
hooks can occur in one request, so their overhead is additive; this is not a
whole-request deadline or a latency guarantee.

A completed audit child's self-cancellation is treated like another optional
audit failure. Caller cancellation propagates unchanged from the wait boundary,
including repeated cancellation during cleanup. Classification does not rely on
historical `Task.cancelling()` counts. An audit failure warning records only stable
correlation fields and the exception type, with no traceback or exception text.
A late cancellation-resistant exception is consumed without exposing its payload.

The wait and grace apply only while the event loop can run. They cannot terminate
synchronous blocking code, a suspended process, or a cancellation-resistant
integration. Resistant work can outlive its caller; the owned session remains
its responsibility until cleanup finishes. Configure driver/network timeouts
and monitor connection-pool pressure in the target environment. Best-effort audit
can be absent after timeout; monitor `rag.auxiliary_audit.audit_failed` and
`rag.auxiliary_audit.cancel_timeout`. This does not provide durable audit delivery.

## Programmatic compatibility

Existing constructors do not automatically wrap or detach their supplied audit
ports. `RagQueryApplicationService(audit=...)` uses that port inline for both paths
unless the caller explicitly supplies `stream_audit=...`. Chat and ServiceToken
adapter ports also retain their supplied inline behavior. This preserves legacy
request-session ownership and does not claim to bound legacy custom hooks.

A caller can explicitly supply `BoundedBestEffortAuditPort(owned_audit=...,
timeout_seconds=...)` for an already-optional hook. The delegate **must own every
resource for its full asynchronous lifetime**. Do not wrap a request-scoped
SQLAlchemy session, required nonstream Query audit, chat persistence, or arbitrary
tool execution. The production composition demonstrates the intended pairing
with `SessionFactoryAuditPort`.

The shared wait core is reused through the existing Agent module's compatibility
wrapper. Agent public imports, budgets, warning event and cancellation behavior
remain unchanged; the RAG-only child-cancellation conversion is opt-in.

## Offline verification and limits

Focused tests cover supported-answer/no-answer finals, chat append/commit order,
ServiceToken final/audit/DONE order, child self-cancellation, stale/pending/racing
and repeated caller cancellation, delayed cleanup and private exception payloads.
Legacy constructors and mandatory nonstream Query failure propagation are checked
separately. Composition tests cover native/LangChain model selections and
native/LangGraph scheduling selections. Existing Agent wait regressions protect
its compatibility wrapper.

File-backed SQLite tests exercise distinct sessions and real transactions,
including optional audit rollback after flush and preserved committed chat
messages. These checks do not establish PostgreSQL, remote driver/network latency,
production connection-pool capacity or complete required-resource shutdown safety.

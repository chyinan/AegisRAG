# Engineering case study: making completion and measurements trustworthy

This is a code-backed explanation of the current project, not a claim of personal
employment history, production traffic, cost savings or measured performance
improvement. Use it alongside the actual acceptance report and its limitations.

## The product problem

A private-knowledge assistant has several different meanings of “done”:

- An upload request returned, but indexing may still be in progress
- A model or tool finished, but a required transaction may not be committed
- A stream ended, but no valid final answer may have been received
- An HTTP measurement returned, but it may have reached the wrong target or
  included failed requests in an unlabeled statistic

Treating these as the same event produces believable but misleading UI states and
operational reports. The changes below make the distinctions explicit without
replacing the application's authorization, Tool Registry or provider ports.

## 1. Transaction completion is not merely task completion

The ordinary Agent service now gives each create/update plus commit phase one
cooperative deadline. Rollback has a separate cooperative cleanup deadline. It tracks
whether commit was not started, acknowledged, or may have happened without an
acknowledgement. An uncertain commit is not automatically replayed or rewritten
as a successful cancellation.

This is deliberately different from adding a generic timeout decorator. The
important question after a timeout is what is known about the business effect.
A late or missing acknowledgement does not prove that the database rolled back.
Likewise, a best-effort audit failure after acknowledged completion must not
replace the committed business result.

Implementation: [Agent application service](../../packages/agent/service.py).
Contracts: [phase failure injection](../../tests/unit/agent/test_persistence_phases.py),
[SQLite transaction lifecycle](../../tests/integration/storage/test_agent_persistence_phases.py),
[independent ordinary persistence review](../../tests/review/test_ordinary_persistence_review.py).
The [operations guide](../operations/AGENT_PERSISTENCE_PHASES.md) records defaults
and failure semantics.

**Remaining tradeoff:** the existing fast-cancellation runtime can return while a
cancellation-resistant tool or required recorder still owns a descendant task.
The new phase budgets do not make a shared request SQL session exclusively owned
or forcibly terminate that task. This limitation remains explicit; these changes
are not a universal transaction-isolation guarantee.

## 2. Optional audit work must not own the response lifecycle

Factory-composed optional Agent and RAG audit work now has an explicit bounded
best-effort boundary with independently owned sessions. An operation that times
out can be cancelled and tracked without sharing the request's required
transaction. RAG constructors leave supplied custom audit ports inline unless
explicitly wrapped; custom Agent audit ports must independently own their
resources to be safe at this boundary. Required non-streaming Query audit, Chat
persistence, tool records and durable ledger writes retain their fail-closed
contracts.

The service-token stream adapter also no longer emits a normal `DONE` marker from
a `finally` block. Closing an async generator after a token or an error chunk
must unwind it; it must not attempt a new yield. Upstream caller cancellation
propagates rather than being converted into a clean terminal marker.

Implementation: [shared bounded auxiliary I/O](../../packages/common/auxiliary_io.py),
[RAG-owned audit adapter](../../packages/rag/auxiliary_audit.py),
[service-token stream](../../packages/rag/service_token.py).
Contracts: [generator close/cancellation](../../tests/unit/rag/test_service_token_cancellation.py),
[SQLite audit isolation](../../tests/integration/storage/test_rag_auxiliary_audit_isolation.py),
[independent audit lifecycle review](../../tests/review/test_rag_optional_audit_review.py).

**Remaining tradeoff:** best-effort delivery can lose events, and cooperative
cancellation depends on the external client. A bounded response is not evidence
that all optional audit events were delivered.

## 3. Durable framework integration needs business state

Native and real LangGraph execution share governed tools and business invariants.
Durability is implemented with a versioned business snapshot, original wall-clock
deadline, remaining budgets, exact approval identity, revision/CAS, lease and tool
result ledger. The framework checkpoint holds safe business references rather
than serializing a live request closure, authentication context or arbitrary
provider object.

A committed tool result can be consumed after a process restart without executing
that completed call again. An unknown side-effect outcome is a different state:
without an external idempotency/transaction protocol, it can require intervention.
The UI exposes this distinction and does not equate stopping local waiting with a
persisted cancellation receipt.

Implementation: [durable service](../../packages/agent/durable),
[storage](../../packages/agent/storage/durable.py),
[workbench state](../../apps/web/src/hooks/use-agent-workbench.ts).
Contracts: [independent-process recovery](../../tests/integration/framework/test_process_recovery.py),
[lost first-response recovery](../../tests/integration/framework/test_keyed_start_process_recovery.py),
and [UI protocol/component tests](../../apps/web/src/components/agent-panel.test.tsx).
See [ADR 0009](../adr/0009-governed-framework-and-recovery.md).

**Remaining tradeoff:** local SQLite/process tests do not validate PostgreSQL
locking, cross-host failover or arbitrary exactly-once effects. The separate
[PostgreSQL acceptance gate](../operations/DURABLE_DATABASE_ACCEPTANCE.md) still
requires its target environment. Consult the delivered report for deferred
review/test scope; a capability being implemented does not imply every gate ran.

## 4. Measurement logic is part of the evidence

Two existing operator utilities had defects that could make successful-looking
reports unreliable: ignoring a selected URL, cross-run state reuse, a mean using
the wrong successful-sample count, empty-sample crashes, and successful CLI exits
after failed workloads. Independent review also found that an empty URL `?` or
`#` delimiter could change the wire path while a 2xx response still looked like a
successful measurement.

The repaired utilities validate their destination before I/O, report empty and
failed outcomes explicitly, and return a nonzero exit for incomplete runs.
The sequential utility uses successful-only nearest-rank latency. The concurrent
closed-loop utility intentionally preserves all-terminal-attempt latency and its
legacy floor-index percentile algorithm. Both definitions are recorded rather
than silently treating their numbers as comparable. Actual elapsed time, not just
the requested dispatch duration, is the closed-loop throughput denominator.

Implementation: [sequential benchmark](../../evaluation/benchmark_pipeline.py),
[closed-loop load utility](../../evaluation/load_test.py).
Contracts: [CLI regressions](../../tests/unit/evaluation/test_benchmark_pipeline.py),
[load measurement contracts](../../tests/unit/evaluation/test_load_test_contracts.py),
[independent load lifecycle review](../../tests/review/test_load_measurement_review.py).

**Remaining tradeoff:** MockTransport and fake clocks establish the accounting
contract only. They do not measure service capacity, answer quality, provider
latency or cost. The legacy development identity headers remain unchanged; these
utilities are not production-authentication clients.

## Reproduce a small offline walkthrough

With the documented development and optional-framework dependencies already
installed, run from the repository root:

```bash
OTEL_SDK_DISABLED=true RAGAS_DO_NOT_TRACK=true AEGISRAG_REAL_ENDPOINT_ALLOWED=0 \
  python -m pytest --no-cov -q \
  tests/unit/agent/test_persistence_phases.py \
  tests/unit/rag/test_service_token_cancellation.py \
  tests/unit/evaluation/test_benchmark_pipeline.py \
  tests/unit/evaluation/test_load_test_contracts.py
```

These are synthetic functional checks, not an operator load run. Show the actual
exit and output; do not substitute a screenshot or this command for execution
evidence. A current source hash and installed dependency record belong beside the
result. The full delivery report includes retained failures and intermittent
timing observations rather than counting only favorable repeats.

## A defensible interview summary

“I can explain how this project separates model completion, validated answers,
transaction acknowledgement and uncertain side effects, and how failure-injection
tests exercise those boundaries. The implementation preserves provider/framework
ports, while the remaining production-environment gates are explicit.”

Adapt that wording to your actual contribution. The useful discussion is about
the state model, recovery decisions and evidence quality, not an unverified
percentage improvement or a list of framework names.

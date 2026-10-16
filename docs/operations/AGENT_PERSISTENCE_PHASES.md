# Ordinary Agent persistence phase budgets

`AgentRunApplicationService` applies `AGENT_PERSISTENCE_PHASE_TIMEOUT_SECONDS`
(default **5 seconds**, finite, greater than zero, at most **60 seconds**) to
ordinary Agent storage. Programmatic callers can pass
`persistence_phase_timeout_seconds`. The API composition root wires the setting
for both native and LangGraph one-shot/streaming execution.

## Scope and transaction outcomes

- Initial create + commit share one phase deadline. The runtime and `started`
  event begin only after that commit is acknowledged.
- Result update + commit share a new phase deadline; the deadline does not reset
  between update and commit. Runtime-exception failed update + commit follows
  the same rule. Terminal acknowledgement is recorded before auditing.
- Rollback has a finite cooperative phase budget and reports whether cleanup
  succeeded. Cancellation writes are skipped after unsuccessful cleanup.
- Cancellation cleanup remains an owned, awaited task, shielded from repeated
  caller cancellation. Its rollback, cancellation write + commit, and optional
  cancellation audit retain an overall cleanup envelope using this budget.
  Repeated cancellation does not start another persistence operation.
- The budget does not change `AgentRunConfig.timeout_seconds`, durable execution
  deadlines, runtime/Registry fast cancellation, or the auxiliary audit budget.
  Several phases may occur in one request, so this is not a total-request SLA.
- Mandatory persistence never uses the best-effort auxiliary-I/O helper and is
  never detached. The normal write/commit deadlines execute in the current task.

An expired write phase cannot proceed to commit, including when a driver swallows
its timeout cancellation and returns later. A late commit acknowledgement does
not yield an on-time success or a final answer. Storage failure exposes only the
fixed `AGENT_RUN_STORAGE_FAILED` error and safe correlation fields, including a
known run ID. A repository's self-cancelled await while the consumer remains
connected is a storage failure, not a confirmed cancellation receipt. Real
caller cancellation still propagates.

A commit that began but did not acknowledge is **uncertain**. It may already have
applied. The service does not automatically retry it or replace the terminal
result with cancellation. Rollback is cleanup, not proof that an uncertain
commit did not apply. Investigate the known run ID using the established
read/reconciliation workflow; do not blindly repeat execution. An acknowledged
failed/completed terminal is not overwritten when later audit is cancelled. An
acknowledged cancellation remains authoritative if its later audit fails.

## Limits and operational tuning

This is a **cooperative** deadline. It bounds cancellable asynchronous I/O while
the event loop runs; it cannot forcibly terminate a driver that suppresses
cancellation, synchronous blocking code, or process suspension. The service
continues owning resistant mandatory I/O until it returns rather than opening
concurrent cleanup against the same session. Configure appropriate driver and
database I/O timeouts separately; do not infer a hard wall-clock bound from the
application budget. The tests include an explicitly releasable resistant await,
not an indefinitely blocked driver.

Five seconds is an operational default, not a database latency guarantee. Tune
against observed write/commit latency and database timeout settings. Raising it
increases the time spent awaiting a failed dependency; lowering it may produce
more uncertain commits. These tests do not establish remote PostgreSQL behavior.

**Unresolved shared-session limitation:** runtime/Registry fast cancellation is
unchanged. Cancellation-resistant descendants may still outlive those layers and
may retain the shared request session used by mandatory tool recording or other
request work. This change serializes the service's own persistence operations;
it does not solve that separate resource-ownership risk, drain those descendants,
or establish production readiness. See [auxiliary I/O boundaries](AGENT_AUXILIARY_IO.md).

## Offline verification

- `tests/unit/agent/test_persistence_phases.py`: phase stalls, deadline sharing,
  swallowed timeout cancellation, commit acknowledgement uncertainty, normal
  failure/audit interruption, explicit rollback outcomes, repeated cancellation,
  safe errors, setting validation/wiring, and native/LangGraph stream parity
- `tests/integration/storage/test_agent_persistence_phases.py`: disposable SQLite
  flush/rollback and applied-but-unacknowledged commits, independently inspected
  from a fresh session, for both backends

No migration, deployment, durable recovery policy, permission policy, or runtime
cancellation contract changes are part of this work.

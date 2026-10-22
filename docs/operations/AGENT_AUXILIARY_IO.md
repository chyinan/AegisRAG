# Agent auxiliary I/O boundaries

Legacy Agent lifecycle and ServiceToken bridge audit, shared runtime and strict
validator audit, Registry's auxiliary `AuditPort`, and progress sinks have finite
waits. They were already best-effort on ordinary failures; a stall now has the same failure policy and
emits a safe warning. No traceback, exception message, raw tool output or input
is included in these warnings. Registry prompt-projection failure warnings use
only safe correlation fields and the exception class name.

## Budgets and tuning

- `AGENT_AUXILIARY_IO_TIMEOUT_SECONDS` controls the application-wired per-hook
  budget (default **1.0 second**, finite, greater than zero, at most 30 seconds)
- Programmatic callers can set `auxiliary_io_timeout_seconds` on
  `AgentRunApplicationService`, `ServiceTokenToolBridge`, `ToolRegistry`,
  `StrictFinalAnswerValidator`, or `AgentRunConfig`. The composition root passes its setting to the legacy
  service, shared runtime, Registry and strict validator, including the
  ServiceToken bridge and Registry. Durable lifecycle transitions retain their
  independent 0.5 second audit cap; this setting does not alter that policy.
  This operational policy is excluded from durable business snapshots; a resumed run uses the current
  deployment telemetry policy without resetting its persisted execution deadline
- The 1 second default allows multiple remote database round trips while keeping
  stalled telemetry finite. This is not a database latency guarantee. Tune it to
  observed audit latency and delivery needs; monitor safe `*.audit_failed`
  warnings with `audit_error_type=TimeoutError` before relying on audit coverage
- Each timed-out hook gets at most an additional **0.05 seconds** to finish
  cancellation cleanup. External cancellation, including a second cancellation
  during that cleanup, is propagated rather than converted to a telemetry failure
- Pre-dispatch progress uses the smaller of the hook allowance and the remaining
  Agent execution deadline. The deadline is checked again before dispatch
- Runtime validation (including its audit) is still bounded by the original run
  deadline. Outcome progress and failure/terminal audit attempts have their own
  bounded telemetry allowance; they do not reset or extend tool/step execution
  budgets. There can be multiple terminal attempts (validation failure audit,
  runtime terminal audit, and application terminal audit), so their overhead is
  additive rather than a single total request timeout
- Legacy application start and terminal audits are outside the active runtime's
  execution budget. Each adds at most the configured allowance plus cleanup.
  Initial/terminal repository operations have separate [cooperative persistence
  phase budgets](AGENT_PERSISTENCE_PHASES.md); neither setting introduces a
  whole-request database timeout

The timeout and cancellation-grace values bound asynchronous waiting while the
event loop runs. They do not bound cold framework import/construction time,
synchronous blocking custom code, or process suspension. Python cannot forcibly
kill a cancellation-resistant custom sink. After its cleanup grace, its result
is ignored and its late exception is consumed without logging its payload. Such
a sink must use independently owned resources and its own I/O timeouts. The
helper is only for telemetry, never tool execution or required persistence.

## Storage ownership and fail-closed boundaries

The production Agent composition root uses `SessionFactoryAuditPort`: every
auxiliary audit owns a fresh `AsyncSession`, transaction, commit and cleanup.
It never captures the request session used by `AgentRunRepository` or
`ToolCallRepository`. This also applies to the ServiceToken tool bridge's audit
and Registry. [RAG optional audit](RAG_AUXILIARY_AUDIT.md) uses the same owned-session
pattern for already-best-effort streaming Query, Chat and ServiceToken adapter hooks.
Required nonstream Query audit and mandatory Chat persistence stay inline; their
required transaction policies are unchanged.

An inherited tenant context must match the internally constructed `AuditEvent`;
a mismatch is rejected before opening a session. The event tenant is explicitly
scoped through transaction creation for PostgreSQL RLS hooks and restored after
completion/cancellation. SQLite tests verify ownership and context propagation;
they do not establish PostgreSQL RLS or remote database latency behavior.

The mandatory `ToolCallRecorder`, durable journal/ledger, permission checks,
final-answer validation and terminal result persistence are **not** best-effort.
Their failures never turn into a successful answer. No final answer is emitted
before validation and successful terminal persistence. A connected consumer
whose provider self-cancels receives a storage error when cancellation persistence
is uncertain; a caller disconnect still propagates cancellation. Tool dispatch has an
explicit revocable receipt on both schedulers, so a pre-dispatch dependency that
suppresses cancellation cannot start a handler after the caller timed out or
disconnected. This is not a process-kill or exactly-once guarantee for a handler
that had already begun.

## Offline verification

`tests/unit/agent/test_auxiliary_io.py` covers bounded stalls, external/repeated
cancellation, late exception privacy, both schedulers, the pre-dispatch fence,
and required recorder failures/stalls. Existing Agent lifecycle and durable
regressions remain applicable. Separate SQLite isolation tests exercise the real
session/transaction boundary; no live provider/database acceptance is implied.

## Task-owned required I/O

The Agent composition root now also isolates cancellable retrieval, mandatory
ToolCall recording and durable evidence revalidation using per-operation session
factories. The task doing the work retains its own session through late cleanup;
request terminal persistence can use or close its separate session safely.
See [Agent task-owned SQL sessions](AGENT_SESSION_LIFETIMES.md) for the ownership
contract, normal SQLite verification and remaining native-I/O limitations.

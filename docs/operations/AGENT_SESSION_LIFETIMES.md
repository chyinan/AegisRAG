# Agent task-owned SQL sessions

The ordinary Agent, durable Agent and ServiceToken tool bridge construct their
retrieval tools from a session factory. Each retrieval invocation owns a fresh
session for its entire pipeline, including hydration and retrieval logging.
Each mandatory tool-call write and bridge lookup owns a separate transaction;
each durable evidence revalidation owns its own read session. The durable service
does not keep an API request session open while an approval is waiting.

The task performing the I/O owns the session's context manager. If a custom
integration needs finite cleanup after cancellation, its session remains owned
by that task until it really exits. A timed-out handler cannot reuse the request
session which writes the Agent's terminal result or has already closed. Required
tool-call writes still commit or propagate their failure; they are never treated
as optional telemetry. Plain Query/Chat mandatory persistence keeps its existing
transaction policy. Optional audits keep their independent owned-session policy.

The default adapters return DTOs rather than ORM objects. Built-in retrieval
already serializes its SQL branches when they share a session. The new adapters
do not change authorization, cancellation deadlines, the journal, or terminal
result validation. Background discovery and execution enter the configured
owner's tenant context before their short-lived SQL transactions; fresh trusted
authentication is still required separately.

## Verification

The focused normal-input tests cover independent retrieval invocations, failure
cleanup, a finite cancellation-resistant handler, required recorder propagation,
evidence ownership, and ordinary/durable/ServiceToken composition. A file-backed
SQLite integration exercises an independently committed timeout ToolCall, the
request's terminal commit, request closure, and the late retrieval's own read.

```bash
python -m pytest --no-cov \
  tests/unit/agent/test_session_scoped_io.py \
  tests/integration/storage/test_agent_owned_session_lifecycle.py \
  tests/unit/api/test_auxiliary_audit_composition.py
```

These checks establish local ownership contracts. PostgreSQL/RLS, real Redis,
remote provider cancellation, pool sizing and deployed workload acceptance are
separate gates. Python cannot kill an infinitely cancellation-resistant custom
integration: native I/O timeouts and supervised worker processes remain required.

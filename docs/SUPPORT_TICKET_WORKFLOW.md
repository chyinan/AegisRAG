# Approval-governed internal support tickets

This opt-in business workflow records a support ticket in the application's own
SQL database. It sends no email, calls no helpdesk vendor and does not promise a
human response. The default Agent catalog remains `rag_search`, `calculator` and
`file_reader`. There is no arbitrary DAG or MultiAgent execution surface.

## Enable and authorize

1. Run `alembic upgrade head` through migration `20261016_0021`.
2. Set `SUPPORT_TICKET_ENABLED=true` and `AGENT_DURABLE_ENABLED=true`. Both default
   to false. Use the normal authenticated `/agent/durable` API.
3. Grant the owner `agent:run`, `agent:tool:create_support_ticket` and
   `support:ticket:read` through the deployment's normal identity configuration.
4. A support manager needs `support:ticket:manage` to advance a ticket's status.
   Permission strings are checked by the application; a role name or model text
   does not grant permission.

The opt-in tool uses the deployment's configured tool timeout and rate limit.
The same governed registry serves both native and LangGraph orchestration. The
ordinary synchronous Agent route cannot supply the approval capability required
to execute this mutation; creation must use the durable approval workflow.

## Version-one tool contract

`create_support_ticket` has `implementation_version="1"`,
`requires_approval=True` and `durable_replay_policy="never"`. Its immutable Pydantic
input schema forbids extra fields:

```json
{
  "client_reference": "47f9901f-753e-4ac8-a258-bf77c13a9c42",
  "title": "Workstation issue",
  "description": "Please investigate the workstation.",
  "priority": "normal"
}
```

`client_reference` must be a UUID v4. Reuse it for the same logical request;
generate a new one for a different ticket. Title is 1–200 characters and
description is 1–4000 characters after normalizing whitespace at the edges;
whitespace-only fields are invalid. Priority is `low`, `normal` or `high`.
Incompatible schema or behavior changes must increment the implementation
version. Existing durable approval fingerprints freeze the tool definition and
arguments before dispatch.

The owner and tenant come from the current authenticated request. Neither is a
tool argument. A unique SQL index over tenant, owner and client reference enforces
idempotency. An identical repeated request returns the same ticket, including its
current status; a changed payload using the same reference returns
`SUPPORT_TICKET_REFERENCE_CONFLICT`. Ticket insertion and its creation audit are
committed together. A repeated identical create does not add a creation audit.

## Approval and lifecycle APIs

1. Start `POST /agent/durable` with the user's request. When the model proposes
   `create_support_ticket`, the runtime automatically enters `awaiting_approval`.
   The client does not need to add the tool to `approval_tools`. No ticket exists
   before approval.
2. Review the exact proposed arguments in the returned approval request. Submit
   `POST /agent/durable/{run_id}/resume` with `expected_revision`, the returned
   `approval_id`, and `approve: true`. The runtime rechecks the current identity
   and tool permission and executes through ToolRegistry.
3. Successful creation returns a bounded tool receipt with `ticket_id` and
   `status`. Only these two keys are projected to the model or restored from a
   durable observation. The receipt describes status at execution time. It does
   not query or assert the ticket's later lifecycle status.
4. The owner can call `GET /support-tickets?limit=50` (1–100) or
   `GET /support-tickets/{ticket_id}` for current state. Both use the authenticated
   tenant and owner in the database query. No arbitrary owner filter is accepted.
5. A same-tenant authorized support manager can inspect
   `GET /support-tickets/manage?status=open&limit=50` and optionally filter by
   `ticket_id` to obtain current records and revisions. This management read
   requires `support:ticket:manage` and records a safe count-only audit.
   The manager can call `PATCH /support-tickets/{ticket_id}/status` with
   `{"expected_revision":1,"status":"in_progress"}`, followed by
   `{"expected_revision":2,"status":"resolved"}`. Only
   `open → in_progress → resolved` is allowed. A revision-checked SQL update and
   status-change audit share one transaction. Stale or invalid transitions return
   `SUPPORT_TICKET_TRANSITION_CONFLICT`.

API responses use the standard request-id envelope. Ticket records contain
private title/description fields for authorized read/status APIs; those fields
are excluded from tool results, prompt projections and audit metadata. Normal
application database/durable-state backup, retention and access controls apply
to stored descriptions and approval arguments.

A tool outcome that becomes uncertain is not automatically dispatched again:
`never` replay preserves the durable intervention behavior. An operator can use
the owner's normal ticket list to reconcile a result and, when appropriate,
request another explicit approval with the same client reference. Application
idempotency makes that operation return the existing ticket.

Every ticket operation owns its SQL session from the configured session factory
until that operation exits, including cancellation cleanup. It never retains a
request-owned session for cancellable tool execution. PostgreSQL migration and
metadata include tenant row-level security; SQLite is supported for local tests.

## Verification

```bash
python -m pytest -q --no-cov tests/unit/support_ticket tests/integration/support_ticket
```

The normal tests use local SQLite and fake decisions, with no paid providers or
external service calls. They cover the frozen contract and disabled default,
persisted idempotency, atomic lifecycle audits, and native/LangGraph ASGI flows:
pause before any ticket exists, explicit owner approval, exactly one ticket,
creation receipt, current-status reads, a second approved request returning the
same ticket, and manager completion of both status transitions.

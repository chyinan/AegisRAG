# Durable Agent workbench

The Agent Runs surface keeps one-shot execution as its default. Durable execution,
active-time accounting, and background dispatch are explicit choices. The page
never changes server configuration or starts a worker.

## Start and review

1. Select **Durable approval and recovery** in the execution-mode picker.
2. Keep **Wall clock** and **Inline request** for the existing behavior: execution,
   approval waiting, and downtime share the original timeout, at most 120 seconds.
3. Select **Active execution** to request up to 3,600 active seconds and a separate
   approval timeout of up to 604,800 seconds (7 days). The workbench defaults the
   approval timeout to 3,600 seconds. Queued time and explicit approval waiting are
   excluded from the active execution budget.
4. Optionally select **Background worker**. This requires operator setup described
   below. A returned running snapshot means queued or running, not completed.
5. Start once. Copy the run ID after a response; if the start outcome is unknown,
   retain the startup recovery key and read by that key instead of repeating a
   possible side effect.
6. Use **Refresh run** to read progress. A read never resumes or approves work.
   Background runs have no manual **Resume run** action in the workbench. For active
   budgets longer than 120 seconds, prefer a configured background worker; the
   workbench recommends this without changing the selected dispatch mode.
7. If approval is requested, inspect the exact tool arguments, action and definition
   digests, revision, and approval expiry. Approve or reject explicitly. The page
   reads the latest state before sending a decision; changes require another review.

The server enforces approval for a configured `create_support_ticket` tool. The
workbench displays this mandatory policy but does not add an unregistered tool
name to the optional calculator/rag-search approval list. Ticket receipts are
limited by the backend to ticket ID and status. Reading, refreshing, or selecting
background dispatch never grants approval.

## Read and recover

Existing active/background runs created through the API can be opened by ID even
when the local form still has the default options selected. The verified response
determines the displayed budget and dispatch mode.

- Older responses without new mode fields retain wall-clock/inline semantics
- Active-time counters are server-checkpointed values; the page does not count
  them down or reset spent active seconds, decisions, or tool calls on refresh
- In active mode, the execution-window checkpoint is not the approval expiry.
  A long approval remains actionable after that checkpoint only while its own
  expiry and remaining active budget are valid
- Stop waiting is local cancellation of the request. It does not confirm that a
  durable server run was cancelled. Refresh or recover before another decision
- An unsuccessful refresh makes the existing view stale and removes any final
  answer from the freshly verified view until another successful read
- Changing the current login aborts in-flight requests and clears the local view
- No private run input, arguments, recovery key, or result is saved in browser storage

## Background deployment boundary

`AGENT_DURABLE_BACKGROUND_ENABLED` is false by default. An operator must deliberately
enable durable storage/background dispatch and install a trusted current-identity
resolver and worker. The resolver must supply a fresh authorized context for each
dispatch. The UI option is not proof that this deployment is ready.

An unconfigured start exposes `DURABLE_BACKGROUND_UNCONFIGURED` without retrying or
silently switching to inline execution. This documented pre-start rejection can
clear the local startup key; an error while reconciling an earlier operation does
not prove that earlier operation failed.

Long inline active requests require suitable HTTP timeouts throughout the path.
The workbench bounds a long active inline wait by its execution budget plus request
overhead. The dedicated `/api/execution` proxy applies finite execution-specific
timeouts, up to 3,615 seconds for long active inline execution. Its authenticated
resume lookup derives the bound from the existing run. Read, recovery, cancellation,
and unrelated backend routes retain their ordinary proxy timeout. Operators must
also configure any upstream ingress, reverse proxy, hosting platform, and API
timeouts. This does not constitute an observed hour-long execution or background
worker deployment validation.

## Offline validation

With the repository's dependencies already available, run focused Vitest tests for
`agent-panel-durable-modes`, `agent-durable-modes`, `agent-workbench-state`, and
`use-agent-workbench`, followed by the normal frontend typecheck and lint commands.
These tests use mocked HTTP responses and ordinary DOM/state checks. They do not
contact a ticket provider, start a real background worker, or constitute real-browser
verification.

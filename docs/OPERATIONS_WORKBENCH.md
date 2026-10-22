# Workbench operations

The existing **Knowledge Base** and **Review** surfaces now expose the opt-in
product workflows. Server configuration and permissions remain authoritative.
A visible control does not enable a feature, start a scheduler, change a role,
or install a background identity resolver.

## Knowledge Base

Both new panels require `document:manage`.

### Folder synchronization

1. Refresh sync plans, or save a plan for the server-configured folder root.
   Configure interval, batch size, scheduling and missing-file candidate collection.
   The browser cannot choose an arbitrary filesystem root.
2. Run one bounded batch or let the separately configured scheduler submit due
   batches. Read the returned cycle, phase, counters, next-run time and error code.
   “Imported” means accepted for ingestion; document version status determines
   downstream retrieval readiness.
3. Load deletion candidates and page through them. Select exact documents, review
   the plan ID, revision and selected IDs, then explicitly confirm soft deletion.
   Scans and the scheduler never confirm deletion. A changed revision requires
   a new read and review.
4. Refresh after an uncertain response. A timeout does not establish whether a
   batch checkpoint or selected deletion committed.

See [Folder synchronization](FOLDER_SYNC.md) for configuration and source retention.

### Queue operations

Refresh failed/stale/stuck deliveries and follow the live pagination cursor.
Review the delivery, domain attempt count, queue status and remaining retries.
Retry consumes existing RQ retry budget; it does not replenish attempts. An
exhausted or semantic-terminal failure cannot be revived by this control.

Termination of a started job returns **stop_requested**, which is not confirmed
termination. Refresh and review the stopped/failed delivery before confirming its
terminal domain state. Mutations clear the old actionable target; conflicts and
uncertain results require refresh. See [Queue operations](QUEUE_OPERATIONS.md).

## Review

Review navigation is available when at least one review, cost or support panel is
permitted. Each panel independently checks its own permission; access to one does
not grant access to another.

### Costs and budget

Requires `cost:read` or `audit:read`. Refresh summary and current monthly budget;
optional summary dates are timezone-aware ISO timestamps. The budget's displayed
period is independent of the summary filter.

Amounts retain their decimal representation and currency. Unknown usage,
unpriced calls, incomplete coverage and retained reservations remain visible.
The priced subtotal is only the known portion when coverage is incomplete.
Missing cost-per-success is shown as unavailable, never zero. Model-call success
cost is not end-to-end answer cost, and reservations are not an invoice hard cap.
See [Usage cost accounting](USAGE_COST_ACCOUNTING.md).

### Support tickets

Owners with `support:ticket:read` can read their stored tickets. Managers with
`support:ticket:manage` can filter the current work queue by status or exact UUID,
review the returned ticket/revision and confirm `open → in_progress → resolved`.
The title and description are authorized ticket content, rendered as text.

Create a ticket through **Agent Runs → Durable approval and recovery**, using
the configured `create_support_ticket` tool. Review and explicitly approve the
exact arguments before creation. The operations panel does not create a ticket
directly. Creation needs the opt-in feature, current tool permission and a
configured decision provider; the default fake Agent does not synthesize business
requests. See [Support ticket workflow](SUPPORT_TICKET_WORKFLOW.md).

## Request lifecycle and verification

Each mounted operation panel owns one in-flight request, suppresses repeated
dispatch and cancels its wait when unmounted or the current authentication scope
changes. Late results cannot populate the new view. Reads and short actions keep
short waits; only explicit folder batch/reconcile paths use the bounded 315-second
execution proxy. Agent execution uses its separate documented path-specific bounds.
Disconnecting is not a server cancellation receipt. Refresh facts before another
decision when an outcome is uncertain.

The focused DOM/API/hook tests use mocked HTTP and installed offline dependencies.
They cover the normal plan/candidate/confirmation, queue snapshot, cost completeness,
ticket processing and navigation journeys. Typecheck and production build are
separate checks. No real-browser, actual proxy socket, remote service or deployed
scheduler acceptance is implied by those tests.

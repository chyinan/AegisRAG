# Durable governed Agent execution

This opt-in API extends the existing sequential Agent runtime with recovery and
explicit tool approval. It is not an arbitrary graph runner or an exactly-once
side-effect system. The default one-shot API remains unchanged.

## Enable only after preparing storage

1. Install the selected framework extras if using LangChain or LangGraph.
2. Review additive migration `20261015_0018`, following the deployment owner's
   approved backup, restore rehearsal and migration process. It follows `0017`
   and adds `durable_agent_runs`, `durable_tool_ledger` and
   `durable_graph_checkpoints`; PostgreSQL tables enforce tenant RLS. Authoring
   this migration does not mean it has been applied or production-validated.
3. Prepare the current release schema through head `20261016_0021` before setting
   `AGENT_DURABLE_ENABLED=true`; durable storage itself was introduced in `0018`.
   The default is `false`; disabled endpoints return `DURABLE_AGENT_DISABLED`.
4. Keep default budgets at or below 20 decisions, 20 tool calls and 120 seconds.
   Set `AGENT_DURABLE_RETENTION_SECONDS` (default 86,400; allowed 120–604,800) and
   `AGENT_DURABLE_LEASE_SECONDS` (default 30; allowed 3–60) deliberately.

The [read-only upgrade preflight](UPGRADE_CHECKLIST.md) now explicitly recognizes
reviewed revisions `0016` through `0021`, requires repository head
`20261016_0021`, uses revision-aware
RLS table sets, and checks durable schema/aggregate state without reading private
payloads. It does not apply migrations, grant access or certify a restore.
PostgreSQL recovery acceptance remains an always-unverified manual gate.
Downgrading `0018` drops private recovery state and is destructive; no automatic
downgrade is provided.

## API workflow

All routes require ordinary authenticated application context with `agent:run`.
Runs belong to one tenant and user. No API accepts a substitute owner, restored
token, framework callback or caller-supplied checkpoint payload.

| Operation | Route | Request |
| --- | --- | --- |
| Start | `POST /agent/durable` | Existing Agent input/budget fields, optional `approval_tools` and UUIDv4 `idempotency_key` |
| Recover a lost start response | `POST /agent/durable/recover` | `idempotency_key` only; read-only operation |
| Read | `GET /agent/durable/{run_id}` | No body |
| Resume / approve | `POST /agent/durable/{run_id}/resume` | `expected_revision`, optionally paired `approval_id` and `approve` |
| Cancel | `POST /agent/durable/{run_id}/cancel` | `expected_revision` |

For example, start with the JSON body:

```json
{
  "input": "Calculate 2 + 2",
  "idempotency_key": "587629a6-65a2-4db2-8e18-c17d8190c0ac",
  "max_steps": 4,
  "max_tool_calls": 2,
  "timeout_seconds": 120,
  "approval_tools": ["calculator"]
}
```

A response contains `run_id`, `status`, `revision`, consumed budgets and
`deadline_at`. The default wall-clock mode retains its original deadline;
opt-in active mode also reports `budget_mode`, checkpointed `active_seconds_used`
and `active_seconds_remaining`. `awaiting_approval` contains an approval request:
exact tool name and arguments, action and definition digests, opaque approval ID
and expiry. Display this request to the authenticated operator. Approve using the
latest revision and exact approval ID; `approve: false` cancels the run. Unknown,
duplicate or non-exact tool names in `approval_tools` are rejected. A tool's own
`requires_approval` policy cannot be weakened by omitting it from that list.

Read again after a conflict; never automatically turn a stale approval into a
new approval. A successful explicit resume does not reset any budget. Inline
execution remains the default; an explicitly configured polling worker can run
opt-in background work, as described below. There is no durable SSE replay API.
`POST /agent/run/stream` separately
streams the one-shot lifecycle, cancels on disconnect and does not accept a
resume cursor.

## Active runtime and independently bounded approval waits

Existing requests and old schema-1 snapshots retain `budget_mode: "wall_clock"`:
at most 120 seconds, with approval waiting and process downtime included. Opt in
per new run to use a separate active runtime allowance:

```json
{
  "input": "Calculate 2 + 2",
  "budget_mode": "active",
  "timeout_seconds": 1800,
  "approval_timeout_seconds": 3600,
  "approval_tools": ["calculator"]
}
```

Active mode permits at most 3,600 active seconds and still permits at most 20
decisions and 20 tool calls. The original configured budgets, spent attempts,
tool calls and repeated-action counts remain authoritative on every resume.
Active elapsed time is checkpointed transactionally with business state.
Approval waiting and unclaimed background-queue time do not consume it. On a
fresh process acquiring interrupted work, the unfinished active window is
conservatively charged through the previous persisted lease expiry; later
downtime is excluded. This can overcharge up to one lease interval after a
crash, but cannot silently refund a previously active interval. Running work
still has a reconstructed bounded runtime deadline and lease supervision.

An active-mode approval expires after `approval_timeout_seconds` (default 3,600,
maximum 604,800), capped by the run's original private-data retention expiry.
Waiting never extends retention. `approval.expires_at` is the approval deadline;
`deadline_at` in active mode describes the current execution window and advances
when a fresh active window starts. Clients must not apply wall-clock-mode
approval-expiry logic to that field. Spent active time is reported at the latest
persisted checkpoint, rather than as a continuously updated stopwatch. Reading,
duplicate starts and recovery never restart execution or change either budget.

The active-budget extension is optional in schema 1. New readers accept old
snapshots without it and continue their absolute-deadline semantics. Default
unkeyed writes preserve their old payload shape, and default keyed-command
fingerprints remain unchanged. Older readers reject the added extension; deploy
compatible readers and workers before enabling these command options, and drain
extended snapshots before rolling back to an older reader. No migration is
needed beyond the existing durable-storage migration.

## Opt-in database-backed background worker

`execution_mode: "background"` persists an owned run before returning, without
performing a model decision or tool call. This option fails with
`DURABLE_BACKGROUND_UNCONFIGURED` unless the application explicitly enables
`AGENT_DURABLE_BACKGROUND_ENABLED`. Inline behavior remains the default.
Use active budget mode when queue waiting must not spend the runtime allowance.
The ordinary authenticated read/cancel endpoints continue to apply.

The database is the durable queue: only opted-in `running` rows with an absent or
expired lease are discovered. A revision CAS and lease claim still select a
single winner. Paused approval rows are not selected by the worker. The operator
approves the exact action through the ordinary authenticated resume endpoint;
for background work it durably queues the approved continuation and returns.
The worker never creates an approval decision. A fresh worker process can then
complete the continuation without keeping the approval HTTP request open.

An operator must install a trusted application factory and supervise the worker:

```bash
python -m apps.worker.durable_agent \
  --factory your_application.durable_worker:create_scheduler --poll-seconds 5
# A scheduled one-shot invocation may add --once.
```

The factory is an async context manager yielding
`packages.agent.durable.scheduler.DurableBackgroundScheduler`. It owns database
engine lifetime and constructs the normal Provider/Registry/evidence service,
an explicit list of `DurableWorkerOwner(tenant_id, user_id)` polling scopes,
and `resolve_context(owner, run_id)`. It must enable `background_enabled=True`
on its service and supply `context_revalidator`; scheduler construction and
dispatch fail closed without current-context revalidation. This is an explicit
operator-installed integration contract, not a preconfigured daemon or a token
refresh service. The factory import path is trusted operator configuration and
must never come from a request, snapshot or queue payload.

The resolver must obtain currently authorized application context from the
deployment's authoritative identity boundary on every dispatch. A scope or
snapshot owner is only a lookup reference, never proof of permission. Failure
or timeout leaves the row queued; the worker does not synthesize permissions,
restore a request token, use a stored bearer credential or silently fall back to
an earlier context. The service revalidates current context, permissions and
evidence during execution through the configured existing boundary. No user
token, refresh token, session secret or resolver callback is persisted in the
snapshot. Deployments without a trustworthy current-context resolver must keep
background mode disabled and use authenticated inline start/resume.

No scheduler process is started automatically and no RQ enqueue is required for
durable Agent jobs. Install the trusted factory and arrange process supervision
before enabling the API switch. Worker health, polling cadence, owner-scope
provisioning, database/RLS acceptance and real provider cancellation behavior
remain deployment acceptance responsibilities. The existing ingestion RQ worker
is independent. A cooperative interruption still follows ordinary bounded
cancellation persistence; an ungraceful process exit leaves leased recovery
state for the next worker after lease expiry.

## Recovering a lost first response

Generate and save a fresh random UUIDv4 **before** sending a start request, and
send it as `idempotency_key`. This is optional: unkeyed starts retain the original
behavior and every request creates a distinct run. A key belongs to the currently
authenticated tenant and user; the same key under another owner resolves to a
different run. It is a recovery reference, never authentication or approval.

If the initial response is lost, send the same original validated start command
and key again, or use the read-only recovery endpoint:

```http
POST /agent/durable/recover
Content-Type: application/json

{"idempotency_key":"587629a6-65a2-4db2-8e18-c17d8190c0ac"}
```

Recovery deliberately uses a POST body rather than a key-bearing GET URL, keeping
raw keys out of ordinary proxy/access-log paths. Do not enable request-body
logging on these endpoints. Durable response envelopes use `Cache-Control:
no-store`, including authentication, validation and business-error responses.

- The same owner, key and canonical validated command returns the current owned
  state. An existing run is resolved before defaults or runtime construction;
  deployment changes cannot replace its original backend, budget or deadline.
- The same owner and key with different validated command fields returns HTTP 409
  `DURABLE_START_KEY_CONFLICT`. The fingerprint covers the normalized input,
  nullable budgets, exact ordered `approval_tools`, sanitized metadata and startup
  protocol version. An omitted budget differs from an explicit budget, even if
  it equals today's default. The key and request/trace IDs are excluded.
- Duplicate starts and recovery **do not execute, resume, acquire a lease, reset a
  budget, refresh an approval or grant consent**. A currently running or
  nonexecuting snapshot may be returned. To continue a recoverable run, explicitly
  resume with its latest revision after the previous lease expires. An approval
  still requires the current exact approval ID and a separate explicit decision.
- Completed results use the ordinary read path, including current authentication,
  tool permission and document evidence revalidation. Recovery does not make a
  cached answer public or bypass revoked access.
- Unknown keys return HTTP 404. Expired private recovery state returns HTTP 410
  `DURABLE_RUN_EXPIRED`, including after the private snapshot has been purged.
  The redacted legacy history row retains only an innocuous keyed-start marker;
  its primary key prevents a concurrent purge/retry from recreating that run.
- Idempotency lasts **only while that legacy tombstone is retained**. Deleting
  legacy history outside this retention policy can make an old key usable again.
  There is no eternal deduplication or exactly-once side-effect promise. Choose a
  new key only when intentionally beginning a new run, especially after an
  ambiguous external tool outcome.

Creation atomically inserts the private snapshot, retained legacy row and a fresh
random initial lease token. Only a confirmed successful insertion may drive
execution, after verifying that exact lease is still unexpired. A competing
request only reads the winner. Timeout/disconnect cleanup can cancel only its own
token, never an unclaimed row or another request's lease. The creation task is
storage-only: a late cancellation-resistant commit cannot launch tools. Bounded
best-effort cleanup runs immediately and again after late storage completion;
if a process exits or storage remains unavailable, recover the existing state and
use explicit resume when appropriate. A lost acknowledgement is not proof that a
commit failed.

## Recovery and approval invariants

- Version-1 business snapshots, consumed counters, repeated-action state and
  tool ledger transitions are transactionally persisted. Model decision attempts
  are reserved before provider I/O; restarting cannot recover spent attempts.
- Default and old snapshots retain the original wall-clock deadline, including
  approval waiting and process downtime. Opt-in active snapshots use the separate
  accounting protocol above. Neither mode resets consumed budgets. Clock
  regression fails closed.
- A revision compare-and-swap and renewable lease serialize competing resumes.
  Cancellation changes the revision and invalidates ownership; a late worker
  cannot overwrite the terminal state. Cooperative request cancellation performs
  bounded best-effort terminal persistence. A database outage can prevent that
  write and is not reported as successful persistence.
- The request supervises the heartbeat and its single business execution together.
  Renewal runs every third of a lease; each renewal I/O is bounded by a third of
  the lease, five seconds, and the original remaining deadline, whichever is
  shortest. A renewal error, timeout or known lease loss immediately revokes
  this process's continuation and cancels cooperative in-flight work. It does
  not invent a persisted cancellation/failure or automatically restart execution.
  A one-second ordinary owned read reconciles a racing terminal commit, including
  current ACL/evidence validation before exposing a completed answer. Every
  completed response uses that bounded read, including completion observed from
  another owner by a business phase or timeout cleanup before the next heartbeat.
  If that read fails or no terminal commit exists, the caller receives a bounded error
  and must read/recover before explicitly resuming. Cleanup waits briefly for
  cooperative tasks and bounds token-fenced lease release to one second;
  external request cancellation remains cancellation even during cleanup. A
  disconnect interrupts a still-cooperative release to attempt cancellation while
  its token remains owned. An already-committed release permanently ends this
  request's cancellation authority: local abort may leave a recoverable
  nonterminal row, with no cancellation receipt. A resistant or unacknowledged
  storage operation must be reconciled from the stored state.
  Python cannot kill cancellation-resistant callbacks or undo external effects.
  Such callbacks may finish outside the request, but revoked continuations cannot
  newly dispatch through the durable journal or commit their late result. An
  already-running storage transaction may still commit; only the stored state
  and fenced ledger establish what happened. A started `never` tool remains
  uncertain and requires intervention on recovery. Cleanup never cancels or
  releases a newer owner's token.
- Current authentication/revocation, tool permissions and document evidence are
  checked again. Approval binds the precise tool definition and arguments; a
  changed tool, stale revision, expired approval or changed evidence cannot reuse
  an old decision. The Registry enforces a one-dispatch approval grant, so direct
  `StructuredTool` or one-shot execution cannot bypass `requires_approval`.
  Tool owners must bump `implementation_version` when handler semantics change;
  executable Python code is not automatically hashed into an approval.
- A completed tool ledger result is reused, not dispatched again. If a process
  dies after dispatch but before result commit, only a tool explicitly declared
  `read_only` may retry, consuming another tool-call budget. A `never` tool moves
  to `intervention_required`; the service will not guess whether a side effect
  occurred. An operator must reconcile external state before choosing a new run.
  A live Registry receipt proving rejection before handler entry is instead a
  known failure. That failure and the completed ledger commit together. A
  cancelled pre-dispatch dependency cannot later launch the handler.
- Built-in `calculator` and `rag_search` support durable read-only execution.
  `file_reader` remains available in the ordinary governed runtime but is
  deliberately unsupported in durable recovery. New tools default to unsupported
  and require a reviewed replay policy and safe recovery projection.
- Only a strictly validated, persisted `completed` result exposes a final answer.
  Reading a completed result revalidates current source evidence and permissions.
  Completed reads use fresh bounded validation rather than extending execution:
  individual read/validation/purge operations have a five-second I/O limit.

Terminal states are `completed`, `failed`, `stopped`, `cancelled` and
`intervention_required`. None can be resumed. `running` may be recovered after
its prior lease expires; `awaiting_approval` requires the matching explicit
approval decision. An HTTP timeout or lost response is not proof that a commit
failed: read the owned run before deciding what to do next.

## Two stores, one authoritative business state

Native and LangGraph scheduling use the same business transitions. The durable
LangGraph path compiles a real `StateGraph` with a purpose-built SQL-backed
`BaseCheckpointSaver` and interrupts before its approval node. Its JSON-only
channels contain an opaque run reference, revision and route. Framework state
never contains the business snapshot, prompts, authentication context, document
excerpts or tool arguments. Checkpoint writes are synchronous and fenced by the
business lease. A graph checkpoint may lag a committed business transition; the
business snapshot and ledger are authoritative when reconciling recovery.
The saver sequences pending writes behind their checkpoint insert, including
when the framework schedules those operations concurrently. It explicitly accepts
the locked Pregel v4 control schema and rejects unknown versions or channels.

This is distinct from the ordinary LangGraph `/agent/run` runtime, whose graph
has no checkpointer. Ordinary Agent history rows alone are not recovery evidence.

## Private data, retention and schema evolution

Durable recovery necessarily stores bounded private user input and tool
arguments in the application database. It does **not** put them in framework
tracing or the public audit trail. Obvious credential-shaped input is rejected,
but that heuristic is not a secret scrubber: callers must never submit credentials.
Snapshots are limited to 256 KiB; tool arguments to 16 KiB; input to 4,000
characters. Raw tool output and transient `prompt_context` are excluded.
Calculator recovery uses a safe numeric projection. Retrieval recovery stores
source references and fingerprints, then rehydrates through current document,
version, chunk, ACL, deletion and content-integrity checks.

Private snapshots, ledgers and graph checkpoints expire together. The bounded,
tenant-scoped store purge removes expired private recovery rows while retaining
redacted legacy Agent history. Purge is opportunistic on starts and expired reads;
there is no automatically installed background retention scheduler. An idle
installation needs an operator-owned scheduled purge if physical deletion by a
specific time is required. Expiry still blocks API access before physical purge.
Database backups need the same private-data retention/access policy.
The private store rejects operations while its SQLAlchemy engine enables SQL
`echo`, INFO or DEBUG logging. Disable those verbose SQL loggers before enabling
durable execution; parameter hiding alone does not suppress DEBUG result rows.

Keyed starts add an optional private `startup` object to snapshot schema 1, with
its own explicit protocol version 1 and SHA-256 canonical-command fingerprint.
Neither the raw key, command fingerprint nor private command is placed in public
legacy metadata, framework checkpoints or audit events. New readers accept old
unkeyed snapshots; new unkeyed writes omit the extension and retain their original
wire shape. Older readers reject keyed snapshots because they forbid unknown
fields. Deploy compatible readers/writers to **all** API and recovery workers
before clients enable keyed starts; do not mix old workers with keyed traffic.
Before rollback, stop keyed traffic and drain/expire/purge keyed private snapshots,
while retaining legacy tombstones. There is no database migration for this
extension. Changing the startup namespace or canonicalization is a protocol change
and requires an explicit compatibility/migration strategy, not a settings edit.

Opt-in active/background starts additionally store the versioned `active_budget`
and/or `dispatch` extensions. These hold timing/accounting and a dispatch mode,
without credentials, serialized authentication context or executable callbacks.

Snapshot schema version is explicit. Unknown or invalid versions fail closed;
there is no automatic migration of old running snapshots. Before changing that
schema, choose and test a migration strategy or drain/expire outstanding runs.
The lifecycle audit records only status, revision, counters and opaque approval
reference; bounded audit delivery is best effort and failures are safely logged.

## Acceptance evidence and remaining deployment gates

Offline acceptance covers both schedulers with real SQLite persistence, real
LangGraph checkpoints, fresh service instances and fresh Python processes.
Fault injection includes hard process exit after dispatch and after completed
result commit, explicit cancellation, competing resume, stale approvals,
permission/evidence changes and failure boundaries around persistence. Separate
independent review tests are included in normal pytest discovery and framework CI.
Keyed-start tests also cover sequential/concurrent duplicates, ownership isolation,
command mismatch and defaults drift, retained tombstones and purge races, losing
request cancellation, ambiguous/late commits, initial-lease expiry, privacy/logging,
explicit approval boundaries, and lost-first-response recovery in fresh Python
processes after initial commit, tool-result commit and final completion.

Run the dedicated suites with the selected extras:

```bash
uv run --frozen --extra langchain --extra langgraph pytest --no-cov \
  tests/unit/agent/test_durable_execution.py \
  tests/unit/agent/test_durable_start_recovery.py \
  tests/integration/api/test_durable_agent_routes.py \
  tests/integration/api/test_durable_start_recovery_routes.py \
  tests/integration/framework/test_process_recovery.py \
  tests/integration/framework/test_keyed_start_process_recovery.py \
  tests/integration/storage/test_agent_stream_persistence.py tests/review
```

Normal positive coverage for the opt-in active/background workflow is available
separately without running the independent review/fault-injection suites:

```bash
python -m pytest --no-cov tests/unit/agent/test_durable_active_background.py
```

These are
synthetic/offline correctness tests, not live-provider quality, PostgreSQL/RLS
concurrency, failover, throughput or production readiness evidence. Production
acceptance still needs an isolated PostgreSQL migration/RLS/concurrency rehearsal,
real endpoint compatibility, cancellation-responsive provider and database
clients, bounded operational timeouts, backup/restore and retention scheduling.
No adapter claims all providers, arbitrary tools or all framework features work.

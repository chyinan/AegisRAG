# Governed Agent workbench

The main workbench's **Agent Runs** surface requires the existing `agent:run`
permission. The backend remains authoritative for tenant/user ownership,
revocation, tool permissions, evidence validation and execution budgets. The
frontend adds no permission, provider credential or server configuration.

## Choose a mode deliberately

- **One-shot (default)** retains the existing `POST /agent/run` workflow. The
  final result shows consumed decisions/tool calls and request/trace identifiers.
- **Live progress (SSE)** opts into `POST /agent/run/stream`. The UI displays
  application-owned `started`, `tool_call` and `tool_result` progress. Only a
  validated terminal `final` response can show an answer and source references.
  A terminal `error` or an incomplete connection is not a completed answer.
  There is no automatic reconnect, replay cursor or durable SSE history.
- **Durable approval and recovery** opts into the separate durable endpoints.
  This server feature is disabled by default. A `DURABLE_AGENT_DISABLED` response
  explains that an operator must prepare storage and enable it deliberately;
  selecting the mode does not change configuration or apply migrations.

Review [Durable governed Agent execution](DURABLE_AGENT.md) before enabling the
last mode. In particular, start/resume are bounded synchronous operations, not
background jobs. The original wall-clock deadline includes approval wait time,
process downtime and recovery. A refreshed view does not renew it.

## Approval and recovery journey

1. Enter up to 4,000 characters and choose bounded budgets: 1–20 decisions,
   0–20 tool calls and 1–120 seconds. Do not enter credentials.
2. In durable mode, choose whether to require explicit approval for built-in
   `calculator` and `rag_search` calls. Both review options are selected initially.
   These add requirements; removing one cannot weaken a tool's backend policy.
   The UI does not claim that every registered tool supports durable recovery.
3. Every explicit durable start generates a secure UUID4 startup recovery key
   before dispatch. Copy it while waiting if you may leave the page. The key is
   retained only in memory until a validated run ID arrives, the server explicitly rejects
   the start as disabled, or the reference is explicitly discarded. It is never a credential,
   permission, approval, or alternate identity.
4. For an approval request, review the exact tool name, JSON arguments, approval
   ID, action/definition digests, revision and expiry. Consumed counters and the
   original deadline are displayed separately. A recovered response does not
   contain configured maximum counters, so the UI does not invent them.
5. **Approve exact action**, **Reject and cancel run**, and **Resume run** first
   read current server state. If the displayed status, revision, deadline,
   counters or exact approval differs, no decision is sent. Review the updated
   state and choose again. An unchanged view is submitted with its exact revision
   and, for approval, the paired approval ID/boolean. The server still performs
   the final compare-and-swap and policy checks.
6. A conflict, failed read, aborted mutation or uncertain response invalidates the
   actionable view. **Refresh run** is required before another decision. The
   previous decision is never automatically retried or transferred to a newer
   approval. Expired approvals/deadlines cannot be approved or resumed locally.
7. Copy the run ID before leaving. **Read an existing durable run** accepts an
   explicit opaque ID and makes only a read request; it never resumes execution.
   A `running` snapshot requires explicit recovery after the prior lease expires.
   A nonterminal displayed run must finish or be cancelled before another run is
   started/read in that view. The backend rechecks owner scope on every request.
8. `completed`, `failed`, `stopped`, `cancelled` and `intervention_required` are
   terminal and have no resume control. An unknown side-effect outcome explicitly
   calls for operator reconciliation of external state before a new run.

Completed answers use the existing safe Markdown and source-inspection
components. Agent source references use the backend's minimal `AgentCitationRef`
contract, not the richer RAG citation DTO. Opening a source performs the ordinary
current-auth evidence resolution. A failed refresh hides a previous durable
answer rather than presenting it as freshly authorized.

## Interruptions are not cancellation receipts

**Stop waiting**, navigation away, sign-out, identity/permission change and bearer
replacement abort requests owned by the current panel. Late responses cannot
mutate a newer identity or view. Same-frame repeated actions have a synchronous
single-owner guard. Reads have a 12-second client wait limit; execution/mutation
requests have a 135-second client wait limit, allowing the server's 120-second
maximum execution budget plus bounded completion work. Client expiry is still
an uncertain outcome, never a commit receipt.

For Live mode, closing the reader requests server disconnect cancellation. The
UI explicitly says that persisted server cancellation is unconfirmed. For a
known durable run, **Cancel durable run** submits the current revision and only a
server response with `status=cancelled` confirms cancellation. It does not assume
that interrupted I/O means the database write succeeded.

The start flow receives its run ID with the initial bounded response. If that
response is lost, malformed, timed out, or locally stopped, the startup key
remains visible and copyable. **Read by retained startup key** sends only
`POST /agent/durable/recover` with `{ "idempotency_key": "<UUID4>" }`. It uses the
12-second read budget, never the mutation timeout. A copied key can also be
entered through **Read by saved startup key** after returning. Both paths only
read; they never repeat the start, resume execution, approve a tool, acquire a
lease or reset the original deadline. A recovered approval still requires a
separate exact-read comparison and explicit decision.

A key lookup uses only the currently authenticated tenant/user. A key from another
identity grants no access and normally returns not found; the page never changes
identity to make a key work. HTTP 404 does **not** prove that the original request
cannot commit later. All uncertain reads, including disabled or expired recovery
responses, retain the unresolved key and block new starts in every mode. The
user may explicitly read that same key again. A different run-ID or key cannot
silently replace the unresolved reference.

**Discard local startup reference** requires checking an acknowledgment that
server effects remain unresolved and discarding does not cancel the run. This
only forgets the local reference, permitting a deliberate new start; it sends no
cancellation or other server request. The operator must still reconcile possible
effects before repeating them. There is no automatic start retry/recreation,
recovery polling, resume, or approval. Only the exact initial
`DURABLE_AGENT_DISABLED` factory rejection clears the key: this guard runs before
service construction and cannot create a new run. Status codes such as 401, 403
or 422 alone do not prove non-commit; they can occur during post-commit
revalidation, so those failures retain the key and block another start.

New starts use Web Crypto `randomUUID`, or `getRandomValues` with UUID4 version
and variant bits. Malformed generated keys or unavailable secure randomness
prevent dispatch; there is no `Math.random` fallback. Supplied keys must be UUID4.
The UI forgets keys, inputs and private results and aborts owned requests on
navigation/unmount, permission loss, auth scope changes or bearer replacement.
A successful start/read switches recovery to the returned run ID and releases
the startup key. Explicitly copying a key is user-controlled clipboard use.

## Private data and protocol boundaries

Input, startup keys, recovery IDs, exact approvals, answers and tokens are not
written by this panel to browser storage. Startup keys occur only in explicit
start/recovery request bodies, never URL/query parameters, metadata, logs or
telemetry. Both request paths use `cache: "no-store"`. Server durable private-data
retention remains governed by the durable operations guide. The UI's credential-shaped argument detection
is a fail-closed display safeguard, not a general-purpose secret scrubber.

Approval arguments bypass the generic recursive response redactor through a
narrow, strict durable decoder: silently deleting a field would make the action
shown differ from the action approved. Unknown schema/status, invalid counters,
invalid approval shape, detected credential fields/values, oversized/deep JSON,
and integers that JavaScript cannot represent exactly prevent approval display.
Safe arguments are retained without editing, including literal keys that the
ordinary response redactor suppresses outside an approval request. JSON is
rendered as escaped text and never executed. Errors do not include rejected
payloads or provider exception bodies. Agent JSON responses (one-shot, durable
and non-OK streaming envelopes) are capped at 1 MiB before parsing, decode UTF-8
strictly, and cancel owned readers on abort without awaiting a stalled transport
cancellation. DTO field limits still apply within that transport cap.

The independent Agent SSE reader validates application envelopes, numeric frame
IDs, contiguous sequences, stable request/trace/run context, tool-call pairing,
terminal result identity and bounded frame sizes. One-shot/live final owner IDs
are compared against available current-auth tenant/user claims. It recognizes the
two explicit pre-dispatch budget/repeat rejection results
without treating other orphan results as valid. It handles split UTF-8 and
CRLF chunks and requests reader cancellation on abort, invalid data and consumer
exit without waiting indefinitely for a stalled cancellation promise. Header
and body request ownership still follows the independent client wait deadlines.
Progress is allowlisted; unrequested raw fields do not enter UI state.

## Verification and limits

Deterministic frontend tests consume
`apps/web/src/test/fixtures/agent-lifecycle.json`, produced with real backend DTOs,
`AgentStreamSink`, the Agent SSE serializer and response-envelope helpers. Do not
hand-edit that fixture. Check it with:

```bash
python scripts/generate_web_agent_fixtures.py --check
cd apps/web
npm run test -- --run src/lib/api/agent-contract.test.ts \
  src/lib/api/agent-sse.test.ts src/lib/api/agent-client.test.ts \
  src/lib/api/agent-startup-key.test.ts src/hooks/use-agent-workbench.test.ts \
  src/lib/agent-workbench-state.test.ts src/components/agent-panel.test.tsx
npm run lint
npm run typecheck
npm run test -- --run
npm run build
```

The initial run-ID recovery workbench milestone passed full frontend lint,
TypeScript checking, 258 unit/UI tests across 22 files, and the production build
using the installed `node node_modules/next/dist/bin/next build --webpack` entry point. In this isolated checkout, the default
Turbopack build could not follow a reused `node_modules` symlink outside its
project root. The installed webpack builder passed without changing project
configuration or downloading dependencies. This is a build-environment
limitation, not evidence that the default Turbopack build passed.

The combined startup-key/transport continuation subsequently passed all 335
frontend tests across 27 files, full ESLint and TypeScript checks, and the installed
webpack production build on 2026-10-15. Source hashes were unchanged across those
checks. The earlier focused counts overlap this aggregate and are not additive.

The suites cover actual wire decoding, approvals/revisions, conflicts, terminal
states, expiry, partial streams, duplicate clicks, local abort, late responses,
identity replacement, navigation, bilingual labels and escaped argument display.
Startup-key regressions additionally cover lost/aborted first responses,
pre-dispatch key visibility/copying, explicit read-only recovery, 404 followed by
a late committed result, same-frame guards, invalid UUIDs, unavailable secure
randomness, disabled endpoints, acknowledged local discard, fresh keys for new
starts, 12-second read expiry, private-state cleanup and unchanged one-shot/live
payloads. They use mock network responses and no live provider calls. Passing
these checks is not evidence of production migration, PostgreSQL failover, provider quality,
load performance or a browser-engine accessibility/layout review. Browser-driven
QA was not executed in this environment because browser execution was unavailable
under its access constraints; those restrictions were not bypassed.

Before deployment, manually verify responsive layout and keyboard/screen-reader
behavior in a supported browser, reverse-proxy streaming/disconnect propagation,
real authentication/expiry, and the approved durable storage/retention/RLS gates.
No browser settings, credentials, deployment, migrations, Git commits or external
publication are part of this workbench implementation.

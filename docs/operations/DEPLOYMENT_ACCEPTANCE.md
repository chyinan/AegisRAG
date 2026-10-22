# Executable target-deployment acceptance

`scripts.deployment_acceptance` is an opt-in operator probe for an already running,
isolated **synthetic-data** AegisRAG API. It uses ordinary HTTP and real local
username/password login. It does not start Compose, provision accounts, alter
configuration, migrate a database, forge authentication, or install a provider.

A successful live run establishes a narrow vertical workflow for one identity and
one generated document. It is **not production certification**, a multi-tenant
isolation proof, a load test, a backup/restore test, or evidence of general model
quality. The associated unit tests use `httpx.MockTransport`: passing them proves
the probe's assertions and failure handling, not that any deployment works.

## Before running

1. Follow [the pilot runbook](PILOT_RUNBOOK.md) to prepare an isolated deployment
   with synthetic data only. Verify that the API origin belongs to that deployment
2. Use an existing dedicated local-login account with `document:manage`,
   `document:read`, and `retrieval:query`. The probe creates neither an account nor
   a permission grant. External OIDC-only deployments are outside this probe
3. Keep development authentication headers disabled. API and workers must use the
   same intended database, queue, object store and embedding configuration
4. Review and approve the configured embedding, rewriting, reranking, and LLM
   providers before running. An upload and retrieval/query requests exercise these
   providers and may incur cost or transmit synthetic content to their configured
   endpoints. The HTTP API does not expose an authoritative provider configuration
   attestation; the CLI cannot verify or restrict those server-side endpoints
5. For a no-paid-service run, use approved local/offline providers and prevent
   unintended provider egress at the deployment boundary. The CLI never changes
   provider selection and never chooses a paid service itself

### Default fake LLM limitation

The default `LLM_PROVIDER=fake` returns the fixed text `Fake LLM response.`. The
backend may still attach fallback authorized citations to that text. Neither that
attachment nor an HTTP 200 proves a cited known-answer workflow. This probe
requires the generated answer to contain the unique synthetic code and its actual
canonical `[cite-…]` marker, so the default fake output **cannot pass** the complete
acceptance gate. An authorized/configured local or other approved provider must
produce the grounded answer. Do not weaken the gate, hardcode a success response,
or treat `no_answer=true` as a successful known-answer check.

## Run

From the repository root, with the existing project dependencies installed:

```bash
uv run python -m scripts.deployment_acceptance \
  --base-url http://127.0.0.1:8000 \
  --tenant-id acceptance \
  --username acceptance-operator \
  --confirm-isolated-target
```

The password is requested using a non-echoing terminal prompt. If a safe prompt
cannot be established, the probe refuses to read it. Password command-line
arguments, password environment variables, and piped stdin are not supported.
Do not paste secrets into shell history, chat, screenshots, or report files.

For an authorized unattended run, supply an existing privately mounted UTF-8
password file containing one password line (an optional final LF/CRLF is allowed):

```bash
uv run python -m scripts.deployment_acceptance \
  --base-url https://acceptance-api.example.internal \
  --tenant-id acceptance \
  --username acceptance-operator \
  --password-file /run/secrets/aegis_acceptance_password \
  --confirm-isolated-target \
  --indexing-timeout 180 \
  --stream-timeout 60 \
  --request-timeout 15 \
  --poll-interval 2 > acceptance-result.json
```

Replace the illustrative origin, identity and secret-file path with the actual
approved target. The file must be a regular non-symlink file; on POSIX it must not
be accessible to group/other users (normally mode `0600` or `0400`). The probe does
not change the file's permissions or save the password. Password spaces are
preserved; multiline, NUL-containing and oversized inputs are refused.

`--confirm-isolated-target` acknowledges the synthetic-only target and the
operator's review/approval of its configured providers. It is not a substitute
for permission to use somebody else's deployment, account, or paid services.

Only direct API origins are accepted, without a path, query, fragment or URL
credentials. HTTPS certificate verification remains enabled. Plain HTTP is
limited to loopback IPs or `localhost`. Redirects are never followed, and ambient
proxy and HTTPX environment configuration are ignored (`trust_env=False`). Use a
properly trusted certificate for an HTTPS deployment; the CLI has no insecure TLS
switch. Point it at the API, not the Web workbench or a path-prefixed proxy.

## Assertions and mutation scope

The probe performs this bounded sequence:

1. Read `/health` and `/ready`; require process health and `ready=true`
2. Log in through `/auth/login`; verify tenant, token contract and required
   permissions. Bearer credentials remain in memory; no development headers or
   synthetic authentication tokens are sent to the target
3. Upload a tiny generated TXT file with a random known code. The CLI supplies
   fresh random document/version UUIDs, a random `acceptance_probe_id` metadata
   value, and a Private ACL explicitly allowing only the authenticated uploader
4. Poll `/documents/{document_id}/versions/{version_id}/status` for those exact
   IDs, requiring `retrieval_ready`, positive chunk/vector counts and a ready
   index. Known in-progress/retryable states continue within the deadline;
   terminal/deleted, unknown/malformed states, or wrong IDs fail immediately
5. Retrieve only with that run's metadata filter. Require matching document,
   version and tenant for every returned candidate. There is no unfiltered
   fallback if the filter fails to propagate through ingestion
6. Read `/query/stream` incrementally. Require a nonempty `delta`, real citation
   events, one complete successful final event, `no_answer=false`, no unsupported
   claims, the unique known code, and canonical citation markers bound to the
   uploaded document/version and retrieved chunks. Truncated streams, error
   events, events after final, missing final, forged/mismatched citations, or
   fallback citations without answer markers fail
7. Resolve a final citation via `/sources/resolve` under the same live identity.
   Require the exact document/version/chunk and an excerpt containing this run's
   marker and known code. The response text is never printed or saved
8. Delete the newly generated document through the normal lifecycle API, then
   re-resolve the same citation and re-run the same scoped retrieval. Require a
   structured `SOURCE_ACCESS_DENIED` with no data (HTTP 403/404), and successful
   authenticated retrieval with an empty candidate list. Generic 401/403, random
   404, 5xx, or a response containing source data cannot pass as deletion evidence
9. Log out the acquired session and require `revoked=true`. Logout failure makes
   the run fail even if the document workflow succeeded

Every HTTP call and the total stream/indexing waits are bounded. JSON and SSE
responses are capped at 1 MiB. Mutating requests and model queries are not
transparently retried. There is no database access, document enumeration, account
provisioning, ACL widening or deletion of an existing user-selected document.

## Results, cleanup and failures

The CLI writes one JSON result to stdout. It contains generated run/document/
version IDs, completed check names, a stage and locally defined error code, and
cleanup/logout outcomes. It deliberately excludes server error bodies, passwords,
access/refresh tokens, response excerpts, generated answers and arbitrary server
metadata. Do not enable verbose HTTP/request-body logging around the command.

Exit codes:

- `0`: every workflow assertion, deletion visibility check and logout passed
- `1`: configuration, transport, contract, workflow or cleanup/logout failure
- `2`: invalid arguments, including a missing explicit target confirmation
- `130`: interrupted run

Any failure after attempting upload triggers a bounded cleanup attempt for only
the locally generated document ID, followed by a session-logout attempt. A normal
successful `cleanup=deleted` means the lifecycle API acknowledged soft deletion;
physical object removal, database audit retention and storage reclamation are
separate operational concerns. The `deleted_source_and_retrieval_hidden` completed
check is the additional evidence that this identity cannot read the deleted
source or retrieve the document.

An interrupted/ambiguous upload is never re-uploaded automatically. The report
keeps `upload_outcome_unknown=true` and `cleanup=unconfirmed` if cleanup cannot
establish the full upload outcome, even when deletion was acknowledged. A delayed
server request, a process kill, expired token, lost network, unavailable storage,
or a failed cleanup can leave synthetic artifacts or an active session. Retain
the generated IDs and have the deployment operator reconcile them through normal
authorized tools. Never broaden cleanup to all documents or retry by replacing
authentication. A failed stage remains failed even when cleanup succeeds.

A first Ctrl-C attempts bounded cleanup/logout and exits nonzero. A second
interrupt or process termination can prevent cleanup; use the target's normal
operational reconciliation. The probe is not a background monitor or crash-recovery
service.

## Tests and evidence

```bash
uv run pytest --no-cov tests/unit/operations/test_deployment_acceptance.py
uv run ruff check scripts/deployment_acceptance.py tests/unit/operations/test_deployment_acceptance.py
uv run python -m scripts.deployment_acceptance --help
```

The mocked HTTP tests include backend-serialized citation/SSE DTOs, normal local
login, exact-version polling, scope/identity mismatches, stream fragmentation,
CRLF/UTF-8 boundaries, total deadlines, response-size limits, terminal failures,
known-answer/citation binding, safe deletion denial, cleanup ambiguity/failures,
logout failure, secret-file handling, argument redaction, and redirect refusal.
They use no external model, real account or live infrastructure.

Record the deployed revision, provider choices, execution date and operator's
actual JSON result separately when a live acceptance is performed. A probe added
to source control, a green mocked test suite, or `/ready` alone is not evidence
that Compose, PostgreSQL RLS, Redis workers, storage persistence, the Web browser,
or a real-model deployment has passed acceptance.

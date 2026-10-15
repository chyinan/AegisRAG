# Private-pilot runbook

The supported acceptance target is one tenant's **local login → document upload → indexing → streamed answer → authorized source excerpt** workflow. This is a pre-1.0 pilot, not a production-readiness certification. Use synthetic documents until the deployment owner has verified isolation, backups, model quality and operational controls in the actual environment.

## 1. Prerequisites and configuration

Use Docker Engine/Desktop with Compose v2, adequate space for the Python/Torch images, and an interactive terminal. Run commands from the repository root. Do not reuse an existing production database for a first-boot test.

**Object-storage maintenance boundary (checked 2026-10-14):** the [MinIO community repository](https://github.com/minio/minio) is archived and states that it is no longer maintained; legacy binaries do not receive updates. The existing Compose MinIO service is retained for isolated synthetic-data development, not endorsed for a new real-data deployment. Before that deployment, select a maintained S3-compatible service, review its support/licensing requirements, and verify upload/read/delete/backup behavior with the application's storage adapter. This patch does not silently change storage vendors or accept a new license.

1. Copy `.env.example` to `.env` and keep it out of Git and screenshots
2. Replace the PostgreSQL bootstrap, migration and runtime passwords with distinct strong values; keep their three usernames distinct
3. Set MinIO access/secret keys, `JWT_SECRET` (at least 32 random bytes), `FOLDER_CURSOR_SECRET` and `GRAFANA_ADMIN_PASSWORD`; use a password manager or secret manager
4. Keep `ENABLE_DEV_AUTH_HEADERS=false` and `JWT_REQUIRE_LOCAL_SESSION=true`
5. Leave `JWT_JWKS_URL`, `JWT_ISSUER` and `JWT_AUDIENCE` blank for the local-login pilot. Local login does not issue issuer/audience claims; configuring those validators would reject its tokens on protected requests. Nonblank issuer/audience values belong to the separately configured external OIDC/JWKS path
6. Start with `LLM_PROVIDER=fake`, `EMBEDDING_PROVIDER=fake`, `RERANK_PROVIDER=fake`, and matching `EMBEDDING_DIM=8` / `VECTOR_INDEX_DIM=8`. Fake providers verify plumbing only; their answer is not evidence of useful model quality
7. Blank `LLM_MAX_OUTPUT_TOKENS` and `LLM_TEMPERATURE` mean “use the provider default.” Nonblank malformed numeric values fail validation

Use URL-safe database passwords or percent-encode their password component when constructing `DATABASE_URL`. Compose constructs its database URLs from the separate role variables. Container endpoints use service names (`postgres`, `redis`, `minio`), not `localhost`.

The Compose stack publishes several ports for local development. Restrict access to a trusted machine/network. TLS termination, ingress policy, external identity setup and Internet-facing deployment are separate operational work.

Check configuration without printing expanded secrets:

```bash
docker compose --env-file .env -f docker/compose.yaml config --quiet
```

## 2. Start the stack and initialize the first administrator

```bash
docker compose --env-file .env -f docker/compose.yaml up -d --build
docker compose --env-file .env -f docker/compose.yaml ps -a
```

The dependency graph runs `postgres-roles` before migrations. API and workers use the separate runtime role; migrations use the migration role. `minio-init` creates the bucket idempotently. Confirm the role/migration/init one-shot containers exited successfully, and the API/Web dependencies are healthy.

Bootstrap is an explicit, opt-in operation, never an API startup side effect:

```bash
docker compose --env-file .env -f docker/compose.yaml --profile bootstrap run --rm bootstrap \
  --tenant-id default --tenant-name "Default Tenant" --admin-username admin
```

Enter a password at the hidden prompt and confirm it. The bootstrap minimum is 12 characters and maximum is 72 UTF-8 bytes, plus the configured password policy. Do not pass passwords in command-line arguments, environment variables or chat. For automation, `--password-file /mounted/secret` reads a separately mounted UTF-8 secret file containing one password line; the file must actually be available inside the container. The command does not print the password.

- On a fresh schema, the migration-created empty `default` tenant can be initialized using the exact name `Default Tenant`
- A new tenant can be initialized with a new tenant ID/name
- Repeating the same command verifies the original bootstrap identity and leaves its password, roles and permissions unchanged; the supplied password is not a reset request
- Existing unrelated users/groups/roles, changed identity markers or disabled/modified bootstrap identities cause `AUTH_BOOTSTRAP_CONFLICT`. Use the normal administration workflow instead of deleting data or forcing bootstrap
- Bootstrap owns one database transaction. A failed operation rolls back its changes

Open `http://localhost:3100` and use tenant `default`, username `admin`, and the password just entered. The workbench currently supports local username/password login. Pasting an external JWT is disabled because the UI does not yet hydrate a verified enterprise identity and its permissions. OIDC/JWKS API support is a separate integration path; configuring it disables local login.

The older `packages.auth.seed` command creates synthetic demo identities and prints generated passwords. It is not the pilot bootstrap path and should not be run after initializing a real tenant.

## 3. Verify the vertical workflow

### Health and schema

```bash
curl --fail http://localhost:8000/health
curl --fail http://localhost:8000/ready
docker compose --env-file .env -f docker/compose.yaml run --rm migration
```

`/health` is process liveness. `/ready` checks configured dependencies, but does not prove that workers are draining queues or that a model/index yields correct answers. Verify the document workflow below even when both are green.

### Upload and indexing

1. In Knowledge Base, upload a small Markdown or TXT file containing a known fact. Existing PDF/DOCX parsers are covered separately by regressions; use searchable text first
2. Choose Tenant (visible within that tenant) or Private (uploader-only under the backend ACL contract). Department is intentionally unavailable; it is not silently widened to Tenant
3. Record the returned `document_id`, `version_id` and `job_id`. The list refreshes after acceptance
4. Wait for the exact version to become `retrieval_ready`. “Uploaded” means accepted, not searchable
5. Repeat using Quick import and confirm the same list/status behavior

Live status access requires `document:manage`. Uploaders without that permission see acceptance IDs and a clear permission notice; the UI does not attempt a forbidden status read. Status shows attempts, next retry time, safe error code and request/trace IDs when supplied by the backend.

The UI checks status; it does not enqueue retries. Active states include parsing, chunking, embedding and indexing. `failed_retryable` continues to be checked, while `failed_terminal`, `deleted` and `retrieval_ready` stop polling. A status request times out after 15 seconds; only transient read failures receive up to three retries with backoff. Exhaustion pauses checks and offers Refresh status. Leaving the view cancels reads. A polling pause does not mean the worker stopped.

RQ workers run with their scheduler enabled for delayed retries. `INGESTION_QUEUE_NAME` and `EMBEDDING_QUEUE_NAME` configure both producers and Compose consumers. Do not run a worker against an obsolete hardcoded queue name. Retry scheduling, retry exhaustion and restart recovery still need a live isolated Redis test in the target environment.

Handled ingestion/embedding failures use the current RQ retry budget and whole-second schedule for `next_retry_at`; the final owned failed attempt becomes `failed_terminal` with no further retry deadline. The status response reflects the embedding job phase while retaining the chunked checkpoint needed for a valid retry. Killed workers, process crashes/OOM and failures before the service starts still require operational reconciliation; this is not a general crash-recovery guarantee.

After chunks and their embedding job commit, an ingestion redelivery replays that same queued embedding job without reading, parsing or chunking the document again. Deterministic queue IDs also deduplicate a delivery whose acknowledgement was lost. Deleted work is not replayed, and started or failed embedding jobs retain their own status and retry budget. This recovery is bounded by the original ingestion RQ retry budget: a sustained queue outage that exhausts it still requires operator reconciliation; there is no unbounded outbox sweeper. The disposable PostgreSQL gate includes Redis/RQ and the production embedding worker for before-acceptance and lost-acknowledgement faults, using synthetic source storage and a fake provider.

Queue IDs now use a versioned, deterministic SHA-256 encoding of job type, tenant and resource. The pinned RQ 2.9 rejects the former colon-delimited IDs before enqueueing. This change does not adopt, migrate or requeue legacy Redis keys. If upgrading an older-RQ deployment that has such jobs, explicitly drain or reconcile them before switching formats. Likewise, drain or reconcile populated queues before renaming them; a queue-name change is not a transparent job migration.

### Ask and inspect evidence

1. Ask a question answerable from the uploaded document
2. Verify incremental `delta` text appears before the final event and copying is locked until a successful final
3. Open a citation and verify the document/version/chunk IDs and authorized excerpt
4. Ask an unsupported question: a successful `no_answer=true` must show an explicit no-answer state, not fabricated evidence
5. Check a failed/interrupted response: the safe error and request ID should be visible, the incomplete answer must not become copyable, and another question should be possible
6. Start a new conversation or leave Ask while streaming; an old request must not overwrite the newer view. Failed assistant history retains an `error_code` and remains a failed answer when restored

The server remains authoritative for authorization. Browser mocks test state handling and DTO compatibility; they do not validate a real tenant's ACL/RLS enforcement.

## 4. Enable real providers deliberately

After infrastructure acceptance, configure approved endpoints in `.env`:

- OpenAI-compatible LLM: `LLM_PROVIDER=openai_compatible`, `LLM_MODEL`, `LLM_BASE_URL`, and the endpoint's required `LLM_API_KEY`
- OpenAI-compatible embeddings: `EMBEDDING_PROVIDER=openai_compatible`, `EMBEDDING_MODEL`, `EMBEDDING_BASE_URL`, and any required key
- Ollama embeddings: `EMBEDDING_PROVIDER=ollama`, a pulled embedding model such as `nomic-embed-text`, and its compatible `/v1` endpoint
- On Docker Desktop, a host service commonly uses `http://host.docker.internal:11434/v1`; on Linux configure a reachable gateway/network explicitly. Inside the API container, `localhost` is the API container itself
- Keep `EMBEDDING_DIM` equal to `VECTOR_INDEX_DIM`. A model/dimension change requires a planned reindex; do not reuse an old index as if it were compatible
- Native adapters remain the default. Optional LangChain/LangGraph selections also need the matching installed Python extras; setting an env variable alone does not install them

Restart affected API/workers after configuration changes and repeat the small known-answer/source check. Evaluate approved real-model outputs separately. The deterministic 20/224-case gates and synthetic browser fixtures do not establish real-model quality, throughput or cost.

### Vector schema upgrades

Revision `20261014_0017` removes the historical fixed `vector(1536)` column limit, preserving existing float32 vector values. A database constraint requires each stored vector's actual dimension to match its positive `embedding_dim`. Searches guard distance evaluation by both dimensions and retain tenant, ACL, model/version and metadata filters. This permits the documented 8-dimensional fake pilot and separately configured dimensions; it does not convert old embeddings into a different model or mix their scores into one search.

There is no automatically provisioned HNSW/ANN index in this pilot. The `PGVECTOR_INDEX_TYPE`/HNSW settings are not proof that such an index exists. Index design, per-model/dimension indexing and performance acceptance require separate work against representative data.

Before applying migrations to any existing deployment, take and verify a backup and plan a maintenance window for table locks and constraint validation. Downgrading `0017` is allowed only when **every** vector is 1536-dimensional, including other tenants' and soft-deleted rows. Otherwise the downgrade refuses and the transaction retains the current schema/data/revision; do not delete or reinterpret vectors to force it through. The migration tests use isolated synthetic databases, not a user's deployment.

## 5. Host development (explicit dotenv loading)

All settings and authentication modules read process environment. Merely copying `.env` does not load it into Python. Use `uv run --env-file …` consistently.

Keep an ignored `.env.dev` with **host** endpoints (`127.0.0.1` and published ports), the runtime database credential, and matching auth/provider settings. Keep migration credentials in a separate ignored `.env.migration`; do not run the API as the bootstrap superuser or migration owner.

```bash
uv sync --dev --frozen
uv run --env-file .env.migration alembic upgrade head
uv run --env-file .env.dev python -m packages.auth.bootstrap_cli \
  --tenant-id default --tenant-name "Default Tenant" --admin-username admin
uv run --env-file .env.dev fastapi dev apps/api/main.py
# In two other terminals, set WORKER_QUEUE_NAME to the configured ingestion /
# embedding queue in each worker's environment before starting:
uv run --env-file .env.worker-ingestion python -m apps.worker.main
uv run --env-file .env.worker-embedding python -m apps.worker.main
# Web, in another terminal:
cd apps/web
npm ci
npm run dev
```

The worker env files should otherwise match `.env.dev`. For a host Web server, the default backend is `http://127.0.0.1:8000`; set `RAG_API_BASE_URL` before starting/building Next.js when using another endpoint. Next rewrites are build-time configuration in a production build; changing only the runner environment does not rebuild those routes.

## 6. Repeatable checks

```bash
uv run ruff check .
uv run pytest tests/unit tests/integration
uv run python -m scripts.generate_web_stream_fixtures --check
uv run python -m scripts.generate_web_workbench_fixtures --check
uv run python -m tests.eval.rag.run_ci_smoke \
  --dataset tests/eval/datasets/rag_smoke.json --config tests/eval/config/rag_smoke_gate.json
uv run python -m tests.eval.rag.run_ci_smoke --extended \
  --config tests/eval/config/rag_extended_gate.json
cd apps/web
npm run lint
npm run typecheck
npm test -- --run
npm run build
npx playwright install chromium
PLAYWRIGHT_USE_BUILD=1 npm run test:e2e
```

Browser tests exercise the real local-login UI with backend-serialized HTTP/SSE fixtures on desktop and mobile Chromium. They never call an external LLM. Use `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to reuse an explicitly selected existing Chromium binary instead of downloading one. CI installs browser system dependencies and runs the production build through the browser contract gate; image publication depends on both backend and Web jobs succeeding.

The backend CI job also provisions a disposable pgvector/PostgreSQL service with separate bootstrap, migration and runtime roles. Its dedicated pilot gate runs fresh migrations through `asyncpg`, then real bootstrap/RLS/vector contracts. Local opt-in runs require `AEGISRAG_PILOT_DISPOSABLE_DATABASE=1`, `AEGISRAG_PILOT_MIGRATION_URL`, `AEGISRAG_PILOT_RUNTIME_URL` and the `AEGISRAG_RLS_*` connection settings used by `tests/integration/storage/test_postgres_pilot.py`. The fixture refuses an existing public schema and never drops/resets a database. Do not point it at a real tenant's database or run migrations before that fresh-database gate.

### Evidence levels

| Level | What it establishes | What it does not establish |
|---|---|---|
| Unit / generated contract | DTO serialization, state transitions, bounded retries and error handling | Running infrastructure |
| SQLite / ASGI | Transaction/idempotence and application request handling with synthetic fixtures | PostgreSQL RLS, pgvector or Redis behavior |
| Browser contract | Actual rendered UI and fetch/stream interactions against mocked HTTP contracts | Deployed API/worker/model operation |
| Target-stack smoke | Fresh migration/bootstrap, live indexing and source workflow, when actually executed | Load, backup/restore, real-model quality or production certification |

Do not count skipped, blocked or never-run checks as passed.

## 7. Troubleshooting

| Symptom | Check |
|---|---|
| Settings validation stops startup | Required placeholders, blank-vs-malformed optional numbers, matching embedding dimensions, reachable provider endpoints |
| Bootstrap conflict | Existing tenant identity/roles, exact tenant name, and migration state; bootstrap is not a password-reset tool |
| Local login disabled | `JWT_JWKS_URL` enables the external identity mode |
| Upload accepted but not searchable | Exact version status, worker logs, queue-name alignment, scheduler and configured provider availability |
| Failed retryable never advances | Redis connection and worker scheduler, retry budget; inspect a synthetic isolated job before claiming retry recovery |
| Status unavailable | `document:manage`, network/timeout, request ID; Refresh status only reads status |
| No answer | Authorized retrieval scope, ingestion readiness, provider setup and diagnostic request ID |
| Source unavailable | Deleted/stale version or denied access; do not reuse a cached excerpt or widen ACL |
| Stream interrupted | Preserve request ID, retry deliberately; partial text is not a successful final answer |
| Web API routes point at wrong host | `RAG_API_BASE_URL` at Next build time, host-versus-container endpoint distinction |

Use scoped logs such as `docker compose --env-file .env -f docker/compose.yaml logs --tail=100 worker-ingestion worker-embedding`. Inspect/redact output before sharing. Do not paste `.env`, expanded Compose config, bearer tokens, passwords or private document text into issue reports.

## 8. Boundaries still requiring acceptance

- No production-ready claim: actual PostgreSQL RLS, Redis retries, MinIO persistence, container startup, backups/restores, load/failure recovery and real-model evaluation need environment-specific evidence
- Web credentials remain in memory only. The positive `expires_in` returned by local login sets a conservative deadline anchored to the successful request start; expiry, foregrounding, tab visibility and page restoration checks return to sign-in, clear this workbench's authenticated query cache and unmount/cancel active UI work. Expiry itself does not attempt server revocation with an expired token
- Explicit sign-out clears local auth/cache immediately and makes one `POST /api/auth/logout` attempt with the original bearer. Its 10-second deadline covers headers and body validation; there is no mutation retry. Only a successful envelope with `data.revoked=true` displays server-confirmed revocation. Network failure, timeout, HTTP 401 or an invalid/false response displays local-only sign-out: the server session may remain active until expiry. A late old response cannot alter a subsequent login
- Automatic token refresh/rotation and enterprise SSO identity hydration are not wired into this Web UI. Reloading or expiry requires signing in again. Component/contract tests cover expiry, background-tab checks and sign-out races; a real browser plus the target deployment's revocation store still requires the live acceptance pass
- Helm is experimental scaffolding, not Compose parity: workers, migrations/role initialization, MinIO and monitoring workloads are incomplete, and provider/value wiring is not fully validated. Do not use the old Helm example as a production deployment recipe
- LangGraph checkpoint/resume/HITL persistence, broader connectors and Kubernetes hardening are separate scope

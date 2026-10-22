# Product deployment and recovery

This guide joins Compose installation, controlled upgrades, offline recovery and
Helm configuration. It documents executable operator procedures; none of the
commands below was run against a real service during implementation. Offline
positive tests are evidence for code/configuration contracts only.

## 1. Prepare an isolated installation

Use a dedicated synthetic-data host/namespace. Review image versions, capacity,
provider egress and credentials before starting. Docker Compose v2 and the locked
application toolchain must already be installed through your approved process.
Copy `.env.example` into a protected ignored `.env`, replace all required secret
placeholders, keep `ENABLE_DEV_AUTH_HEADERS=false`, and leave optional workflows
and real providers disabled until separately configured. URL-encode special
characters in database URL credentials as required by your deployment.

```bash
docker compose --env-file .env -p aegisrag -f docker/compose.yaml config --quiet
docker compose --env-file .env -p aegisrag -f docker/compose.yaml up -d --build \
  postgres postgres-roles redis minio minio-init migration api web \
  worker-ingestion worker-embedding queue-metrics prometheus grafana jaeger
docker compose --env-file .env -p aegisrag -f docker/compose.yaml ps -a
```

Never publish full `compose config` output: it expands credentials. Normal startup
uses a one-shot bootstrap-role service, a separate migration login, and restricted
runtime credentials for API/workers. It does not create/reset local application
accounts; use the existing explicit bootstrap procedure in the pilot runbook.

Record migration Job/service completion, dependency readiness and image identity.
Check `/health`, `/ready`, the web workbench and internal monitoring. Run the
[opt-in deployment probe](DEPLOYMENT_ACCEPTANCE.md) only after reviewing its
synthetic writes and configured provider destinations. A fake fixed LLM cannot
satisfy the grounded known-answer gate, and a healthy process is not full RAG
acceptance. Missing live checks stay unverified.

## 2. Feature and worker configuration

All opt-in flags now pass from `.env` into API/RQ/durable containers:

- `FOLDER_SYNC_ENABLED`, batch timeout and managed-source bounds. The API needs
  the approved connector tree mounted read-only and explicitly configured plans
- `AGENT_DURABLE_ENABLED`, `AGENT_DURABLE_BACKGROUND_ENABLED` and retention/lease
  budgets. `SUPPORT_TICKET_ENABLED` is independent and disabled by default
- `COST_ACCOUNTING_ENABLED`, `COST_CURRENCY`, versioned `COST_MODEL_PRICES_JSON`,
  optional monthly budget/minimum reservation, and bounded accounting I/O timeout

Folder polling is opt-in via the `folder-sync` Compose profile. Supply an existing
least-privilege `FOLDER_SYNC_BEARER_TOKEN` through protected configuration, then
explicitly start `worker-folder-sync`. It shares the API network namespace so its
ordinary HTTP requests remain loopback-only. The token is passed only to that
poller; credentials are neither refreshed nor created. Restart it alongside an
API container recreation so it shares the new network namespace.

Durable polling is opt-in via the `durable-agent` profile. Supply
`AGENT_DURABLE_WORKER_FACTORY` naming a trusted `module:attribute` in the built
image. The factory must own database lifetime, providers, owner scopes and current
authorization. Enabling a profile does not supply that application integration.
Both optional pollers use `restart: "no"` so a missing token/factory requires
operator attention rather than an uncontrolled crash loop. See
[DURABLE_AGENT.md](DURABLE_AGENT.md) and
[the support workflow](../SUPPORT_TICKET_WORKFLOW.md).

RQ ingestion/embedding workers retain their existing scheduler and retries.
`WORKER_STOP_GRACE_PERIOD` defaults to 960 seconds for a 900-second job timeout;
review it whenever the timeout changes. No worker is automatically force-drained,
repaired, purged or restarted to make a maintenance gate pass.

## 3. Existing deployment upgrade

Follow [UPGRADE_CHECKLIST.md](UPGRADE_CHECKLIST.md) first. The read-only checker
supports reviewed public-schema revisions `0016` through `0021`, requires only
the tables present at each revision, and pins repository head `20261016_0021`.
It never migrates, grants privileges, repairs queues or treats RLS-hidden rows as
an empty store. Manual backup/restore, quiescence and acceptance remain unverified
in its report, even when all automated checks pass.

Before the maintenance window, actually restore a prior recovery point into a
separate disposable deployment and record content, vector/metadata consistency,
revision, role/policy semantics, synthetic workflow results and elapsed recovery
time. Quiesce external producers, optional schedulers, admission of API writes
and other administrators. Drain/reconcile queued, retrying, scheduled and durable
work through the existing application process. Gracefully stop workers only after
that work is accounted for. A queue snapshot cannot prove write quiescence.

Take a new coordinated recovery point before applying migrations. After approval,
use the reviewed Compose migration service and locked release images. Do not
restart `postgres-roles` for read-only diagnosis: it changes role/object state.
Do not reopen writes until the intended `0021` revision, normal synthetic indexing,
source retrieval, optional workflows and operational monitoring are verified in
the target. `0018` downgrade drops durable snapshots/ledgers/checkpoints; `0019`
drops sync plans/snapshots; `0020` drops reservations/charges; `0021` drops support
tickets. Never apply a downgrade or destructive rollback as an automatic retry.

## 4. Offline bundled-volume backup

`scripts.compose_snapshot` is a conservative physical snapshot helper for the
three bundled Compose volumes: PostgreSQL, Redis and MinIO. It does not handle
external services, external PostgreSQL tablespaces/symlinks, arbitrary mounted
paths, configuration/credential retention, connector source trees, model caches,
Grafana/Prometheus history or a live/HA database. Use the selected provider's
approved tools for those cases. Save a separately protected copy of the required
configuration/credentials and approved connector sources under your recovery
policy. All three stores must be fully stopped, and external writers must also
be quiesced. This deliberately trades downtime for a coherent local snapshot.

After approved drain/quiescence, stop the entire source project without removing
containers or volumes. Include any active optional profiles and give running
jobs their reviewed grace period. Do not use `down -v`, flush Redis or delete jobs.
Then first inspect the plan:

```bash
python -m scripts.compose_snapshot backup --env-file .env --project aegisrag \
  --directory /approved/backups/new-recovery-point
# Only after all source writers and services are stopped and the revision verified:
python -m scripts.compose_snapshot backup --env-file .env --project aegisrag \
  --directory /approved/backups/new-recovery-point --source-revision 20261016_0021 \
  --execute --confirm-offline-source
```

The parent backup directory must already exist; the recovery-point directory
must not. The helper checks cleanly exited dependency containers (exit code zero) and matching local
image IDs, uses existing images only (`--pull never`), and creates private tar
archives plus a SHA-256/size/image manifest. It refuses unsupported archive links
or special devices. Archives contain private data, sessions and document objects;
protect/encrypt them through your approved backup process. The helper does not
transmit, encrypt or publish them. Incomplete backups are retained for diagnosis;
never overwrite a previous recovery point or assume a partial result is usable.
The manifest records the operator-declared revision and `restore_verified=false`.
A checksum and readable archive do not establish a successful restoration.

## 5. Restore into a new disposable project

Prepare a separate protected `.env.restore` with the exact recorded image bytes,
preserved database authentication/ownership context and isolated destinations.
Change conflicting host ports and remove production/external-provider references.
Do not use the source project or a live Redis/object bucket. The helper restores
volumes only, so no application endpoints are contacted and no application/datastore services
start. Only the isolated, network-disabled tar helper container runs.

```bash
python -m scripts.compose_snapshot restore --env-file .env.restore \
  --project aegis-rehearsal --directory /approved/backups/new-recovery-point
# After independently verifying the destination is disposable and isolated:
python -m scripts.compose_snapshot restore --env-file .env.restore \
  --project aegis-rehearsal --directory /approved/backups/new-recovery-point \
  --execute --confirm-disposable-target
```

Restore appends a random suffix to the requested project prefix and reports the
actual project name. It requires no existing containers/target volumes, verifies
all archives before creation, and refuses changed local image IDs. Preserve the
reported project name and use it with `docker compose -p` for every subsequent
rehearsal command; using the old project name would select another deployment.
A failed restore can leave partial new volumes; it does not remove/retry them or
touch the source. Investigate and retain them under your cleanup policy.

After separately approving startup, start **that reported project** with its
isolated configuration and perform the full recovery rehearsal: database revision
and preserved values/metadata, matching document bytes/checksums, expected queue
mapping, normal synthetic upload/index/retrieve/citation/source flow and any
configured optional workflow. Record elapsed restore/start/validation times,
recovery point and owner signoff. The CLI always reports `restore_verified=false`
because extracting files cannot prove those service-level outcomes. It never
marks a migration/restore accepted or reopens traffic.

## 6. Monitoring and Helm

Compose includes internal `queue-metrics:9181` scraped by Prometheus. It reports
only registered-state and queued/intermediate/started/scheduled/deferred/failed
cardinalities for the two configured RQ queues. Redis operations are bounded and
read-only, no payload is deserialized, and no stale-entry cleanup occurs. Queue
slot labels avoid exposing queue/tenant names. An unavailable Redis inspection
exports health 0 without previous counts. Counts alone cannot attest to drained
work; stale registrations and retries still need operator review.

Prometheus rules cover API down, failed queue inspection and sustained backlog.
Grafana provisions the existing API dashboard and disables anonymous access.
Alert thresholds are examples and no delivery receiver is configured. Configure
and verify your organization's notification destination separately. Jaeger is a
local ephemeral tracing view, not a retained trace archive.

[The Helm guide](../../helm/aegisrag/README.md) documents the corresponding
manifests, external-service-safe defaults, existing-Secret keys, migration gating,
PVC behavior, HTTPS folder polling and operator-provided durable factory. Bundled
stores are single-instance rehearsal services; the chart does not provide HA,
production ingress, cloud secrets, managed backup scheduling or certification.

## 7. Evidence and remaining target acceptance

Offline tests cover positive migration/revision-aware schema contracts, Compose
settings/profile wiring, safe chart defaults, fake-Redis aggregate collection,
plan-only CLI behavior and fake-command backup/separate-restore contracts. These
checks do not launch Docker or access a real PostgreSQL/Redis/MinIO service.

Docker, Helm and Kubernetes executables were unavailable in the implementation
environment. Therefore actual Compose config/build/install, Helm lint/render and
cluster admission, container startup/readiness, graceful drain, PVC permissions,
upgrade and backup/restore rehearsal remain **unverified**. Execute those stages
in the approved isolated target and retain their exact results before calling
the deployment accepted. No real credentials or paid provider calls are needed
for the offline test suite.

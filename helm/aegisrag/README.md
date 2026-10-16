# AegisRAG Helm deployment

The chart now includes API/web, ingestion/embedding RQ workers, a migration Job,
optional folder/durable schedulers, optional PostgreSQL/Redis/MinIO persistence,
MinIO bucket initialization, Prometheus/queue metrics, Grafana provisioning and
Jaeger. This is an installable configuration candidate, not cluster acceptance.
See [the lifecycle guide](../../docs/operations/PRODUCT_DEPLOYMENT.md).

## Safe defaults and secrets

PostgreSQL, Redis, MinIO and observability are **external/disabled by default**.
Fake LLM/embedding/reranking providers remain selected; optional workflows remain
disabled. Configure operator-approved services and publish built application
images before installation. Pin all production image tags/digests through your
release process; the example `latest` tags do not establish image identity.

Prefer `secrets.existingSecret` referencing an existing namespace-local Secret.
Required keys are:

- `database-url`: runtime-only `postgresql+asyncpg://` URL
- `migration-database-url`: separate migrator URL for the same database
- `redis-url`, `minio-access-key`, `minio-secret-key`
- `jwt-secret`, `folder-cursor-secret`
- `llm-api-key`, `embedding-api-key`: empty strings are allowed with fake/local providers
- `grafana-admin-password` when Grafana is enabled
- `postgres-password`, `postgres-migration-password`, `postgres-runtime-password`
  when bundled PostgreSQL is enabled

Set `minio.endpoint` for an external object store and provision the configured
bucket independently. The chart initializes buckets only for bundled MinIO. The
runtime object-store account should be scoped to its bucket; do not distribute
external administrator credentials. Bundled MinIO uses the supplied root account
for a private rehearsal and is not an HA/production secret-management solution.

When bundled PostgreSQL is enabled, the Secret's two database URLs must target the
bundled service with **different** runtime/migrator logins. The one-shot roles Job
uses the bootstrap account and the same reviewed SQL as Compose. It prepares
pgvector, migrator ownership and restricted runtime permissions; bootstrap and
migration credentials are never injected into API/RQ worker containers. Existing
external PostgreSQL must already have appropriate roles, extension, default
privileges and policies. No external role provisioning is attempted.

If `existingSecret` is empty, the chart generates a Secret from `secrets.*` values
and refuses missing required values. Values files and rendered manifests then
contain credentials: keep them private, outside Git and CI output. Helm release
storage also retains values/manifests. Do not pass passwords in `--set` arguments.

## Validate, install and upgrade

In a permitted environment with existing Helm/Kubernetes tools:

```bash
helm lint helm/aegisrag -f /private/aegis-values.yaml
# Render to a private unused file if the values include credentials.
umask 077
set -C
helm template aegisrag helm/aegisrag -n aegisrag \
  -f /private/aegis-values.yaml > /private/new-aegis-manifests.yaml
# Only after operator review/approval of destination and rendered resources:
helm upgrade --install aegisrag helm/aegisrag -n aegisrag --create-namespace \
  -f /private/aegis-values.yaml --wait --wait-for-jobs --timeout 15m
```

`migration.enabled=true` creates a revision-named Job that waits for the migrator
login before running Alembic. New API/RQ/durable pods wait for the exact
`migration.expectedRevision` (`20261016_0021`), including when migrations are
managed externally and `migration.enabled=false`. They never run the migration
as the runtime login. Failed Jobs/init containers remain an operator-visible
blocker. Check Job completion and pod readiness; Helm submission alone is not
success. Old pods can remain active during an upgrade: complete the quiescence,
backup and rehearsal checklist **before** applying the new chart. Do not use
`helm rollback` as a database rollback; additive migration downgrades destroy
private state and need a separate approved recovery decision.

The chart is a single-node rehearsal topology for bundled stores (Recreate
strategy and RWO PVCs), with no managed failover, ingress controller or automatic
backup scheduling. `global.storageClass` selects the existing cluster class;
it does not install a provisioner. Ingress serves the web workbench only; the
web server proxies the API. TLS termination and public API routing are operator
responsibilities. No monitoring service is published through ingress.

## Optional workers

RQ workers use the same runtime configuration as the API and keep RQ's existing
scheduler/retry support. Tune `worker.terminationGracePeriodSeconds` above the
configured `QUEUE_JOB_TIMEOUT_SECONDS` plus a reviewed cleanup margin. Replica
counts and queue names are explicit values.

Folder synchronization requires `api.env.FOLDER_SYNC_ENABLED="true"`, an existing
read-only connector PVC via `api.folderConnectorClaim`, and explicitly configured
sync plans. To enable the separate poller, set `worker.folderSync.enabled=true`,
`apiUrl` to a trusted HTTPS API origin and `tokenSecret` to an existing Secret with
`bearer-token`. Its token is injected only into that poller. The token must remain
valid and narrowly scoped; no refresh or new permission grant is created here.

Durable background work requires explicit durable/background feature settings
and `worker.durable.enabled=true`. `worker.durable.factory` must name a trusted
application `module:attribute` already included in the worker image. That factory
owns persistence, providers, current authorization and owner scopes. The chart
does not invent or dynamically download a factory. See the durable operations
and support-ticket workflow guides before enabling these paths.

Nonsecret `api.env` supports cost-accounting flags, currency, reviewed versioned
`COST_MODEL_PRICES_JSON`, budget/reservation values and timeouts. Disabled cost
accounting makes no price claim; an empty catalog is not a billing configuration.
Never put credentials or bearer tokens in `api.env`.

## Monitoring

Enable `observability.prometheus.enabled` to scrape API `/metrics` and the internal
read-only queue exporter. The exporter exposes only two configured queues' raw
cardinalities using `LLEN`, `ZCARD` and `SISMEMBER`, with bounded Redis I/O and no
job reads/cleanup. Queue slots follow ingestion then embedding (deduplicated).
Raw counts can include stale entries and are not proof that a queue has drained.
A failed inspection exports `aegisrag_queue_exporter_up 0` without stale counts.

Grafana requires Prometheus and a non-default Secret password. Its datasource,
dashboard provider and API dashboard are provisioned from ConfigMaps. Prometheus
and Grafana PVCs have suitable pod groups (65534 and 472 respectively). Jaeger
all-in-one is optional and ephemeral. Alert rules cover API availability,
inspection failure and sustained backlog; tune thresholds to measured workload.
No Alertmanager receiver is configured, so rules alone do not deliver pages.

## Validation status

Offline Python tests verify configuration contracts, reviewed migration metadata
and fake-Redis cardinality collection. Docker/Helm/Kubernetes tools were absent in
the implementation environment. Actual `helm lint/template`, server-side schema
admission, image startup, PVC permissions, upgrades and restoration remain
**unverified** until run in the intended isolated target. Do not convert those
missing checks into a deployment-success claim.

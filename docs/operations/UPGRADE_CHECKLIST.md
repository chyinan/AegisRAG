# Existing-deployment upgrade checklist: 0016 / 0017 → 0018

Use this checklist **before** starting the new Compose dependency graph against an existing database. `docker compose up` can run role initialization and `alembic upgrade head`; it is not a read-only readiness check. The first-boot instructions in [PILOT_RUNBOOK.md](PILOT_RUNBOOK.md) are not an existing-deployment upgrade procedure.

This checklist covers PostgreSQL's `public` schema at `20260830_0016` or `20261014_0017`, upgrading to `20261015_0018`, and inspecting the resulting `0018` state. Revision `0017` removes the fixed vector typmod; additive `0018` introduces private durable Agent storage. The durable API remains disabled unless explicitly configured after migration. Older revisions, multiple/unknown revisions, a newer repository head, other schemas and other vector stores require a separate reviewed migration plan. It does not certify production readiness or perform migrations, repair jobs, create roles, disable RLS, reindex embeddings, make backups, or restore data.

## 1. Read-only preflight

Use the target release's checkout and installed, locked dependencies. This CLI currently runs from the checkout on the host; the API image does not include the `scripts` directory. Do not start/recreate application services merely to run the check.

Create a locally protected, ignored `.env.upgrade-check` through the deployment's existing secret-handling process. Load it explicitly. It must contain:

- `DATABASE_URL`: the **actual API/worker runtime login**, with a `postgresql+asyncpg://` URL and host-reachable endpoint. A migration or bootstrap login here must be reported as unsafe, not used to make the runtime check pass
- `REDIS_URL`: the deployed Redis database, `redis://` or `rediss://`, without query-string overrides. Use exactly the configured ingestion and embedding queue names; changing the names hides old queues rather than draining them
- The deployment's `INGESTION_QUEUE_NAME`, `EMBEDDING_QUEUE_NAME`, `VECTOR_STORE_TYPE=pgvector`, and active `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `EMBEDDING_PROVIDER_VERSION`, `EMBEDDING_DIM`, `VECTOR_INDEX_DIM` settings
- Other required application configuration, since the check reuses `AppSettings` validation. It does **not** instantiate or call an LLM, embedding provider, object store, or application server
- Optionally `AEGISRAG_UPGRADE_AUDIT_DATABASE_URL`: an **already authorized** inspection login with SELECT access and existing all-tenant visibility on this same database. It is used only for aggregate data checks in a separate read-only connection. Do not grant BYPASSRLS, turn off FORCE RLS, or change permissions just to turn this report green. Without approved all-tenant visibility, the global checks remain `UNVERIFIED`

The runtime and audit URLs must have the same host, port, database and URL query options. Usernames/passwords can differ. Host aliases and separate proxy endpoints are intentionally rejected. This comparison cannot independently establish a proxy's routing or database identity; the deployment owner must confirm the destination. Credentials belong in the protected environment/secret store, never command arguments, reports, chat or source control.

From the repository root:

```bash
uv run --env-file .env.upgrade-check python -m scripts.check_upgrade_readiness --help
uv run --env-file .env.upgrade-check python -m scripts.check_upgrade_readiness \
  --timeout-seconds 5
```

The command writes one JSON report to stdout with `schema_version`, `observed_at_utc`, `target_revision`, `status`, `automated_status` and stable check IDs. Descriptions are fixed strings; facts contain reviewed revision IDs and aggregate counts. It does not emit connection URLs, role/queue names, model identifiers, tenant IDs, document text, vector values or raw backend exceptions. Treat aggregate counts as operational information and review the report before sharing.

For a protected local report, select a **new** destination outside tracked source and use shell no-clobber mode. Preserve the nonzero exit status; do not append `|| true` in an acceptance pipeline:

```bash
umask 077
set -C
uv run --env-file .env.upgrade-check python -m scripts.check_upgrade_readiness \
  --timeout-seconds 5 > /approved/new-report-directory/upgrade-preflight.json
result=$?
printf 'Preflight exit code: %s\n' "$result"
```

Replace the example report path with an approved existing directory and unused filename. Directory creation and evidence retention follow the operator's policy.

### Status and exit-code contract

| Status | Exit code | Interpretation |
|---|---:|---|
| `PASS` | 0 | Checks in this group have evidence supporting their bounded assertions |
| `WARN` | 1 | Observed risk needs review, such as retained failed jobs or a blocked 1536-only downgrade |
| `BLOCKED` | 2 | Observed unsafe/unsupported state; do not proceed until investigated |
| `UNVERIFIED` | 3 | Evidence unavailable, hidden by RLS, timed out, or requires an operator |

Overall precedence is `BLOCKED` → `UNVERIFIED` → `WARN` → `PASS`. `automated_status` aggregates only automated checks. The four manual checks (`backup_restore`, `write_quiescence`, `deployment_acceptance`, `durable_recovery`) always remain `UNVERIFIED`, so a complete report deliberately cannot return overall `PASS` or exit 0. There is no flag to self-attest a backup or bypass those checks. Review automated WARN/BLOCKED results even if the overall report is UNVERIFIED. A human change decision with separately retained evidence is required.

### What the checks actually establish

- `schema_revision`: one known supported Alembic revision; metadata is read without calling Alembic's migration runner
- `runtime_role`: no detected superuser/BYPASSRLS/role-creation/database-creation/replication flags, privileged-role membership, public-schema CREATE privilege, database ownership or ownership through public relation-owner roles. This inspects the supplied login, not every other deployed worker or all possible SECURITY DEFINER functions
- `tenant_rls`: the revision-aware table set (18 tables at `0016`/`0017`, 21 at `0018`) checks every expected tenant table is an ordinary table with ENABLE/FORCE RLS and exactly the reviewed all-command, PUBLIC-role tenant policy for both USING and WITH CHECK. Missing, extra or changed policies block; this catalog contract does not replace real two-tenant authorization tests
- `vector_schema`: pgvector exists; the embedding and dimension columns are non-null; dimension metadata has the expected integer type; `0016` has `vector(1536)`; `0017` and `0018` have unconstrained `vector` plus the validated positive/equal-dimension CHECK. It does not prove that an ANN index exists or is performant
- `vector_integrity` / `embedding_identity`: when all-tenant visibility is available, scan all records, including other tenants and soft-deleted rows, for actual-vs-recorded dimension violations and conflicting dimensions for the configured provider/model/version. No raw vector contents leave the database. This is not an embedding quality or numerical-equivalence comparison
- `rollback_1536`: warns if **any** retained vector would prevent the guarded `0017` downgrade. A PASS is a point-in-time dimension observation, not permission to downgrade or proof that restore works
- `database_jobs`: any ingestion job outside `chunked`/`failed_terminal`, or embedding job outside `embedded`/`failed_terminal`, blocks. Unknown states, queued/running work and retryable failures stay unresolved. Terminal failures warn. Rows are not omitted because a document was deleted
- `durable_schema` (at `0018`): safety-bearing owner, JSON snapshot, revision/lease columns, primary/unique keys, nonnegative revision and cascade foreign keys match the reviewed migration. This is not complete physical-schema or index-performance validation
- `durable_runs` (at `0018`): only with proven all-tenant visibility, count retained, unresolved, unsupported-schema and expired rows without reading private JSON. Running/awaiting-approval/intervention-required or unknown states block; retained terminal state warns because downgrade would drop it. Missing or RLS-filtered visibility is `UNVERIFIED`, never an empty-state claim. The check never purges data
- `durable_recovery`: an always-manual acceptance requirement for PostgreSQL recovery, approval/cancel fencing, backup privacy and retention. It cannot be self-attested by a CLI flag
- `redis_queue_N`: registered queue, intermediate-list, started, scheduled and deferred raw cardinalities; pending entries block, absent registration remains unverified, retained failed entries warn. Only `SISMEMBER`, `LLEN` and `ZCARD` are used. RQ registry methods that clean expired entries are deliberately avoided. Expired/stale registry entries can therefore block conservatively; investigate them through the approved operational process rather than deleting them to obtain a green check

The visibility guard uses PostgreSQL’s [row_security_active introspection function](https://www.postgresql.org/docs/17/functions-info.html); the database [documents the all-row backup risk of RLS filtering](https://www.postgresql.org/docs/17/ddl-rowsecurity.html). The tool never changes the row-security setting or switches roles.

PostgreSQL connections request connection-local default read-only, statement/lock timeouts, then use a read-only repeatable-read transaction, verify the read-only setting and roll back. The check never commits or persists settings. Per-I/O timeout is 0.1–60 seconds, with an overall bound for each probe group. A timeout/permission error is `UNVERIFIED`; it is never counted as zero rows. SELECT scans still consume resources and can hold read locks: run within the agreed inspection window. Redis and PostgreSQL are separate snapshots, so no report proves the absence of a concurrent enqueue or write.

## 2. Rehearse recovery before the maintenance window

An archive checksum, `pg_restore --list`, an object listing or an existing backup filename is **not** a successful restore rehearsal. Record all of the following in the change record:

1. The source database revision, application image/source version, pgvector/PostgreSQL versions, active model identity/dimension, object-store bucket/prefix and queue mapping
2. A consistent recovery point across database metadata, private durable Agent snapshots/ledgers/checkpoints and document objects, with an explicit plan for queued/scheduled work and external producers. Include auth/roles and required configuration in the secure recovery plan; do not paste secrets into the change record
3. A fresh backup under a unique filename/object version; never overwrite the previous recovery point. Confirm that the backup method includes all tenants despite RLS
4. An actual restore into a **separate, explicitly disposable, empty target** with the required PostgreSQL/pgvector versions and preserved role/ownership/policy semantics. The target must not reference live Redis, production object buckets, real external providers or production service endpoints
5. Database/object consistency, vector dimensions and checksums/value-preservation checks against the pre-upgrade baseline; two-tenant and private-document denial checks; a synthetic upload → indexing → retrieval → source check; measured restore duration, recovery point and owner signoff

For PostgreSQL custom archives, a DBA's already configured service/credential files can keep credentials out of arguments. The following are templates for that approved process, **not commands run by the preflight**:

```bash
# Set backup to a new path under the approved backup location. Refuse reuse.
: "${backup:?Set a new approved backup path}"
umask 077
set -C
PGSERVICE=aegis_source_backup pg_dump --format=custom > "$backup"
# Structural readability only; this does not count as restoration.
pg_restore --list "$backup" > /dev/null
# Only after independently proving aegis_restore_isolated is the new empty target,
# with the correct existing roles/extension and no production connections:
pg_restore --single-transaction --exit-on-error \
  --dbname=service=aegis_restore_isolated "$backup"
```

Have the owner protect the backup destination from concurrent writers and confirm the source login is authorized to include all rows. Do not use `--clean`, `--create`, role grants, volume deletion or an existing target as a shortcut. The application cannot determine safe object-storage backup/restore commands for an arbitrary provider: use the selected maintained provider's approved process and actually restore a matching object snapshot. `0018` adds private recovery tables with tenant RLS. Its downgrade drops those tables, so even terminal recovery records need an explicit retention/restore decision. `0017` is not a data-conversion migration; changing provider/model/dimension still requires an independent reindex plan.

## 3. Quiesce and inspect again

1. Announce the approved maintenance window. Stop admission of new writes/uploads and all external producer schedules. Account for retries and ingestion jobs that enqueue embedding work
2. Let workers drain both queues and all scheduled/deferred work under the existing retry policy. Investigate unresolved database jobs, old colon-delimited job IDs, old queue names, failed jobs and stale RQ registrations. Never flush Redis, delete jobs/vectors, reset the database, or widen ACL to satisfy the check
3. Gracefully stop workers and their schedulers only after draining, using a termination grace period at least as long as the configured job timeout plus an operator-selected margin. A forced kill can lose the very evidence being checked
4. Verify that no other API instance, worker, connector, cron task or administrator can write during migration. A single node's Compose status does not establish this across a deployment
5. Run the preflight again with the unchanged runtime/queue/model configuration. Verify counts are stable and no automated BLOCKED results remain. Resolve every automated UNVERIFIED result or explicitly postpone the upgrade. Save the report beside recovery, quiescence and rollback evidence

For a single Compose deployment, after the owner has disabled external producers and chosen a sufficient worker grace period, `stop` commands can form part of that procedure:

```bash
docker compose --env-file .env -f docker/compose.yaml stop api web
# Drain/investigate first while workers are still running, then:
docker compose --env-file .env -f docker/compose.yaml stop \
  --timeout "$WORKER_GRACE_SECONDS" worker-ingestion worker-embedding
docker compose --env-file .env -f docker/compose.yaml ps -a
```

Set `WORKER_GRACE_SECONDS` explicitly to the reviewed numeric grace period; it is not an application default. Do not use `down -v`, restart role initialization, recreate Redis or run a migration as part of read-only diagnosis.

## 4. Apply only under the separate approved change procedure

After the owner accepts the actual rehearsal evidence, stable quiescence, bounded lock risk and rollback plan, execute the migration through the existing migration login. Keep that login separate from `DATABASE_URL` in the check and from every API/worker runtime. Set appropriate lock and statement time limits through the deployment's reviewed migration process so a lock conflict does not wait indefinitely. The preflight never performs this action or changes those settings for a migration.

`0017` removes the fixed vector typmod and validates a dimension constraint; it may need strong table locks and scan retained rows. Do not infer lock duration from a small unit test. If migration fails, inspect the actual transaction/schema/revision state before retrying. Do not stamp a revision, drop the constraint or reinterpret vectors to hide the failure.

`0018` adds the durable tables and tenant policies without rewriting existing Agent
history. Rehearse new table access with the existing runtime role and approved
migration process; the checker does not grant privileges. Its downgrade drops
private state. Do not use a zero-row runtime SELECT under RLS as proof that it is
safe to drop, and do not turn off RLS to obtain a green check.

Before reopening writes:

- Run the read-only preflight against `0018`, using the runtime credentials again
- Compare preserved vector values/metadata and the recovery baseline using the approved all-tenant process
- Restart the intended version of API/workers/schedulers with the correct queues and verify the synthetic private-pilot workflow plus isolation tests
- If enabling durable Agents, verify synthetic native/LangGraph pause → fresh-process resume, stale approval, concurrent cancellation, current evidence denial and ambiguous side-effect intervention in isolated PostgreSQL
- Inspect failure/retry behavior and monitoring, then let the owner reopen traffic
- Keep the prior backup/recovery point according to the agreed retention policy

If any new non-1536 rows exist, `0017` downgrade intentionally refuses, including when those rows are soft-deleted or hidden by tenant RLS. Do not delete them or weaken RLS to force the old schema. Follow the rehearsed restore/forward-fix decision with the owner. This checklist does not authorize a rollback, merge, release or deployment.

## 5. Local verification and evidence limits

```bash
uv run ruff check packages/operations scripts/check_upgrade_readiness.py \
  tests/unit/operations/test_upgrade_readiness.py tests/unit/operations/test_durable_upgrade_readiness.py
uv run pytest tests/unit/operations/test_upgrade_readiness.py \
  tests/unit/operations/test_durable_upgrade_readiness.py --no-cov
```

The offline suite tests real repository migration metadata, supported/unknown revisions, runtime roles, policy/schema drift, FORCE-RLS-hidden data, dimension and job classifications, read-only/rollback/timeout configuration, read-only Redis command selection, redaction and CLI exit behavior. Fakes prove decisions and emitted command contracts; they do not establish that a deployed PostgreSQL/Redis service accepted the probes or that a restore succeeded. Execute a target-stack acceptance pass in the isolated rehearsal environment before relying on this operational tool for a real upgrade. Missing, skipped or blocked live checks remain unverified.

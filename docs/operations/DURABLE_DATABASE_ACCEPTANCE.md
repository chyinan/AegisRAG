# Durable Agent database acceptance

The local framework suite uses SQLite and controlled model decisions. That is
useful regression evidence, but it does not prove PostgreSQL row locking, RLS,
physical restart behavior or a production migration. This gate deliberately
keeps those claims separate.

## What the gate checks

`tests/integration/storage/test_durable_database_acceptance.py` runs the same
portable scenarios with SQLite and an explicitly selected PostgreSQL database:

- Native and real LangGraph approval pause/resume with fresh service instances
- Two competing resumes, exactly one observed handler invocation, one completed
  tool ledger, cumulative counters and
  final state read without driving the model again
- Forced competing keyed-start inserts after both initial reads miss, one model/tool
  driver, read-only key recovery and retained-tombstone refusal after private purge
- Separate-session lease acquisition and revision CAS contention
- Completed-ledger immutability with transaction rollback of the snapshot CAS
- Concurrent graph pending writes without losing either write
- Cancellation fencing stale business writes and graph writes
- Reference-only graph checkpoint payloads, excluding synthetic private input,
  tool arguments/results and authenticated identity

A PostgreSQL-only case additionally queries all three durable tables without
application tenant filters. It checks current-tenant visibility, another tenant,
no tenant after connection-pool reuse, application user scoping, forbidden
INSERT, invisible-row UPDATE and forbidden tenant reassignment on each table.
All synthetic write probes roll back even if a policy unexpectedly permits them. SQLite
explicitly skips this case; it does not emulate or certify RLS.

The gate uses a registered calculator and synthetic model decisions. It never
calls a live model, object store or third-party tool. It tests competing
transactions in one process, without claiming measured overlap of PostgreSQL
lock waits. Cancellation checks stale writes after the fence; they do not
simulate cancelling an actively blocked remote side effect. The gate does not
test network partitions, power loss or cross-host
failover. Separate subprocess regressions cover application-process restart
against SQLite and do not replace a PostgreSQL disaster-recovery rehearsal.

## CI sequence

The existing disposable `pgvector/pgvector:pg17` service and fresh migration pilot
run first with separate bootstrap/migrator/runtime roles. That pilot is the only
owner of schema setup and its synthetic upgrade/downgrade checks. The new durable
gate runs after optional framework dependencies are installed, using only the
non-privileged runtime connection.

The durable fixture checks migration head `20261016_0021`, the expected runtime
login, and absence of superuser, BYPASSRLS, CREATEDB and CREATEROLE privileges.
It does not create roles, change grants, migrate, reset or drop application tables.
Synthetic rows are left in the disposable database; the job owns its lifecycle.
The existing catalog gate expects all 25 tenant tables at revision 0021.

The required CI phase sets `AEGISRAG_REQUIRE_POSTGRES_DURABLE=1`. A missing URL
fails instead of silently skipping. Framework import is checked before pytest.
The physical cases carry the `physical_postgres` marker, append to the shared
coverage report and are part of the required backend job.

## Running against an isolated acceptance database

Only use a database explicitly designated disposable by its owner. Complete the
fresh PostgreSQL pilot documented in [PILOT_RUNBOOK.md](PILOT_RUNBOOK.md) first.
Provide the existing runtime URL through the approved environment/secret channel;
do not paste a real URL into a report, command transcript or committed file.

```bash
# Existing isolated acceptance environment supplies AEGISRAG_PILOT_RUNTIME_URL.
AEGISRAG_PILOT_DISPOSABLE_DATABASE=1 \
AEGISRAG_REQUIRE_POSTGRES_DURABLE=1 \
OTEL_SDK_DISABLED=true \
uv run --frozen --extra langchain --extra langgraph pytest \
  tests/integration/storage/test_durable_database_acceptance.py \
  -m physical_postgres --no-cov -ra
```

Without a PostgreSQL URL, ordinary offline discovery reports explicit physical
skips. To run only the portable SQLite cases:

```bash
OTEL_SDK_DISABLED=true uv run --frozen --extra langchain --extra langgraph pytest \
  tests/integration/storage/test_durable_database_acceptance.py \
  -m 'not physical_postgres' --no-cov -ra
```

## Historical local gate-authoring evidence

The portable scenarios were executed locally. The required-gate missing-URL path
was also exercised and correctly failed all selected physical cases. The actual
PostgreSQL cases and the modified CI job have **not** run in this environment.
No live PostgreSQL/RLS, physical failover, production deployment or migration
acceptance is claimed. Record actual command/version/results when that separate
gate is executed; an authored CI job alone is not passing evidence.

For the 2026-10-16 product-completion pass, the permitted local selection and
remaining target gates are recorded separately in
[PRODUCT_COMPLETION_VALIDATION.md](PRODUCT_COMPLETION_VALIDATION.md). Existing
naturally triggered CI results must be read against the PR's exact head; neither
this historical record nor a passing disposable CI database certifies production
PostgreSQL recovery or disaster-recovery acceptance.

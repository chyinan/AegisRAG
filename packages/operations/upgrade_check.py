"""Read-only preflight for the public-schema 0016/0017 -> 0018 upgrade.

The report is evidence, not an upgrade authorization. A restricted runtime role
cannot attest to all-tenant data: PostgreSQL FORCE RLS may hide every row. No
probe changes roles/policies, repairs queues, runs migrations, or reads payloads.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from alembic.config import Config
from alembic.script import ScriptDirectory
from redis.asyncio import Redis
from rq import Queue
from rq.intermediate_queue import IntermediateQueue
from rq.registry import (
    DeferredJobRegistry,
    FailedJobRegistry,
    ScheduledJobRegistry,
    StartedJobRegistry,
)
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.pool import NullPool

from packages.common.config import AppSettings
from packages.data.storage.rls import TENANT_SCOPED_TABLES
from packages.data.storage.session import create_async_db_engine
from packages.operations import durable_upgrade
from packages.operations.durable_upgrade import DURABLE_REVISION, DURABLE_TABLES

RLS_REVISION = "20260830_0016"
VECTOR_REVISION = "20261014_0017"
ROOT = Path(__file__).resolve().parents[2]


class Status(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    BLOCKED = "BLOCKED"
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class Check:
    id: str
    status: Status
    summary: str
    facts: dict[str, int | bool | str] = field(default_factory=dict)
    manual: bool = False


@dataclass(frozen=True)
class Report:
    checks: tuple[Check, ...]
    target_revision: str | None = None
    observed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def status(self) -> Status:
        return combined_status(self.checks)

    @property
    def exit_code(self) -> int:
        return {Status.PASS: 0, Status.WARN: 1, Status.BLOCKED: 2, Status.UNVERIFIED: 3}[
            self.status
        ]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "observed_at_utc": self.observed_at,
            "status": self.status.value,
            "automated_status": combined_status(
                [check for check in self.checks if not check.manual]
            ).value,
            "target_revision": self.target_revision,
            "checks": [asdict(check) for check in self.checks],
        }


def combined_status(checks: Sequence[Check]) -> Status:
    statuses = {check.status for check in checks}
    for status in (Status.BLOCKED, Status.UNVERIFIED, Status.WARN, Status.PASS):
        if status in statuses:
            return status
    return Status.UNVERIFIED


def manual_checks() -> list[Check]:
    return [
        Check(
            "backup_restore",
            Status.UNVERIFIED,
            "Verify a coordinated database/object-store backup by restoring into an isolated "
            "environment and recording integrity, access and recovery-time evidence. "
            "Backup-file existence is not a restore rehearsal.",
            manual=True,
        ),
        Check(
            "write_quiescence",
            Status.UNVERIFIED,
            "Confirm producers, API writes, workers and schedulers are quiesced for the "
            "maintenance window; an empty queue snapshot does not prove this.",
            manual=True,
        ),
        Check(
            "deployment_acceptance",
            Status.UNVERIFIED,
            "Verify lock-duration capacity, deployment/queue identity, all worker configuration, "
            "and target-stack isolation and synthetic indexing after the upgrade.",
            manual=True,
        ),
    ]


@dataclass(frozen=True)
class MigrationPlan:
    head: str
    revisions: frozenset[str]


def load_migration_plan(root: Path = ROOT) -> MigrationPlan:
    """Load version metadata only; never execute Alembic env.py or a migration."""
    config = Config()
    config.set_main_option("script_location", str(root / "migrations"))
    scripts = ScriptDirectory.from_config(config)
    heads = scripts.get_heads()
    if len(heads) != 1:
        raise ValueError("A single migration head is required.")
    revisions = frozenset(revision.revision for revision in scripts.walk_revisions())
    # Intentionally bounded: a newer migration needs its own upgrade risk review.
    if (
        heads[0] != DURABLE_REVISION
        or not {RLS_REVISION, VECTOR_REVISION, DURABLE_REVISION} <= revisions
    ):
        raise ValueError("Unsupported migration target.")
    return MigrationPlan(head=heads[0], revisions=revisions)


ROLE_SQL = """
SELECT r.rolsuper, r.rolbypassrls, r.rolcreaterole, r.rolcreatedb, r.rolreplication,
       current_user = session_user AS direct_login,
       current_setting('transaction_read_only') = 'on' AS read_only,
       EXISTS (
         SELECT 1 FROM pg_catalog.pg_roles AS other
         WHERE pg_has_role(current_user, other.oid, 'MEMBER')
           AND (other.rolsuper OR other.rolbypassrls OR other.rolcreaterole
                OR other.rolcreatedb OR other.rolreplication)
       ) AS privileged_membership,
       pg_has_role(current_user, d.datdba, 'MEMBER') AS database_owner,
       has_schema_privilege(current_user, 'public', 'CREATE') AS schema_create,
       EXISTS (
         SELECT 1 FROM pg_catalog.pg_class AS c
         JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public' AND pg_has_role(current_user, c.relowner, 'MEMBER')
       ) AS relation_owner
FROM pg_catalog.pg_roles AS r
JOIN pg_catalog.pg_database AS d ON d.datname = current_database()
WHERE r.rolname = current_user
"""

TABLE_SQL = """
SELECT c.relname, c.relkind, c.relrowsecurity, c.relforcerowsecurity,
       pg_has_role(current_user, c.relowner, 'MEMBER') AS owner_membership
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = ANY(:tables)
"""

POLICY_SQL = """
SELECT c.relname, p.polname, p.polcmd, p.polpermissive,
       p.polroles = ARRAY[0::oid] AS public_roles,
       pg_get_expr(p.polqual, p.polrelid) AS using_expression,
       pg_get_expr(p.polwithcheck, p.polrelid) AS check_expression
FROM pg_catalog.pg_policy AS p
JOIN pg_catalog.pg_class AS c ON c.oid = p.polrelid
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = ANY(:tables)
"""

VECTOR_SCHEMA_SQL = """
SELECT t.typname, a.atttypmod, a.attnotnull,
       dim.attnotnull AS dimension_not_null, dim_type.typname AS dimension_type,
       EXISTS (SELECT 1 FROM pg_catalog.pg_extension WHERE extname = 'vector') AS extension,
       con.convalidated,
       pg_get_constraintdef(con.oid) AS dimension_constraint
FROM pg_catalog.pg_attribute AS a
JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
JOIN pg_catalog.pg_type AS t ON t.oid = a.atttypid
LEFT JOIN pg_catalog.pg_attribute AS dim ON dim.attrelid = c.oid
  AND dim.attname = 'embedding_dim' AND NOT dim.attisdropped
LEFT JOIN pg_catalog.pg_type AS dim_type ON dim_type.oid = dim.atttypid
LEFT JOIN pg_catalog.pg_constraint AS con ON con.conrelid = c.oid
  AND con.conname = 'ck_vector_records_embedding_dimension' AND con.contype = 'c'
WHERE n.nspname = 'public' AND c.relname = 'vector_records'
  AND a.attname = 'embedding' AND NOT a.attisdropped
"""

VISIBILITY_SQL = """
SELECT c.relname, c.relkind, row_security_active(c.oid) AS filtered,
       has_table_privilege(current_user, c.oid, 'SELECT') AS readable
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname IN ('vector_records', 'ingestion_jobs', 'embedding_jobs')
"""

VECTOR_DATA_SQL = """
SELECT count(*) AS total,
       count(*) FILTER (WHERE embedding IS NULL OR embedding_dim IS NULL
           OR embedding_dim <= 0
           OR vector_dims(embedding) IS DISTINCT FROM embedding_dim) AS invalid,
       count(*) FILTER (WHERE embedding_dim IS DISTINCT FROM 1536
           OR vector_dims(embedding) IS DISTINCT FROM 1536) AS non_1536,
       count(*) FILTER (WHERE embedding_provider = :provider AND embedding_model = :model
           AND embedding_version IS NOT DISTINCT FROM :version
           AND embedding_dim IS DISTINCT FROM :dimension) AS model_dimension_conflicts
FROM public.vector_records
"""

# Unknown statuses and retries conservatively remain unresolved. Do not omit
# soft-deleted documents or old tenants, and do not deserialize job payloads.
JOB_DATA_SQL = """
SELECT
  (SELECT count(*) FROM public.ingestion_jobs
     WHERE status IS NULL OR status NOT IN ('chunked', 'failed_terminal')) AS ingestion_unresolved,
  (SELECT count(*) FROM public.embedding_jobs
     WHERE status IS NULL OR status NOT IN ('embedded', 'failed_terminal')) AS embedding_unresolved,
  (SELECT count(*) FROM public.ingestion_jobs WHERE status = 'failed_terminal')
  + (SELECT count(*) FROM public.embedding_jobs WHERE status = 'failed_terminal')
     AS terminal_failures
"""


def _canonical(expression: object) -> str:
    if not isinstance(expression, str):
        return ""
    # Preserve string literals exactly: stripping spaces inside app.tenant_id
    # could incorrectly accept a policy that reads a different session setting.
    parts = re.split(r"('(?:''|[^'])*')", expression)
    return "".join(
        part if index % 2 else re.sub(r"[\s()]", "", part.replace("::text", "")).lower()
        for index, part in enumerate(parts)
    )


def check_revision(revisions: Sequence[str], plan: MigrationPlan) -> Check:
    if len(revisions) != 1 or revisions[0] not in plan.revisions:
        return Check(
            "schema_revision",
            Status.BLOCKED,
            "Database revision is missing, unknown or branched; investigate before upgrading.",
        )
    if revisions[0] not in (RLS_REVISION, VECTOR_REVISION, DURABLE_REVISION):
        return Check(
            "schema_revision",
            Status.BLOCKED,
            "This check supports 0016/0017 to 0018 only; rehearse other upgrades separately.",
        )
    return Check(
        "schema_revision",
        Status.PASS,
        "Recognized supported schema revision.",
        {"current_revision": revisions[0], "migration_pending": revisions[0] != plan.head},
    )


def check_runtime_role(role: Mapping[str, Any], tables: Sequence[Mapping[str, Any]]) -> Check:
    unsafe = (
        any(
            role[name]
            for name in (
                "rolsuper",
                "rolbypassrls",
                "rolcreaterole",
                "rolcreatedb",
                "rolreplication",
                "privileged_membership",
                "database_owner",
                "schema_create",
                "relation_owner",
            )
        )
        or any(table["owner_membership"] for table in tables)
        or not role["direct_login"]
    )
    return Check(
        "runtime_role",
        Status.BLOCKED if unsafe else Status.PASS,
        "Runtime login has elevated privileges, ownership or assumable privileged roles."
        if unsafe
        else "Runtime login has no detected privileged flags, ownership or role membership.",
    )


def check_rls(
    tables: Sequence[Mapping[str, Any]],
    policies: Sequence[Mapping[str, Any]],
    revision: str | None = DURABLE_REVISION,
) -> Check:
    by_name = {table["relname"]: table for table in tables}
    predicate = _canonical("tenant_id = current_setting('app.tenant_id', true)")
    invalid = 0
    expected_tables = (
        TENANT_SCOPED_TABLES
        if revision == DURABLE_REVISION
        else tuple(name for name in TENANT_SCOPED_TABLES if name not in DURABLE_TABLES)
    )
    for name in expected_tables:
        table = by_name.get(name)
        matching = [policy for policy in policies if policy["relname"] == name]
        valid = bool(
            table
            and table["relkind"] == "r"
            and table["relrowsecurity"]
            and table["relforcerowsecurity"]
            and len(matching) == 1
        )
        if valid:
            policy = matching[0]
            valid = (
                policy["polname"] == f"tenant_isolation_{name}"
                and policy["polcmd"] == "*"
                and policy["polpermissive"]
                and policy["public_roles"]
                and _canonical(policy["using_expression"]) == predicate
                and _canonical(policy["check_expression"]) == predicate
            )
        invalid += not valid
    return Check(
        "tenant_rls",
        Status.BLOCKED if invalid else Status.PASS,
        "Tenant tables or policies differ from the reviewed revision contract."
        if invalid
        else "Expected tenant tables have FORCE RLS and the exact isolation policy.",
        {"tables_checked": len(expected_tables), "tables_with_drift": invalid},
    )


def check_vector_schema(rows: Sequence[Mapping[str, Any]], revision: str | None) -> Check:
    valid = len(rows) == 1 and revision in (RLS_REVISION, VECTOR_REVISION, DURABLE_REVISION)
    if valid:
        row = rows[0]
        valid = bool(
            row["extension"]
            and row["typname"] == "vector"
            and row["attnotnull"]
            and row["dimension_not_null"]
            and row["dimension_type"] == "int4"
        )
        valid = valid and row["atttypmod"] == (1536 if revision == RLS_REVISION else -1)
        if revision in (VECTOR_REVISION, DURABLE_REVISION):
            valid = (
                valid
                and bool(row["convalidated"])
                and _canonical(row["dimension_constraint"])
                == _canonical(
                    "CHECK (embedding_dim > 0 AND vector_dims(embedding) = embedding_dim)"
                )
            )
    return Check(
        "vector_schema",
        Status.PASS if valid else Status.BLOCKED,
        "Vector column and validated dimension constraint match the schema revision."
        if valid
        else "Vector extension, column dimension or validated constraint is inconsistent.",
    )


def check_vector_data(row: Mapping[str, Any]) -> list[Check]:
    total, invalid, non_1536, conflicts = (
        int(row[name]) for name in ("total", "invalid", "non_1536", "model_dimension_conflicts")
    )
    return [
        Check(
            "vector_integrity",
            Status.BLOCKED if invalid else Status.PASS,
            "All-tenant vectors contain invalid dimensions."
            if invalid
            else "All-tenant vector dimensions match stored metadata, including deleted rows.",
            {"rows_checked": total, "invalid_rows": invalid},
        ),
        Check(
            "embedding_identity",
            Status.BLOCKED if conflicts else Status.PASS,
            "Stored dimensions conflict with the configured provider/model/version."
            if conflicts
            else "No stored dimension conflict for the configured provider/model/version.",
            {"conflicting_rows": conflicts},
        ),
        Check(
            "rollback_1536",
            Status.WARN if non_1536 else Status.PASS,
            "0017 downgrade will refuse non-1536 rows; retain 0017 or use a rehearsed restore plan."
            if non_1536
            else "No dimension blocker to 0017 downgrade was observed in this snapshot.",
            {"non_1536_rows": non_1536},
        ),
    ]


@asynccontextmanager
async def readonly_connection(url: str, timeout: float) -> AsyncIterator[AsyncConnection]:
    parsed = make_url(url)
    if parsed.drivername != "postgresql+asyncpg":
        raise ValueError("Only the application's PostgreSQL asyncpg URL is supported.")
    engine = create_async_db_engine(
        url,
        poolclass=NullPool,
        echo=False,
        hide_parameters=True,
        connect_args={
            "timeout": timeout,
            "command_timeout": timeout,
            "server_settings": {
                "default_transaction_read_only": "on",
                "statement_timeout": str(max(1, int(timeout * 1000))),
                "lock_timeout": str(max(1, int(timeout * 1000))),
            },
        },
    )
    try:
        async with engine.connect() as connection:
            await connection.execution_options(
                isolation_level="REPEATABLE READ",
                postgresql_readonly=True,
            )
            # Exit rolls back; no commit, even though the transaction is read-only.
            transaction = await connection.begin()
            try:
                enabled = await connection.scalar(text("SHOW transaction_read_only"))
                if enabled != "on":
                    raise ValueError("Read-only enforcement unavailable.")
                yield connection
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def _rows(
    connection: AsyncConnection, query: str, params: Mapping[str, Any] | None = None
) -> list[Mapping[str, Any]]:
    result = await connection.execute(text(query), dict(params or {}))
    return [dict(row) for row in result.mappings()]


def _data_unverified(summary: str) -> list[Check]:
    return [
        Check(name, Status.UNVERIFIED, summary)
        for name in (
            "vector_integrity",
            "embedding_identity",
            "rollback_1536",
            "database_jobs",
        )
    ]


async def inspect_all_tenant_data(
    connection: AsyncConnection, settings: AppSettings
) -> list[Check]:
    visibility = await _rows(connection, VISIBILITY_SQL)
    if {row["relname"] for row in visibility} != {
        "vector_records",
        "ingestion_jobs",
        "embedding_jobs",
    } or any(row["filtered"] or not row["readable"] or row["relkind"] != "r" for row in visibility):
        return _data_unverified(
            "All-tenant visibility is not available. FORCE RLS or missing SELECT access cannot "
            "be treated as an empty database; use an already-authorized audit connection."
        )
    vectors = await _rows(
        connection,
        VECTOR_DATA_SQL,
        {
            "provider": settings.embedding_provider,
            "model": settings.embedding_model,
            "version": settings.embedding_provider_version,
            "dimension": settings.embedding_dim,
        },
    )
    checks = check_vector_data(vectors[0])
    try:
        jobs = (await _rows(connection, JOB_DATA_SQL))[0]
    except Exception:
        checks.append(
            Check(
                "database_jobs",
                Status.UNVERIFIED,
                "Database job inspection failed or timed out; earlier vector findings still apply.",
            )
        )
        return checks
    unresolved = int(jobs["ingestion_unresolved"]) + int(jobs["embedding_unresolved"])
    failures = int(jobs["terminal_failures"])
    checks.append(
        Check(
            "database_jobs",
            Status.BLOCKED if unresolved else Status.WARN if failures else Status.PASS,
            "Unresolved database jobs require draining or operator investigation."
            if unresolved
            else "No unresolved database jobs; review terminal failures before maintenance."
            if failures
            else "No unresolved database ingestion/embedding jobs were observed.",
            {"unresolved": unresolved, "terminal_failures": failures},
        )
    )
    return checks


def same_database_endpoint(runtime_url: str, audit_url: str) -> bool:
    """Conservative match: aliases/proxies require separate operator verification."""
    runtime, audit = make_url(runtime_url), make_url(audit_url)
    if runtime.drivername != "postgresql+asyncpg" or audit.drivername != runtime.drivername:
        return False
    # URL query parameters can override hosts/databases or startup options. Do
    # not permit an audit endpoint to hide a different destination in the query.
    if runtime.query != audit.query:
        return False
    return (runtime.host, runtime.port or 5432, runtime.database) == (
        audit.host,
        audit.port or 5432,
        audit.database,
    )


async def inspect_database(
    settings: AppSettings, plan: MigrationPlan, audit_url: str | None, timeout: float
) -> list[Check]:
    if not settings.database_url:
        return [Check("database_connection", Status.UNVERIFIED, "DATABASE_URL is required.")]
    checks: list[Check] = []
    revision: str | None = None
    durable_schema_valid = False
    try:
        async with asyncio.timeout(timeout * 12):
            async with readonly_connection(settings.database_url, timeout) as connection:
                present = await connection.scalar(
                    text("SELECT to_regclass('public.alembic_version')")
                )
                revisions = (
                    list(
                        await connection.scalars(
                            text("SELECT version_num FROM public.alembic_version")
                        )
                    )
                    if present
                    else []
                )
                checks.append(check_revision(revisions, plan))
                revision = revisions[0] if len(revisions) == 1 else None
                tables = await _rows(connection, TABLE_SQL, {"tables": list(TENANT_SCOPED_TABLES)})
                role = (await _rows(connection, ROLE_SQL))[0]
                checks.append(check_runtime_role(role, tables))
                policies = await _rows(
                    connection, POLICY_SQL, {"tables": list(TENANT_SCOPED_TABLES)}
                )
                checks.append(check_rls(tables, policies, revision))
                if revision == DURABLE_REVISION:
                    durable_schema_valid = durable_upgrade.schema_is_valid(
                        await _rows(
                            connection, durable_upgrade.COLUMN_SQL, {"tables": list(DURABLE_TABLES)}
                        ),
                        await _rows(
                            connection,
                            durable_upgrade.CONSTRAINT_SQL,
                            {"tables": list(DURABLE_TABLES)},
                        ),
                    )
                    checks.append(
                        Check(
                            "durable_schema",
                            Status.PASS if durable_schema_valid else Status.BLOCKED,
                            "Durable owner, lease, snapshot and ledger constraints match 0018."
                            if durable_schema_valid
                            else "Durable storage differs from the reviewed 0018 contract.",
                        )
                    )
                schema = check_vector_schema(
                    await _rows(connection, VECTOR_SCHEMA_SQL),
                    revisions[0] if len(revisions) == 1 else None,
                )
                checks.append(schema)
                if not audit_url:
                    checks.extend(
                        await inspect_all_tenant_data(connection, settings)
                        if schema.status == Status.PASS
                        else _data_unverified(
                            "Vector schema must be understood before data checks."
                        )
                    )
                    if revision == DURABLE_REVISION:
                        checks.append(await inspect_durable_data(connection, durable_schema_valid))
    except Exception:
        # Operational boundary: turn permission/network/driver/timeouts into
        # explicit failed verification, never raw exceptions with URL/SQL inputs.
        checks.append(
            Check(
                "database_connection",
                Status.UNVERIFIED,
                "Database inspection failed or timed out; check access and connectivity.",
            )
        )
    if audit_url:
        try:
            if not same_database_endpoint(settings.database_url, audit_url):
                checks.append(
                    Check(
                        "audit_endpoint",
                        Status.BLOCKED,
                        "Audit and runtime connections must identify the same endpoint/database.",
                    )
                )
                return checks
            async with asyncio.timeout(timeout * 6):
                async with readonly_connection(audit_url, timeout) as connection:
                    checks.extend(await inspect_all_tenant_data(connection, settings))
                    if revision == DURABLE_REVISION:
                        checks.append(await inspect_durable_data(connection, durable_schema_valid))
        except Exception:
            checks.extend(
                _data_unverified(
                    "Audit inspection failed or timed out; no global data-safety claim can be made."
                )
            )
    if revision == DURABLE_REVISION and not any(item.id == "durable_runs" for item in checks):
        checks.append(
            Check("durable_runs", Status.UNVERIFIED, "Durable all-tenant state was not verified.")
        )
    return checks


async def inspect_durable_data(connection: AsyncConnection, schema_valid: bool) -> Check:
    if not schema_valid:
        return Check(
            "durable_runs", Status.UNVERIFIED, "Understand durable storage before inspecting state."
        )
    visibility = await _rows(connection, durable_upgrade.VISIBILITY_SQL)
    if len(visibility) != 1 or any(
        row["relname"] != "durable_agent_runs"
        or row["relkind"] != "r"
        or row["filtered"]
        or not row["readable"]
        for row in visibility
    ):
        return Check(
            "durable_runs",
            Status.UNVERIFIED,
            "All-tenant durable visibility is unavailable; filtered rows are not an empty store.",
        )
    row = (await _rows(connection, durable_upgrade.DATA_SQL))[0]
    facts: dict[str, int | bool | str] = {
        name: int(row[name]) for name in ("retained", "unresolved", "unsupported_schema", "expired")
    }
    status = (
        Status.BLOCKED
        if facts["unresolved"] or facts["unsupported_schema"]
        else Status.WARN
        if facts["retained"]
        else Status.PASS
    )
    return Check(
        "durable_runs",
        status,
        "Unresolved or unsupported durable runs require reconciliation before maintenance."
        if status == Status.BLOCKED
        else "Retained private recovery state needs a backup, retention and downgrade decision."
        if status == Status.WARN
        else "No retained durable runs were observed with all-tenant visibility.",
        facts,
    )


async def inspect_queues(settings: AppSettings, timeout: float) -> list[Check]:
    if not settings.redis_url:
        return [
            Check("redis_queues", Status.UNVERIFIED, "REDIS_URL is required for queue inspection.")
        ]
    connection: Redis | None = None
    checks: list[Check] = []
    try:
        parsed = urlsplit(settings.redis_url)
        if parsed.scheme not in {"redis", "rediss"} or parsed.query or parsed.fragment:
            raise ValueError("Unsupported Redis URL options.")
        connection = Redis.from_url(
            settings.redis_url,
            socket_timeout=timeout,
            socket_connect_timeout=timeout,
            retry_on_timeout=False,
        )
        queues = list(dict.fromkeys((settings.ingestion_queue_name, settings.embedding_queue_name)))
        async with asyncio.timeout(timeout * 3):
            for index, name in enumerate(queues, start=1):
                queue_key = f"{Queue.redis_queue_namespace_prefix}{name}"
                # Registry.count/get_job_ids can perform cleanup and writes in
                # RQ. Use key templates only and read raw cardinalities instead.
                async with connection.pipeline(transaction=False) as pipeline:
                    pipeline.sismember(Queue.redis_queues_keys, queue_key)
                    pipeline.llen(queue_key)
                    pipeline.llen(IntermediateQueue.get_intermediate_queue_key(queue_key))
                    for registry in (
                        StartedJobRegistry,
                        ScheduledJobRegistry,
                        DeferredJobRegistry,
                        FailedJobRegistry,
                    ):
                        pipeline.zcard(registry.key_template.format(name))
                    observed = await pipeline.execute()
                registered, queued, intermediate, started, scheduled, deferred, failed = map(
                    int, observed
                )
                facts: dict[str, int | bool | str] = dict(
                    zip(
                        ("queued", "intermediate", "started", "scheduled", "deferred", "failed"),
                        (queued, intermediate, started, scheduled, deferred, failed),
                        strict=True,
                    )
                )
                pending = queued + intermediate + started + scheduled + deferred
                status = (
                    Status.BLOCKED
                    if pending
                    else Status.UNVERIFIED
                    if not registered
                    else Status.WARN
                    if failed
                    else Status.PASS
                )
                checks.append(
                    Check(
                        f"redis_queue_{index}",
                        status,
                        "Pending work exists; drain or investigate without deleting jobs."
                        if pending
                        else "Queue registration absent; verify Redis database and queue mapping."
                        if not registered
                        else "Queue has retained failures requiring operator review."
                        if failed
                        else "No pending queue, intermediate or active-registry entries observed.",
                        facts,
                    )
                )
    except Exception:
        checks.append(
            Check(
                "redis_queues",
                Status.UNVERIFIED,
                "Queue inspection failed or timed out; no queue-drained claim can be made.",
            )
        )
    finally:
        if connection is not None:
            try:
                async with asyncio.timeout(timeout):
                    await connection.aclose()
            except Exception:
                checks.append(
                    Check(
                        "redis_disconnect",
                        Status.UNVERIFIED,
                        "Redis connection cleanup failed; earlier queue findings still apply.",
                    )
                )
    return checks


async def run_checks(
    settings: AppSettings, *, audit_url: str | None = None, timeout: float = 5.0, root: Path = ROOT
) -> Report:
    checks = manual_checks()
    if not math.isfinite(timeout) or not 0.1 <= timeout <= 60:
        checks.append(Check("configuration", Status.BLOCKED, "Timeout must be 0.1 to 60 seconds."))
        return Report(tuple(checks))
    if (
        settings.embedding_dim != settings.vector_index_dim
        or settings.vector_store_type != "pgvector"
    ):
        checks.append(
            Check(
                "configuration",
                Status.BLOCKED,
                "This preflight requires pgvector and matching embedding/index dimensions.",
            )
        )
        return Report(tuple(checks))
    try:
        plan = load_migration_plan(root)
    except Exception:
        checks.append(
            Check(
                "migration_metadata",
                Status.BLOCKED,
                "Cannot load the supported single-head 0018 migration metadata.",
            )
        )
        return Report(tuple(checks))
    checks.append(
        Check(
            "durable_recovery",
            Status.UNVERIFIED,
            "Rehearse 0018 recovery/approval/cancel and backup retention on isolated PostgreSQL; "
            "downgrade drops private recovery state. No migration or deletion is authorized.",
            manual=True,
        )
    )
    checks.extend(await inspect_database(settings, plan, audit_url, timeout))
    checks.extend(await inspect_queues(settings, timeout))
    return Report(tuple(checks), plan.head)

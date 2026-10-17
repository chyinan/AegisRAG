"""Offline operational contracts; no live database, Redis or model calls."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from packages.common.config import AppSettings
from packages.data.storage.rls import TENANT_SCOPED_TABLES
from packages.operations import upgrade_check as check
from scripts import check_upgrade_readiness as cli


@pytest.fixture
def settings() -> AppSettings:
    return AppSettings(
        DATABASE_URL="postgresql+asyncpg://runtime:hidden@db:5432/app",
        REDIS_URL="redis://:hidden@redis:6379/0",
        VECTOR_STORE_TYPE="pgvector",
        EMBEDDING_PROVIDER="fake",
        EMBEDDING_MODEL="fake-embedding",
        EMBEDDING_DIM=8,
        VECTOR_INDEX_DIM=8,
        INGESTION_QUEUE_NAME="ingestion-private",
        EMBEDDING_QUEUE_NAME="embedding-private",
    )


def role() -> dict[str, bool]:
    return {
        "rolsuper": False,
        "rolbypassrls": False,
        "rolcreaterole": False,
        "rolcreatedb": False,
        "rolreplication": False,
        "direct_login": True,
        "privileged_membership": False,
        "database_owner": False,
        "schema_create": False,
        "relation_owner": False,
        "read_only": True,
    }


def tables() -> list[dict[str, Any]]:
    return [
        {
            "relname": name,
            "relkind": "r",
            "relrowsecurity": True,
            "relforcerowsecurity": True,
            "owner_membership": False,
        }
        for name in TENANT_SCOPED_TABLES
    ]


def policies() -> list[dict[str, Any]]:
    return [
        {
            "relname": name,
            "polname": f"tenant_isolation_{name}",
            "polcmd": "*",
            "polpermissive": True,
            "public_roles": True,
            "using_expression": (
                "((tenant_id)::text = current_setting('app.tenant_id'::text, true))"
            ),
            "check_expression": (
                "((tenant_id)::text = current_setting('app.tenant_id'::text, true))"
            ),
        }
        for name in TENANT_SCOPED_TABLES
    ]


def vector_schema(revision: str = check.VECTOR_REVISION) -> dict[str, Any]:
    return {
        "typname": "vector",
        "atttypmod": -1 if revision == check.VECTOR_REVISION else 1536,
        "attnotnull": True,
        "dimension_not_null": True,
        "dimension_type": "int4",
        "extension": True,
        "convalidated": revision == check.VECTOR_REVISION,
        "dimension_constraint": (
            "CHECK (((embedding_dim > 0) AND (vector_dims(embedding) = embedding_dim)))"
        ),
    }


def visibility(*, filtered: bool = False) -> list[dict[str, Any]]:
    return [
        {"relname": name, "relkind": "r", "filtered": filtered, "readable": True}
        for name in ("vector_records", "ingestion_jobs", "embedding_jobs")
    ]


def vector_data(**changes: int) -> dict[str, int]:
    return {"total": 2, "invalid": 0, "non_1536": 0, "model_dimension_conflicts": 0, **changes}


def job_data(**changes: int) -> dict[str, int]:
    return {"ingestion_unresolved": 0, "embedding_unresolved": 0, "terminal_failures": 0, **changes}


class FakeConnection:
    def __init__(self, *, filtered: bool = False, revision: str = check.VECTOR_REVISION):
        self.queries: list[str] = []
        self.parameters: list[dict[str, Any]] = []
        self.revisions = [revision]
        self.rows: dict[str, list[dict[str, Any]]] = {
            check.ROLE_SQL: [role()],
            check.TABLE_SQL: tables(),
            check.POLICY_SQL: policies(),
            check.VECTOR_SCHEMA_SQL: [vector_schema(revision)],
            check.VISIBILITY_SQL: visibility(filtered=filtered),
            check.VECTOR_DATA_SQL: [vector_data()],
            check.JOB_DATA_SQL: [job_data()],
        }
        self.execution_options = AsyncMock(return_value=self)
        self.rollback = AsyncMock()
        self.begin = AsyncMock(return_value=SimpleNamespace(rollback=self.rollback))
        self.read_only = "on"

    async def __aenter__(self) -> FakeConnection:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    async def execute(self, statement: object, params: dict[str, Any]) -> object:
        sql = str(statement)
        assert sql.strip().upper().startswith("SELECT"), sql
        self.queries.append(sql)
        self.parameters.append(params)
        return SimpleNamespace(mappings=lambda: self.rows[sql])

    async def scalar(self, statement: object) -> object:
        sql = str(statement)
        self.queries.append(sql)
        if sql == "SHOW transaction_read_only":
            return self.read_only
        assert sql == "SELECT to_regclass('public.alembic_version')"
        return "alembic_version" if self.revisions else None

    async def scalars(self, statement: object) -> list[str]:
        sql = str(statement)
        assert sql == "SELECT version_num FROM public.alembic_version"
        self.queries.append(sql)
        return self.revisions


def fake_connections(monkeypatch: pytest.MonkeyPatch, *connections: FakeConnection) -> list[str]:
    iterator = iter(connections)
    urls: list[str] = []

    @asynccontextmanager
    async def connect(url: str, timeout: float):
        urls.append(url)
        yield next(iterator)

    monkeypatch.setattr(check, "readonly_connection", connect)
    return urls


def by_id(results: list[check.Check] | tuple[check.Check, ...]) -> dict[str, check.Check]:
    return {result.id: result for result in results}


def test_real_metadata_load_never_executes_migrations(monkeypatch: pytest.MonkeyPatch) -> None:
    from alembic import command

    monkeypatch.setattr(command, "upgrade", lambda *a, **k: pytest.fail("must not upgrade"))
    monkeypatch.setattr(command, "downgrade", lambda *a, **k: pytest.fail("must not downgrade"))
    plan = check.load_migration_plan()
    assert plan.head == check.TARGET_REVISION
    assert len(plan.revisions) == 21


@pytest.mark.parametrize("heads", [[], ["a", "b"], ["future-revision"]])
def test_unknown_or_branched_metadata_is_refused(
    monkeypatch: pytest.MonkeyPatch, heads: list[str]
) -> None:
    scripts = SimpleNamespace(get_heads=lambda: heads, walk_revisions=lambda: [])
    monkeypatch.setattr(check.ScriptDirectory, "from_config", lambda config: scripts)
    with pytest.raises(ValueError):
        check.load_migration_plan()


@pytest.mark.parametrize(
    "revisions", [[], ["unknown"], [check.RLS_REVISION, check.VECTOR_REVISION], ["20260527_0001"]]
)
def test_bad_or_unsupported_database_revision_blocks(revisions: list[str]) -> None:
    assert (
        check.check_revision(revisions, check.load_migration_plan()).status == check.Status.BLOCKED
    )


@pytest.mark.parametrize("revision", [check.RLS_REVISION, check.VECTOR_REVISION])
def test_supported_revision_and_schema_pass(revision: str) -> None:
    result = check.check_revision([revision], check.load_migration_plan())
    assert result.status == check.Status.PASS
    assert result.facts["migration_pending"] == (revision != check.TARGET_REVISION)
    assert (
        check.check_vector_schema([vector_schema(revision)], revision).status == check.Status.PASS
    )


@pytest.mark.parametrize(
    "field",
    [
        "rolsuper",
        "rolbypassrls",
        "rolcreaterole",
        "rolcreatedb",
        "rolreplication",
        "privileged_membership",
        "database_owner",
        "schema_create",
        "relation_owner",
    ],
)
def test_elevated_runtime_roles_block(field: str) -> None:
    unsafe = role()
    unsafe[field] = True
    assert check.check_runtime_role(unsafe, tables()).status == check.Status.BLOCKED


def test_runtime_ownership_or_role_switch_blocks() -> None:
    records = tables()
    records[0]["owner_membership"] = True
    assert check.check_runtime_role(role(), records).status == check.Status.BLOCKED
    unsafe = role()
    unsafe["direct_login"] = False
    assert check.check_runtime_role(unsafe, tables()).status == check.Status.BLOCKED
    assert check.check_runtime_role(role(), tables()).status == check.Status.PASS


@pytest.mark.parametrize(
    "field,value", [("relrowsecurity", False), ("relforcerowsecurity", False), ("relkind", "v")]
)
def test_missing_force_rls_or_non_table_blocks(field: str, value: object) -> None:
    records = tables()
    records[0][field] = value
    assert check.check_rls(records, policies()).status == check.Status.BLOCKED
    assert check.check_rls(records[1:], policies()).status == check.Status.BLOCKED


@pytest.mark.parametrize(
    "field,value",
    [
        ("using_expression", "true"),
        ("check_expression", None),
        ("using_expression", "tenant_id = current_setting('app. tenant_id', true)"),
        ("polname", "unexpected"),
        ("polcmd", "r"),
        ("polpermissive", False),
        ("public_roles", False),
    ],
)
def test_policy_drift_blocks(field: str, value: object) -> None:
    records = policies()
    records[0][field] = value
    assert check.check_rls(tables(), records).status == check.Status.BLOCKED


def test_exact_policy_only_no_additional_permissive_policies() -> None:
    assert check.check_rls(tables(), policies()).status == check.Status.PASS
    records = policies()
    records.append({**records[0], "polname": "allow_all", "using_expression": "true"})
    assert check.check_rls(tables(), records).status == check.Status.BLOCKED


@pytest.mark.parametrize(
    "field,value",
    [
        ("extension", False),
        ("typname", "json"),
        ("attnotnull", False),
        ("dimension_not_null", False),
        ("dimension_type", "int8"),
        ("atttypmod", 1536),
        ("convalidated", False),
        ("dimension_constraint", "CHECK (embedding_dim > 0)"),
    ],
)
def test_vector_schema_drift_blocks(field: str, value: object) -> None:
    row = vector_schema()
    row[field] = value
    assert check.check_vector_schema([row], check.VECTOR_REVISION).status == check.Status.BLOCKED


def test_all_tenant_invalid_vectors_and_same_model_dim_conflict_block() -> None:
    results = by_id(check.check_vector_data(vector_data(invalid=1, model_dimension_conflicts=1)))
    assert results["vector_integrity"].status == check.Status.BLOCKED
    assert results["embedding_identity"].status == check.Status.BLOCKED


def test_valid_mixed_dimensions_warn_about_downgrade_without_changing_values() -> None:
    row = vector_data(non_1536=1)
    results = by_id(check.check_vector_data(row))
    assert results["vector_integrity"].status == check.Status.PASS
    assert results["rollback_1536"].status == check.Status.WARN
    assert results["rollback_1536"].facts["non_1536_rows"] == 1
    assert row == vector_data(non_1536=1)


@pytest.mark.asyncio
async def test_rls_hidden_zero_rows_cannot_pass_global_data(settings: AppSettings) -> None:
    connection = FakeConnection(filtered=True)
    results = await check.inspect_all_tenant_data(connection, settings)
    assert all(result.status == check.Status.UNVERIFIED for result in results)
    assert connection.queries == [check.VISIBILITY_SQL]


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("readable", False), ("relkind", "v")])
async def test_unreadable_or_non_table_data_is_unverified(
    settings: AppSettings, field: str, value: object
) -> None:
    connection = FakeConnection()
    connection.rows[check.VISIBILITY_SQL][0][field] = value
    results = await check.inspect_all_tenant_data(connection, settings)
    assert all(result.status == check.Status.UNVERIFIED for result in results)
    assert check.VECTOR_DATA_SQL not in connection.queries


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["ingestion_unresolved", "embedding_unresolved"])
async def test_unresolved_database_jobs_block(settings: AppSettings, field: str) -> None:
    connection = FakeConnection()
    connection.rows[check.JOB_DATA_SQL] = [job_data(**{field: 1})]
    results = by_id(await check.inspect_all_tenant_data(connection, settings))
    assert results["database_jobs"].status == check.Status.BLOCKED
    assert results["database_jobs"].facts["unresolved"] == 1
    assert "failed_retryable" not in check.JOB_DATA_SQL  # not whitelisted as terminal
    assert "deleted_at" not in check.VECTOR_DATA_SQL  # includes soft-deleted rows
    assert all(sql.strip().startswith("SELECT") for sql in connection.queries)


@pytest.mark.asyncio
async def test_terminal_database_failures_warn(settings: AppSettings) -> None:
    connection = FakeConnection()
    connection.rows[check.JOB_DATA_SQL] = [job_data(terminal_failures=1)]
    results = by_id(await check.inspect_all_tenant_data(connection, settings))
    assert results["database_jobs"].status == check.Status.WARN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "postgresql://runtime:driver-secret@private-db/app",
        "postgresql+psycopg://runtime:driver-secret@private-db/app",
        "sqlite+aiosqlite:///private-app.db",
    ],
)
async def test_unsupported_database_driver_rejected_before_engine_creation(
    monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    create_engine = Mock(side_effect=AssertionError("must reject before creating an engine"))
    monkeypatch.setattr(check, "create_async_db_engine", create_engine)

    with pytest.raises(ValueError) as error:
        async with check.readonly_connection(url, 1):
            pytest.fail("must not yield a connection for an unsupported driver")

    assert str(error.value) == "Only the application's PostgreSQL asyncpg URL is supported."
    create_engine.assert_not_called()


@pytest.mark.asyncio
async def test_readonly_connection_enforces_read_only_and_rolls_back(
    monkeypatch: pytest.MonkeyPatch, settings: AppSettings
) -> None:
    connection = FakeConnection()
    engine = SimpleNamespace(connect=lambda: connection, dispose=AsyncMock())
    calls = []

    def create(url: str, **kwargs: Any):
        calls.append((url, kwargs))
        return engine

    monkeypatch.setattr(check, "create_async_db_engine", create)
    async with check.readonly_connection(settings.database_url, 2) as result:
        assert result is connection
    options = calls[0][1]
    assert options["hide_parameters"] is True
    assert options["echo"] is False
    assert options["connect_args"]["timeout"] == 2
    assert options["connect_args"]["command_timeout"] == 2
    assert options["connect_args"]["server_settings"] == {
        "default_transaction_read_only": "on",
        "statement_timeout": "2000",
        "lock_timeout": "2000",
    }
    connection.execution_options.assert_awaited_once_with(
        isolation_level="REPEATABLE READ",
        postgresql_readonly=True,
    )
    connection.rollback.assert_awaited_once()
    engine.dispose.assert_awaited_once()
    assert connection.queries == ["SHOW transaction_read_only"]


@pytest.mark.asyncio
async def test_readonly_enforcement_failure_stops_before_inspection(
    monkeypatch: pytest.MonkeyPatch, settings: AppSettings
) -> None:
    connection = FakeConnection()
    connection.read_only = "off"
    engine = SimpleNamespace(connect=lambda: connection, dispose=AsyncMock())
    monkeypatch.setattr(check, "create_async_db_engine", lambda *a, **k: engine)
    with pytest.raises(ValueError):
        async with check.readonly_connection(settings.database_url, 1):
            pytest.fail("must not yield a writable transaction")
    connection.rollback.assert_awaited_once()
    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_readonly_connection_disposes_after_probe_exception(
    monkeypatch: pytest.MonkeyPatch, settings: AppSettings
) -> None:
    connection = FakeConnection()
    engine = SimpleNamespace(connect=lambda: connection, dispose=AsyncMock())
    monkeypatch.setattr(check, "create_async_db_engine", lambda *a, **k: engine)
    with pytest.raises(RuntimeError):
        async with check.readonly_connection(settings.database_url, 1):
            raise RuntimeError("secret")
    connection.rollback.assert_awaited_once()
    engine.dispose.assert_awaited_once()


@pytest.mark.parametrize(
    "other",
    [
        "postgresql+asyncpg://audit:secret@other:5432/app",
        "postgresql+asyncpg://audit:secret@db:5433/app",
        "postgresql+asyncpg://audit:secret@db:5432/wrong",
        "postgresql+asyncpg://audit:secret@db:5432/app?host=other",
        "sqlite:///app",
    ],
)
def test_audit_endpoint_mismatch_is_rejected(settings: AppSettings, other: str) -> None:
    assert not check.same_database_endpoint(settings.database_url, other)


@pytest.mark.asyncio
async def test_audit_endpoint_not_opened_if_it_differs(
    monkeypatch: pytest.MonkeyPatch, settings: AppSettings
) -> None:
    runtime = FakeConnection(filtered=True)
    urls = fake_connections(monkeypatch, runtime)
    results = by_id(
        await check.inspect_database(
            settings,
            check.load_migration_plan(),
            "postgresql+asyncpg://audit:p@elsewhere/app",
            1,
        )
    )
    assert results["audit_endpoint"].status == check.Status.BLOCKED
    assert urls == [settings.database_url]


@pytest.mark.asyncio
@pytest.mark.parametrize("filtered", [False, True], ids=["all-tenant-visible", "rls-hidden"])
async def test_no_audit_url_requires_runtime_visibility_before_global_data_checks(
    monkeypatch: pytest.MonkeyPatch, settings: AppSettings, filtered: bool
) -> None:
    runtime = FakeConnection(filtered=filtered)
    urls = fake_connections(monkeypatch, runtime)

    results = by_id(await check.inspect_database(settings, check.load_migration_plan(), None, 1))

    assert {name: result.status for name, result in results.items()} == {
        "schema_revision": check.Status.PASS,
        "runtime_role": check.Status.PASS,
        "tenant_rls": check.Status.PASS,
        "vector_schema": check.Status.PASS,
        "vector_integrity": check.Status.UNVERIFIED if filtered else check.Status.PASS,
        "embedding_identity": check.Status.UNVERIFIED if filtered else check.Status.PASS,
        "rollback_1536": check.Status.UNVERIFIED if filtered else check.Status.PASS,
        "database_jobs": check.Status.UNVERIFIED if filtered else check.Status.PASS,
    }
    assert urls == [settings.database_url]
    assert runtime.queries == [
        "SELECT to_regclass('public.alembic_version')",
        "SELECT version_num FROM public.alembic_version",
        check.TABLE_SQL,
        check.ROLE_SQL,
        check.POLICY_SQL,
        check.VECTOR_SCHEMA_SQL,
        check.VISIBILITY_SQL,
        *([] if filtered else [check.VECTOR_DATA_SQL, check.JOB_DATA_SQL]),
    ]
    if filtered:
        for name in ("vector_integrity", "embedding_identity", "rollback_1536", "database_jobs"):
            assert results[name].facts == {}
            assert "cannot be treated as an empty database" in results[name].summary
    else:
        assert results["vector_integrity"].facts == {"rows_checked": 2, "invalid_rows": 0}
        assert runtime.parameters[-2] == {
            "provider": settings.embedding_provider,
            "model": settings.embedding_model,
            "version": settings.embedding_provider_version,
            "dimension": settings.embedding_dim,
        }


@pytest.mark.asyncio
async def test_no_audit_url_blocks_data_probes_until_vector_schema_is_understood(
    monkeypatch: pytest.MonkeyPatch, settings: AppSettings
) -> None:
    runtime = FakeConnection()
    runtime.rows[check.VECTOR_SCHEMA_SQL][0]["extension"] = False
    urls = fake_connections(monkeypatch, runtime)

    results = by_id(await check.inspect_database(settings, check.load_migration_plan(), None, 1))

    assert results["vector_schema"].status == check.Status.BLOCKED
    assert set(results) == {
        "schema_revision",
        "runtime_role",
        "tenant_rls",
        "vector_schema",
        "vector_integrity",
        "embedding_identity",
        "rollback_1536",
        "database_jobs",
    }
    for name in ("vector_integrity", "embedding_identity", "rollback_1536", "database_jobs"):
        assert results[name] == check.Check(
            name,
            check.Status.UNVERIFIED,
            "Vector schema must be understood before data checks.",
        )
    assert urls == [settings.database_url]
    assert runtime.queries == [
        "SELECT to_regclass('public.alembic_version')",
        "SELECT version_num FROM public.alembic_version",
        check.TABLE_SQL,
        check.ROLE_SQL,
        check.POLICY_SQL,
        check.VECTOR_SCHEMA_SQL,
    ]


@pytest.mark.asyncio
async def test_explicit_audit_connection_checks_all_tenants_but_runtime_separately(
    monkeypatch: pytest.MonkeyPatch,
    settings: AppSettings,
) -> None:
    runtime, audit = FakeConnection(filtered=True), FakeConnection()
    audit_url = "postgresql+asyncpg://audit:also-hidden@db/app"
    urls = fake_connections(monkeypatch, runtime, audit)
    results = by_id(
        await check.inspect_database(settings, check.load_migration_plan(), audit_url, 1)
    )
    assert all(result.status == check.Status.PASS for result in results.values())
    assert check.VECTOR_DATA_SQL not in runtime.queries
    assert check.ROLE_SQL not in audit.queries
    assert check.VECTOR_DATA_SQL in audit.queries
    assert urls == [settings.database_url, audit_url]
    serialized = json.dumps([check.asdict(result) for result in results.values()])
    assert "hidden" not in serialized and "fake-embedding" not in serialized


@pytest.mark.asyncio
async def test_audit_connection_failure_is_redacted_and_preserves_runtime_blocker(
    monkeypatch: pytest.MonkeyPatch,
    settings: AppSettings,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture,
) -> None:
    runtime = FakeConnection(filtered=True)
    runtime.rows[check.ROLE_SQL][0]["rolcreatedb"] = True
    audit_url = "postgresql+asyncpg://audit:audit-password-secret@db/app"
    diagnostic = f"{audit_url}; SELECT private_payload FROM secret_audit_records"
    runtime_engine = SimpleNamespace(connect=Mock(return_value=runtime), dispose=AsyncMock())
    audit_engine = SimpleNamespace(
        connect=Mock(side_effect=ConnectionError(diagnostic)), dispose=AsyncMock()
    )
    create_engine = Mock(side_effect=[runtime_engine, audit_engine])
    monkeypatch.setattr(check, "create_async_db_engine", create_engine)

    results = by_id(
        await check.inspect_database(settings, check.load_migration_plan(), audit_url, 1)
    )

    assert {name: result.status for name, result in results.items()} == {
        "schema_revision": check.Status.PASS,
        "runtime_role": check.Status.BLOCKED,
        "tenant_rls": check.Status.PASS,
        "vector_schema": check.Status.PASS,
        "vector_integrity": check.Status.UNVERIFIED,
        "embedding_identity": check.Status.UNVERIFIED,
        "rollback_1536": check.Status.UNVERIFIED,
        "database_jobs": check.Status.UNVERIFIED,
    }
    for name in ("vector_integrity", "embedding_identity", "rollback_1536", "database_jobs"):
        assert results[name] == check.Check(
            name,
            check.Status.UNVERIFIED,
            "Audit inspection failed or timed out; no global data-safety claim can be made.",
        )
    assert check.Report(tuple(results.values())).status == check.Status.BLOCKED
    assert [call.args[0] for call in create_engine.call_args_list] == [
        settings.database_url,
        audit_url,
    ]
    runtime_engine.connect.assert_called_once_with()
    audit_engine.connect.assert_called_once_with()
    runtime.rollback.assert_awaited_once()
    runtime_engine.dispose.assert_awaited_once()
    audit_engine.dispose.assert_awaited_once()
    assert check.VISIBILITY_SQL not in runtime.queries
    assert check.VECTOR_DATA_SQL not in runtime.queries
    assert check.JOB_DATA_SQL not in runtime.queries
    output = capsys.readouterr()
    serialized = json.dumps([check.asdict(result) for result in results.values()])
    for secret in ("audit-password-secret", "private_payload", "secret_audit_records", audit_url):
        assert secret not in serialized + caplog.text + output.out + output.err


@pytest.mark.asyncio
async def test_network_failure_is_unverified_and_redacted(
    monkeypatch: pytest.MonkeyPatch, settings: AppSettings
) -> None:
    @asynccontextmanager
    async def fail(*args: Any):
        raise ConnectionError("postgresql://user:password@secret-host/private-db")
        yield  # pragma: no cover

    monkeypatch.setattr(check, "readonly_connection", fail)
    results = await check.inspect_database(settings, check.load_migration_plan(), None, 1)
    assert by_id(results)["database_connection"].status == check.Status.UNVERIFIED
    assert "password" not in json.dumps([check.asdict(result) for result in results])


class FakePipeline:
    def __init__(self, connection: FakeRedis):
        self.connection = connection

    async def __aenter__(self) -> FakePipeline:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    def sismember(self, key: str, member: str) -> None:
        self.connection.commands.append(("SISMEMBER", key, member))

    def llen(self, key: str) -> None:
        self.connection.commands.append(("LLEN", key))

    def zcard(self, key: str) -> None:
        self.connection.commands.append(("ZCARD", key))

    async def execute(self) -> list[int]:
        return self.connection.values


class FakeRedis:
    def __init__(self, values: list[int]):
        self.values = values
        self.commands: list[tuple[str, ...]] = []
        self.aclose = AsyncMock()

    def pipeline(self, *, transaction: bool) -> FakePipeline:
        assert transaction is False
        return FakePipeline(self)


@pytest.mark.asyncio
@pytest.mark.parametrize("position", [1, 2, 3, 4, 5])
async def test_all_rq_active_states_block_without_cleanup(
    monkeypatch: pytest.MonkeyPatch, settings: AppSettings, position: int
) -> None:
    values = [1, 0, 0, 0, 0, 0, 0]
    values[position] = 1
    redis = FakeRedis(values)
    monkeypatch.setattr(check.Redis, "from_url", lambda *a, **k: redis)
    results = await check.inspect_queues(settings, 1)
    assert all(result.status == check.Status.BLOCKED for result in results)
    assert {command[0] for command in redis.commands} == {"SISMEMBER", "LLEN", "ZCARD"}
    assert any(command[1].endswith(":intermediate") for command in redis.commands)
    assert any(command[1].startswith("rq:scheduled:") for command in redis.commands)
    assert any(command[1].startswith("rq:wip:") for command in redis.commands)
    redis.aclose.assert_awaited_once()
    assert "private" not in json.dumps([check.asdict(result) for result in results])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "values,expected",
    [
        ([1, 0, 0, 0, 0, 0, 0], check.Status.PASS),
        ([0, 0, 0, 0, 0, 0, 0], check.Status.UNVERIFIED),
        ([1, 0, 0, 0, 0, 0, 1], check.Status.WARN),
    ],
)
async def test_redis_empty_unregistered_and_failed_states(
    monkeypatch: pytest.MonkeyPatch,
    settings: AppSettings,
    values: list[int],
    expected: check.Status,
) -> None:
    redis = FakeRedis(values)
    monkeypatch.setattr(check.Redis, "from_url", lambda *a, **k: redis)
    results = await check.inspect_queues(settings, 1)
    assert all(result.status == expected for result in results)
    assert len(results) == 2


@pytest.mark.asyncio
async def test_redis_failure_does_not_expose_credentials(
    monkeypatch: pytest.MonkeyPatch, settings: AppSettings
) -> None:
    def fail(*args: Any, **kwargs: Any) -> None:
        raise ConnectionError(settings.redis_url)

    monkeypatch.setattr(check.Redis, "from_url", fail)
    results = await check.inspect_queues(settings, 1)
    assert results[0].status == check.Status.UNVERIFIED
    assert "hidden" not in str(results)


@pytest.mark.asyncio
async def test_redis_options_cannot_override_bounded_timeout(
    monkeypatch: pytest.MonkeyPatch, settings: AppSettings
) -> None:
    monkeypatch.setattr(check.Redis, "from_url", lambda *a, **k: pytest.fail("must reject options"))
    settings = settings.model_copy(update={"redis_url": "redis://redis/0?socket_timeout=9999"})
    results = await check.inspect_queues(settings, 1)
    assert results[0].status == check.Status.UNVERIFIED


@pytest.mark.asyncio
async def test_automated_pass_never_claims_restore_or_quiescence(
    monkeypatch: pytest.MonkeyPatch, settings: AppSettings
) -> None:
    fake_connections(monkeypatch, FakeConnection(filtered=True), FakeConnection())
    redis = FakeRedis([1, 0, 0, 0, 0, 0, 0])
    monkeypatch.setattr(check.Redis, "from_url", lambda *a, **k: redis)
    result = await check.run_checks(
        settings,
        audit_url="postgresql+asyncpg://audit:secret@db/app",
        timeout=1,
    )
    assert result.to_dict()["automated_status"] == "PASS"
    assert result.status == check.Status.UNVERIFIED
    assert result.exit_code == 3
    assert by_id(result.checks)["backup_restore"].status == check.Status.UNVERIFIED
    assert by_id(result.checks)["write_quiescence"].status == check.Status.UNVERIFIED


@pytest.mark.parametrize(
    "statuses,expected,exit_code",
    [
        ([check.Status.PASS], check.Status.PASS, 0),
        ([check.Status.PASS, check.Status.WARN], check.Status.WARN, 1),
        ([check.Status.UNVERIFIED, check.Status.BLOCKED], check.Status.BLOCKED, 2),
        ([check.Status.UNVERIFIED, check.Status.WARN], check.Status.UNVERIFIED, 3),
        ([], check.Status.UNVERIFIED, 3),
    ],
)
def test_report_precedence_and_exit_codes(
    statuses: list[check.Status], expected: check.Status, exit_code: int
) -> None:
    result = check.Report(
        tuple(check.Check(str(index), status, "safe") for index, status in enumerate(statuses))
    )
    assert result.status == expected
    assert result.exit_code == exit_code
    assert result.to_dict()["schema_version"] == 1


@pytest.mark.parametrize(
    "argv",
    [
        ["--password", "do-not-print-this"],
        ["--timeout-seconds", "nan"],
        ["--timeout-seconds", "inf"],
        ["--timeout-seconds", "0"],
        ["--timeout-seconds", "61"],
    ],
)
def test_cli_invalid_arguments_redacted_json(
    argv: list[str], capsys: pytest.CaptureFixture
) -> None:
    assert cli.main(argv) == 2
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["status"] == "BLOCKED"
    assert captured.err == ""
    assert "do-not-print-this" not in captured.out


def test_cli_settings_error_redacted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    def fail():
        raise ValueError("password-secret-db-url")

    monkeypatch.setattr(cli, "load_settings", fail)
    assert cli.main([]) == 2
    output = capsys.readouterr()
    assert "password-secret" not in output.out + output.err
    assert json.loads(output.out)["status"] == "BLOCKED"


def test_cli_json_success_path_retains_manual_unverified(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, settings: AppSettings
) -> None:
    report = check.Report(
        tuple(
            check.manual_checks()
            + [
                check.Check("schema_revision", check.Status.PASS, "safe"),
            ]
        ),
        check.VECTOR_REVISION,
    )
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(cli, "run_checks", AsyncMock(return_value=report))
    assert cli.main([]) == 3
    output = capsys.readouterr()
    assert json.loads(output.out)["automated_status"] == "PASS"
    assert output.err == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("timeout", [float("nan"), float("inf"), 0, 61])
async def test_invalid_timeout_cannot_reach_io(settings: AppSettings, timeout: float) -> None:
    report = await check.run_checks(settings, timeout=timeout)
    assert report.status == check.Status.BLOCKED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"vector_store_type": "faiss"},
        {"vector_store_type": "milvus"},
        {"vector_store_type": "fake"},
        {"vector_index_dim": 16},
    ],
    ids=["faiss", "milvus", "fake-store", "dimension-mismatch"],
)
async def test_unsupported_vector_configuration_blocks_before_metadata_or_network_io(
    monkeypatch: pytest.MonkeyPatch, settings: AppSettings, changes: dict[str, Any]
) -> None:
    load_metadata = Mock(side_effect=AssertionError("must not read migration metadata"))
    create_engine = Mock(side_effect=AssertionError("must not create a database engine"))
    create_redis = Mock(side_effect=AssertionError("must not create a Redis connection"))
    monkeypatch.setattr(check, "load_migration_plan", load_metadata)
    monkeypatch.setattr(check, "create_async_db_engine", create_engine)
    monkeypatch.setattr(check.Redis, "from_url", create_redis)

    report = await check.run_checks(settings.model_copy(update=changes))

    assert report.status == check.Status.BLOCKED
    assert report.exit_code == 2
    assert report.target_revision is None
    assert report.to_dict()["automated_status"] == "BLOCKED"
    assert by_id(report.checks)["configuration"] == check.Check(
        "configuration",
        check.Status.BLOCKED,
        "This preflight requires pgvector and matching embedding/index dimensions.",
    )
    assert {result.id for result in report.checks} == {
        "backup_restore",
        "write_quiescence",
        "deployment_acceptance",
        "configuration",
    }
    load_metadata.assert_not_called()
    create_engine.assert_not_called()
    create_redis.assert_not_called()


@pytest.mark.asyncio
async def test_missing_connections_and_metadata_fail_closed(
    settings: AppSettings, tmp_path: Path
) -> None:
    report = await check.run_checks(settings, root=tmp_path)
    assert report.status == check.Status.BLOCKED
    settings = settings.model_copy(update={"database_url": None, "redis_url": None})
    report = await check.run_checks(settings)
    assert report.status == check.Status.UNVERIFIED
    assert report.to_dict()["automated_status"] == "UNVERIFIED"


@pytest.mark.asyncio
async def test_job_probe_failure_preserves_known_vector_blocker(settings: AppSettings) -> None:
    connection = FakeConnection()
    connection.rows[check.VECTOR_DATA_SQL] = [vector_data(invalid=1)]
    connection.rows.pop(check.JOB_DATA_SQL)
    results = by_id(await check.inspect_all_tenant_data(connection, settings))
    assert results["vector_integrity"].status == check.Status.BLOCKED
    assert results["database_jobs"].status == check.Status.UNVERIFIED


@pytest.mark.asyncio
async def test_later_queue_failure_preserves_earlier_blocker(
    monkeypatch: pytest.MonkeyPatch,
    settings: AppSettings,
) -> None:
    redis = FakeRedis([1, 1, 0, 0, 0, 0, 0])
    original = redis.pipeline
    count = 0

    def pipeline(*, transaction: bool):
        nonlocal count
        count += 1
        if count > 1:
            raise TimeoutError("secret-endpoint")
        return original(transaction=transaction)

    monkeypatch.setattr(redis, "pipeline", pipeline)
    monkeypatch.setattr(check.Redis, "from_url", lambda *a, **k: redis)
    results = by_id(await check.inspect_queues(settings, 1))
    assert results["redis_queue_1"].status == check.Status.BLOCKED
    assert results["redis_queues"].status == check.Status.UNVERIFIED
    assert "secret-endpoint" not in str(results)


@pytest.mark.asyncio
async def test_redis_close_failure_keeps_findings_redacted(
    monkeypatch: pytest.MonkeyPatch,
    settings: AppSettings,
) -> None:
    redis = FakeRedis([1, 1, 0, 0, 0, 0, 0])
    redis.aclose.side_effect = TimeoutError("secret-endpoint")
    monkeypatch.setattr(check.Redis, "from_url", lambda *a, **k: redis)
    results = by_id(await check.inspect_queues(settings, 1))
    assert results["redis_queue_1"].status == check.Status.BLOCKED
    assert results["redis_disconnect"].status == check.Status.UNVERIFIED
    assert "secret-endpoint" not in str(results)


def test_cli_does_not_change_parent_logging_configuration(
    monkeypatch: pytest.MonkeyPatch,
    settings: AppSettings,
    capsys: pytest.CaptureFixture,
) -> None:
    import logging

    logger = logging.getLogger("sqlalchemy.engine")
    original = logger.level
    monkeypatch.setattr(cli, "load_settings", lambda: settings)
    monkeypatch.setattr(
        cli,
        "run_checks",
        AsyncMock(
            return_value=check.Report(
                tuple(check.manual_checks()),
            )
        ),
    )
    cli.main([])
    assert logger.level == original
    assert json.loads(capsys.readouterr().out)["status"] == "UNVERIFIED"

"""0018 preflight stays read-only and never treats filtered state as absent."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from packages.operations import durable_upgrade as durable
from packages.operations import upgrade_check as check
from tests.unit.operations.test_upgrade_readiness import (
    FakeConnection,
    by_id,
    fake_connections,
    policies,
    tables,
    vector_schema,
)
from tests.unit.operations.test_upgrade_readiness import (
    settings as settings,
)


def columns():
    return [
        {"relname": table, "attname": name, "typname": kind, "attnotnull": required}
        for table, expected in durable.EXPECTED_COLUMNS.items()
        for name, (kind, required) in expected.items()
    ]


def constraints():
    result = [
        {"relname": table, "definition": "PRIMARY KEY (id)", "convalidated": True}
        for table in durable.DURABLE_TABLES
    ]
    result.append(
        {
            "relname": "durable_agent_runs",
            "definition": "CHECK ((revision >= 0))",
            "convalidated": True,
        }
    )
    for table, unique in (
        ("durable_tool_ledger", "call_id"),
        ("durable_graph_checkpoints", "checkpoint_id"),
    ):
        for definition in (
            f"UNIQUE ({unique})",
            "FOREIGN KEY (run_id) REFERENCES durable_agent_runs(id) ON DELETE CASCADE",
        ):
            result.append({"relname": table, "definition": definition, "convalidated": True})
    return result


def connection(*, filtered=False, retained=0, unresolved=0, unsupported_schema=0):
    value = FakeConnection(revision=durable.DURABLE_REVISION)
    value.rows[check.VECTOR_SCHEMA_SQL] = [vector_schema()]
    value.rows[durable.COLUMN_SQL] = columns()
    value.rows[durable.CONSTRAINT_SQL] = constraints()
    value.rows[durable.VISIBILITY_SQL] = [
        {
            "relname": "durable_agent_runs",
            "relkind": "r",
            "filtered": filtered,
            "readable": True,
        }
    ]
    value.rows[durable.DATA_SQL] = [
        {
            "retained": retained,
            "unresolved": unresolved,
            "unsupported_schema": unsupported_schema,
            "expired": 0,
        }
    ]
    return value


def test_current_and_historical_rls_table_sets_are_revision_aware():
    legacy_tables = [row for row in tables() if row["relname"] not in durable.DURABLE_TABLES]
    legacy_policies = [row for row in policies() if row["relname"] not in durable.DURABLE_TABLES]
    for revision in (check.RLS_REVISION, check.VECTOR_REVISION):
        result = check.check_rls(legacy_tables, legacy_policies, revision)
        assert result.status == check.Status.PASS
        assert result.facts["tables_checked"] == 18
    assert (
        check.check_rls(legacy_tables, legacy_policies, durable.DURABLE_REVISION).status
        == check.Status.BLOCKED
    )
    assert (
        check.check_rls(tables(), policies(), durable.DURABLE_REVISION).facts["tables_checked"]
        == 21
    )


def test_0018_preserves_vector_dimension_contract():
    assert (
        check.check_vector_schema([vector_schema()], durable.DURABLE_REVISION).status
        == check.Status.PASS
    )
    invalid = vector_schema()
    invalid["convalidated"] = False
    assert (
        check.check_vector_schema([invalid], durable.DURABLE_REVISION).status
        == check.Status.BLOCKED
    )


@pytest.mark.parametrize(
    "damage", ["owner", "nullable", "json_type", "revision", "foreign_key", "unique", "unvalidated"]
)
def test_durable_schema_safety_drift_fails_closed(damage):
    fields, rules = columns(), constraints()
    assert durable.schema_is_valid(fields, rules)
    if damage == "owner":
        fields = [row for row in fields if row["attname"] != "tenant_id"]
    elif damage == "nullable":
        next(row for row in fields if row["attname"] == "snapshot")["attnotnull"] = False
    elif damage == "json_type":
        next(row for row in fields if row["attname"] == "snapshot")["typname"] = "text"
    elif damage == "revision":
        rules = [row for row in rules if not row["definition"].startswith("CHECK")]
    elif damage == "foreign_key":
        rules = [row for row in rules if not row["definition"].startswith("FOREIGN")]
    elif damage == "unique":
        rules = [row for row in rules if not row["definition"].startswith("UNIQUE")]
    else:
        rules[0]["convalidated"] = False
    assert not durable.schema_is_valid(fields, rules)


@pytest.mark.asyncio
@pytest.mark.parametrize("filtered", [False, True])
async def test_0018_read_only_inspection_never_exports_private_payloads(
    monkeypatch, settings, filtered
):
    runtime = connection(filtered=filtered)
    fake_connections(monkeypatch, runtime)
    results = by_id(await check.inspect_database(settings, check.load_migration_plan(), None, 1))
    assert results["durable_schema"].status == check.Status.PASS
    assert results["durable_runs"].status == (
        check.Status.UNVERIFIED if filtered else check.Status.PASS
    )
    assert (durable.DATA_SQL in runtime.queries) is not filtered
    assert all(sql.lstrip().startswith("SELECT") for sql in runtime.queries)
    assert not any("snapshot" in sql or "entry" in sql for sql in runtime.queries)
    assert "hidden" not in json.dumps([check.asdict(item) for item in results.values()])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "retained,unresolved,unsupported,expected",
    [
        (0, 0, 0, check.Status.PASS),
        (1, 0, 0, check.Status.WARN),
        (1, 1, 0, check.Status.BLOCKED),
        (1, 0, 1, check.Status.BLOCKED),
    ],
)
async def test_retained_or_unresolved_runs_are_not_safe_to_drop(
    retained, unresolved, unsupported, expected
):
    result = await check.inspect_durable_data(
        connection(retained=retained, unresolved=unresolved, unsupported_schema=unsupported),
        True,
    )
    assert result.status == expected
    assert result.facts["retained"] == retained


@pytest.mark.asyncio
async def test_bad_schema_blocks_private_state_query():
    runtime = connection()
    result = await check.inspect_durable_data(runtime, False)
    assert result.status == check.Status.UNVERIFIED
    assert not runtime.queries


@pytest.mark.asyncio
async def test_0018_acceptance_remains_manual_and_unverified(monkeypatch, settings):
    monkeypatch.setattr(check, "inspect_database", AsyncMock(return_value=[]))
    monkeypatch.setattr(check, "inspect_queues", AsyncMock(return_value=[]))
    report = await check.run_checks(settings)
    result = by_id(report.checks)["durable_recovery"]
    assert result.manual and result.status == check.Status.UNVERIFIED
    assert report.target_revision == "20261016_0021"

"""Independent read-only 0018 preflight boundary probes; no database connection."""

from __future__ import annotations

import pytest

from packages.operations import durable_upgrade as durable
from packages.operations import upgrade_check as check
from tests.unit.operations.test_durable_upgrade_readiness import connection
from tests.unit.operations.test_upgrade_readiness import policies, tables


@pytest.mark.parametrize("damage", ["missing", "view", "filtered", "unreadable", "wrong_table"])
async def test_no_global_durable_claim_without_exact_unfiltered_visibility(damage) -> None:
    fake = connection()
    rows = fake.rows[durable.VISIBILITY_SQL]
    if damage == "missing":
        fake.rows[durable.VISIBILITY_SQL] = []
    elif damage == "view":
        rows[0]["relkind"] = "v"
    elif damage == "filtered":
        rows[0]["filtered"] = True
    elif damage == "unreadable":
        rows[0]["readable"] = False
    else:
        rows[0]["relname"] = "another_table"
    result = await check.inspect_durable_data(fake, True)
    assert result.status == check.Status.UNVERIFIED
    assert durable.DATA_SQL not in fake.queries
    assert not result.facts


@pytest.mark.parametrize("table_name", durable.DURABLE_TABLES)
def test_0018_rls_never_inherits_historical_table_exemption(table_name) -> None:
    all_tables, all_policies = tables(), policies()
    next(row for row in all_tables if row["relname"] == table_name)["relforcerowsecurity"] = False
    result = check.check_rls(all_tables, all_policies, durable.DURABLE_REVISION)
    assert result.status == check.Status.BLOCKED
    assert result.facts["tables_checked"] == 21
    assert result.facts["tables_with_drift"] == 1


async def test_global_empty_count_does_not_certify_migration_acceptance() -> None:
    empty = await check.inspect_durable_data(connection(), True)
    assert empty.status == check.Status.PASS
    report = check.Report((*check.manual_checks(), empty), durable.DURABLE_REVISION)
    assert report.status == check.Status.UNVERIFIED
    assert report.exit_code != 0

"""Read-only metadata and aggregate checks for the explicit 0018 migration.

Never read snapshot/ledger/checkpoint JSON or grant all-tenant access. A filtered
runtime connection is not evidence that private recovery state is absent.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

DURABLE_TABLES = ("durable_agent_runs", "durable_tool_ledger", "durable_graph_checkpoints")
DURABLE_REVISION = "20261015_0018"

COLUMN_SQL = """
SELECT c.relname, a.attname, t.typname, a.attnotnull
FROM pg_catalog.pg_attribute AS a
JOIN pg_catalog.pg_class AS c ON c.oid = a.attrelid
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
JOIN pg_catalog.pg_type AS t ON t.oid = a.atttypid
WHERE n.nspname = 'public' AND c.relname = ANY(:tables)
  AND a.attnum > 0 AND NOT a.attisdropped
"""
CONSTRAINT_SQL = """
SELECT c.relname, con.contype, con.convalidated,
       pg_get_constraintdef(con.oid) AS definition
FROM pg_catalog.pg_constraint AS con
JOIN pg_catalog.pg_class AS c ON c.oid = con.conrelid
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = ANY(:tables)
"""
VISIBILITY_SQL = """
SELECT c.relname, c.relkind, row_security_active(c.oid) AS filtered,
       has_table_privilege(current_user, c.oid, 'SELECT') AS readable
FROM pg_catalog.pg_class AS c
JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname = 'durable_agent_runs'
"""
DATA_SQL = """
SELECT count(*) AS retained,
       count(*) FILTER (WHERE status IS NULL OR status NOT IN
         ('completed', 'failed', 'stopped', 'cancelled')) AS unresolved,
       count(*) FILTER (WHERE schema_version IS DISTINCT FROM 1) AS unsupported_schema,
       count(*) FILTER (WHERE expires_at <= extract(epoch FROM CURRENT_TIMESTAMP)) AS expired
FROM public.durable_agent_runs
"""

# Required safety-bearing columns, including owner scope and lease fences. Extra
# columns do not silently certify another revision: the caller pins head 0018.
_COMMON = {
    "id": ("varchar", True),
    "tenant_id": ("varchar", True),
    "user_id": ("varchar", True),
    "status": ("varchar", True),
}
EXPECTED_COLUMNS = {
    "durable_agent_runs": {
        **_COMMON,
        "schema_version": ("int4", True),
        "revision": ("int4", True),
        "snapshot": ("json", True),
        "expires_at": ("float8", True),
        "lease_token": ("varchar", False),
        "lease_until": ("float8", False),
    },
    "durable_tool_ledger": {
        **_COMMON,
        "run_id": ("varchar", True),
        "call_id": ("varchar", True),
        "entry": ("json", True),
    },
    "durable_graph_checkpoints": {
        **_COMMON,
        "run_id": ("varchar", True),
        "checkpoint_id": ("varchar", True),
        "checkpoint": ("json", True),
        "metadata": ("json", True),
        "parent_checkpoint_id": ("varchar", False),
        "writes": ("json", True),
    },
}


def schema_is_valid(
    columns: Sequence[Mapping[str, Any]],
    constraints: Sequence[Mapping[str, Any]],
) -> bool:
    from packages.operations.upgrade_check import _canonical

    by_column = {(row["relname"], row["attname"]): row for row in columns}
    for table, expected in EXPECTED_COLUMNS.items():
        for name, (kind, required) in expected.items():
            row = by_column.get((table, name))
            if row is None or (row["typname"], row["attnotnull"]) != (kind, required):
                return False
    expected_constraints = {
        "durable_agent_runs": ("PRIMARY KEY (id)", "CHECK (revision >= 0)"),
        "durable_tool_ledger": (
            "PRIMARY KEY (id)",
            "UNIQUE (call_id)",
            "FOREIGN KEY (run_id) REFERENCES durable_agent_runs(id) ON DELETE CASCADE",
        ),
        "durable_graph_checkpoints": (
            "PRIMARY KEY (id)",
            "UNIQUE (checkpoint_id)",
            "FOREIGN KEY (run_id) REFERENCES durable_agent_runs(id) ON DELETE CASCADE",
        ),
    }
    for table, definitions in expected_constraints.items():
        actual = {
            _canonical(row["definition"])
            for row in constraints
            if row["relname"] == table and row["convalidated"]
        }
        if not {_canonical(value) for value in definitions} <= actual:
            return False
    return True

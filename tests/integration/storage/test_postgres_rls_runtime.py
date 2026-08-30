"""Optional black-box checks for the non-privileged PostgreSQL runtime role.

Set ``AEGISRAG_RLS_ADMIN_DSN``, ``AEGISRAG_RLS_RUNTIME_DSN`` and
``AEGISRAG_RLS_RUNTIME_USER`` to run this against a real migrated database.
The default test suite skips it when PostgreSQL credentials are unavailable.
"""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from psycopg import Connection, connect
from psycopg.errors import InsufficientPrivilege


def _dsn(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


def test_runtime_role_is_non_privileged_and_rls_is_fail_closed() -> None:
    admin_dsn = _dsn("AEGISRAG_RLS_ADMIN_DSN")
    runtime_dsn = _dsn("AEGISRAG_RLS_RUNTIME_DSN")
    runtime_user = _dsn("AEGISRAG_RLS_RUNTIME_USER")
    if admin_dsn is None or runtime_dsn is None or runtime_user is None:
        pytest.skip("Set AEGISRAG_RLS_* variables to run the PostgreSQL RLS probe.")

    table_name = f"rls_probe_{uuid4().hex}"
    quoted_table = f'"{table_name}"'
    admin: Connection[object] | None = None
    try:
        admin = connect(admin_dsn, autocommit=True)
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = %s",
                (runtime_user,),
            )
            role = cursor.fetchone()
            assert role == (False, False)
            cursor.execute(
                f"CREATE TABLE {quoted_table} (id integer PRIMARY KEY, tenant_id text NOT NULL)"
            )
            cursor.execute(
                f"ALTER TABLE {quoted_table} ENABLE ROW LEVEL SECURITY"
            )
            cursor.execute(
                f"ALTER TABLE {quoted_table} FORCE ROW LEVEL SECURITY"
            )
            cursor.execute(
                f"CREATE POLICY rls_probe_policy ON {quoted_table} "
                "USING (tenant_id = current_setting('app.tenant_id', true)) "
                "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
            )
            cursor.execute(
                f"GRANT SELECT, INSERT ON {quoted_table} TO \"{runtime_user}\""
            )
            cursor.execute(
                f"INSERT INTO {quoted_table} (id, tenant_id) "
                "VALUES (1, 'tenant-a'), (2, 'tenant-b')"
            )

        with connect(runtime_dsn, autocommit=True) as runtime:
            with runtime.cursor() as cursor:
                cursor.execute("SELECT current_user")
                assert cursor.fetchone() == (runtime_user,)
                cursor.execute(
                    "SELECT set_config('app.tenant_id', %s, false)",
                    ("tenant-a",),
                )
                cursor.execute(f"SELECT id FROM {quoted_table} ORDER BY id")
                assert cursor.fetchall() == [(1,)]
                cursor.execute(
                    "SELECT set_config('app.tenant_id', %s, false)",
                    ("tenant-b",),
                )
                cursor.execute(f"SELECT id FROM {quoted_table} ORDER BY id")
                assert cursor.fetchall() == [(2,)]
                cursor.execute("SELECT set_config('app.tenant_id', NULL, false)")
                cursor.execute(f"SELECT id FROM {quoted_table}")
                assert cursor.fetchall() == []
                with pytest.raises(InsufficientPrivilege):
                    cursor.execute(
                        f"INSERT INTO {quoted_table} (id, tenant_id) VALUES (3, 'tenant-a')"
                    )
    finally:
        if admin is not None:
            with admin.cursor() as cursor:
                cursor.execute(f"DROP TABLE IF EXISTS {quoted_table}")
            admin.close()

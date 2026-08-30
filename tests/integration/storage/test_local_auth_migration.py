"""Test that the 20260618_0014 migration creates local_users and user_groups."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _sqlite_async_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def _sqlite_sync_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def test_local_auth_migration_creates_user_groups_and_local_users_tables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "local_auth.db"
    monkeypatch.setenv("DATABASE_URL", _sqlite_async_url(database_path))
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))

    command.upgrade(config, "head")

    engine = create_engine(_sqlite_sync_url(database_path))
    try:
        inspector = inspect(engine)
        table_names = set(inspector.get_table_names())

        # Check both tables exist
        assert "user_groups" in table_names, f"user_groups not found in {table_names}"
        assert "local_users" in table_names, f"local_users not found in {table_names}"

        # Check user_groups columns
        ug_columns = {col["name"] for col in inspector.get_columns("user_groups")}
        expected = {
            "id", "tenant_id", "name", "description", "roles",
            "permissions", "created_at", "updated_at",
        }
        assert expected <= ug_columns

        # Check local_users columns
        lu_columns = {col["name"] for col in inspector.get_columns("local_users")}
        assert {
            "id",
            "tenant_id",
            "username",
            "password_hash",
            "email",
            "display_name",
            "is_active",
            "auth_version",
            "group_id",
            "created_at",
            "updated_at",
        } <= lu_columns

        # Check foreign key
        fks = inspector.get_foreign_keys("local_users")
        fk_columns = {
            (tuple(fk["constrained_columns"]), fk["referred_table"], tuple(fk["referred_columns"]))
            for fk in fks
        }
        assert (("tenant_id",), "tenants", ("id",)) in fk_columns
        assert (
            ("tenant_id", "group_id"),
            "user_groups",
            ("tenant_id", "id"),
        ) in fk_columns

        # Check unique constraints
        ug_uniques = {uc["name"] for uc in inspector.get_unique_constraints("user_groups")}
        assert "uq_user_groups_tenant_name" in ug_uniques

        lu_uniques = {uc["name"] for uc in inspector.get_unique_constraints("local_users")}
        assert "uq_local_users_tenant_username" in lu_uniques

    finally:
        engine.dispose()


def test_local_auth_downgrade_preflights_cross_tenant_name_conflicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "local_auth_downgrade.db"
    monkeypatch.setenv("DATABASE_URL", _sqlite_async_url(database_path))
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    command.upgrade(config, "head")

    engine = create_engine(_sqlite_sync_url(database_path))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO tenants (
                        id, created_at, updated_at, created_by, status, name, metadata
                    ) VALUES (
                        'tenant-b', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL,
                        'active', 'Tenant B', '{}'
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO user_groups (
                        id, tenant_id, name, description, roles, permissions,
                        created_at, updated_at
                    ) VALUES
                        ('group-default', 'default', 'Readers', NULL, NULL, NULL,
                         CURRENT_TIMESTAMP, CURRENT_TIMESTAMP),
                        ('group-b', 'tenant-b', 'Readers', NULL, NULL, NULL,
                         CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """
                )
            )
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="cannot downgrade tenant-scoped local auth"):
        command.downgrade(config, "20260618_0014")

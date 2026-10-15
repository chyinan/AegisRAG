"""Scope local authentication users and groups to tenants.

Revision ID: 20260830_0015
Revises: 20260618_0014
Create Date: 2026-08-30 00:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260830_0015"
down_revision: str | None = "20260618_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_TENANT_ID = "default"
_FK_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            INSERT INTO tenants (
                id, created_at, updated_at, created_by, status, name, metadata
            )
            SELECT
                :tenant_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL,
                'active', 'Default Tenant', :metadata
            WHERE NOT EXISTS (
                SELECT 1 FROM tenants WHERE id = :tenant_id
            )
            """
        ).bindparams(sa.bindparam("tenant_id", type_=sa.String(length=36))),
        {"tenant_id": _DEFAULT_TENANT_ID, "metadata": "{}"},
    )

    op.add_column("user_groups", sa.Column("tenant_id", sa.String(length=36), nullable=True))
    op.add_column("local_users", sa.Column("tenant_id", sa.String(length=36), nullable=True))
    op.add_column(
        "local_users",
        sa.Column(
            "auth_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    connection.execute(
        sa.text("UPDATE user_groups SET tenant_id = :tenant_id WHERE tenant_id IS NULL"),
        {"tenant_id": _DEFAULT_TENANT_ID},
    )
    connection.execute(
        sa.text("UPDATE local_users SET tenant_id = :tenant_id WHERE tenant_id IS NULL"),
        {"tenant_id": _DEFAULT_TENANT_ID},
    )

    with op.batch_alter_table(
        "user_groups",
        naming_convention=_FK_NAMING_CONVENTION,
    ) as batch_op:
        batch_op.alter_column("tenant_id", existing_type=sa.String(length=36), nullable=False)
        batch_op.drop_constraint("uq_user_groups_name", type_="unique")
        batch_op.create_unique_constraint(
            "uq_user_groups_tenant_id_id",
            ["tenant_id", "id"],
        )
        batch_op.create_unique_constraint(
            "uq_user_groups_tenant_name",
            ["tenant_id", "name"],
        )
        batch_op.create_foreign_key(
            "fk_user_groups_tenant_id_tenants",
            "tenants",
            ["tenant_id"],
            ["id"],
            ondelete="CASCADE",
        )

    old_group_fk_name = _local_user_group_fk_name(connection)
    with op.batch_alter_table(
        "local_users",
        naming_convention=_FK_NAMING_CONVENTION,
    ) as batch_op:
        batch_op.alter_column("tenant_id", existing_type=sa.String(length=36), nullable=False)
        batch_op.drop_constraint("uq_local_users_username", type_="unique")
        batch_op.drop_constraint(old_group_fk_name, type_="foreignkey")
        batch_op.create_unique_constraint(
            "uq_local_users_tenant_username",
            ["tenant_id", "username"],
        )
        batch_op.create_foreign_key(
            "fk_local_users_tenant_id_tenants",
            "tenants",
            ["tenant_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "fk_local_users_tenant_group",
            "user_groups",
            ["tenant_id", "group_id"],
            ["tenant_id", "id"],
        )

    op.create_index("ix_user_groups_tenant_id", "user_groups", ["tenant_id"])
    op.create_index("ix_local_users_tenant_id", "local_users", ["tenant_id"])
    op.create_index(
        "ix_local_users_tenant_group",
        "local_users",
        ["tenant_id", "group_id"],
    )


def downgrade() -> None:
    _assert_downgrade_is_safe(op.get_bind())
    op.drop_index("ix_local_users_tenant_group", table_name="local_users")
    op.drop_index("ix_local_users_tenant_id", table_name="local_users")
    op.drop_index("ix_user_groups_tenant_id", table_name="user_groups")

    with op.batch_alter_table(
        "local_users",
        naming_convention=_FK_NAMING_CONVENTION,
    ) as batch_op:
        batch_op.drop_constraint("fk_local_users_tenant_group", type_="foreignkey")
        batch_op.drop_constraint("fk_local_users_tenant_id_tenants", type_="foreignkey")
        batch_op.drop_constraint("uq_local_users_tenant_username", type_="unique")
        batch_op.create_unique_constraint("uq_local_users_username", ["username"])
        batch_op.create_foreign_key(
            "fk_local_users_group_id_user_groups",
            "user_groups",
            ["group_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.drop_column("auth_version")
        batch_op.drop_column("tenant_id")

    with op.batch_alter_table(
        "user_groups",
        naming_convention=_FK_NAMING_CONVENTION,
    ) as batch_op:
        batch_op.drop_constraint("fk_user_groups_tenant_id_tenants", type_="foreignkey")
        batch_op.drop_constraint("uq_user_groups_tenant_id_id", type_="unique")
        batch_op.drop_constraint("uq_user_groups_tenant_name", type_="unique")
        batch_op.create_unique_constraint("uq_user_groups_name", ["name"])
        batch_op.drop_column("tenant_id")


def _local_user_group_fk_name(connection: sa.Connection) -> str:
    foreign_keys = sa.inspect(connection).get_foreign_keys("local_users")
    for foreign_key in foreign_keys:
        if (
            foreign_key.get("referred_table") == "user_groups"
            and foreign_key.get("constrained_columns") == ["group_id"]
        ):
            name = foreign_key.get("name")
            if isinstance(name, str) and name:
                return name
            return "fk_local_users_group_id_user_groups"
    raise RuntimeError("local_users.group_id foreign key was not found")


def _assert_downgrade_is_safe(connection: sa.Connection) -> None:
    group_conflict = connection.execute(
        sa.text(
            """
            SELECT name
            FROM user_groups
            GROUP BY name
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).scalar_one_or_none()
    user_conflict = connection.execute(
        sa.text(
            """
            SELECT username
            FROM local_users
            GROUP BY username
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).scalar_one_or_none()
    if group_conflict is not None or user_conflict is not None:
        raise RuntimeError(
            "cannot downgrade tenant-scoped local auth while duplicate group names or "
            "usernames exist across tenants"
        )

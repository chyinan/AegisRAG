"""Durable opt-in folder synchronization plans.

Revision ID: 20261016_0019
Revises: 20261015_0018
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261016_0019"
down_revision: str | None = "20261015_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "folder_sync_plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("connector_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.Float(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_until", sa.Float()),
        sa.UniqueConstraint("tenant_id", "connector_id", name="uq_folder_sync_tenant_connector"),
    )
    op.create_index(
        "ix_folder_sync_plans_due", "folder_sync_plans", ["tenant_id", "enabled", "next_run_at"]
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(sa.text("ALTER TABLE folder_sync_plans ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text("ALTER TABLE folder_sync_plans FORCE ROW LEVEL SECURITY"))
        predicate = "tenant_id = current_setting('app.tenant_id', true)"
        op.execute(
            sa.text(
                f"CREATE POLICY tenant_isolation_folder_sync_plans ON folder_sync_plans "
                f"USING ({predicate}) WITH CHECK ({predicate})"
            )
        )


def downgrade() -> None:
    op.drop_table("folder_sync_plans")

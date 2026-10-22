"""Private durable agent snapshots, result ledger and real graph checkpoints.

Revision ID: 20261015_0018
Revises: 20261014_0017
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision: str = "20261015_0018"
down_revision: str | None = "20261014_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None
_TABLES = ("durable_agent_runs", "durable_tool_ledger", "durable_graph_checkpoints")


def _base_columns(status_length: int = 32) -> list[sa.Column[str] | sa.Column[datetime]]:
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("status", sa.String(status_length), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "durable_agent_runs",
        *_base_columns(),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.Float(), nullable=False),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_until", sa.Float()),
        sa.CheckConstraint("revision >= 0", name="ck_durable_agent_runs_revision"),
    )
    op.create_index(
        "ix_durable_agent_runs_owner", "durable_agent_runs", ["tenant_id", "user_id", "id"]
    )
    op.create_index("ix_durable_agent_runs_expires", "durable_agent_runs", ["expires_at"])
    op.create_table(
        "durable_tool_ledger",
        *_base_columns(16),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("durable_agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("call_id", sa.String(128), unique=True, nullable=False),
        sa.Column("entry", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_durable_tool_ledger_run", "durable_tool_ledger", ["tenant_id", "user_id", "run_id"]
    )
    op.create_table(
        "durable_graph_checkpoints",
        *_base_columns(16),
        sa.Column(
            "run_id",
            sa.String(36),
            sa.ForeignKey("durable_agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("checkpoint_id", sa.String(64), unique=True, nullable=False),
        sa.Column("checkpoint", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("parent_checkpoint_id", sa.String(64)),
        sa.Column("writes", sa.JSON(), nullable=False),
    )
    op.create_index(
        "ix_durable_graph_owner_run",
        "durable_graph_checkpoints",
        ["tenant_id", "user_id", "run_id", "checkpoint_id"],
    )
    if op.get_bind().dialect.name == "postgresql":
        for table in _TABLES:
            op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
            op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
            predicate = "tenant_id = current_setting('app.tenant_id', true)"
            op.execute(
                sa.text(
                    f"CREATE POLICY tenant_isolation_{table} ON {table} "
                    f"USING ({predicate}) WITH CHECK ({predicate})"
                )
            )


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_table(table)

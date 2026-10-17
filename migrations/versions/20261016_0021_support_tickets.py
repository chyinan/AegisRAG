"""Approval-governed internal support tickets.

Revision ID: 20261016_0021
Revises: 20261016_0020
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261016_0021"
down_revision: str | None = "20261016_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "support_tickets",
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
        sa.Column("client_reference", sa.String(36), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("tool_version", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "status IN ('open', 'in_progress', 'resolved')", name="ck_ticket_status"
        ),
        sa.CheckConstraint("priority IN ('low', 'normal', 'high')", name="ck_ticket_priority"),
        sa.CheckConstraint("revision >= 1", name="ck_ticket_revision"),
    )
    op.create_index(
        "uq_support_ticket_owner_reference",
        "support_tickets",
        ["tenant_id", "user_id", "client_reference"],
        unique=True,
    )
    op.create_index(
        "ix_support_ticket_owner_created", "support_tickets", ["tenant_id", "user_id", "created_at"]
    )
    if op.get_bind().dialect.name == "postgresql":
        for statement in (
            "ALTER TABLE support_tickets ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE support_tickets FORCE ROW LEVEL SECURITY",
            "CREATE POLICY tenant_isolation_support_tickets ON support_tickets "
            "USING (tenant_id = current_setting('app.tenant_id', true)) "
            "WITH CHECK (tenant_id = current_setting('app.tenant_id', true))",
        ):
            op.execute(sa.text(statement))


def downgrade() -> None:
    op.drop_table("support_tickets")

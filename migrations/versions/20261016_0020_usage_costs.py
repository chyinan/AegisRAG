"""Versioned usage costs and atomic monthly monetary reservations.

Revision ID: 20261016_0020
Revises: 20261016_0019
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261016_0020"
down_revision: str | None = "20261016_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("period", sa.String(7), nullable=False),
        sa.Column("reserved_nanos", sa.BigInteger(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "cost_accounts",
        *_base(),
        sa.Column("spent_nanos", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "spent_nanos >= 0 AND reserved_nanos >= 0", name="ck_cost_account_amount"
        ),
    )
    op.create_index(
        "uq_cost_account_period", "cost_accounts", ["tenant_id", "currency", "period"], unique=True
    )
    op.create_table(
        "usage_charges",
        *_base(),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("request_id", sa.String(128), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("operation", sa.String(64), nullable=False),
        sa.Column("provider_version", sa.String(128)),
        sa.Column("price_version", sa.String(64)),
        sa.Column("price_snapshot", sa.JSON()),
        sa.Column("usage_known", sa.Boolean(), nullable=False),
        sa.Column("input_tokens", sa.BigInteger()),
        sa.Column("output_tokens", sa.BigInteger()),
        sa.Column("total_tokens", sa.BigInteger()),
        sa.Column("amount_nanos", sa.BigInteger()),
        sa.Column("latency_ms", sa.Float()),
        sa.CheckConstraint("reserved_nanos >= 0", name="ck_usage_charge_reserved"),
        sa.CheckConstraint(
            "amount_nanos IS NULL OR amount_nanos >= 0", name="ck_usage_charge_amount"
        ),
    )
    op.create_index("ix_usage_charge_tenant_created", "usage_charges", ["tenant_id", "created_at"])
    if op.get_bind().dialect.name == "postgresql":
        for table in ("cost_accounts", "usage_charges"):
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
    op.drop_table("usage_charges")
    op.drop_table("cost_accounts")

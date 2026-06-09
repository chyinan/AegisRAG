"""Add review queue items.

Revision ID: 20260609_0013
Revises: 20260609_0012
Create Date: 2026-06-09 19:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260609_0013"
down_revision: str | None = "20260609_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_items",
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("item_type", sa.String(length=64), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("source_view", sa.String(length=64), nullable=False),
        sa.Column("safe_identifiers", sa.JSON(), nullable=False),
        sa.Column("safe_summary", sa.JSON(), nullable=False),
        sa.Column("eval_candidate", sa.JSON(), nullable=True),
        sa.Column("status_history", sa.JSON(), nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_review_items_tenant_status_created",
        "review_items",
        ["tenant_id", "status", "created_at"],
    )
    op.create_index(
        "ix_review_items_tenant_type_created",
        "review_items",
        ["tenant_id", "item_type", "created_at"],
    )
    op.create_index(
        "ix_review_items_tenant_severity_created",
        "review_items",
        ["tenant_id", "severity", "created_at"],
    )
    op.create_index(
        "ix_review_items_tenant_request_trace",
        "review_items",
        ["tenant_id", "request_id", "trace_id"],
    )
    op.create_index(
        "ix_review_items_tenant_source_created",
        "review_items",
        ["tenant_id", "source_view", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_review_items_tenant_source_created", table_name="review_items")
    op.drop_index("ix_review_items_tenant_request_trace", table_name="review_items")
    op.drop_index("ix_review_items_tenant_severity_created", table_name="review_items")
    op.drop_index("ix_review_items_tenant_type_created", table_name="review_items")
    op.drop_index("ix_review_items_tenant_status_created", table_name="review_items")
    op.drop_table("review_items")

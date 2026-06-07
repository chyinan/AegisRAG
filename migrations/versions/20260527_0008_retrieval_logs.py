"""Add retrieval logs table.

Revision ID: 20260527_0008
Revises: 20260527_0007
Create Date: 2026-06-07 12:20:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260527_0008"
down_revision: str | None = "20260527_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "retrieval_logs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("top_k", sa.Integer(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("rerank_score", sa.Float(), nullable=True),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("query_summary", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.CheckConstraint("status in ('success', 'failure')", name="ck_retrieval_logs_status"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_retrieval_logs_request_id", "retrieval_logs", ["request_id"])
    op.create_index("ix_retrieval_logs_trace_id", "retrieval_logs", ["trace_id"])
    op.create_index("ix_retrieval_logs_tenant_id", "retrieval_logs", ["tenant_id"])
    op.create_index("ix_retrieval_logs_created_at", "retrieval_logs", ["created_at"])
    op.create_index(
        "ix_retrieval_logs_tenant_request",
        "retrieval_logs",
        ["tenant_id", "request_id"],
    )
    op.create_index(
        "ix_retrieval_logs_tenant_created",
        "retrieval_logs",
        ["tenant_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_retrieval_logs_tenant_created", table_name="retrieval_logs")
    op.drop_index("ix_retrieval_logs_tenant_request", table_name="retrieval_logs")
    op.drop_index("ix_retrieval_logs_created_at", table_name="retrieval_logs")
    op.drop_index("ix_retrieval_logs_tenant_id", table_name="retrieval_logs")
    op.drop_index("ix_retrieval_logs_trace_id", table_name="retrieval_logs")
    op.drop_index("ix_retrieval_logs_request_id", table_name="retrieval_logs")
    op.drop_table("retrieval_logs")

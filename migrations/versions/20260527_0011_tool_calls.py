"""Add durable tool call persistence.

Revision ID: 20260527_0011
Revises: 20260527_0010
Create Date: 2026-06-08 14:30:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260527_0011"
down_revision: str | None = "20260527_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tool_calls",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("agent_run_id", sa.String(length=36), nullable=False),
        sa.Column("tool_name", sa.String(length=128), nullable=False),
        sa.Column("permission", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("arguments_summary", sa.JSON(), nullable=False),
        sa.Column("result_summary", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "status in ('success', 'denied', 'failure')",
            name="ck_tool_calls_status",
        ),
        sa.CheckConstraint(
            "latency_ms >= 0",
            name="ck_tool_calls_latency_ms_nonnegative",
        ),
        sa.ForeignKeyConstraint(["agent_run_id"], ["agent_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tool_calls_agent_run_id", "tool_calls", ["agent_run_id"])
    op.create_index("ix_tool_calls_tool_name", "tool_calls", ["tool_name"])
    op.create_index("ix_tool_calls_status_created", "tool_calls", ["status", "created_at"])
    op.create_index(
        "ix_tool_calls_agent_run_tool_status",
        "tool_calls",
        ["agent_run_id", "tool_name", "status"],
    )
    op.create_index(
        "ix_tool_calls_tenant_user_created",
        "tool_calls",
        ["tenant_id", "user_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_tool_calls_tenant_user_created", table_name="tool_calls")
    op.drop_index("ix_tool_calls_agent_run_tool_status", table_name="tool_calls")
    op.drop_index("ix_tool_calls_status_created", table_name="tool_calls")
    op.drop_index("ix_tool_calls_tool_name", table_name="tool_calls")
    op.drop_index("ix_tool_calls_agent_run_id", table_name="tool_calls")
    op.drop_table("tool_calls")

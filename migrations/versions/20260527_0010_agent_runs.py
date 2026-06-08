"""Add agent run persistence.

Revision ID: 20260527_0010
Revises: 20260527_0009
Create Date: 2026-06-08 13:10:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260527_0010"
down_revision: str | None = "20260527_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("max_steps", sa.Integer(), nullable=False),
        sa.Column("max_tool_calls", sa.Integer(), nullable=False),
        sa.Column("timeout_seconds", sa.Float(), nullable=False),
        sa.Column("steps_used", sa.Integer(), nullable=False),
        sa.Column("tool_calls_used", sa.Integer(), nullable=False),
        sa.Column("termination_reason", sa.String(length=64), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=True),
        sa.Column("input_summary", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "status in ('running', 'completed', 'stopped', 'failed')",
            name="ck_agent_runs_status",
        ),
        sa.CheckConstraint("max_steps > 0", name="ck_agent_runs_max_steps_positive"),
        sa.CheckConstraint(
            "max_tool_calls >= 0",
            name="ck_agent_runs_max_tool_calls_nonnegative",
        ),
        sa.CheckConstraint("timeout_seconds > 0", name="ck_agent_runs_timeout_positive"),
        sa.CheckConstraint("steps_used >= 0", name="ck_agent_runs_steps_used_nonnegative"),
        sa.CheckConstraint(
            "tool_calls_used >= 0",
            name="ck_agent_runs_tool_calls_used_nonnegative",
        ),
        sa.CheckConstraint(
            "latency_ms is null or latency_ms >= 0",
            name="ck_agent_runs_latency_ms",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_runs_tenant_user_id",
        "agent_runs",
        ["tenant_id", "user_id", "id"],
    )
    op.create_index("ix_agent_runs_request_id", "agent_runs", ["request_id"])
    op.create_index(
        "ix_agent_runs_tenant_status_created",
        "agent_runs",
        ["tenant_id", "status", "created_at"],
    )
    op.create_index(
        "ix_agent_runs_tenant_user_status",
        "agent_runs",
        ["tenant_id", "user_id", "status"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_tenant_user_status", table_name="agent_runs")
    op.drop_index("ix_agent_runs_tenant_status_created", table_name="agent_runs")
    op.drop_index("ix_agent_runs_request_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_tenant_user_id", table_name="agent_runs")
    op.drop_table("agent_runs")

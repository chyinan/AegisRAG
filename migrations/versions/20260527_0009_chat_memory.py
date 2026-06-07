"""Add chat memory tables.

Revision ID: 20260527_0009
Revises: 20260527_0008
Create Date: 2026-06-07 18:32:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260527_0009"
down_revision: str | None = "20260527_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "chat_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=True),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.CheckConstraint(
            "status in ('active', 'closed', 'deleted')",
            name="ck_chat_sessions_status",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "chat_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("trace_id", sa.String(length=128), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("created_by", sa.String(length=36), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_summary", sa.String(length=512), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.CheckConstraint("status in ('active', 'deleted')", name="ck_chat_messages_status"),
        sa.CheckConstraint(
            "role in ('user', 'assistant', 'system_summary')",
            name="ck_chat_messages_role",
        ),
        sa.CheckConstraint("token_count >= 0", name="ck_chat_messages_token_count_nonnegative"),
        sa.CheckConstraint("sequence_no > 0", name="ck_chat_messages_sequence_no_positive"),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "session_id",
            "sequence_no",
            name="uq_chat_messages_sequence",
        ),
    )
    op.create_index(
        "ix_chat_sessions_tenant_user_id",
        "chat_sessions",
        ["tenant_id", "user_id", "id"],
    )
    op.create_index(
        "ix_chat_sessions_tenant_user_status",
        "chat_sessions",
        ["tenant_id", "user_id", "status"],
    )
    op.create_index("ix_chat_sessions_request_id", "chat_sessions", ["request_id"])
    op.create_index("ix_chat_sessions_trace_id", "chat_sessions", ["trace_id"])
    op.create_index(
        "ix_chat_messages_tenant_session_sequence",
        "chat_messages",
        ["tenant_id", "session_id", "sequence_no"],
    )
    op.create_index(
        "ix_chat_messages_tenant_session_created",
        "chat_messages",
        ["tenant_id", "session_id", "created_at"],
    )
    op.create_index(
        "ix_chat_messages_tenant_user_session",
        "chat_messages",
        ["tenant_id", "user_id", "session_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_messages_tenant_user_session", table_name="chat_messages")
    op.drop_index("ix_chat_messages_tenant_session_created", table_name="chat_messages")
    op.drop_index("ix_chat_messages_tenant_session_sequence", table_name="chat_messages")
    op.drop_index("ix_chat_sessions_trace_id", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_request_id", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_tenant_user_status", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_tenant_user_id", table_name="chat_sessions")
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")

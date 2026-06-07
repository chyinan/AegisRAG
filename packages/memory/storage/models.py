from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from packages.data.storage.base import Base, IdMixin, TimestampMixin


class ChatSessionModel(IdMixin, TimestampMixin, Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        CheckConstraint(
            "status in ('active', 'closed', 'deleted')",
            name="ck_chat_sessions_status",
        ),
        Index("ix_chat_sessions_tenant_user_id", "tenant_id", "user_id", "id"),
        Index("ix_chat_sessions_tenant_user_status", "tenant_id", "user_id", "status"),
        Index("ix_chat_sessions_request_id", "request_id"),
        Index("ix_chat_sessions_trace_id", "trace_id"),
    )

    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSON,
        nullable=False,
        default=dict,
    )


class ChatMessageModel(IdMixin, TimestampMixin, Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint(
            "status in ('active', 'deleted')",
            name="ck_chat_messages_status",
        ),
        CheckConstraint(
            "role in ('user', 'assistant', 'system_summary')",
            name="ck_chat_messages_role",
        ),
        CheckConstraint("token_count >= 0", name="ck_chat_messages_token_count_nonnegative"),
        CheckConstraint("sequence_no > 0", name="ck_chat_messages_sequence_no_positive"),
        UniqueConstraint(
            "tenant_id",
            "session_id",
            "sequence_no",
            name="uq_chat_messages_sequence",
        ),
        Index("ix_chat_messages_tenant_session_sequence", "tenant_id", "session_id", "sequence_no"),
        Index("ix_chat_messages_tenant_session_created", "tenant_id", "session_id", "created_at"),
        Index("ix_chat_messages_tenant_user_session", "tenant_id", "user_id", "session_id"),
    )

    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_summary: Mapped[str] = mapped_column(String(512), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sequence_no: Mapped[int] = mapped_column(Integer, nullable=False)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSON,
        nullable=False,
        default=dict,
    )

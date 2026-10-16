from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from packages.data.storage.base import Base, IdMixin, TimestampMixin


class SupportTicketModel(Base, IdMixin, TimestampMixin):
    __tablename__ = "support_tickets"
    __table_args__ = (
        Index(
            "uq_support_ticket_owner_reference",
            "tenant_id",
            "user_id",
            "client_reference",
            unique=True,
        ),
        Index("ix_support_ticket_owner_created", "tenant_id", "user_id", "created_at"),
        CheckConstraint("status IN ('open', 'in_progress', 'resolved')", name="ck_ticket_status"),
        CheckConstraint("priority IN ('low', 'normal', 'high')", name="ck_ticket_priority"),
        CheckConstraint("revision >= 1", name="ck_ticket_revision"),
    )

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    client_reference: Mapped[str] = mapped_column(String(36), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    tool_version: Mapped[str] = mapped_column(String(64), nullable=False)

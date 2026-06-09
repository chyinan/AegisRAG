from __future__ import annotations

from sqlalchemy import JSON, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from packages.data.storage.base import Base, IdMixin, TimestampMixin


class AuditLogModel(IdMixin, TimestampMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_tenant_id", "tenant_id"),
        Index("ix_audit_logs_user_id", "user_id"),
        Index("ix_audit_logs_request_id", "request_id"),
        Index("ix_audit_logs_trace_id", "trace_id"),
        Index("ix_audit_logs_created_at", "created_at"),
        Index("ix_audit_logs_status", "status"),
        Index("ix_audit_logs_tenant_created", "tenant_id", "created_at"),
        Index("ix_audit_logs_tenant_action_created", "tenant_id", "action", "created_at"),
        Index(
            "ix_audit_logs_tenant_resource_created",
            "tenant_id",
            "resource_type",
            "created_at",
        ),
    )

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_metadata: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSON,
        nullable=False,
        default=dict,
    )

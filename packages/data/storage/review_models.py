from __future__ import annotations

from sqlalchemy import JSON, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from packages.data.storage.base import Base, IdMixin, TimestampMixin


class ReviewItemModel(IdMixin, TimestampMixin, Base):
    __tablename__ = "review_items"
    __table_args__ = (
        Index("ix_review_items_tenant_status_created", "tenant_id", "status", "created_at"),
        Index("ix_review_items_tenant_type_created", "tenant_id", "item_type", "created_at"),
        Index("ix_review_items_tenant_severity_created", "tenant_id", "severity", "created_at"),
        Index("ix_review_items_tenant_request_trace", "tenant_id", "request_id", "trace_id"),
        Index("ix_review_items_tenant_source_created", "tenant_id", "source_view", "created_at"),
    )

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    item_type: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    source_view: Mapped[str] = mapped_column(String(64), nullable=False)
    safe_identifiers: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    safe_summary: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    eval_candidate: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    status_history: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )

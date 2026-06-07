from __future__ import annotations

from sqlalchemy import JSON, CheckConstraint, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from packages.data.storage.base import Base, IdMixin, TimestampMixin


class RetrievalLogModel(IdMixin, TimestampMixin, Base):
    __tablename__ = "retrieval_logs"
    __table_args__ = (
        CheckConstraint(
            "status in ('success', 'failure')",
            name="ck_retrieval_logs_status",
        ),
        Index("ix_retrieval_logs_request_id", "request_id"),
        Index("ix_retrieval_logs_trace_id", "trace_id"),
        Index("ix_retrieval_logs_tenant_id", "tenant_id"),
        Index("ix_retrieval_logs_created_at", "created_at"),
        Index("ix_retrieval_logs_tenant_request", "tenant_id", "request_id"),
        Index("ix_retrieval_logs_tenant_created", "tenant_id", "created_at"),
    )

    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    top_k: Mapped[int] = mapped_column(Integer, nullable=False)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False)
    rerank_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    query_summary: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSON,
        nullable=False,
        default=dict,
    )

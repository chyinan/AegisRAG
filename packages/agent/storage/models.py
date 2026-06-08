from __future__ import annotations

from sqlalchemy import JSON, CheckConstraint, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from packages.data.storage.base import Base, IdMixin, TimestampMixin


class AgentRunModel(IdMixin, TimestampMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status in ('running', 'completed', 'stopped', 'failed')",
            name="ck_agent_runs_status",
        ),
        CheckConstraint("max_steps > 0", name="ck_agent_runs_max_steps_positive"),
        CheckConstraint("max_tool_calls >= 0", name="ck_agent_runs_max_tool_calls_nonnegative"),
        CheckConstraint("timeout_seconds > 0", name="ck_agent_runs_timeout_positive"),
        CheckConstraint("steps_used >= 0", name="ck_agent_runs_steps_used_nonnegative"),
        CheckConstraint("tool_calls_used >= 0", name="ck_agent_runs_tool_calls_used_nonnegative"),
        CheckConstraint("latency_ms is null or latency_ms >= 0", name="ck_agent_runs_latency_ms"),
        Index("ix_agent_runs_tenant_user_id", "tenant_id", "user_id", "id"),
        Index("ix_agent_runs_request_id", "request_id"),
        Index("ix_agent_runs_tenant_status_created", "tenant_id", "status", "created_at"),
        Index("ix_agent_runs_tenant_user_status", "tenant_id", "user_id", "status"),
    )

    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False)
    max_tool_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    timeout_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    steps_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_calls_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    termination_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    input_summary: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata",
        JSON,
        nullable=False,
        default=dict,
    )

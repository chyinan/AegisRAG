from __future__ import annotations

from sqlalchemy import JSON, CheckConstraint, Float, ForeignKey, Index, Integer, String
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


class ToolCallModel(IdMixin, TimestampMixin, Base):
    __tablename__ = "tool_calls"
    __table_args__ = (
        CheckConstraint(
            "status in ('success', 'denied', 'failure')",
            name="ck_tool_calls_status",
        ),
        CheckConstraint("latency_ms >= 0", name="ck_tool_calls_latency_ms_nonnegative"),
        Index("ix_tool_calls_agent_run_id", "agent_run_id"),
        Index("ix_tool_calls_tool_name", "tool_name"),
        Index("ix_tool_calls_status_created", "status", "created_at"),
        Index("ix_tool_calls_agent_run_tool_status", "agent_run_id", "tool_name", "status"),
        Index("ix_tool_calls_tenant_user_created", "tenant_id", "user_id", "created_at"),
    )

    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    agent_run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("agent_runs.id"),
        nullable=False,
    )
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    permission: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    arguments_summary: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )
    result_summary: Mapped[dict[str, object]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
    )

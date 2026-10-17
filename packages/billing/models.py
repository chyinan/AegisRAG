from __future__ import annotations

from sqlalchemy import JSON, BigInteger, Boolean, CheckConstraint, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from packages.data.storage.base import Base, IdMixin, TimestampMixin


class CostAccountModel(Base, IdMixin, TimestampMixin):
    __tablename__ = "cost_accounts"
    __table_args__ = (
        Index("uq_cost_account_period", "tenant_id", "currency", "period", unique=True),
        CheckConstraint("spent_nanos >= 0 AND reserved_nanos >= 0", name="ck_cost_account_amount"),
    )

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    spent_nanos: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    reserved_nanos: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class UsageChargeModel(Base, IdMixin, TimestampMixin):
    __tablename__ = "usage_charges"
    __table_args__ = (
        Index("ix_usage_charge_tenant_created", "tenant_id", "created_at"),
        CheckConstraint("reserved_nanos >= 0", name="ck_usage_charge_reserved"),
        CheckConstraint("amount_nanos IS NULL OR amount_nanos >= 0", name="ck_usage_charge_amount"),
    )

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    user_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    trace_id: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_version: Mapped[str | None] = mapped_column(String(128))
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    price_version: Mapped[str | None] = mapped_column(String(64))
    price_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSON)
    usage_known: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    input_tokens: Mapped[int | None] = mapped_column(BigInteger)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger)
    total_tokens: Mapped[int | None] = mapped_column(BigInteger)
    amount_nanos: Mapped[int | None] = mapped_column(BigInteger)
    reserved_nanos: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    latency_ms: Mapped[float | None] = mapped_column(Float)

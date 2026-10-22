"""Tenant-scoped durable plans with short, atomic cross-process batch leases."""

from __future__ import annotations

import time
from uuid import uuid4

from sqlalchemy import JSON, Boolean, Float, Index, Integer, String, UniqueConstraint, or_, select
from sqlalchemy import update as sql_update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

from packages.common.errors import DomainError
from packages.data.storage.base import Base, IdMixin, TimestampMixin
from packages.ingestion.folder_sync.dto import FolderSyncPlan


def sync_error(code: str, message: str, status_code: int = 400) -> DomainError:
    return DomainError(code=code, message=message, status_code=status_code)


class FolderSyncPlanModel(IdMixin, TimestampMixin, Base):
    __tablename__ = "folder_sync_plans"
    __table_args__ = (
        UniqueConstraint("tenant_id", "connector_id", name="uq_folder_sync_tenant_connector"),
        Index("ix_folder_sync_plans_due", "tenant_id", "enabled", "next_run_at"),
    )

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False)
    connector_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    next_run_at: Mapped[float] = mapped_column(Float, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[float | None] = mapped_column(Float, nullable=True)


class SqlAlchemyFolderSyncStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def create(self, plan: FolderSyncPlan) -> None:
        async with self._sessions() as session:
            session.add(
                FolderSyncPlanModel(
                    id=plan.id,
                    tenant_id=plan.tenant_id,
                    created_by=plan.created_by,
                    connector_id=plan.connector_id,
                    status=plan.status,
                    enabled=plan.enabled,
                    next_run_at=plan.next_run_at,
                    revision=0,
                    snapshot=plan.model_dump(mode="json"),
                )
            )
            try:
                await session.commit()
            except IntegrityError as exc:
                raise sync_error(
                    "FOLDER_SYNC_PLAN_EXISTS", "Folder sync plan already exists.", 409
                ) from exc
            except SQLAlchemyError as exc:
                raise sync_error(
                    "FOLDER_SYNC_STORAGE_FAILED", "Folder sync storage failed.", 503
                ) from exc

    async def get(self, *, tenant_id: str, plan_id: str) -> FolderSyncPlan | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(FolderSyncPlanModel).where(
                    FolderSyncPlanModel.tenant_id == tenant_id,
                    FolderSyncPlanModel.id == plan_id,
                )
            )
            return FolderSyncPlan.model_validate(row.snapshot) if row is not None else None

    async def list(self, *, tenant_id: str) -> list[FolderSyncPlan]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(FolderSyncPlanModel)
                .where(FolderSyncPlanModel.tenant_id == tenant_id)
                .order_by(FolderSyncPlanModel.id)
            )
            return [FolderSyncPlan.model_validate(row.snapshot) for row in rows]

    async def claim(
        self, *, tenant_id: str, plan_id: str, lease_seconds: float, due_only: bool = False
    ) -> tuple[FolderSyncPlan, str] | None:
        now = time.time()
        token = str(uuid4())
        statement = sql_update(FolderSyncPlanModel).where(
            FolderSyncPlanModel.tenant_id == tenant_id,
            FolderSyncPlanModel.id == plan_id,
            or_(
                FolderSyncPlanModel.lease_token.is_(None),
                FolderSyncPlanModel.lease_until < now,
            ),
        )
        if due_only:
            statement = statement.where(
                FolderSyncPlanModel.enabled.is_(True), FolderSyncPlanModel.next_run_at <= now
            )
        async with self._sessions() as session:
            result = await session.execute(
                statement.values(lease_token=token, lease_until=now + lease_seconds)
            )
            await session.commit()
            if not result.rowcount:  # type: ignore[attr-defined]
                return None
            row = await session.scalar(
                select(FolderSyncPlanModel).where(
                    FolderSyncPlanModel.tenant_id == tenant_id,
                    FolderSyncPlanModel.id == plan_id,
                    FolderSyncPlanModel.lease_token == token,
                )
            )
            if row is None:
                return None
            return FolderSyncPlan.model_validate(row.snapshot), token

    async def save(self, plan: FolderSyncPlan, token: str, *, release: bool = False) -> None:
        next_revision = plan.revision + 1
        values: dict[str, object] = {
            "snapshot": {**plan.model_dump(mode="json"), "revision": next_revision},
            "status": plan.status,
            "enabled": plan.enabled,
            "next_run_at": plan.next_run_at,
            "revision": FolderSyncPlanModel.revision + 1,
        }
        if release:
            values.update(lease_token=None, lease_until=None)
        async with self._sessions() as session:
            result = await session.execute(
                sql_update(FolderSyncPlanModel)
                .where(
                    FolderSyncPlanModel.tenant_id == plan.tenant_id,
                    FolderSyncPlanModel.id == plan.id,
                    FolderSyncPlanModel.lease_token == token,
                    FolderSyncPlanModel.lease_until > time.time(),
                )
                .values(**values)
            )
            await session.commit()
            if not result.rowcount:  # type: ignore[attr-defined]
                raise sync_error("FOLDER_SYNC_LEASE_LOST", "Folder sync batch lease expired.", 409)
            plan.revision = next_revision

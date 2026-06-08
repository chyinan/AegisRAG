from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.audit import AuditEvent, AuditPort
from packages.common.logging import redact_mapping
from packages.data.storage.audit_models import AuditLogModel
from packages.data.storage.base import generate_uuid
from packages.data.storage.exceptions import StorageError


class AuditLogRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    user_id: str
    created_by: str | None
    status: str
    request_id: str
    trace_id: str
    action: str
    resource_type: str
    resource_id: str
    resource_metadata: dict[str, object] = Field(default_factory=dict)
    latency_ms: float
    error_code: str | None
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class AuditLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, event: AuditEvent) -> AuditLogRecord:
        model = build_audit_log_model(event)
        self._session.add(model)
        try:
            await self._session.flush()
            await self._session.refresh(model)
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="AUDIT_STORAGE_WRITE_FAILED",
                message="Audit storage write failed.",
                details={"action": event.action, "resource_type": event.resource.type},
            ) from exc
        return audit_log_record_from_model(model)

    async def list_by_request_id(
        self,
        *,
        tenant_id: str,
        request_id: str,
        limit: int = 100,
    ) -> list[AuditLogRecord]:
        bounded_limit = min(max(limit, 1), 500)
        statement = (
            select(AuditLogModel)
            .where(
                AuditLogModel.tenant_id == tenant_id,
                AuditLogModel.request_id == request_id,
            )
            .order_by(AuditLogModel.created_at)
            .limit(bounded_limit)
        )
        try:
            models = list(await self._session.scalars(statement))
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="AUDIT_STORAGE_READ_FAILED",
                message="Audit storage read failed.",
                details={"tenant_id": tenant_id, "request_id": request_id},
            ) from exc
        return [audit_log_record_from_model(model) for model in models]

    async def list_by_trace_id(
        self,
        *,
        tenant_id: str,
        trace_id: str,
        limit: int = 100,
    ) -> list[AuditLogRecord]:
        bounded_limit = min(max(limit, 1), 500)
        statement = (
            select(AuditLogModel)
            .where(
                AuditLogModel.tenant_id == tenant_id,
                AuditLogModel.trace_id == trace_id,
            )
            .order_by(AuditLogModel.created_at)
            .limit(bounded_limit)
        )
        try:
            models = list(await self._session.scalars(statement))
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="AUDIT_STORAGE_READ_FAILED",
                message="Audit storage read failed.",
                details={"tenant_id": tenant_id, "trace_id": trace_id},
            ) from exc
        return [audit_log_record_from_model(model) for model in models]


class SqlAlchemyAuditPort(AuditPort):
    def __init__(self, session: AsyncSession, *, auto_commit: bool = False) -> None:
        self._session = session
        self._repository = AuditLogRepository(session)
        self._auto_commit = auto_commit

    async def record(self, event: AuditEvent) -> None:
        await self._repository.create(event)
        if self._auto_commit:
            try:
                await self._session.commit()
            except SQLAlchemyError as exc:
                await self._session.rollback()
                raise StorageError(
                    code="AUDIT_STORAGE_COMMIT_FAILED",
                    message="Audit storage commit failed.",
                    details={"action": event.action, "resource_type": event.resource.type},
                ) from exc


def build_audit_log_model(
    event: AuditEvent,
    *,
    audit_log_id: str | None = None,
) -> AuditLogModel:
    return AuditLogModel(
        id=audit_log_id or generate_uuid(),
        created_at=event.created_at,
        updated_at=event.created_at,
        tenant_id=event.tenant_id,
        user_id=event.user_id,
        created_by=event.user_id,
        status=event.status.value,
        request_id=event.request_id,
        trace_id=event.trace_id,
        action=event.action,
        resource_type=event.resource.type,
        resource_id=event.resource.id,
        resource_metadata=redact_mapping(event.resource.metadata),
        latency_ms=event.latency_ms,
        error_code=event.error_code,
        metadata_=redact_mapping(event.metadata),
    )


def audit_log_record_from_model(model: AuditLogModel) -> AuditLogRecord:
    return AuditLogRecord(
        id=model.id,
        tenant_id=model.tenant_id,
        user_id=model.user_id,
        created_by=model.created_by,
        status=model.status,
        request_id=model.request_id,
        trace_id=model.trace_id,
        action=model.action,
        resource_type=model.resource_type,
        resource_id=model.resource_id,
        resource_metadata=dict(model.resource_metadata or {}),
        latency_ms=model.latency_ms,
        error_code=model.error_code,
        metadata=dict(model.metadata_ or {}),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )

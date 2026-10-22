"""Session-isolated auxiliary audit writes for cancellable Agent telemetry."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.audit import AuditEvent
from packages.common.tenant_context import current_tenant_id, tenant_scope
from packages.data.storage.audit_repositories import SqlAlchemyAuditPort
from packages.data.storage.exceptions import StorageError


class SessionFactoryAuditPort:
    """Own each audit transaction independently of mandatory application storage.

    The calling task owns this context through cancellation cleanup. No request
    session is captured or reused, so a slow/failed audit cannot commit, roll
    back, invalidate or concurrently use the run/tool-call persistence session.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record(self, event: AuditEvent) -> None:
        tenant_id = current_tenant_id()
        if tenant_id is not None and tenant_id != event.tenant_id:
            raise StorageError(
                code="AUDIT_TENANT_CONTEXT_MISMATCH",
                message="Audit tenant context does not match the authenticated request.",
                details={},
            )
        # AuditEvent is constructed by the authenticated application, never a
        # model argument. Preserve/check an inherited tenant; set it explicitly
        # for direct trusted callers before PostgreSQL's transaction/RLS hook.
        with tenant_scope(event.tenant_id):
            async with self._session_factory() as session:
                await SqlAlchemyAuditPort(session, auto_commit=True).record(event)

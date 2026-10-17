"""Application boundary: current permissions, scoped persistence and atomic audits."""

from __future__ import annotations

from time import perf_counter
from uuid import uuid4

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.audit import AuditEvent, AuditResource, AuditStatus
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError
from packages.common.tenant_context import tenant_scope
from packages.data.storage.audit_repositories import build_audit_log_model
from packages.support_ticket.dto import (
    SUPPORT_TICKET_CREATE_PERMISSION,
    SUPPORT_TICKET_MANAGE_PERMISSION,
    SUPPORT_TICKET_READ_PERMISSION,
    SUPPORT_TICKET_TOOL_VERSION,
    CreateSupportTicketInput,
    SupportTicketList,
    SupportTicketRecord,
    SupportTicketStatus,
    SupportTicketStatusUpdate,
)
from packages.support_ticket.models import SupportTicketModel
from packages.support_ticket.repository import SupportTicketRepository, ticket_record


def ticket_error(code: str, *, status_code: int = 409) -> DomainError:
    return DomainError(
        code=code,
        message="Support ticket operation could not proceed.",
        details={"error_code": code},
        status_code=status_code,
    )


def require_permission(context: AuthenticatedRequestContext, permission: str) -> None:
    if permission not in context.auth.permissions:
        raise ticket_error("SUPPORT_TICKET_FORBIDDEN", status_code=403)


class SupportTicketService:
    """Each awaited operation owns its SQL session until it actually exits.

    The tool never captures a request session. Cancellation cleanup therefore
    cannot use a session that the HTTP request has already returned to its pool.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: CreateSupportTicketInput,
    ) -> SupportTicketRecord:
        require_permission(context, SUPPORT_TICKET_CREATE_PERMISSION)
        started = perf_counter()
        with tenant_scope(context.auth.tenant_id):
            async with self._session_factory() as session:
                repository = SupportTicketRepository(session)
                try:
                    existing = await repository.by_reference(
                        tenant_id=context.auth.tenant_id,
                        user_id=context.auth.user_id,
                        client_reference=str(command.client_reference),
                    )
                    if existing is not None:
                        return self._existing(existing, command)
                    model = SupportTicketModel(
                        id=str(uuid4()),
                        tenant_id=context.auth.tenant_id,
                        user_id=context.auth.user_id,
                        created_by=context.auth.user_id,
                        client_reference=str(command.client_reference),
                        request_fingerprint=command.fingerprint(),
                        title=command.title,
                        description=command.description,
                        priority=command.priority,
                        status="open",
                        revision=1,
                        tool_version=SUPPORT_TICKET_TOOL_VERSION,
                    )
                    session.add(model)
                    await session.flush()
                    self._audit(session, context, model, "support_ticket.created", started)
                    await session.commit()
                    await session.refresh(model)
                    return ticket_record(model)
                except IntegrityError as exc:
                    # The unique owner/reference index is the persistence fence.
                    # A concurrent identical create returns the winner's ticket.
                    await session.rollback()
                    existing = await repository.by_reference(
                        tenant_id=context.auth.tenant_id,
                        user_id=context.auth.user_id,
                        client_reference=str(command.client_reference),
                    )
                    if existing is not None:
                        return self._existing(existing, command)
                    raise ticket_error("SUPPORT_TICKET_STORAGE_FAILED", status_code=503) from exc
                except SQLAlchemyError as exc:
                    await session.rollback()
                    raise ticket_error("SUPPORT_TICKET_STORAGE_FAILED", status_code=503) from exc

    async def get_owned(
        self,
        *,
        context: AuthenticatedRequestContext,
        ticket_id: str,
    ) -> SupportTicketRecord:
        require_permission(context, SUPPORT_TICKET_READ_PERMISSION)
        with tenant_scope(context.auth.tenant_id):
            async with self._session_factory() as session:
                try:
                    model = await SupportTicketRepository(session).get(
                        tenant_id=context.auth.tenant_id,
                        user_id=context.auth.user_id,
                        ticket_id=ticket_id,
                    )
                    if model is None:
                        raise ticket_error("SUPPORT_TICKET_NOT_FOUND", status_code=404)
                    return ticket_record(model)
                except SQLAlchemyError as exc:
                    raise ticket_error("SUPPORT_TICKET_STORAGE_FAILED", status_code=503) from exc

    async def list_owned(
        self,
        *,
        context: AuthenticatedRequestContext,
        limit: int = 50,
    ) -> SupportTicketList:
        require_permission(context, SUPPORT_TICKET_READ_PERMISSION)
        if not 1 <= limit <= 100:
            raise ticket_error("SUPPORT_TICKET_INVALID_LIMIT", status_code=422)
        with tenant_scope(context.auth.tenant_id):
            async with self._session_factory() as session:
                try:
                    return SupportTicketList(
                        items=await SupportTicketRepository(session).list_owned(
                            tenant_id=context.auth.tenant_id,
                            user_id=context.auth.user_id,
                            limit=limit,
                        )
                    )
                except SQLAlchemyError as exc:
                    raise ticket_error("SUPPORT_TICKET_STORAGE_FAILED", status_code=503) from exc

    async def list_managed(
        self,
        *,
        context: AuthenticatedRequestContext,
        limit: int = 50,
        status: SupportTicketStatus | None = None,
        ticket_id: str | None = None,
    ) -> SupportTicketList:
        require_permission(context, SUPPORT_TICKET_MANAGE_PERMISSION)
        if not 1 <= limit <= 100:
            raise ticket_error("SUPPORT_TICKET_INVALID_LIMIT", status_code=422)
        started = perf_counter()
        with tenant_scope(context.auth.tenant_id):
            async with self._session_factory() as session:
                try:
                    items = await SupportTicketRepository(session).list_managed(
                        tenant_id=context.auth.tenant_id,
                        limit=limit,
                        status=status,
                        ticket_id=ticket_id,
                    )
                    session.add(
                        build_audit_log_model(
                            AuditEvent(
                                request_id=context.request_id,
                                trace_id=context.trace_id,
                                tenant_id=context.auth.tenant_id,
                                user_id=context.auth.user_id,
                                action="support_ticket.management_list",
                                resource=AuditResource(type="support_tickets", id="management"),
                                status=AuditStatus.SUCCESS,
                                latency_ms=(perf_counter() - started) * 1000,
                                metadata={"item_count": len(items)},
                            )
                        )
                    )
                    await session.commit()
                    return SupportTicketList(items=items)
                except SQLAlchemyError as exc:
                    await session.rollback()
                    raise ticket_error("SUPPORT_TICKET_STORAGE_FAILED", status_code=503) from exc

    async def update_status(
        self,
        *,
        context: AuthenticatedRequestContext,
        ticket_id: str,
        command: SupportTicketStatusUpdate,
    ) -> SupportTicketRecord:
        require_permission(context, SUPPORT_TICKET_MANAGE_PERMISSION)
        started = perf_counter()
        with tenant_scope(context.auth.tenant_id):
            async with self._session_factory() as session:
                repository = SupportTicketRepository(session)
                try:
                    model = await repository.get(
                        tenant_id=context.auth.tenant_id,
                        user_id=None,
                        ticket_id=ticket_id,
                    )
                    if model is None:
                        raise ticket_error("SUPPORT_TICKET_NOT_FOUND", status_code=404)
                    next_status = {"open": "in_progress", "in_progress": "resolved"}.get(
                        model.status,
                    )
                    if model.revision != command.expected_revision or next_status != command.status:
                        raise ticket_error("SUPPORT_TICKET_TRANSITION_CONFLICT")
                    changed = await repository.transition(
                        tenant_id=context.auth.tenant_id,
                        ticket_id=ticket_id,
                        expected_revision=command.expected_revision,
                        old_status=model.status,
                        new_status=command.status,
                    )
                    if not changed:
                        raise ticket_error("SUPPORT_TICKET_TRANSITION_CONFLICT")
                    await session.refresh(model)
                    self._audit(session, context, model, "support_ticket.status_changed", started)
                    await session.commit()
                    return ticket_record(model)
                except SQLAlchemyError as exc:
                    await session.rollback()
                    raise ticket_error("SUPPORT_TICKET_STORAGE_FAILED", status_code=503) from exc

    @staticmethod
    def _existing(
        model: SupportTicketModel,
        command: CreateSupportTicketInput,
    ) -> SupportTicketRecord:
        if model.request_fingerprint != command.fingerprint():
            raise ticket_error("SUPPORT_TICKET_REFERENCE_CONFLICT")
        return ticket_record(model)

    @staticmethod
    def _audit(
        session: AsyncSession,
        context: AuthenticatedRequestContext,
        model: SupportTicketModel,
        action: str,
        started: float,
    ) -> None:
        session.add(
            build_audit_log_model(
                AuditEvent(
                    request_id=context.request_id,
                    trace_id=context.trace_id,
                    tenant_id=context.auth.tenant_id,
                    user_id=context.auth.user_id,
                    action=action,
                    resource=AuditResource(type="support_ticket", id=model.id),
                    status=AuditStatus.SUCCESS,
                    latency_ms=(perf_counter() - started) * 1000,
                    metadata={"ticket_status": model.status, "revision": model.revision},
                )
            )
        )

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.config import AppSettings, load_settings
from packages.support_ticket.service import SupportTicketService, ticket_error


def create_support_ticket_service(
    settings: AppSettings,
    session_factory: async_sessionmaker[AsyncSession],
) -> SupportTicketService:
    if not settings.support_ticket_enabled:
        raise ticket_error("SUPPORT_TICKET_DISABLED", status_code=503)
    return SupportTicketService(session_factory)


def get_support_ticket_service() -> SupportTicketService:
    from apps.api.factories.common import create_session_factory

    settings = load_settings()
    # Check the flag before attempting to construct a database pool.
    if not settings.support_ticket_enabled:
        raise ticket_error("SUPPORT_TICKET_DISABLED", status_code=503)
    return create_support_ticket_service(settings, create_session_factory(settings.database_url))


SupportTicketServiceDep = Annotated[SupportTicketService, Depends(get_support_ticket_service)]

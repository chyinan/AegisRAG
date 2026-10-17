"""Keep each cancellable retrieval operation's SQL session owned by its task.

A deadline bounds the caller's wait, not cancellation-resistant provider
cleanup. Capture a session factory so the task retains its own session until
its full retrieval pipeline really exits.
"""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.common.context import AuthenticatedRequestContext
from packages.common.tenant_context import tenant_scope
from packages.retrieval.application import (
    RetrieveApplicationService,
    RetrieveCommand,
    RetrieveResponse,
)


class SessionFactoryRetrievalApplication:
    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        build_service: Callable[[AsyncSession], RetrieveApplicationService],
    ) -> None:
        self._session_factory = session_factory
        self._build_service = build_service

    async def retrieve(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: RetrieveCommand,
    ) -> RetrieveResponse:
        with tenant_scope(context.auth.tenant_id):
            async with self._session_factory() as session:
                return await self._build_service(session).retrieve(context=context, command=command)

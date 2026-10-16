"""Own SQL resources in the task performing Agent persistence or evidence I/O.

Capture a session factory, never a request session. Each operation retains its
session until it really exits, including cancellation cleanup.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from packages.agent.adapters.document_evidence import DocumentEvidenceRevalidator
from packages.agent.dto import ToolCallCreate, ToolCallRecord
from packages.agent.durable.evidence import RehydratedEvidence
from packages.agent.runtime import AgentObservationSummary
from packages.agent.storage.repositories import ToolCallRepository
from packages.common.context import AuthenticatedRequestContext
from packages.common.tenant_context import tenant_scope
from packages.data.storage.repositories import DocumentRepository


class SessionFactoryToolCallRecorder:
    """Required tool-call persistence, with an independently owned transaction."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def record_tool_call(self, record: ToolCallCreate) -> None:
        with tenant_scope(record.tenant_id):
            async with self._session_factory() as session:
                await ToolCallRepository(session).record_tool_call(record)

    async def list_by_agent_run(
        self,
        *,
        tenant_id: str,
        user_id: str,
        agent_run_id: str,
    ) -> list[ToolCallRecord]:
        with tenant_scope(tenant_id):
            async with self._session_factory() as session:
                return await ToolCallRepository(session).list_by_agent_run(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    agent_run_id=agent_run_id,
                )


class SessionFactoryEvidenceRevalidator:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def rehydrate(
        self,
        *,
        context: AuthenticatedRequestContext,
        observations: Sequence[AgentObservationSummary],
        fingerprints: dict[str, str],
    ) -> RehydratedEvidence:
        with tenant_scope(context.auth.tenant_id):
            async with self._session_factory() as session:
                return await DocumentEvidenceRevalidator(
                    repository=DocumentRepository(session),
                ).rehydrate(
                    context=context,
                    observations=observations,
                    fingerprints=fingerprints,
                )

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from packages.support_ticket.dto import SupportTicketRecord, SupportTicketStatus
from packages.support_ticket.models import SupportTicketModel


class SupportTicketRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def by_reference(
        self,
        *,
        tenant_id: str,
        user_id: str,
        client_reference: str,
    ) -> SupportTicketModel | None:
        return cast(
            SupportTicketModel | None,
            await self._session.scalar(
                select(SupportTicketModel).where(
                    SupportTicketModel.tenant_id == tenant_id,
                    SupportTicketModel.user_id == user_id,
                    SupportTicketModel.client_reference == client_reference,
                )
            ),
        )

    async def get(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
        user_id: str | None,
    ) -> SupportTicketModel | None:
        query = select(SupportTicketModel).where(
            SupportTicketModel.tenant_id == tenant_id,
            SupportTicketModel.id == ticket_id,
        )
        if user_id is not None:
            query = query.where(SupportTicketModel.user_id == user_id)
        return cast(SupportTicketModel | None, await self._session.scalar(query))

    async def list_owned(
        self,
        *,
        tenant_id: str,
        user_id: str,
        limit: int,
    ) -> tuple[SupportTicketRecord, ...]:
        models = await self._session.scalars(
            select(SupportTicketModel)
            .where(
                SupportTicketModel.tenant_id == tenant_id,
                SupportTicketModel.user_id == user_id,
            )
            .order_by(SupportTicketModel.created_at.desc(), SupportTicketModel.id)
            .limit(limit)
        )
        return tuple(ticket_record(model) for model in models)

    async def list_managed(
        self,
        *,
        tenant_id: str,
        limit: int,
        status: SupportTicketStatus | None = None,
        ticket_id: str | None = None,
    ) -> tuple[SupportTicketRecord, ...]:
        query = select(SupportTicketModel).where(SupportTicketModel.tenant_id == tenant_id)
        if status is not None:
            query = query.where(SupportTicketModel.status == status)
        if ticket_id is not None:
            query = query.where(SupportTicketModel.id == ticket_id)
        models = await self._session.scalars(
            query.order_by(SupportTicketModel.created_at.desc(), SupportTicketModel.id).limit(limit)
        )
        return tuple(ticket_record(model) for model in models)

    async def transition(
        self,
        *,
        tenant_id: str,
        ticket_id: str,
        expected_revision: int,
        old_status: str,
        new_status: str,
    ) -> bool:
        result = await self._session.execute(
            update(SupportTicketModel)
            .where(
                SupportTicketModel.tenant_id == tenant_id,
                SupportTicketModel.id == ticket_id,
                SupportTicketModel.revision == expected_revision,
                SupportTicketModel.status == old_status,
            )
            .values(
                status=new_status,
                revision=expected_revision + 1,
                updated_at=datetime.now(UTC),
            ),
            execution_options={"synchronize_session": False},
        )
        return result.rowcount == 1  # type: ignore[attr-defined,no-any-return]


def ticket_record(model: SupportTicketModel) -> SupportTicketRecord:
    return SupportTicketRecord.model_validate(
        dict(
            ticket_id=model.id,
            tenant_id=model.tenant_id,
            user_id=model.user_id,
            created_by=model.created_by,
            client_reference=model.client_reference,
            title=model.title,
            description=model.description,
            priority=model.priority,
            status=model.status,
            revision=model.revision,
            tool_version=model.tool_version,
            created_at=model.created_at,
            updated_at=model.updated_at,
        )
    )

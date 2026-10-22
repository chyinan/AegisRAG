from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from apps.api.dependencies import AuthenticatedRequestContextDep
from apps.api.factories.support_tickets import SupportTicketServiceDep
from packages.common.envelope import ApiResponse, success_response
from packages.support_ticket.dto import (
    SupportTicketList,
    SupportTicketRecord,
    SupportTicketStatus,
    SupportTicketStatusUpdate,
)

router = APIRouter(tags=["support-tickets"])


@router.get("/support-tickets", response_model=ApiResponse[SupportTicketList])
async def list_support_tickets(
    context: AuthenticatedRequestContextDep,
    service: SupportTicketServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> ApiResponse[SupportTicketList]:
    result = await service.list_owned(context=context, limit=limit)
    return success_response(request_id=context.request_id, data=result)


@router.get("/support-tickets/manage", response_model=ApiResponse[SupportTicketList])
async def list_managed_support_tickets(
    context: AuthenticatedRequestContextDep,
    service: SupportTicketServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    status: SupportTicketStatus | None = None,
    ticket_id: UUID | None = None,
) -> ApiResponse[SupportTicketList]:
    result = await service.list_managed(
        context=context,
        limit=limit,
        status=status,
        ticket_id=str(ticket_id) if ticket_id is not None else None,
    )
    return success_response(request_id=context.request_id, data=result)


@router.get("/support-tickets/{ticket_id}", response_model=ApiResponse[SupportTicketRecord])
async def get_support_ticket(
    context: AuthenticatedRequestContextDep,
    service: SupportTicketServiceDep,
    ticket_id: UUID,
) -> ApiResponse[SupportTicketRecord]:
    result = await service.get_owned(context=context, ticket_id=str(ticket_id))
    return success_response(request_id=context.request_id, data=result)


@router.patch(
    "/support-tickets/{ticket_id}/status", response_model=ApiResponse[SupportTicketRecord]
)
async def update_support_ticket_status(
    context: AuthenticatedRequestContextDep,
    service: SupportTicketServiceDep,
    ticket_id: UUID,
    body: SupportTicketStatusUpdate,
) -> ApiResponse[SupportTicketRecord]:
    result = await service.update_status(context=context, ticket_id=str(ticket_id), command=body)
    return success_response(request_id=context.request_id, data=result)

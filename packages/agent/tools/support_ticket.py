"""Governed local mutation. Dispatch always needs an explicit durable approval."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from packages.agent.dto import ToolDefinition, ToolRateLimit
from packages.common.context import AuthenticatedRequestContext
from packages.support_ticket.dto import (
    SUPPORT_TICKET_CREATE_PERMISSION,
    SUPPORT_TICKET_TOOL_VERSION,
    CreateSupportTicketInput,
    CreateSupportTicketOutput,
)
from packages.support_ticket.receipt import support_ticket_receipt
from packages.support_ticket.service import SupportTicketService


def build_support_ticket_tool(
    *,
    service: SupportTicketService,
    timeout_seconds: float,
    rate_limit: ToolRateLimit,
) -> ToolDefinition:
    async def handler(
        payload: CreateSupportTicketInput,
        context: AuthenticatedRequestContext,
    ) -> CreateSupportTicketOutput:
        ticket = await service.create(context=context, command=payload)
        return CreateSupportTicketOutput(ticket_id=UUID(ticket.ticket_id), status=ticket.status)

    return ToolDefinition(
        name="create_support_ticket",
        description=(
            "Create one internal support ticket after the owner explicitly approves it. "
            "Use a fresh UUID v4 client_reference for a new ticket, "
            "and reuse it for the same request. "
            "This records a local application ticket only; it sends no email or external request."
        ),
        input_schema=CreateSupportTicketInput,
        output_schema=CreateSupportTicketOutput,
        permission=SUPPORT_TICKET_CREATE_PERMISSION,
        timeout_seconds=timeout_seconds,
        rate_limit=rate_limit,
        handler=handler,
        requires_approval=True,
        durable_replay_policy="never",
        implementation_version=SUPPORT_TICKET_TOOL_VERSION,
        prompt_projection=_ticket_prompt_projection,
    )


def _ticket_prompt_projection(output: Mapping[str, object]) -> Mapping[str, object]:
    return support_ticket_receipt(output)

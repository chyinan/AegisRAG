from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse

from apps.api.dependencies import AuthenticatedRequestContextDep
from apps.api.service_dependencies import AgentRunApplicationServiceDep, DurableAgentServiceDep
from packages.agent.dto import AgentRunRequestBody, AgentRunResponse
from packages.agent.durable.dto import (
    DurableCancelCommand,
    DurableRecoverCommand,
    DurableResumeCommand,
    DurableRunResponse,
    DurableStartCommand,
)
from packages.agent.exceptions import AGENT_RUN_FORBIDDEN, AgentRunError
from packages.agent.streaming import format_agent_sse_event
from packages.auth.policies import has_agent_run_permission
from packages.common.context import AuthenticatedRequestContext
from packages.common.envelope import ApiResponse, success_response

router = APIRouter(tags=["agent"])


def get_agent_context(context: AuthenticatedRequestContextDep) -> AuthenticatedRequestContext:
    if not has_agent_run_permission(context.auth):
        raise AgentRunError(
            code=AGENT_RUN_FORBIDDEN,
            message="Agent run permission is required.",
            details={"required_permissions": ["agent:run"]},
            status_code=403,
        )
    return context


AgentContextDep = Annotated[AuthenticatedRequestContext, Depends(get_agent_context)]


@router.post("/agent/run", response_model=ApiResponse[AgentRunResponse])
async def run_agent(
    context: AuthenticatedRequestContextDep,
    service: AgentRunApplicationServiceDep,
    body: AgentRunRequestBody,
) -> ApiResponse[AgentRunResponse]:
    result = await service.run(context=context, command=body.to_command())
    return success_response(request_id=context.request_id, data=result)


@router.post("/agent/run/stream")
async def stream_agent(
    context: AgentContextDep,
    service: AgentRunApplicationServiceDep,
    body: AgentRunRequestBody,
) -> StreamingResponse:
    async def event_frames() -> AsyncIterator[str]:
        events = service.stream(context=context, command=body.to_command())
        try:
            async for event in events:
                yield format_agent_sse_event(event)
        finally:
            await events.aclose()

    return StreamingResponse(
        event_frames(),
        media_type="text/event-stream",
        headers={"X-Request-ID": context.request_id, "Cache-Control": "no-store"},
    )


@router.post("/agent/durable", response_model=ApiResponse[DurableRunResponse])
async def start_durable_agent(
    context: AgentContextDep,
    service: DurableAgentServiceDep,
    body: DurableStartCommand,
) -> ApiResponse[DurableRunResponse]:
    result = await service.start(context=context, command=body)
    return success_response(request_id=context.request_id, data=result)


@router.post("/agent/durable/recover", response_model=ApiResponse[DurableRunResponse])
async def recover_durable_agent(
    context: AgentContextDep,
    service: DurableAgentServiceDep,
    body: DurableRecoverCommand,
) -> ApiResponse[DurableRunResponse]:
    result = await service.recover(context=context, idempotency_key=body.idempotency_key)
    return success_response(request_id=context.request_id, data=result)


@router.get("/agent/durable/{run_id}", response_model=ApiResponse[DurableRunResponse])
async def get_durable_agent(
    context: AgentContextDep,
    service: DurableAgentServiceDep,
    run_id: str,
) -> ApiResponse[DurableRunResponse]:
    result = await service.get(context=context, run_id=run_id)
    return success_response(request_id=context.request_id, data=result)


@router.post("/agent/durable/{run_id}/resume", response_model=ApiResponse[DurableRunResponse])
async def resume_durable_agent(
    context: AgentContextDep,
    service: DurableAgentServiceDep,
    run_id: str,
    body: DurableResumeCommand,
) -> ApiResponse[DurableRunResponse]:
    result = await service.resume(context=context, run_id=run_id, command=body)
    return success_response(request_id=context.request_id, data=result)


@router.post("/agent/durable/{run_id}/cancel", response_model=ApiResponse[DurableRunResponse])
async def cancel_durable_agent(
    context: AgentContextDep,
    service: DurableAgentServiceDep,
    run_id: str,
    body: DurableCancelCommand,
) -> ApiResponse[DurableRunResponse]:
    result = await service.cancel(context=context, run_id=run_id, command=body)
    return success_response(request_id=context.request_id, data=result)

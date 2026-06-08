from __future__ import annotations

from fastapi import APIRouter

from apps.api.dependencies import AuthenticatedRequestContextDep
from apps.api.service_dependencies import AgentRunApplicationServiceDep
from packages.agent.dto import AgentRunRequestBody, AgentRunResponse
from packages.common.envelope import ApiResponse, success_response

router = APIRouter(tags=["agent"])


@router.post("/agent/run", response_model=ApiResponse[AgentRunResponse])
async def run_agent(
    context: AuthenticatedRequestContextDep,
    service: AgentRunApplicationServiceDep,
    body: AgentRunRequestBody,
) -> ApiResponse[AgentRunResponse]:
    result = await service.run(context=context, command=body.to_command())
    return success_response(request_id=context.request_id, data=result)

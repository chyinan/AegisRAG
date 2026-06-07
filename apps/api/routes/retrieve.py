from __future__ import annotations

from fastapi import APIRouter

from apps.api.dependencies import AuthenticatedRequestContextDep
from apps.api.service_dependencies import RetrieveApplicationServiceDep
from packages.common.envelope import ApiResponse, success_response
from packages.retrieval.application import RetrieveRequestBody, RetrieveResponse

router = APIRouter(tags=["retrieval"])


@router.post("/retrieve", response_model=ApiResponse[RetrieveResponse])
async def retrieve(
    context: AuthenticatedRequestContextDep,
    service: RetrieveApplicationServiceDep,
    body: RetrieveRequestBody,
) -> ApiResponse[RetrieveResponse]:
    result = await service.retrieve(context=context, command=body.to_command())
    return success_response(request_id=context.request_id, data=result)

from __future__ import annotations

from fastapi import APIRouter

from apps.api.routes.query import RagQueryContextDep
from apps.api.service_dependencies import SourceResolveServiceDep
from packages.common.envelope import ApiResponse, success_response
from packages.rag.source_resolver import SourceResolveRequestBody, SourceResolveResponse

router = APIRouter(tags=["rag"])


@router.post("/sources/resolve", response_model=ApiResponse[SourceResolveResponse])
async def resolve_source(
    context: RagQueryContextDep,
    service: SourceResolveServiceDep,
    body: SourceResolveRequestBody,
) -> ApiResponse[SourceResolveResponse]:
    result = await service.resolve(context=context, command=body.to_command())
    return success_response(request_id=context.request_id, data=result)

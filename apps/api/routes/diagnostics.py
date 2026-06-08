from __future__ import annotations

from fastapi import APIRouter

from apps.api.dependencies import AuthenticatedRequestContextDep
from apps.api.service_dependencies import DiagnosticsServiceDep
from packages.common.envelope import ApiResponse, success_response
from packages.diagnostics.dto import DiagnosticsLookupRequest, DiagnosticsResolveResponse

router = APIRouter(tags=["diagnostics"])


@router.post("/diagnostics/resolve", response_model=ApiResponse[DiagnosticsResolveResponse])
async def resolve_diagnostics(
    context: AuthenticatedRequestContextDep,
    service: DiagnosticsServiceDep,
    body: DiagnosticsLookupRequest,
) -> ApiResponse[DiagnosticsResolveResponse]:
    result = await service.resolve(context=context, lookup=body)
    return success_response(request_id=context.request_id, data=result)

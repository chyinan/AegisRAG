from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request

from apps.api.dependencies import AuthenticatedRequestContextDep
from apps.api.service_dependencies import AuditExplorerServiceDep
from packages.audit import (
    AuditExplorerListResponse,
    AuditExportPayload,
    AuditExportRequest,
    AuditLogQueryRequest,
)
from packages.common.envelope import ApiResponse, success_response

router = APIRouter(prefix="/audit", tags=["audit"])

_FORBIDDEN_QUERY_PARAMS = {"tenant_id", "roles", "permissions"}


@router.get("/logs", response_model=ApiResponse[AuditExplorerListResponse])
async def list_audit_logs(
    request: Request,
    context: AuthenticatedRequestContextDep,
    service: AuditExplorerServiceDep,
    user_id: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    status: str | None = None,
    created_at_from: datetime | None = None,
    created_at_to: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    include_associations: bool = True,
) -> ApiResponse[AuditExplorerListResponse]:
    _reject_identity_override(request)
    result = await service.list_logs(
        context=context,
        query=AuditLogQueryRequest(
            user_id=user_id,
            request_id=request_id,
            trace_id=trace_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            status=status,
            created_at_from=created_at_from,
            created_at_to=created_at_to,
            limit=limit,
            include_associations=include_associations,
        ),
    )
    return success_response(request_id=context.request_id, data=result)


@router.post("/export", response_model=ApiResponse[AuditExportPayload])
async def export_audit_logs(
    context: AuthenticatedRequestContextDep,
    service: AuditExplorerServiceDep,
    export_request: AuditExportRequest,
) -> ApiResponse[AuditExportPayload]:
    result = await service.export_logs(context=context, request=export_request)
    return success_response(request_id=context.request_id, data=result)


def _reject_identity_override(request: Request) -> None:
    if _FORBIDDEN_QUERY_PARAMS.intersection(request.query_params.keys()):
        raise HTTPException(status_code=422, detail="Unsupported audit query parameter.")

from __future__ import annotations

from fastapi import APIRouter, Query

from apps.api.dependencies import AuthenticatedRequestContextDep
from apps.api.service_dependencies import EvalEvidenceServiceDep
from packages.common.envelope import ApiResponse, success_response
from packages.eval import EvalEvidenceReportListResponse, EvalEvidenceResolveResponse

router = APIRouter(prefix="/eval", tags=["eval"])


@router.get("/reports", response_model=ApiResponse[EvalEvidenceReportListResponse])
async def list_eval_reports(
    context: AuthenticatedRequestContextDep,
    service: EvalEvidenceServiceDep,
    limit: int = Query(default=20, ge=1, le=100),
) -> ApiResponse[EvalEvidenceReportListResponse]:
    result = await service.list_reports(context=context, limit=limit)
    return success_response(request_id=context.request_id, data=result)


@router.get("/reports/{report_filename}", response_model=ApiResponse[EvalEvidenceResolveResponse])
async def resolve_eval_report(
    context: AuthenticatedRequestContextDep,
    service: EvalEvidenceServiceDep,
    report_filename: str,
) -> ApiResponse[EvalEvidenceResolveResponse]:
    result = await service.resolve_report(context=context, report_filename=report_filename)
    return success_response(request_id=context.request_id, data=result)

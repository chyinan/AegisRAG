from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter

from apps.api.dependencies import AuthenticatedRequestContextDep
from apps.api.factories.billing import CostAccountingServiceDep
from packages.billing.dto import BudgetSummary, BusinessMetrics
from packages.common.envelope import ApiResponse, success_response

router = APIRouter(tags=["costs"])


@router.get("/costs/summary", response_model=ApiResponse[BusinessMetrics])
async def cost_summary(
    context: AuthenticatedRequestContextDep,
    service: CostAccountingServiceDep,
    start: datetime | None = None,
    end: datetime | None = None,
) -> ApiResponse[BusinessMetrics]:
    result = await service.summary(context=context, start=start, end=end)
    return success_response(request_id=context.request_id, data=result)


@router.get("/costs/budget", response_model=ApiResponse[BudgetSummary])
async def cost_budget(
    context: AuthenticatedRequestContextDep,
    service: CostAccountingServiceDep,
) -> ApiResponse[BudgetSummary]:
    result = await service.budget(context=context)
    return success_response(request_id=context.request_id, data=result)

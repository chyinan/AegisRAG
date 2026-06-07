from fastapi import APIRouter, Response, status

from apps.api.dependencies import RequestContextDep
from packages.common.envelope import ApiResponse, success_response
from packages.common.health import HealthData, ReadinessData, get_health_data
from packages.data.readiness import collect_readiness

router = APIRouter(tags=["system"])


@router.get("/health", response_model=ApiResponse[HealthData])
def get_health(context: RequestContextDep) -> ApiResponse[HealthData]:
    return success_response(
        request_id=context.request_id,
        data=get_health_data(),
    )


@router.get("/ready", response_model=ApiResponse[ReadinessData])
async def get_ready(
    response: Response,
    context: RequestContextDep,
) -> ApiResponse[ReadinessData]:
    readiness = await collect_readiness(context=context)
    if not readiness.ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return success_response(
        request_id=context.request_id,
        data=readiness,
    )

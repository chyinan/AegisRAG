"""Authenticated, audited operator actions on existing queue deliveries."""

from fastapi import APIRouter, Query

from apps.api.dependencies import AuthenticatedRequestContextDep
from apps.api.factories.queue_ops import QueueOperationsServiceDep
from packages.common.envelope import ApiResponse, success_response
from packages.ingestion.queue_operations import (
    JobKind,
    QueueJobListResult,
    QueueOperationCommand,
    QueueOperationResult,
)

router = APIRouter(prefix="/queue/jobs", tags=["queue-operations"])


@router.get("", response_model=ApiResponse[QueueJobListResult])
async def list_queue_attention_jobs(
    context: AuthenticatedRequestContextDep,
    service: QueueOperationsServiceDep,
    cursor: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=50, ge=1, le=100),
) -> ApiResponse[QueueJobListResult]:
    result = await service.list_jobs(context, cursor=cursor, limit=limit)
    return success_response(request_id=context.request_id, data=result)


@router.post("/{job_kind}/{queue_job_id}/retry", response_model=ApiResponse[QueueOperationResult])
async def retry_queue_delivery(
    context: AuthenticatedRequestContextDep,
    service: QueueOperationsServiceDep,
    job_kind: JobKind,
    queue_job_id: str,
    command: QueueOperationCommand,
) -> ApiResponse[QueueOperationResult]:
    result = await service.retry_job(
        context,
        job_kind=job_kind,
        queue_job_id=queue_job_id,
        command=command,
    )
    return success_response(request_id=context.request_id, data=result)


@router.post(
    "/{job_kind}/{queue_job_id}/terminate", response_model=ApiResponse[QueueOperationResult]
)
async def terminate_queue_delivery(
    context: AuthenticatedRequestContextDep,
    service: QueueOperationsServiceDep,
    job_kind: JobKind,
    queue_job_id: str,
    command: QueueOperationCommand,
) -> ApiResponse[QueueOperationResult]:
    result = await service.terminate_job(
        context,
        job_kind=job_kind,
        queue_job_id=queue_job_id,
        command=command,
    )
    return success_response(request_id=context.request_id, data=result)

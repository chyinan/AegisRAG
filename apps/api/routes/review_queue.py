from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request

from apps.api.dependencies import AuthenticatedRequestContextDep
from apps.api.service_dependencies import ReviewQueueServiceDep
from packages.common.envelope import ApiResponse, success_response
from packages.review import (
    EvalCandidatePreview,
    ReviewItemCreateRequest,
    ReviewItemQueryRequest,
    ReviewItemStatusUpdateRequest,
    ReviewItemSummary,
    ReviewQueueListResponse,
)

router = APIRouter(prefix="/review", tags=["review"])

_FORBIDDEN_PARAMS = {"tenant_id", "created_by", "user_id", "roles", "permissions"}


@router.post("/items", response_model=ApiResponse[ReviewItemSummary])
async def create_review_item(
    context: AuthenticatedRequestContextDep,
    service: ReviewQueueServiceDep,
    payload: ReviewItemCreateRequest,
) -> ApiResponse[ReviewItemSummary]:
    result = await service.create_item(context=context, request=payload)
    return success_response(request_id=context.request_id, data=result)


@router.get("/items", response_model=ApiResponse[ReviewQueueListResponse])
async def list_review_items(
    request: Request,
    context: AuthenticatedRequestContextDep,
    service: ReviewQueueServiceDep,
    item_type: str | None = None,
    severity: str | None = None,
    status: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    source_view: str | None = None,
    created_at_from: datetime | None = None,
    created_at_to: datetime | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> ApiResponse[ReviewQueueListResponse]:
    _reject_identity_override(request)
    result = await service.list_items(
        context=context,
        query=ReviewItemQueryRequest(
            item_type=item_type,  # type: ignore[arg-type]
            severity=severity,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            request_id=request_id,
            trace_id=trace_id,
            source_view=source_view,  # type: ignore[arg-type]
            created_at_from=created_at_from,
            created_at_to=created_at_to,
            limit=limit,
        ),
    )
    return success_response(request_id=context.request_id, data=result)


@router.get("/items/{item_id}", response_model=ApiResponse[ReviewItemSummary])
async def get_review_item(
    item_id: str,
    context: AuthenticatedRequestContextDep,
    service: ReviewQueueServiceDep,
) -> ApiResponse[ReviewItemSummary]:
    result = await service.get_item(context=context, item_id=item_id)
    return success_response(request_id=context.request_id, data=result)


@router.post("/items/{item_id}/status", response_model=ApiResponse[ReviewItemSummary])
async def update_review_item_status(
    item_id: str,
    context: AuthenticatedRequestContextDep,
    service: ReviewQueueServiceDep,
    payload: ReviewItemStatusUpdateRequest,
) -> ApiResponse[ReviewItemSummary]:
    result = await service.update_status(context=context, item_id=item_id, request=payload)
    return success_response(request_id=context.request_id, data=result)


@router.post("/items/{item_id}/eval-candidate", response_model=ApiResponse[EvalCandidatePreview])
async def convert_review_item_to_eval_candidate(
    item_id: str,
    context: AuthenticatedRequestContextDep,
    service: ReviewQueueServiceDep,
) -> ApiResponse[EvalCandidatePreview]:
    result = await service.convert_to_eval_candidate(context=context, item_id=item_id)
    return success_response(request_id=context.request_id, data=result)


def _reject_identity_override(request: Request) -> None:
    if _FORBIDDEN_PARAMS.intersection(request.query_params.keys()):
        raise HTTPException(status_code=422, detail="Unsupported review query parameter.")

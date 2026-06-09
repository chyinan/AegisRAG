from __future__ import annotations

from fastapi import APIRouter, Query

from apps.api.dependencies import AuthenticatedRequestContextDep
from apps.api.service_dependencies import DocumentLifecycleServiceDep
from packages.common.envelope import ApiResponse, success_response
from packages.data.dto import (
    DocumentDeleteCommand,
    DocumentDeleteResult,
    DocumentReviewListResult,
    DocumentVersionReviewDetail,
    DocumentVersionStatusResult,
)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("/review", response_model=ApiResponse[DocumentReviewListResult])
async def list_review_documents(
    context: AuthenticatedRequestContextDep,
    service: DocumentLifecycleServiceDep,
    status: str | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    cursor: str | None = Query(default=None),
) -> ApiResponse[DocumentReviewListResult]:
    result = await service.list_review_documents(
        context,
        status=status,
        limit=limit,
        cursor=cursor,
    )
    return success_response(request_id=context.request_id, data=result)


@router.get("/{document_id}/review", response_model=ApiResponse[DocumentVersionReviewDetail])
async def get_review_document_detail(
    context: AuthenticatedRequestContextDep,
    service: DocumentLifecycleServiceDep,
    document_id: str,
) -> ApiResponse[DocumentVersionReviewDetail]:
    result = await service.get_review_document_detail(
        context,
        document_id=document_id,
        version_id=None,
    )
    return success_response(request_id=context.request_id, data=result)


@router.get(
    "/{document_id}/versions/{version_id}/review",
    response_model=ApiResponse[DocumentVersionReviewDetail],
)
async def get_review_document_version_detail(
    context: AuthenticatedRequestContextDep,
    service: DocumentLifecycleServiceDep,
    document_id: str,
    version_id: str,
) -> ApiResponse[DocumentVersionReviewDetail]:
    result = await service.get_review_document_detail(
        context,
        document_id=document_id,
        version_id=version_id,
    )
    return success_response(request_id=context.request_id, data=result)


@router.get(
    "/{document_id}/versions/{version_id}/status",
    response_model=ApiResponse[DocumentVersionStatusResult],
)
async def get_document_version_status(
    context: AuthenticatedRequestContextDep,
    service: DocumentLifecycleServiceDep,
    document_id: str,
    version_id: str,
) -> ApiResponse[DocumentVersionStatusResult]:
    result = await service.get_version_status(
        context,
        document_id=document_id,
        version_id=version_id,
    )
    return success_response(request_id=context.request_id, data=result)


@router.delete("/{document_id}", response_model=ApiResponse[DocumentDeleteResult])
async def delete_document(
    context: AuthenticatedRequestContextDep,
    service: DocumentLifecycleServiceDep,
    document_id: str,
) -> ApiResponse[DocumentDeleteResult]:
    result = await service.delete(
        context,
        DocumentDeleteCommand(document_id=document_id),
    )
    return success_response(request_id=context.request_id, data=result)


@router.delete(
    "/{document_id}/versions/{version_id}",
    response_model=ApiResponse[DocumentDeleteResult],
)
async def delete_document_version(
    context: AuthenticatedRequestContextDep,
    service: DocumentLifecycleServiceDep,
    document_id: str,
    version_id: str,
) -> ApiResponse[DocumentDeleteResult]:
    result = await service.delete(
        context,
        DocumentDeleteCommand(document_id=document_id, version_id=version_id),
    )
    return success_response(request_id=context.request_id, data=result)

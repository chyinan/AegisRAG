from __future__ import annotations

from fastapi import APIRouter

from apps.api.dependencies import AuthenticatedRequestContextDep
from apps.api.service_dependencies import DocumentLifecycleServiceDep
from packages.common.envelope import ApiResponse, success_response
from packages.data.dto import (
    DocumentDeleteCommand,
    DocumentDeleteResult,
    DocumentVersionStatusResult,
)

router = APIRouter(prefix="/documents", tags=["documents"])


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

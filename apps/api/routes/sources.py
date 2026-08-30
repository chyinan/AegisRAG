from __future__ import annotations

# pattern: Imperative Shell
import os
import re
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel, Field

from apps.api.dependencies import AuthenticatedRequestContextDep
from apps.api.routes.query import RagQueryContextDep
from apps.api.service_dependencies import FolderImportServiceDep, SourceResolveServiceDep
from packages.common.config import load_settings
from packages.common.envelope import ApiResponse, success_response
from packages.data.dto import UploadDocumentResult
from packages.data.exceptions import DocumentUploadInvalidMetadataError
from packages.ingestion.connectors import FolderConnector
from packages.rag.source_resolver import SourceResolveRequestBody, SourceResolveResponse

router = APIRouter(tags=["rag"])


class FolderImportRequest(BaseModel):
    connector_id: str = Field(default="folder", min_length=1, max_length=64)
    cursor: str | None = Field(default=None, max_length=512)
    batch_size: int = Field(default=25, ge=1, le=100)


class FolderImportResponse(BaseModel):
    connector_id: str
    imported_count: int
    documents: tuple[UploadDocumentResult, ...]
    next_cursor: str | None = None


@router.post("/sources/resolve", response_model=ApiResponse[SourceResolveResponse])
async def resolve_source(
    context: RagQueryContextDep,
    service: SourceResolveServiceDep,
    body: SourceResolveRequestBody,
) -> ApiResponse[SourceResolveResponse]:
    result = await service.resolve(context=context, command=body.to_command())
    return success_response(request_id=context.request_id, data=result)


@router.post(
    "/sources/folder/import",
    response_model=ApiResponse[FolderImportResponse],
)
async def import_folder(
    context: AuthenticatedRequestContextDep,
    service: FolderImportServiceDep,
    body: FolderImportRequest,
) -> ApiResponse[FolderImportResponse]:
    settings = load_settings()
    if body.connector_id != "folder":
        raise DocumentUploadInvalidMetadataError(details={"field": "connector_id"})
    tenant_segment = context.auth.tenant_id
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", tenant_segment) is None:
        raise DocumentUploadInvalidMetadataError(details={"field": "tenant_id"})
    base_root = Path(settings.folder_connector_root).expanduser().resolve()
    tenant_root = base_root / tenant_segment
    # A tenant directory is a trust boundary.  Reject symlink roots and any
    # resolution that escapes the configured connector root before scanning.
    if tenant_root.is_symlink():
        raise DocumentUploadInvalidMetadataError(
            details={"field": "folder_connector_root", "reason": "tenant_root_symlink"}
        )
    try:
        tenant_root.resolve().relative_to(base_root)
    except ValueError as exc:
        raise DocumentUploadInvalidMetadataError(
            details={"field": "folder_connector_root", "reason": "tenant_root_escape"}
        ) from exc
    try:
        cursor_secret = os.getenv("FOLDER_CURSOR_SECRET") or os.getenv("JWT_SECRET")
        if not cursor_secret and os.getenv("APP_ENV", "local").strip().lower() == "production":
            raise ValueError("FOLDER_CURSOR_SECRET is required in production")
        connector = FolderConnector(
            root=tenant_root,
            tenant_id=context.auth.tenant_id,
            connector_id=body.connector_id,
            max_bytes=settings.upload_max_bytes,
            cursor_secret=cursor_secret or "development-folder-cursor",
        )
    except ValueError as exc:
        raise DocumentUploadInvalidMetadataError(
            details={"field": "folder_connector_root"}
        ) from exc
    batch = await service.import_folder(
        context=context,
        connector=connector,
        cursor=body.cursor,
        batch_size=body.batch_size,
    )
    return success_response(
        request_id=context.request_id,
        data=FolderImportResponse(
            connector_id=body.connector_id,
            imported_count=len(batch.documents),
            documents=batch.documents,
            next_cursor=batch.next_cursor,
        ),
    )

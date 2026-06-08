from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile
from pydantic import ValidationError

from apps.api.dependencies import AuthenticatedRequestContextDep
from apps.api.service_dependencies import DocumentUploadServiceDep
from packages.common.envelope import ApiResponse, success_response
from packages.data.dto import UploadDocumentCommand, UploadDocumentResult
from packages.data.exceptions import DocumentUploadInvalidMetadataError

router = APIRouter(tags=["documents"])


@router.post("/upload", response_model=ApiResponse[UploadDocumentResult])
async def upload_document(
    context: AuthenticatedRequestContextDep,
    service: DocumentUploadServiceDep,
    file: Annotated[UploadFile, File()],
    source_type: Annotated[str, Form()],
    document_id: Annotated[str | None, Form()] = None,
    version_id: Annotated[str | None, Form()] = None,
    source_uri: Annotated[str | None, Form()] = None,
    title: Annotated[str | None, Form()] = None,
    acl: Annotated[str | None, Form()] = None,
    metadata: Annotated[str | None, Form()] = None,
) -> ApiResponse[UploadDocumentResult]:
    try:
        command = UploadDocumentCommand(
            document_id=document_id,
            version_id=version_id,
            filename=file.filename or "upload",
            content_type=file.content_type,
            source_type=source_type,
            source_uri=source_uri,
            title=title,
            acl=_parse_json_mapping("acl", acl) or {"visibility": "tenant"},
            metadata=_parse_json_mapping("metadata", metadata) or {},
            stream=file.file,
        )
    except ValidationError as exc:
        raise DocumentUploadInvalidMetadataError(details={"field": "upload"}) from exc
    result = await service.upload(context, command)
    return success_response(request_id=context.request_id, data=result)


def _parse_json_mapping(field_name: str, raw_value: str | None) -> dict[str, object] | None:
    if raw_value is None or not raw_value.strip():
        return None
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError as exc:
        raise DocumentUploadInvalidMetadataError(details={"field": field_name}) from exc
    if not isinstance(parsed, Mapping):
        raise DocumentUploadInvalidMetadataError(details={"field": field_name})
    return dict(parsed)

from __future__ import annotations

from packages.ingestion.domain import (
    ParsedDocument,
    ParseRequest,
    Section,
    safe_title_from_filename,
)
from packages.ingestion.parsers._common import decode_utf8_strict, section_metadata


class TxtParser:
    async def parse(self, request: ParseRequest) -> ParsedDocument:
        text = decode_utf8_strict(request.content)
        title = safe_title_from_filename(request.filename)
        title_path = [title]
        section = Section(
            section_id=f"{request.version_id}:section-1",
            tenant_id=request.tenant_id,
            document_id=request.document_id,
            version_id=request.version_id,
            source_type="txt",
            source_uri=request.source_uri,
            title=title,
            title_path=title_path,
            content=text,
            acl=request.acl,
            metadata=section_metadata(request, title_path=title_path),
        )
        return ParsedDocument(
            tenant_id=request.tenant_id,
            document_id=request.document_id,
            version_id=request.version_id,
            source_type="txt",
            source_uri=request.source_uri,
            sections=[section],
            acl=request.acl,
            checksum=request.checksum,
            metadata={
                "filename": request.filename,
                "source_uri": request.source_uri,
                "section_count": 1,
            },
        )

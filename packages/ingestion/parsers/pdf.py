from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from packages.ingestion.domain import (
    ParsedDocument,
    ParseRequest,
    Section,
    safe_title_from_filename,
)
from packages.ingestion.exceptions import EmptyDocumentContentError, GenericDocumentParseError

_PDF_PARSE_EXCEPTIONS = (
    PdfReadError,
    OSError,
    ValueError,
    KeyError,
    TypeError,
    NotImplementedError,
    RecursionError,
)


class PdfParser:
    async def parse(self, request: ParseRequest) -> ParsedDocument:
        try:
            reader = PdfReader(BytesIO(request.content))
        except _PDF_PARSE_EXCEPTIONS as exc:
            raise GenericDocumentParseError(details={"reason": "invalid_pdf"}) from exc

        if reader.is_encrypted:
            try:
                decrypt_result = reader.decrypt("")
            except _PDF_PARSE_EXCEPTIONS as exc:
                raise GenericDocumentParseError(details={"reason": "encrypted_pdf"}) from exc
            if decrypt_result == 0:
                raise GenericDocumentParseError(details={"reason": "encrypted_pdf"})

        try:
            page_count = len(reader.pages)
        except _PDF_PARSE_EXCEPTIONS as exc:
            raise GenericDocumentParseError(details={"reason": "page_tree_failed"}) from exc
        if page_count == 0:
            raise EmptyDocumentContentError()

        title = safe_title_from_filename(request.filename)
        sections: list[Section] = []
        page_ranges: list[list[int]] = []
        for page_index in range(page_count):
            page_number = page_index + 1
            try:
                page = reader.pages[page_index]
                extracted = page.extract_text() or ""
            except _PDF_PARSE_EXCEPTIONS as exc:
                raise GenericDocumentParseError(
                    details={"reason": "page_extract_failed", "page": page_number}
                ) from exc
            page_text = extracted.strip()
            if not page_text:
                continue
            title_path = [title, f"Page {page_number}"]
            page_ranges.append([page_number, page_number])
            sections.append(
                Section(
                    section_id=f"{request.version_id}:page-{page_number}",
                    tenant_id=request.tenant_id,
                    document_id=request.document_id,
                    version_id=request.version_id,
                    source_type="pdf",
                    source_uri=request.source_uri,
                    title=f"Page {page_number}",
                    title_path=title_path,
                    content=page_text,
                    page_start=page_number,
                    page_end=page_number,
                    acl=request.acl,
                    metadata={
                        "source_uri": request.source_uri,
                        "filename": request.filename,
                        "checksum": request.checksum,
                        "title_path": title_path,
                        "page_start": page_number,
                        "page_end": page_number,
                        "page_count": page_count,
                        "content_char_count": len(page_text),
                        "metadata": dict(request.metadata),
                    },
                )
            )

        if not sections:
            raise EmptyDocumentContentError()

        return ParsedDocument(
            tenant_id=request.tenant_id,
            document_id=request.document_id,
            version_id=request.version_id,
            source_type="pdf",
            source_uri=request.source_uri,
            sections=sections,
            acl=request.acl,
            checksum=request.checksum,
            metadata={
                "filename": request.filename,
                "source_uri": request.source_uri,
                "section_count": len(sections),
                "page_count": page_count,
                "page_ranges": page_ranges,
            },
        )

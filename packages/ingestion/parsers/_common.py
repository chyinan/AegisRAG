from __future__ import annotations

from packages.ingestion.domain import ParseRequest
from packages.ingestion.exceptions import DocumentEncodingError, EmptyDocumentContentError


def decode_utf8_strict(content: bytes) -> str:
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise DocumentEncodingError() from exc
    if not text.strip():
        raise EmptyDocumentContentError()
    return text


def section_metadata(request: ParseRequest, *, title_path: list[str]) -> dict[str, object]:
    return {
        "source_uri": request.source_uri,
        "filename": request.filename,
        "checksum": request.checksum,
        "title_path": title_path,
        "metadata": dict(request.metadata),
    }

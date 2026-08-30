"""Content validation shared by upload and connector ingestion paths."""

# pattern: Functional Core

from __future__ import annotations

from io import BytesIO
from zipfile import BadZipFile, ZipFile


class UnsafeDocumentContentError(ValueError):
    """Raised when bytes do not match the declared document type safely."""


def validate_content_signature(
    *,
    source_type: str,
    payload: bytes,
    max_uncompressed_bytes: int = 100 * 1024 * 1024,
    max_compression_ratio: float = 100.0,
) -> None:
    header = payload[:4096]
    signatures: dict[str, tuple[bytes, ...]] = {
        "pdf": (b"%PDF-",),
        "scanned_pdf": (b"%PDF-",),
        "docx": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
        "image": (
            b"\xff\xd8\xff",
            b"\x89PNG\r\n\x1a\n",
            b"BM",
            b"II*\x00",
            b"MM\x00*",
        ),
    }
    expected = signatures.get(source_type)
    if expected is not None and not any(header.startswith(signature) for signature in expected):
        raise UnsafeDocumentContentError("content signature does not match source type")
    if source_type in {"txt", "markdown"}:
        if b"\x00" in payload:
            raise UnsafeDocumentContentError("text content contains binary bytes")
        try:
            payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UnsafeDocumentContentError("text content is not valid UTF-8") from exc
    if source_type == "docx":
        _validate_docx_archive(payload, max_uncompressed_bytes, max_compression_ratio)


def _validate_docx_archive(
    payload: bytes,
    max_uncompressed_bytes: int,
    max_compression_ratio: float,
) -> None:
    try:
        with ZipFile(BytesIO(payload)) as archive:
            names = set(archive.namelist())
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise UnsafeDocumentContentError("docx package entries are incomplete")
            uncompressed = 0
            for info in archive.infolist():
                if info.flag_bits & 0x1:
                    raise UnsafeDocumentContentError("encrypted docx entries are not allowed")
                uncompressed += info.file_size
                if uncompressed > max_uncompressed_bytes:
                    raise UnsafeDocumentContentError("docx uncompressed size exceeds limit")
                if (
                    info.compress_size > 0
                    and info.file_size / info.compress_size > max_compression_ratio
                ):
                    raise UnsafeDocumentContentError("docx compression ratio exceeds limit")
    except BadZipFile as exc:
        raise UnsafeDocumentContentError("docx archive is invalid") from exc

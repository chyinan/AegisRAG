from __future__ import annotations

import builtins
import struct
import zlib
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest

from packages.ingestion.domain import ParseRequest
from packages.ingestion.exceptions import DocumentParseError
from packages.ingestion.parsers.ocr import (
    ImageOcrParser,
    ScannedPdfOcrParser,
    TesseractOCRProvider,
)


def _request(
    *,
    content: bytes,
    source_type: str = "image",
    filename: str = "scan.png",
) -> ParseRequest:
    return ParseRequest(
        tenant_id="tenant-1",
        document_id="doc-1",
        version_id="ver-1",
        source_type=source_type,
        source_uri="kb://scan",
        filename=filename,
        content=content,
        acl={"visibility": "tenant"},
        metadata={"department": "HR"},
        checksum="checksum-1",
    )


def _minimal_png_bytes() -> bytes:
    """Return a minimal valid 1x1 black PNG image."""

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        chunk = chunk_type + data
        crc = struct.pack(">I", zlib.crc32(chunk) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + chunk + crc

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    ihdr = _chunk(b"IHDR", ihdr_data)
    raw_row = b"\x00\x00\x00\x00"  # filter byte + RGB
    compressed = zlib.compress(raw_row)
    idat = _chunk(b"IDAT", compressed)
    iend = _chunk(b"IEND", b"")
    return signature + ihdr + idat + iend


def _minimal_pdf_bytes(page_texts: list[str]) -> bytes:
    """Build a minimal valid PDF binary."""

    def _page_stream(text: str) -> str:
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        return f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET"

    objects: list[str] = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    page_refs: list[str] = []
    content_object_numbers: list[int] = []
    page_object_numbers: list[int] = []
    next_object_number = 4
    for _page_text in page_texts:
        page_object_numbers.append(next_object_number)
        content_object_numbers.append(next_object_number + 1)
        page_refs.append(f"{next_object_number} 0 R")
        next_object_number += 2

    objects[1] = f"<< /Type /Pages /Kids [{' '.join(page_refs)}] /Count {len(page_refs)} >>"
    for page_number, page_text in enumerate(page_texts):
        content = _page_stream(page_text)
        objects.append(
            "<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 3 0 R >> >> "
            f"/MediaBox [0 0 612 792] /Contents {content_object_numbers[page_number]} 0 R >>"
        )
        objects.append(
            f"<< /Length {len(content.encode('latin-1'))} >>\nstream\n{content}\nendstream"
        )

    output = BytesIO()
    output.write(b"%PDF-1.4\n")
    offsets = [0]
    for object_number, body in enumerate(objects, start=1):
        offsets.append(output.tell())
        output.write(f"{object_number} 0 obj\n{body}\nendobj\n".encode("latin-1"))
    xref_offset = output.tell()
    output.write(f"xref\n0 {len(objects) + 1}\n".encode("latin-1"))
    output.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode("latin-1"))
    output.write(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("latin-1")
    )
    return output.getvalue()


# ── _check_tesseract unit tests ─────────────────────────────────────


def test_check_tesseract_raises_when_not_found() -> None:
    """TesseractOCRProvider._ensure_tesseract raises when tesseract not on PATH."""
    provider = TesseractOCRProvider()
    with patch("shutil.which", return_value=None):
        with pytest.raises(DocumentParseError) as exc_info:
            provider.extract_text(image=MagicMock())
        assert exc_info.value.code == "DOCUMENT_PARSE_FAILED"
        assert "tesseract_not_installed" in str(exc_info.value.details)


def test_check_tesseract_passes_when_found() -> None:
    """TesseractOCRProvider succeeds when tesseract is on PATH."""
    provider = TesseractOCRProvider()
    with patch("shutil.which", return_value="/usr/bin/tesseract"):
        with patch("pytesseract.image_to_string", return_value="Hello"):
            result = provider.extract_text(image=MagicMock())
            assert result == "Hello"


# ── ImageOcrParser tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_image_ocr_parser_rejects_when_tesseract_missing() -> None:
    parser = ImageOcrParser()
    with patch("shutil.which", return_value=None):
        with pytest.raises(DocumentParseError) as exc_info:
            await parser.parse(_request(content=_minimal_png_bytes()))
        assert exc_info.value.code == "DOCUMENT_PARSE_FAILED"
        assert "tesseract_not_installed" in str(exc_info.value.details)


@pytest.mark.asyncio
async def test_image_ocr_parser_rejects_invalid_image_data() -> None:
    parser = ImageOcrParser()
    with patch("shutil.which", return_value="/usr/bin/tesseract"):
        with pytest.raises(DocumentParseError) as exc_info:
            await parser.parse(_request(content=b"not-an-image"))
        assert exc_info.value.code == "DOCUMENT_PARSE_FAILED"
        assert "image_open_failed" in str(exc_info.value.details)


@pytest.mark.asyncio
async def test_image_ocr_parser_extracts_text_and_returns_parsed_document() -> None:
    parser = ImageOcrParser()

    with (
        patch("shutil.which", return_value="/usr/bin/tesseract"),
        patch(
            "pytesseract.image_to_string",
            return_value="Hello world from OCR\n",
        ),
    ):
        parsed = await parser.parse(
            _request(content=_minimal_png_bytes(), source_type="image", filename="scan.png")
        )

    assert parsed.source_type == "image"
    assert parsed.metadata["page_count"] == 1
    assert parsed.metadata["page_ranges"] == [[1, 1]]
    assert len(parsed.sections) == 1
    section = parsed.sections[0]
    assert section.content == "Hello world from OCR"
    assert section.title_path == ["scan.png"]
    assert section.page_start == 1
    assert section.page_end == 1
    assert section.source_type == "image"
    assert section.metadata["checksum"] == "checksum-1"
    assert section.metadata["filename"] == "scan.png"


@pytest.mark.asyncio
async def test_image_ocr_parser_rejects_empty_ocr_result() -> None:
    parser = ImageOcrParser()

    with (
        patch("shutil.which", return_value="/usr/bin/tesseract"),
        patch(
            "pytesseract.image_to_string",
            return_value="   \n\t ",
        ),
    ):
        with pytest.raises(DocumentParseError) as exc_info:
            await parser.parse(_request(content=_minimal_png_bytes()))
        assert exc_info.value.code == "DOCUMENT_PARSE_EMPTY_CONTENT"


# ── ScannedPdfOcrParser tests ───────────────────────────────────────


@pytest.mark.asyncio
async def test_scanned_pdf_ocr_parser_rejects_when_tesseract_missing() -> None:
    parser = ScannedPdfOcrParser()
    with patch("shutil.which", return_value=None):
        with pytest.raises(DocumentParseError) as exc_info:
            await parser.parse(
                _request(
                    content=_minimal_pdf_bytes(["text"]),
                    source_type="scanned_pdf",
                    filename="scan.pdf",
                )
            )
        assert exc_info.value.code == "DOCUMENT_PARSE_FAILED"
        assert "tesseract_not_installed" in str(exc_info.value.details)


@pytest.mark.asyncio
async def test_scanned_pdf_ocr_parser_rejects_when_pymupdf_missing() -> None:
    parser = ScannedPdfOcrParser()

    original_import = builtins.__import__

    def _block_fitz_import(name, *args, **kwargs):
        if name == "fitz":
            raise ImportError("No module named fitz")
        return original_import(name, *args, **kwargs)

    with (
        patch("shutil.which", return_value="/usr/bin/tesseract"),
        patch("builtins.__import__", side_effect=_block_fitz_import),
    ):
        with pytest.raises(DocumentParseError) as exc_info:
            await parser.parse(
                _request(
                    content=_minimal_pdf_bytes(["text"]),
                    source_type="scanned_pdf",
                    filename="scan.pdf",
                )
            )
        assert exc_info.value.code == "DOCUMENT_PARSE_FAILED"
        assert "pymupdf_not_installed" in str(exc_info.value.details)


@pytest.mark.asyncio
async def test_scanned_pdf_ocr_parser_extracts_pages_with_ocr() -> None:
    parser = ScannedPdfOcrParser()

    mock_page = MagicMock()
    mock_pix = MagicMock()
    mock_pix.width = 100
    mock_pix.height = 100
    mock_pix.samples = b"\x00" * (100 * 100 * 3)
    mock_page.get_pixmap.return_value = mock_pix

    mock_doc = MagicMock()
    mock_doc.page_count = 2
    mock_doc.load_page.return_value = mock_page

    with (
        patch("shutil.which", return_value="/usr/bin/tesseract"),
        patch("fitz.open", return_value=mock_doc),
        patch(
            "pytesseract.image_to_string",
            side_effect=["First page OCR text\n", "Second page OCR text\n"],
        ),
    ):
        parsed = await parser.parse(
            _request(
                content=_minimal_pdf_bytes(["text"]),
                source_type="scanned_pdf",
                filename="scan.pdf",
            )
        )

    assert parsed.source_type == "scanned_pdf"
    assert parsed.metadata["page_count"] == 2
    assert parsed.metadata["page_ranges"] == [[1, 1], [2, 2]]
    assert len(parsed.sections) == 2
    assert parsed.sections[0].content == "First page OCR text"
    assert parsed.sections[1].content == "Second page OCR text"
    assert parsed.sections[0].title_path == ["scan.pdf", "Page 1"]
    assert parsed.sections[1].title_path == ["scan.pdf", "Page 2"]
    assert parsed.sections[0].page_start == 1
    assert parsed.sections[0].page_end == 1
    assert parsed.sections[1].page_start == 2
    assert parsed.sections[1].page_end == 2
    mock_doc.close.assert_called_once()


@pytest.mark.asyncio
async def test_scanned_pdf_ocr_parser_skips_empty_pages() -> None:
    parser = ScannedPdfOcrParser()

    mock_page = MagicMock()
    mock_pix = MagicMock()
    mock_pix.width = 100
    mock_pix.height = 100
    mock_pix.samples = b"\x00" * (100 * 100 * 3)
    mock_page.get_pixmap.return_value = mock_pix

    mock_doc = MagicMock()
    mock_doc.page_count = 3
    mock_doc.load_page.return_value = mock_page

    with (
        patch("shutil.which", return_value="/usr/bin/tesseract"),
        patch("fitz.open", return_value=mock_doc),
        patch(
            "pytesseract.image_to_string",
            side_effect=["Page 1 text\n", "   \n\t", "Page 3 text\n"],
        ),
    ):
        parsed = await parser.parse(
            _request(
                content=_minimal_pdf_bytes(["text"]),
                source_type="scanned_pdf",
                filename="scan.pdf",
            )
        )

    assert len(parsed.sections) == 2
    assert parsed.sections[0].content == "Page 1 text"
    assert parsed.sections[1].content == "Page 3 text"
    assert parsed.metadata["page_ranges"] == [[1, 1], [3, 3]]


@pytest.mark.asyncio
async def test_scanned_pdf_ocr_parser_rejects_all_empty_pages() -> None:
    parser = ScannedPdfOcrParser()

    mock_page = MagicMock()
    mock_pix = MagicMock()
    mock_pix.width = 100
    mock_pix.height = 100
    mock_pix.samples = b"\x00" * (100 * 100 * 3)
    mock_page.get_pixmap.return_value = mock_pix

    mock_doc = MagicMock()
    mock_doc.page_count = 2
    mock_doc.load_page.return_value = mock_page

    with (
        patch("shutil.which", return_value="/usr/bin/tesseract"),
        patch("fitz.open", return_value=mock_doc),
        patch(
            "pytesseract.image_to_string",
            return_value="   \n\t",
        ),
    ):
        with pytest.raises(DocumentParseError) as exc_info:
            await parser.parse(
                _request(
                    content=_minimal_pdf_bytes(["text"]),
                    source_type="scanned_pdf",
                    filename="scan.pdf",
                )
            )
        assert exc_info.value.code == "DOCUMENT_PARSE_EMPTY_CONTENT"


@pytest.mark.asyncio
async def test_scanned_pdf_ocr_parser_rejects_zero_page_pdf() -> None:
    parser = ScannedPdfOcrParser()

    mock_doc = MagicMock()
    mock_doc.page_count = 0

    with (
        patch("shutil.which", return_value="/usr/bin/tesseract"),
        patch("fitz.open", return_value=mock_doc),
    ):
        with pytest.raises(DocumentParseError) as exc_info:
            await parser.parse(
                _request(
                    content=_minimal_pdf_bytes([]),
                    source_type="scanned_pdf",
                    filename="empty.pdf",
                )
            )
        assert exc_info.value.code == "DOCUMENT_PARSE_EMPTY_CONTENT"
        mock_doc.close.assert_called_once()

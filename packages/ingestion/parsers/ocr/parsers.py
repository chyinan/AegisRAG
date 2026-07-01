"""Provider-neutral OCR parsers.

ImageOcrParser and ScannedPdfOcrParser accept any OCRProvider (Tesseract,
PaddleOCR, Surya, …) and delegate text extraction to it.
"""

from __future__ import annotations

from io import BytesIO

from packages.common.config import AppSettings
from packages.ingestion.domain import (
    ParsedDocument,
    ParseRequest,
    Section,
    safe_title_from_filename,
)
from packages.ingestion.exceptions import EmptyDocumentContentError, GenericDocumentParseError
from packages.ingestion.parsers.ocr.ports import OCRProvider, ocr_extract


def create_ocr_provider(settings: "AppSettings") -> OCRProvider:
    """Create the configured OCR provider from settings.

    Supported values for ``OCR_PROVIDER``:
      - ``tesseract`` — Tesseract via pytesseract (default, no extra deps)
      - ``paddle``   — PaddleOCR (Baidu, strong Chinese)
      - ``surya``    — Surya (modern DL OCR, 90+ languages)
    """
    provider = settings.ocr_provider.strip().lower()

    if provider == "tesseract":
        from packages.ingestion.parsers.ocr.tesseract import TesseractOCRProvider
        return TesseractOCRProvider()

    if provider == "paddle":
        from packages.ingestion.parsers.ocr.paddle import PaddleOCRProvider
        return PaddleOCRProvider()

    if provider == "surya":
        from packages.ingestion.parsers.ocr.surya import SuryaOCRProvider
        return SuryaOCRProvider()

    raise GenericDocumentParseError(
        details={
            "reason": "unknown_ocr_provider",
            "provider": provider,
            "available": ["tesseract", "paddle", "surya"],
        }
    )


class ImageOcrParser:
    """OCR parser for common image formats (jpg, png, bmp, tiff).

    Uses a pluggable OCRProvider — defaults to Tesseract.
    """

    def __init__(self, ocr: OCRProvider | None = None) -> None:
        if ocr is None:
            from packages.ingestion.parsers.ocr.tesseract import TesseractOCRProvider
            ocr = TesseractOCRProvider()
        self._ocr = ocr

    async def parse(self, request: ParseRequest) -> ParsedDocument:
        try:
            from PIL import Image

            # Protect against decompression bombs (PIL's default is
            # ~89.5 MP; we raise it to 200 MP as a reasonable cap).
            _prev_max_pixels = Image.MAX_IMAGE_PIXELS
            Image.MAX_IMAGE_PIXELS = 200_000_000
            try:
                image = Image.open(BytesIO(request.content))
            finally:
                Image.MAX_IMAGE_PIXELS = _prev_max_pixels
        except Exception as exc:
            raise GenericDocumentParseError(
                details={"reason": "image_open_failed"}
            ) from exc

        if image.mode not in ("RGB", "L"):
            try:
                image = image.convert("RGB")
            except Exception as exc:
                raise GenericDocumentParseError(
                    details={"reason": "image_convert_failed"}
                ) from exc

        try:
            text = await ocr_extract(self._ocr, image=image, lang="eng+chi_sim")
        except Exception as exc:
            raise GenericDocumentParseError(
                details={"reason": "ocr_failed"}
            ) from exc

        text = text.strip()
        if not text:
            raise EmptyDocumentContentError()

        title = safe_title_from_filename(request.filename)
        section = Section(
            section_id=f"{request.version_id}:ocr-page-1",
            tenant_id=request.tenant_id,
            document_id=request.document_id,
            version_id=request.version_id,
            source_type=request.source_type,
            source_uri=request.source_uri,
            title=title,
            title_path=[title],
            content=text,
            page_start=1,
            page_end=1,
            acl=request.acl,
            metadata={
                "source_uri": request.source_uri,
                "filename": request.filename,
                "checksum": request.checksum,
                "title_path": [title],
                "page_start": 1,
                "page_end": 1,
                "page_count": 1,
                "content_char_count": len(text),
                "metadata": dict(request.metadata),
            },
        )

        return ParsedDocument(
            tenant_id=request.tenant_id,
            document_id=request.document_id,
            version_id=request.version_id,
            source_type=request.source_type,
            source_uri=request.source_uri,
            sections=[section],
            acl=request.acl,
            checksum=request.checksum,
            metadata={
                "filename": request.filename,
                "source_uri": request.source_uri,
                "section_count": 1,
                "page_count": 1,
                "page_ranges": [[1, 1]],
            },
        )


class ScannedPdfOcrParser:
    """OCR parser for scanned/image-based PDFs.

    Renders each page via PyMuPDF at 300 DPI, then runs OCR via a pluggable
    OCRProvider.  Defaults to Tesseract.
    """

    def __init__(self, ocr: OCRProvider | None = None) -> None:
        if ocr is None:
            from packages.ingestion.parsers.ocr.tesseract import TesseractOCRProvider
            ocr = TesseractOCRProvider()
        self._ocr = ocr

    async def parse(self, request: ParseRequest) -> ParsedDocument:
        try:
            import fitz  # PyMuPDF
        except ImportError as exc:
            raise GenericDocumentParseError(
                details={
                    "reason": "pymupdf_not_installed",
                    "help": "Install PyMuPDF: pip install pymupdf",
                }
            ) from exc

        try:
            doc = fitz.open(stream=request.content, filetype="pdf")
        except Exception as exc:
            raise GenericDocumentParseError(
                details={"reason": "scanned_pdf_open_failed"}
            ) from exc

        try:
            page_count = doc.page_count
            if page_count == 0:
                raise EmptyDocumentContentError()

            settings = AppSettings()
            if page_count > settings.ocr_max_pdf_pages:
                raise GenericDocumentParseError(
                    details={
                        "reason": "pdf_too_many_pages",
                        "page_count": page_count,
                        "max_allowed": settings.ocr_max_pdf_pages,
                    }
                )

            title = safe_title_from_filename(request.filename)
            sections: list[Section] = []
            page_ranges: list[list[int]] = []

            for page_index in range(page_count):
                page_number = page_index + 1
                try:
                    page = doc.load_page(page_index)
                    pix = page.get_pixmap(dpi=300)
                    from PIL import Image
                    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                    extracted = await ocr_extract(self._ocr, image=img, lang="eng+chi_sim")
                except Exception as exc:
                    raise GenericDocumentParseError(
                        details={"reason": "scanned_page_ocr_failed", "page": page_number}
                    ) from exc

                page_text = extracted.strip()
                if not page_text:
                    continue

                page_ranges.append([page_number, page_number])
                sections.append(
                    Section(
                        section_id=f"{request.version_id}:ocr-page-{page_number}",
                        tenant_id=request.tenant_id,
                        document_id=request.document_id,
                        version_id=request.version_id,
                        source_type=request.source_type,
                        source_uri=request.source_uri,
                        title=f"Page {page_number}",
                        title_path=[title, f"Page {page_number}"],
                        content=page_text,
                        page_start=page_number,
                        page_end=page_number,
                        acl=request.acl,
                        metadata={
                            "source_uri": request.source_uri,
                            "filename": request.filename,
                            "checksum": request.checksum,
                            "title_path": [title, f"Page {page_number}"],
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
                source_type=request.source_type,
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
        finally:
            doc.close()

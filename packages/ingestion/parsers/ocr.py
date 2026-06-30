from __future__ import annotations

import shutil
from io import BytesIO

from packages.ingestion.domain import (
    ParsedDocument,
    ParseRequest,
    Section,
    safe_title_from_filename,
)
from packages.ingestion.exceptions import EmptyDocumentContentError, GenericDocumentParseError

_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif"}


def _check_tesseract() -> None:
    """Verify Tesseract OCR is installed and discoverable."""
    if shutil.which("tesseract") is None:
        raise GenericDocumentParseError(
            details={
                "reason": "tesseract_not_installed",
                "help": (
                    "Install Tesseract OCR: "
                    "https://github.com/UB-Mannheim/tesseract/wiki "
                    "(Windows) or 'apt install tesseract-ocr' / 'brew install tesseract' "
                    "(Linux/macOS)."
                ),
            }
        )


class ImageOcrParser:
    """OCR parser for common image formats (jpg, png, bmp, tiff).

    Uses Tesseract via pytesseract.  Raises a clear error when the
    Tesseract binary is not found on the system path.
    """

    async def parse(self, request: ParseRequest) -> ParsedDocument:
        _check_tesseract()

        try:
            from PIL import Image

            image = Image.open(BytesIO(request.content))
        except Exception as exc:
            raise GenericDocumentParseError(
                details={"reason": "image_open_failed"}
            ) from exc

        # Convert to RGB if necessary (e.g. RGBA / P images)
        if image.mode not in ("RGB", "L"):
            try:
                image = image.convert("RGB")
            except Exception as exc:
                raise GenericDocumentParseError(
                    details={"reason": "image_convert_failed"}
                ) from exc

        try:
            import pytesseract

            text = pytesseract.image_to_string(image, lang="eng+chi_sim")
        except Exception as exc:
            raise GenericDocumentParseError(
                details={"reason": "ocr_failed"}
            ) from exc

        text = text.strip()
        if not text:
            raise EmptyDocumentContentError()

        title = safe_title_from_filename(request.filename)
        title_path = [title]

        section = Section(
            section_id=f"{request.version_id}:ocr-page-1",
            tenant_id=request.tenant_id,
            document_id=request.document_id,
            version_id=request.version_id,
            source_type=request.source_type,
            source_uri=request.source_uri,
            title=title,
            title_path=title_path,
            content=text,
            page_start=1,
            page_end=1,
            acl=request.acl,
            metadata={
                "source_uri": request.source_uri,
                "filename": request.filename,
                "checksum": request.checksum,
                "title_path": title_path,
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

    Renders each page to an image via PyMuPDF, then runs Tesseract OCR on
    every page.  Raises clear errors when Tesseract or PyMuPDF are missing.
    """

    async def parse(self, request: ParseRequest) -> ParsedDocument:
        _check_tesseract()

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

        page_count = doc.page_count
        if page_count == 0:
            doc.close()
            raise EmptyDocumentContentError()

        import pytesseract

        title = safe_title_from_filename(request.filename)
        sections: list[Section] = []
        page_ranges: list[list[int]] = []

        for page_index in range(page_count):
            page_number = page_index + 1
            try:
                page = doc.load_page(page_index)
                # Render at 300 DPI for good OCR quality
                pix = page.get_pixmap(dpi=300)
                # Convert to PIL Image
                from PIL import Image

                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                extracted = pytesseract.image_to_string(img, lang="eng+chi_sim")
            except Exception as exc:
                doc.close()
                raise GenericDocumentParseError(
                    details={"reason": "scanned_page_ocr_failed", "page": page_number}
                ) from exc

            page_text = extracted.strip()
            if not page_text:
                continue

            title_path = [title, f"Page {page_number}"]
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

        doc.close()

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

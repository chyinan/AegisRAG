"""Tesseract OCR provider (default)."""

from __future__ import annotations

import shutil

from packages.ingestion.exceptions import GenericDocumentParseError


class TesseractOCRProvider:
    """OCR via Tesseract (pytesseract).

    Requires the ``tesseract`` binary on ``PATH``.  Supports any language pack
    installed alongside Tesseract (e.g. ``eng``, ``chi_sim``).
    """

    def __init__(self) -> None:
        self._checked = False

    def _ensure_tesseract(self) -> None:
        if self._checked:
            return
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
        self._checked = True

    def extract_text(
        self,
        *,
        image: object,  # PIL.Image.Image
        lang: str = "eng+chi_sim",
    ) -> str:
        self._ensure_tesseract()
        import pytesseract

        return pytesseract.image_to_string(image, lang=lang)

    def supports_pdf_render(self) -> bool:
        return False  # caller must render PDF pages via PyMuPDF

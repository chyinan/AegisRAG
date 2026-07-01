"""OCR provider package.

Exports:
  - OCRProvider Protocol (`ports`)
  - Provider implementations (Tesseract, PaddleOCR, Surya)
  - Provider-neutral parsers (ImageOcrParser, ScannedPdfOcrParser)
  - Factory function `create_ocr_provider`
"""

from __future__ import annotations

from packages.ingestion.parsers.ocr.paddle import PaddleOCRProvider
from packages.ingestion.parsers.ocr.parsers import (
    ImageOcrParser,
    ScannedPdfOcrParser,
    create_ocr_provider,
)
from packages.ingestion.parsers.ocr.ports import OCRProvider, ocr_extract
from packages.ingestion.parsers.ocr.surya import SuryaOCRProvider
from packages.ingestion.parsers.ocr.tesseract import TesseractOCRProvider

__all__ = [
    "OCRProvider",
    "TesseractOCRProvider",
    "PaddleOCRProvider",
    "SuryaOCRProvider",
    "ocr_extract",
    "ImageOcrParser",
    "ScannedPdfOcrParser",
    "create_ocr_provider",
]

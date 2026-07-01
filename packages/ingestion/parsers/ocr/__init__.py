"""OCR provider package — Protocol + provider-neutral parsers.

Exports:
  - OCRProvider Protocol (ports.py)
  - Provider-neutral parsers (ImageOcrParser, ScannedPdfOcrParser)
  - Factory function ``create_ocr_provider``

Provider implementations (TesseractOCRProvider, PaddleOCRProvider,
SuryaOCRProvider) are loaded lazily via the factory — import them directly
only if you need a specific provider.
"""

from __future__ import annotations

from packages.ingestion.parsers.ocr.parsers import (
    ImageOcrParser,
    ScannedPdfOcrParser,
    create_ocr_provider,
)
from packages.ingestion.parsers.ocr.ports import OCRProvider, ocr_extract

__all__ = [
    "OCRProvider",
    "ocr_extract",
    "ImageOcrParser",
    "ScannedPdfOcrParser",
    "create_ocr_provider",
]

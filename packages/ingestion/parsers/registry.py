from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from packages.ingestion.exceptions import UnsupportedDocumentTypeError
from packages.ingestion.parsers.docx import DocxParser
from packages.ingestion.parsers.markdown import MarkdownParser
from packages.ingestion.parsers.ocr import (
    ImageOcrParser,
    ScannedPdfOcrParser,
)
from packages.ingestion.parsers.pdf import PdfParser
from packages.ingestion.parsers.txt import TxtParser
from packages.ingestion.ports import DocumentParser

if TYPE_CHECKING:
    from packages.common.config import AppSettings


@dataclass(frozen=True)
class ParserRegistry:
    parsers: dict[str, DocumentParser]

    @classmethod
    def default(cls) -> ParserRegistry:
        """Default registry with Tesseract OCR (backward-compatible)."""
        markdown = MarkdownParser()
        txt = TxtParser()
        pdf = PdfParser()
        docx = DocxParser()
        image = ImageOcrParser()
        scanned_pdf = ScannedPdfOcrParser()
        return cls(
            parsers={
                "markdown": markdown,
                "md": markdown,
                "txt": txt,
                "pdf": pdf,
                "docx": docx,
                "image": image,
                "scanned_pdf": scanned_pdf,
            }
        )

    @classmethod
    def from_settings(cls, settings: AppSettings) -> ParserRegistry:
        """Build registry with the configured OCR provider."""
        from apps.api.factories.common import create_ocr_provider

        ocr = create_ocr_provider(settings)
        markdown = MarkdownParser()
        txt = TxtParser()
        pdf = PdfParser()
        docx = DocxParser()
        image = ImageOcrParser(ocr)
        scanned_pdf = ScannedPdfOcrParser(ocr)
        return cls(
            parsers={
                "markdown": markdown,
                "md": markdown,
                "txt": txt,
                "pdf": pdf,
                "docx": docx,
                "image": image,
                "scanned_pdf": scanned_pdf,
            }
        )

    def get(self, source_type: str) -> DocumentParser:
        normalized = source_type.strip().lower()
        parser = self.parsers.get(normalized)
        if parser is None:
            raise UnsupportedDocumentTypeError(source_type=normalized or source_type)
        return parser

from __future__ import annotations

from dataclasses import dataclass

from packages.ingestion.exceptions import UnsupportedDocumentTypeError
from packages.ingestion.parsers.docx import DocxParser
from packages.ingestion.parsers.markdown import MarkdownParser
from packages.ingestion.parsers.pdf import PdfParser
from packages.ingestion.parsers.txt import TxtParser
from packages.ingestion.ports import DocumentParser


@dataclass(frozen=True)
class ParserRegistry:
    parsers: dict[str, DocumentParser]

    @classmethod
    def default(cls) -> ParserRegistry:
        markdown = MarkdownParser()
        txt = TxtParser()
        pdf = PdfParser()
        docx = DocxParser()
        return cls(
            parsers={
                "markdown": markdown,
                "md": markdown,
                "txt": txt,
                "pdf": pdf,
                "docx": docx,
            }
        )

    def get(self, source_type: str) -> DocumentParser:
        normalized = source_type.strip().lower()
        parser = self.parsers.get(normalized)
        if parser is None:
            raise UnsupportedDocumentTypeError(source_type=normalized or source_type)
        return parser

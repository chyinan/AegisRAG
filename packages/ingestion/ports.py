from __future__ import annotations

from typing import Protocol

from packages.ingestion.domain import Chunk, ParsedDocument, ParseRequest


class DocumentParser(Protocol):
    async def parse(self, request: ParseRequest) -> ParsedDocument: ...


class DocumentCleaner(Protocol):
    def clean(self, document: ParsedDocument) -> ParsedDocument: ...


class DocumentDeduplicator(Protocol):
    def deduplicate(self, document: ParsedDocument) -> ParsedDocument: ...


class Chunker(Protocol):
    def split(self, document: ParsedDocument) -> list[Chunk]: ...


class AsyncChunker(Protocol):
    async def split(self, document: ParsedDocument) -> list[Chunk]: ...

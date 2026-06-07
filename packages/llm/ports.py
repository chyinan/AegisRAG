from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from packages.llm.dto import GenerateChunk, GenerateRequest, GenerateResponse


class LLMProvider(Protocol):
    async def generate(self, request: GenerateRequest) -> GenerateResponse: ...

    def stream(self, request: GenerateRequest) -> AsyncIterator[GenerateChunk]: ...

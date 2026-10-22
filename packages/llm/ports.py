from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from packages.llm.decisions import DecisionRequest, DecisionResponse
from packages.llm.dto import GenerateChunk, GenerateRequest, GenerateResponse


class LLMProvider(Protocol):
    async def generate(self, request: GenerateRequest) -> GenerateResponse: ...

    def stream(self, request: GenerateRequest) -> AsyncIterator[GenerateChunk]: ...


@runtime_checkable
class DecisionProvider(Protocol):
    """Produce one declarative decision; never execute tools or own a loop."""

    async def decide(self, request: DecisionRequest) -> DecisionResponse: ...

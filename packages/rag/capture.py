"""Opt-in, in-process evaluation evidence from the *actual* generation context.

These DTOs may contain private document text. They are never part of public query
responses, audit logs, or a second retrieval. Evaluation callers own their use
and must explicitly authorize any external judge or persisted raw-text export.
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict

from packages.rag.dto import QueryResponse


class CapturedContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str
    document_id: str
    version_id: str
    chunk_ids: tuple[str, ...]

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


class CapturedQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    capture_version: str = "packed-context-v1"
    response: QueryResponse
    contexts: tuple[CapturedContext, ...]
    prompt_sha256: str | None

    def ragas_result(self) -> tuple[str, tuple[str, ...]]:
        """Adapter for RagasEvaluator.run_fn; no new retrieval or provider call."""
        return self.response.answer, tuple(item.content for item in self.contexts)

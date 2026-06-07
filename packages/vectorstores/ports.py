from __future__ import annotations

from typing import Protocol

from packages.vectorstores.dto import (
    VectorDeleteResult,
    VectorRecord,
    VectorSearchRequest,
    VectorSearchResult,
    VectorUpsertResult,
)


class VectorStore(Protocol):
    async def upsert(self, vectors: list[VectorRecord]) -> VectorUpsertResult:
        ...

    async def search(self, request: VectorSearchRequest) -> list[VectorSearchResult]:
        ...

    async def delete_by_document(
        self,
        document_id: str,
        version_id: str | None = None,
        *,
        tenant_id: str,
    ) -> VectorDeleteResult:
        ...

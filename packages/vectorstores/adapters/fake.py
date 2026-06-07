from __future__ import annotations

from datetime import UTC, datetime
from math import sqrt

from packages.vectorstores.acl import acl_allows
from packages.vectorstores.dto import (
    MetadataFilter,
    VectorDeleteResult,
    VectorRecord,
    VectorSearchRequest,
    VectorSearchResult,
    VectorUpsertResult,
)
from packages.vectorstores.exceptions import (
    INDEX_DIMENSION_MISMATCH,
    VECTOR_RECORD_SCOPE_MISMATCH,
    VECTOR_STORE_WRITE_FAILED,
    VectorStoreError,
)


class FakeVectorStore:
    def __init__(self, *, index_dim: int) -> None:
        if index_dim <= 0:
            raise ValueError("index_dim must be greater than 0")
        self._index_dim = index_dim
        self._records: dict[tuple[str, str, str, str, str, str | None], VectorRecord] = {}

    async def upsert(self, vectors: list[VectorRecord]) -> VectorUpsertResult:
        if not vectors:
            raise VectorStoreError(
                code=VECTOR_STORE_WRITE_FAILED,
                message="Vector upsert requires at least one record.",
                retryable=False,
            )
        _validate_record_batch(vectors=vectors, index_dim=self._index_dim)
        for record in vectors:
            self._records[_record_key(record)] = record
        first = vectors[0]
        return VectorUpsertResult(
            upserted_count=len(vectors),
            tenant_id=first.tenant_id,
            document_id=first.document_id,
            version_id=first.version_id,
            embedding_provider=first.embedding_provider,
            embedding_model=first.embedding_model,
            embedding_version=first.embedding_version,
            embedding_dim=first.embedding_dim,
        )

    async def search(self, request: VectorSearchRequest) -> list[VectorSearchResult]:
        if request.embedding_dim != self._index_dim:
            raise VectorStoreError(
                code=INDEX_DIMENSION_MISMATCH,
                message="Query vector dimension does not match vector index dimension.",
                retryable=False,
                details={"expected_dim": self._index_dim, "actual_dim": request.embedding_dim},
            )
        scored: list[tuple[float, VectorRecord]] = []
        for record in self._records.values():
            if not _record_matches_request(record=record, request=request):
                continue
            score = _score(
                query_vector=request.query_vector,
                record_vector=record.vector,
                metric=request.distance_metric,
            )
            if request.score_threshold is not None and score < request.score_threshold:
                continue
            scored.append((score, record))
        scored.sort(key=lambda item: (-item[0], item[1].chunk_id))
        return [
            _search_result(record=record, score=score)
            for score, record in scored[: request.top_k]
        ]

    async def delete_by_document(
        self,
        document_id: str,
        version_id: str | None = None,
        *,
        tenant_id: str,
    ) -> VectorDeleteResult:
        deleted_count = 0
        now = datetime.now(tz=UTC)
        for key, record in list(self._records.items()):
            if record.tenant_id != tenant_id or record.document_id != document_id:
                continue
            if version_id is not None and record.version_id != version_id:
                continue
            if record.deleted_at is not None or record.status == "deleted":
                continue
            self._records[key] = record.model_copy(update={"status": "deleted", "deleted_at": now})
            deleted_count += 1
        return VectorDeleteResult(
            deleted_count=deleted_count,
            tenant_id=tenant_id,
            document_id=document_id,
            version_id=version_id,
        )


def _validate_record_batch(*, vectors: list[VectorRecord], index_dim: int) -> None:
    first = vectors[0]
    expected_scope = (first.tenant_id, first.document_id, first.version_id)
    expected_embedding = (
        first.embedding_provider,
        first.embedding_model,
        first.embedding_version,
        first.embedding_dim,
    )
    for record in vectors:
        if record.embedding_dim != index_dim or len(record.vector) != index_dim:
            raise VectorStoreError(
                code=INDEX_DIMENSION_MISMATCH,
                message="Vector dimension does not match vector index dimension.",
                retryable=False,
                details={
                    "tenant_id": record.tenant_id,
                    "document_id": record.document_id,
                    "version_id": record.version_id,
                    "chunk_id": record.chunk_id,
                    "expected_dim": index_dim,
                    "actual_dim": len(record.vector),
                },
            )
        if (record.tenant_id, record.document_id, record.version_id) != expected_scope:
            raise VectorStoreError(
                code=VECTOR_RECORD_SCOPE_MISMATCH,
                message="Vector records must share tenant, document, and version scope.",
                retryable=False,
                details={
                    "tenant_id": first.tenant_id,
                    "document_id": first.document_id,
                    "version_id": first.version_id,
                    "chunk_id": record.chunk_id,
                },
            )
        if (
            record.embedding_provider,
            record.embedding_model,
            record.embedding_version,
            record.embedding_dim,
        ) != expected_embedding:
            raise VectorStoreError(
                code=VECTOR_RECORD_SCOPE_MISMATCH,
                message="Vector records must share embedding provider, model, version, and dim.",
                retryable=False,
                details={"chunk_id": record.chunk_id},
            )


def _record_matches_request(*, record: VectorRecord, request: VectorSearchRequest) -> bool:
    if record.tenant_id != request.tenant_id:
        return False
    if not request.include_deleted and (record.deleted_at is not None or record.status != "active"):
        return False
    if record.embedding_dim != request.embedding_dim:
        return False
    if (
        request.embedding_provider is not None
        and record.embedding_provider != request.embedding_provider
    ):
        return False
    if request.embedding_model is not None and record.embedding_model != request.embedding_model:
        return False
    if (
        request.embedding_version is not None
        and record.embedding_version != request.embedding_version
    ):
        return False
    if not _metadata_allowed(record.metadata, request.metadata_filters):
        return False
    return acl_allows(record.acl, request.acl_filter)


def _metadata_allowed(metadata: dict[str, object], filters: list[MetadataFilter]) -> bool:
    return all(metadata.get(filter_.key) == filter_.value for filter_ in filters)


def _score(*, query_vector: list[float], record_vector: list[float], metric: str) -> float:
    if metric == "l2":
        distance = sqrt(
            sum(
                (left - right) ** 2
                for left, right in zip(query_vector, record_vector, strict=True)
            )
        )
        return 1.0 / (1.0 + distance)
    dot = sum(left * right for left, right in zip(query_vector, record_vector, strict=True))
    query_norm = sqrt(sum(value * value for value in query_vector))
    record_norm = sqrt(sum(value * value for value in record_vector))
    if query_norm == 0 or record_norm == 0:
        return 0.0
    return dot / (query_norm * record_norm)


def _record_key(record: VectorRecord) -> tuple[str, str, str, str, str, str | None]:
    return (
        record.tenant_id,
        record.document_id,
        record.version_id,
        record.chunk_id,
        record.embedding_model,
        record.embedding_version,
    )


def _search_result(*, record: VectorRecord, score: float) -> VectorSearchResult:
    return VectorSearchResult(
        document_id=record.document_id,
        version_id=record.version_id,
        chunk_id=record.chunk_id,
        source=record.source_uri,
        source_type=record.source_type,
        source_uri=record.source_uri,
        page_start=record.page_start,
        page_end=record.page_end,
        title_path=record.title_path,
        score=score,
        retrieval_method="dense",
        tenant_id=record.tenant_id,
        acl=record.acl,
        metadata=record.metadata,
    )

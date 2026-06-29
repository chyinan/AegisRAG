"""Milvus vector store adapter — enterprise-scale ANN search backend.

Implements the VectorStore protocol so the rest of the system is
completely agnostic to which backend powers the vector index.
Switching between pgvector and Milvus is a one-line config change.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from pymilvus import (
    Collection,
    DataType,
    MilvusClient,
    connections,
)

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
    VECTOR_STORE_DELETE_FAILED,
    VECTOR_STORE_SEARCH_FAILED,
    VECTOR_STORE_WRITE_FAILED,
    VectorStoreError,
)

_MILVUS_COLLECTION = "vector_records"
# Fields beyond the embedding (used for filtering and metadata).
_SCALAR_FIELDS: list[dict[str, Any]] = [
    {"name": "id", "dtype": DataType.INT64, "is_primary": True, "auto_id": True},
    {"name": "tenant_id", "dtype": DataType.VARCHAR, "max_length": 128},
    {"name": "document_id", "dtype": DataType.VARCHAR, "max_length": 256},
    {"name": "version_id", "dtype": DataType.VARCHAR, "max_length": 256},
    {"name": "chunk_id", "dtype": DataType.VARCHAR, "max_length": 512},
    {"name": "source_type", "dtype": DataType.VARCHAR, "max_length": 64},
    {"name": "source_uri", "dtype": DataType.VARCHAR, "max_length": 2048},
    {"name": "title_path", "dtype": DataType.ARRAY, "element_type": DataType.VARCHAR, "max_length": 256, "max_capacity": 32},
    {"name": "page_start", "dtype": DataType.INT64},
    {"name": "page_end", "dtype": DataType.INT64},
    {"name": "acl_json", "dtype": DataType.VARCHAR, "max_length": 4096},
    {"name": "metadata_json", "dtype": DataType.VARCHAR, "max_length": 8192},
    {"name": "status", "dtype": DataType.VARCHAR, "max_length": 32},
    {"name": "deleted_at", "dtype": DataType.INT64},  # epoch millis, 0 = active
    {"name": "embedding_provider", "dtype": DataType.VARCHAR, "max_length": 128},
    {"name": "embedding_model", "dtype": DataType.VARCHAR, "max_length": 256},
    {"name": "embedding_version", "dtype": DataType.VARCHAR, "max_length": 64},
    {"name": "embedding_dim", "dtype": DataType.INT32},
    {"name": "token_count", "dtype": DataType.INT32},
    {"name": "checksum", "dtype": DataType.VARCHAR, "max_length": 128},
]


class MilvusVectorStore:
    """VectorStore backed by Milvus — sub-millisecond ANN at billion scale."""

    def __init__(
        self,
        *,
        uri: str = "http://localhost:19530",
        token: str = "",
        index_dim: int,
        index_type: str = "HNSW",
        metric_type: str = "COSINE",
    ) -> None:
        if index_dim <= 0:
            raise ValueError("index_dim must be greater than 0")
        self._uri = uri
        self._token = token
        self._index_dim = index_dim
        self._index_type = index_type
        self._metric_type = metric_type
        self._client: MilvusClient | None = None
        self._collection: Collection | None = None

    # ------------------------------------------------------------------
    # VectorStore protocol
    # ------------------------------------------------------------------

    async def upsert(self, vectors: list[VectorRecord]) -> VectorUpsertResult:
        if not vectors:
            raise VectorStoreError(
                code=VECTOR_STORE_WRITE_FAILED,
                message="Vector upsert requires at least one record.",
                retryable=False,
            )
        _validate_record_batch(vectors=vectors, index_dim=self._index_dim)
        data = [_record_to_milvus_row(r) for r in vectors]
        try:
            self._ensure_collection()
            self._client.insert(collection_name=_MILVUS_COLLECTION, data=data)
        except Exception as exc:
            raise VectorStoreError(
                code=VECTOR_STORE_WRITE_FAILED,
                details={
                    "tenant_id": vectors[0].tenant_id,
                    "document_id": vectors[0].document_id,
                },
            ) from exc
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
        filter_expr = _build_filter_expr(request)
        self._ensure_collection()
        try:
            results = self._client.search(
                collection_name=_MILVUS_COLLECTION,
                data=[request.query_vector],
                anns_field="embedding",
                limit=request.top_k,
                filter=filter_expr,
                output_fields=[
                    "document_id", "version_id", "chunk_id", "source_type",
                    "source_uri", "page_start", "page_end", "title_path",
                    "tenant_id", "acl_json", "metadata_json",
                ],
                search_params={"metric_type": self._metric_type, "params": {"ef": max(64, request.top_k * 2)}},
            )
        except Exception as exc:
            raise VectorStoreError(
                code=VECTOR_STORE_SEARCH_FAILED,
                details={"tenant_id": request.tenant_id},
            ) from exc

        scored: list[tuple[float, dict[str, Any]]] = []
        for hits in results:
            for hit in hits:
                entity = hit.get("entity", {})
                score = hit.get("distance", 0.0)
                if request.score_threshold is not None and score < request.score_threshold:
                    continue
                # ACL enforcement (Milvus cannot evaluate JSON ACL natively,
                # so we post-filter here — same semantics as pgvector fallback).
                acl_raw = entity.get("acl_json", "{}")
                acl = json.loads(acl_raw) if isinstance(acl_raw, str) else acl_raw
                if not acl_allows(acl, request.acl_filter):
                    continue
                scored.append((score, entity))
        scored.sort(key=lambda item: (-item[0], str(item[1].get("chunk_id", ""))))
        return [
            _hit_to_result(entity=entity, score=score)
            for score, entity in scored[: request.top_k]
        ]

    async def delete_by_document(
        self,
        document_id: str,
        version_id: str | None = None,
        *,
        tenant_id: str,
    ) -> VectorDeleteResult:
        self._ensure_collection()
        expr = f'tenant_id == "{tenant_id}" && document_id == "{document_id}" && status == "active"'
        if version_id is not None:
            expr += f' && version_id == "{version_id}"'
        try:
            # Query first to count.
            results = self._client.query(
                collection_name=_MILVUS_COLLECTION,
                filter=expr,
                output_fields=["id"],
            )
            deleted_count = len(results)
            if deleted_count > 0:
                ids = [r["id"] for r in results]
                # Milvus soft-delete via upsert with status="deleted".
                now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
                self._client.upsert(
                    collection_name=_MILVUS_COLLECTION,
                    data=[{"id": id_, "status": "deleted", "deleted_at": now_ms} for id_ in ids],
                )
        except Exception as exc:
            raise VectorStoreError(
                code=VECTOR_STORE_DELETE_FAILED,
                details={"tenant_id": tenant_id, "document_id": document_id},
            ) from exc
        return VectorDeleteResult(
            deleted_count=deleted_count,
            tenant_id=tenant_id,
            document_id=document_id,
            version_id=version_id,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_collection(self) -> None:
        """Lazily connect and create the collection + index if they don't exist yet."""
        if self._client is not None:
            return
        # Milvus Lite (embedded) vs Milvus standalone.
        if self._uri.startswith("http"):
            connections.connect("default", uri=self._uri, token=self._token or "")
        else:
            connections.connect("default", uri=self._uri)
        self._client = MilvusClient(uri=self._uri, token=self._token or "")

        if self._client.has_collection(_MILVUS_COLLECTION):
            return

        # Create schema.
        schema = self._client.create_schema(
            auto_id=True,
            enable_dynamic_field=False,
        )
        for field in _SCALAR_FIELDS:
            schema.add_field(**field)
        schema.add_field(
            field_name="embedding",
            datatype=DataType.FLOAT_VECTOR,
            dim=self._index_dim,
        )

        index_params = self._client.prepare_index_params()
        index_params.add_index(
            field_name="embedding",
            index_type=self._index_type,
            metric_type=self._metric_type,
            params={"M": "16", "efConstruction": "64"},
        )

        self._client.create_collection(
            collection_name=_MILVUS_COLLECTION,
            schema=schema,
            index_params=index_params,
        )

        # Load into memory.
        self._client.load_collection(_MILVUS_COLLECTION)


# ------------------------------------------------------------------
# Batch validation (reused from pgvector module contracts)
# ------------------------------------------------------------------

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
                    "expected_dim": index_dim,
                    "actual_dim": len(record.vector),
                },
            )
        if (record.tenant_id, record.document_id, record.version_id) != expected_scope:
            raise VectorStoreError(
                code=VECTOR_RECORD_SCOPE_MISMATCH,
                retryable=False,
                details={"chunk_id": record.chunk_id},
            )
        if (
            record.embedding_provider,
            record.embedding_model,
            record.embedding_version,
            record.embedding_dim,
        ) != expected_embedding:
            raise VectorStoreError(
                code=VECTOR_RECORD_SCOPE_MISMATCH,
                retryable=False,
                details={"chunk_id": record.chunk_id},
            )


# ------------------------------------------------------------------
# Serialisation helpers
# ------------------------------------------------------------------

def _record_to_milvus_row(record: VectorRecord) -> dict[str, Any]:
    return {
        "tenant_id": record.tenant_id,
        "document_id": record.document_id,
        "version_id": record.version_id,
        "chunk_id": record.chunk_id,
        "source_type": record.source_type,
        "source_uri": record.source_uri or "",
        "title_path": record.title_path,
        "page_start": record.page_start or 0,
        "page_end": record.page_end or 0,
        "acl_json": json.dumps(record.acl, ensure_ascii=False),
        "metadata_json": json.dumps(record.metadata, ensure_ascii=False),
        "status": record.status,
        "deleted_at": int(record.deleted_at.timestamp() * 1000) if record.deleted_at else 0,
        "embedding_provider": record.embedding_provider,
        "embedding_model": record.embedding_model,
        "embedding_version": record.embedding_version or "",
        "embedding_dim": record.embedding_dim,
        "token_count": record.token_count,
        "checksum": record.checksum,
        "embedding": record.vector,
    }


def _build_filter_expr(request: VectorSearchRequest) -> str:
    parts: list[str] = [
        f'tenant_id == "{request.tenant_id}"',
        f"embedding_dim == {request.embedding_dim}",
    ]
    if not request.include_deleted:
        parts.append('status == "active"')
    if request.embedding_provider:
        parts.append(f'embedding_provider == "{request.embedding_provider}"')
    if request.embedding_model:
        parts.append(f'embedding_model == "{request.embedding_model}"')
    if request.embedding_version:
        parts.append(f'embedding_version == "{request.embedding_version}"')
    for f in request.metadata_filters:
        parts.append(f'metadata_json like "%{_escape(f.key)}%{_escape(str(f.value))}%"')
    return " && ".join(parts)


def _escape(value: str) -> str:
    return value.replace('"', '\\"')


def _hit_to_result(*, entity: dict[str, Any], score: float) -> VectorSearchResult:
    return VectorSearchResult(
        document_id=str(entity.get("document_id", "")),
        version_id=str(entity.get("version_id", "")),
        chunk_id=str(entity.get("chunk_id", "")),
        source=entity.get("source_uri") or None,
        source_type=str(entity.get("source_type", "")),
        source_uri=entity.get("source_uri") or None,
        page_start=entity.get("page_start") or None,
        page_end=entity.get("page_end") or None,
        title_path=list(entity.get("title_path") or []),
        score=score,
        retrieval_method="dense",
        tenant_id=str(entity.get("tenant_id", "")),
        acl=json.loads(entity.get("acl_json", "{}")),
        metadata=json.loads(entity.get("metadata_json", "{}")),
    )

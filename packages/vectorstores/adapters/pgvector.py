from __future__ import annotations

import json
from datetime import UTC, datetime
from math import sqrt

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from packages.data.storage.models import VectorRecordModel
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


class PgVectorStore:
    def __init__(self, session: AsyncSession, *, index_dim: int) -> None:
        if index_dim <= 0:
            raise ValueError("index_dim must be greater than 0")
        self._session = session
        self._index_dim = index_dim

    async def upsert(self, vectors: list[VectorRecord]) -> VectorUpsertResult:
        if not vectors:
            raise VectorStoreError(
                code=VECTOR_STORE_WRITE_FAILED,
                message="Vector upsert requires at least one record.",
                retryable=False,
            )
        _validate_record_batch(vectors=vectors, index_dim=self._index_dim)
        try:
            for record in vectors:
                model = await self._find_existing(record)
                if model is None:
                    self._session.add(_model_from_record(record))
                    continue
                _update_model_from_record(model=model, record=record)
            await self._session.flush()
        except SQLAlchemyError as exc:
            await self._session.rollback()
            first = vectors[0]
            raise VectorStoreError(
                code=VECTOR_STORE_WRITE_FAILED,
                details={
                    "tenant_id": first.tenant_id,
                    "document_id": first.document_id,
                    "version_id": first.version_id,
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
        if _is_postgresql_session(self._session):
            return await self._search_postgresql(request)

        statement = select(VectorRecordModel).where(
            VectorRecordModel.tenant_id == request.tenant_id,
            VectorRecordModel.embedding_dim == request.embedding_dim,
        )
        if not request.include_deleted:
            statement = statement.where(
                VectorRecordModel.status == "active",
                VectorRecordModel.deleted_at.is_(None),
            )
        if request.embedding_provider is not None:
            statement = statement.where(
                VectorRecordModel.embedding_provider == request.embedding_provider
            )
        if request.embedding_model is not None:
            statement = statement.where(
                VectorRecordModel.embedding_model == request.embedding_model
            )
        if request.embedding_version is not None:
            statement = statement.where(
                VectorRecordModel.embedding_version == request.embedding_version
            )
        try:
            records = [record_from_model(model) for model in await self._session.scalars(statement)]
        except SQLAlchemyError as exc:
            raise VectorStoreError(
                code=VECTOR_STORE_SEARCH_FAILED,
                details={"tenant_id": request.tenant_id},
            ) from exc
        scored: list[tuple[float, VectorRecord]] = []
        for record in records:
            if not _metadata_allowed(record.metadata, request.metadata_filters):
                continue
            if not acl_allows(record.acl, request.acl_filter):
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

    async def _search_postgresql(
        self,
        request: VectorSearchRequest,
    ) -> list[VectorSearchResult]:
        statement, params = _postgres_search_query(request)
        try:
            rows = (await self._session.execute(statement, params)).mappings().all()
        except SQLAlchemyError as exc:
            raise VectorStoreError(
                code=VECTOR_STORE_SEARCH_FAILED,
                details={"tenant_id": request.tenant_id},
            ) from exc
        return [
            VectorSearchResult(
                document_id=str(row["document_id"]),
                version_id=str(row["version_id"]),
                chunk_id=str(row["chunk_id"]),
                source=row["source_uri"],
                source_type=str(row["source_type"]),
                source_uri=row["source_uri"],
                page_start=row["page_start"],
                page_end=row["page_end"],
                title_path=list(row["title_path"] or []),
                score=float(row["score"]),
                retrieval_method="dense",
                tenant_id=str(row["tenant_id"]),
                acl=dict(row["acl"] or {}),
                metadata=dict(row["metadata"] or {}),
            )
            for row in rows
        ]

    async def delete_by_document(
        self,
        document_id: str,
        version_id: str | None = None,
        *,
        tenant_id: str,
    ) -> VectorDeleteResult:
        statement = select(VectorRecordModel).where(
            VectorRecordModel.tenant_id == tenant_id,
            VectorRecordModel.document_id == document_id,
            VectorRecordModel.status == "active",
            VectorRecordModel.deleted_at.is_(None),
        )
        if version_id is not None:
            statement = statement.where(VectorRecordModel.version_id == version_id)
        now = datetime.now(tz=UTC)
        try:
            models = list(await self._session.scalars(statement))
            for model in models:
                model.status = "deleted"
                model.deleted_at = now
            await self._session.flush()
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise VectorStoreError(
                code=VECTOR_STORE_DELETE_FAILED,
                details={"tenant_id": tenant_id, "document_id": document_id},
            ) from exc
        return VectorDeleteResult(
            deleted_count=len(models),
            tenant_id=tenant_id,
            document_id=document_id,
            version_id=version_id,
        )

    async def _find_existing(self, record: VectorRecord) -> VectorRecordModel | None:
        model = await self._session.scalar(
            select(VectorRecordModel).where(
                VectorRecordModel.tenant_id == record.tenant_id,
                VectorRecordModel.document_id == record.document_id,
                VectorRecordModel.version_id == record.version_id,
                VectorRecordModel.chunk_id == record.chunk_id,
                VectorRecordModel.embedding_model == record.embedding_model,
                VectorRecordModel.embedding_version == record.embedding_version,
            )
        )
        return model


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


def _model_from_record(record: VectorRecord) -> VectorRecordModel:
    return VectorRecordModel(
        tenant_id=record.tenant_id,
        created_by=record.created_by,
        status=record.status,
        document_id=record.document_id,
        version_id=record.version_id,
        chunk_id=record.chunk_id,
        source_type=record.source_type,
        source_uri=record.source_uri,
        title_path=record.title_path,
        page_start=record.page_start,
        page_end=record.page_end,
        token_count=record.token_count,
        acl=record.acl,
        checksum=record.checksum,
        embedding_provider=record.embedding_provider,
        embedding_model=record.embedding_model,
        embedding_version=record.embedding_version,
        embedding_dim=record.embedding_dim,
        embedding=record.vector,
        metadata_=record.metadata,
        deleted_at=record.deleted_at,
    )


def _update_model_from_record(*, model: VectorRecordModel, record: VectorRecord) -> None:
    model.created_by = record.created_by
    model.status = record.status
    model.source_type = record.source_type
    model.source_uri = record.source_uri
    model.title_path = record.title_path
    model.page_start = record.page_start
    model.page_end = record.page_end
    model.token_count = record.token_count
    model.acl = record.acl
    model.checksum = record.checksum
    model.embedding_provider = record.embedding_provider
    model.embedding_dim = record.embedding_dim
    model.embedding = record.vector
    model.metadata_ = record.metadata
    model.deleted_at = record.deleted_at


def record_from_model(model: VectorRecordModel) -> VectorRecord:
    return VectorRecord(
        id=model.id,
        tenant_id=model.tenant_id,
        document_id=model.document_id,
        version_id=model.version_id,
        chunk_id=model.chunk_id,
        created_by=model.created_by,
        status=model.status,
        vector=list(model.embedding or []),
        embedding_provider=model.embedding_provider,
        embedding_model=model.embedding_model,
        embedding_version=model.embedding_version,
        embedding_dim=model.embedding_dim,
        source_type=model.source_type,
        source_uri=model.source_uri,
        title_path=list(model.title_path or []),
        page_start=model.page_start,
        page_end=model.page_end,
        token_count=model.token_count,
        acl=dict(model.acl or {}),
        checksum=model.checksum,
        metadata=dict(model.metadata_ or {}),
        deleted_at=model.deleted_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


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


def _is_postgresql_session(session: AsyncSession) -> bool:
    return session.get_bind().dialect.name == "postgresql"


def _postgres_search_query(
    request: VectorSearchRequest,
) -> tuple[TextClause, dict[str, object]]:
    distance_operator = "<->" if request.distance_metric == "l2" else "<=>"
    distance_sql = f"embedding {distance_operator} CAST(:query_vector AS vector)"
    score_sql = (
        f"(1.0 / (1.0 + ({distance_sql})))"
        if request.distance_metric == "l2"
        else f"(1.0 - ({distance_sql}))"
    )
    where_clauses = [
        "tenant_id = :tenant_id",
        "embedding_dim = :embedding_dim",
    ]
    params: dict[str, object] = {
        "tenant_id": request.tenant_id,
        "embedding_dim": request.embedding_dim,
        "query_vector": _pgvector_literal(request.query_vector),
        "top_k": request.top_k,
        "denied_user_acl": json.dumps({"denied_users": [request.acl_filter.user_id]}),
    }
    if not request.include_deleted:
        where_clauses.extend(["status = 'active'", "deleted_at IS NULL"])
    if request.embedding_provider is not None:
        where_clauses.append("embedding_provider = :embedding_provider")
        params["embedding_provider"] = request.embedding_provider
    if request.embedding_model is not None:
        where_clauses.append("embedding_model = :embedding_model")
        params["embedding_model"] = request.embedding_model
    if request.embedding_version is not None:
        where_clauses.append("embedding_version = :embedding_version")
        params["embedding_version"] = request.embedding_version
    for index, filter_ in enumerate(request.metadata_filters):
        key = f"metadata_filter_{index}"
        where_clauses.append(f'"metadata"::jsonb @> CAST(:{key} AS jsonb)')
        params[key] = json.dumps({filter_.key: filter_.value})

    where_clauses.append("NOT (acl::jsonb @> CAST(:denied_user_acl AS jsonb))")
    acl_allow_clauses = [
        "LOWER(COALESCE(acl ->> 'visibility', 'tenant')) IN ('public', 'tenant')",
    ]
    _append_acl_json_contains(
        clauses=acl_allow_clauses,
        params=params,
        key="allowed_users",
        values=[request.acl_filter.user_id],
    )
    _append_acl_json_contains(
        clauses=acl_allow_clauses,
        params=params,
        key="allowed_roles",
        values=request.acl_filter.roles,
    )
    _append_acl_json_contains(
        clauses=acl_allow_clauses,
        params=params,
        key="allowed_departments",
        values=[request.acl_filter.department] if request.acl_filter.department else [],
    )
    _append_acl_json_contains(
        clauses=acl_allow_clauses,
        params=params,
        key="allowed_permissions",
        values=request.acl_filter.permissions,
    )
    where_clauses.append("(" + " OR ".join(acl_allow_clauses) + ")")

    if request.score_threshold is not None:
        where_clauses.append(f"{score_sql} >= :score_threshold")
        params["score_threshold"] = request.score_threshold

    sql = f"""
        SELECT
            document_id,
            version_id,
            chunk_id,
            source_type,
            source_uri,
            page_start,
            page_end,
            title_path,
            tenant_id,
            acl,
            "metadata" AS metadata,
            {score_sql} AS score
        FROM vector_records
        WHERE {" AND ".join(where_clauses)}
        ORDER BY {distance_sql} ASC, chunk_id ASC
        LIMIT :top_k
    """
    return text(sql), params


def _append_acl_json_contains(
    *,
    clauses: list[str],
    params: dict[str, object],
    key: str,
    values: list[str],
) -> None:
    for index, value in enumerate(values):
        param_key = f"acl_{key}_{index}"
        clauses.append(f"acl::jsonb @> CAST(:{param_key} AS jsonb)")
        params[param_key] = json.dumps({key: [value]})


def _pgvector_literal(vector: list[float]) -> str:
    return "[" + ",".join(str(float(item)) for item in vector) + "]"


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

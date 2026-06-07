from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from math import isfinite
from typing import Protocol, cast

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import TextClause

from packages.common.logging import REDACTED_VALUE, redact_sensitive_data
from packages.data.storage.models import ChunkModel
from packages.retrieval.dto import RetrievalCandidate, RetrievalFilterSet, RetrievalRequest
from packages.retrieval.exceptions import (
    RETRIEVAL_SPARSE_QUERY_INVALID,
    RETRIEVAL_SPARSE_SEARCH_FAILED,
    RetrievalError,
)
from packages.retrieval.filters import to_sparse_filter_payload, to_vector_acl_filter
from packages.vectorstores.acl import acl_allows

_QUERY_TOKEN_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:-]*|[\u4e00-\u9fff]+")
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_SENSITIVE_METADATA_KEYS = {
    "chunk_content",
    "chunk_text",
    "content",
    "document_content",
    "query",
    "raw_response",
    "sql",
    "text",
    "tsquery",
    "tsvector",
}


class SparseRetrieverConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    language_config: str = "simple"
    timeout_seconds: float = 2.0
    min_score: float = 0.0
    max_query_terms: int = 32
    max_query_term_length: int = 128

    @field_validator("language_config")
    @classmethod
    def _language_config_required(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("language_config must not be blank")
        return normalized

    @field_validator("timeout_seconds")
    @classmethod
    def _timeout_positive(cls, value: float) -> float:
        if not isfinite(value) or value <= 0:
            raise ValueError("timeout_seconds must be finite and greater than 0")
        return value

    @field_validator("min_score")
    @classmethod
    def _min_score_in_range(cls, value: float) -> float:
        if not isfinite(value) or value < 0.0 or value > 1.0:
            raise ValueError("min_score must be between 0 and 1")
        return value

    @field_validator("max_query_terms")
    @classmethod
    def _max_query_terms_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("max_query_terms must be greater than 0")
        return value

    @field_validator("max_query_term_length")
    @classmethod
    def _max_query_term_length_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("max_query_term_length must be greater than 0")
        return value


class SparseChunkRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    document_id: str
    version_id: str
    chunk_id: str
    status: str
    content: str = ""
    source_type: str
    source_uri: str | None = None
    title_path: Sequence[str]
    page_start: int | None = None
    page_end: int | None = None
    acl: Mapping[str, object]
    metadata: Mapping[str, object]
    deleted_at: datetime | None = None
    rank: float


class SparseSearchBackend(Protocol):
    backend_kind: str

    async def search(
        self,
        *,
        request: RetrievalRequest,
        filters_payload: Mapping[str, object],
        query_terms: tuple[str, ...],
        config: SparseRetrieverConfig,
    ) -> list[SparseChunkRecord]:
        ...


class PostgresSparseRetriever:
    def __init__(
        self,
        *,
        config: SparseRetrieverConfig,
        backend: SparseSearchBackend | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        if backend is None and session is None:
            raise ValueError("backend or session is required")
        if backend is not None and session is not None:
            raise ValueError("provide either backend or session, not both")
        self._config = config
        if backend is None:
            if session is None:
                raise ValueError("session is required when backend is not provided")
            self._backend: SparseSearchBackend = SqlAlchemySparseSearchBackend(session=session)
        else:
            self._backend = backend

    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> list[RetrievalCandidate]:
        try:
            query_terms = _parse_query_terms(request=request, config=self._config)
        except ValueError as exc:
            raise RetrievalError(
                code=RETRIEVAL_SPARSE_QUERY_INVALID,
                message="Sparse retrieval query is invalid.",
                details=_safe_details(
                    request=request,
                    filters=filters,
                    config=self._config,
                    backend_kind=self._backend.backend_kind,
                    error_code=RETRIEVAL_SPARSE_QUERY_INVALID,
                ),
                status_code=400,
            ) from exc

        if not query_terms:
            return []

        try:
            records = await asyncio.wait_for(
                self._backend.search(
                    request=request,
                    filters_payload=to_sparse_filter_payload(filters),
                    query_terms=query_terms,
                    config=self._config,
                ),
                timeout=self._config.timeout_seconds,
            )
        except TimeoutError as exc:
            raise RetrievalError(
                code=RETRIEVAL_SPARSE_SEARCH_FAILED,
                message="Sparse retrieval backend failed.",
                details=_safe_details(
                    request=request,
                    filters=filters,
                    config=self._config,
                    backend_kind=self._backend.backend_kind,
                    error_code=RETRIEVAL_SPARSE_SEARCH_FAILED,
                ),
                status_code=502,
            ) from exc
        except RetrievalError:
            raise
        except Exception as exc:
            raise RetrievalError(
                code=RETRIEVAL_SPARSE_SEARCH_FAILED,
                message="Sparse retrieval backend failed.",
                details=_safe_details(
                    request=request,
                    filters=filters,
                    config=self._config,
                    backend_kind=self._backend.backend_kind,
                    error_code=RETRIEVAL_SPARSE_SEARCH_FAILED,
                ),
                status_code=502,
            ) from exc

        filtered = _filter_records(
            records=records,
            request=request,
            filters=filters,
            query_terms=query_terms,
            min_score=self._config.min_score,
        )
        filtered.sort(key=lambda record: (-record.rank, record.chunk_id))
        try:
            return [_candidate_from_record(record) for record in filtered[: request.top_k]]
        except ValidationError as exc:
            raise RetrievalError(
                code=RETRIEVAL_SPARSE_SEARCH_FAILED,
                message="Sparse retrieval backend failed.",
                details=_safe_details(
                    request=request,
                    filters=filters,
                    config=self._config,
                    backend_kind=self._backend.backend_kind,
                    error_code=RETRIEVAL_SPARSE_SEARCH_FAILED,
                ),
                status_code=502,
            ) from exc

    def build_postgres_statement(
        self,
        *,
        request: RetrievalRequest,
        filters: RetrievalFilterSet,
    ) -> tuple[TextClause, dict[str, object]]:
        query_terms = _parse_query_terms(request=request, config=self._config)
        return _postgres_search_statement(
            request=request,
            filters=filters,
            query_terms=query_terms,
            config=self._config,
        )


class SqlAlchemySparseSearchBackend:
    backend_kind = "postgres"

    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session

    async def search(
        self,
        *,
        request: RetrievalRequest,
        filters_payload: Mapping[str, object],
        query_terms: tuple[str, ...],
        config: SparseRetrieverConfig,
    ) -> list[SparseChunkRecord]:
        if self._session.get_bind().dialect.name == "postgresql":
            statement, params = _postgres_search_statement(
                request=request,
                filters=_filters_from_payload(filters_payload),
                query_terms=query_terms,
                config=config,
            )
            try:
                rows = (await self._session.execute(statement, params)).mappings().all()
            except SQLAlchemyError as exc:
                raise RetrievalError(
                    code=RETRIEVAL_SPARSE_SEARCH_FAILED,
                    message="Sparse retrieval backend failed.",
                    details={
                        "tenant_id": filters_payload["tenant_id"],
                        "retrieval_method": "sparse",
                        "backend_kind": self.backend_kind,
                        "error_code": RETRIEVAL_SPARSE_SEARCH_FAILED,
                    },
                    status_code=502,
                ) from exc
            try:
                return [_record_from_row(cast("Mapping[str, object]", row)) for row in rows]
            except ValidationError as exc:
                raise RetrievalError(
                    code=RETRIEVAL_SPARSE_SEARCH_FAILED,
                    message="Sparse retrieval backend failed.",
                    details={
                        "tenant_id": filters_payload["tenant_id"],
                        "retrieval_method": "sparse",
                        "backend_kind": self.backend_kind,
                        "error_code": RETRIEVAL_SPARSE_SEARCH_FAILED,
                    },
                    status_code=502,
                ) from exc

        return await self._search_python_fallback(
            request=request,
            filters_payload=filters_payload,
            query_terms=query_terms,
            config=config,
        )

    async def _search_python_fallback(
        self,
        *,
        request: RetrievalRequest,
        filters_payload: Mapping[str, object],
        query_terms: tuple[str, ...],
        config: SparseRetrieverConfig,
    ) -> list[SparseChunkRecord]:
        filters = _filters_from_payload(filters_payload)
        statement = select(ChunkModel).where(
            ChunkModel.tenant_id == str(filters_payload["tenant_id"]),
            ChunkModel.status == "active",
            ChunkModel.deleted_at.is_(None),
        )
        try:
            scope_rows = (
                await self._session.execute(
                    select(
                        ChunkModel.id,
                        ChunkModel.acl,
                        ChunkModel.metadata_.label("metadata"),
                    ).where(
                        ChunkModel.tenant_id == str(filters_payload["tenant_id"]),
                        ChunkModel.status == "active",
                        ChunkModel.deleted_at.is_(None),
                    )
                )
            ).all()
        except SQLAlchemyError as exc:
            raise RetrievalError(
                code=RETRIEVAL_SPARSE_SEARCH_FAILED,
                message="Sparse retrieval backend failed.",
                details={
                    "tenant_id": filters_payload["tenant_id"],
                    "retrieval_method": "sparse",
                    "backend_kind": self.backend_kind,
                    "error_code": RETRIEVAL_SPARSE_SEARCH_FAILED,
                },
                status_code=502,
            ) from exc

        acl_filter = to_vector_acl_filter(filters)
        allowed_ids: list[str] = []
        for row in scope_rows:
            row_mapping = row._mapping
            metadata = _dict_or_empty(row_mapping.get("metadata"))
            acl = _dict_or_empty(row_mapping.get("acl"))
            if not _metadata_matches(metadata, filters.metadata_filter):
                continue
            if not acl_allows(acl, acl_filter):
                continue
            row_id = row_mapping.get("id")
            if isinstance(row_id, str):
                allowed_ids.append(row_id)

        if not allowed_ids:
            return []

        try:
            models = list(
                await self._session.scalars(statement.where(ChunkModel.id.in_(allowed_ids)))
            )
        except SQLAlchemyError as exc:
            raise RetrievalError(
                code=RETRIEVAL_SPARSE_SEARCH_FAILED,
                message="Sparse retrieval backend failed.",
                details={
                    "tenant_id": filters_payload["tenant_id"],
                    "retrieval_method": "sparse",
                    "backend_kind": self.backend_kind,
                    "error_code": RETRIEVAL_SPARSE_SEARCH_FAILED,
                },
                status_code=502,
            ) from exc

        records = [_record_from_model(model=model, query_terms=query_terms) for model in models]
        filtered = _filter_records(
            records=records,
            request=request,
            filters=filters,
            query_terms=query_terms,
            min_score=config.min_score,
        )
        filtered.sort(key=lambda record: (-record.rank, record.chunk_id))
        return filtered[: request.top_k]


def parse_sparse_query_terms(
    query: str,
    *,
    max_terms: int,
    max_term_length: int = 128,
) -> tuple[str, ...]:
    if max_terms <= 0:
        raise ValueError("max_terms must be greater than 0")
    if max_term_length <= 0:
        raise ValueError("max_term_length must be greater than 0")
    terms: list[str] = []
    seen: set[str] = set()
    for match in _QUERY_TOKEN_RE.finditer(query.lower()):
        term = match.group(0).strip(" .:-")[:max_term_length].strip(" .:-")
        if not term or term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= max_terms:
            break
    return tuple(terms)


def _parse_query_terms(
    *,
    request: RetrievalRequest,
    config: SparseRetrieverConfig,
) -> tuple[str, ...]:
    return parse_sparse_query_terms(
        request.query,
        max_terms=config.max_query_terms,
        max_term_length=config.max_query_term_length,
    )


def _filter_records(
    *,
    records: Sequence[SparseChunkRecord],
    request: RetrievalRequest,
    filters: RetrievalFilterSet,
    query_terms: tuple[str, ...],
    min_score: float,
) -> list[SparseChunkRecord]:
    acl_filter = to_vector_acl_filter(filters)
    threshold = max(min_score, request.score_threshold or 0.0)
    filtered: list[SparseChunkRecord] = []
    for record in records:
        if record.tenant_id != filters.tenant_id:
            continue
        if record.status != "active" or record.deleted_at is not None:
            continue
        if not isfinite(record.rank) or record.rank < threshold:
            continue
        if not _metadata_matches(record.metadata, filters.metadata_filter):
            continue
        if not acl_allows(record.acl, acl_filter):
            continue
        if not _content_matches_terms(content=record.content, query_terms=query_terms):
            continue
        filtered.append(record)
    return filtered


def _metadata_matches(
    candidate_metadata: Mapping[str, object],
    required_metadata: Mapping[str, object],
) -> bool:
    return all(candidate_metadata.get(key) == value for key, value in required_metadata.items())


def _content_matches_terms(*, content: str, query_terms: tuple[str, ...]) -> bool:
    if not content:
        return True
    normalized = content.lower()
    return any(term in normalized for term in query_terms)


def _candidate_from_record(record: SparseChunkRecord) -> RetrievalCandidate:
    return RetrievalCandidate(
        document_id=record.document_id,
        version_id=record.version_id,
        chunk_id=record.chunk_id,
        source=_safe_optional_text(record.source_uri),
        source_type=record.source_type,
        source_uri=_safe_optional_text(record.source_uri),
        page_start=record.page_start,
        page_end=record.page_end,
        title_path=tuple(record.title_path),
        score=record.rank,
        retrieval_method="sparse",
        tenant_id=record.tenant_id,
        acl=_safe_mapping(record.acl),
        metadata=_safe_mapping(record.metadata),
    )


def _safe_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    if _looks_like_local_absolute_path(value):
        return REDACTED_VALUE
    redacted = redact_sensitive_data(value)
    if isinstance(redacted, str):
        return redacted
    return REDACTED_VALUE


def _safe_mapping(value: Mapping[str, object]) -> dict[str, object]:
    return {
        str(key): _safe_metadata_value(key=str(key), value=item)
        for key, item in value.items()
    }


def _safe_metadata_value(*, key: str, value: object) -> object:
    normalized_key = key.strip().lower()
    if normalized_key in _SENSITIVE_METADATA_KEYS:
        return REDACTED_VALUE
    if isinstance(value, Mapping):
        return _safe_mapping(value)
    if isinstance(value, list | tuple):
        return [
            _safe_metadata_value(key=normalized_key, value=item)
            for item in value
        ]
    if isinstance(value, str) and _looks_like_local_absolute_path(value):
        return REDACTED_VALUE
    redacted = redact_sensitive_data({key: value})
    if isinstance(redacted, Mapping):
        return redacted.get(key, REDACTED_VALUE)
    return REDACTED_VALUE


def _looks_like_local_absolute_path(value: str) -> bool:
    normalized = value.strip()
    return (
        normalized.startswith("/")
        or normalized.startswith("\\\\")
        or _WINDOWS_ABSOLUTE_PATH.match(normalized) is not None
    )


def _safe_details(
    *,
    request: RetrievalRequest,
    filters: RetrievalFilterSet,
    config: SparseRetrieverConfig,
    backend_kind: str,
    error_code: str,
) -> dict[str, object]:
    return {
        "request_id": request.request_id,
        "trace_id": request.trace_id,
        "tenant_id": filters.tenant_id,
        "user_id": filters.user_id,
        "top_k": request.top_k,
        "retrieval_method": "sparse",
        "backend_kind": backend_kind,
        "language_config": config.language_config,
        "error_code": error_code,
    }


def _postgres_search_statement(
    *,
    request: RetrievalRequest,
    filters: RetrievalFilterSet,
    query_terms: tuple[str, ...],
    config: SparseRetrieverConfig,
) -> tuple[TextClause, dict[str, object]]:
    rank_sql = """
        ts_rank_cd(
            to_tsvector(:language_config, COALESCE(content, '')),
            websearch_to_tsquery(:language_config, :query)
        )
    """
    where_clauses = [
        "tenant_id = :tenant_id",
        "status = 'active'",
        "deleted_at IS NULL",
        (
            "to_tsvector(:language_config, COALESCE(content, '')) "
            "@@ websearch_to_tsquery(:language_config, :query)"
        ),
    ]
    params: dict[str, object] = {
        "language_config": config.language_config,
        "query": " ".join(query_terms),
        "tenant_id": filters.tenant_id,
        "top_k": request.top_k,
        "denied_user": filters.user_id,
    }
    for index, (key, value) in enumerate(sorted(filters.metadata_filter.items())):
        param_key = f"metadata_filter_{index}"
        where_clauses.append(f'"metadata"::jsonb @> CAST(:{param_key} AS jsonb)')
        params[param_key] = json.dumps({key: value})

    where_clauses.append(
        "NOT ("
        "(jsonb_typeof(acl::jsonb -> 'denied_users') = 'array' "
        "AND (acl::jsonb -> 'denied_users') ? :denied_user) "
        "OR acl ->> 'denied_users' = :denied_user"
        ")"
    )
    acl_allow_clauses = [
        "LOWER(COALESCE(acl ->> 'visibility', 'tenant')) IN ('public', 'tenant')",
    ]
    _append_acl_json_contains(
        clauses=acl_allow_clauses,
        params=params,
        key="allowed_users",
        values=[filters.user_id],
    )
    _append_acl_json_contains(
        clauses=acl_allow_clauses,
        params=params,
        key="allowed_roles",
        values=list(filters.roles),
    )
    _append_acl_json_contains(
        clauses=acl_allow_clauses,
        params=params,
        key="allowed_departments",
        values=[filters.department] if filters.department else [],
    )
    _append_acl_json_contains(
        clauses=acl_allow_clauses,
        params=params,
        key="allowed_permissions",
        values=list(filters.permissions),
    )
    where_clauses.append("(" + " OR ".join(acl_allow_clauses) + ")")

    threshold = max(config.min_score, request.score_threshold or 0.0)
    if threshold > 0.0:
        where_clauses.append(f"({rank_sql}) >= :score_threshold")
        params["score_threshold"] = threshold

    sql = f"""
        SELECT
            tenant_id,
            document_id,
            version_id,
            chunk_id,
            status,
            source_type,
            source_uri,
            title_path,
            page_start,
            page_end,
            acl,
            "metadata" AS metadata,
            deleted_at,
            {rank_sql} AS rank
        FROM chunks
        WHERE {" AND ".join(where_clauses)}
        ORDER BY rank DESC, chunk_id ASC
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
        clauses.append(
            f"("
            f"(jsonb_typeof(acl::jsonb -> '{key}') = 'array' "
            f"AND (acl::jsonb -> '{key}') ? :{param_key}) "
            f"OR acl ->> '{key}' = :{param_key}"
            f")"
        )
        params[param_key] = value


def _filters_from_payload(payload: Mapping[str, object]) -> RetrievalFilterSet:
    metadata_filter = payload.get("metadata_filter", {})
    acl_filter = payload.get("acl_filter", {})
    return RetrievalFilterSet(
        tenant_id=str(payload["tenant_id"]),
        user_id=str(payload["user_id"]),
        roles=_text_tuple(payload.get("roles", ())),
        department=(
            str(payload["department"])
            if payload.get("department") is not None
            else None
        ),
        permissions=_text_tuple(payload.get("permissions", ())),
        metadata_filter=metadata_filter if isinstance(metadata_filter, Mapping) else {},
        acl_filter=acl_filter if isinstance(acl_filter, Mapping) else {},
        include_deleted=False,
    )


def _record_from_row(row: Mapping[str, object]) -> SparseChunkRecord:
    return SparseChunkRecord(
        tenant_id=str(row["tenant_id"]),
        document_id=str(row["document_id"]),
        version_id=str(row["version_id"]),
        chunk_id=str(row["chunk_id"]),
        status=str(row["status"]),
        content="",
        source_type=str(row["source_type"]),
        source_uri=row["source_uri"] if isinstance(row["source_uri"], str) else None,
        title_path=_text_list(row.get("title_path")),
        page_start=row["page_start"] if isinstance(row["page_start"], int) else None,
        page_end=row["page_end"] if isinstance(row["page_end"], int) else None,
        acl=_dict_or_empty(row.get("acl")),
        metadata=_dict_or_empty(row.get("metadata")),
        deleted_at=row["deleted_at"] if isinstance(row["deleted_at"], datetime) else None,
        rank=_float_or_zero(row.get("rank")),
    )


def _text_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, Sequence):
        return tuple(str(item) for item in value)
    return ()


def _text_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, Sequence):
        return [str(item) for item in value]
    return []


def _dict_or_empty(value: object) -> dict[str, object]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def _float_or_zero(value: object) -> float:
    if isinstance(value, int | float | str):
        return float(value)
    return 0.0


def _record_from_model(*, model: ChunkModel, query_terms: tuple[str, ...]) -> SparseChunkRecord:
    content = model.content or ""
    normalized = content.lower()
    matched_count = sum(1 for term in query_terms if term in normalized)
    rank = matched_count / len(query_terms) if query_terms else 0.0
    return SparseChunkRecord(
        tenant_id=model.tenant_id,
        document_id=model.document_id,
        version_id=model.version_id,
        chunk_id=model.chunk_id,
        status=model.status,
        content=content,
        source_type=model.source_type,
        source_uri=model.source_uri,
        title_path=list(model.title_path or []),
        page_start=model.page_start,
        page_end=model.page_end,
        acl=dict(model.acl or {}),
        metadata=dict(model.metadata_ or {}),
        deleted_at=model.deleted_at,
        rank=rank,
    )

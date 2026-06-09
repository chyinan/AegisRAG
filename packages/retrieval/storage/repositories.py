from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, cast

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.common.logging import redact_mapping
from packages.data.storage.base import generate_uuid
from packages.data.storage.exceptions import StorageError
from packages.retrieval.dto import RetrievalLogCreate, RetrievalLogRecord
from packages.retrieval.storage.models import RetrievalLogModel

_SENSITIVE_RETRIEVAL_KEYS = {
    "chunk_content",
    "chunk_ids",
    "chunk_text",
    "content",
    "candidate_ids",
    "document_content",
    "embedding",
    "embedding_vector",
    "full_query",
    "local_path",
    "object_key",
    "prompt",
    "provider_payload",
    "provider_raw_response",
    "query",
    "query_text",
    "raw_exception",
    "query_vector",
    "raw_response",
    "secret",
    "source_uri",
    "sql",
    "text",
    "token",
    "tsquery",
    "tsvector",
    "vector",
}


class RetrievalLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, record: RetrievalLogCreate) -> RetrievalLogRecord:
        model = build_retrieval_log_model(record)
        self._session.add(model)
        try:
            await self._session.flush()
            await self._session.refresh(model)
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="RETRIEVAL_LOG_STORAGE_WRITE_FAILED",
                message="Retrieval log storage write failed.",
                details=_safe_error_details(record=record, error_code="storage_write_failed"),
            ) from exc
        return retrieval_log_record_from_model(model)

    async def get_by_request_id(
        self,
        *,
        tenant_id: str,
        request_id: str,
    ) -> RetrievalLogRecord | None:
        records = await self.list_by_request_id(tenant_id=tenant_id, request_id=request_id)
        return records[0] if records else None

    async def list_by_request_id(
        self,
        *,
        tenant_id: str,
        request_id: str,
    ) -> list[RetrievalLogRecord]:
        statement = (
            select(RetrievalLogModel)
            .where(
                RetrievalLogModel.tenant_id == tenant_id,
                RetrievalLogModel.request_id == request_id,
            )
            .order_by(RetrievalLogModel.created_at)
        )
        try:
            models = list(await self._session.scalars(statement))
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="RETRIEVAL_LOG_STORAGE_READ_FAILED",
                message="Retrieval log storage read failed.",
                details={"tenant_id": tenant_id, "request_id": request_id},
            ) from exc
        return [retrieval_log_record_from_model(model) for model in models]

    async def list_by_trace_id(
        self,
        *,
        tenant_id: str,
        trace_id: str,
        limit: int = 100,
    ) -> list[RetrievalLogRecord]:
        bounded_limit = min(max(limit, 1), 500)
        statement = (
            select(RetrievalLogModel)
            .where(
                RetrievalLogModel.tenant_id == tenant_id,
                RetrievalLogModel.trace_id == trace_id,
            )
            .order_by(RetrievalLogModel.created_at)
            .limit(bounded_limit)
        )
        try:
            models = list(await self._session.scalars(statement))
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="RETRIEVAL_LOG_STORAGE_READ_FAILED",
                message="Retrieval log storage read failed.",
                details={"tenant_id": tenant_id, "trace_id": trace_id},
            ) from exc
        return [retrieval_log_record_from_model(model) for model in models]

    async def list_by_created_at(
        self,
        *,
        tenant_id: str,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int = 100,
    ) -> list[RetrievalLogRecord]:
        bounded_limit = min(max(limit, 1), 500)
        statement = select(RetrievalLogModel).where(RetrievalLogModel.tenant_id == tenant_id)
        if created_from is not None:
            statement = statement.where(RetrievalLogModel.created_at >= created_from)
        if created_to is not None:
            statement = statement.where(RetrievalLogModel.created_at <= created_to)
        statement = statement.order_by(RetrievalLogModel.created_at).limit(bounded_limit)
        try:
            models = list(await self._session.scalars(statement))
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="RETRIEVAL_LOG_STORAGE_READ_FAILED",
                message="Retrieval log storage read failed.",
                details={"tenant_id": tenant_id},
            ) from exc
        return [retrieval_log_record_from_model(model) for model in models]

    async def commit(self) -> None:
        try:
            await self._session.commit()
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="RETRIEVAL_LOG_STORAGE_COMMIT_FAILED",
                message="Retrieval log storage commit failed.",
            ) from exc

    async def rollback(self) -> None:
        await self._session.rollback()


def build_retrieval_log_model(
    record: RetrievalLogCreate,
    *,
    log_id: str | None = None,
) -> RetrievalLogModel:
    created_at = record.created_at or datetime.now(tz=UTC)
    return RetrievalLogModel(
        id=log_id or generate_uuid(),
        created_at=created_at,
        updated_at=created_at,
        request_id=record.request_id,
        trace_id=record.trace_id,
        tenant_id=record.tenant_id,
        user_id=record.user_id,
        created_by=record.created_by,
        status=record.status,
        latency_ms=record.latency_ms,
        top_k=record.top_k,
        result_count=record.result_count,
        rerank_score=record.rerank_score,
        error_code=record.error_code,
        query_summary=redact_mapping(record.query_summary),
        metadata_=_redact_retrieval_mapping(record.metadata),
    )


def retrieval_log_record_from_model(model: RetrievalLogModel) -> RetrievalLogRecord:
    return RetrievalLogRecord(
        id=model.id,
        created_at=model.created_at,
        updated_at=model.updated_at,
        request_id=model.request_id,
        trace_id=model.trace_id,
        tenant_id=model.tenant_id,
        user_id=model.user_id,
        created_by=model.created_by,
        status=_log_status_from_model(model.status),
        latency_ms=model.latency_ms,
        top_k=model.top_k,
        result_count=model.result_count,
        rerank_score=model.rerank_score,
        error_code=model.error_code,
        query_summary=_query_summary_from_model(model.query_summary or {}),
        metadata=dict(model.metadata_ or {}),
    )


def _safe_error_details(
    *,
    record: RetrievalLogCreate,
    error_code: str,
) -> dict[str, object]:
    return {
        "request_id": record.request_id,
        "trace_id": record.trace_id,
        "tenant_id": record.tenant_id,
        "user_id": record.user_id,
        "error_code": error_code,
    }


def _query_summary_from_model(value: dict[str, object]) -> dict[str, int]:
    return {
        str(key): item
        for key, item in value.items()
        if isinstance(item, int) and not isinstance(item, bool)
    }


def _log_status_from_model(value: str) -> Literal["success", "failure"]:
    if value in {"success", "failure"}:
        return cast(Literal["success", "failure"], value)
    raise StorageError(
        code="RETRIEVAL_LOG_STORAGE_READ_FAILED",
        message="Retrieval log storage returned an invalid status.",
        details={"status": "[REDACTED]"},
    )


def _redact_retrieval_mapping(value: dict[str, object]) -> dict[str, object]:
    redacted = redact_mapping(value)
    return {
        str(key): _redact_retrieval_value(str(key), item)
        for key, item in redacted.items()
    }


def _redact_retrieval_value(key: str, value: object) -> object:
    if _is_retrieval_sensitive_key(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {
            str(child_key): _redact_retrieval_value(str(child_key), child_value)
            for child_key, child_value in value.items()
        }
    if isinstance(value, list):
        return [_redact_retrieval_value(key, item) for item in value]
    return value


def _is_retrieval_sensitive_key(key: str) -> bool:
    normalized = key.strip().lower().replace("-", "_").replace(" ", "_")
    compact = "".join(char for char in normalized if char.isalnum())
    compact_sensitive = {
        "".join(char for char in item if char.isalnum())
        for item in _SENSITIVE_RETRIEVAL_KEYS
    }
    return normalized in _SENSITIVE_RETRIEVAL_KEYS or compact in compact_sensitive

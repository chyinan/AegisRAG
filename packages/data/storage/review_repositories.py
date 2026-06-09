from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.data.storage.exceptions import StorageError
from packages.data.storage.review_models import ReviewItemModel
from packages.review.dto import ReviewItemCreateRequest, ReviewItemQueryRequest, ReviewStatus


class ReviewItemRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    created_by: str
    status: ReviewStatus
    item_type: str
    severity: str
    request_id: str
    trace_id: str
    source_view: str
    safe_identifiers: dict[str, object] = Field(default_factory=dict)
    safe_summary: dict[str, object] = Field(default_factory=dict)
    eval_candidate: dict[str, object] | None = None
    status_history: list[dict[str, object]] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class ReviewItemRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_item(
        self,
        *,
        tenant_id: str,
        created_by: str,
        request: ReviewItemCreateRequest,
        status_history: list[dict[str, object]],
    ) -> ReviewItemRecord:
        model = ReviewItemModel(
            tenant_id=tenant_id,
            created_by=created_by,
            status="open",
            item_type=request.item_type,
            severity=request.severity,
            request_id=request.request_id,
            trace_id=request.trace_id,
            source_view=request.source_view,
            safe_identifiers=dict(request.safe_identifiers),
            safe_summary=dict(request.safe_summary),
            status_history=list(status_history),
        )
        self._session.add(model)
        try:
            await self._session.flush()
            await self._session.refresh(model)
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="REVIEW_QUEUE_STORAGE_WRITE_FAILED",
                message="Review item storage write failed.",
                details={"tenant_id": tenant_id, "request_id": request.request_id},
            ) from exc
        return review_item_record_from_model(model)

    async def list_items(
        self,
        *,
        tenant_id: str,
        query: ReviewItemQueryRequest,
    ) -> list[ReviewItemRecord]:
        statement = select(ReviewItemModel).where(ReviewItemModel.tenant_id == tenant_id)
        if query.item_type is not None:
            statement = statement.where(ReviewItemModel.item_type == query.item_type)
        if query.severity is not None:
            statement = statement.where(ReviewItemModel.severity == query.severity)
        if query.status is not None:
            statement = statement.where(ReviewItemModel.status == query.status)
        if query.request_id is not None:
            statement = statement.where(ReviewItemModel.request_id == query.request_id)
        if query.trace_id is not None:
            statement = statement.where(ReviewItemModel.trace_id == query.trace_id)
        if query.source_view is not None:
            statement = statement.where(ReviewItemModel.source_view == query.source_view)
        if query.created_at_from is not None:
            statement = statement.where(ReviewItemModel.created_at >= query.created_at_from)
        if query.created_at_to is not None:
            statement = statement.where(ReviewItemModel.created_at <= query.created_at_to)
        statement = statement.order_by(
            ReviewItemModel.created_at.desc(),
            ReviewItemModel.id.desc(),
        ).limit(query.limit)
        try:
            models = list(await self._session.scalars(statement))
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="REVIEW_QUEUE_STORAGE_READ_FAILED",
                message="Review item storage read failed.",
                details=_safe_query_details(tenant_id=tenant_id, query=query),
            ) from exc
        return [review_item_record_from_model(model) for model in models]

    async def get_item(
        self,
        *,
        tenant_id: str,
        item_id: str,
    ) -> ReviewItemRecord | None:
        try:
            model = await self._session.scalar(
                select(ReviewItemModel).where(
                    ReviewItemModel.tenant_id == tenant_id,
                    ReviewItemModel.id == item_id,
                )
            )
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="REVIEW_QUEUE_STORAGE_READ_FAILED",
                message="Review item storage read failed.",
                details={"tenant_id": tenant_id, "review_item_id": item_id},
            ) from exc
        if model is None:
            return None
        return review_item_record_from_model(model)

    async def update_status(
        self,
        *,
        tenant_id: str,
        item_id: str,
        status: ReviewStatus,
        status_history: list[dict[str, object]],
        eval_candidate: dict[str, object] | None = None,
    ) -> ReviewItemRecord | None:
        try:
            model = await self._session.scalar(
                select(ReviewItemModel).where(
                    ReviewItemModel.tenant_id == tenant_id,
                    ReviewItemModel.id == item_id,
                )
            )
            if model is None:
                return None
            model.status = status
            model.status_history = list(status_history)
            if eval_candidate is not None:
                model.eval_candidate = dict(eval_candidate)
            await self._session.flush()
            await self._session.refresh(model)
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="REVIEW_QUEUE_STORAGE_WRITE_FAILED",
                message="Review item storage write failed.",
                details={"tenant_id": tenant_id, "review_item_id": item_id},
            ) from exc
        return review_item_record_from_model(model)


def review_item_record_from_model(model: ReviewItemModel) -> ReviewItemRecord:
    return ReviewItemRecord(
        id=model.id,
        tenant_id=model.tenant_id,
        created_by=model.created_by,
        status=_status(model.status),
        item_type=model.item_type,
        severity=model.severity,
        request_id=model.request_id,
        trace_id=model.trace_id,
        source_view=model.source_view,
        safe_identifiers=dict(model.safe_identifiers or {}),
        safe_summary=dict(model.safe_summary or {}),
        eval_candidate=dict(model.eval_candidate) if model.eval_candidate else None,
        status_history=list(model.status_history or []),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _status(value: str) -> ReviewStatus:
    return value  # type: ignore[return-value]


def _safe_query_details(
    *,
    tenant_id: str,
    query: ReviewItemQueryRequest,
) -> dict[str, object]:
    details: dict[str, object] = {"tenant_id": tenant_id, "limit": query.limit}
    for field in ("item_type", "severity", "status", "request_id", "trace_id", "source_view"):
        value = getattr(query, field)
        if value is not None:
            details[field] = value
    return details

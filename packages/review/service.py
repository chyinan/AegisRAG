from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from packages.auth.policies import (
    has_eval_candidate_write_permission,
    has_review_queue_read_permission,
    has_review_queue_write_permission,
)
from packages.common.audit import AuditEvent, AuditPort, AuditResource, AuditStatus
from packages.common.context import AuthenticatedRequestContext
from packages.data.storage.exceptions import StorageError
from packages.review.dto import (
    SAFE_REVIEW_IDENTIFIER_FIELDS,
    SAFE_REVIEW_SUMMARY_FIELDS,
    EvalCandidatePreview,
    ReviewItemCreateRequest,
    ReviewItemQueryRequest,
    ReviewItemStatusHistoryEntry,
    ReviewItemStatusUpdateRequest,
    ReviewItemSummary,
    ReviewQueueListResponse,
    ReviewStatus,
    safe_mapping,
)
from packages.review.exceptions import (
    REVIEW_QUEUE_EVAL_CANDIDATE_FAILED,
    REVIEW_QUEUE_FORBIDDEN,
    REVIEW_QUEUE_INVALID_STATUS_TRANSITION,
    REVIEW_QUEUE_NOT_FOUND,
    REVIEW_QUEUE_STORAGE_READ_FAILED,
    REVIEW_QUEUE_STORAGE_WRITE_FAILED,
    ReviewQueueError,
)

_NEXT_STEPS = (
    ".venv\\Scripts\\python.exe -m pytest tests/unit/review_queue -q",
    ".venv\\Scripts\\python.exe -m pytest "
    "tests/integration/api/test_review_queue_routes.py "
    "tests/integration/storage/test_review_queue_repositories.py -q",
    "node tests/unit/web/sidecar_behavior_runner.js",
)
_ALLOWED_TRANSITIONS: dict[ReviewStatus, tuple[ReviewStatus, ...]] = {
    "open": ("accepted", "rejected", "needs_followup"),
    "needs_followup": ("accepted", "rejected", "converted_to_eval_case"),
    "accepted": ("converted_to_eval_case",),
    "rejected": (),
    "converted_to_eval_case": (),
}
_METRIC_COUNT_KEYS = (
    "citation_count",
    "unsupported_count",
    "forged_reference_count",
    "prompt_risk_count",
    "retrieval_result_count",
    "context_item_count",
    "tool_call_count",
    "latency_ms",
)


class ReviewItemRecordProtocol(Protocol):
    id: str
    tenant_id: str
    created_by: str
    status: ReviewStatus
    item_type: str
    severity: str
    request_id: str
    trace_id: str
    source_view: str
    safe_identifiers: dict[str, object]
    safe_summary: dict[str, object]
    eval_candidate: dict[str, object] | None
    status_history: list[dict[str, object]]
    created_at: datetime
    updated_at: datetime


class ReviewQueueRepository(Protocol):
    async def create_item(
        self,
        *,
        tenant_id: str,
        created_by: str,
        request: ReviewItemCreateRequest,
        status_history: list[dict[str, object]],
    ) -> ReviewItemRecordProtocol:
        ...

    async def list_items(
        self,
        *,
        tenant_id: str,
        query: ReviewItemQueryRequest,
    ) -> Sequence[ReviewItemRecordProtocol]:
        ...

    async def get_item(
        self,
        *,
        tenant_id: str,
        item_id: str,
    ) -> ReviewItemRecordProtocol | None:
        ...

    async def update_status(
        self,
        *,
        tenant_id: str,
        item_id: str,
        status: ReviewStatus,
        status_history: list[dict[str, object]],
        eval_candidate: dict[str, object] | None = None,
    ) -> ReviewItemRecordProtocol | None:
        ...


class ReviewQueueService:
    def __init__(
        self,
        *,
        repository: ReviewQueueRepository,
        audit: AuditPort | None = None,
        now: Callable[[], datetime] | None = None,
        item_id_factory: Callable[[], str] | None = None,
        candidate_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repository = repository
        self._audit = audit
        self._now = now or (lambda: datetime.now(tz=UTC))
        self._item_id_factory = item_id_factory or (lambda: str(uuid4()))
        self._candidate_id_factory = candidate_id_factory or (lambda: f"eval-candidate-{uuid4()}")

    async def create_item(
        self,
        *,
        context: AuthenticatedRequestContext,
        request: ReviewItemCreateRequest,
    ) -> ReviewItemSummary:
        started = time.perf_counter()
        _assert_write(context)
        history = [
            _history_entry(
                status="open",
                changed_by=context.auth.user_id,
                changed_at=self._now(),
                reason_code="created",
            )
        ]
        try:
            record = await self._repository.create_item(
                tenant_id=context.auth.tenant_id,
                created_by=context.auth.user_id,
                request=request,
                status_history=history,
            )
        except StorageError as exc:
            raise ReviewQueueError(
                code=REVIEW_QUEUE_STORAGE_WRITE_FAILED,
                message="Review queue storage write failed.",
                details=_safe_error_details(context=context, stage="storage"),
                status_code=503,
            ) from exc
        await self._record_audit(
            context=context,
            action="review_queue.create_item",
            record=record,
            started=started,
            old_status=None,
            new_status="open",
        )
        return _summary(record)

    async def list_items(
        self,
        *,
        context: AuthenticatedRequestContext,
        query: ReviewItemQueryRequest,
    ) -> ReviewQueueListResponse:
        _assert_read(context)
        try:
            records = await self._repository.list_items(
                tenant_id=context.auth.tenant_id,
                query=query,
            )
        except StorageError as exc:
            raise ReviewQueueError(
                code=REVIEW_QUEUE_STORAGE_READ_FAILED,
                message="Review queue storage read failed.",
                details=_safe_error_details(context=context, stage="storage"),
                status_code=503,
            ) from exc
        return ReviewQueueListResponse(
            items=tuple(_summary(record) for record in records),
            next_steps=_NEXT_STEPS,
        )

    async def get_item(
        self,
        *,
        context: AuthenticatedRequestContext,
        item_id: str,
    ) -> ReviewItemSummary:
        _assert_read(context)
        record = await self._get_record(context=context, item_id=item_id)
        return _summary(record)

    async def update_status(
        self,
        *,
        context: AuthenticatedRequestContext,
        item_id: str,
        request: ReviewItemStatusUpdateRequest,
    ) -> ReviewItemSummary:
        started = time.perf_counter()
        _assert_write(context)
        record = await self._get_record(context=context, item_id=item_id)
        _assert_transition(record.status, request.status, context=context, item_id=item_id)
        old_status = record.status
        history = [
            *record.status_history,
            _history_entry(
                status=request.status,
                changed_by=context.auth.user_id,
                changed_at=self._now(),
                reason_code=request.reason_code,
            ),
        ]
        try:
            updated = await self._repository.update_status(
                tenant_id=context.auth.tenant_id,
                item_id=item_id,
                status=request.status,
                status_history=history,
            )
        except StorageError as exc:
            raise ReviewQueueError(
                code=REVIEW_QUEUE_STORAGE_WRITE_FAILED,
                message="Review queue storage write failed.",
                details=_safe_error_details(context=context, stage="storage", item_id=item_id),
                status_code=503,
            ) from exc
        if updated is None:
            raise _not_found(context=context, item_id=item_id)
        await self._record_audit(
            context=context,
            action="review_queue.update_status",
            record=updated,
            started=started,
            old_status=old_status,
            new_status=request.status,
        )
        return _summary(updated)

    async def convert_to_eval_candidate(
        self,
        *,
        context: AuthenticatedRequestContext,
        item_id: str,
    ) -> EvalCandidatePreview:
        started = time.perf_counter()
        _assert_write(context)
        _assert_eval_write(context)
        record = await self._get_record(context=context, item_id=item_id)
        _assert_transition(
            record.status,
            "converted_to_eval_case",
            context=context,
            item_id=item_id,
        )
        old_status = record.status
        candidate = _candidate_from_record(record, candidate_id=self._candidate_id_factory())
        history = [
            *record.status_history,
            _history_entry(
                status="converted_to_eval_case",
                changed_by=context.auth.user_id,
                changed_at=self._now(),
                reason_code="eval_candidate_preview",
            ),
        ]
        try:
            updated = await self._repository.update_status(
                tenant_id=context.auth.tenant_id,
                item_id=item_id,
                status="converted_to_eval_case",
                status_history=history,
                eval_candidate=candidate.model_dump(mode="json"),
            )
        except StorageError as exc:
            raise ReviewQueueError(
                code=REVIEW_QUEUE_EVAL_CANDIDATE_FAILED,
                message="Review queue eval candidate preview failed.",
                details=_safe_error_details(
                    context=context,
                    stage="eval_candidate",
                    item_id=item_id,
                ),
                status_code=503,
            ) from exc
        if updated is None:
            raise _not_found(context=context, item_id=item_id)
        await self._record_audit(
            context=context,
            action="review_queue.convert_to_eval_candidate",
            record=updated,
            started=started,
            old_status=old_status,
            new_status="converted_to_eval_case",
            candidate_id=candidate.candidate_id,
            requires_human_confirmation=True,
        )
        return candidate

    async def _get_record(
        self,
        *,
        context: AuthenticatedRequestContext,
        item_id: str,
    ) -> ReviewItemRecordProtocol:
        try:
            record = await self._repository.get_item(
                tenant_id=context.auth.tenant_id,
                item_id=item_id,
            )
        except StorageError as exc:
            raise ReviewQueueError(
                code=REVIEW_QUEUE_STORAGE_READ_FAILED,
                message="Review queue storage read failed.",
                details=_safe_error_details(context=context, stage="storage", item_id=item_id),
                status_code=503,
            ) from exc
        if record is None:
            raise _not_found(context=context, item_id=item_id)
        return record

    async def _record_audit(
        self,
        *,
        context: AuthenticatedRequestContext,
        action: str,
        record: ReviewItemRecordProtocol,
        started: float,
        old_status: str | None,
        new_status: str,
        candidate_id: str | None = None,
        requires_human_confirmation: bool | None = None,
    ) -> None:
        if self._audit is None:
            return
        metadata: dict[str, object] = {
            "review_item_id": record.id,
            "item_type": record.item_type,
            "severity": record.severity,
            "old_status": old_status,
            "new_status": new_status,
            "source_view": record.source_view,
            "safe_identifier_count": len(record.safe_identifiers),
            "request_id": record.request_id,
            "trace_id": record.trace_id,
        }
        if candidate_id is not None:
            metadata["candidate_id"] = candidate_id
        if requires_human_confirmation is not None:
            metadata["requires_human_confirmation"] = requires_human_confirmation
        await self._audit.record(
            AuditEvent(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                action=action,
                resource=AuditResource(type="review_item", id=record.id, metadata=metadata),
                status=AuditStatus.SUCCESS,
                latency_ms=max((time.perf_counter() - started) * 1000, 0.0),
                metadata=metadata,
                created_at=self._now(),
            )
        )


def _summary(record: ReviewItemRecordProtocol) -> ReviewItemSummary:
    return ReviewItemSummary(
        id=record.id,
        item_type=record.item_type,  # type: ignore[arg-type]
        severity=record.severity,  # type: ignore[arg-type]
        status=record.status,
        request_id=record.request_id,
        trace_id=record.trace_id,
        source_view=record.source_view,  # type: ignore[arg-type]
        safe_identifiers=safe_mapping(record.safe_identifiers, SAFE_REVIEW_IDENTIFIER_FIELDS),
        safe_summary=safe_mapping(record.safe_summary, SAFE_REVIEW_SUMMARY_FIELDS),
        status_history=tuple(_history_from_mapping(item) for item in record.status_history),
        allowed_transitions=_ALLOWED_TRANSITIONS[record.status],
        eval_candidate=(
            EvalCandidatePreview.model_validate(record.eval_candidate)
            if record.eval_candidate
            else None
        ),
        created_by=record.created_by,
        tenant_id=record.tenant_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _history_from_mapping(item: dict[str, object]) -> ReviewItemStatusHistoryEntry:
    return ReviewItemStatusHistoryEntry.model_validate(item)


def _history_entry(
    *,
    status: ReviewStatus,
    changed_by: str,
    changed_at: datetime,
    reason_code: str | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": status,
        "changed_by": changed_by,
        "changed_at": changed_at.isoformat(),
    }
    if reason_code:
        payload["reason_code"] = reason_code
    return payload


def _candidate_from_record(
    record: ReviewItemRecordProtocol,
    *,
    candidate_id: str,
) -> EvalCandidatePreview:
    safe_counts: dict[str, int | float] = {}
    for key in _METRIC_COUNT_KEYS:
        value = record.safe_summary.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int | float):
            safe_counts[key] = value
    expected = record.safe_summary.get("expected_behavior")
    expected_behavior = (
        expected
        if isinstance(expected, str)
        else "Human reviewer confirms expected behavior before dataset inclusion."
    )
    failure_stage = record.safe_summary.get("failure_stage")
    return EvalCandidatePreview(
        candidate_id=candidate_id,
        source_review_item_id=record.id,
        case_type=record.item_type,
        safe_identifiers=dict(record.safe_identifiers),
        failure_stage=failure_stage if isinstance(failure_stage, str) else None,
        safe_metric_counts=safe_counts,
        expected_behavior=expected_behavior,
        request_id=record.request_id,
        trace_id=record.trace_id,
        requires_human_confirmation=True,
    )


def _assert_read(context: AuthenticatedRequestContext) -> None:
    if has_review_queue_read_permission(context.auth):
        return
    raise ReviewQueueError(
        code=REVIEW_QUEUE_FORBIDDEN,
        message="Review queue read permission is required.",
        details=_safe_error_details(context=context, stage="permission"),
        status_code=403,
    )


def _assert_write(context: AuthenticatedRequestContext) -> None:
    if has_review_queue_write_permission(context.auth):
        return
    raise ReviewQueueError(
        code=REVIEW_QUEUE_FORBIDDEN,
        message="Review queue write permission is required.",
        details=_safe_error_details(context=context, stage="permission"),
        status_code=403,
    )


def _assert_eval_write(context: AuthenticatedRequestContext) -> None:
    if has_eval_candidate_write_permission(context.auth):
        return
    raise ReviewQueueError(
        code=REVIEW_QUEUE_FORBIDDEN,
        message="Eval candidate write permission is required.",
        details=_safe_error_details(context=context, stage="permission"),
        status_code=403,
    )


def _assert_transition(
    current: ReviewStatus,
    desired: ReviewStatus,
    *,
    context: AuthenticatedRequestContext,
    item_id: str,
) -> None:
    if desired in _ALLOWED_TRANSITIONS[current]:
        return
    raise ReviewQueueError(
        code=REVIEW_QUEUE_INVALID_STATUS_TRANSITION,
        message="Review queue status transition is not allowed.",
        details=_safe_error_details(
            context=context,
            stage="status_transition",
            item_id=item_id,
        ),
        status_code=409,
    )


def _not_found(
    *,
    context: AuthenticatedRequestContext,
    item_id: str,
) -> ReviewQueueError:
    return ReviewQueueError(
        code=REVIEW_QUEUE_NOT_FOUND,
        message="Review item was not found.",
        details=_safe_error_details(context=context, stage="lookup", item_id=item_id),
        status_code=404,
    )


def _safe_error_details(
    *,
    context: AuthenticatedRequestContext,
    stage: str,
    item_id: str | None = None,
) -> dict[str, object]:
    details: dict[str, object] = {
        "request_id": context.request_id,
        "trace_id": context.trace_id,
        "stage": stage,
    }
    if item_id is not None:
        details["review_item_id"] = item_id
    return details

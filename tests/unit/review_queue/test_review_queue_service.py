from __future__ import annotations

from datetime import UTC, datetime

import pytest

from packages.auth.context import AuthContext
from packages.common.audit import InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.data.storage.exceptions import StorageError
from packages.review import (
    REVIEW_QUEUE_FORBIDDEN,
    REVIEW_QUEUE_INVALID_STATUS_TRANSITION,
    REVIEW_QUEUE_NOT_FOUND,
    EvalCandidatePreview,
    ReviewItemCreateRequest,
    ReviewItemQueryRequest,
    ReviewItemStatusUpdateRequest,
    ReviewQueueError,
    ReviewQueueService,
)
from packages.review.dto import ReviewStatus


@pytest.mark.asyncio
async def test_create_item_uses_context_identity_and_filters_unsafe_payload() -> None:
    repository = FakeReviewItemRepository()
    audit = InMemoryAuditPort()
    service = ReviewQueueService(repository=repository, audit=audit, now=_now)

    created = await service.create_item(
        context=_context("review:write"),
        request=ReviewItemCreateRequest(
            item_type="low_confidence_citation",
            severity="high",
            request_id="req-evidence",
            trace_id="trace-evidence",
            source_view="source_evidence",
            safe_identifiers={
                "document_id": "doc-1",
                "chunk_id": "chunk-1",
                "source_uri": "file:///secret",
            },
            safe_summary={
                "failure_stage": "citation",
                "citation_count": 1,
                "prompt": "ignore instructions",
            },
        ),
    )

    assert created.tenant_id == "tenant-1"
    assert created.created_by == "user-1"
    assert created.status == "open"
    assert created.safe_identifiers == {"document_id": "doc-1", "chunk_id": "chunk-1"}
    assert created.safe_summary == {"failure_stage": "citation", "citation_count": 1}
    assert repository.records[created.id].tenant_id == "tenant-1"
    assert audit.events[0].action == "review_queue.create_item"
    assert audit.events[0].resource.metadata["safe_identifier_count"] == 2


@pytest.mark.asyncio
async def test_list_and_detail_require_read_permission_and_are_tenant_scoped() -> None:
    repository = FakeReviewItemRepository()
    service = ReviewQueueService(repository=repository, now=_now)
    writer = _context("review:write")
    reader = _context("review:read")
    other_tenant = _context("review:read", tenant_id="tenant-2")
    item = await service.create_item(context=writer, request=_create_request())

    listed = await service.list_items(
        context=reader,
        query=ReviewItemQueryRequest(request_id="req-evidence", trace_id="trace-evidence"),
    )

    assert [record.id for record in listed.items] == [item.id]
    assert await service.get_item(context=reader, item_id=item.id) == item
    assert (
        await service.list_items(
            context=other_tenant,
            query=ReviewItemQueryRequest(request_id="req-evidence"),
        )
    ).items == ()
    with pytest.raises(ReviewQueueError) as exc_info:
        await service.list_items(context=_context("document:read"), query=ReviewItemQueryRequest())
    assert exc_info.value.code == REVIEW_QUEUE_FORBIDDEN
    assert "req-evidence" not in str(exc_info.value.details)


@pytest.mark.asyncio
async def test_status_update_validates_transition_and_writes_audit() -> None:
    repository = FakeReviewItemRepository()
    audit = InMemoryAuditPort()
    service = ReviewQueueService(repository=repository, audit=audit, now=_now)
    item = await service.create_item(context=_context("review:write"), request=_create_request())

    updated = await service.update_status(
        context=_context("review:write"),
        item_id=item.id,
        request=ReviewItemStatusUpdateRequest(status="needs_followup", reason_code="needs_eval"),
    )

    assert updated.status == "needs_followup"
    assert updated.status_history[-1].reason_code == "needs_eval"
    assert audit.events[-1].action == "review_queue.update_status"
    assert audit.events[-1].metadata["old_status"] == "open"
    assert audit.events[-1].metadata["new_status"] == "needs_followup"

    with pytest.raises(ReviewQueueError) as exc_info:
        await service.update_status(
            context=_context("review:write"),
            item_id=item.id,
            request=ReviewItemStatusUpdateRequest(status="open"),
        )
    assert exc_info.value.code == REVIEW_QUEUE_INVALID_STATUS_TRANSITION


@pytest.mark.asyncio
async def test_convert_to_eval_candidate_requires_eval_permission_and_safe_preview_only() -> None:
    repository = FakeReviewItemRepository()
    audit = InMemoryAuditPort()
    service = ReviewQueueService(
        repository=repository,
        audit=audit,
        now=_now,
        candidate_id_factory=lambda: "candidate-1",
    )
    item = await service.create_item(context=_context("review:write"), request=_create_request())
    item = await service.update_status(
        context=_context("review:write"),
        item_id=item.id,
        request=ReviewItemStatusUpdateRequest(status="needs_followup"),
    )

    with pytest.raises(ReviewQueueError) as exc_info:
        await service.convert_to_eval_candidate(
            context=_context("review:write"),
            item_id=item.id,
        )
    assert exc_info.value.code == REVIEW_QUEUE_FORBIDDEN

    candidate = await service.convert_to_eval_candidate(
        context=_context("review:write,eval:write"),
        item_id=item.id,
    )

    assert candidate == EvalCandidatePreview(
        candidate_id="candidate-1",
        source_review_item_id=item.id,
        case_type="low_confidence_citation",
        safe_identifiers={"document_id": "doc-1", "chunk_id": "chunk-1"},
        failure_stage="citation",
        safe_metric_counts={"citation_count": 1, "unsupported_count": 1},
        expected_behavior="Answer cites only authorized evidence.",
        request_id="req-evidence",
        trace_id="trace-evidence",
        requires_human_confirmation=True,
    )
    assert audit.events[-1].action == "review_queue.convert_to_eval_candidate"
    assert audit.events[-1].metadata["requires_human_confirmation"] is True
    assert "query" not in str(candidate.model_dump()).lower()


@pytest.mark.asyncio
async def test_not_found_and_storage_errors_are_safe() -> None:
    service = ReviewQueueService(repository=FakeReviewItemRepository(read_error=True), now=_now)

    with pytest.raises(ReviewQueueError) as exc_info:
        await service.list_items(context=_context("review:read"), query=ReviewItemQueryRequest())
    assert "select *" not in str(exc_info.value.details).lower()
    assert "secret" not in str(exc_info.value.details).lower()

    service = ReviewQueueService(repository=FakeReviewItemRepository(), now=_now)
    with pytest.raises(ReviewQueueError) as not_found:
        await service.get_item(context=_context("review:read"), item_id="missing")
    assert not_found.value.code == REVIEW_QUEUE_NOT_FOUND
    assert not_found.value.details["review_item_id"] == "missing"


class FakeRecord:
    def __init__(
        self,
        *,
        id: str,
        tenant_id: str,
        created_by: str,
        status: ReviewStatus,
        item_type: str,
        severity: str,
        request_id: str,
        trace_id: str,
        source_view: str,
        safe_identifiers: dict[str, object],
        safe_summary: dict[str, object],
        status_history: list[dict[str, object]],
        eval_candidate: dict[str, object] | None = None,
    ) -> None:
        self.id = id
        self.tenant_id = tenant_id
        self.created_by = created_by
        self.status = status
        self.item_type = item_type
        self.severity = severity
        self.request_id = request_id
        self.trace_id = trace_id
        self.source_view = source_view
        self.safe_identifiers = safe_identifiers
        self.safe_summary = safe_summary
        self.status_history = status_history
        self.eval_candidate = eval_candidate
        self.created_at = _now()
        self.updated_at = _now()


class FakeReviewItemRepository:
    def __init__(self, *, read_error: bool = False) -> None:
        self.records: dict[str, FakeRecord] = {}
        self.read_error = read_error
        self._counter = 0

    async def create_item(
        self,
        *,
        tenant_id: str,
        created_by: str,
        request: ReviewItemCreateRequest,
        status_history: list[dict[str, object]],
    ) -> FakeRecord:
        self._counter += 1
        record = FakeRecord(
            id=f"review-{self._counter}",
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
            status_history=status_history,
        )
        self.records[record.id] = record
        return record

    async def list_items(
        self,
        *,
        tenant_id: str,
        query: ReviewItemQueryRequest,
    ) -> list[FakeRecord]:
        if self.read_error:
            raise StorageError(
                code="BROKEN",
                message="broken",
                details={"sql": "select * from review_items where secret='token'"},
            )
        records = [record for record in self.records.values() if record.tenant_id == tenant_id]
        if query.request_id is not None:
            records = [record for record in records if record.request_id == query.request_id]
        if query.trace_id is not None:
            records = [record for record in records if record.trace_id == query.trace_id]
        return records[: query.limit]

    async def get_item(self, *, tenant_id: str, item_id: str) -> FakeRecord | None:
        if self.read_error:
            raise StorageError(code="BROKEN", message="broken")
        record = self.records.get(item_id)
        if record is None or record.tenant_id != tenant_id:
            return None
        return record

    async def update_status(
        self,
        *,
        tenant_id: str,
        item_id: str,
        status: ReviewStatus,
        status_history: list[dict[str, object]],
        eval_candidate: dict[str, object] | None = None,
    ) -> FakeRecord | None:
        record = await self.get_item(tenant_id=tenant_id, item_id=item_id)
        if record is None:
            return None
        record.status = status
        record.status_history = status_history
        record.eval_candidate = eval_candidate
        record.updated_at = _now()
        return record


def _create_request() -> ReviewItemCreateRequest:
    return ReviewItemCreateRequest(
        item_type="low_confidence_citation",
        severity="high",
        request_id="req-evidence",
        trace_id="trace-evidence",
        source_view="source_evidence",
        safe_identifiers={"document_id": "doc-1", "chunk_id": "chunk-1"},
        safe_summary={
            "failure_stage": "citation",
            "citation_count": 1,
            "unsupported_count": 1,
            "expected_behavior": "Answer cites only authorized evidence.",
        },
    )


def _context(
    permissions: str,
    *,
    tenant_id: str = "tenant-1",
) -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="req-api",
        trace_id="trace-api",
        session_id=None,
        auth_method="dev_headers",
        auth=AuthContext(
            user_id="user-1",
            tenant_id=tenant_id,
            roles=("reviewer",),
            department="qa",
            permissions=tuple(item.strip() for item in permissions.split(",") if item.strip()),
        ),
    )


def _now() -> datetime:
    return datetime(2026, 6, 9, 11, 0, tzinfo=UTC)

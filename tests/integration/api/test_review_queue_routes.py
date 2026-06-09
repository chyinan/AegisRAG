from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from apps.api.main import app
from apps.api.service_dependencies import get_review_queue_service
from packages.common.context import AuthenticatedRequestContext
from packages.review import (
    REVIEW_QUEUE_FORBIDDEN,
    EvalCandidatePreview,
    ReviewItemCreateRequest,
    ReviewItemQueryRequest,
    ReviewItemStatusUpdateRequest,
    ReviewItemSummary,
    ReviewQueueError,
    ReviewQueueListResponse,
)


class StubReviewQueueService:
    def __init__(self, *, error: ReviewQueueError | None = None) -> None:
        self.error = error
        self.create_calls: list[tuple[AuthenticatedRequestContext, ReviewItemCreateRequest]] = []
        self.list_calls: list[tuple[AuthenticatedRequestContext, ReviewItemQueryRequest]] = []
        self.detail_calls: list[tuple[AuthenticatedRequestContext, str]] = []
        self.status_calls: list[
            tuple[AuthenticatedRequestContext, str, ReviewItemStatusUpdateRequest]
        ] = []
        self.convert_calls: list[tuple[AuthenticatedRequestContext, str]] = []

    async def create_item(
        self,
        *,
        context: AuthenticatedRequestContext,
        request: ReviewItemCreateRequest,
    ) -> ReviewItemSummary:
        self.create_calls.append((context, request))
        if self.error is not None:
            raise self.error
        return _summary(context)

    async def list_items(
        self,
        *,
        context: AuthenticatedRequestContext,
        query: ReviewItemQueryRequest,
    ) -> ReviewQueueListResponse:
        self.list_calls.append((context, query))
        if self.error is not None:
            raise self.error
        return ReviewQueueListResponse(items=(_summary(context),), next_steps=("pytest",))

    async def get_item(
        self,
        *,
        context: AuthenticatedRequestContext,
        item_id: str,
    ) -> ReviewItemSummary:
        self.detail_calls.append((context, item_id))
        if self.error is not None:
            raise self.error
        return _summary(context, item_id=item_id)

    async def update_status(
        self,
        *,
        context: AuthenticatedRequestContext,
        item_id: str,
        request: ReviewItemStatusUpdateRequest,
    ) -> ReviewItemSummary:
        self.status_calls.append((context, item_id, request))
        if self.error is not None:
            raise self.error
        return _summary(context, item_id=item_id, status=request.status)

    async def convert_to_eval_candidate(
        self,
        *,
        context: AuthenticatedRequestContext,
        item_id: str,
    ) -> EvalCandidatePreview:
        self.convert_calls.append((context, item_id))
        if self.error is not None:
            raise self.error
        return EvalCandidatePreview(
            candidate_id="candidate-1",
            source_review_item_id=item_id,
            case_type="low_confidence_citation",
            safe_identifiers={"document_id": "doc-1"},
            failure_stage="citation",
            safe_metric_counts={"citation_count": 1},
            expected_behavior="Human confirmation required.",
            request_id="req-evidence",
            trace_id="trace-evidence",
            requires_human_confirmation=True,
        )


@pytest.fixture(autouse=True)
def _clear_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_review_create_rejects_identity_override_and_returns_safe_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubReviewQueueService()
    app.dependency_overrides[get_review_queue_service] = lambda: service
    client = TestClient(app)

    response = client.post(
        "/review/items",
        headers=_auth_headers("review:write"),
        json={
            "item_type": "low_confidence_citation",
            "severity": "high",
            "request_id": "req-evidence",
            "trace_id": "trace-evidence",
            "source_view": "source_evidence",
            "safe_identifiers": {"document_id": "doc-1", "source_uri": "file:///secret"},
            "safe_summary": {"failure_stage": "citation", "query": "must reject"},
            "tenant_id": "tenant-evil",
        },
    )

    assert response.status_code == 422
    assert service.create_calls == []

    response = client.post(
        "/review/items",
        headers=_auth_headers("review:write"),
        json={
            "item_type": "low_confidence_citation",
            "severity": "high",
            "request_id": "req-evidence",
            "trace_id": "trace-evidence",
            "source_view": "source_evidence",
            "safe_identifiers": {"document_id": "doc-1"},
            "safe_summary": {"failure_stage": "citation"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["data"]["tenant_id"] == "tenant-1"
    assert "source_uri" not in response.text.lower()
    assert service.create_calls[0][0].auth.tenant_id == "tenant-1"


def test_review_list_detail_update_and_convert_routes_are_thin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubReviewQueueService()
    app.dependency_overrides[get_review_queue_service] = lambda: service
    client = TestClient(app)

    list_response = client.get(
        "/review/items?request_id=req-evidence&trace_id=trace-evidence&limit=5",
        headers=_auth_headers("review:read"),
    )
    detail_response = client.get("/review/items/review-1", headers=_auth_headers("review:read"))
    status_response = client.post(
        "/review/items/review-1/status",
        headers=_auth_headers("review:write"),
        json={"status": "needs_followup", "reason_code": "needs_eval"},
    )
    candidate_response = client.post(
        "/review/items/review-1/eval-candidate",
        headers=_auth_headers("review:write,eval:write"),
    )

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    assert status_response.json()["data"]["status"] == "needs_followup"
    assert candidate_response.json()["data"]["requires_human_confirmation"] is True
    assert service.list_calls[0][1].request_id == "req-evidence"
    assert service.status_calls[0][2].reason_code == "needs_eval"
    assert service.convert_calls[0][1] == "review-1"


def test_review_query_identity_override_and_permission_errors_are_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    service = StubReviewQueueService(
        error=ReviewQueueError(
            code=REVIEW_QUEUE_FORBIDDEN,
            message="Review queue permission is required.",
            details={"request_id": "req-api", "trace_id": "trace-api", "stage": "permission"},
            status_code=403,
        )
    )
    app.dependency_overrides[get_review_queue_service] = lambda: service
    client = TestClient(app)

    response = client.get(
        "/review/items?tenant_id=tenant-evil&request_id=req-target",
        headers=_auth_headers("review:read"),
    )
    assert response.status_code == 422
    assert service.list_calls == []

    response = client.get("/review/items?request_id=req-target", headers=_auth_headers())
    assert response.status_code == 403
    body = response.json()
    assert body["error"]["code"] == REVIEW_QUEUE_FORBIDDEN
    assert "req-target" not in str(body["error"]["details"])
    assert "source_uri" not in response.text.lower()


def _summary(
    context: AuthenticatedRequestContext,
    *,
    item_id: str = "review-1",
    status: str = "open",
) -> ReviewItemSummary:
    now = datetime(2026, 6, 9, 11, 0, tzinfo=UTC)
    return ReviewItemSummary(
        id=item_id,
        item_type="low_confidence_citation",
        severity="high",
        status=status,  # type: ignore[arg-type]
        request_id="req-evidence",
        trace_id="trace-evidence",
        source_view="source_evidence",
        safe_identifiers={"document_id": "doc-1"},
        safe_summary={"failure_stage": "citation"},
        status_history=(),
        allowed_transitions=("accepted", "rejected", "needs_followup"),
        eval_candidate=None,
        created_by=context.auth.user_id,
        tenant_id=context.auth.tenant_id,
        created_at=now,
        updated_at=now,
    )


def _auth_headers(permissions: str = "document:read") -> dict[str, str]:
    return {
        "X-Request-ID": "req-api",
        "X-Trace-ID": "trace-api",
        "X-User-ID": "user-1",
        "X-Tenant-ID": "tenant-1",
        "X-Roles": "reviewer",
        "X-Permissions": permissions,
    }

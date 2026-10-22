"""Normal queue operator routes through ASGI and the application service."""

import httpx
import pytest
from fastapi import FastAPI

from apps.api.dependencies import get_authenticated_request_context
from apps.api.error_handlers import register_error_handlers
from apps.api.factories.queue_ops import get_queue_operations_service
from apps.api.routes.queue_ops import router
from tests.unit.ingestion.test_queue_operations import operator, workflow


@pytest.mark.parametrize("kind", ["ingestion", "embedding"])
async def test_queue_list_route_returns_actionable_attention_items(kind):
    service, repo, queue, audit, job, delivery = workflow(kind)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router, prefix="/v1")
    app.dependency_overrides[get_authenticated_request_context] = operator
    app.dependency_overrides[get_queue_operations_service] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/v1/queue/jobs?limit=10")
    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == "request-1"
    item = body["data"]["items"][0]
    assert item["job_kind"] == kind and item["attempt_count"] == 2
    assert item["retry_allowed"] and item["terminate_allowed"]
    assert item["queue_status"] == "failed"


@pytest.mark.parametrize("kind", ["ingestion", "embedding"])
@pytest.mark.parametrize("action,result_status", [("retry", "queued"), ("terminate", "terminated")])
async def test_queue_mutation_route_returns_confirmed_service_result(kind, action, result_status):
    service, repo, queue, audit, job, delivery = workflow(kind)
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(router, prefix="/v1")
    app.dependency_overrides[get_authenticated_request_context] = operator
    app.dependency_overrides[get_queue_operations_service] = lambda: service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            f"/v1/queue/jobs/{kind}/delivery-1/{action}",
            json={"expected_attempt_count": 2, "expected_queue_status": "failed"},
        )
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["status"] == result_status and result["attempt_count"] == 2
    assert result["job_id"] == "job-1" and result["queue_job_id"] == "delivery-1"
    assert audit.events[0].action == f"queue.job.{action}"

"""ASGI route boundary plus the actual SQLite-backed recovery application service."""
from collections.abc import Iterator

import httpx
import pytest

from apps.api.dependencies import get_authenticated_request_context
from apps.api.main import app
from apps.api.service_dependencies import get_handoff_recovery_service
from packages.common.audit import InMemoryAuditPort
from packages.data.storage.repositories import DocumentRepository
from tests.integration.storage.test_handoff_recovery import (
    InspectQueue,
    operator,
    recovery,
)
from tests.integration.storage.test_handoff_recovery import (
    committed_handoff as committed_handoff,
)
from tests.integration.storage.test_handoff_recovery import (
    database_url as database_url,
)
from tests.integration.storage.test_handoff_recovery import (
    factory as factory,
)
from tests.integration.storage.test_handoff_recovery import (
    global_rq_fetch as global_rq_fetch,
)

URL = "/documents/doc-1/versions/ver-1/recover-handoff"


@pytest.fixture(autouse=True)
def clean_overrides() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


async def test_recovery_api_serializes_confirmed_delivery_with_safe_ids(factory, committed_handoff):
    transport = InspectQueue()
    async with factory() as session:
        service = recovery(DocumentRepository(session), transport, InMemoryAuditPort())
        app.dependency_overrides[get_authenticated_request_context] = operator
        app.dependency_overrides[get_handoff_recovery_service] = lambda: service
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            response = await client.post(URL, json={"ingestion_job_id": "job-1"})
    assert response.status_code == 200
    result = response.json()["data"]
    assert result["status"] == "delivery_confirmed"
    assert result["document_id"] == "doc-1" and result["version_id"] == "ver-1"
    assert result["ingestion_job_id"] == "job-1" and result["embedding_job_id"] == "embed-job-1"
    assert result["request_id"] and result["trace_id"]
    assert transport.calls == 1
    assert set(result) == {
        "status", "document_id", "version_id", "ingestion_job_id", "embedding_job_id",
        "queue_job_id", "queue_name", "queue_status", "request_id", "trace_id",
    }


@pytest.mark.parametrize("permissions,tenant,code,status", [
    ((), "tenant-1", "DOCUMENT_MANAGE_FORBIDDEN", 403),
    (("document:manage",), "foreign", "DOCUMENT_VERSION_NOT_FOUND", 404),
])
async def test_recovery_api_denies_permission_or_foreign_tenant(
    factory, committed_handoff, permissions, tenant, code, status,
):
    transport = InspectQueue()
    async with factory() as session:
        service = recovery(DocumentRepository(session), transport, InMemoryAuditPort())
        app.dependency_overrides[get_authenticated_request_context] = (
            lambda: operator(permissions=permissions, tenant=tenant)
        )
        app.dependency_overrides[get_handoff_recovery_service] = lambda: service
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            response = await client.post(URL, json={"ingestion_job_id": "job-1"})
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert transport.calls == 0


@pytest.mark.parametrize("body", [
    {}, {"ingestion_job_id": " "}, {"ingestion_job_id": "x" * 129},
    {"ingestion_job_id": "job-1", "tenant_id": "foreign"},
    {"ingestion_job_id": "job-1", "reset_retries": True},
])
async def test_recovery_api_rejects_unbounded_or_extra_mutation_inputs(body):
    app.dependency_overrides[get_authenticated_request_context] = operator
    app.dependency_overrides[get_handoff_recovery_service] = lambda: object()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.post(URL, json=body)
    assert response.status_code == 422


async def test_recovery_api_requires_authentication(monkeypatch):
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "false")
    app.dependency_overrides[get_handoff_recovery_service] = lambda: object()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        response = await client.post(URL, json={"ingestion_job_id": "job-1"})
    assert response.status_code == 401

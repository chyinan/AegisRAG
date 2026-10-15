"""ASGI route boundary plus the actual SQLite-backed recovery application service."""
from collections.abc import Iterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from apps.api.dependencies import get_authenticated_request_context
from apps.api.main import app
from apps.api.service_dependencies import get_handoff_recovery_service
from packages.common.audit import InMemoryAuditPort
from packages.common.config import AppSettings
from packages.data.queue.adapters import RQEmbeddingJobQueue
from packages.data.storage.audit_repositories import AuditLogRepository
from packages.data.storage.repositories import DocumentRepository
from packages.ingestion.recovery import HandoffRecoveryCommand, HandoffRecoveryResult
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


async def test_recovery_route_returns_service_result_in_request_envelope():
    from apps.api.routes.documents import recover_document_embedding_handoff

    context = operator()
    command = HandoffRecoveryCommand(ingestion_job_id="job-1")
    result = HandoffRecoveryResult(
        queue_job_id="confirmed-rq-job", queue_name="embedding", queue_status="queued",
        document_id="doc-1", version_id="ver-1", ingestion_job_id="job-1",
        embedding_job_id="embed-job-1", request_id=context.request_id, trace_id=context.trace_id,
    )
    service = SimpleNamespace(recover=AsyncMock(return_value=result))
    envelope = await recover_document_embedding_handoff(
        context=context, service=service, document_id="doc-1", version_id="ver-1", command=command,
    )
    service.recover.assert_awaited_once_with(
        context, document_id="doc-1", version_id="ver-1", command=command,
    )
    assert envelope.request_id == context.request_id
    assert envelope.error is None
    assert envelope.data is result
    assert envelope.model_dump(mode="json")["data"] == result.model_dump(mode="json")


@pytest.mark.parametrize("queue_factory_fails", [False, True])
async def test_recovery_factory_wires_persisted_audit_and_closes_session(
    factory, database_url, committed_handoff, monkeypatch, queue_factory_fails,
):
    from apps.api.factories import domain

    settings = AppSettings(DATABASE_URL=database_url, EMBEDDING_QUEUE_NAME="embedding")
    transport = InspectQueue()
    queue = RQEmbeddingJobQueue(queue=transport, queue_name=settings.embedding_queue_name)
    close_calls = []

    @asynccontextmanager
    async def session_scope():
        async with factory() as session:
            close = AsyncMock(wraps=session.close)
            monkeypatch.setattr(session, "close", close)
            close_calls.append(close)
            yield session

    session_factory = Mock(return_value=session_scope)
    queue_factory = Mock(return_value=queue)
    if queue_factory_fails:
        queue_factory.side_effect = RuntimeError("synthetic queue setup failure")
    monkeypatch.setattr(domain, "load_settings", lambda: settings)
    monkeypatch.setattr(domain, "create_session_factory", session_factory)
    monkeypatch.setattr(domain.RQEmbeddingJobQueue, "from_settings", queue_factory)
    dependency = domain.get_handoff_recovery_service()
    try:
        if queue_factory_fails:
            with pytest.raises(RuntimeError, match="synthetic queue setup failure"):
                await anext(dependency)
        else:
            service = await anext(dependency)
            result = await service.recover(
                operator(), document_id="doc-1", version_id="ver-1",
                command=HandoffRecoveryCommand(ingestion_job_id="job-1"),
            )
            assert result.status == "delivery_confirmed"
            assert result.embedding_job_id == "embed-job-1"
            assert transport.calls == 1
    finally:
        await dependency.aclose()
    session_factory.assert_called_once_with(database_url)
    queue_factory.assert_called_once_with(settings)
    assert len(close_calls) == 1
    close_calls[0].assert_awaited_once()
    async with factory() as session:
        records = await AuditLogRepository(session).list_by_request_id(
            tenant_id="tenant-1", request_id=operator().request_id,
        )
    if queue_factory_fails:
        assert records == [] and transport.calls == 0
    else:
        assert len(records) == 1
        assert records[0].action == "document.embedding_handoff.recover"
        assert records[0].user_id == "operator-1"
        assert records[0].status == "success"
        assert records[0].metadata == {"version_id": "ver-1", "ingestion_job_id": "job-1"}

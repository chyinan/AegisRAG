"""Normal end-to-end folder sync features; all providers and transports stay local."""

from __future__ import annotations

import importlib.util
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import BinaryIO

import httpx
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.dependencies import get_authenticated_request_context
from apps.api.factories.folder_sync import get_folder_sync_service
from apps.api.routes.folder_sync import router
from apps.worker.folder_sync import run_scheduler_pass
from packages.auth.context import AuthContext
from packages.common.audit import InMemoryAuditPort
from packages.common.config import AppSettings
from packages.common.context import AuthenticatedRequestContext
from packages.data.dto import EnqueuedJob, StoredObject
from packages.data.lifecycle import DocumentLifecycleService
from packages.data.queue.contracts import QueuePayload
from packages.data.service import DocumentUploadService, FolderImportService
from packages.data.storage.base import Base
from packages.data.storage.models import DocumentModel, DocumentVersionModel
from packages.data.storage.repositories import DocumentRepository
from packages.ingestion.folder_sync.dto import (
    FolderSyncPlanCommand,
    FolderSyncReconcileCommand,
    FolderSyncSettings,
)
from packages.ingestion.folder_sync.service import FolderSyncService
from packages.ingestion.folder_sync.storage import SqlAlchemyFolderSyncStore
from packages.vectorstores.adapters.fake import FakeVectorStore


class LocalObjectStorage:
    def __init__(self) -> None:
        self.uploaded: list[bytes] = []

    async def put_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        filename: str,
        content_type: str | None,
        stream: BinaryIO,
        byte_size: int,
        checksum: str,
    ) -> StoredObject:
        self.uploaded.append(stream.read())
        return StoredObject(
            bucket="local-test",
            object_key=f"{document_id}/{version_id}/{filename}",
            byte_size=byte_size,
            checksum=checksum,
        )

    async def delete_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        object_key: str,
    ) -> None:
        return None


class LocalQueue:
    def __init__(self) -> None:
        self.jobs: list[QueuePayload] = []

    async def enqueue_ingestion_job(self, payload: QueuePayload) -> EnqueuedJob:
        self.jobs.append(payload)
        return EnqueuedJob(queue_job_id=f"local-{len(self.jobs)}", queue_name="ingestion")


def context() -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="folder-feature-request",
        trace_id="folder-feature-trace",
        auth=AuthContext(
            tenant_id="folder-tenant",
            user_id="folder-user",
            permissions=("document:manage",),
        ),
    )


@pytest.fixture
async def sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'folder-sync.sqlite'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


def service(
    session: AsyncSession,
    sessions: async_sessionmaker[AsyncSession],
    root: Path,
    objects: LocalObjectStorage,
    queue: LocalQueue,
    audit: InMemoryAuditPort,
) -> FolderSyncService:
    repository = DocumentRepository(session)
    return FolderSyncService(
        store=SqlAlchemyFolderSyncStore(sessions),
        importer=FolderImportService(
            upload_service=DocumentUploadService(
                object_storage=objects,
                repository=repository,
                job_queue=queue,
                audit=audit,
            )
        ),
        lifecycle=DocumentLifecycleService(
            repository=repository,
            vector_store=FakeVectorStore(index_dim=8),
            audit=audit,
        ),
        settings=AppSettings(FOLDER_CONNECTOR_ROOT=str(root)),
        sync_settings=FolderSyncSettings(FOLDER_SYNC_ENABLED=True),
        audit=audit,
    )


async def complete_cycle(application: FolderSyncService, plan_id: str) -> None:
    for _ in range(20):
        status = await application.run_batch(context(), plan_id)
        if status.status == "completed":
            return
    pytest.fail("The small local cycle did not finish")


async def test_continuous_batches_restart_checksum_increment_and_status(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    root = tmp_path / "connectors"
    tenant = root / context().auth.tenant_id
    tenant.mkdir(parents=True)
    (tenant / "a.md").write_text("# One\nFirst source", encoding="utf-8")
    (tenant / "b.txt").write_text("Second source", encoding="utf-8")
    objects, queue, audit = LocalObjectStorage(), LocalQueue(), InMemoryAuditPort()
    async with sessions() as session:
        application = service(session, sessions, root, objects, queue, audit)
        plan = await application.configure(context(), FolderSyncPlanCommand(batch_size=1))
        first = await application.run_batch(context(), plan.id)
        assert first.status == "scanning"
        assert first.imported_count == 1
        assert len(queue.jobs) == 1
    # A fresh application and SQLAlchemy session resume the durable path checkpoint.
    async with sessions() as session:
        application = service(session, sessions, root, objects, queue, audit)
        await complete_cycle(application, plan.id)
        status = await application.get_status(context(), plan.id)
        assert status.managed_count == 2
        assert status.imported_count == 2
        assert status.last_completed_at is not None
        assert status.next_run_at > time.time()
        due_noop = await application.run_batch(context(), plan.id, due_only=True)
        assert due_noop.cycle == 1
        assert len(queue.jobs) == 2
        await complete_cycle(application, plan.id)
        unchanged = await application.get_status(context(), plan.id)
        assert unchanged.skipped_count == 2
        assert unchanged.imported_count == 0
        assert len(objects.uploaded) == len(queue.jobs) == 2
        (tenant / "a.md").write_text("# One\nUpdated source", encoding="utf-8")
        await complete_cycle(application, plan.id)
        changed = await application.get_status(context(), plan.id)
        assert changed.imported_count == changed.skipped_count == 1
        assert len(queue.jobs) == 3
        versions = list((await session.scalars(select(DocumentVersionModel))).all())
        assert len(versions) == 3
        assert len({row.document_id for row in versions}) == 2
        assert audit.events[-1].action == "folder.sync.batch"


async def test_deletion_opt_in_reuses_lifecycle_and_source_reappearance(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    root = tmp_path / "connectors"
    tenant = root / context().auth.tenant_id
    tenant.mkdir(parents=True)
    source = tenant / "notes.txt"
    source.write_text("Managed notes", encoding="utf-8")
    objects, queue, audit = LocalObjectStorage(), LocalQueue(), InMemoryAuditPort()
    async with sessions() as session:
        application = service(session, sessions, root, objects, queue, audit)
        plan = await application.configure(context(), FolderSyncPlanCommand())
        await complete_cycle(application, plan.id)
        source.unlink()
        await complete_cycle(application, plan.id)
        assert (await application.get_status(context(), plan.id)).managed_count == 1
        assert not any(event.action == "document.delete" for event in audit.events)
        await application.configure(context(), FolderSyncPlanCommand(delete_missing=True))
        await complete_cycle(application, plan.id)
        candidates = await application.list_candidates(context(), plan.id)
        discovered = await application.get_status(context(), plan.id)
        assert candidates.total == discovered.candidate_count == 1
        assert discovered.deleted_count == 0
        assert not any(event.action == "document.delete" for event in audit.events)
        await application.reconcile(
            context(),
            plan.id,
            FolderSyncReconcileCommand(
                expected_revision=candidates.revision,
                document_ids=[candidates.items[0].document_id],
            ),
        )
        deleted = await application.get_status(context(), plan.id)
        assert deleted.deleted_count == 1
        assert deleted.managed_count == 0
        assert deleted.candidate_count == 0
        assert any(event.action == "document.delete" for event in audit.events)
        old = await session.scalar(select(DocumentModel))
        assert old is not None and old.status == "deleted"
        old_id = old.id
        source.write_text("Managed notes", encoding="utf-8")
        await complete_cycle(application, plan.id)
        restored = await application.get_status(context(), plan.id)
        assert restored.imported_count == restored.managed_count == 1
        documents = list((await session.scalars(select(DocumentModel))).all())
        assert len(documents) == 2
        assert any(row.id != old_id and row.status != "deleted" for row in documents)


async def test_paused_plan_and_normal_api_contract(
    sessions: async_sessionmaker[AsyncSession],
    tmp_path: Path,
) -> None:
    root = tmp_path / "connectors"
    tenant = root / context().auth.tenant_id
    tenant.mkdir(parents=True)
    (tenant / "api.md").write_text("# API folder source", encoding="utf-8")
    objects, queue, audit = LocalObjectStorage(), LocalQueue(), InMemoryAuditPort()
    async with sessions() as session:
        application = service(session, sessions, root, objects, queue, audit)
        app = FastAPI()
        app.include_router(router, prefix="/v1")
        app.dependency_overrides[get_authenticated_request_context] = context
        app.dependency_overrides[get_folder_sync_service] = lambda: application
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local-test"
        ) as client:
            response = await client.put("/v1/sources/folder/sync/plan", json={"enabled": False})
            assert response.status_code == 200
            plan_id = response.json()["data"]["id"]
            response = await client.post(f"/v1/sources/folder/sync/plans/{plan_id}/batch")
            assert response.json()["data"]["cycle"] == 0
            assert not queue.jobs
            await client.put("/v1/sources/folder/sync/plan", json={"enabled": True})
            response = await client.post(f"/v1/sources/folder/sync/plans/{plan_id}/batch")
            assert response.json()["data"]["imported_count"] == 1
            listing = await client.get("/v1/sources/folder/sync/plans")
            assert len(listing.json()["data"]) == 1
            detail = await client.get(f"/v1/sources/folder/sync/plans/{plan_id}")
            assert detail.json()["data"]["managed_count"] == 1
            assert "sources" not in detail.json()["data"]
            await client.put(
                "/v1/sources/folder/sync/plan", json={"enabled": True, "delete_missing": True}
            )
            (tenant / "api.md").unlink()
            await client.post(f"/v1/sources/folder/sync/plans/{plan_id}/batch")
            candidates = await client.get(f"/v1/sources/folder/sync/plans/{plan_id}/candidates")
            review = candidates.json()["data"]
            assert review["total"] == 1
            reconciled = await client.post(
                f"/v1/sources/folder/sync/plans/{plan_id}/reconcile",
                json={
                    "expected_revision": review["revision"],
                    "document_ids": [review["items"][0]["document_id"]],
                },
            )
            assert reconciled.status_code == 200
            assert reconciled.json()["data"]["deleted_count"] == 1


async def test_scheduler_submits_due_plan_batch() -> None:
    calls: list[httpx.Request] = []

    async def transport(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": "due", "enabled": True, "next_run_at": 0},
                        {"id": "future", "enabled": True, "next_run_at": time.time() + 3600},
                        {"id": "paused", "enabled": False, "next_run_at": 0},
                    ]
                },
            )
        return httpx.Response(200, json={"data": {"status": "completed"}})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), base_url="http://local-test"
    ) as client:
        assert await run_scheduler_pass(client) == 1
    assert len(calls) == 2
    assert calls[-1].url.path == "/v1/sources/folder/sync/plans/due/batch"
    assert calls[-1].url.params["due_only"] == "true"


def test_folder_sync_migration_roundtrip(tmp_path: Path) -> None:
    path = Path(__file__).resolve().parents[3] / "migrations/versions/20261016_0019_folder_sync.py"
    spec = importlib.util.spec_from_file_location("folder_sync_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.sqlite'}")
    with engine.begin() as connection:
        module.__dict__["op"] = Operations(MigrationContext.configure(connection))
        module.upgrade()
        assert "folder_sync_plans" in inspect(connection).get_table_names()
        assert {row["name"] for row in inspect(connection).get_columns("folder_sync_plans")} >= {
            "tenant_id",
            "created_by",
            "status",
            "snapshot",
            "lease_token",
            "lease_until",
        }
        module.downgrade()
        assert "folder_sync_plans" not in inspect(connection).get_table_names()
    engine.dispose()

"""Normal operator workflows with SQLite persistence and a mocked queue transport."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.common.audit import InMemoryAuditPort
from packages.data.dto import IngestionJobRecord
from packages.data.storage.models import (
    DocumentModel,
    DocumentVersionModel,
    EmbeddingJobModel,
    IngestionJobModel,
)
from packages.data.storage.repositories import DocumentRepository
from packages.ingestion.queue_operations import QueueOperationCommand, QueueOperationsService
from tests.unit.ingestion.test_queue_operations import operator, workflow


@pytest.mark.parametrize("kind", ["ingestion", "embedding"])
@pytest.mark.parametrize("action", ["retry", "terminate"])
async def test_operator_workflow_with_real_repository(tmp_path, kind, action):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'queue-ops.db'}")
    tables = [
        DocumentModel.__table__,
        DocumentVersionModel.__table__,
        IngestionJobModel.__table__,
        EmbeddingJobModel.__table__,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(
            lambda sync: DocumentModel.metadata.create_all(sync, tables=tables)
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        _, fake_repo, queue, _, job, delivery = workflow(kind)
        async with factory() as session:
            repository = DocumentRepository(session)
            ingestion = (
                job
                if kind == "ingestion"
                else IngestionJobRecord(
                    id="ingest-1",
                    tenant_id="tenant-1",
                    created_by="user-1",
                    status="chunked",
                    document_id="doc-1",
                    version_id="ver-1",
                    queue_name="ingestion",
                )
            )
            await repository.create_upload_records(
                document=fake_repo.get_document.return_value,
                version=fake_repo.get_version.return_value,
                job=ingestion,
            )
            if kind == "embedding":
                await repository.create_embedding_job(job=job)
            await repository.commit()
            audit = InMemoryAuditPort()
            service = QueueOperationsService(repository=repository, queue=queue, audit=audit)
            command = QueueOperationCommand(
                expected_attempt_count=2, expected_queue_status="failed"
            )
            if action == "retry":
                result = await service.retry_job(
                    operator(),
                    job_kind=kind,
                    queue_job_id=delivery.queue_job_id,
                    command=command,
                )
                assert result.status == "queued"
            else:
                result = await service.terminate_job(
                    operator(),
                    job_kind=kind,
                    queue_job_id=delivery.queue_job_id,
                    command=command,
                )
                assert result.status == "terminated"
        async with factory() as session:
            repository = DocumentRepository(session)
            reader = (
                repository.get_ingestion_job
                if kind == "ingestion"
                else repository.get_embedding_job
            )
            persisted = await reader(tenant_id="tenant-1", job_id="job-1")
            assert persisted.attempt_count == 2
            if action == "retry":
                assert persisted.status == "failed_retryable"
                assert result.retries_remaining == 1
            else:
                assert persisted.status == "failed_terminal"
                assert persisted.error_code == "QUEUE_OPERATOR_TERMINATED"
                version = await repository.get_version(tenant_id="tenant-1", version_id="ver-1")
                assert version.status == "failed_terminal"
    finally:
        await engine.dispose()

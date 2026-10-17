"""Composition root for independently checkpointed folder synchronization."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends

from apps.api.factories.common import create_session_factory, create_vector_store
from packages.common.config import load_settings
from packages.data.adapters.minio_object_storage import MinioObjectStorage
from packages.data.lifecycle import DocumentLifecycleService
from packages.data.queue.adapters import RQIngestionJobQueue
from packages.data.service import DocumentUploadService, FolderImportService
from packages.data.storage.audit_repositories import SqlAlchemyAuditPort
from packages.data.storage.repositories import DocumentRepository
from packages.ingestion.folder_sync.dto import FolderSyncSettings
from packages.ingestion.folder_sync.service import FolderSyncService
from packages.ingestion.folder_sync.storage import SqlAlchemyFolderSyncStore, sync_error


async def get_folder_sync_service() -> AsyncIterator[FolderSyncService]:
    sync_settings = FolderSyncSettings()
    if not sync_settings.enabled:
        raise sync_error("FOLDER_SYNC_DISABLED", "Folder synchronization is disabled.", 503)
    settings = load_settings()
    session_factory = create_session_factory(settings.database_url)
    async with session_factory() as session, session_factory() as audit_session:
        repository = DocumentRepository(session)
        audit = SqlAlchemyAuditPort(session)
        upload = DocumentUploadService(
            object_storage=MinioObjectStorage.from_settings(settings),
            repository=repository,
            job_queue=RQIngestionJobQueue.from_settings(settings),
            audit=audit,
            max_upload_bytes=settings.upload_max_bytes,
            queue_name=settings.ingestion_queue_name,
        )
        lifecycle = DocumentLifecycleService(
            repository=repository,
            vector_store=create_vector_store(
                settings.vector_store_type,
                settings.vector_index_dim,
                session,
                milvus_uri=settings.milvus_uri,
                milvus_token=settings.milvus_token,
                milvus_index_type=settings.milvus_index_type,
            ),
            audit=audit,
        )
        yield FolderSyncService(
            store=SqlAlchemyFolderSyncStore(session_factory),
            importer=FolderImportService(upload_service=upload),
            lifecycle=lifecycle,
            audit=SqlAlchemyAuditPort(audit_session, auto_commit=True),
            settings=settings,
            sync_settings=sync_settings,
        )


FolderSyncServiceDep = Annotated[FolderSyncService, Depends(get_folder_sync_service)]

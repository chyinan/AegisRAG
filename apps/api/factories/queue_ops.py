"""Queue control dependency wiring, separate from document processing."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends

from apps.api.factories.common import create_session_factory
from packages.common.config import load_settings
from packages.data.queue.operations import RQQueueOperations
from packages.data.storage.audit_repositories import SqlAlchemyAuditPort
from packages.data.storage.repositories import DocumentRepository
from packages.ingestion.queue_operations import QueueOperationsService


async def get_queue_operations_service() -> AsyncIterator[QueueOperationsService]:
    settings = load_settings()
    session_factory = create_session_factory(settings.database_url)
    async with session_factory() as session:
        yield QueueOperationsService(
            repository=DocumentRepository(session),
            queue=RQQueueOperations.from_settings(settings),
            audit=SqlAlchemyAuditPort(session),
            stale_after_seconds=settings.queue_job_timeout_seconds,
        )


QueueOperationsServiceDep = Annotated[
    QueueOperationsService,
    Depends(get_queue_operations_service),
]

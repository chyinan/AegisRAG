"""Real SQL filtering parity, also reused by the opt-in PostgreSQL pilot gate."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.auth.context import AuthContext
from packages.common.audit import InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.data.lifecycle import DocumentLifecycleService
from packages.data.storage.base import Base
from packages.data.storage.exceptions import StorageError
from packages.data.storage.models import (
    ChunkModel,
    DocumentModel,
    DocumentVersionModel,
    EmbeddingJobModel,
    IngestionJobModel,
)
from packages.data.storage.repositories import DocumentRepository
from packages.vectorstores.adapters.fake import FakeVectorStore

_START = datetime(2026, 1, 1, tzinfo=UTC)
_STATUS_CASES = (
    ("uploaded", None, "uploaded"),
    ("parsing", None, "parsing"),
    ("failed_retryable", None, "failed_retryable"),
    ("failed_terminal", None, "failed_terminal"),
    ("chunked", None, "chunked"),
    ("chunked", "queued", "chunked"),
    ("chunked", "embedding", "embedding"),
    ("chunked", "failed_retryable", "failed_retryable"),
    ("chunked", "failed_terminal", "failed_terminal"),
    ("embedded", "embedding", "embedded"),
    ("embedded", "failed_retryable", "embedded"),
    ("embedded", "failed_terminal", "embedded"),
    ("retrieval_ready", "embedding", "retrieval_ready"),
    ("retrieval_ready", "failed_retryable", "retrieval_ready"),
    ("retrieval_ready", "failed_terminal", "retrieval_ready"),
    ("deleted", "embedding", "deleted"),
    ("deleted", "failed_retryable", "deleted"),
    ("deleted", "failed_terminal", "deleted"),
    ("failed_terminal", "queued", "failed_terminal"),
)


@pytest.fixture
async def status_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync: Base.metadata.create_all(
                    sync,
                    tables=[
                        DocumentModel.__table__,
                        DocumentVersionModel.__table__,
                        IngestionJobModel.__table__,
                        EmbeddingJobModel.__table__,
                        ChunkModel.__table__,
                    ],
                )
            )
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def _seed_document(
    session: AsyncSession,
    *,
    tenant: str,
    suffix: str,
    status: str,
    position: int = 0,
) -> DocumentVersionModel:
    document_id = f"{tenant}-{suffix}"
    session.add(
        DocumentModel(
            id=document_id,
            tenant_id=tenant,
            created_by="synthetic",
            status=status,
            source_type="txt",
            title="Synthetic policy",
            checksum="synthetic-checksum",
            acl={"visibility": "tenant"},
            metadata_={},
            created_at=_START + timedelta(seconds=position),
            deleted_at=_START if status == "deleted" else None,
        )
    )
    await session.flush()
    return await _seed_version(
        session, tenant=tenant, document_id=document_id,
        version_id=f"{document_id}-v1", status=status,
    )


async def _seed_version(
    session: AsyncSession,
    *,
    tenant: str,
    document_id: str,
    version_id: str,
    status: str,
    position: int = 0,
) -> DocumentVersionModel:
    version = DocumentVersionModel(
        id=version_id,
        document_id=document_id,
        tenant_id=tenant,
        created_by="synthetic",
        status=status,
        source_type="txt",
        object_key=f"synthetic/{version_id}",
        filename="policy.txt",
        byte_size=12,
        checksum="synthetic-checksum",
        acl={"visibility": "tenant"},
        metadata_={},
        created_at=_START + timedelta(seconds=position),
        deleted_at=_START if status == "deleted" else None,
    )
    session.add(version)
    await session.flush()
    session.add(
        IngestionJobModel(
            id=f"{version_id}-i",
            document_id=document_id,
            version_id=version_id,
            tenant_id=tenant,
            created_by="synthetic",
            status=status,
            queue_name="ingestion",
            created_at=_START,
        )
    )
    await session.flush()
    return version


async def _seed_embedding_job(
    session: AsyncSession,
    version: DocumentVersionModel,
    status: str,
    *,
    suffix: str = "e1",
    position: int = 0,
) -> None:
    session.add(
        EmbeddingJobModel(
            id=f"{version.id}-{suffix}",
            document_id=version.document_id,
            version_id=version.id,
            tenant_id=version.tenant_id,
            created_by="synthetic",
            status=status,
            provider="fake",
            model="synthetic",
            dim=4,
            metadata_={},
            created_at=_START + timedelta(seconds=position),
        )
    )
    await session.flush()


def _service(session: AsyncSession, tenant: str) -> tuple[
    DocumentLifecycleService, AuthenticatedRequestContext,
]:
    return (
        DocumentLifecycleService(
            repository=DocumentRepository(session),
            vector_store=FakeVectorStore(index_dim=4),
            audit=InMemoryAuditPort(),
        ),
        AuthenticatedRequestContext(
            request_id="effective-status-test",
            trace_id="effective-status-test",
            auth=AuthContext(
                user_id="synthetic",
                tenant_id=tenant,
                roles=("knowledge_admin",),
                permissions=("document:manage",),
            ),
        ),
    )


async def exercise_effective_status_matrix(session: AsyncSession, tenant: str) -> None:
    expected: dict[str, str] = {}
    for index, (stored, job, projected) in enumerate(_STATUS_CASES):
        version = await _seed_document(
            session, tenant=tenant, suffix=str(index), status=stored, position=index,
        )
        if job is not None:
            await _seed_embedding_job(session, version, job)
        expected[version.document_id] = projected
    await session.commit()
    repository = DocumentRepository(session)
    rows = await repository.list_documents(tenant_id=tenant)
    assert {row.id: row.status for row in rows} == expected
    service, context = _service(session, tenant)
    for status in sorted(set(expected.values())):
        rows = await repository.list_documents(tenant_id=tenant, status=status)
        assert {row.id for row in rows} == {
            document_id for document_id, projected in expected.items() if projected == status
        }
        assert all(row.status == status for row in rows)
        # `embedded` is a repository state, not an advertised review filter.
        if status != "embedded":
            page = await service.list_review_documents(context, status=status, limit=100)
            assert {item.document_id for item in page.items} == {row.id for row in rows}
            assert all(item.status == status for item in page.items)
            assert page.next_cursor is None
    for index, (stored, _job, projected) in enumerate(_STATUS_CASES):
        document_id = f"{tenant}-{index}"
        version_id = f"{document_id}-v1"
        document = await repository.get_document(tenant_id=tenant, document_id=document_id)
        version = await repository.get_version(tenant_id=tenant, version_id=version_id)
        detail = await repository.get_document_version_status(
            tenant_id=tenant, document_id=document_id, version_id=version_id,
        )
        assert document is not None and document.status == stored
        assert version is not None and version.status == stored
        assert detail is not None and detail.status == projected


async def exercise_latest_version_and_job(session: AsyncSession, tenant: str) -> None:
    # A later failure belonging to an older version must not hide the ready one.
    old = await _seed_document(session, tenant=tenant, suffix="multi", status="chunked")
    await _seed_embedding_job(session, old, "failed_retryable", position=100)
    latest = await _seed_version(
        session, tenant=tenant, document_id=old.document_id,
        version_id=f"{old.document_id}-v2", status="retrieval_ready", position=1,
    )
    # A newer deleted version loses to the live version even with a running job.
    deleted = await _seed_version(
        session, tenant=tenant, document_id=old.document_id,
        version_id=f"{old.document_id}-v3", status="deleted", position=2,
    )
    await _seed_embedding_job(session, deleted, "embedding", position=200)
    superseded = await _seed_document(session, tenant=tenant, suffix="jobs", status="chunked")
    await _seed_embedding_job(session, superseded, "failed_retryable", suffix="a", position=1)
    await _seed_embedding_job(session, superseded, "queued", suffix="b", position=2)
    tied = await _seed_document(session, tenant=tenant, suffix="ties", status="chunked")
    await _seed_embedding_job(session, tied, "failed_terminal", suffix="z", position=10)
    await _seed_embedding_job(session, tied, "embedding", suffix="a", position=10)
    # Equal version timestamps resolve by id, independently of insertion order.
    tie_old = await _seed_document(session, tenant=tenant, suffix="vtie", status="chunked")
    tie_new = await _seed_version(
        session, tenant=tenant, document_id=tie_old.document_id,
        version_id=f"{tie_old.document_id}-v9", status="chunked",
    )
    await _seed_embedding_job(session, tie_old, "failed_terminal", position=100)
    await _seed_embedding_job(session, tie_new, "embedding")
    await session.commit()
    repository = DocumentRepository(session)
    expected = {
        old.document_id: "retrieval_ready",
        superseded.document_id: "chunked",
        tied.document_id: "failed_terminal",
        tie_old.document_id: "embedding",
    }
    assert {
        row.id: row.status for row in await repository.list_documents(tenant_id=tenant)
    } == expected
    service, context = _service(session, tenant)
    for status in expected.values():
        page = await service.list_review_documents(context, status=status)
        assert {item.document_id for item in page.items} == {
            document_id for document_id, value in expected.items() if value == status
        }
        assert all(item.status == status for item in page.items)
    # Deleting the latest live version falls back to the old retryable version.
    await repository.soft_delete_document_version(
        tenant_id=tenant, document_id=old.document_id,
        version_id=latest.id, deleted_by="synthetic",
    )
    await repository.commit()
    page = await service.list_review_documents(context, status="failed_retryable")
    assert [(item.document_id, item.version_id) for item in page.items] == [
        (old.document_id, old.id),
    ]
    # Deleting the document overrides leftover failed/running jobs on all versions.
    await repository.soft_delete_document(
        tenant_id=tenant, document_id=old.document_id, deleted_by="synthetic",
    )
    await repository.commit()
    assert await repository.list_documents(tenant_id=tenant, status="failed_retryable") == []
    page = await service.list_review_documents(context, status="deleted")
    assert [(item.document_id, item.version_id, item.status) for item in page.items] == [
        (old.document_id, deleted.id, "deleted"),
    ]


async def exercise_filtered_pagination(session: AsyncSession, tenant: str) -> None:
    for index in range(9):
        version = await _seed_document(
            session, tenant=tenant, suffix=str(index), status="chunked", position=index,
        )
        await _seed_embedding_job(
            session, version, "failed_retryable" if index in {2, 5, 8} else "embedding",
        )
    await session.commit()
    repository = DocumentRepository(session)
    for offset, expected_index in enumerate((2, 5, 8)):
        rows = await repository.list_documents(
            tenant_id=tenant, status="failed_retryable", limit=1, cursor=offset,
        )
        assert [(row.id, row.status) for row in rows] == [
            (f"{tenant}-{expected_index}", "failed_retryable"),
        ]
    service, context = _service(session, tenant)
    first = await service.list_review_documents(context, status="failed_retryable", limit=2)
    assert [item.document_id for item in first.items] == [f"{tenant}-2", f"{tenant}-5"]
    assert first.next_cursor == "2"
    second = await service.list_review_documents(
        context, status="failed_retryable", limit=2, cursor=first.next_cursor,
    )
    assert [item.document_id for item in second.items] == [f"{tenant}-8"]
    assert second.next_cursor is None
    assert all(item.status == "failed_retryable" for item in first.items + second.items)
    assert await repository.list_documents(tenant_id=tenant, status="chunked") == []


async def test_effective_status_matches_filter_and_preserves_checkpoint(
    status_session: AsyncSession,
) -> None:
    await exercise_effective_status_matrix(status_session, "status-matrix")


async def test_latest_live_version_and_latest_scoped_job_control_filter(
    status_session: AsyncSession,
) -> None:
    await exercise_latest_version_and_job(status_session, "status-versions")


async def test_effective_status_filter_runs_before_cursor_and_limit(
    status_session: AsyncSession,
) -> None:
    await exercise_filtered_pagination(status_session, "status-pages")


async def test_filtered_document_query_failure_is_an_error_not_an_empty_page(
    status_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant = "status-query-failure"
    for index in range(2):
        version = await _seed_document(
            status_session, tenant=tenant, suffix=str(index), status="chunked", position=index,
        )
        await _seed_embedding_job(status_session, version, "failed_retryable")
    await status_session.commit()
    repository = DocumentRepository(status_session)
    filters = {"tenant_id": tenant, "status": "failed_retryable", "limit": 1, "cursor": 1}
    before = await repository.list_documents(**filters)
    assert [(row.id, row.status) for row in before] == [
        (f"{tenant}-1", "failed_retryable"),
    ]
    failure = SQLAlchemyError("synthetic filtered document query failure")
    query = AsyncMock(side_effect=failure)
    with monkeypatch.context() as patch:
        patch.setattr(status_session, "execute", query)
        with pytest.raises(StorageError) as exc:
            await repository.list_documents(**filters)
    query.assert_awaited_once()
    assert exc.value.code == "DOCUMENT_STORAGE_QUERY_FAILED"
    assert exc.value.__cause__ is failure
    # The same real page still exists when the database boundary is restored.
    assert await repository.list_documents(**filters) == before


async def test_effective_status_is_tenant_scoped_and_handles_no_version(
    status_session: AsyncSession,
) -> None:
    for tenant, phase in (("status-a", "embedding"), ("status-b", "failed_retryable")):
        version = await _seed_document(
            status_session, tenant=tenant, suffix="doc", status="chunked",
        )
        await _seed_embedding_job(status_session, version, phase)
    status_session.add(
        DocumentModel(
            id="status-a-orphan", tenant_id="status-a", created_by="synthetic",
            status="uploaded", source_type="txt", checksum="synthetic", acl={}, metadata_={},
        )
    )
    await status_session.commit()
    repository = DocumentRepository(status_session)
    assert await repository.list_documents(tenant_id="status-a", status="failed_retryable") == []
    assert await repository.list_documents(tenant_id="status-b", status="embedding") == []
    assert [row.id for row in await repository.list_documents(tenant_id="status-a")] == [
        "status-a-doc", "status-a-orphan",
    ]
    page = await _service(status_session, "status-a")[0].list_review_documents(
        _service(status_session, "status-a")[1], status="uploaded",
    )
    assert [(item.document_id, item.status, item.version_id) for item in page.items] == [
        ("status-a-orphan", "uploaded", None),
    ]

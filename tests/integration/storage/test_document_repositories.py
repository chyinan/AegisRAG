from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.data.dto import (
    ChunkRecord,
    DocumentRecord,
    DocumentVersionRecord,
    EmbeddingJobRecord,
    IngestionJobRecord,
)
from packages.data.storage.exceptions import StorageError
from packages.data.storage.repositories import DocumentRepository

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _sqlite_async_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def _run_migrations(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    command.upgrade(config, "head")


def _document(
    *,
    tenant_id: str = "tenant-1",
    document_id: str = "doc-1",
    status: str = "parsed",
) -> DocumentRecord:
    return DocumentRecord(
        id=document_id,
        tenant_id=tenant_id,
        created_by="user-1",
        status=status,
        source_type="txt",
        source_uri="kb://policy.txt",
        title="Policy",
        acl={"visibility": "tenant"},
        checksum=f"checksum-{document_id}",
        metadata={},
    )


def _version(
    *,
    tenant_id: str = "tenant-1",
    document_id: str = "doc-1",
    version_id: str = "ver-1",
    status: str = "parsed",
) -> DocumentVersionRecord:
    return DocumentVersionRecord(
        id=version_id,
        document_id=document_id,
        tenant_id=tenant_id,
        created_by="user-1",
        status=status,
        source_type="txt",
        source_uri="kb://policy.txt",
        object_key=f"raw/{tenant_id}/{document_id}/{version_id}/policy.txt",
        filename="policy.txt",
        content_type="text/plain",
        byte_size=12,
        acl={"visibility": "tenant"},
        checksum=f"checksum-{version_id}",
        metadata={"parsed_artifact_summary": {"section_count": 1}},
    )


def _job(
    *,
    tenant_id: str = "tenant-1",
    document_id: str = "doc-1",
    version_id: str = "ver-1",
    job_id: str = "job-1",
    status: str = "parsed",
) -> IngestionJobRecord:
    return IngestionJobRecord(
        id=job_id,
        tenant_id=tenant_id,
        created_by="user-1",
        status=status,
        document_id=document_id,
        version_id=version_id,
        queue_name="ingestion",
    )


def _embedding_job(
    *,
    tenant_id: str = "tenant-1",
    document_id: str = "doc-1",
    version_id: str = "ver-1",
    job_id: str = "embed-job-1",
    status: str = "queued",
    next_retry_at: datetime | None = None,
) -> EmbeddingJobRecord:
    return EmbeddingJobRecord(
        id=job_id,
        tenant_id=tenant_id,
        created_by="user-1",
        status=status,
        document_id=document_id,
        version_id=version_id,
        provider="fake",
        model="fake-embedding",
        dim=4,
        next_retry_at=next_retry_at,
    )


def _chunk(
    *,
    tenant_id: str = "tenant-1",
    document_id: str = "doc-1",
    version_id: str = "ver-1",
    chunk_id: str = "chunk-1",
    content: str = "Policy chunk content",
    token_count: int = 3,
    page_start: int | None = 1,
    page_end: int | None = 1,
    acl: dict[str, object] | None = None,
) -> ChunkRecord:
    return ChunkRecord.model_validate(
        {
            "tenant_id": tenant_id,
            "document_id": document_id,
            "version_id": version_id,
            "chunk_id": chunk_id,
            "created_by": "user-1",
            "status": "active",
            "source_type": "txt",
            "source_uri": "kb://policy.txt",
            "title_path": ["Policy"],
            "content": content,
            "page_start": page_start,
            "page_end": page_end,
            "token_count": token_count,
            "acl": acl,
            "checksum": f"checksum-{chunk_id}",
            "section_ids": ["section-1"],
            "metadata": None,
        }
    )


def test_document_repository_persists_upload_records_and_tenant_scoped_queries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "documents.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repository() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                repository = DocumentRepository(session)
                document, version, job = await repository.create_upload_records(
                    document=DocumentRecord(
                        id="doc-1",
                        tenant_id="tenant-1",
                        created_by="user-1",
                        status="uploaded",
                        source_type="txt",
                        source_uri="kb://policy.txt",
                        title="Policy",
                        acl={"visibility": "tenant"},
                        checksum="checksum-1",
                        metadata={"department": "HR"},
                    ),
                    version=DocumentVersionRecord(
                        id="ver-1",
                        document_id="doc-1",
                        tenant_id="tenant-1",
                        created_by="user-1",
                        status="uploaded",
                        source_type="txt",
                        source_uri="kb://policy.txt",
                        object_key="raw/tenant-1/doc-1/ver-1/policy.txt",
                        filename="policy.txt",
                        content_type="text/plain",
                        byte_size=12,
                        acl={"visibility": "tenant"},
                        checksum="checksum-1",
                        metadata={"department": "HR"},
                    ),
                    job=IngestionJobRecord(
                        id="job-1",
                        tenant_id="tenant-1",
                        created_by="user-1",
                        status="uploaded",
                        document_id="doc-1",
                        version_id="ver-1",
                        queue_name="ingestion",
                        queue_job_id=None,
                        attempt_count=0,
                        error_code=None,
                    ),
                )
                await repository.commit()

                by_tenant = await repository.list_documents(
                    tenant_id="tenant-1",
                    status="uploaded",
                )
                cross_tenant = await repository.list_documents(
                    tenant_id="tenant-2",
                    status="uploaded",
                )
                versions = await repository.list_versions(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    status="uploaded",
                )
                version_by_id = await repository.get_version(
                    tenant_id="tenant-1",
                    version_id="ver-1",
                )
                cross_tenant_version = await repository.get_version(
                    tenant_id="tenant-2",
                    version_id="ver-1",
                )
                jobs = await repository.list_ingestion_jobs(
                    tenant_id="tenant-1",
                    status="uploaded",
                    version_id="ver-1",
                )

            assert document.id == "doc-1"
            assert version.id == "ver-1"
            assert job.id == "job-1"
            assert by_tenant == [document]
            assert cross_tenant == []
            assert versions == [version]
            assert version_by_id == version
            assert cross_tenant_version is None
            assert jobs == [job]
            assert not hasattr(document, "_sa_instance_state")
            assert not hasattr(version, "_sa_instance_state")
            assert not hasattr(job, "_sa_instance_state")
        finally:
            await engine.dispose()

    asyncio.run(exercise_repository())


def test_document_repository_creates_new_version_without_overwriting_existing_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "document-versioning.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repository() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                repository = DocumentRepository(session)
                await repository.create_upload_records(
                    document=_document(status="retrieval_ready"),
                    version=_version(status="retrieval_ready"),
                    job=_job(status="chunked"),
                )
                created_version, created_job = await repository.create_document_version_records(
                    version=_version(version_id="ver-2", status="uploaded"),
                    job=_job(version_id="ver-2", job_id="job-2", status="uploaded"),
                )
                await repository.commit()

                versions = await repository.list_versions(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                )
                document = await repository.get_document(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                )

            assert created_version.id == "ver-2"
            assert created_job.version_id == "ver-2"
            assert [version.id for version in versions] == ["ver-1", "ver-2"]
            assert versions[0].status == "retrieval_ready"
            assert versions[1].status == "uploaded"
            assert document is not None
            assert document.status == "uploaded"
        finally:
            await engine.dispose()

    asyncio.run(exercise_repository())


def test_document_repository_updates_parser_job_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "parser-jobs.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repository() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                repository = DocumentRepository(session)
                await repository.create_upload_records(
                    document=DocumentRecord(
                        id="doc-1",
                        tenant_id="tenant-1",
                        created_by="user-1",
                        status="uploaded",
                        source_type="markdown",
                        source_uri="kb://policy.md",
                        title="Policy",
                        acl={"visibility": "tenant"},
                        checksum="checksum-1",
                        metadata={"department": "HR"},
                    ),
                    version=DocumentVersionRecord(
                        id="ver-1",
                        document_id="doc-1",
                        tenant_id="tenant-1",
                        created_by="user-1",
                        status="uploaded",
                        source_type="markdown",
                        source_uri="kb://policy.md",
                        object_key="raw/tenant-1/doc-1/ver-1/policy.md",
                        filename="policy.md",
                        content_type="text/markdown",
                        byte_size=12,
                        acl={"visibility": "tenant"},
                        checksum="checksum-1",
                        metadata={"department": "HR"},
                    ),
                    job=IngestionJobRecord(
                        id="job-1",
                        tenant_id="tenant-1",
                        created_by="user-1",
                        status="queued",
                        document_id="doc-1",
                        version_id="ver-1",
                        queue_name="ingestion",
                    ),
                )
                await repository.commit()

                job = await repository.get_ingestion_job(tenant_id="tenant-1", job_id="job-1")
                cross_tenant = await repository.get_ingestion_job(
                    tenant_id="tenant-2",
                    job_id="job-1",
                )
                parsing = await repository.mark_ingestion_job_parsing(
                    tenant_id="tenant-1",
                    job_id="job-1",
                )
                parsed = await repository.mark_ingestion_job_parsed(
                    tenant_id="tenant-1",
                    job_id="job-1",
                    parsed_metadata={"section_count": 2, "page_count": 2},
                )
                await repository.commit()
                parsed_version = await repository.get_version(
                    tenant_id="tenant-1",
                    version_id="ver-1",
                )
                failed = await repository.mark_ingestion_job_failed(
                    tenant_id="tenant-1",
                    job_id="job-1",
                    status="failed_terminal",
                    error_code="DOCUMENT_PARSE_EMPTY_CONTENT",
                )
                await repository.commit()
                failed_version = await repository.get_version(
                    tenant_id="tenant-1",
                    version_id="ver-1",
                )
                failed_documents = await repository.list_documents(
                    tenant_id="tenant-1",
                    status="failed_terminal",
                )

            assert job is not None
            assert cross_tenant is None
            assert parsing.status == "parsing"
            assert parsing.attempt_count == 1
            assert parsing.last_attempt_at is not None
            assert parsed.status == "parsed"
            assert parsed_version is not None
            assert parsed_version.status == "parsed"
            assert parsed_version.metadata["parsed_artifact_summary"] == {
                "section_count": 2,
                "page_count": 2,
            }
            assert failed.status == "failed_terminal"
            assert failed.error_code == "DOCUMENT_PARSE_EMPTY_CONTENT"
            assert failed.last_attempt_at is not None
            assert failed_version is not None
            assert failed_version.status == "failed_terminal"
            assert [document.id for document in failed_documents] == ["doc-1"]
        finally:
            await engine.dispose()

    asyncio.run(exercise_repository())


def test_document_repository_claims_parser_jobs_atomically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "parser-claims.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repository() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                repository = DocumentRepository(session)
                await repository.create_upload_records(
                    document=DocumentRecord(
                        id="doc-1",
                        tenant_id="tenant-1",
                        created_by="user-1",
                        status="uploaded",
                        source_type="txt",
                        source_uri="kb://policy.txt",
                        title="Policy",
                        acl={"visibility": "tenant"},
                        checksum="checksum-1",
                        metadata={},
                    ),
                    version=DocumentVersionRecord(
                        id="ver-1",
                        document_id="doc-1",
                        tenant_id="tenant-1",
                        created_by="user-1",
                        status="uploaded",
                        source_type="txt",
                        source_uri="kb://policy.txt",
                        object_key="raw/tenant-1/doc-1/ver-1/policy.txt",
                        filename="policy.txt",
                        content_type="text/plain",
                        byte_size=12,
                        acl={"visibility": "tenant"},
                        checksum="checksum-1",
                        metadata={},
                    ),
                    job=IngestionJobRecord(
                        id="job-1",
                        tenant_id="tenant-1",
                        created_by="user-1",
                        status="queued",
                        document_id="doc-1",
                        version_id="ver-1",
                        queue_name="ingestion",
                    ),
                )
                await repository.commit()

                stale_before = None
                first_claim = await repository.claim_ingestion_job_parsing(
                    tenant_id="tenant-1",
                    job_id="job-1",
                    document_id="doc-1",
                    version_id="ver-1",
                    stale_before=stale_before,
                )
                second_claim = await repository.claim_ingestion_job_parsing(
                    tenant_id="tenant-1",
                    job_id="job-1",
                    document_id="doc-1",
                    version_id="ver-1",
                    stale_before=stale_before,
                )
                mismatched_claim = await repository.claim_ingestion_job_parsing(
                    tenant_id="tenant-1",
                    job_id="job-1",
                    document_id="other-doc",
                    version_id="ver-1",
                    stale_before=stale_before,
                )

            assert first_claim is not None
            assert first_claim.status == "parsing"
            assert first_claim.attempt_count == 1
            assert first_claim.last_attempt_at is not None
            assert second_claim is None
            assert mismatched_claim is None
        finally:
            await engine.dispose()

    asyncio.run(exercise_repository())


def test_document_repository_replaces_and_reads_tenant_scoped_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "chunks.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repository() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                repository = DocumentRepository(session)
                await repository.create_upload_records(
                    document=_document(),
                    version=_version(),
                    job=_job(),
                )
                await repository.create_upload_records(
                    document=_document(tenant_id="tenant-2", document_id="doc-2"),
                    version=_version(
                        tenant_id="tenant-2",
                        document_id="doc-2",
                        version_id="ver-2",
                    ),
                    job=_job(
                        tenant_id="tenant-2",
                        document_id="doc-2",
                        version_id="ver-2",
                        job_id="job-2",
                    ),
                )
                await repository.commit()

                first_write = await repository.replace_chunks_for_version(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-1",
                    chunks=[
                        _chunk(chunk_id="chunk-1", token_count=10),
                        _chunk(
                            chunk_id="chunk-2",
                            content="DOCX section without page numbers",
                            token_count=5,
                            page_start=None,
                            page_end=None,
                        ),
                    ],
                )
                await repository.commit()

                replacement = await repository.replace_chunks_for_version(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-1",
                    chunks=[_chunk(chunk_id="chunk-3", token_count=8)],
                )
                await repository.commit()

                await repository.replace_chunks_for_version(
                    tenant_id="tenant-2",
                    document_id="doc-2",
                    version_id="ver-2",
                    chunks=[
                        _chunk(
                            tenant_id="tenant-2",
                            document_id="doc-2",
                            version_id="ver-2",
                            chunk_id="chunk-1",
                        )
                    ],
                )
                await repository.commit()

                tenant_chunks = await repository.list_chunks_for_version(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-1",
                )
                by_chunk_id = await repository.get_chunk(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-1",
                    chunk_id="chunk-3",
                )
                tenant2_by_chunk_id = await repository.get_chunk(
                    tenant_id="tenant-2",
                    chunk_id="chunk-1",
                )
                cross_tenant = await repository.get_chunk(
                    tenant_id="tenant-2",
                    document_id="doc-1",
                    version_id="ver-1",
                    chunk_id="chunk-3",
                )

            assert [chunk.chunk_id for chunk in first_write] == ["chunk-1", "chunk-2"]
            assert replacement[0].chunk_id == "chunk-3"
            assert [chunk.chunk_id for chunk in tenant_chunks] == ["chunk-3"]
            assert by_chunk_id == replacement[0]
            assert tenant2_by_chunk_id is not None
            assert tenant2_by_chunk_id.document_id == "doc-2"
            assert cross_tenant is None
            assert not hasattr(by_chunk_id, "_sa_instance_state")
            assert replacement[0].acl == {"visibility": "tenant"}
            assert replacement[0].metadata == {}
        finally:
            await engine.dispose()

    asyncio.run(exercise_repository())


def test_document_repository_rejects_chunk_version_document_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "chunk-scope-mismatch.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repository() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                repository = DocumentRepository(session)
                await repository.create_upload_records(
                    document=_document(document_id="doc-1"),
                    version=_version(document_id="doc-1", version_id="ver-1"),
                    job=_job(document_id="doc-1", version_id="ver-1", job_id="job-1"),
                )
                await repository.create_upload_records(
                    document=_document(document_id="doc-2"),
                    version=_version(document_id="doc-2", version_id="ver-2"),
                    job=_job(document_id="doc-2", version_id="ver-2", job_id="job-2"),
                )
                await repository.commit()

                with pytest.raises(StorageError) as exc_info:
                    await repository.replace_chunks_for_version(
                        tenant_id="tenant-1",
                        document_id="doc-1",
                        version_id="ver-2",
                        chunks=[
                            _chunk(
                                document_id="doc-1",
                                version_id="ver-2",
                                chunk_id="wrong-scope",
                            )
                        ],
                    )

            assert exc_info.value.code == "CHUNK_STORAGE_WRITE_FAILED"
            assert exc_info.value.details == {
                "tenant_id": "tenant-1",
                "document_id": "doc-1",
                "version_id": "ver-2",
            }
        finally:
            await engine.dispose()

    asyncio.run(exercise_repository())


def test_document_repository_rejects_empty_chunk_replacement_without_deleting_existing_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "empty-chunk-replacement.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repository() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                repository = DocumentRepository(session)
                await repository.create_upload_records(
                    document=_document(),
                    version=_version(),
                    job=_job(),
                )
                await repository.replace_chunks_for_version(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-1",
                    chunks=[_chunk(chunk_id="kept-chunk")],
                )
                await repository.commit()

                with pytest.raises(StorageError) as exc_info:
                    await repository.replace_chunks_for_version(
                        tenant_id="tenant-1",
                        document_id="doc-1",
                        version_id="ver-1",
                        chunks=[],
                    )
                await repository.rollback()

                chunks = await repository.list_chunks_for_version(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-1",
                )

            assert exc_info.value.code == "CHUNK_STORAGE_EMPTY"
            assert [chunk.chunk_id for chunk in chunks] == ["kept-chunk"]
        finally:
            await engine.dispose()

    asyncio.run(exercise_repository())


def test_document_repository_marks_ingestion_job_chunked_with_safe_version_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "chunked-job.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repository() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                repository = DocumentRepository(session)
                await repository.create_upload_records(
                    document=_document(),
                    version=_version(),
                    job=_job(),
                )
                await repository.replace_chunks_for_version(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-1",
                    chunks=[
                        _chunk(chunk_id="chunk-1", content="secret policy one", token_count=10),
                        _chunk(chunk_id="chunk-2", content="secret policy two", token_count=20),
                    ],
                )
                chunked = await repository.mark_ingestion_job_chunked(
                    tenant_id="tenant-1",
                    job_id="job-1",
                    chunk_metadata={
                        "chunk_count": 2,
                        "token_count_min": 10,
                        "token_count_max": 20,
                        "checksum_summary": ["checksum-chunk-1", "checksum-chunk-2"],
                        "content": "must not be stored",
                        "chunks": [{"content": "must not be stored"}],
                    },
                )
                await repository.commit()

                version = await repository.get_version(tenant_id="tenant-1", version_id="ver-1")
                documents = await repository.list_documents(tenant_id="tenant-1", status="chunked")

            assert chunked.status == "chunked"
            assert version is not None
            assert version.status == "chunked"
            summary = version.metadata["chunk_artifact_summary"]
            assert summary == {
                "chunk_count": 2,
                "token_count_min": 10,
                "token_count_max": 20,
                "checksum_summary": ["checksum-chunk-1", "checksum-chunk-2"],
            }
            assert "content" not in str(version.metadata)
            assert [document.id for document in documents] == ["doc-1"]
        finally:
            await engine.dispose()

    asyncio.run(exercise_repository())


def test_document_repository_rejects_chunked_state_without_matching_persisted_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "chunked-count-validation.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repository() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                repository = DocumentRepository(session)
                await repository.create_upload_records(
                    document=_document(),
                    version=_version(),
                    job=_job(),
                )
                await repository.commit()

                with pytest.raises(StorageError) as missing_chunks:
                    await repository.mark_ingestion_job_chunked(
                        tenant_id="tenant-1",
                        job_id="job-1",
                        chunk_metadata={"chunk_count": 1},
                    )
                await repository.rollback()

                await repository.replace_chunks_for_version(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-1",
                    chunks=[_chunk(chunk_id="chunk-1")],
                )
                with pytest.raises(StorageError) as count_mismatch:
                    await repository.mark_ingestion_job_chunked(
                        tenant_id="tenant-1",
                        job_id="job-1",
                        chunk_metadata={"chunk_count": 2},
                    )
                await repository.rollback()

            assert missing_chunks.value.code == "CHUNK_STORAGE_EMPTY"
            assert count_mismatch.value.code == "CHUNK_METADATA_MISMATCH"
        finally:
            await engine.dispose()

    asyncio.run(exercise_repository())


def test_document_repository_rejects_duplicate_chunk_ids_with_storage_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "duplicate-chunks.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repository() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                repository = DocumentRepository(session)
                await repository.create_upload_records(
                    document=_document(),
                    version=_version(),
                    job=_job(),
                )
                await repository.commit()

                with pytest.raises(StorageError) as exc_info:
                    await repository.replace_chunks_for_version(
                        tenant_id="tenant-1",
                        document_id="doc-1",
                        version_id="ver-1",
                        chunks=[
                            _chunk(chunk_id="duplicate"),
                            _chunk(chunk_id="duplicate", content="second duplicate"),
                        ],
                    )

            assert exc_info.value.code == "CHUNK_STORAGE_WRITE_FAILED"
            assert exc_info.value.details == {
                "tenant_id": "tenant-1",
                "document_id": "doc-1",
                "version_id": "ver-1",
            }
        finally:
            await engine.dispose()

    asyncio.run(exercise_repository())


def test_document_repository_rejects_duplicate_chunk_ids_within_tenant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "tenant-duplicate-chunks.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repository() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                repository = DocumentRepository(session)
                await repository.create_upload_records(
                    document=_document(document_id="doc-1"),
                    version=_version(document_id="doc-1", version_id="ver-1"),
                    job=_job(document_id="doc-1", version_id="ver-1", job_id="job-1"),
                )
                await repository.create_upload_records(
                    document=_document(document_id="doc-2"),
                    version=_version(document_id="doc-2", version_id="ver-2"),
                    job=_job(document_id="doc-2", version_id="ver-2", job_id="job-2"),
                )
                await repository.commit()
                await repository.replace_chunks_for_version(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-1",
                    chunks=[_chunk(chunk_id="tenant-unique")],
                )
                await repository.commit()

                with pytest.raises(StorageError) as exc_info:
                    await repository.replace_chunks_for_version(
                        tenant_id="tenant-1",
                        document_id="doc-2",
                        version_id="ver-2",
                        chunks=[
                            _chunk(
                                document_id="doc-2",
                                version_id="ver-2",
                                chunk_id="tenant-unique",
                            )
                        ],
                    )

            assert exc_info.value.code == "CHUNK_STORAGE_WRITE_FAILED"
        finally:
            await engine.dispose()

    asyncio.run(exercise_repository())


def test_document_repository_manages_embedding_jobs_tenant_scoped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "embedding-jobs.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repository() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                repository = DocumentRepository(session)
                await repository.create_upload_records(
                    document=_document(status="chunked"),
                    version=_version(status="chunked"),
                    job=_job(status="chunked"),
                )
                await repository.replace_chunks_for_version(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-1",
                    chunks=[
                        _chunk(chunk_id="chunk-1", token_count=10),
                        _chunk(chunk_id="chunk-2", token_count=20),
                    ],
                )
                created = await repository.create_embedding_job(job=_embedding_job())
                await repository.commit()

                listed = await repository.list_embedding_jobs(
                    tenant_id="tenant-1",
                    status="queued",
                    version_id="ver-1",
                )
                cross_tenant = await repository.get_embedding_job(
                    tenant_id="tenant-2",
                    job_id="embed-job-1",
                )
                claim = await repository.claim_embedding_job(
                    tenant_id="tenant-1",
                    job_id="embed-job-1",
                    document_id="doc-1",
                    version_id="ver-1",
                    stale_before=None,
                )
                second_claim = await repository.claim_embedding_job(
                    tenant_id="tenant-1",
                    job_id="embed-job-1",
                    document_id="doc-1",
                    version_id="ver-1",
                    stale_before=None,
                )
                embedded = await repository.mark_embedding_job_embedded(
                    tenant_id="tenant-1",
                    job_id="embed-job-1",
                    embedding_metadata={
                        "stage": "embedded",
                        "provider": "fake",
                        "model": "fake-embedding",
                        "version": "fake-v1",
                        "dim": 4,
                        "chunk_count": 2,
                        "token_count_min": 10,
                        "token_count_max": 20,
                        "usage": {"text_count": 2},
                        "content": "must not be stored",
                        "vectors": [[1.0, 2.0, 3.0, 4.0]],
                    },
                )
                await repository.commit()

                version = await repository.get_version(tenant_id="tenant-1", version_id="ver-1")
                chunks = await repository.list_chunks_for_version(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-1",
                    status="active",
                )
                documents = await repository.list_documents(tenant_id="tenant-1", status="embedded")

            assert created.status == "queued"
            assert listed == [created]
            assert cross_tenant is None
            assert claim is not None
            assert claim.status == "embedding"
            assert claim.attempt_count == 1
            assert second_claim is None
            assert embedded.status == "embedded"
            assert embedded.chunk_count == 2
            assert version is not None
            assert version.status == "embedded"
            summary = version.metadata["embedding_artifact_summary"]
            assert isinstance(summary, dict)
            assert summary["provider"] == "fake"
            assert summary["dim"] == 4
            assert "content" not in str(summary)
            assert "vectors" not in str(summary)
            assert [document.id for document in documents] == ["doc-1"]
            for chunk in chunks:
                chunk_summary = chunk.metadata["embedding_summary"]
                assert isinstance(chunk_summary, dict)
                assert chunk_summary["dim"] == 4
        finally:
            await engine.dispose()

    asyncio.run(exercise_repository())


def test_document_repository_marks_retrieval_ready_only_after_index_summary_matches_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "retrieval-ready.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repository() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                repository = DocumentRepository(session)
                await repository.create_upload_records(
                    document=_document(status="chunked"),
                    version=_version(status="chunked"),
                    job=_job(status="chunked"),
                )
                await repository.replace_chunks_for_version(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-1",
                    chunks=[
                        _chunk(chunk_id="chunk-1", token_count=10),
                        _chunk(chunk_id="chunk-2", token_count=20),
                    ],
                )
                await repository.create_embedding_job(job=_embedding_job(status="embedding"))
                await repository.mark_embedding_job_embedded(
                    tenant_id="tenant-1",
                    job_id="embed-job-1",
                    embedding_metadata={
                        "stage": "embedded",
                        "provider": "fake",
                        "model": "fake-embedding",
                        "version": "fake-v1",
                        "dim": 4,
                        "chunk_count": 2,
                        "vector_index_summary": {
                            "stage": "vector_indexed",
                            "status": "indexed",
                            "vector_count": 2,
                            "provider": "fake",
                            "model": "fake-embedding",
                            "version": "fake-v1",
                            "dim": 4,
                        },
                    },
                )
                ready = await repository.mark_document_version_retrieval_ready(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-1",
                )
                await repository.commit()
                status = await repository.get_document_version_status(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-1",
                )
                documents = await repository.list_documents(
                    tenant_id="tenant-1",
                    status="retrieval_ready",
                )

            assert ready.status == "retrieval_ready"
            assert status is not None
            assert status.chunk_count == 2
            assert status.vector_count == 2
            assert status.index_status == "indexed"
            assert status.embedding_provider == "fake"
            assert status.job_id == "embed-job-1"
            assert status.attempt_count == 0
            assert status.last_attempt_at is None
            assert status.next_retry_at is None
            assert status.error_summary is None
            assert [document.id for document in documents] == ["doc-1"]
        finally:
            await engine.dispose()

    asyncio.run(exercise_repository())


def test_document_repository_syncs_document_status_from_latest_non_deleted_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "latest-version-status.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repository() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                repository = DocumentRepository(session)
                await repository.create_upload_records(
                    document=_document(status="chunked"),
                    version=_version(status="chunked"),
                    job=_job(status="chunked"),
                )
                await repository.replace_chunks_for_version(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-1",
                    chunks=[_chunk(chunk_id="chunk-1"), _chunk(chunk_id="chunk-2")],
                )
                await repository.create_embedding_job(job=_embedding_job(status="embedding"))
                await repository.mark_embedding_job_embedded(
                    tenant_id="tenant-1",
                    job_id="embed-job-1",
                    embedding_metadata={
                        "stage": "embedded",
                        "provider": "fake",
                        "model": "fake-embedding",
                        "version": "fake-v1",
                        "dim": 4,
                        "chunk_count": 2,
                        "vector_index_summary": {"status": "indexed", "vector_count": 2},
                    },
                )
                await repository.create_document_version_records(
                    version=_version(version_id="ver-2", status="uploaded"),
                    job=_job(version_id="ver-2", job_id="job-2", status="uploaded"),
                )
                await repository.mark_document_version_retrieval_ready(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-1",
                )
                document_after_old_ready = await repository.get_document(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                )
                await repository.soft_delete_document_version(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-2",
                    deleted_by="user-1",
                )
                document_after_latest_delete = await repository.get_document(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                )

            assert document_after_old_ready is not None
            assert document_after_old_ready.status == "uploaded"
            assert document_after_old_ready.metadata["latest_version_id"] == "ver-2"
            assert document_after_latest_delete is not None
            assert document_after_latest_delete.status == "retrieval_ready"
            assert document_after_latest_delete.metadata["latest_version_id"] == "ver-1"
        finally:
            await engine.dispose()

    asyncio.run(exercise_repository())


def test_document_repository_rejects_retrieval_ready_when_vector_count_mismatches_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "retrieval-ready-mismatch.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repository() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                repository = DocumentRepository(session)
                await repository.create_upload_records(
                    document=_document(status="chunked"),
                    version=_version(status="chunked"),
                    job=_job(status="chunked"),
                )
                await repository.replace_chunks_for_version(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-1",
                    chunks=[_chunk(chunk_id="chunk-1"), _chunk(chunk_id="chunk-2")],
                )
                await repository.create_embedding_job(job=_embedding_job(status="embedding"))
                await repository.mark_embedding_job_embedded(
                    tenant_id="tenant-1",
                    job_id="embed-job-1",
                    embedding_metadata={
                        "stage": "embedded",
                        "provider": "fake",
                        "model": "fake-embedding",
                        "version": "fake-v1",
                        "dim": 4,
                        "chunk_count": 2,
                        "vector_index_summary": {"status": "indexed", "vector_count": 1},
                    },
                )
                with pytest.raises(StorageError) as exc_info:
                    await repository.mark_document_version_retrieval_ready(
                        tenant_id="tenant-1",
                        document_id="doc-1",
                        version_id="ver-1",
                    )

            assert exc_info.value.code == "DOCUMENT_INDEX_NOT_READY"
        finally:
            await engine.dispose()

    asyncio.run(exercise_repository())


def test_document_repository_rejects_worker_updates_for_deleted_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "deleted-worker-guard.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repository() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                repository = DocumentRepository(session)
                await repository.create_upload_records(
                    document=_document(status="uploaded"),
                    version=_version(status="uploaded"),
                    job=_job(status="queued"),
                )
                await repository.soft_delete_document_version(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-1",
                    deleted_by="user-1",
                )
                claimed = await repository.claim_ingestion_job_parsing(
                    tenant_id="tenant-1",
                    job_id="job-1",
                    document_id="doc-1",
                    version_id="ver-1",
                    stale_before=datetime.now(tz=UTC) - timedelta(minutes=1),
                )
                with pytest.raises(StorageError) as parsed_error:
                    await repository.mark_ingestion_job_parsed(
                        tenant_id="tenant-1",
                        job_id="job-1",
                        parsed_metadata={"section_count": 1},
                    )

            assert claimed is None
            assert parsed_error.value.code == "DOCUMENT_VERSION_INVALID_STATE"
        finally:
            await engine.dispose()

    asyncio.run(exercise_repository())


def test_document_repository_soft_deletes_version_document_and_chunks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "soft-delete.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repository() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                repository = DocumentRepository(session)
                await repository.create_upload_records(
                    document=_document(status="retrieval_ready"),
                    version=_version(status="retrieval_ready"),
                    job=_job(status="chunked"),
                )
                await repository.create_document_version_records(
                    version=_version(version_id="ver-2", status="retrieval_ready"),
                    job=_job(version_id="ver-2", job_id="job-2", status="chunked"),
                )
                await repository.replace_chunks_for_version(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-1",
                    chunks=[_chunk(chunk_id="chunk-1")],
                )
                await repository.replace_chunks_for_version(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-2",
                    chunks=[_chunk(version_id="ver-2", chunk_id="chunk-2")],
                )
                deleted_chunks = await repository.soft_delete_chunks_for_version(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-1",
                )
                deleted_versions = await repository.soft_delete_document_version(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-1",
                    deleted_by="user-1",
                )
                await repository.commit()
                version_status = await repository.get_document_version_status(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-1",
                )
                remaining_chunks = await repository.list_chunks_for_version(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-1",
                )
                whole_doc_chunks = await repository.soft_delete_chunks_for_version(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    version_id="ver-2",
                )
                whole_doc_versions = await repository.soft_delete_document(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                    deleted_by="user-1",
                )
                await repository.commit()
                document = await repository.get_document(
                    tenant_id="tenant-1",
                    document_id="doc-1",
                )

            assert deleted_chunks == 1
            assert deleted_versions == 1
            assert version_status is not None
            assert version_status.status == "deleted"
            assert version_status.deleted_at is not None
            assert remaining_chunks == []
            assert whole_doc_chunks == 1
            assert whole_doc_versions == 1
            assert document is not None
            assert document.status == "deleted"
            assert document.deleted_at is not None
        finally:
            await engine.dispose()

    asyncio.run(exercise_repository())


def test_document_repository_rejects_embedding_job_document_version_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "embedding-job-mismatch.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repository() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                repository = DocumentRepository(session)
                await repository.create_upload_records(
                    document=_document(document_id="doc-1"),
                    version=_version(document_id="doc-1", version_id="ver-1"),
                    job=_job(document_id="doc-1", version_id="ver-1"),
                )
                await repository.create_upload_records(
                    document=_document(document_id="doc-2"),
                    version=_version(document_id="doc-2", version_id="ver-2"),
                    job=_job(document_id="doc-2", version_id="ver-2", job_id="job-2"),
                )
                await repository.commit()

                with pytest.raises(StorageError) as exc_info:
                    await repository.create_embedding_job(
                        job=_embedding_job(document_id="doc-1", version_id="ver-2")
                    )

            assert exc_info.value.code == "EMBEDDING_JOB_SCOPE_MISMATCH"
        finally:
            await engine.dispose()

    asyncio.run(exercise_repository())


def test_document_repository_respects_embedding_retry_backoff_when_claiming(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "embedding-retry-backoff.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repository() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                repository = DocumentRepository(session)
                await repository.create_upload_records(
                    document=_document(status="chunked"),
                    version=_version(status="chunked"),
                    job=_job(status="chunked"),
                )
                await repository.create_embedding_job(
                    job=_embedding_job(
                        job_id="future-retry",
                        status="failed_retryable",
                        next_retry_at=datetime.now(tz=UTC) + timedelta(minutes=5),
                    )
                )
                await repository.create_embedding_job(
                    job=_embedding_job(
                        job_id="past-retry",
                        status="failed_retryable",
                        next_retry_at=datetime.now(tz=UTC) - timedelta(minutes=5),
                    )
                )
                await repository.commit()

                future_claim = await repository.claim_embedding_job(
                    tenant_id="tenant-1",
                    job_id="future-retry",
                    document_id="doc-1",
                    version_id="ver-1",
                    stale_before=None,
                )
                past_claim = await repository.claim_embedding_job(
                    tenant_id="tenant-1",
                    job_id="past-retry",
                    document_id="doc-1",
                    version_id="ver-1",
                    stale_before=None,
                )

            assert future_claim is None
            assert past_claim is not None
            assert past_claim.status == "embedding"
            assert past_claim.next_retry_at is None
        finally:
            await engine.dispose()

    asyncio.run(exercise_repository())

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.data.dto import DocumentRecord, DocumentVersionRecord, IngestionJobRecord
from packages.data.storage.repositories import DocumentRepository
from packages.vectorstores.adapters.pgvector import PgVectorStore
from packages.vectorstores.dto import AclFilter, VectorRecord, VectorSearchRequest

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _sqlite_async_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def _run_migrations(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    command.upgrade(config, "head")


def test_pgvector_store_sqlite_fallback_upsert_search_delete_smoke(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "vectors.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_store() -> None:
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
                store = PgVectorStore(session, index_dim=3)
                await store.upsert(
                    [
                        _vector_record(chunk_id="chunk-1", vector=[1.0, 0.0, 0.0]),
                        _vector_record(chunk_id="chunk-2", vector=[0.0, 1.0, 0.0]),
                    ]
                )

                results = await store.search(
                    VectorSearchRequest(
                        tenant_id="tenant-1",
                        query_vector=[1.0, 0.0, 0.0],
                        embedding_dim=3,
                        top_k=1,
                        score_threshold=0.8,
                        acl_filter=AclFilter(user_id="user-1", roles=["hr"]),
                    )
                )
                cross_tenant_results = await store.search(
                    VectorSearchRequest(
                        tenant_id="tenant-2",
                        query_vector=[1.0, 0.0, 0.0],
                        embedding_dim=3,
                        top_k=10,
                        acl_filter=AclFilter(user_id="user-1", roles=["hr"]),
                    )
                )
                delete_result = await store.delete_by_document(
                    "doc-1",
                    "ver-1",
                    tenant_id="tenant-1",
                )
                deleted_results = await store.search(
                    VectorSearchRequest(
                        tenant_id="tenant-1",
                        query_vector=[1.0, 0.0, 0.0],
                        embedding_dim=3,
                        top_k=10,
                        acl_filter=AclFilter(user_id="user-1", roles=["hr"]),
                    )
                )

                assert [result.chunk_id for result in results] == ["chunk-1"]
                assert cross_tenant_results == []
                assert delete_result.deleted_count == 2
                assert deleted_results == []
        finally:
            await engine.dispose()

    asyncio.run(exercise_store())


def test_pgvector_store_sqlite_fallback_allows_department_and_permission_acl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "acl-vectors.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_store() -> None:
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
                store = PgVectorStore(session, index_dim=3)
                await store.upsert(
                    [
                        _vector_record(
                            chunk_id="chunk-dept",
                            vector=[1.0, 0.0, 0.0],
                            acl={"visibility": "restricted", "allowed_departments": ["legal"]},
                        ),
                        _vector_record(
                            chunk_id="chunk-permission",
                            vector=[0.9, 0.1, 0.0],
                            acl={
                                "visibility": "restricted",
                                "allowed_permissions": ["documents:read"],
                            },
                        ),
                    ]
                )

                department_results = await store.search(
                    VectorSearchRequest(
                        tenant_id="tenant-1",
                        query_vector=[1.0, 0.0, 0.0],
                        embedding_dim=3,
                        top_k=10,
                        acl_filter=AclFilter(user_id="user-1", department="legal"),
                    )
                )
                permission_results = await store.search(
                    VectorSearchRequest(
                        tenant_id="tenant-1",
                        query_vector=[1.0, 0.0, 0.0],
                        embedding_dim=3,
                        top_k=10,
                        acl_filter=AclFilter(user_id="user-1", permissions=["documents:read"]),
                    )
                )
                denied_results = await store.search(
                    VectorSearchRequest(
                        tenant_id="tenant-1",
                        query_vector=[1.0, 0.0, 0.0],
                        embedding_dim=3,
                        top_k=10,
                        acl_filter=AclFilter(user_id="user-1"),
                    )
                )

                assert [result.chunk_id for result in department_results] == ["chunk-dept"]
                assert [result.chunk_id for result in permission_results] == [
                    "chunk-permission"
                ]
                assert denied_results == []
        finally:
            await engine.dispose()

    asyncio.run(exercise_store())


def _document() -> DocumentRecord:
    return DocumentRecord(
        id="doc-1",
        tenant_id="tenant-1",
        created_by="user-1",
        status="chunked",
        source_type="txt",
        source_uri="kb://policy.txt",
        title="Policy",
        acl={"visibility": "tenant"},
        checksum="checksum-doc-1",
        metadata={},
    )


def _version() -> DocumentVersionRecord:
    return DocumentVersionRecord(
        id="ver-1",
        document_id="doc-1",
        tenant_id="tenant-1",
        created_by="user-1",
        status="chunked",
        source_type="txt",
        source_uri="kb://policy.txt",
        object_key="raw/tenant-1/doc-1/ver-1/policy.txt",
        filename="policy.txt",
        content_type="text/plain",
        byte_size=12,
        acl={"visibility": "tenant"},
        checksum="checksum-ver-1",
        metadata={},
    )


def _job() -> IngestionJobRecord:
    return IngestionJobRecord(
        id="job-1",
        tenant_id="tenant-1",
        created_by="user-1",
        status="chunked",
        document_id="doc-1",
        version_id="ver-1",
        queue_name="ingestion",
    )


def _vector_record(
    *,
    chunk_id: str,
    vector: list[float],
    acl: dict[str, object] | None = None,
) -> VectorRecord:
    return VectorRecord(
        tenant_id="tenant-1",
        document_id="doc-1",
        version_id="ver-1",
        chunk_id=chunk_id,
        created_by="user-1",
        status="active",
        vector=vector,
        embedding_provider="fake",
        embedding_model="fake-embedding",
        embedding_version="fake-v1",
        embedding_dim=3,
        source_type="txt",
        source_uri="kb://policy.txt",
        title_path=["Policy"],
        page_start=1,
        page_end=1,
        token_count=10,
        acl=acl or {"visibility": "tenant", "allowed_roles": ["hr"]},
        checksum=f"checksum-{chunk_id}",
        metadata={"department": "hr"},
    )

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, cast

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.data.storage.base import Base
from packages.data.storage.exceptions import StorageError
from packages.retrieval.dto import RetrievalLogCreate
from packages.retrieval.storage import models as retrieval_models  # noqa: F401
from packages.retrieval.storage.repositories import RetrievalLogRepository


@pytest.fixture
async def session_factory(
    tmp_path: Path,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    database_url = f"sqlite+aiosqlite:///{(tmp_path / 'retrieval_logs.db').as_posix()}"
    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def _record(
    *,
    tenant_id: str = "tenant-1",
    request_id: str = "req-1",
    created_at: datetime | None = None,
    status: Literal["success", "failure"] = "success",
) -> RetrievalLogCreate:
    return RetrievalLogCreate(
        request_id=request_id,
        trace_id="trace-1",
        tenant_id=tenant_id,
        user_id="user-1",
        created_by="user-1",
        status=status,
        latency_ms=12.0,
        top_k=10,
        result_count=2 if status == "success" else 0,
        rerank_score=0.9 if status == "success" else None,
        error_code=None if status == "success" else "RETRIEVAL_BACKEND_FAILED",
        query_summary={"length": 12},
        metadata={
            "candidate_ids": [{"document_id": "doc-1", "version_id": "ver-1", "chunk_id": "c1"}],
            "query": "raw query must redact",
            "sql": "select * from chunks",
        },
        created_at=created_at or datetime(2026, 6, 7, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_retrieval_log_repository_create_and_query_by_request_id(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        repository = RetrievalLogRepository(session)

        created = await repository.create(_record())
        await repository.commit()

        assert created.id
        assert created.request_id == "req-1"
        assert created.metadata["query"] == "[REDACTED]"
        assert created.metadata["sql"] == "[REDACTED]"

    async with session_factory() as session:
        repository = RetrievalLogRepository(session)
        fetched = await repository.get_by_request_id(tenant_id="tenant-1", request_id="req-1")
        assert fetched is not None
        assert fetched.request_id == "req-1"
        assert fetched.tenant_id == "tenant-1"
        assert fetched.result_count == 2


@pytest.mark.asyncio
async def test_retrieval_log_repository_tenant_isolation_and_created_at_ordering(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    base = datetime(2026, 6, 7, tzinfo=UTC)
    async with session_factory() as session:
        repository = RetrievalLogRepository(session)
        await repository.create(_record(created_at=base + timedelta(seconds=2)))
        await repository.create(_record(created_at=base + timedelta(seconds=1), status="failure"))
        await repository.create(_record(tenant_id="tenant-2", created_at=base))
        await repository.commit()

    async with session_factory() as session:
        repository = RetrievalLogRepository(session)
        tenant_1_records = await repository.list_by_request_id(
            tenant_id="tenant-1",
            request_id="req-1",
        )
        tenant_2_records = await repository.list_by_request_id(
            tenant_id="tenant-2",
            request_id="req-1",
        )

    assert [record.status for record in tenant_1_records] == ["failure", "success"]
    assert len(tenant_2_records) == 1
    assert tenant_2_records[0].tenant_id == "tenant-2"


@pytest.mark.asyncio
async def test_retrieval_log_repository_lists_by_created_at_range(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    base = datetime(2026, 6, 7, tzinfo=UTC)
    async with session_factory() as session:
        repository = RetrievalLogRepository(session)
        await repository.create(_record(created_at=base, request_id="req-old"))
        await repository.create(_record(created_at=base + timedelta(hours=1), request_id="req-hit"))
        await repository.create(_record(created_at=base + timedelta(hours=2), request_id="req-new"))
        await repository.create(
            _record(tenant_id="tenant-2", created_at=base + timedelta(hours=1), request_id="req-2")
        )
        await repository.commit()

    async with session_factory() as session:
        repository = RetrievalLogRepository(session)
        records = await repository.list_by_created_at(
            tenant_id="tenant-1",
            created_from=base + timedelta(minutes=30),
            created_to=base + timedelta(hours=1, minutes=30),
        )

    assert [record.request_id for record in records] == ["req-hit"]


def test_retrieval_log_create_rejects_unknown_status() -> None:
    payload: dict[str, object] = {
        "request_id": "req-1",
        "trace_id": "trace-1",
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "created_by": "user-1",
        "status": "succes",
        "latency_ms": 12.0,
        "top_k": 10,
        "result_count": 0,
    }
    with pytest.raises(ValidationError):
        RetrievalLogCreate.model_validate(payload)


class BrokenSession:
    def __init__(self) -> None:
        self.rollbacks = 0

    def add(self, model: object) -> None:
        return None

    async def flush(self) -> None:
        raise SQLAlchemyError("select * from chunks where password='secret'")

    async def rollback(self) -> None:
        self.rollbacks += 1
        return None


@pytest.mark.asyncio
async def test_retrieval_log_repository_storage_error_is_stable() -> None:
    session = BrokenSession()
    repository = RetrievalLogRepository(cast(AsyncSession, session))

    with pytest.raises(StorageError) as exc_info:
        await repository.create(_record())

    error = exc_info.value
    assert error.code == "RETRIEVAL_LOG_STORAGE_WRITE_FAILED"
    assert "select *" not in str(error.details)
    assert "password" not in str(error.details)
    assert session.rollbacks == 1


class BrokenReadSession:
    def __init__(self) -> None:
        self.rollbacks = 0

    def scalars(self, statement: object) -> object:
        raise SQLAlchemyError("select * from chunks where password='secret'")

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_retrieval_log_repository_read_error_rolls_back() -> None:
    session = BrokenReadSession()
    repository = RetrievalLogRepository(cast(AsyncSession, session))

    with pytest.raises(StorageError) as exc_info:
        await repository.list_by_request_id(tenant_id="tenant-1", request_id="req-1")

    assert exc_info.value.code == "RETRIEVAL_LOG_STORAGE_READ_FAILED"
    assert session.rollbacks == 1

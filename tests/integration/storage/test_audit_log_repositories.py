from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.common.audit import AuditEvent, AuditResource, AuditStatus
from packages.data.storage.audit_repositories import AuditLogRepository, AuditLogStorageQuery
from packages.data.storage.exceptions import StorageError

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _sqlite_async_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def _run_migrations(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    command.upgrade(config, "head")


def test_audit_log_repository_lists_tenant_scoped_records_with_filters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "audit-explorer-list.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise_repository() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        base_time = datetime(2026, 6, 9, 10, 0, tzinfo=UTC)
        try:
            async with session_factory() as session:
                repository = AuditLogRepository(session)
                await repository.create(_event("tenant-1", "req-1", "trace-1", base_time))
                await repository.create(
                    _event(
                        "tenant-1",
                        "req-2",
                        "trace-1",
                        base_time + timedelta(minutes=1),
                        action="agent.tool.execute",
                        resource_type="tool_call",
                    )
                )
                await repository.create(
                    _event("tenant-2", "req-2", "trace-1", base_time + timedelta(minutes=2))
                )
                await session.commit()

            async with session_factory() as session:
                repository = AuditLogRepository(session)
                records = await repository.list_records(
                    tenant_id="tenant-1",
                    query=AuditLogStorageQuery(
                        trace_id="trace-1",
                        request_id="req-2",
                        action="agent.tool.execute",
                        created_at_from=base_time,
                        created_at_to=base_time + timedelta(minutes=2),
                        limit=10,
                    ),
                )

            assert [record.request_id for record in records] == ["req-2"]
            assert all(record.tenant_id == "tenant-1" for record in records)
            assert records[0].resource_type == "tool_call"
        finally:
            await engine.dispose()

    asyncio.run(exercise_repository())


@pytest.mark.asyncio
async def test_audit_log_repository_read_error_is_safe() -> None:
    repository = AuditLogRepository(cast(AsyncSession, BrokenSession()))

    with pytest.raises(StorageError) as exc_info:
        await repository.list_records(
            tenant_id="tenant-1",
            query=AuditLogStorageQuery(request_id="req-1"),
        )

    assert exc_info.value.code == "AUDIT_STORAGE_READ_FAILED"
    assert "select *" not in str(exc_info.value.details).lower()
    assert "secret" not in str(exc_info.value.details).lower()


def _event(
    tenant_id: str,
    request_id: str,
    trace_id: str,
    created_at: datetime,
    *,
    action: str = "rag.query",
    resource_type: str = "rag_query",
) -> AuditEvent:
    return AuditEvent(
        request_id=request_id,
        trace_id=trace_id,
        tenant_id=tenant_id,
        user_id="user-1",
        action=action,
        resource=AuditResource(type=resource_type, id=request_id),
        status=AuditStatus.SUCCESS,
        latency_ms=1.0,
        created_at=created_at,
        metadata={"safe": "ok"},
    )


class BrokenSession:
    async def scalars(self, statement: object) -> object:
        _ = statement
        raise SQLAlchemyError("select * from audit_logs where secret='token'")

    async def rollback(self) -> None:
        return None

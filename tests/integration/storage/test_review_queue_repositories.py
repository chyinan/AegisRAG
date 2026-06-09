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

from packages.data.storage.exceptions import StorageError
from packages.data.storage.review_repositories import ReviewItemRepository
from packages.review import ReviewItemCreateRequest, ReviewItemQueryRequest

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _sqlite_async_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def _run_migrations(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    command.upgrade(config, "head")


def test_review_item_repository_filters_by_tenant_request_trace_window_and_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "review-queue.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                repository = ReviewItemRepository(session)
                first = await repository.create_item(
                    tenant_id="tenant-1",
                    created_by="user-1",
                    request=_request("req-1", "trace-1"),
                    status_history=_history("open"),
                )
                second = await repository.create_item(
                    tenant_id="tenant-1",
                    created_by="user-1",
                    request=_request("req-1", "trace-2"),
                    status_history=_history("open"),
                )
                await repository.create_item(
                    tenant_id="tenant-2",
                    created_by="user-2",
                    request=_request("req-1", "trace-1"),
                    status_history=_history("open"),
                )
                await session.commit()

            async with session_factory() as session:
                repository = ReviewItemRepository(session)
                records = await repository.list_items(
                    tenant_id="tenant-1",
                    query=ReviewItemQueryRequest(
                        request_id="req-1",
                        trace_id="trace-1",
                        created_at_from=first.created_at - timedelta(seconds=1),
                        created_at_to=second.created_at + timedelta(seconds=1),
                        limit=10,
                    ),
                )

            assert [record.id for record in records] == [first.id]
            assert all(record.tenant_id == "tenant-1" for record in records)
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def test_review_item_repository_persists_status_history_and_eval_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url = _sqlite_async_url(tmp_path / "review-queue-status.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                repository = ReviewItemRepository(session)
                created = await repository.create_item(
                    tenant_id="tenant-1",
                    created_by="user-1",
                    request=_request("req-2", "trace-2"),
                    status_history=_history("open"),
                )
                updated = await repository.update_status(
                    tenant_id="tenant-1",
                    item_id=created.id,
                    status="converted_to_eval_case",
                    status_history=[
                        *_history("open"),
                        {
                            "status": "converted_to_eval_case",
                            "changed_by": "user-1",
                            "changed_at": datetime(2026, 6, 9, 11, 5, tzinfo=UTC).isoformat(),
                        },
                    ],
                    eval_candidate={
                        "candidate_id": "candidate-1",
                        "source_review_item_id": created.id,
                        "case_type": "low_confidence_citation",
                        "safe_identifiers": {"document_id": "doc-1"},
                        "safe_metric_counts": {"citation_count": 1},
                        "expected_behavior": "Confirm manually.",
                        "request_id": "req-2",
                        "trace_id": "trace-2",
                        "requires_human_confirmation": True,
                    },
                )
                await session.commit()

            assert updated is not None
            assert updated.status == "converted_to_eval_case"
            assert updated.eval_candidate is not None
            assert updated.eval_candidate["requires_human_confirmation"] is True
            assert len(updated.status_history) == 2
        finally:
            await engine.dispose()

    asyncio.run(exercise())


@pytest.mark.asyncio
async def test_review_item_repository_read_error_is_safe() -> None:
    repository = ReviewItemRepository(cast(AsyncSession, BrokenSession()))

    with pytest.raises(StorageError) as exc_info:
        await repository.list_items(
            tenant_id="tenant-1",
            query=ReviewItemQueryRequest(request_id="req-1"),
        )

    assert exc_info.value.code == "REVIEW_QUEUE_STORAGE_READ_FAILED"
    assert "select *" not in str(exc_info.value.details).lower()
    assert "secret" not in str(exc_info.value.details).lower()


def _request(request_id: str, trace_id: str) -> ReviewItemCreateRequest:
    return ReviewItemCreateRequest(
        item_type="low_confidence_citation",
        severity="high",
        request_id=request_id,
        trace_id=trace_id,
        source_view="source_evidence",
        safe_identifiers={"document_id": "doc-1", "chunk_id": "chunk-1"},
        safe_summary={"failure_stage": "citation", "citation_count": 1},
    )


def _history(status: str) -> list[dict[str, object]]:
    return [
        {
            "status": status,
            "changed_by": "user-1",
            "changed_at": datetime(2026, 6, 9, 11, 0, tzinfo=UTC).isoformat(),
        }
    ]


class BrokenSession:
    async def scalars(self, statement: object) -> object:
        _ = statement
        raise SQLAlchemyError("select * from review_items where secret='token'")

    async def rollback(self) -> None:
        return None

"""End-to-end tests: verify create_user / create_group write to the database.

Uses in-memory SQLite + Alembic migrations to validate that the ORM models
flow through to persisted rows.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.auth.group_service import GroupService
from packages.auth.models import LocalUserModel, UserGroupModel
from packages.auth.user_service import UserService

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _sqlite_async_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def _run_migrations(database_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "migrations"))
    command.upgrade(config, "head")


def test_create_group_persists_to_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_group() writes a row that is visible via raw SQLAlchemy query."""
    database_url = _sqlite_async_url(tmp_path / "group-test.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                service = GroupService(session)
                result = await service.create_group(
                    name="Engineering", description="Engineers and dev leads"
                )

                assert isinstance(result, dict)
                assert result["name"] == "Engineering"
                assert result["description"] == "Engineers and dev leads"
                assert result["id"]

                # Verify via raw SQLAlchemy
                stmt = select(UserGroupModel).where(
                    UserGroupModel.id == result["id"]
                )
                row_result = await session.execute(stmt)
                model = row_result.scalar_one_or_none()
                assert model is not None
                assert model.name == "Engineering"
                assert model.description == "Engineers and dev leads"
                assert model.roles is None
                assert model.permissions is None
                assert model.created_at is not None
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def test_create_user_persists_to_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_user() writes a hashed user row visible via raw SQLAlchemy query."""
    database_url = _sqlite_async_url(tmp_path / "user-test.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                service = UserService(session)
                result = await service.create_user(
                    username="alice",
                    password="Str0ng!Pass",
                    email="alice@example.com",
                    display_name="Alice Example",
                    group_id=None,
                )

                assert isinstance(result, dict)
                assert result["username"] == "alice"
                assert result["email"] == "alice@example.com"
                assert result["display_name"] == "Alice Example"
                assert result["id"]
                assert "password_hash" not in result  # hash never leaks

                # Verify via raw SQLAlchemy
                stmt = select(LocalUserModel).where(
                    LocalUserModel.id == result["id"]
                )
                row_result = await session.execute(stmt)
                model = row_result.scalar_one_or_none()
                assert model is not None
                assert model.username == "alice"
                assert model.password_hash.startswith("$2b$")  # bcrypt
                assert model.email == "alice@example.com"
                assert model.display_name == "Alice Example"
                assert model.is_active is True
                assert model.group_id is None
                assert model.created_at is not None
        finally:
            await engine.dispose()

    asyncio.run(exercise())


def test_create_user_with_group_fk_persists_relation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_user(group_id=…) correctly sets the foreign key in the DB."""
    database_url = _sqlite_async_url(tmp_path / "user-group-test.db")
    _run_migrations(database_url, monkeypatch)

    async def exercise() -> None:
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with session_factory() as session:
                group_service = GroupService(session)
                group = await group_service.create_group(
                    name="Researchers", description="Research team"
                )

                user_service = UserService(session)
                user = await user_service.create_user(
                    username="bob",
                    password="Test!2345Pass",
                    email="bob@research.example.com",
                    display_name="Bob Research",
                    group_id=str(group["id"]),
                )

                assert user["group_id"] == group["id"]

                # Verify via raw SQLAlchemy
                stmt = select(LocalUserModel).where(
                    LocalUserModel.id == user["id"]
                )
                row_result = await session.execute(stmt)
                model = row_result.scalar_one_or_none()
                assert model is not None
                assert model.group_id == group["id"]
        finally:
            await engine.dispose()

    asyncio.run(exercise())

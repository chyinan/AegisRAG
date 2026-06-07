from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from packages.common.config import load_settings
from packages.data.storage.exceptions import StorageConfigurationError


def create_async_db_engine(database_url: str | None = None, **kwargs: object) -> AsyncEngine:
    resolved_url = database_url or load_settings().database_url
    if not resolved_url:
        raise StorageConfigurationError(details={"missing": ["DATABASE_URL"]})
    return create_async_engine(resolved_url, pool_pre_ping=True, **kwargs)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def iter_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session

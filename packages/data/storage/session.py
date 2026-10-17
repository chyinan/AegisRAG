from __future__ import annotations

# pattern: Imperative Shell
from collections.abc import AsyncIterator

from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session

from packages.common.config import load_settings
from packages.common.tenant_context import current_tenant_id
from packages.data.storage.exceptions import StorageConfigurationError


def create_async_db_engine(database_url: str | None = None, **kwargs: object) -> AsyncEngine:
    settings = load_settings()
    resolved_url = database_url or settings.database_url
    if not resolved_url:
        raise StorageConfigurationError(details={"missing": ["DATABASE_URL"]})
    # PostgreSQL pools are per engine/process. SQLite in-memory uses StaticPool
    # and does not accept QueuePool sizing options.
    if resolved_url.startswith(("postgresql:", "postgresql+")):
        kwargs.setdefault("pool_size", settings.database_pool_size)
        kwargs.setdefault("max_overflow", settings.database_max_overflow)
        kwargs.setdefault("pool_timeout", settings.database_pool_timeout_seconds)
    return create_async_engine(resolved_url, pool_pre_ping=True, **kwargs)


@event.listens_for(Session, "after_begin")
def _set_postgres_tenant_context(
    session: Session,
    transaction: object,
    connection: Connection,
) -> None:
    del session, transaction
    dialect = getattr(connection, "dialect", None)
    if getattr(dialect, "name", None) != "postgresql":
        return
    tenant_id = current_tenant_id()
    if tenant_id is None:
        return
    connection.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": tenant_id},
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def iter_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session

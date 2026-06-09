from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from packages.agent.storage import models as agent_models  # noqa: F401
from packages.auth.storage import models as auth_models  # noqa: F401
from packages.common.config import load_settings
from packages.data.storage import (
    audit_models,  # noqa: F401
    review_models,  # noqa: F401
)
from packages.data.storage import models as data_models  # noqa: F401
from packages.data.storage.base import Base
from packages.retrieval.storage import models as retrieval_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    configured_url = load_settings().database_url or config.get_main_option("sqlalchemy.url")
    if not configured_url or configured_url.startswith("driver://"):
        raise RuntimeError("DATABASE_URL must be configured before running migrations.")
    return configured_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _database_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

"""Database-backed local JWT session validation."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.auth.context import AuthContext
from packages.auth.models import LocalUserModel
from packages.auth.storage.models import TenantModel
from packages.common.errors import DomainError
from packages.data.storage.base import Base


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database_session:
        database_session.add_all(
            [
                TenantModel(id="tenant-a", name="Tenant A", status="active"),
                LocalUserModel(
                    id="user-a",
                    tenant_id="tenant-a",
                    username="alice",
                    password_hash="unused",
                    display_name="Alice",
                    auth_version=2,
                ),
            ]
        )
        await database_session.commit()
        yield database_session
    await engine.dispose()


def _local_auth(auth_version: int) -> AuthContext:
    return AuthContext(
        user_id="user-a",
        tenant_id="tenant-a",
        identity_provider="local",
        auth_version=auth_version,
    )


async def test_local_session_validator_rejects_stale_auth_version(
    session: AsyncSession,
) -> None:
    from packages.auth.session_validation import LocalAuthSessionValidator

    with pytest.raises(DomainError) as exc_info:
        await LocalAuthSessionValidator(session).validate(_local_auth(auth_version=1))

    assert exc_info.value.code == "AUTH_CONTEXT_INVALID"


async def test_local_session_validator_accepts_current_auth_version(
    session: AsyncSession,
) -> None:
    from packages.auth.session_validation import LocalAuthSessionValidator

    await LocalAuthSessionValidator(session).validate(_local_auth(auth_version=2))

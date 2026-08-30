"""Tenant-bound local login and refresh behavior."""

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.auth.login_service import LoginService
from packages.auth.models import LocalUserModel, UserGroupModel
from packages.auth.revocation import InMemoryTokenRevocationStore
from packages.auth.storage.models import TenantModel
from packages.common.errors import DomainError
from packages.data.storage.base import Base


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as database_session:
        yield database_session
    await engine.dispose()


def _password_hash(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


async def _seed_same_username_in_two_tenants(session: AsyncSession) -> None:
    session.add_all(
        [
            TenantModel(id="tenant-a", name="Tenant A", status="active"),
            TenantModel(id="tenant-b", name="Tenant B", status="active"),
            UserGroupModel(
                id="group-a",
                tenant_id="tenant-a",
                name="Readers",
                roles='["reader-a"]',
                permissions='["document:read"]',
            ),
            UserGroupModel(
                id="group-b",
                tenant_id="tenant-b",
                name="Readers",
                roles='["reader-b"]',
                permissions='["document:read"]',
            ),
            LocalUserModel(
                id="user-a",
                tenant_id="tenant-a",
                username="alice",
                password_hash=_password_hash("TenantA-Secret1!"),
                display_name="Alice A",
                group_id="group-a",
            ),
            LocalUserModel(
                id="user-b",
                tenant_id="tenant-b",
                username="alice",
                password_hash=_password_hash("TenantB-Secret1!"),
                display_name="Alice B",
                group_id="group-b",
            ),
        ]
    )
    await session.commit()


async def test_login_resolves_same_username_inside_explicit_tenant(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jwt_secret = "tenant-login-test-secret-at-least-32-bytes"
    monkeypatch.setenv("JWT_SECRET", jwt_secret)
    await _seed_same_username_in_two_tenants(session)

    result = await LoginService(session).login(
        tenant_id="tenant-b",
        username="alice",
        password="TenantB-Secret1!",
    )

    assert result.user_id == "user-b"
    assert result.tenant_id == "tenant-b"
    assert result.roles == ["reader-b"]
    access_claims = jwt.decode(result.access_token, jwt_secret, algorithms=["HS256"])
    assert access_claims["identity_provider"] == "local"
    assert access_claims["auth_version"] == 1


async def test_refresh_rejects_token_when_tenant_no_longer_matches_user(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jwt_secret = "tenant-login-test-secret-at-least-32-bytes"
    monkeypatch.setenv("JWT_SECRET", jwt_secret)
    await _seed_same_username_in_two_tenants(session)
    now = datetime.now(tz=UTC)
    forged_tenant_refresh = jwt.encode(
        {
            "sub": "user-a",
            "user_id": "user-a",
            "tenant_id": "tenant-b",
            "type": "refresh",
            "jti": "refresh-user-a",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        jwt_secret,
        algorithm="HS256",
    )

    with pytest.raises(DomainError) as exc_info:
        await LoginService(session).refresh(refresh_token=forged_tenant_refresh)

    assert exc_info.value.code == "AUTH_INVALID_TOKEN"


async def test_refresh_requires_tenant_claim(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jwt_secret = "tenant-login-test-secret-at-least-32-bytes"
    monkeypatch.setenv("JWT_SECRET", jwt_secret)
    await _seed_same_username_in_two_tenants(session)
    now = datetime.now(tz=UTC)
    refresh_without_tenant = jwt.encode(
        {
            "sub": "user-a",
            "user_id": "user-a",
            "type": "refresh",
            "jti": "refresh-user-a-without-tenant",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        jwt_secret,
        algorithm="HS256",
    )

    with pytest.raises(DomainError) as exc_info:
        await LoginService(session).refresh(refresh_token=refresh_without_tenant)

    assert exc_info.value.code == "AUTH_INVALID_TOKEN"


async def test_refresh_rejects_stale_auth_version(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jwt_secret = "tenant-login-test-secret-at-least-32-bytes"
    monkeypatch.setenv("JWT_SECRET", jwt_secret)
    await _seed_same_username_in_two_tenants(session)
    now = datetime.now(tz=UTC)
    stale_refresh = jwt.encode(
        {
            "sub": "user-a",
            "user_id": "user-a",
            "tenant_id": "tenant-a",
            "identity_provider": "local",
            "auth_version": 0,
            "type": "refresh",
            "jti": "refresh-user-a-stale",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        jwt_secret,
        algorithm="HS256",
    )

    with pytest.raises(DomainError) as exc_info:
        await LoginService(session).refresh(refresh_token=stale_refresh)

    assert exc_info.value.code == "AUTH_INVALID_TOKEN"


async def test_refresh_rejects_revoked_refresh_token(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jwt_secret = "tenant-login-test-secret-at-least-32-bytes"
    monkeypatch.setenv("JWT_SECRET", jwt_secret)
    await _seed_same_username_in_two_tenants(session)
    store = InMemoryTokenRevocationStore()
    service = LoginService(session, revocation_store=store)
    result = await service.login(
        tenant_id="tenant-a",
        username="alice",
        password="TenantA-Secret1!",
    )
    claims = jwt.decode(result.refresh_token, jwt_secret, algorithms=["HS256"])
    assert isinstance(claims["jti"], str)
    assert isinstance(claims["exp"], int | float)
    await store.revoke(
        claims["jti"],
        datetime.fromtimestamp(float(claims["exp"]), tz=UTC),
    )

    with pytest.raises(DomainError) as exc_info:
        await service.refresh(refresh_token=result.refresh_token)

    assert exc_info.value.code == "AUTH_TOKEN_REVOKED"

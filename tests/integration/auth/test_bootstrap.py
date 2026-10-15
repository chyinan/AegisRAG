"""SQLite transaction + ASGI coverage; does not certify PostgreSQL RLS or Redis."""

from collections.abc import AsyncIterator

import bcrypt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.auth import bootstrap
from packages.auth.bootstrap import (
    ADMIN_PERMISSIONS,
    BOOTSTRAP_METADATA_KEY,
    BootstrapRequest,
    bootstrap_admin,
)
from packages.auth.login_service import LoginService
from packages.auth.models import LocalUserModel, UserGroupModel
from packages.auth.revocation import InMemoryTokenRevocationStore
from packages.auth.storage.models import RoleModel, TenantModel, UserModel
from packages.auth.user_service import UserService
from packages.common.errors import DomainError
from packages.common.tenant_context import current_tenant_id
from packages.data.storage.base import Base
from packages.data.storage.session import create_session_factory

PASSWORD = "Bootstrap-Secret42!"
REQUEST = BootstrapRequest("tenant-a", "Tenant A", "admin", PASSWORD)
TABLES = [
    model.__table__
    for model in (
        TenantModel,
        UserGroupModel,
        LocalUserModel,
        UserModel,
        RoleModel,
    )
]


@pytest.fixture
async def factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite://")

    @event.listens_for(engine.sync_engine, "connect")
    def enforce_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=TABLES))
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


async def test_bootstrap_creates_tenant_and_admin_atomically(factory) -> None:
    async with factory() as session:
        result = await bootstrap_admin(session, REQUEST)
    assert result.created is True
    assert current_tenant_id() is None
    async with factory() as session:
        tenant = await session.get(TenantModel, REQUEST.tenant_id)
        user = await session.get(LocalUserModel, result.user_id)
        group = await session.get(UserGroupModel, result.group_id)
        assert tenant.metadata_[BOOTSTRAP_METADATA_KEY] == {
            "user_id": result.user_id,
            "group_id": result.group_id,
        }
        assert user.group_id == group.id
        assert user.tenant_id == group.tenant_id == tenant.id
        assert user.is_active is True
        assert set(group.get_permissions()) == set(ADMIN_PERMISSIONS)
        assert bcrypt.checkpw(PASSWORD.encode(), user.password_hash.encode())
        assert user.password_hash != PASSWORD


async def test_rerun_does_not_reset_password_or_duplicate_identity(factory) -> None:
    async with factory() as session:
        first = await bootstrap_admin(session, REQUEST)
    async with factory() as session:
        original_hash = (await session.get(LocalUserModel, first.user_id)).password_hash
    different = BootstrapRequest("tenant-a", "Tenant A", "admin", "Different-Secret42!")
    async with factory() as session:
        second = await bootstrap_admin(session, different)
    assert second.created is False
    assert second.user_id == first.user_id
    async with factory() as session:
        user = await session.get(LocalUserModel, first.user_id)
        assert user.password_hash == original_hash
        assert not bcrypt.checkpw(different.password.encode(), user.password_hash.encode())
        for model in (TenantModel, UserGroupModel, LocalUserModel):
            assert await session.scalar(select(func.count()).select_from(model)) == 1


async def test_exact_empty_migration_tenant_can_be_initialized(factory) -> None:
    async with factory() as session:
        session.add(TenantModel(id="default", name="Default Tenant", metadata_={}))
        await session.commit()
    async with factory() as session:
        result = await bootstrap_admin(
            session,
            BootstrapRequest("default", "Default Tenant", "admin", PASSWORD),
        )
        assert result.created is True


@pytest.mark.parametrize("kind", ["local_user", "group", "external_user", "role"])
async def test_bootstrap_refuses_to_adopt_unrelated_existing_identities(factory, kind) -> None:
    async with factory() as session:
        session.add(TenantModel(id=REQUEST.tenant_id, name=REQUEST.tenant_name, metadata_={}))
        await session.flush()
        models = {
            "local_user": LocalUserModel(
                id="existing",
                tenant_id=REQUEST.tenant_id,
                username="admin",
                display_name="Existing",
                password_hash="untouched",
            ),
            "group": UserGroupModel(
                id="existing",
                tenant_id=REQUEST.tenant_id,
                name="Bootstrap Administrators",
                roles='["viewer"]',
                permissions='["document:read"]',
            ),
            "external_user": UserModel(
                id="existing",
                tenant_id=REQUEST.tenant_id,
                external_id="subject",
                display_name="Existing",
            ),
            "role": RoleModel(id="existing", tenant_id=REQUEST.tenant_id, name="Reader"),
        }
        session.add(models[kind])
        await session.commit()
    async with factory() as session:
        with pytest.raises(DomainError) as exc_info:
            await bootstrap_admin(session, REQUEST)
        assert exc_info.value.code == "AUTH_BOOTSTRAP_CONFLICT"
    async with factory() as session:
        assert (await session.get(TenantModel, REQUEST.tenant_id)).metadata_ == {}
        if kind == "local_user":
            user = await session.get(LocalUserModel, "existing")
            assert user.password_hash == "untouched"
            assert user.group_id is None
        elif kind == "group":
            assert (await session.get(UserGroupModel, "existing")).get_roles() == ["viewer"]


@pytest.mark.parametrize("change", ["inactive", "permissions", "username", "group", "marker"])
async def test_rerun_fails_closed_on_changed_bootstrap_state(factory, change) -> None:
    async with factory() as session:
        result = await bootstrap_admin(session, REQUEST)
    async with factory() as session:
        user = await session.get(LocalUserModel, result.user_id)
        if change == "inactive":
            user.is_active = False
        elif change == "permissions":
            (await session.get(UserGroupModel, result.group_id)).permissions = "[]"
        elif change == "username":
            user.username = "renamed"
        elif change == "group":
            user.group_id = None
        else:
            (await session.get(TenantModel, REQUEST.tenant_id)).metadata_ = {
                BOOTSTRAP_METADATA_KEY: "invalid",
            }
        await session.commit()
    async with factory() as session:
        with pytest.raises(DomainError) as exc_info:
            await bootstrap_admin(session, REQUEST)
        assert exc_info.value.code == "AUTH_BOOTSTRAP_CONFLICT"
    assert current_tenant_id() is None


async def test_failure_rolls_back_tenant_group_user_and_marker(factory, monkeypatch) -> None:
    def fail_hash(_password):
        assert current_tenant_id() == REQUEST.tenant_id
        raise DomainError(code="AUTH_WEAK_PASSWORD", message="Weak password.", status_code=422)

    monkeypatch.setattr(bootstrap, "hash_password", fail_hash)
    async with factory() as session:
        with pytest.raises(DomainError):
            await bootstrap_admin(session, REQUEST)
    async with factory() as session:
        for model in (TenantModel, UserGroupModel, LocalUserModel):
            assert await session.scalar(select(func.count()).select_from(model)) == 0
    assert current_tenant_id() is None


async def test_tenant_name_collision_does_not_leave_partial_bootstrap(factory) -> None:
    async with factory() as session:
        session.add(TenantModel(id="other", name=REQUEST.tenant_name))
        await session.commit()
    async with factory() as session:
        with pytest.raises(DomainError) as exc_info:
            await bootstrap_admin(session, REQUEST)
        assert exc_info.value.code == "AUTH_BOOTSTRAP_CONFLICT"
    async with factory() as session:
        assert await session.get(TenantModel, REQUEST.tenant_id) is None
        assert await session.scalar(select(func.count()).select_from(LocalUserModel)) == 0


async def test_bootstrapped_admin_can_login_and_access_authenticated_admin_route(
    factory,
    monkeypatch,
) -> None:
    from packages.common import tracing

    monkeypatch.setattr(tracing, "OTEL_AVAILABLE", False)
    from apps.api import dependencies
    from apps.api.main import create_app
    from apps.api.routes.auth import get_login_service
    from apps.api.routes.users import get_user_service

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("JWT_SECRET", "test-bootstrap-jwt-secret-at-least-32-bytes")
    monkeypatch.setenv("JWT_REQUIRE_LOCAL_SESSION", "true")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite://")
    for name in ("REDIS_URL", "JWT_JWKS_URL", "JWT_AUDIENCE", "JWT_ISSUER"):
        monkeypatch.delenv(name, raising=False)
    store = InMemoryTokenRevocationStore()
    monkeypatch.setattr(dependencies, "get_token_revocation_store", lambda: store)
    monkeypatch.setattr(dependencies, "_cached_session_factory", lambda _url: factory)

    async with factory() as session:
        result = await bootstrap_admin(session, REQUEST)

    async def login_service():
        async with factory() as session:
            yield LoginService(session, revocation_store=store)

    async def user_service():
        async with factory() as session:
            yield UserService(session)

    app = create_app()
    app.dependency_overrides[get_login_service] = login_service
    app.dependency_overrides[get_user_service] = user_service
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        login = await client.post(
            "/v1/auth/login",
            json={
                "tenant_id": REQUEST.tenant_id,
                "username": REQUEST.username,
                "password": PASSWORD,
            },
        )
        assert login.status_code == 200
        payload = login.json()["data"]
        assert payload["user_id"] == result.user_id
        assert "admin:settings" in payload["permissions"]
        assert PASSWORD not in login.text
        users = await client.get(
            "/v1/users",
            headers={
                "Authorization": f"Bearer {payload['access_token']}",
            },
        )
        assert users.status_code == 200
        assert [row["id"] for row in users.json()["data"]] == [result.user_id]
        wrong_tenant = await client.post(
            "/v1/auth/login",
            json={
                "tenant_id": "tenant-other",
                "username": REQUEST.username,
                "password": PASSWORD,
            },
        )
        assert wrong_tenant.status_code == 401

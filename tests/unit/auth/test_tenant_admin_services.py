"""Tenant isolation for local user and group administration services."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.auth.group_service import GroupService
from packages.auth.models import LocalUserModel, UserGroupModel
from packages.auth.storage.models import TenantModel
from packages.auth.user_service import UserService
from packages.common.errors import DomainError
from packages.data.storage.base import Base


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as database_session:
        yield database_session
    await engine.dispose()


async def _seed_tenants_and_groups(session: AsyncSession) -> None:
    session.add_all(
        [
            TenantModel(id="tenant-a", name="Tenant A", status="active"),
            TenantModel(id="tenant-b", name="Tenant B", status="active"),
            UserGroupModel(id="group-a", tenant_id="tenant-a", name="Readers"),
            UserGroupModel(id="group-b", tenant_id="tenant-b", name="Readers"),
            LocalUserModel(
                id="user-a",
                tenant_id="tenant-a",
                username="alice",
                password_hash="unused",
                display_name="Alice A",
                group_id="group-a",
            ),
            LocalUserModel(
                id="user-b",
                tenant_id="tenant-b",
                username="alice",
                password_hash="unused",
                display_name="Alice B",
                group_id="group-b",
            ),
        ]
    )
    await session.commit()


async def test_admin_lists_are_tenant_scoped(session: AsyncSession) -> None:
    await _seed_tenants_and_groups(session)

    groups = await GroupService(session).list_groups(tenant_id="tenant-a")
    users = await UserService(session).list_users(tenant_id="tenant-a")

    assert [group["id"] for group in groups] == ["group-a"]
    assert [user["id"] for user in users] == ["user-a"]


async def test_create_user_rejects_group_from_another_tenant(session: AsyncSession) -> None:
    await _seed_tenants_and_groups(session)

    with pytest.raises(DomainError) as exc_info:
        await UserService(session).create_user(
            tenant_id="tenant-a",
            username="mallory",
            password="TenantA-Secret2!",
            email=None,
            display_name="Mallory",
            group_id="group-b",
        )

    assert exc_info.value.code == "AUTH_GROUP_NOT_FOUND"


async def test_delete_group_unassigns_only_users_in_same_tenant(session: AsyncSession) -> None:
    await _seed_tenants_and_groups(session)

    await GroupService(session).delete_group(tenant_id="tenant-a", group_id="group-a")

    user_a = await session.scalar(select(LocalUserModel).where(LocalUserModel.id == "user-a"))
    user_b = await session.scalar(select(LocalUserModel).where(LocalUserModel.id == "user-b"))
    assert user_a is not None and user_a.group_id is None
    assert user_b is not None and user_b.group_id == "group-b"
    assert user_a.auth_version == 2
    assert user_b.auth_version == 1

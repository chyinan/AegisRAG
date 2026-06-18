"""Test the seed module for local auth data."""

from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from packages.auth.models import LocalUserModel, UserGroupModel
from packages.auth.seed import seed
from packages.data.storage.base import Base


@pytest.fixture
async def session() -> AsyncSession:
    """Create an in-memory SQLite database for each test."""
    engine = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as s:
        yield s
    await engine.dispose()


async def test_seed_creates_3_groups(session: AsyncSession) -> None:
    result = await seed(session)
    assert len(result["created_groups"]) == 3
    assert len(result["created_users"]) == 5

    # Verify groups exist
    groups_result = await session.execute(select(UserGroupModel))
    groups = groups_result.scalars().all()
    group_names = {g.name for g in groups}
    assert group_names == {"Administrators", "Editors", "Viewers"}


async def test_seed_creates_5_users_with_bcrypt_hashes(session: AsyncSession) -> None:
    result = await seed(session)
    assert len(result["created_users"]) == 5

    # Verify users exist with hashed passwords
    users_result = await session.execute(select(LocalUserModel))
    users = users_result.scalars().all()
    usernames = {u.username for u in users}
    assert usernames == {"admin", "editor1", "editor2", "viewer1", "viewer2"}

    # Verify passwords are bcrypt hashes (not plain text)
    for user in users:
        assert user.password_hash.startswith("$2b$") or user.password_hash.startswith("$2a$")


async def test_seed_is_idempotent(session: AsyncSession) -> None:
    first = await seed(session)
    second = await seed(session)

    assert first["created_groups"] == ["Administrators", "Editors", "Viewers"]
    assert second["created_groups"] == []
    assert second["skipped_groups"] == 3
    assert second["skipped_users"] == 5

    # Still only 3 groups and 5 users
    groups = await session.execute(select(UserGroupModel))
    assert len(groups.scalars().all()) == 3
    users = await session.execute(select(LocalUserModel))
    assert len(users.scalars().all()) == 5


async def test_users_are_assigned_to_correct_groups(session: AsyncSession) -> None:
    await seed(session)

    users_result = await session.execute(select(LocalUserModel))
    users = {u.username: u for u in users_result.scalars().all()}

    groups_result = await session.execute(select(UserGroupModel))
    groups = {g.name: g for g in groups_result.scalars().all()}

    # Admin is in Administrators
    assert users["admin"].group_id == groups["Administrators"].id
    # Editors are in Editors
    assert users["editor1"].group_id == groups["Editors"].id
    assert users["editor2"].group_id == groups["Editors"].id
    # Viewers are in Viewers
    assert users["viewer1"].group_id == groups["Viewers"].id
    assert users["viewer2"].group_id == groups["Viewers"].id


async def test_seed_groups_have_roles_and_permissions(session: AsyncSession) -> None:
    await seed(session)

    groups_result = await session.execute(select(UserGroupModel))
    groups = {g.name: g for g in groups_result.scalars().all()}

    # Administrators should have admin roles + full permissions
    admin_group = groups["Administrators"]
    assert "admin" in admin_group.get_roles()
    assert "admin:settings" in admin_group.get_permissions()

    # Editors should have knowledge_manager roles
    editor_group = groups["Editors"]
    assert "editor" in editor_group.get_roles() or "knowledge_manager" in editor_group.get_roles()
    assert "document:read" in editor_group.get_permissions()

    # Viewers should have basic roles
    viewer_group = groups["Viewers"]
    assert "viewer" in viewer_group.get_roles() or "employee" in viewer_group.get_roles()
    assert "document:read" in viewer_group.get_permissions()


async def test_seed_generates_random_passwords(session: AsyncSession) -> None:
    result = await seed(session)
    passwords = result.get("passwords", {})
    assert isinstance(passwords, dict)
    # 5 users should have 5 generated passwords
    assert len(passwords) == 5
    # Passwords should be at least 16 chars (token_urlsafe(16) produces ~22 chars)
    for pwd in passwords.values():
        assert isinstance(pwd, str)
        assert len(pwd) >= 16

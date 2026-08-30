"""Seed data for enterprise login — 5 users + 3 groups.

Usage::

    python packages/auth/seed.py

Requires DATABASE_URL to be set (reads from environment via load_settings).
"""

# pattern: Imperative Shell

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
from typing import TypedDict

import bcrypt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.auth.models import LocalUserModel, UserGroupModel
from packages.common.config import load_settings
from packages.data.storage.session import create_async_db_engine, create_session_factory

# ── Seed data ──


class SeedGroup(TypedDict):
    name: str
    description: str
    roles: str
    permissions: str


SEED_GROUPS: list[SeedGroup] = [
    {
        "name": "Administrators",
        "description": "Full system access",
        "roles": json.dumps(["admin", "platform_admin"]),
        "permissions": json.dumps([
            "document:read", "document:upload", "document:manage",
            "retrieval:query", "diagnostics:read", "eval:read",
            "audit:read", "audit:export", "review:read",
            "agent:run", "admin:settings",
        ]),
    },
    {
        "name": "Editors",
        "description": "Content editing access",
        "roles": json.dumps(["editor", "knowledge_manager"]),
        "permissions": json.dumps([
            "document:read", "document:upload", "document:manage",
            "retrieval:query", "review:read",
        ]),
    },
    {
        "name": "Viewers",
        "description": "Read-only access",
        "roles": json.dumps(["viewer", "employee"]),
        "permissions": json.dumps([
            "document:read", "retrieval:query",
        ]),
    },
]

SEED_USERS: list[dict[str, str]] = [
    {"username": "admin", "display_name": "Admin User", "group": "Administrators"},
    {"username": "editor1", "display_name": "Editor One", "group": "Editors"},
    {"username": "editor2", "display_name": "Editor Two", "group": "Editors"},
    {"username": "viewer1", "display_name": "Viewer One", "group": "Viewers"},
    {"username": "viewer2", "display_name": "Viewer Two", "group": "Viewers"},
]


async def seed(session: AsyncSession, *, tenant_id: str) -> dict[str, object]:
    """Populate user_groups and local_users tables with seed data (idempotent)."""
    created_groups: list[str] = []
    created_users: list[str] = []
    skipped_groups: int = 0
    skipped_users: int = 0
    passwords: dict[str, str] = {}

    # ── Seed groups ──
    for group_data in SEED_GROUPS:
        name = group_data["name"]
        existing = await session.execute(
            select(UserGroupModel).where(
                UserGroupModel.tenant_id == tenant_id,
                UserGroupModel.name == name,
            )
        )
        if existing.scalar_one_or_none() is not None:
            skipped_groups += 1
            continue

        model = UserGroupModel(
            tenant_id=tenant_id,
            name=name,
            description=group_data["description"],
            roles=group_data["roles"],
            permissions=group_data["permissions"],
        )
        session.add(model)
        created_groups.append(name)

    if created_groups:
        await session.flush()

    # ── Resolve group IDs ──
    group_map: dict[str, str] = {}
    result = await session.execute(
        select(UserGroupModel).where(UserGroupModel.tenant_id == tenant_id)
    )
    for group_model in result.scalars().all():
        group_map[group_model.name] = group_model.id

    # ── Seed users ──
    for user_data in SEED_USERS:
        username = user_data["username"]
        existing = await session.execute(
            select(LocalUserModel).where(
                LocalUserModel.tenant_id == tenant_id,
                LocalUserModel.username == username,
            )
        )
        if existing.scalar_one_or_none() is not None:
            skipped_users += 1
            continue

        raw_password = _seed_password(username)
        passwords[username] = raw_password
        password_hash = bcrypt.hashpw(
            raw_password.encode("utf-8"),
            bcrypt.gensalt(),
        ).decode("utf-8")

        user_model = LocalUserModel(
            tenant_id=tenant_id,
            username=username,
            password_hash=password_hash,
            display_name=user_data["display_name"],
            group_id=group_map.get(user_data["group"]),
        )
        session.add(user_model)
        created_users.append(username)

    await session.commit()

    return {
        "created_groups": created_groups,
        "created_users": created_users,
        "skipped_groups": skipped_groups,
        "skipped_users": skipped_users,
        "passwords": passwords,
    }


def _seed_password(username: str) -> str:
    env_key = f"SEED_PASSWORD_{username.upper()}"
    env_value = os.getenv(env_key)
    if env_value and len(env_value) >= 12:
        return env_value
    return secrets.token_urlsafe(16)


async def _main() -> None:
    settings = load_settings()
    if not settings.database_url:
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        sys.exit(1)

    tenant_id = os.getenv("SEED_TENANT_ID", "").strip()
    if not tenant_id:
        print("ERROR: SEED_TENANT_ID is not set.", file=sys.stderr)
        sys.exit(1)

    engine = create_async_db_engine(settings.database_url)
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        result = await seed(session, tenant_id=tenant_id)
        print(f"Seeded groups: {result['created_groups']}")
        print(f"Seeded users: {result['created_users']}")
        msg = (
            f"Skipped {result['skipped_groups']} existing groups, "
            f"{result['skipped_users']} existing users."
        )
        print(msg)
        passwords_raw: object = result.get("passwords", {})
        if isinstance(passwords_raw, dict):
            print("\nGenerated passwords (store securely!):")
            for uname, pwd in passwords_raw.items():
                print(f"  {uname}: {pwd}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())

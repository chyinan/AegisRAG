"""Login service — authenticates local users with bcrypt and issues JWTs."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.auth.models import LocalUserModel, UserGroupModel
from packages.auth.parsers import JwtAuthSettings
from packages.common.errors import DomainError


@dataclass(frozen=True)
class LoginResult:
    access_token: str
    user_id: str
    display_name: str
    tenant_id: str
    roles: tuple[str, ...]
    permissions: tuple[str, ...]


class LoginService:
    """Authenticates a local user by username/password and returns a JWT."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def login(self, *, username: str, password: str) -> LoginResult:
        """Verify credentials and return LoginResult with JWT and user info."""
        user = await self._find_user(username)
        if user is None:
            raise DomainError(
                code="AUTH_INVALID_CREDENTIALS",
                message="Invalid username or password.",
                status_code=401,
            )

        password_bytes = password.encode("utf-8")
        hash_bytes = user.password_hash.encode("utf-8")
        if not bcrypt.checkpw(password_bytes, hash_bytes):
            raise DomainError(
                code="AUTH_INVALID_CREDENTIALS",
                message="Invalid username or password.",
                status_code=401,
            )

        if not user.is_active:
            raise DomainError(
                code="AUTH_INVALID_CREDENTIALS",
                message="Invalid username or password.",
                status_code=401,
            )

        settings = JwtAuthSettings.from_environment()
        if not settings.secret:
            raise DomainError(
                code="AUTH_JWT_NOT_CONFIGURED",
                message="JWT signing secret is not configured.",
                status_code=500,
            )

        # Load group roles/permissions
        roles: tuple[str, ...] = ()
        permissions: tuple[str, ...] = ()
        if user.group_id is not None:
            group_result = await self._session.execute(
                select(UserGroupModel).where(UserGroupModel.id == user.group_id)
            )
            group = group_result.scalar_one_or_none()
            if group is not None:
                roles = tuple(group.get_roles())
                permissions = tuple(group.get_permissions())

        tenant_id = self._resolve_tenant_id()

        now = datetime.now(tz=UTC)
        claims: dict[str, object] = {
            "sub": user.id,
            "user_id": user.id,
            "tenant_id": tenant_id,
            "display_name": user.display_name,
            "roles": list(roles),
            "permissions": list(permissions),
            "iat": now,
            "exp": now + timedelta(hours=24),
        }

        token = jwt.encode(
            claims,
            settings.secret,
            algorithm=settings.algorithm,
        )

        return LoginResult(
            access_token=token,
            user_id=user.id,
            display_name=user.display_name,
            tenant_id=tenant_id,
            roles=roles,
            permissions=permissions,
        )

    async def _find_user(self, username: str) -> LocalUserModel | None:
        result = await self._session.execute(
            select(LocalUserModel).where(LocalUserModel.username == username)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _resolve_tenant_id() -> str:
        env_value = os.getenv("TENANT_ID", "").strip()
        if env_value:
            return env_value
        return "default"

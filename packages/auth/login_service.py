"""Login service — authenticates local users with bcrypt and issues JWTs."""

# pattern: Imperative Shell

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.auth.models import LocalUserModel, UserGroupModel
from packages.auth.parsers import JwtAuthSettings
from packages.common.errors import DomainError


def _generate_jti() -> str:
    return uuid.uuid4().hex


@dataclass(frozen=True)
class LoginResult:
    access_token: str
    refresh_token: str
    expires_in: int
    user_id: str
    display_name: str
    tenant_id: str
    roles: list[str]
    permissions: list[str]


class LoginService:
    """Authenticates a local user by username/password and returns a JWT."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def login(self, *, tenant_id: str, username: str, password: str) -> LoginResult:
        """Verify credentials and return LoginResult with JWT and user info."""
        normalized_tenant_id = _required_identifier(tenant_id, field_name="tenant_id")
        user = await self._find_user(tenant_id=normalized_tenant_id, username=username)
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
        roles: list[str] = []
        permissions: list[str] = []
        if user.group_id is not None:
            group_result = await self._session.execute(
                select(UserGroupModel).where(
                    UserGroupModel.tenant_id == user.tenant_id,
                    UserGroupModel.id == user.group_id,
                )
            )
            group = group_result.scalar_one_or_none()
            if group is not None:
                roles = group.get_roles()
                permissions = group.get_permissions()

        resolved_tenant_id = user.tenant_id

        access_expiry = int(os.getenv("JWT_ACCESS_EXPIRY_SECONDS", "3600"))
        refresh_expiry = int(os.getenv("JWT_REFRESH_EXPIRY_SECONDS", "604800"))

        now = datetime.now(tz=UTC)
        access_jti = _generate_jti()
        access_claims: dict[str, object] = {
            "sub": user.id,
            "user_id": user.id,
            "tenant_id": resolved_tenant_id,
            "identity_provider": "local",
            "auth_version": user.auth_version,
            "display_name": user.display_name,
            "roles": roles,
            "permissions": permissions,
            "type": "access",
            "jti": access_jti,
            "iat": now,
            "exp": now + timedelta(seconds=access_expiry),
        }

        access_token = jwt.encode(
            access_claims,
            settings.secret,
            algorithm=settings.algorithm,
        )

        refresh_jti = _generate_jti()
        refresh_claims: dict[str, object] = {
            "sub": user.id,
            "user_id": user.id,
            "tenant_id": resolved_tenant_id,
            "identity_provider": "local",
            "auth_version": user.auth_version,
            "type": "refresh",
            "jti": refresh_jti,
            "iat": now,
            "exp": now + timedelta(seconds=refresh_expiry),
        }

        refresh_token = jwt.encode(
            refresh_claims,
            settings.secret,
            algorithm=settings.algorithm,
        )

        return LoginResult(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=access_expiry,
            user_id=user.id,
            display_name=user.display_name,
            tenant_id=resolved_tenant_id,
            roles=roles,
            permissions=permissions,
        )

    async def _find_user(self, *, tenant_id: str, username: str) -> LocalUserModel | None:
        result = await self._session.execute(
            select(LocalUserModel).where(
                LocalUserModel.tenant_id == tenant_id,
                LocalUserModel.username == username,
            )
        )
        return result.scalar_one_or_none()

    async def verify_refresh_token(self, token: str) -> dict[str, object]:
        """Verify a refresh token and return claims dict (raises on failure)."""
        settings = JwtAuthSettings.from_environment()
        if not settings.secret:
            raise DomainError(
                code="AUTH_JWT_NOT_CONFIGURED",
                message="JWT signing secret is not configured.",
                status_code=500,
            )

        try:
            claims = jwt.decode(
                token,
                settings.secret,
                algorithms=[settings.algorithm],
                options={"require": ["exp", "sub", "type"]},
            )
        except jwt.InvalidTokenError as err:
            raise DomainError(
                code="AUTH_INVALID_TOKEN",
                message="Invalid or expired refresh token.",
                status_code=401,
            ) from err

        if not isinstance(claims, dict):
            raise DomainError(
                code="AUTH_INVALID_TOKEN",
                message="Invalid token claims.",
                status_code=401,
            )

        if claims.get("type") != "refresh":
            raise DomainError(
                code="AUTH_INVALID_TOKEN",
                message="Token is not a refresh token.",
                status_code=401,
            )

        user_id = claims.get("user_id") or claims.get("sub")
        tenant_id = claims.get("tenant_id")
        if not isinstance(user_id, str) or not isinstance(tenant_id, str):
            raise DomainError(
                code="AUTH_INVALID_TOKEN",
                message="Invalid token claims.",
                status_code=401,
            )
        return claims

    async def refresh(self, *, refresh_token: str) -> LoginResult:
        """Validate a refresh token and issue a new access token."""
        claims = await self.verify_refresh_token(refresh_token)

        user_id = claims.get("user_id") or claims.get("sub")
        if not isinstance(user_id, str) or not user_id.strip():
            raise DomainError(
                code="AUTH_INVALID_TOKEN",
                message="Invalid token: missing user identity.",
                status_code=401,
            )

        # Look up user to verify they still exist and are active
        from sqlalchemy import select as _select
        result = await self._session.execute(
            _select(LocalUserModel).where(LocalUserModel.id == user_id)
        )
        user = result.scalar_one_or_none()
        if user is None or not user.is_active:
            raise DomainError(
                code="AUTH_INVALID_TOKEN",
                message="User no longer exists or is inactive.",
                status_code=401,
            )

        token_tenant_id = claims.get("tenant_id")
        if not isinstance(token_tenant_id, str) or token_tenant_id != user.tenant_id:
            raise DomainError(
                code="AUTH_INVALID_TOKEN",
                message="Invalid or expired refresh token.",
                status_code=401,
            )
        token_auth_version = claims.get("auth_version")
        if (
            not isinstance(token_auth_version, int)
            or isinstance(token_auth_version, bool)
            or token_auth_version != user.auth_version
        ):
            raise DomainError(
                code="AUTH_INVALID_TOKEN",
                message="Invalid or expired refresh token.",
                status_code=401,
            )

        settings = JwtAuthSettings.from_environment()
        access_expiry = int(os.getenv("JWT_ACCESS_EXPIRY_SECONDS", "3600"))

        # Load current group roles/permissions
        roles: list[str] = []
        permissions: list[str] = []
        if user.group_id is not None:
            group_result = await self._session.execute(
                _select(UserGroupModel).where(
                    UserGroupModel.tenant_id == user.tenant_id,
                    UserGroupModel.id == user.group_id,
                )
            )
            group = group_result.scalar_one_or_none()
            if group is not None:
                roles = group.get_roles()
                permissions = group.get_permissions()

        tenant_id = user.tenant_id

        now = datetime.now(tz=UTC)
        access_jti = _generate_jti()
        access_claims: dict[str, object] = {
            "sub": user.id,
            "user_id": user.id,
            "tenant_id": tenant_id,
            "identity_provider": "local",
            "auth_version": user.auth_version,
            "display_name": user.display_name,
            "roles": roles,
            "permissions": permissions,
            "type": "access",
            "jti": access_jti,
            "iat": now,
            "exp": now + timedelta(seconds=access_expiry),
        }

        access_token = jwt.encode(
            access_claims,
            settings.secret,
            algorithm=settings.algorithm,
        )

        return LoginResult(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=access_expiry,
            user_id=user.id,
            display_name=user.display_name,
            tenant_id=tenant_id,
            roles=roles,
            permissions=permissions,
        )


def _required_identifier(value: str, *, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise DomainError(
            code="AUTH_INVALID_CREDENTIALS",
            message="Invalid username or password.",
            status_code=401,
            details={"field": field_name},
        )
    return normalized

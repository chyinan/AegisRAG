"""Authoritative database validation for local JWT sessions."""

# pattern: Imperative Shell

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.auth.context import AuthContext
from packages.auth.exceptions import AuthContextInvalidError
from packages.auth.models import LocalUserModel
from packages.auth.storage.models import TenantModel


class LocalAuthSessionValidator:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def validate(self, auth: AuthContext) -> None:
        if auth.identity_provider != "local":
            return
        if auth.auth_version is None:
            raise _invalid_local_session()

        row = (
            await self._session.execute(
                select(
                    LocalUserModel.auth_version,
                    LocalUserModel.is_active,
                    TenantModel.status,
                )
                .join(TenantModel, TenantModel.id == LocalUserModel.tenant_id)
                .where(
                    LocalUserModel.tenant_id == auth.tenant_id,
                    LocalUserModel.id == auth.user_id,
                )
            )
        ).one_or_none()
        if row is None:
            raise _invalid_local_session()
        auth_version, is_active, tenant_status = row
        if not is_active or tenant_status != "active" or auth_version != auth.auth_version:
            raise _invalid_local_session()


def _invalid_local_session() -> AuthContextInvalidError:
    return AuthContextInvalidError(details={"reason": "local_auth_session_invalid"})

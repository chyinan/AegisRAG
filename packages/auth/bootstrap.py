"""Explicit, atomic first-administrator provisioning; never a startup side effect."""

# pattern: Imperative Shell

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.auth.models import LocalUserModel, UserGroupModel
from packages.auth.storage.models import RoleModel, TenantModel, UserModel
from packages.auth.user_service import hash_password
from packages.common.errors import DomainError
from packages.common.tenant_context import tenant_scope

BOOTSTRAP_METADATA_KEY = "local_auth_bootstrap_v1"
ADMIN_GROUP_NAME = "Bootstrap Administrators"
ADMIN_ROLES = ("admin", "platform_admin")
ADMIN_PERMISSIONS = (
    "document:read",
    "document:upload",
    "document:manage",
    "retrieval:query",
    "diagnostics:read",
    "eval:read",
    "eval:write",
    "audit:read",
    "audit:export",
    "review:read",
    "review:write",
    "agent:run",
    "admin:settings",
)


@dataclass(frozen=True)
class BootstrapRequest:
    tenant_id: str
    tenant_name: str
    username: str
    password: str = field(repr=False)
    display_name: str = "Administrator"

    def __post_init__(self) -> None:
        # Match DB column bounds before opening a connection. Reject accidental
        # whitespace/control characters rather than silently changing identity.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,35}", self.tenant_id):
            raise _invalid("Tenant ID must be 1-36 letters, digits, dots, underscores or hyphens.")
        for value in (self.tenant_name, self.username, self.display_name):
            if (
                not value
                or len(value) > 255
                or value != value.strip()
                or any(ord(char) < 32 or ord(char) == 127 for char in value)
            ):
                raise _invalid("Names must be nonblank, trimmed text of at most 255 characters.")
        if not 12 <= len(self.password) or len(self.password.encode("utf-8")) > 72:
            raise _invalid(
                "Bootstrap password must be at least 12 characters and at most 72 bytes."
            )


@dataclass(frozen=True)
class BootstrapResult:
    tenant_id: str
    user_id: str
    group_id: str
    created: bool


async def bootstrap_admin(session: AsyncSession, request: BootstrapRequest) -> BootstrapResult:
    """Own a fresh session transaction, creating only brand-new identities.

    A migration-created empty tenant may be initialized when its exact name
    matches. Any unrelated users/roles/groups cause a conflict. Reruns require
    the original marker and identity to remain intact and never repair, promote,
    reactivate, or reset an existing user. The database enforces race conflicts.
    """
    if session.in_transaction():
        raise _invalid("Bootstrap requires a fresh database session.")
    with tenant_scope(request.tenant_id):
        try:
            async with session.begin():
                tenant = await session.scalar(
                    select(TenantModel).where(TenantModel.id == request.tenant_id).with_for_update()
                )
                if tenant is not None:
                    if tenant.name != request.tenant_name or tenant.status != "active":
                        raise _conflict()
                    marker = tenant.metadata_.get(BOOTSTRAP_METADATA_KEY)
                    if marker is not None:
                        return await _existing_result(session, request, marker)
                    # Do not adopt identities created by demo seed, an IdP, or an
                    # administrator. In particular, never upgrade a named group.
                    for model in (LocalUserModel, UserGroupModel, UserModel, RoleModel):
                        if (
                            await session.scalar(
                                select(model.id)
                                .where(model.tenant_id == request.tenant_id)
                                .limit(1)
                            )
                            is not None
                        ):
                            raise _conflict()
                else:
                    tenant = TenantModel(
                        id=request.tenant_id,
                        name=request.tenant_name,
                        status="active",
                        metadata_={},
                    )
                    session.add(tenant)
                    await session.flush()

                user_id, group_id = str(uuid.uuid4()), str(uuid.uuid4())
                group = UserGroupModel(
                    id=group_id,
                    tenant_id=request.tenant_id,
                    name=ADMIN_GROUP_NAME,
                    description="Initial administrator group created by explicit bootstrap.",
                    roles=json.dumps(ADMIN_ROLES),
                    permissions=json.dumps(ADMIN_PERMISSIONS),
                )
                session.add(group)
                await session.flush()
                session.add(
                    LocalUserModel(
                        id=user_id,
                        tenant_id=request.tenant_id,
                        username=request.username,
                        display_name=request.display_name,
                        password_hash=hash_password(request.password),
                        group_id=group_id,
                        is_active=True,
                    )
                )
                tenant.metadata_ = {
                    **tenant.metadata_,
                    BOOTSTRAP_METADATA_KEY: {"user_id": user_id, "group_id": group_id},
                }
                await session.flush()
                return BootstrapResult(request.tenant_id, user_id, group_id, created=True)
        except IntegrityError as exc:
            # Includes duplicate tenant names and concurrent bootstrap attempts.
            # No DB exception text: it can contain SQL parameters/credential hashes.
            raise _conflict() from exc


async def _existing_result(
    session: AsyncSession,
    request: BootstrapRequest,
    marker: object,
) -> BootstrapResult:
    if not isinstance(marker, dict):
        raise _conflict()
    user_id, group_id = marker.get("user_id"), marker.get("group_id")
    if not isinstance(user_id, str) or not isinstance(group_id, str):
        raise _conflict()
    user = await session.scalar(
        select(LocalUserModel).where(
            LocalUserModel.tenant_id == request.tenant_id,
            LocalUserModel.id == user_id,
        )
    )
    group = await session.scalar(
        select(UserGroupModel).where(
            UserGroupModel.tenant_id == request.tenant_id,
            UserGroupModel.id == group_id,
        )
    )
    if (
        user is None
        or group is None
        or not user.is_active
        or user.username != request.username
        or user.group_id != group_id
        or group.name != ADMIN_GROUP_NAME
        or set(group.get_roles()) != set(ADMIN_ROLES)
        or set(group.get_permissions()) != set(ADMIN_PERMISSIONS)
    ):
        raise _conflict()
    return BootstrapResult(request.tenant_id, user_id, group_id, created=False)


def _invalid(message: str) -> DomainError:
    return DomainError(code="AUTH_BOOTSTRAP_INVALID", message=message, status_code=422)


def _conflict() -> DomainError:
    return DomainError(
        code="AUTH_BOOTSTRAP_CONFLICT",
        message=(
            "Tenant or administrator state conflicts with bootstrap. "
            "No existing identity was changed; use the normal administration workflow."
        ),
        status_code=409,
    )

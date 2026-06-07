from __future__ import annotations

from collections.abc import Mapping, Sequence

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.auth.dto import RoleRecord, TenantRecord, UserRecord, UserRoleRecord
from packages.auth.storage.models import RoleModel, TenantModel, UserModel, UserRoleModel
from packages.data.storage.exceptions import StorageError


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_tenant(
        self,
        *,
        tenant_id: str,
        name: str,
        created_by: str | None,
        status: str = "active",
        metadata: Mapping[str, object] | None = None,
    ) -> TenantRecord:
        model = TenantModel(
            id=tenant_id,
            name=name,
            created_by=created_by,
            status=status,
            metadata_=dict(metadata or {}),
        )
        await self._flush_and_refresh(model)
        return _tenant_record(model)

    async def create_user(
        self,
        *,
        user_id: str,
        tenant_id: str,
        created_by: str | None,
        display_name: str,
        external_id: str | None = None,
        email: str | None = None,
        department: str | None = None,
        status: str = "active",
    ) -> UserRecord:
        if external_id is None and email is None:
            raise StorageError(
                code="AUTH_STORAGE_INVALID_USER_IDENTITY",
                message="User must have external_id or email.",
                details={"tenant_id": tenant_id},
            )
        model = UserModel(
            id=user_id,
            tenant_id=tenant_id,
            created_by=created_by,
            status=status,
            external_id=external_id,
            email=email,
            display_name=display_name,
            department=department,
        )
        await self._flush_and_refresh(model)
        return _user_record(model)

    async def create_role(
        self,
        *,
        role_id: str,
        tenant_id: str,
        created_by: str | None,
        name: str,
        description: str | None = None,
        permissions: Sequence[str] = (),
        status: str = "active",
    ) -> RoleRecord:
        normalized_permissions = _normalize_permissions(permissions)
        model = RoleModel(
            id=role_id,
            tenant_id=tenant_id,
            created_by=created_by,
            status=status,
            name=name,
            description=description,
            permissions=normalized_permissions,
        )
        await self._flush_and_refresh(model)
        return _role_record(model)

    async def assign_role(
        self,
        *,
        assignment_id: str,
        tenant_id: str,
        user_id: str,
        role_id: str,
        created_by: str | None,
        status: str = "active",
    ) -> UserRoleRecord:
        await self._validate_assignment_tenant(
            tenant_id=tenant_id,
            user_id=user_id,
            role_id=role_id,
        )
        model = UserRoleModel(
            id=assignment_id,
            tenant_id=tenant_id,
            user_id=user_id,
            role_id=role_id,
            created_by=created_by,
            status=status,
        )
        await self._flush_and_refresh(model)
        return _user_role_record(model)

    async def _flush_and_refresh(
        self,
        model: TenantModel | UserModel | RoleModel | UserRoleModel,
    ) -> None:
        self._session.add(model)
        try:
            await self._session.flush()
            await self._session.refresh(model)
        except SQLAlchemyError as exc:
            await self._session.rollback()
            raise StorageError(
                code="AUTH_STORAGE_WRITE_FAILED",
                message="Auth storage write failed.",
                details={"model": type(model).__name__},
            ) from exc

    async def _validate_assignment_tenant(
        self,
        *,
        tenant_id: str,
        user_id: str,
        role_id: str,
    ) -> None:
        user_tenant_id = await self._session.scalar(
            select(UserModel.tenant_id).where(UserModel.id == user_id)
        )
        role_tenant_id = await self._session.scalar(
            select(RoleModel.tenant_id).where(RoleModel.id == role_id)
        )
        if user_tenant_id != tenant_id or role_tenant_id != tenant_id:
            raise StorageError(
                code="AUTH_STORAGE_TENANT_MISMATCH",
                message="Role assignment tenant must match both user and role.",
                details={"tenant_id": tenant_id},
            )


def _tenant_record(model: TenantModel) -> TenantRecord:
    return TenantRecord(
        id=model.id,
        name=model.name,
        status=model.status,
        created_by=model.created_by,
        metadata=dict(model.metadata_ or {}),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _normalize_permissions(permissions: Sequence[str]) -> list[str]:
    if isinstance(permissions, str):
        raise StorageError(
            code="AUTH_STORAGE_INVALID_PERMISSIONS",
            message="Role permissions must be a sequence of permission strings.",
        )

    normalized: list[str] = []
    for permission in permissions:
        permission_text = permission.strip()
        if not permission_text:
            raise StorageError(
                code="AUTH_STORAGE_INVALID_PERMISSIONS",
                message="Role permissions must not contain blank values.",
            )
        normalized.append(permission_text)
    return normalized


def _user_record(model: UserModel) -> UserRecord:
    return UserRecord(
        id=model.id,
        tenant_id=model.tenant_id,
        status=model.status,
        created_by=model.created_by,
        external_id=model.external_id,
        email=model.email,
        display_name=model.display_name,
        department=model.department,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _role_record(model: RoleModel) -> RoleRecord:
    return RoleRecord(
        id=model.id,
        tenant_id=model.tenant_id,
        status=model.status,
        created_by=model.created_by,
        name=model.name,
        description=model.description,
        permissions=tuple(model.permissions or ()),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def _user_role_record(model: UserRoleModel) -> UserRoleRecord:
    return UserRoleRecord(
        id=model.id,
        tenant_id=model.tenant_id,
        status=model.status,
        created_by=model.created_by,
        user_id=model.user_id,
        role_id=model.role_id,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )

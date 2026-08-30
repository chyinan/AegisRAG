"""Group service — CRUD operations on UserGroupModel."""

# pattern: Imperative Shell

from __future__ import annotations

from typing import TypedDict

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.auth.models import LocalUserModel, UserGroupModel
from packages.common.errors import DomainError


class GroupData(TypedDict):
    id: str
    tenant_id: str
    name: str
    description: str | None
    created_at: str | None
    updated_at: str | None


class GroupService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_groups(self, *, tenant_id: str) -> list[GroupData]:
        result = await self._session.execute(
            select(UserGroupModel)
            .where(UserGroupModel.tenant_id == tenant_id)
            .order_by(UserGroupModel.name)
        )
        models = result.scalars().all()
        return [_group_dict(m) for m in models]

    async def get_group(self, *, tenant_id: str, group_id: str) -> GroupData:
        model = await self._get_or_404(tenant_id=tenant_id, group_id=group_id)
        return _group_dict(model)

    async def create_group(
        self, *, tenant_id: str, name: str, description: str | None
    ) -> GroupData:
        model = UserGroupModel(tenant_id=tenant_id, name=name, description=description)
        self._session.add(model)
        try:
            await self._session.flush()
            await self._session.refresh(model)
            await self._session.commit()
        except IntegrityError as err:
            await self._session.rollback()
            raise DomainError(
                code="AUTH_GROUP_NAME_EXISTS",
                message=f'Group "{name}" already exists.',
                status_code=409,
            ) from err
        return _group_dict(model)

    async def update_group(
        self,
        *,
        tenant_id: str,
        group_id: str,
        name: str | None,
        description: str | None,
    ) -> GroupData:
        model = await self._get_or_404(tenant_id=tenant_id, group_id=group_id)
        if name is not None:
            model.name = name
        if description is not None:
            model.description = description
        try:
            await self._session.flush()
            await self._session.refresh(model)
            await self._session.commit()
        except IntegrityError as err:
            await self._session.rollback()
            raise DomainError(
                code="AUTH_GROUP_NAME_EXISTS",
                message=f'Group "{name}" already exists.',
                status_code=409,
            ) from err
        return _group_dict(model)

    async def delete_group(self, *, tenant_id: str, group_id: str) -> None:
        model = await self._get_or_404(tenant_id=tenant_id, group_id=group_id)
        await self._session.execute(
            update(LocalUserModel)
            .where(
                LocalUserModel.tenant_id == tenant_id,
                LocalUserModel.group_id == group_id,
            )
            .values(
                group_id=None,
                auth_version=LocalUserModel.auth_version + 1,
            )
        )
        await self._session.delete(model)
        await self._session.flush()
        await self._session.commit()

    async def _get_or_404(self, *, tenant_id: str, group_id: str) -> UserGroupModel:
        result = await self._session.execute(
            select(UserGroupModel).where(
                UserGroupModel.tenant_id == tenant_id,
                UserGroupModel.id == group_id,
            )
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise DomainError(
                code="AUTH_GROUP_NOT_FOUND",
                message="Group not found.",
                status_code=404,
            )
        return model


def _group_dict(model: UserGroupModel) -> GroupData:
    return {
        "id": model.id,
        "tenant_id": model.tenant_id,
        "name": model.name,
        "description": model.description,
        "created_at": model.created_at.isoformat() if model.created_at else None,
        "updated_at": model.updated_at.isoformat() if model.updated_at else None,
    }

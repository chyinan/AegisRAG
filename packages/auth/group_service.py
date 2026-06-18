"""Group service — CRUD operations on UserGroupModel."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.auth.models import UserGroupModel
from packages.common.errors import DomainError


class GroupService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_groups(self) -> list[dict[str, object]]:
        result = await self._session.execute(
            select(UserGroupModel).order_by(UserGroupModel.name)
        )
        models = result.scalars().all()
        return [_group_dict(m) for m in models]

    async def get_group(self, *, group_id: str) -> dict[str, object]:
        model = await self._get_or_404(group_id)
        return _group_dict(model)

    async def create_group(self, *, name: str, description: str | None) -> dict[str, object]:
        model = UserGroupModel(name=name, description=description)
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
        self, *, group_id: str, name: str | None, description: str | None
    ) -> dict[str, object]:
        model = await self._get_or_404(group_id)
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

    async def delete_group(self, *, group_id: str) -> None:
        model = await self._get_or_404(group_id)
        await self._session.delete(model)
        await self._session.flush()
        await self._session.commit()

    async def _get_or_404(self, group_id: str) -> UserGroupModel:
        result = await self._session.execute(
            select(UserGroupModel).where(UserGroupModel.id == group_id)
        )
        model = result.scalar_one_or_none()
        if model is None:
            raise DomainError(
                code="AUTH_GROUP_NOT_FOUND",
                message="Group not found.",
                status_code=404,
            )
        return model


def _group_dict(model: UserGroupModel) -> dict[str, object]:
    return {
        "id": model.id,
        "name": model.name,
        "description": model.description,
        "created_at": model.created_at.isoformat() if model.created_at else None,
        "updated_at": model.updated_at.isoformat() if model.updated_at else None,
    }

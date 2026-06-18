"""User service — CRUD operations on LocalUserModel with bcrypt password hashing."""

from __future__ import annotations

import re

import bcrypt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from packages.auth.models import LocalUserModel
from packages.common.errors import DomainError


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_users(self) -> list[dict[str, object]]:
        result = await self._session.execute(
            select(LocalUserModel).order_by(LocalUserModel.username)
        )
        models = result.scalars().all()
        return [_user_dict(m) for m in models]

    async def create_user(
        self,
        *,
        username: str,
        password: str,
        email: str | None,
        display_name: str,
        group_id: str | None,
    ) -> dict[str, object]:
        _validate_password(password)

        password_hash = bcrypt.hashpw(
            password.encode("utf-8"),
            bcrypt.gensalt(),
        ).decode("utf-8")

        model = LocalUserModel(
            username=username,
            password_hash=password_hash,
            email=email,
            display_name=display_name,
            group_id=group_id,
        )
        self._session.add(model)
        try:
            await self._session.flush()
            await self._session.refresh(model)
            await self._session.commit()
        except IntegrityError as err:
            await self._session.rollback()
            raise DomainError(
                code="AUTH_USERNAME_EXISTS",
                message="Username is not available.",
                status_code=409,
            ) from err
        return _user_dict(model)


def _validate_password(password: str) -> None:
    import os as _os

    min_length = int(_os.getenv("AUTH_PASSWORD_MIN_LENGTH", "8"))
    required_categories = int(_os.getenv("AUTH_PASSWORD_REQUIRED_CATEGORIES", "3"))

    if len(password) < min_length:
        raise DomainError(
            code="AUTH_WEAK_PASSWORD",
            message=f"Password must be at least {min_length} characters.",
            status_code=422,
        )

    categories = 0
    if re.search(r"[A-Z]", password):
        categories += 1
    if re.search(r"[a-z]", password):
        categories += 1
    if re.search(r"[0-9]", password):
        categories += 1
    if re.search(r'[!"#$%&\'()*+,\-./:;<=>?@\[\\\]^_`{|}~]', password):
        categories += 1

    if categories < required_categories:
        raise DomainError(
            code="AUTH_WEAK_PASSWORD",
            message=(
                f"Password must contain at least {required_categories} of: "
                "uppercase letter, lowercase letter, digit, special character."
            ),
            status_code=422,
        )


def _user_dict(model: LocalUserModel) -> dict[str, object]:
    return {
        "id": model.id,
        "username": model.username,
        "email": model.email,
        "display_name": model.display_name,
        "is_active": model.is_active,
        "group_id": model.group_id,
        "created_at": model.created_at.isoformat() if model.created_at else None,
        "updated_at": model.updated_at.isoformat() if model.updated_at else None,
    }

"""Local-authentication SQLAlchemy models — username/password with bcrypt hashing.

Separate from the multi-tenant RBAC models in ``packages.auth.storage.models``.
These models power the enterprise login backend: local users can be organised
into named groups, and passwords are stored as bcrypt hashes.
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

import json as _json

from packages.data.storage.base import Base, IdMixin, TimestampMixin


class UserGroupModel(IdMixin, TimestampMixin, Base):
    """A named group that local users can belong to."""

    __tablename__ = "user_groups"
    __table_args__ = (
        UniqueConstraint("name", name="uq_user_groups_name"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    roles: Mapped[str | None] = mapped_column(String(500), nullable=True)
    permissions: Mapped[str | None] = mapped_column(String(500), nullable=True)

    users: Mapped[list["LocalUserModel"]] = relationship(
        "LocalUserModel",
        back_populates="group",
        lazy="selectin",
    )

    def get_roles(self) -> list[str]:
        if self.roles:
            try:
                parsed = _json.loads(self.roles)
                if isinstance(parsed, list):
                    return [str(r) for r in parsed if str(r).strip()]
            except (_json.JSONDecodeError, TypeError):
                pass
        return []

    def get_permissions(self) -> list[str]:
        if self.permissions:
            try:
                parsed = _json.loads(self.permissions)
                if isinstance(parsed, list):
                    return [str(p) for p in parsed if str(p).strip()]
            except (_json.JSONDecodeError, TypeError):
                pass
        return []


class LocalUserModel(IdMixin, TimestampMixin, Base):
    """A local user with bcrypt-hashed password."""

    __tablename__ = "local_users"
    __table_args__ = (
        UniqueConstraint("username", name="uq_local_users_username"),
    )

    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    group_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("user_groups.id", ondelete="SET NULL"),
        nullable=True,
    )

    group: Mapped[UserGroupModel | None] = relationship(
        "UserGroupModel",
        back_populates="users",
        lazy="selectin",
    )

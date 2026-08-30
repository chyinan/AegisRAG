"""Local-authentication SQLAlchemy models — username/password with bcrypt hashing.

Separate from the multi-tenant RBAC models in ``packages.auth.storage.models``.
These models power the enterprise login backend: local users can be organised
into named groups, and passwords are stored as bcrypt hashes.
"""

# pattern: Functional Core

from __future__ import annotations

import json as _json

from sqlalchemy import (
    Boolean,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.data.storage.base import Base, IdMixin, TimestampMixin


class UserGroupModel(IdMixin, TimestampMixin, Base):
    """A named group that local users can belong to."""

    __tablename__ = "user_groups"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_user_groups_tenant_id_id"),
        UniqueConstraint("tenant_id", "name", name="uq_user_groups_tenant_name"),
        Index("ix_user_groups_tenant_id", "tenant_id"),
    )

    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    roles: Mapped[str | None] = mapped_column(Text, nullable=True)
    permissions: Mapped[str | None] = mapped_column(Text, nullable=True)

    users: Mapped[list[LocalUserModel]] = relationship(
        "LocalUserModel",
        back_populates="group",
        lazy="selectin",
        passive_deletes="all",
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
        ForeignKeyConstraint(
            ["tenant_id", "group_id"],
            ["user_groups.tenant_id", "user_groups.id"],
            name="fk_local_users_tenant_group",
        ),
        UniqueConstraint("tenant_id", "username", name="uq_local_users_tenant_username"),
        Index("ix_local_users_tenant_id", "tenant_id"),
        Index("ix_local_users_tenant_group", "tenant_id", "group_id"),
    )

    tenant_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    username: Mapped[str] = mapped_column(String(255), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    auth_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )

    group_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
    )

    group: Mapped[UserGroupModel | None] = relationship(
        "UserGroupModel",
        back_populates="users",
        lazy="selectin",
    )

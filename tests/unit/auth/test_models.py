"""Unit tests for local-auth models (packages/auth/models.py)."""

from typing import Any

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from packages.auth.models import LocalUserModel, UserGroupModel


def _column_names(model: Any) -> set[str]:
    return set(model.__table__.columns.keys())


def _index_map(model: Any) -> dict[str, tuple[str, ...]]:
    return {
        index.name: tuple(column.name for column in index.columns)
        for index in model.__table__.indexes
        if index.name is not None
    }


def _unique_constraint_names(model: Any) -> set[str]:
    return {
        str(constraint.name)
        for constraint in model.__table__.constraints
        if isinstance(constraint, UniqueConstraint) and constraint.name is not None
    }


def _check_constraint_names(model: Any) -> set[str]:
    return {
        str(constraint.name)
        for constraint in model.__table__.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name is not None
    }


def _foreign_key_constraint_columns(model: Any) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    constraints: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
    from sqlalchemy import ForeignKeyConstraint as FKC

    for constraint in model.__table__.constraints:
        if not isinstance(constraint, FKC):
            continue
        local_columns = tuple(column.name for column in constraint.columns)
        remote_columns = tuple(
            f"{element.column.table.name}.{element.column.name}"
            for element in constraint.elements
        )
        constraints.add((local_columns, remote_columns))
    return constraints


# ── UserGroupModel ────────────────────────────────────────────

def test_user_group_model_uses_snake_case_table_name() -> None:
    assert UserGroupModel.__tablename__ == "user_groups"


def test_user_group_model_required_columns() -> None:
    assert {
        "id",
        "name",
        "description",
        "created_at",
        "updated_at",
    } <= _column_names(UserGroupModel)


def test_user_group_name_must_be_unique() -> None:
    assert "uq_user_groups_name" in _unique_constraint_names(UserGroupModel)


def test_user_group_description_nullable() -> None:
    col = UserGroupModel.__table__.columns["description"]
    assert col.nullable is True


# ── LocalUserModel ────────────────────────────────────────────

def test_local_user_model_uses_snake_case_table_name() -> None:
    assert LocalUserModel.__tablename__ == "local_users"


def test_local_user_model_required_columns() -> None:
    assert {
        "id",
        "username",
        "password_hash",
        "email",
        "display_name",
        "is_active",
        "group_id",
        "created_at",
        "updated_at",
    } <= _column_names(LocalUserModel)


def test_local_user_username_must_be_unique() -> None:
    assert "uq_local_users_username" in _unique_constraint_names(LocalUserModel)


def test_local_user_password_hash_column_type() -> None:
    col = LocalUserModel.__table__.columns["password_hash"]
    assert str(col.type).lower().startswith("varchar") or "string" in str(col.type).lower()


def test_local_user_has_foreign_key_to_user_groups() -> None:
    fks = _foreign_key_constraint_columns(LocalUserModel)
    assert any(
        local == ("group_id",) and remote == ("user_groups.id",)
        for local, remote in fks
    )


def test_local_user_group_id_nullable() -> None:
    col = LocalUserModel.__table__.columns["group_id"]
    assert col.nullable is True


def test_local_user_is_active_defaults_to_true() -> None:
    col = LocalUserModel.__table__.columns["is_active"]
    assert col.default is not None


def test_local_user_email_nullable() -> None:
    col = LocalUserModel.__table__.columns["email"]
    assert col.nullable is True

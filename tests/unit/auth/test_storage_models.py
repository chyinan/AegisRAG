from typing import Any

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from packages.auth.storage.models import RoleModel, TenantModel, UserModel, UserRoleModel


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
    for constraint in model.__table__.constraints:
        if not isinstance(constraint, ForeignKeyConstraint):
            continue
        local_columns = tuple(column.name for column in constraint.columns)
        remote_columns = tuple(
            f"{element.column.table.name}.{element.column.name}" for element in constraint.elements
        )
        constraints.add((local_columns, remote_columns))
    return constraints


def test_identity_tables_use_plural_snake_case_table_names() -> None:
    assert TenantModel.__tablename__ == "tenants"
    assert UserModel.__tablename__ == "users"
    assert RoleModel.__tablename__ == "roles"
    assert UserRoleModel.__tablename__ == "user_roles"


def test_tenant_model_contains_governance_fields_and_indexes() -> None:
    assert {
        "id",
        "created_at",
        "updated_at",
        "created_by",
        "status",
        "name",
        "metadata",
    } <= _column_names(TenantModel)

    indexes = _index_map(TenantModel)
    assert indexes["ix_tenants_status"] == ("status",)
    assert indexes["ix_tenants_created_at"] == ("created_at",)
    assert "uq_tenants_name" in _unique_constraint_names(TenantModel)


def test_user_model_contains_governance_fields_and_tenant_indexes() -> None:
    assert {
        "id",
        "created_at",
        "updated_at",
        "tenant_id",
        "created_by",
        "status",
        "external_id",
        "email",
        "display_name",
        "department",
    } <= _column_names(UserModel)

    indexes = _index_map(UserModel)
    assert indexes["ix_users_tenant_id"] == ("tenant_id",)
    assert indexes["ix_users_status"] == ("status",)
    assert indexes["ix_users_created_at"] == ("created_at",)
    assert "ck_users_external_id_or_email" in _check_constraint_names(UserModel)
    assert "uq_users_tenant_id_id" in _unique_constraint_names(UserModel)
    assert "uq_users_tenant_id_external_id" in _unique_constraint_names(UserModel)
    assert "uq_users_tenant_id_email" in _unique_constraint_names(UserModel)


def test_role_model_contains_permissions_and_unique_name_per_tenant() -> None:
    assert {
        "id",
        "created_at",
        "updated_at",
        "tenant_id",
        "created_by",
        "status",
        "name",
        "description",
        "permissions",
    } <= _column_names(RoleModel)

    indexes = _index_map(RoleModel)
    assert indexes["ix_roles_tenant_id"] == ("tenant_id",)
    assert indexes["ix_roles_status"] == ("status",)
    assert "uq_roles_tenant_id_id" in _unique_constraint_names(RoleModel)
    assert "uq_roles_tenant_id_name" in _unique_constraint_names(RoleModel)


def test_user_role_model_prevents_duplicate_assignment_per_tenant() -> None:
    assert {
        "id",
        "created_at",
        "updated_at",
        "tenant_id",
        "created_by",
        "status",
        "user_id",
        "role_id",
    } <= _column_names(UserRoleModel)

    indexes = _index_map(UserRoleModel)
    assert indexes["ix_user_roles_tenant_id"] == ("tenant_id",)
    assert indexes["ix_user_roles_user_id"] == ("user_id",)
    assert indexes["ix_user_roles_status"] == ("status",)
    assert "uq_user_roles_tenant_id_user_id_role_id" in _unique_constraint_names(UserRoleModel)
    assert (
        ("tenant_id", "user_id"),
        ("users.tenant_id", "users.id"),
    ) in _foreign_key_constraint_columns(UserRoleModel)
    assert (
        ("tenant_id", "role_id"),
        ("roles.tenant_id", "roles.id"),
    ) in _foreign_key_constraint_columns(UserRoleModel)

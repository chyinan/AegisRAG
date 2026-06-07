import pytest
from pydantic import ValidationError

from packages.auth.context import AuthContext
from packages.auth.exceptions import AuthContextInvalidError, AuthContextRequiredError


def test_auth_context_requires_user_id_and_tenant_id() -> None:
    auth = AuthContext.model_validate(
        {
            "user_id": "user-123",
            "tenant_id": "tenant-abc",
            "roles": ["admin", "knowledge_manager"],
            "department": "HR",
            "permissions": ["document:read", "retrieval:query"],
        }
    )

    assert auth.user_id == "user-123"
    assert auth.tenant_id == "tenant-abc"
    assert auth.roles == ("admin", "knowledge_manager")
    assert auth.department == "HR"
    assert auth.permissions == ("document:read", "retrieval:query")
    assert auth.model_dump() == {
        "user_id": "user-123",
        "tenant_id": "tenant-abc",
        "roles": ("admin", "knowledge_manager"),
        "department": "HR",
        "permissions": ("document:read", "retrieval:query"),
    }


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("user_id", ""),
        ("user_id", "   "),
        ("tenant_id", ""),
        ("tenant_id", "   "),
    ],
)
def test_auth_context_rejects_blank_required_identifiers(field_name: str, value: str) -> None:
    payload = {
        "user_id": "user-123",
        "tenant_id": "tenant-abc",
        "roles": (),
        "department": None,
        "permissions": (),
        field_name: value,
    }

    with pytest.raises(ValidationError):
        AuthContext.model_validate(payload)


def test_auth_context_uses_immutable_empty_tuple_defaults() -> None:
    first = AuthContext(user_id="user-1", tenant_id="tenant-1")
    second = AuthContext(user_id="user-2", tenant_id="tenant-2")

    assert first.roles == ()
    assert first.permissions == ()
    assert second.roles == ()
    assert second.permissions == ()


@pytest.mark.parametrize("field_name", ["roles", "permissions"])
def test_auth_context_rejects_unsupported_role_permission_shapes(field_name: str) -> None:
    payload = {
        "user_id": "user-123",
        "tenant_id": "tenant-abc",
        field_name: 123,
    }

    with pytest.raises(ValidationError):
        AuthContext.model_validate(payload)


def test_auth_context_errors_expose_stable_codes_without_sensitive_details() -> None:
    required = AuthContextRequiredError(details={"missing": ["tenant_id"]})
    invalid = AuthContextInvalidError()

    assert required.code == "AUTH_CONTEXT_REQUIRED"
    assert required.message == "Authentication context is required."
    assert required.details == {"missing": ["tenant_id"]}
    assert str(required) == "AUTH_CONTEXT_REQUIRED: Authentication context is required."
    assert invalid.code == "AUTH_CONTEXT_INVALID"

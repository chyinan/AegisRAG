from typing import Any, cast

import pytest

from packages.auth.context import AuthContext
from packages.auth.policies import (
    AccessFilter,
    build_access_filter,
    has_diagnostics_read_permission,
    has_rag_query_permission,
)


def test_build_access_filter_preserves_tenant_user_and_acl_facts() -> None:
    auth = AuthContext(
        user_id="user-123",
        tenant_id="tenant-abc",
        roles=("admin", "knowledge_manager"),
        department="HR",
        permissions=("document:read", "retrieval:query"),
    )

    access_filter = build_access_filter(auth)

    assert isinstance(access_filter, AccessFilter)
    assert access_filter.tenant_id == "tenant-abc"
    assert access_filter.user_id == "user-123"
    assert access_filter.roles == ("admin", "knowledge_manager")
    assert access_filter.department == "HR"
    assert access_filter.permissions == ("document:read", "retrieval:query")
    assert access_filter.metadata_filter == {"tenant_id": "tenant-abc"}
    assert access_filter.acl_filter == {
        "tenant_id": "tenant-abc",
        "user_id": "user-123",
        "roles": ("admin", "knowledge_manager"),
        "department": "HR",
        "permissions": ("document:read", "retrieval:query"),
    }


def test_build_access_filter_keeps_cross_tenant_filter_explicit() -> None:
    tenant_a = build_access_filter(AuthContext(user_id="user-1", tenant_id="tenant-a"))
    tenant_b = build_access_filter(AuthContext(user_id="user-1", tenant_id="tenant-b"))

    assert tenant_a.metadata_filter["tenant_id"] == "tenant-a"
    assert tenant_b.metadata_filter["tenant_id"] == "tenant-b"
    assert tenant_a.metadata_filter != tenant_b.metadata_filter


def test_build_access_filter_handles_empty_roles_permissions_and_missing_department() -> None:
    access_filter = build_access_filter(AuthContext(user_id="user-123", tenant_id="tenant-abc"))

    assert access_filter.roles == ()
    assert access_filter.permissions == ()
    assert access_filter.department is None
    assert access_filter.metadata_filter == {"tenant_id": "tenant-abc"}
    assert access_filter.acl_filter == {
        "tenant_id": "tenant-abc",
        "user_id": "user-123",
        "roles": (),
        "department": None,
        "permissions": (),
    }


def test_access_filter_is_structured_data_not_prompt_text() -> None:
    access_filter = build_access_filter(
        AuthContext(
            user_id="user-123",
            tenant_id="tenant-abc",
            permissions=("document:read",),
        )
    )

    payload = access_filter.model_dump()

    assert isinstance(payload, dict)
    assert "prompt" not in payload
    assert "policy_prompt" not in payload
    assert isinstance(payload["metadata_filter"], dict)
    assert isinstance(payload["acl_filter"], dict)


def test_access_filter_does_not_expose_mutable_nested_filters() -> None:
    access_filter = build_access_filter(AuthContext(user_id="user-123", tenant_id="tenant-abc"))
    metadata_filter = cast(Any, access_filter.metadata_filter)

    with pytest.raises(TypeError):
        metadata_filter["department"] = "HR"


@pytest.mark.parametrize(
    ("permissions", "allowed"),
    [
        (("document:read", "retrieval:query"), True),
        (("document:read",), False),
        (("retrieval:query",), False),
        (("document:upload",), False),
        ((), False),
    ],
)
def test_rag_query_permission_requires_read_and_query_permission(
    permissions: tuple[str, ...],
    allowed: bool,
) -> None:
    auth = AuthContext(
        user_id="user-123",
        tenant_id="tenant-abc",
        permissions=permissions,
    )

    assert has_rag_query_permission(auth) is allowed


@pytest.mark.parametrize(
    ("permissions", "allowed"),
    [
        (("audit:read",), True),
        (("diagnostics:read",), True),
        (("audit:read", "document:read"), True),
        (("document:read",), False),
        (("agent:run",), False),
        ((), False),
    ],
)
def test_diagnostics_read_permission_reuses_audit_read_or_dedicated_permission(
    permissions: tuple[str, ...],
    allowed: bool,
) -> None:
    auth = AuthContext(
        user_id="user-123",
        tenant_id="tenant-abc",
        permissions=permissions,
    )

    assert has_diagnostics_read_permission(auth) is allowed

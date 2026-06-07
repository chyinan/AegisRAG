from __future__ import annotations

import pytest

from packages.auth.context import AuthContext
from packages.retrieval.dto import RetrievalRequest
from packages.retrieval.exceptions import RETRIEVAL_FORBIDDEN_FILTER, RetrievalError
from packages.retrieval.filters import (
    build_retrieval_filter_set,
    to_sparse_filter_payload,
    to_vector_acl_filter,
    to_vector_metadata_filters,
)


def test_build_retrieval_filter_set_preserves_auth_and_request_filters() -> None:
    auth = AuthContext(
        user_id="user-1",
        tenant_id="tenant-a",
        roles=("hr", "manager"),
        department="people",
        permissions=("document:read", "retrieval:query"),
    )
    request = RetrievalRequest(
        query="policy",
        metadata_filter={"department": "people", "source_type": "markdown"},
        request_id="req-1",
        trace_id="trace-1",
    )

    filters = build_retrieval_filter_set(auth=auth, request=request)

    assert filters.tenant_id == "tenant-a"
    assert filters.user_id == "user-1"
    assert filters.roles == ("hr", "manager")
    assert filters.department == "people"
    assert filters.permissions == ("document:read", "retrieval:query")
    assert filters.metadata_filter == {"department": "people", "source_type": "markdown"}
    assert filters.acl_filter == {
        "tenant_id": "tenant-a",
        "user_id": "user-1",
        "roles": ("hr", "manager"),
        "department": "people",
        "permissions": ("document:read", "retrieval:query"),
    }
    assert filters.include_deleted is False


def test_vector_filter_conversion_uses_same_filter_set() -> None:
    filters = build_retrieval_filter_set(
        auth=AuthContext(
            user_id="user-1",
            tenant_id="tenant-a",
            roles=("hr",),
            department="people",
            permissions=("document:read",),
        ),
        request=RetrievalRequest(
            query="policy",
            metadata_filter={"department": "people"},
            request_id="req-1",
            trace_id="trace-1",
        ),
    )

    acl_filter = to_vector_acl_filter(filters)
    metadata_filters = to_vector_metadata_filters(filters)
    sparse_payload = to_sparse_filter_payload(filters)

    assert acl_filter.user_id == "user-1"
    assert acl_filter.roles == ["hr"]
    assert acl_filter.department == "people"
    assert acl_filter.permissions == ["document:read"]
    assert [(item.key, item.value) for item in metadata_filters] == [("department", "people")]
    assert sparse_payload == {
        "tenant_id": "tenant-a",
        "user_id": "user-1",
        "roles": ("hr",),
        "department": "people",
        "permissions": ("document:read",),
        "metadata_filter": {"department": "people"},
        "acl_filter": {
            "tenant_id": "tenant-a",
            "user_id": "user-1",
            "roles": ("hr",),
            "department": "people",
            "permissions": ("document:read",),
        },
        "include_deleted": False,
    }


def test_matching_request_tenant_filter_is_accepted_but_not_treated_as_metadata() -> None:
    filters = build_retrieval_filter_set(
        auth=AuthContext(user_id="user-1", tenant_id="tenant-a"),
        request=RetrievalRequest(
            query="policy",
            metadata_filter={"tenant_id": "tenant-a", "department": "people"},
            request_id="req-1",
            trace_id="trace-1",
        ),
    )

    assert filters.tenant_id == "tenant-a"
    assert filters.metadata_filter == {"department": "people"}


def test_cross_tenant_request_metadata_filter_is_rejected() -> None:
    with pytest.raises(RetrievalError) as exc_info:
        build_retrieval_filter_set(
            auth=AuthContext(user_id="user-1", tenant_id="tenant-a"),
            request=RetrievalRequest(
                query="policy",
                metadata_filter={"tenant_id": "tenant-b"},
                request_id="req-1",
                trace_id="trace-1",
            ),
        )

    assert exc_info.value.code == RETRIEVAL_FORBIDDEN_FILTER
    assert exc_info.value.details == {
        "request_id": "req-1",
        "trace_id": "trace-1",
        "tenant_id": "tenant-a",
        "user_id": "user-1",
        "top_k": 10,
        "error_code": RETRIEVAL_FORBIDDEN_FILTER,
    }

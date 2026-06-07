from __future__ import annotations

from collections.abc import Mapping

from packages.auth.context import AuthContext
from packages.auth.policies import FrozenDict, build_access_filter
from packages.retrieval.dto import RetrievalFilterSet, RetrievalRequest
from packages.retrieval.exceptions import RETRIEVAL_FORBIDDEN_FILTER, RetrievalError
from packages.vectorstores.dto import AclFilter, MetadataFilter


def build_retrieval_filter_set(
    *,
    auth: AuthContext,
    request: RetrievalRequest,
) -> RetrievalFilterSet:
    access_filter = build_access_filter(auth)
    request_metadata = dict(request.metadata_filter)
    requested_tenant = request_metadata.pop("tenant_id", None)
    if requested_tenant is not None and requested_tenant != auth.tenant_id:
        raise RetrievalError(
            code=RETRIEVAL_FORBIDDEN_FILTER,
            message="Retrieval metadata filter cannot widen tenant scope.",
            details=_safe_details(
                request=request,
                auth=auth,
                error_code=RETRIEVAL_FORBIDDEN_FILTER,
            ),
            status_code=403,
        )

    return RetrievalFilterSet(
        tenant_id=access_filter.tenant_id,
        user_id=access_filter.user_id,
        roles=access_filter.roles,
        department=access_filter.department,
        permissions=access_filter.permissions,
        metadata_filter=FrozenDict(request_metadata),
        acl_filter=access_filter.acl_filter,
        include_deleted=False,
    )


def to_vector_acl_filter(filters: RetrievalFilterSet) -> AclFilter:
    return AclFilter(
        user_id=filters.user_id,
        roles=list(filters.roles),
        department=filters.department,
        permissions=list(filters.permissions),
    )


def to_vector_metadata_filters(filters: RetrievalFilterSet) -> list[MetadataFilter]:
    return [
        MetadataFilter(key=key, value=value)
        for key, value in sorted(filters.metadata_filter.items())
    ]


def to_sparse_filter_payload(filters: RetrievalFilterSet) -> dict[str, object]:
    return {
        "tenant_id": filters.tenant_id,
        "user_id": filters.user_id,
        "roles": filters.roles,
        "department": filters.department,
        "permissions": filters.permissions,
        "metadata_filter": dict(filters.metadata_filter),
        "acl_filter": dict(filters.acl_filter),
        "include_deleted": filters.include_deleted,
    }


def _safe_details(
    *,
    request: RetrievalRequest,
    auth: AuthContext | None,
    error_code: str,
) -> Mapping[str, object]:
    details: dict[str, object] = {
        "request_id": request.request_id,
        "trace_id": request.trace_id,
        "top_k": request.top_k,
        "error_code": error_code,
    }
    if auth is not None:
        details["tenant_id"] = auth.tenant_id
        details["user_id"] = auth.user_id
    return details

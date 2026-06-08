from collections.abc import Iterator, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_serializer

from packages.auth.context import AuthContext

DOCUMENT_UPLOAD_PERMISSIONS = frozenset({"document:upload", "document:manage"})
DOCUMENT_MANAGE_PERMISSIONS = frozenset({"document:manage"})
RAG_QUERY_PERMISSIONS = frozenset({"document:read", "retrieval:query"})
AGENT_RUN_PERMISSIONS = frozenset({"agent:run"})


class FrozenDict(Mapping[str, object]):
    def __init__(self, values: Mapping[str, object] | None = None) -> None:
        self._values = dict(values or {})

    def __getitem__(self, key: str) -> object:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return repr(self._values)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Mapping):
            return self._values == dict(other)
        return False

    def to_dict(self) -> dict[str, object]:
        return dict(self._values)


class AccessFilter(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    tenant_id: str
    user_id: str
    roles: tuple[str, ...] = ()
    department: str | None = None
    permissions: tuple[str, ...] = ()
    metadata_filter: FrozenDict = Field(default_factory=FrozenDict)
    acl_filter: FrozenDict = Field(default_factory=FrozenDict)

    @field_serializer("metadata_filter", "acl_filter")
    def _serialize_frozen_dict(self, value: FrozenDict) -> dict[str, object]:
        return value.to_dict()


def build_access_filter(auth: AuthContext) -> AccessFilter:
    metadata_filter: dict[str, object] = {"tenant_id": auth.tenant_id}

    acl_filter: dict[str, object] = {
        "tenant_id": auth.tenant_id,
        "user_id": auth.user_id,
        "roles": auth.roles,
        "department": auth.department,
        "permissions": auth.permissions,
    }
    return AccessFilter(
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        roles=auth.roles,
        department=auth.department,
        permissions=auth.permissions,
        metadata_filter=FrozenDict(metadata_filter),
        acl_filter=FrozenDict(acl_filter),
    )


def has_document_upload_permission(auth: AuthContext) -> bool:
    return bool(DOCUMENT_UPLOAD_PERMISSIONS.intersection(auth.permissions))


def has_document_manage_permission(auth: AuthContext) -> bool:
    return bool(DOCUMENT_MANAGE_PERMISSIONS.intersection(auth.permissions))


def has_rag_query_permission(auth: AuthContext) -> bool:
    return RAG_QUERY_PERMISSIONS.issubset(set(auth.permissions))


def has_agent_run_permission(auth: AuthContext) -> bool:
    return AGENT_RUN_PERMISSIONS.issubset(set(auth.permissions))

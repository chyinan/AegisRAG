from __future__ import annotations

from collections.abc import Mapping

from packages.auth.context import AuthContext
from packages.auth.policies import build_access_filter
from packages.vectorstores.acl import acl_allows
from packages.vectorstores.dto import AclFilter


def acl_filter_from_auth(auth: AuthContext) -> AclFilter:
    access_filter = build_access_filter(auth)
    return AclFilter(
        user_id=access_filter.user_id,
        roles=list(access_filter.roles),
        department=access_filter.department,
        permissions=list(access_filter.permissions),
    )


def acl_allows_auth(acl: Mapping[str, object], auth: AuthContext) -> bool:
    return acl_allows(acl, acl_filter_from_auth(auth))

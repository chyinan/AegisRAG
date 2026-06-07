from __future__ import annotations

from collections.abc import Mapping

from packages.vectorstores.dto import AclFilter


def acl_allows(acl: Mapping[str, object], acl_filter: AclFilter) -> bool:
    denied_users = _text_set(acl.get("denied_users"))
    if acl_filter.user_id in denied_users:
        return False

    visibility = str(acl.get("visibility", "tenant")).strip().lower()
    if visibility in {"public", "tenant"}:
        return True

    allowed_users = _text_set(acl.get("allowed_users"))
    if acl_filter.user_id in allowed_users:
        return True

    allowed_roles = _text_set(acl.get("allowed_roles"))
    if allowed_roles and allowed_roles.intersection(acl_filter.roles):
        return True

    allowed_departments = _text_set(acl.get("allowed_departments"))
    if acl_filter.department is not None and acl_filter.department in allowed_departments:
        return True

    allowed_permissions = _text_set(acl.get("allowed_permissions"))
    if allowed_permissions and allowed_permissions.intersection(acl_filter.permissions):
        return True

    return False


def _text_set(value: object) -> set[str]:
    if isinstance(value, str):
        return {value.strip()} if value.strip() else set()
    if isinstance(value, list | tuple | set):
        return {str(item).strip() for item in value if str(item).strip()}
    return set()

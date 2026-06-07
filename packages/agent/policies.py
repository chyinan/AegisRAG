from packages.auth.context import AuthContext


def has_tool_permission(auth: AuthContext, permission: str) -> bool:
    required = permission.strip()
    return bool(required) and required in set(auth.permissions)

import os
from collections.abc import Mapping
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from packages.auth.context import AuthContext
from packages.auth.exceptions import AuthContextInvalidError, AuthContextRequiredError
from packages.auth.parsers import (
    JwtAuthSettings,
    OpenWebUIServiceTokenSettings,
    decode_jwt_token,
    parse_dev_auth_headers,
    parse_openwebui_service_token,
)
from packages.common.context import AuthenticatedRequestContext, AuthMethod, RequestContext

_AUTH_METHODS: frozenset[AuthMethod] = frozenset(
    {"jwt_bearer", "openwebui_service_token", "dev_headers"}
)

RequestIdHeader = Annotated[str | None, Header(alias="X-Request-ID")]
TraceIdHeader = Annotated[str | None, Header(alias="X-Trace-ID")]
SessionIdHeader = Annotated[str | None, Header(alias="X-Session-ID")]
DevUserHeader = Annotated[str | None, Header(alias="X-User-ID")]
DevTenantHeader = Annotated[str | None, Header(alias="X-Tenant-ID")]
DevRolesHeader = Annotated[str | None, Header(alias="X-Roles")]
DevDepartmentHeader = Annotated[str | None, Header(alias="X-Department")]
DevPermissionsHeader = Annotated[str | None, Header(alias="X-Permissions")]

_bearer_scheme = HTTPBearer(auto_error=False)
BearerCredentialsDep = Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)]


def request_context_from_headers(headers: Mapping[str, str | None]) -> RequestContext:
    return RequestContext(
        request_id=_header_or_uuid(_header_value(headers, "X-Request-ID")),
        trace_id=_header_or_uuid(_header_value(headers, "X-Trace-ID")),
        session_id=_header_value(headers, "X-Session-ID"),
    )


def get_request_context(
    request: Request,
    x_request_id: RequestIdHeader = None,
    x_trace_id: TraceIdHeader = None,
    x_session_id: SessionIdHeader = None,
) -> RequestContext:
    existing = _request_context_from_state(request)
    if existing is not None:
        return existing

    context = RequestContext(
        request_id=_header_or_uuid(x_request_id),
        trace_id=_header_or_uuid(x_trace_id),
        session_id=x_session_id,
    )
    request.state.request_context = context
    return context


def get_auth_context(
    request: Request,
    credentials: BearerCredentialsDep = None,
    x_user_id: DevUserHeader = None,
    x_tenant_id: DevTenantHeader = None,
    x_roles: DevRolesHeader = None,
    x_department: DevDepartmentHeader = None,
    x_permissions: DevPermissionsHeader = None,
) -> AuthContext:
    existing = _auth_context_from_state(request)
    if existing is not None:
        return existing

    if credentials is not None:
        service_token_settings = OpenWebUIServiceTokenSettings.from_environment()
        if service_token_settings.has_records():
            try:
                auth_context = parse_openwebui_service_token(
                    credentials.credentials,
                    service_token_settings,
                )
            except AuthContextInvalidError as exc:
                if exc.details != {"reason": "openwebui_service_token_unknown"}:
                    raise
            else:
                return _store_auth_context(
                    request,
                    auth_context,
                    auth_method="openwebui_service_token",
                )

        auth_context = decode_jwt_token(credentials.credentials, JwtAuthSettings.from_environment())
        return _store_auth_context(request, auth_context, auth_method="jwt_bearer")

    if _dev_auth_headers_enabled():
        auth_context = parse_dev_auth_headers(
            {
                "X-User-ID": x_user_id,
                "X-Tenant-ID": x_tenant_id,
                "X-Roles": x_roles,
                "X-Department": x_department,
                "X-Permissions": x_permissions,
            }
        )
        return _store_auth_context(request, auth_context, auth_method="dev_headers")

    raise AuthContextRequiredError(details={"missing": ["auth_context"]})


def get_authenticated_request_context(
    request: Request,
    request_context: Annotated[RequestContext, Depends(get_request_context)],
    auth_context: Annotated[AuthContext, Depends(get_auth_context)],
) -> AuthenticatedRequestContext:
    request.state.auth_context = auth_context
    return AuthenticatedRequestContext(
        request_id=request_context.request_id,
        trace_id=request_context.trace_id,
        session_id=request_context.session_id,
        auth_method=_auth_method_from_state(request),
        auth=auth_context,
    )


RequestContextDep = Annotated[RequestContext, Depends(get_request_context)]
AuthContextDep = Annotated[AuthContext, Depends(get_auth_context)]
AuthenticatedRequestContextDep = Annotated[
    AuthenticatedRequestContext,
    Depends(get_authenticated_request_context),
]


def _dev_auth_headers_enabled() -> bool:
    flag_enabled = os.getenv("ENABLE_DEV_AUTH_HEADERS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    app_env = os.getenv("APP_ENV", "").strip().lower()
    return flag_enabled and app_env in {"local", "dev", "development", "test", "testing"}


def _header_or_uuid(value: str | None) -> str:
    if value is None:
        return str(uuid4())
    normalized = value.strip()
    return normalized or str(uuid4())


def _header_value(headers: Mapping[str, str | None], name: str) -> str | None:
    return headers.get(name) or headers.get(name.lower())


def _request_context_from_state(request: Request) -> RequestContext | None:
    candidate = getattr(request.state, "request_context", None)
    if isinstance(candidate, RequestContext):
        return candidate
    return None


def _auth_context_from_state(request: Request) -> AuthContext | None:
    candidate = getattr(request.state, "auth_context", None)
    if isinstance(candidate, AuthContext):
        return candidate
    return None


def _auth_method_from_state(request: Request) -> AuthMethod | None:
    candidate: object = getattr(request.state, "auth_method", None)
    if candidate in _AUTH_METHODS:
        return candidate
    return None


def _store_auth_context(
    request: Request,
    auth_context: AuthContext,
    *,
    auth_method: AuthMethod,
) -> AuthContext:
    request.state.auth_context = auth_context
    request.state.auth_method = auth_method
    return auth_context

# pattern: Imperative Shell

import asyncio
import os
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from functools import lru_cache
from typing import Annotated
from uuid import uuid4

from fastapi import Depends, Header, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.factories.common import create_session_factory
from packages.auth.context import AuthContext
from packages.auth.exceptions import AuthContextInvalidError, AuthContextRequiredError
from packages.auth.oidc import OidcJwtVerifier
from packages.auth.parsers import (
    JwtAuthSettings,
    ServiceTokenSettings,
    decode_jwt_token,
    parse_dev_auth_headers,
    parse_jwt_claims,
    parse_service_token,
    validate_local_session_claims,
)
from packages.auth.revocation import (
    RedisTokenRevocationStore,
    TokenRevocationStore,
    TokenRevocationUnavailableError,
)
from packages.auth.session_validation import LocalAuthSessionValidator
from packages.common.config import load_settings
from packages.common.context import AuthenticatedRequestContext, AuthMethod, RequestContext
from packages.common.tenant_context import reset_tenant_id, set_tenant_id, tenant_scope

_AUTH_METHODS: frozenset[AuthMethod] = frozenset(
    {"jwt_bearer", "service_token", "dev_headers"}
)
_OIDC_VERIFIERS: set[OidcJwtVerifier] = set()

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


async def get_auth_context(
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
        service_token_settings = ServiceTokenSettings.from_environment()
        if service_token_settings.has_records():
            try:
                auth_context = parse_service_token(
                    credentials.credentials,
                    service_token_settings,
                )
            except AuthContextInvalidError as exc:
                if exc.details != {"reason": "service_token_unknown"}:
                    raise
            else:
                return _store_auth_context(
                    request,
                    auth_context,
                    auth_method="service_token",
                )

        jwt_settings = JwtAuthSettings.from_environment()
        if jwt_settings.jwks_url:
            claims = await _cached_oidc_verifier(jwt_settings).verify(credentials.credentials)
            if jwt_settings.require_local_session:
                validate_local_session_claims(claims)
            if "type" not in claims:
                claims = {**claims, "type": "access"}
            auth_context = parse_jwt_claims(claims)
        else:
            auth_context = decode_jwt_token(credentials.credentials, jwt_settings)
        await _validate_token_revocation(auth_context)
        with tenant_scope(auth_context.tenant_id):
            await _validate_local_auth_session(auth_context)
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


async def _validate_local_auth_session(auth: AuthContext) -> None:
    if auth.identity_provider != "local":
        return
    database_url = load_settings().database_url
    if database_url is None:
        raise AuthContextInvalidError(details={"reason": "local_auth_storage_unavailable"})
    session_factory = _cached_session_factory(database_url)
    async with session_factory() as session:
        await LocalAuthSessionValidator(session).validate(auth)


@lru_cache(maxsize=4)
def _cached_session_factory(database_url: str) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(database_url)


@lru_cache(maxsize=4)
def _cached_token_revocation_store(redis_url: str) -> RedisTokenRevocationStore:
    return RedisTokenRevocationStore(redis_url)


def get_token_revocation_store() -> TokenRevocationStore | None:
    redis_url = load_settings().redis_url
    if redis_url is None or not redis_url.strip():
        return None
    return _cached_token_revocation_store(redis_url)


def validate_oidc_configuration() -> None:
    settings = JwtAuthSettings.from_environment()
    if settings.jwks_url:
        _cached_oidc_verifier(settings)


@lru_cache(maxsize=4)
def _cached_oidc_verifier(settings: JwtAuthSettings) -> OidcJwtVerifier:
    algorithms = tuple(
        item.strip()
        for item in os.getenv("JWT_JWKS_ALGORITHMS", "RS256").split(",")
        if item.strip()
    )
    assert settings.jwks_url is not None
    verifier = OidcJwtVerifier(
        settings.jwks_url,
        algorithms=algorithms,
        issuer=settings.issuer,
        audience=settings.audience,
        cache_seconds=settings.jwks_cache_seconds,
        timeout_seconds=settings.oidc_timeout_seconds,
    )
    _OIDC_VERIFIERS.add(verifier)
    return verifier


async def close_cached_oidc_verifiers() -> None:
    verifiers = tuple(_OIDC_VERIFIERS)
    _OIDC_VERIFIERS.clear()
    _cached_oidc_verifier.cache_clear()
    await asyncio.gather(*(verifier.aclose() for verifier in verifiers))


async def _validate_token_revocation(auth: AuthContext) -> None:
    if auth.jti is None:
        return
    store = get_token_revocation_store()
    if store is None:
        if auth.identity_provider == "local":
            raise AuthContextInvalidError(details={"reason": "token_revocation_unavailable"})
        return
    try:
        revoked = await store.is_revoked(auth.jti)
    except TokenRevocationUnavailableError as exc:
        raise AuthContextInvalidError(
            details={"reason": "token_revocation_unavailable"}
        ) from exc
    if revoked:
        raise AuthContextInvalidError(details={"reason": "token_revoked"})
    if auth.session_id is not None:
        try:
            family_revoked = await store.is_family_revoked(auth.session_id)
        except TokenRevocationUnavailableError as exc:
            raise AuthContextInvalidError(
                details={"reason": "token_revocation_unavailable"}
            ) from exc
        if family_revoked:
            raise AuthContextInvalidError(details={"reason": "token_revoked"})


async def revalidate_authenticated_context(
    context: AuthenticatedRequestContext,
) -> AuthenticatedRequestContext:
    """Recheck the live session before a durable execution phase.

    Every API request still performs normal authentication first. This additional
    check prevents a long-lived request from ignoring local session revocation
    or a token/session expiry while waiting for a model/tool. No credentials are
    persisted in durable state, and an external identity provider retains its
    existing token/claim revocation contract.
    """
    now = datetime.now(UTC)
    for deadline in (context.auth.token_expires_at, context.auth.session_expires_at):
        if deadline is not None and deadline <= now:
            raise AuthContextInvalidError(details={"reason": "auth_context_expired"})
    await _validate_token_revocation(context.auth)
    with tenant_scope(context.auth.tenant_id):
        await _validate_local_auth_session(context.auth)
    return context


async def get_authenticated_request_context(
    request: Request,
    request_context: Annotated[RequestContext, Depends(get_request_context)],
    auth_context: Annotated[AuthContext, Depends(get_auth_context)],
) -> AsyncIterator[AuthenticatedRequestContext]:
    request.state.auth_context = auth_context
    token = set_tenant_id(auth_context.tenant_id)
    context = AuthenticatedRequestContext(
        request_id=request_context.request_id,
        trace_id=request_context.trace_id,
        session_id=request_context.session_id,
        auth_method=_auth_method_from_state(request),
        auth=auth_context,
    )
    try:
        yield context
    finally:
        reset_tenant_id(token)


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

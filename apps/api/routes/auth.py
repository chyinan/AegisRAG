"""Authentication routes — local username/password login."""

# pattern: Imperative Shell

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from apps.api.dependencies import (
    AuthContextDep,
    BearerCredentialsDep,
    RequestContextDep,
    get_token_revocation_store,
)
from apps.api.factories.common import create_session_factory
from packages.auth.login_service import LoginService
from packages.auth.revocation import TokenRevocationStore, TokenRevocationUnavailableError
from packages.common.config import load_settings
from packages.common.envelope import ApiResponse, success_response
from packages.common.errors import DomainError
from packages.common.tenant_context import tenant_scope

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    tenant_id: str = Field(..., min_length=1, max_length=36)
    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=255)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1)


class LoginResponseData(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: str
    display_name: str
    tenant_id: str
    roles: list[str] = []
    permissions: list[str] = []


class LogoutResponseData(BaseModel):
    revoked: bool = True


async def get_login_service() -> AsyncIterator[LoginService]:
    settings = load_settings()
    session_factory = create_session_factory(settings.database_url)
    async with session_factory() as session:
        yield LoginService(session, revocation_store=get_token_revocation_store())


LoginServiceDep = Annotated[LoginService, Depends(get_login_service)]
TokenRevocationStoreDep = Annotated[
    TokenRevocationStore | None,
    Depends(get_token_revocation_store),
]


@router.post("/logout", response_model=ApiResponse[LogoutResponseData])
async def logout(
    context: RequestContextDep,
    auth: AuthContextDep,
    credentials: BearerCredentialsDep,
    store: TokenRevocationStoreDep,
) -> ApiResponse[LogoutResponseData]:
    if (
        credentials is None
        or auth.jti is None
        or auth.session_id is None
        or auth.session_expires_at is None
        or auth.token_expires_at is None
    ):
        raise DomainError(
            code="AUTH_TOKEN_NOT_REVOCABLE",
            message="The supplied access token cannot be revoked.",
            status_code=400,
        )
    if store is None:
        raise DomainError(
            code="AUTH_REVOCATION_UNAVAILABLE",
            message="Token revocation is temporarily unavailable.",
            status_code=503,
        )
    try:
        await store.revoke_family(
            auth.session_id,
            auth.session_expires_at,
        )
        await store.revoke(auth.jti, auth.token_expires_at)
    except TokenRevocationUnavailableError as exc:
        raise DomainError(
            code="AUTH_REVOCATION_UNAVAILABLE",
            message="Token revocation is temporarily unavailable.",
            status_code=503,
        ) from exc
    return success_response(
        request_id=context.request_id,
        data=LogoutResponseData(),
    )


@router.post("/refresh", response_model=ApiResponse[LoginResponseData])
async def refresh(
    body: RefreshRequest,
    context: RequestContextDep,
    service: LoginServiceDep,
) -> ApiResponse[LoginResponseData]:
    result = await service.refresh(refresh_token=body.refresh_token)
    return success_response(
        request_id=context.request_id,
        data=LoginResponseData(
            access_token=result.access_token,
            refresh_token=result.refresh_token,
            expires_in=result.expires_in,
            user_id=result.user_id,
            display_name=result.display_name,
            tenant_id=result.tenant_id,
            roles=result.roles,
            permissions=result.permissions,
        ),
    )


@router.post("/login", response_model=ApiResponse[LoginResponseData])
async def login(
    body: LoginRequest,
    context: RequestContextDep,
    service: LoginServiceDep,
) -> ApiResponse[LoginResponseData]:
    with tenant_scope(body.tenant_id):
        result = await service.login(
            tenant_id=body.tenant_id,
            username=body.username,
            password=body.password,
        )
    return success_response(
        request_id=context.request_id,
        data=LoginResponseData(
            access_token=result.access_token,
            refresh_token=result.refresh_token,
            expires_in=result.expires_in,
            user_id=result.user_id,
            display_name=result.display_name,
            tenant_id=result.tenant_id,
            roles=result.roles,
            permissions=result.permissions,
        ),
    )

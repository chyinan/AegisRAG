"""Authentication routes — local username/password login."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from apps.api.dependencies import RequestContextDep
from apps.api.factories.common import create_session_factory
from packages.auth.login_service import LoginService
from packages.common.config import load_settings
from packages.common.envelope import ApiResponse, success_response

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
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


async def get_login_service() -> AsyncIterator[LoginService]:
    settings = load_settings()
    session_factory = create_session_factory(settings.database_url)
    async with session_factory() as session:
        yield LoginService(session)


LoginServiceDep = Annotated[LoginService, Depends(get_login_service)]


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
    result = await service.login(username=body.username, password=body.password)
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

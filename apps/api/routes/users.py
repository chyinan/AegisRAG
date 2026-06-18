"""User management routes — GET/POST for /users."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from apps.api.dependencies import AuthenticatedRequestContextDep, RequestContextDep
from apps.api.factories.common import create_session_factory
from packages.auth.user_service import UserService
from packages.common.config import load_settings
from packages.common.context import AuthenticatedRequestContext
from packages.common.envelope import ApiResponse, success_response
from packages.common.errors import DomainError

router = APIRouter(prefix="/users", tags=["users"])

_ADMIN_PERMISSION = "admin:settings"


class CreateUserRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=8, max_length=255)
    email: str | None = None
    display_name: str = Field(..., min_length=1, max_length=255)
    group_id: str | None = None


class UserResponse(BaseModel):
    id: str
    username: str
    email: str | None
    display_name: str
    is_active: bool
    group_id: str | None
    created_at: str | None
    updated_at: str | None


async def get_user_service() -> AsyncIterator[UserService]:
    settings = load_settings()
    session_factory = create_session_factory(settings.database_url)
    async with session_factory() as session:
        yield UserService(session)


UserServiceDep = Annotated[UserService, Depends(get_user_service)]


def _require_admin(context: AuthenticatedRequestContext) -> None:
    if _ADMIN_PERMISSION not in context.auth.permissions:
        raise DomainError(
            code="AUTH_FORBIDDEN",
            message="Admin permission required.",
            status_code=403,
        )


@router.get("", response_model=ApiResponse[list[UserResponse]])
async def list_users(
    context: AuthenticatedRequestContextDep,
    service: UserServiceDep,
) -> ApiResponse[list[UserResponse]]:
    _require_admin(context)
    users = await service.list_users()
    return success_response(
        request_id=context.request_id,
        data=[UserResponse(**u) for u in users],
    )


@router.post("", response_model=ApiResponse[UserResponse])
async def create_user(
    body: CreateUserRequest,
    context: AuthenticatedRequestContextDep,
    service: UserServiceDep,
) -> ApiResponse[UserResponse]:
    _require_admin(context)
    user = await service.create_user(
        username=body.username,
        password=body.password,
        email=body.email,
        display_name=body.display_name,
        group_id=body.group_id,
    )
    return success_response(
        request_id=context.request_id,
        data=UserResponse(**user),
    )

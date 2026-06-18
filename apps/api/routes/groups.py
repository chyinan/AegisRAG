"""Group management routes — CRUD for /groups."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from pydantic import BaseModel, Field

from apps.api.dependencies import AuthenticatedRequestContextDep, RequestContextDep
from apps.api.factories.common import create_session_factory
from packages.auth.group_service import GroupService
from packages.common.config import load_settings
from packages.common.context import AuthenticatedRequestContext
from packages.common.envelope import ApiResponse, success_response
from packages.common.errors import DomainError

router = APIRouter(prefix="/groups", tags=["groups"])

_ADMIN_PERMISSION = "admin:settings"


class CreateGroupRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None


class UpdateGroupRequest(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None


class GroupResponse(BaseModel):
    id: str
    name: str
    description: str | None
    created_at: str | None
    updated_at: str | None


async def get_group_service() -> AsyncIterator[GroupService]:
    settings = load_settings()
    session_factory = create_session_factory(settings.database_url)
    async with session_factory() as session:
        yield GroupService(session)


GroupServiceDep = Annotated[GroupService, Depends(get_group_service)]


def _require_admin(context: AuthenticatedRequestContext) -> None:
    if _ADMIN_PERMISSION not in context.auth.permissions:
        raise DomainError(
            code="AUTH_FORBIDDEN",
            message="Admin permission required.",
            status_code=403,
        )


@router.get("", response_model=ApiResponse[list[GroupResponse]])
async def list_groups(
    context: AuthenticatedRequestContextDep,
    service: GroupServiceDep,
) -> ApiResponse[list[GroupResponse]]:
    _require_admin(context)
    groups = await service.list_groups()
    return success_response(
        request_id=context.request_id,
        data=[GroupResponse(**g) for g in groups],
    )


@router.post("", response_model=ApiResponse[GroupResponse])
async def create_group(
    body: CreateGroupRequest,
    context: AuthenticatedRequestContextDep,
    service: GroupServiceDep,
) -> ApiResponse[GroupResponse]:
    _require_admin(context)
    group = await service.create_group(name=body.name, description=body.description)
    return success_response(
        request_id=context.request_id,
        data=GroupResponse(**group),
    )


@router.get("/{group_id}", response_model=ApiResponse[GroupResponse])
async def get_group(
    group_id: str,
    context: AuthenticatedRequestContextDep,
    service: GroupServiceDep,
) -> ApiResponse[GroupResponse]:
    _require_admin(context)
    group = await service.get_group(group_id=group_id)
    return success_response(
        request_id=context.request_id,
        data=GroupResponse(**group),
    )


@router.put("/{group_id}", response_model=ApiResponse[GroupResponse])
async def update_group(
    group_id: str,
    body: UpdateGroupRequest,
    context: AuthenticatedRequestContextDep,
    service: GroupServiceDep,
) -> ApiResponse[GroupResponse]:
    _require_admin(context)
    group = await service.update_group(
        group_id=group_id,
        name=body.name,
        description=body.description,
    )
    return success_response(
        request_id=context.request_id,
        data=GroupResponse(**group),
    )


@router.delete("/{group_id}")
async def delete_group(
    group_id: str,
    response: Response,
    context: AuthenticatedRequestContextDep,
    service: GroupServiceDep,
) -> None:
    _require_admin(context)
    await service.delete_group(group_id=group_id)
    response.status_code = status.HTTP_204_NO_CONTENT

"""Thin authenticated routes for configured-root continuous folder synchronization."""

from __future__ import annotations

from fastapi import APIRouter, Query

from apps.api.dependencies import AuthenticatedRequestContextDep
from apps.api.factories.folder_sync import FolderSyncServiceDep
from packages.common.envelope import ApiResponse, success_response
from packages.ingestion.folder_sync.dto import (
    FolderSyncCandidatePage,
    FolderSyncPlanCommand,
    FolderSyncReconcileCommand,
    FolderSyncStatus,
)

router = APIRouter(prefix="/sources/folder/sync", tags=["sources"])


@router.put("/plan", response_model=ApiResponse[FolderSyncStatus])
async def configure_folder_sync(
    context: AuthenticatedRequestContextDep,
    service: FolderSyncServiceDep,
    body: FolderSyncPlanCommand,
) -> ApiResponse[FolderSyncStatus]:
    result = await service.configure(context, body)
    return success_response(request_id=context.request_id, data=result)


@router.get("/plans", response_model=ApiResponse[list[FolderSyncStatus]])
async def list_folder_sync(
    context: AuthenticatedRequestContextDep,
    service: FolderSyncServiceDep,
) -> ApiResponse[list[FolderSyncStatus]]:
    return success_response(request_id=context.request_id, data=await service.list_status(context))


@router.get("/plans/{plan_id}", response_model=ApiResponse[FolderSyncStatus])
async def get_folder_sync(
    plan_id: str,
    context: AuthenticatedRequestContextDep,
    service: FolderSyncServiceDep,
) -> ApiResponse[FolderSyncStatus]:
    return success_response(
        request_id=context.request_id, data=await service.get_status(context, plan_id)
    )


@router.post("/plans/{plan_id}/batch", response_model=ApiResponse[FolderSyncStatus])
async def run_folder_sync_batch(
    plan_id: str,
    context: AuthenticatedRequestContextDep,
    service: FolderSyncServiceDep,
    due_only: bool = Query(default=False),
) -> ApiResponse[FolderSyncStatus]:
    return success_response(
        request_id=context.request_id,
        data=await service.run_batch(context, plan_id, due_only=due_only),
    )


@router.get("/plans/{plan_id}/candidates", response_model=ApiResponse[FolderSyncCandidatePage])
async def list_folder_sync_candidates(
    plan_id: str,
    context: AuthenticatedRequestContextDep,
    service: FolderSyncServiceDep,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
) -> ApiResponse[FolderSyncCandidatePage]:
    return success_response(
        request_id=context.request_id,
        data=await service.list_candidates(context, plan_id, offset=offset, limit=limit),
    )


@router.post("/plans/{plan_id}/reconcile", response_model=ApiResponse[FolderSyncStatus])
async def reconcile_folder_sync(
    plan_id: str,
    context: AuthenticatedRequestContextDep,
    service: FolderSyncServiceDep,
    body: FolderSyncReconcileCommand,
) -> ApiResponse[FolderSyncStatus]:
    return success_response(
        request_id=context.request_id,
        data=await service.reconcile(context, plan_id, body),
    )

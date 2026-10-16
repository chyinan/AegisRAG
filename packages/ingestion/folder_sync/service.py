"""Application-layer incremental folder scans and explicitly enabled reconciliation."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import stat
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from packages.auth.policies import has_document_manage_permission
from packages.common.audit import AuditEvent, AuditPort, AuditResource, AuditStatus
from packages.common.config import AppSettings
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError
from packages.data.dto import DocumentDeleteCommand, DocumentDeleteResult, UploadDocumentResult
from packages.ingestion.connectors import ConnectorDocument, FolderConnector
from packages.ingestion.folder_sync.dto import (
    FolderSyncCandidatePage,
    FolderSyncPlan,
    FolderSyncPlanCommand,
    FolderSyncReconcileCommand,
    FolderSyncSettings,
    FolderSyncStatus,
    ManagedFolderSource,
    candidate_items,
)
from packages.ingestion.folder_sync.storage import sync_error


class FolderSyncStore(Protocol):
    async def create(self, plan: FolderSyncPlan) -> None: ...
    async def get(self, *, tenant_id: str, plan_id: str) -> FolderSyncPlan | None: ...
    async def list(self, *, tenant_id: str) -> list[FolderSyncPlan]: ...
    async def claim(
        self, *, tenant_id: str, plan_id: str, lease_seconds: float, due_only: bool = False
    ) -> tuple[FolderSyncPlan, str] | None: ...
    async def save(self, plan: FolderSyncPlan, token: str, *, release: bool = False) -> None: ...


class FolderDocumentImporter(Protocol):
    async def import_document(
        self, *, context: AuthenticatedRequestContext, document: ConnectorDocument
    ) -> UploadDocumentResult: ...


class FolderDocumentDeleter(Protocol):
    async def delete(
        self, context: AuthenticatedRequestContext, command: DocumentDeleteCommand
    ) -> DocumentDeleteResult: ...


class FolderSyncService:
    def __init__(
        self,
        *,
        store: FolderSyncStore,
        importer: FolderDocumentImporter,
        lifecycle: FolderDocumentDeleter,
        audit: AuditPort,
        settings: AppSettings,
        sync_settings: FolderSyncSettings,
    ) -> None:
        self._store = store
        self._importer = importer
        self._lifecycle = lifecycle
        self._audit = audit
        self._settings = settings
        self._sync_settings = sync_settings

    def _authorize(self, context: AuthenticatedRequestContext) -> None:
        if not has_document_manage_permission(context.auth):
            raise sync_error("DOCUMENT_MANAGE_FORBIDDEN", "Document management is required.", 403)
        if not self._sync_settings.enabled:
            raise sync_error("FOLDER_SYNC_DISABLED", "Folder synchronization is disabled.", 503)

    async def configure(
        self,
        context: AuthenticatedRequestContext,
        command: FolderSyncPlanCommand,
    ) -> FolderSyncStatus:
        self._authorize(context)
        # Validate only the administrator-configured tenant directory, never a caller path.
        root_identity = None
        if command.enabled:
            _, root = await asyncio.to_thread(_connector, self._settings, context)
            root_identity = await asyncio.to_thread(_root_identity, root)
        plan_id = str(uuid5(NAMESPACE_URL, f"folder-sync:{context.auth.tenant_id}:folder"))
        plan = await self._store.get(tenant_id=context.auth.tenant_id, plan_id=plan_id)
        if plan is None:
            plan = FolderSyncPlan(
                id=plan_id,
                tenant_id=context.auth.tenant_id,
                created_by=context.auth.user_id,
                root_identity=root_identity,
                **command.model_dump(),
            )
            await self._store.create(plan)
        else:
            claim = await self._store.claim(
                tenant_id=context.auth.tenant_id,
                plan_id=plan_id,
                lease_seconds=30,
            )
            if claim is None:
                raise sync_error("FOLDER_SYNC_BUSY", "Folder sync batch is already running.", 409)
            plan, token = claim
            if root_identity is not None:
                if plan.root_identity is not None and plan.root_identity != root_identity:
                    await self._store.save(plan, token, release=True)
                    raise sync_error(
                        "FOLDER_SYNC_ROOT_CHANGED", "Folder sync root identity changed."
                    )
                plan.root_identity = root_identity
            for name, value in command.model_dump().items():
                setattr(plan, name, value)
            if not plan.delete_missing:
                plan.deletion_candidates.clear()
            await self._store.save(plan, token, release=True)
        await self._record(context, plan, "folder.sync.configure")
        return FolderSyncStatus.from_plan(plan)

    async def list_status(self, context: AuthenticatedRequestContext) -> list[FolderSyncStatus]:
        self._authorize(context)
        return [
            FolderSyncStatus.from_plan(plan)
            for plan in await self._store.list(tenant_id=context.auth.tenant_id)
        ]

    async def get_status(
        self,
        context: AuthenticatedRequestContext,
        plan_id: str,
    ) -> FolderSyncStatus:
        self._authorize(context)
        return FolderSyncStatus.from_plan(await self._get(context, plan_id))

    async def _get(self, context: AuthenticatedRequestContext, plan_id: str) -> FolderSyncPlan:
        plan = await self._store.get(tenant_id=context.auth.tenant_id, plan_id=plan_id)
        if plan is None:
            raise sync_error("FOLDER_SYNC_NOT_FOUND", "Folder sync plan was not found.", 404)
        return plan

    async def list_candidates(
        self,
        context: AuthenticatedRequestContext,
        plan_id: str,
        *,
        offset: int = 0,
        limit: int = 25,
    ) -> FolderSyncCandidatePage:
        self._authorize(context)
        if offset < 0 or not 1 <= limit <= 100:
            raise sync_error("FOLDER_SYNC_PAGE_INVALID", "Candidate page is invalid.")
        plan = await self._get(context, plan_id)
        total = len(plan.deletion_candidates)
        return FolderSyncCandidatePage(
            revision=plan.revision,
            total=total,
            items=candidate_items(plan, offset=offset, limit=limit),
            next_offset=offset + limit if offset + limit < total else None,
        )

    async def reconcile(
        self,
        context: AuthenticatedRequestContext,
        plan_id: str,
        command: FolderSyncReconcileCommand,
    ) -> FolderSyncStatus:
        self._authorize(context)
        await self._get(context, plan_id)
        claim = await self._store.claim(
            tenant_id=context.auth.tenant_id,
            plan_id=plan_id,
            lease_seconds=self._sync_settings.batch_timeout_seconds + 30,
        )
        if claim is None:
            raise sync_error("FOLDER_SYNC_BUSY", "Folder sync batch is already running.", 409)
        plan, token = claim
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._sync_settings.batch_timeout_seconds):
                if command.expected_revision != plan.revision:
                    raise sync_error(
                        "FOLDER_SYNC_REVISION_CHANGED", "Review the latest candidates.", 409
                    )
                if not plan.delete_missing or plan.status != "completed":
                    raise sync_error(
                        "FOLDER_SYNC_REVIEW_REQUIRED", "Complete the candidate scan first.", 409
                    )
                _, root = await asyncio.to_thread(_connector, self._settings, context)
                if await asyncio.to_thread(_root_identity, root) != plan.root_identity:
                    raise sync_error(
                        "FOLDER_SYNC_ROOT_CHANGED", "Folder sync root identity changed."
                    )
                by_document = {
                    document_id: path for path, document_id in plan.deletion_candidates.items()
                }
                if len(set(command.document_ids)) != len(command.document_ids) or any(
                    document_id not in by_document for document_id in command.document_ids
                ):
                    raise sync_error(
                        "FOLDER_SYNC_CANDIDATE_INVALID", "Select current candidate document IDs."
                    )
                for document_id in command.document_ids:
                    path = by_document[document_id]
                    if not await asyncio.to_thread(
                        _source_is_missing, root, path, plan.root_identity
                    ):
                        raise sync_error(
                            "FOLDER_SYNC_CANDIDATES_CHANGED",
                            "Rescan the changed sources first.",
                            409,
                        )
                for document_id in command.document_ids:
                    path = by_document[document_id]
                    await self._lifecycle.delete(
                        context, DocumentDeleteCommand(document_id=document_id)
                    )
                    plan.sources[path].deleted = True
                    plan.deletion_candidates.pop(path)
                    plan.deleted_count += 1
                    await self._store.save(plan, token)
                await self._record(context, plan, "folder.sync.reconcile", started=started)
        except (Exception, asyncio.CancelledError):
            await self._store.save(plan, token, release=True)
            raise
        await self._store.save(plan, token, release=True)
        return FolderSyncStatus.from_plan(plan)

    async def run_batch(
        self,
        context: AuthenticatedRequestContext,
        plan_id: str,
        *,
        due_only: bool = False,
    ) -> FolderSyncStatus:
        self._authorize(context)
        current = await self._get(context, plan_id)
        if not current.enabled:
            return FolderSyncStatus.from_plan(current)
        if due_only and current.next_run_at > time.time():
            return FolderSyncStatus.from_plan(current)
        claim = await self._store.claim(
            tenant_id=context.auth.tenant_id,
            plan_id=plan_id,
            lease_seconds=self._sync_settings.batch_timeout_seconds + 30,
            due_only=due_only,
        )
        if claim is None:
            raise sync_error("FOLDER_SYNC_BUSY", "Folder sync batch is already running.", 409)
        plan, token = claim
        started = time.perf_counter()
        try:
            async with asyncio.timeout(self._sync_settings.batch_timeout_seconds):
                connector, root = await asyncio.to_thread(_connector, self._settings, context)
                root_identity = await asyncio.to_thread(_root_identity, root)
                if plan.root_identity is not None and plan.root_identity != root_identity:
                    raise sync_error(
                        "FOLDER_SYNC_ROOT_CHANGED", "Folder sync root identity changed."
                    )
                plan.root_identity = root_identity
                if plan.status in {"idle", "completed"}:
                    plan.cycle += 1
                    plan.imported_count = plan.skipped_count = plan.deleted_count = 0
                    plan.last_started_at = time.time()
                    plan.phase = "scan"
                    plan.cursor = None
                plan.status = "scanning" if plan.phase == "scan" else "reconciling"
                plan.error_code = None
                await self._store.save(plan, token)
                remaining = plan.batch_size
                completed = False
                if plan.phase == "scan":
                    remaining -= await self._scan(context, plan, token, connector)
                if plan.phase == "reconcile" and remaining > 0:
                    completed = await self._reconcile(context, plan, token, root, remaining)
                plan.next_run_at = time.time() + plan.interval_seconds if completed else 0
                await self._record(context, plan, "folder.sync.batch", started=started)
                await self._store.save(plan, token, release=True)
                return FolderSyncStatus.from_plan(plan)
        except (Exception, asyncio.CancelledError) as exc:
            plan.status = "failed"
            plan.error_code = (
                exc.code
                if isinstance(exc, DomainError)
                else (
                    "FOLDER_SYNC_TIMEOUT"
                    if isinstance(exc, TimeoutError)
                    else "FOLDER_SYNC_BATCH_FAILED"
                )
            )
            plan.next_run_at = time.time() + min(plan.interval_seconds, 60)
            # Save completed-item checkpoints; a retry resumes the same cycle/cursor.
            await self._store.save(plan, token, release=True)
            await self._record(context, plan, "folder.sync.batch", started=started, failed=True)
            if isinstance(exc, asyncio.CancelledError):
                raise
            if isinstance(exc, DomainError):
                raise
            raise sync_error(
                plan.error_code, "Folder sync batch failed; retry is scheduled.", 503
            ) from exc

    async def _scan(
        self,
        context: AuthenticatedRequestContext,
        plan: FolderSyncPlan,
        token: str,
        connector: FolderConnector,
    ) -> int:
        iterator = iter(connector.iter_documents(start_after=plan.cursor))
        consumed = 0
        while consumed < plan.batch_size:
            document = await asyncio.to_thread(_next_document, iterator)
            if document is None:
                plan.phase = "reconcile"
                plan.status = "reconciling"
                plan.cursor = None
                await self._store.save(plan, token)
                break
            previous = plan.sources.get(document.relative_path)
            if previous is None and len(plan.sources) >= self._sync_settings.max_managed_sources:
                raise sync_error(
                    "FOLDER_SYNC_SOURCE_LIMIT", "Folder sync managed-source limit reached."
                )
            incarnation = 0 if previous is None else previous.incarnation + int(previous.deleted)
            if incarnation:
                document = replace(
                    document, external_id=f"{document.external_id}:incarnation:{incarnation}"
                )
            result = await self._importer.import_document(context=context, document=document)
            plan.sources[document.relative_path] = ManagedFolderSource(
                relative_path=document.relative_path,
                document_id=result.document_id,
                version_id=result.version_id,
                checksum=document.checksum,
                incarnation=incarnation,
            )
            plan.deletion_candidates.pop(document.relative_path, None)
            if result.status == "skipped":
                plan.skipped_count += 1
            else:
                plan.imported_count += 1
            plan.cursor = document.relative_path
            consumed += 1
            await self._store.save(plan, token)
        return consumed

    async def _reconcile(
        self,
        context: AuthenticatedRequestContext,
        plan: FolderSyncPlan,
        token: str,
        root: Path,
        remaining: int,
    ) -> bool:
        # Reconciliation has its own durable path cursor, and visits at most one batch.
        candidates = sorted(
            path for path in plan.sources if plan.cursor is None or path > plan.cursor
        )
        for path in candidates[:remaining]:
            source = plan.sources[path]
            if (
                plan.delete_missing
                and not source.deleted
                and await asyncio.to_thread(_source_is_missing, root, path, plan.root_identity)
            ):
                plan.deletion_candidates[path] = source.document_id
            else:
                plan.deletion_candidates.pop(path, None)
            plan.cursor = path
            await self._store.save(plan, token)
        if len(candidates) <= remaining:
            plan.status = "completed"
            plan.phase = "scan"
            plan.cursor = None
            plan.last_completed_at = time.time()
            return True
        return False

    async def _record(
        self,
        context: AuthenticatedRequestContext,
        plan: FolderSyncPlan,
        action: str,
        *,
        started: float | None = None,
        failed: bool = False,
    ) -> None:
        await self._audit.record(
            AuditEvent(
                request_id=context.request_id,
                trace_id=context.trace_id,
                tenant_id=context.auth.tenant_id,
                user_id=context.auth.user_id,
                action=action,
                resource=AuditResource(type="folder_sync_plan", id=plan.id),
                status=AuditStatus.FAILURE if failed else AuditStatus.SUCCESS,
                latency_ms=0 if started is None else (time.perf_counter() - started) * 1000,
                error_code=plan.error_code,
                metadata={
                    "cycle": plan.cycle,
                    "status": plan.status,
                    "imported_count": plan.imported_count,
                    "skipped_count": plan.skipped_count,
                    "deleted_count": plan.deleted_count,
                    "delete_missing": plan.delete_missing,
                },
            )
        )


def _next_document(iterator: Iterator[ConnectorDocument]) -> ConnectorDocument | None:
    return next(iterator, None)


def _connector(
    settings: AppSettings,
    context: AuthenticatedRequestContext,
) -> tuple[FolderConnector, Path]:
    tenant_id = context.auth.tenant_id
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", tenant_id) is None:
        raise sync_error("FOLDER_SYNC_ROOT_INVALID", "Folder sync tenant directory is invalid.")
    base = Path(settings.folder_connector_root).expanduser().resolve()
    root = base / tenant_id
    try:
        if root.is_symlink():
            raise ValueError("tenant root is a symlink")
        root.resolve().relative_to(base)
        secret = os.getenv("FOLDER_CURSOR_SECRET") or os.getenv("JWT_SECRET")
        if not secret and os.getenv("APP_ENV", "local").strip().lower() == "production":
            raise ValueError("folder cursor secret is required")
        return FolderConnector(
            root=root,
            tenant_id=tenant_id,
            max_bytes=settings.upload_max_bytes,
            cursor_secret=secret or "development-folder-cursor",
        ), root
    except (OSError, ValueError) as exc:
        raise sync_error(
            "FOLDER_SYNC_ROOT_INVALID", "Configured folder sync root is unavailable."
        ) from exc


def _root_identity(root: Path) -> str:
    metadata = root.lstat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise sync_error("FOLDER_SYNC_ROOT_INVALID", "Configured folder sync root is unavailable.")
    return hashlib.sha256(f"{root}:{metadata.st_dev}:{metadata.st_ino}".encode()).hexdigest()


def _source_is_missing(root: Path, relative_path: str, root_identity: str | None) -> bool:
    """Only an unambiguous ENOENT below the configured root can authorize deletion.

    Existing invalid/oversized files, permission errors and symlinks are retained.
    No file is opened here, and no symlink is followed during reconciliation.
    """
    path = Path(relative_path)
    if path.is_absolute() or not path.parts or any(part in {".", ".."} for part in path.parts):
        return False
    current = root
    try:
        if root_identity is None or _root_identity(root) != root_identity:
            return False
    except (OSError, DomainError):
        return False
    try:
        for index, part in enumerate(path.parts):
            current /= part
            mode = current.lstat().st_mode
            if stat.S_ISLNK(mode):
                return False
            if index < len(path.parts) - 1 and not stat.S_ISDIR(mode):
                return False
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False

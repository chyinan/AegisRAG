from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class FolderSyncSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    enabled: bool = Field(default=False, alias="FOLDER_SYNC_ENABLED")
    batch_timeout_seconds: int = Field(
        default=120, ge=10, le=300, alias="FOLDER_SYNC_BATCH_TIMEOUT_SECONDS"
    )
    max_managed_sources: int = Field(
        default=10000, ge=1, le=100000, alias="FOLDER_SYNC_MAX_MANAGED_SOURCES"
    )


class FolderSyncPlanCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector_id: Literal["folder"] = "folder"
    enabled: bool = True
    interval_seconds: int = Field(default=300, ge=30, le=86400)
    batch_size: int = Field(default=25, ge=1, le=100)
    delete_missing: bool = False


class ManagedFolderSource(BaseModel):
    relative_path: str
    document_id: str
    version_id: str
    checksum: str
    incarnation: int = 0
    deleted: bool = False


class FolderSyncReconcileCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    document_ids: list[str] = Field(min_length=1, max_length=100)


class FolderSyncDeletionCandidate(BaseModel):
    document_id: str
    version_id: str
    source_uri: str


class FolderSyncCandidatePage(BaseModel):
    revision: int
    total: int
    items: list[FolderSyncDeletionCandidate]
    next_offset: int | None = None


class FolderSyncPlan(BaseModel):
    id: str
    tenant_id: str
    created_by: str
    connector_id: Literal["folder"] = "folder"
    enabled: bool = True
    interval_seconds: int = 300
    batch_size: int = 25
    delete_missing: bool = False
    status: Literal["idle", "scanning", "reconciling", "completed", "failed"] = "idle"
    cursor: str | None = None
    phase: Literal["scan", "reconcile"] = "scan"
    cycle: int = 0
    imported_count: int = 0
    skipped_count: int = 0
    deleted_count: int = 0
    last_started_at: float | None = None
    last_completed_at: float | None = None
    next_run_at: float = 0
    error_code: str | None = None
    root_identity: str | None = None
    revision: int = 0
    deletion_candidates: dict[str, str] = Field(default_factory=dict)
    sources: dict[str, ManagedFolderSource] = Field(default_factory=dict)


class FolderSyncStatus(BaseModel):
    id: str
    connector_id: str
    enabled: bool
    interval_seconds: int
    batch_size: int
    delete_missing: bool
    status: str
    phase: str
    cycle: int
    imported_count: int
    skipped_count: int
    deleted_count: int
    managed_count: int
    last_started_at: float | None
    last_completed_at: float | None
    next_run_at: float
    error_code: str | None
    revision: int
    candidate_count: int
    deletion_candidates: list[FolderSyncDeletionCandidate]

    @classmethod
    def from_plan(cls, plan: FolderSyncPlan) -> FolderSyncStatus:
        return cls(
            **plan.model_dump(
                exclude={
                    "tenant_id",
                    "created_by",
                    "cursor",
                    "sources",
                    "root_identity",
                    "deletion_candidates",
                }
            ),
            managed_count=sum(not source.deleted for source in plan.sources.values()),
            candidate_count=len(plan.deletion_candidates),
            deletion_candidates=candidate_items(plan, offset=0, limit=25),
        )


def candidate_items(
    plan: FolderSyncPlan,
    *,
    offset: int,
    limit: int,
) -> list[FolderSyncDeletionCandidate]:
    return [
        FolderSyncDeletionCandidate(
            document_id=plan.deletion_candidates[path],
            version_id=plan.sources[path].version_id,
            source_uri=f"folder://{plan.connector_id}/{path}",
        )
        for path in sorted(plan.deletion_candidates)[offset : offset + limit]
    ]

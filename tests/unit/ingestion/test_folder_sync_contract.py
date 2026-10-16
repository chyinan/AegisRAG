from __future__ import annotations

from pathlib import Path

from packages.ingestion.connectors import FolderConnector
from packages.ingestion.folder_sync.dto import (
    FolderSyncPlan,
    FolderSyncPlanCommand,
    FolderSyncSettings,
    FolderSyncStatus,
    ManagedFolderSource,
)


def test_folder_sync_is_opt_in_and_reconciliation_defaults_to_retention() -> None:
    settings = FolderSyncSettings(FOLDER_SYNC_ENABLED=False)
    command = FolderSyncPlanCommand()
    assert settings.enabled is False
    assert command.delete_missing is False
    assert command.interval_seconds == 300
    assert command.batch_size == 25


def test_folder_sync_status_reports_active_sources_without_internal_manifest() -> None:
    plan = FolderSyncPlan(
        id="plan",
        tenant_id="tenant",
        created_by="operator",
        root_identity="private-root-identity",
        sources={
            "current.txt": ManagedFolderSource(
                relative_path="current.txt",
                document_id="current",
                version_id="v1",
                checksum="abc",
            ),
            "removed.txt": ManagedFolderSource(
                relative_path="removed.txt",
                document_id="removed",
                version_id="v2",
                checksum="def",
                deleted=True,
            ),
        },
    )
    status = FolderSyncStatus.from_plan(plan).model_dump()
    assert status["managed_count"] == 1
    assert "sources" not in status
    assert "root_identity" not in status
    assert "tenant_id" not in status


def test_markdown_extension_has_canonical_source_type(tmp_path: Path) -> None:
    (tmp_path / "readme.md").write_text("# Folder source", encoding="utf-8")
    (tmp_path / "guide.markdown").write_text("# Folder guide", encoding="utf-8")
    connector = FolderConnector(root=tmp_path, tenant_id="tenant")
    documents = list(connector.iter_documents())
    assert len(documents) == 2
    assert {document.source_type for document in documents} == {"markdown"}

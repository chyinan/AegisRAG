"""Secure source connector contracts."""

import hashlib
from pathlib import Path

import pytest

from packages.auth.context import AuthContext
from packages.common.context import AuthenticatedRequestContext
from packages.common.errors import DomainError
from packages.data.dto import DocumentVersionRecord, UploadDocumentResult
from packages.data.exceptions import DocumentUploadInvalidMetadataError
from packages.data.service import FolderImportService
from packages.ingestion.connectors import FolderConnector, folder_cursor_token


def test_folder_connector_lists_supported_files_with_stable_checksums(tmp_path: Path) -> None:
    (tmp_path / "policy.md").write_text("retention: 7 years", encoding="utf-8")
    (tmp_path / "ignored.exe").write_bytes(b"not a document")
    connector = FolderConnector(root=tmp_path, tenant_id="tenant-a", max_bytes=100)

    documents = list(connector.iter_documents())

    assert len(documents) == 1
    assert documents[0].filename == "policy.md"
    assert documents[0].tenant_id == "tenant-a"
    assert documents[0].checksum
    assert documents[0].source_uri == "folder://folder/policy.md"
    assert documents[0].external_id == "tenant-a:folder:policy.md"


def test_folder_connector_rejects_symlink_escape_and_oversized_files(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-secret.txt"
    outside.write_text("secret", encoding="utf-8")
    large = tmp_path / "large.txt"
    large.write_bytes(b"x" * 20)
    try:
        (tmp_path / "escape.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are unavailable on this platform")

    connector = FolderConnector(root=tmp_path, tenant_id="tenant-a", max_bytes=10)

    assert list(connector.iter_documents()) == []


def test_folder_cursor_token_is_bounded_and_does_not_expose_path() -> None:
    with pytest.raises(ValueError, match="bounded cursor"):
        folder_cursor_token("deep/" + ("a" * 4096) + ".txt")
    token = folder_cursor_token("deep/" + ("a" * 200) + ".txt")

    assert len(token) <= 512
    assert token == folder_cursor_token("deep/" + ("a" * 200) + ".txt")
    assert "/" not in token


def test_folder_connector_start_after_continues_when_cursor_file_is_deleted(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("first", encoding="utf-8")
    (tmp_path / "b.txt").write_text("second", encoding="utf-8")
    connector = FolderConnector(root=tmp_path, tenant_id="tenant-a")
    (tmp_path / "a.txt").unlink()

    documents = list(connector.iter_documents(start_after="a.txt"))

    assert [document.relative_path for document in documents] == ["b.txt"]


def test_folder_connector_cursor_order_is_global_across_root_and_nested_paths(
    tmp_path: Path,
) -> None:
    (tmp_path / "a0.txt").write_text("root", encoding="utf-8")
    nested = tmp_path / "a"
    nested.mkdir()
    (nested / "x.txt").write_text("nested", encoding="utf-8")
    connector = FolderConnector(root=tmp_path, tenant_id="tenant-a")

    documents = list(connector.iter_documents())

    assert [document.relative_path for document in documents] == ["a/x.txt", "a0.txt"]


@pytest.mark.asyncio
async def test_folder_import_rejects_tampered_cursor(tmp_path: Path) -> None:
    (tmp_path / "policy.txt").write_text("retention policy", encoding="utf-8")
    context = AuthenticatedRequestContext(
        request_id="req-stale",
        trace_id="trace-stale",
        auth=AuthContext(user_id="user-a", tenant_id="tenant-a", permissions=("document:manage",)),
    )
    service = FolderImportService(upload_service=object())  # type: ignore[arg-type]

    with pytest.raises(DocumentUploadInvalidMetadataError) as exc_info:
        await service.import_folder(
            context=context,
            connector=FolderConnector(root=tmp_path, tenant_id="tenant-a"),
            cursor="0" * 64,
        )

    assert exc_info.value.details["field"] == "cursor"


@pytest.mark.asyncio
async def test_folder_import_is_idempotent_for_unchanged_file(tmp_path: Path) -> None:
    (tmp_path / "policy.txt").write_text("retention policy", encoding="utf-8")

    class UploadService:
        def __init__(self) -> None:
            self.versions: dict[str, DocumentVersionRecord] = {}
            self.uploads = 0

        async def get_existing_version(
            self,
            *,
            tenant_id: str,
            version_id: str,
        ) -> DocumentVersionRecord | None:
            del tenant_id
            return self.versions.get(version_id)

        async def upload(
            self,
            context: AuthenticatedRequestContext,
            command: object,
        ) -> UploadDocumentResult:
            del context
            self.uploads += 1
            version_id = command.version_id  # type: ignore[attr-defined]
            document_id = command.document_id  # type: ignore[attr-defined]
            payload = command.stream.read()  # type: ignore[attr-defined]
            record = DocumentVersionRecord(
                id=version_id,
                document_id=document_id,
                tenant_id="tenant-a",
                created_by="user-a",
                status="uploaded",
                source_type="txt",
                source_uri="folder://folder/policy.txt",
                object_key="object",
                filename="policy.txt",
                content_type="text/plain",
                checksum=hashlib.sha256(payload).hexdigest(),
                byte_size=len(payload),
            )
            self.versions[version_id] = record
            return UploadDocumentResult(
                document_id=document_id,
                version_id=version_id,
                job_id="job-a",
                status="uploaded",
            )

    upload_service = UploadService()
    service = FolderImportService(upload_service=upload_service)  # type: ignore[arg-type]
    context = AuthenticatedRequestContext(
        request_id="req-a",
        trace_id="trace-a",
        auth=AuthContext(
            user_id="user-a",
            tenant_id="tenant-a",
            permissions=("document:manage",),
        ),
    )
    connector = FolderConnector(root=tmp_path, tenant_id="tenant-a")

    first = await service.import_folder(context=context, connector=connector)
    second = await service.import_folder(context=context, connector=connector)

    assert len(first.documents) == 1
    assert len(second.documents) == 1
    assert upload_service.uploads == 1
    assert second.documents[0].job_id is None
    assert second.documents[0].status == "skipped"


@pytest.mark.asyncio
async def test_folder_import_returns_winner_when_concurrent_insert_races(tmp_path: Path) -> None:
    (tmp_path / "policy.txt").write_text("retention policy", encoding="utf-8")

    class RacingUploadService:
        def __init__(self) -> None:
            self.winner: DocumentVersionRecord | None = None

        async def get_existing_version(
            self,
            *,
            tenant_id: str,
            version_id: str,
        ) -> DocumentVersionRecord | None:
            del tenant_id, version_id
            return self.winner

        async def upload(
            self,
            context: AuthenticatedRequestContext,
            command: object,
        ) -> UploadDocumentResult:
            del context
            payload = command.stream.read()  # type: ignore[attr-defined]
            self.winner = DocumentVersionRecord(
                id=command.version_id,  # type: ignore[attr-defined]
                document_id=command.document_id,  # type: ignore[attr-defined]
                tenant_id="tenant-a",
                created_by="user-a",
                status="uploaded",
                source_type="txt",
                source_uri="folder://folder/policy.txt",
                object_key="object",
                filename="policy.txt",
                content_type="text/plain",
                checksum=hashlib.sha256(payload).hexdigest(),
                byte_size=len(payload),
                metadata={"ingestion_job_id": "winner-job"},
            )
            raise DomainError(code="DOCUMENT_VERSION_CONFLICT", message="race")

    service = FolderImportService(upload_service=RacingUploadService())  # type: ignore[arg-type]
    context = AuthenticatedRequestContext(
        request_id="req-race",
        trace_id="trace-race",
        auth=AuthContext(user_id="user-a", tenant_id="tenant-a", permissions=("document:manage",)),
    )

    result = await service.import_folder(
        context=context,
        connector=FolderConnector(root=tmp_path, tenant_id="tenant-a"),
    )

    assert result.documents[0].status == "skipped"
    assert result.documents[0].job_id == "winner-job"

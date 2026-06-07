from __future__ import annotations

from io import BytesIO
from typing import Any, cast

import pytest
from pydantic import ValidationError

from packages.data.dto import DocumentRecord, UploadDocumentCommand


def test_upload_document_command_normalizes_optional_text_and_default_maps() -> None:
    command = UploadDocumentCommand(
        filename=" policy.md ",
        content_type=" text/markdown ",
        source_type=" markdown ",
        source_uri=" ",
        title=" ",
        stream=BytesIO(b"# Policy"),
    )

    assert command.filename == "policy.md"
    assert command.content_type == "text/markdown"
    assert command.source_type == "markdown"
    assert command.source_uri is None
    assert command.title is None
    assert command.document_id is None
    assert command.acl == {"visibility": "tenant"}
    assert command.metadata == {}


def test_upload_document_command_normalizes_optional_document_id() -> None:
    command = UploadDocumentCommand(
        document_id=" doc-1 ",
        filename="policy.txt",
        content_type="text/plain",
        source_type="txt",
        stream=BytesIO(b"policy"),
    )

    assert command.document_id == "doc-1"


def test_document_records_require_traceable_identity_fields() -> None:
    with pytest.raises(ValidationError):
        DocumentRecord(
            id=" ",
            tenant_id="tenant-1",
            created_by="user-1",
            status="uploaded",
            source_type="txt",
            source_uri="kb://policy.txt",
            title="Policy",
            acl={"visibility": "tenant"},
            checksum="abc",
            metadata={},
        )


def test_upload_document_command_rejects_non_mapping_acl_and_metadata() -> None:
    with pytest.raises(ValidationError):
        UploadDocumentCommand(
            filename="policy.txt",
            content_type="text/plain",
            source_type="txt",
            stream=BytesIO(b"policy"),
            acl=cast(Any, ["admin"]),
        )

    with pytest.raises(ValidationError):
        UploadDocumentCommand(
            filename="policy.txt",
            content_type="text/plain",
            source_type="txt",
            stream=BytesIO(b"policy"),
            metadata=cast(Any, "department=HR"),
        )

from __future__ import annotations

import pytest

from packages.data.adapters.minio_object_storage import MinioObjectStorage
from packages.data.exceptions import DocumentStorageReadError, DocumentStorageWriteError


class FakeResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content
        self.closed = False
        self.released = False

    def read(self) -> bytes:
        return self.content

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.released = True


class FakeMinioClient:
    def __init__(self, content: bytes = b"hello") -> None:
        self.content = content
        self.removed: list[tuple[str, str]] = []
        self.reads: list[tuple[str, str]] = []

    def get_object(self, bucket_name: str, object_name: str) -> FakeResponse:
        self.reads.append((bucket_name, object_name))
        return FakeResponse(self.content)

    def remove_object(self, bucket_name: str, object_name: str) -> None:
        self.removed.append((bucket_name, object_name))


@pytest.mark.asyncio
async def test_minio_get_document_rejects_object_key_outside_document_scope() -> None:
    client = FakeMinioClient()
    storage = MinioObjectStorage(client=client, bucket="documents", prefix="raw")

    with pytest.raises(DocumentStorageReadError):
        await storage.get_document(
            tenant_id="tenant-1",
            document_id="doc-1",
            version_id="ver-1",
            object_key="raw/tenant-2/doc-1/ver-1/policy.txt",
        )

    assert client.reads == []


@pytest.mark.asyncio
async def test_minio_delete_document_rejects_object_key_outside_document_scope() -> None:
    client = FakeMinioClient()
    storage = MinioObjectStorage(client=client, bucket="documents", prefix="raw")

    with pytest.raises(DocumentStorageWriteError):
        await storage.delete_document(
            tenant_id="tenant-1",
            document_id="doc-1",
            version_id="ver-1",
            object_key="raw/tenant-1/doc-2/ver-1/policy.txt",
        )

    assert client.removed == []


@pytest.mark.asyncio
async def test_minio_get_document_returns_actual_checksum() -> None:
    client = FakeMinioClient(content=b"hello")
    storage = MinioObjectStorage(client=client, bucket="documents", prefix="raw")

    stored = await storage.get_document(
        tenant_id="tenant-1",
        document_id="doc-1",
        version_id="ver-1",
        object_key="raw/tenant-1/doc-1/ver-1/policy.txt",
    )

    assert stored.byte_size == 5
    assert stored.checksum == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from typing import BinaryIO

from packages.data.dto import StoredDocumentContent, StoredObject
from packages.data.exceptions import DocumentStorageReadError, DocumentStorageWriteError


class LocalFileObjectStorage:
    def __init__(self, root: str, bucket: str = "local", prefix: str = "raw-documents") -> None:
        self.root = Path(root)
        self.bucket = bucket
        self.prefix = prefix.strip().strip("/")

    def _path(self, tenant_id: str, document_id: str, version_id: str, filename: str) -> tuple[str, Path]:
        safe_name = Path(filename).name
        key = "/".join(part for part in (self.prefix, tenant_id, document_id, version_id, safe_name) if part)
        return key, self.root / Path(key)

    async def put_document(self, *, tenant_id: str, document_id: str, version_id: str, filename: str, content_type: str | None, stream: BinaryIO, byte_size: int, checksum: str) -> StoredObject:
        key, path = self._path(tenant_id, document_id, version_id, filename)
        try:
            await asyncio.to_thread(self._write, path, stream)
        except Exception as exc:
            raise DocumentStorageWriteError(details={"document_id": document_id}) from exc
        return StoredObject(bucket=self.bucket, object_key=key, etag=None, byte_size=byte_size, checksum=checksum)

    @staticmethod
    def _write(path: Path, stream: BinaryIO) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        stream.seek(0)
        with path.open("wb") as target:
            while chunk := stream.read(1024 * 1024):
                target.write(chunk)

    def _safe_path(self, tenant_id: str, document_id: str, version_id: str, object_key: str) -> Path:
        expected = f"{self.prefix}/{tenant_id}/{document_id}/{version_id}/"
        normalized = object_key.replace("\\", "/")
        if not normalized.startswith(expected) or ".." in Path(normalized).parts:
            raise DocumentStorageReadError(details={"document_id": document_id, "field": "object_key"})
        return self.root / Path(normalized)

    async def delete_document(self, *, tenant_id: str, document_id: str, version_id: str, object_key: str) -> None:
        path = self._safe_path(tenant_id, document_id, version_id, object_key)
        try:
            await asyncio.to_thread(path.unlink, missing_ok=True)
        except Exception as exc:
            raise DocumentStorageWriteError(details={"document_id": document_id}) from exc

    async def get_document(self, *, tenant_id: str, document_id: str, version_id: str, object_key: str) -> StoredDocumentContent:
        path = self._safe_path(tenant_id, document_id, version_id, object_key)
        try:
            content = await asyncio.to_thread(path.read_bytes)
        except Exception as exc:
            raise DocumentStorageReadError(details={"document_id": document_id}) from exc
        return StoredDocumentContent(bucket=self.bucket, object_key=object_key, content=content, byte_size=len(content), checksum=hashlib.sha256(content).hexdigest())

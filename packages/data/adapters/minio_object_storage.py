from __future__ import annotations

import asyncio
import hashlib
import importlib
from dataclasses import dataclass
from typing import Any, BinaryIO, Protocol, cast
from urllib.parse import urlparse

from packages.common.config import AppSettings
from packages.data.dto import StoredDocumentContent, StoredObject
from packages.data.exceptions import (
    DocumentStorageReadError,
    DocumentStorageWriteError,
    ObjectStorageConfigurationError,
)


class _MinioClient(Protocol):
    def put_object(
        self,
        bucket_name: str,
        object_name: str,
        data: BinaryIO,
        length: int,
        *,
        content_type: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> object: ...

    def remove_object(self, bucket_name: str, object_name: str) -> None: ...

    def get_object(self, bucket_name: str, object_name: str) -> _MinioResponse: ...


class _MinioResponse(Protocol):
    def read(self) -> bytes: ...


@dataclass(frozen=True)
class MinioObjectStorage:
    client: object
    bucket: str
    prefix: str = "raw-documents"

    @classmethod
    def from_settings(cls, settings: AppSettings) -> MinioObjectStorage:
        missing = [
            name
            for name, value in {
                "MINIO_ENDPOINT": settings.minio_endpoint,
                "MINIO_ACCESS_KEY": settings.minio_access_key,
                "MINIO_SECRET_KEY": settings.minio_secret_key,
                "MINIO_BUCKET": settings.minio_bucket,
            }.items()
            if not value
        ]
        if missing:
            raise ObjectStorageConfigurationError(details={"missing": missing})

        minio_module = _import_dependency("minio")
        urllib3_module = _import_dependency("urllib3")
        endpoint, secure = _parse_endpoint(str(settings.minio_endpoint))
        timeout = float(settings.readiness_timeout_seconds)
        http_client = urllib3_module.PoolManager(
            timeout=urllib3_module.Timeout(connect=timeout, read=timeout),
            retries=False,
        )
        client = minio_module.Minio(
            endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=secure,
            http_client=http_client,
        )
        return cls(
            client=client,
            bucket=str(settings.minio_bucket),
            prefix=settings.minio_document_prefix.strip().strip("/"),
        )

    async def put_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        filename: str,
        content_type: str | None,
        stream: BinaryIO,
        byte_size: int,
        checksum: str,
    ) -> StoredObject:
        object_key = _object_key(
            prefix=self.prefix,
            tenant_id=tenant_id,
            document_id=document_id,
            version_id=version_id,
            filename=filename,
        )
        try:
            result = await asyncio.to_thread(
                self._put_object_sync,
                object_key,
                stream,
                byte_size,
                content_type,
                checksum,
            )
        except Exception as exc:
            raise DocumentStorageWriteError(
                details={"bucket": self.bucket, "document_id": document_id}
            ) from exc
        etag = getattr(result, "etag", None)
        return StoredObject(
            bucket=self.bucket,
            object_key=object_key,
            etag=str(etag) if etag is not None else None,
            byte_size=byte_size,
            checksum=checksum,
        )

    def _put_object_sync(
        self,
        object_key: str,
        stream: BinaryIO,
        byte_size: int,
        content_type: str | None,
        checksum: str,
    ) -> object:
        stream.seek(0)
        client = cast(_MinioClient, self.client)
        return client.put_object(
            self.bucket,
            object_key,
            stream,
            byte_size,
            content_type=content_type,
            metadata={"x-amz-meta-sha256": checksum},
        )

    async def delete_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        object_key: str,
    ) -> None:
        if not _object_key_matches_scope(
            prefix=self.prefix,
            tenant_id=tenant_id,
            document_id=document_id,
            version_id=version_id,
            object_key=object_key,
        ):
            raise DocumentStorageWriteError(
                details={"document_id": document_id, "field": "object_key"}
            )
        try:
            await asyncio.to_thread(self._delete_object_sync, object_key)
        except Exception as exc:
            raise DocumentStorageWriteError(details={"document_id": document_id}) from exc

    def _delete_object_sync(self, object_key: str) -> None:
        client = cast(_MinioClient, self.client)
        client.remove_object(self.bucket, object_key)

    async def get_document(
        self,
        *,
        tenant_id: str,
        document_id: str,
        version_id: str,
        object_key: str,
    ) -> StoredDocumentContent:
        if not _object_key_matches_scope(
            prefix=self.prefix,
            tenant_id=tenant_id,
            document_id=document_id,
            version_id=version_id,
            object_key=object_key,
        ):
            raise DocumentStorageReadError(
                details={"document_id": document_id, "field": "object_key"}
            )
        try:
            content = await asyncio.to_thread(self._get_object_sync, object_key)
        except Exception as exc:
            raise DocumentStorageReadError(details={"document_id": document_id}) from exc
        return StoredDocumentContent(
            bucket=self.bucket,
            object_key=object_key,
            content=content,
            byte_size=len(content),
            checksum=hashlib.sha256(content).hexdigest(),
        )

    def _get_object_sync(self, object_key: str) -> bytes:
        client = cast(_MinioClient, self.client)
        response = client.get_object(self.bucket, object_key)
        try:
            return response.read()
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                close()
            release = getattr(response, "release_conn", None)
            if callable(release):
                release()


def _parse_endpoint(value: str) -> tuple[str, bool]:
    parsed = urlparse(value)
    if parsed.scheme in {"http", "https"}:
        endpoint = parsed.netloc
        secure = parsed.scheme == "https"
    else:
        endpoint = value
        secure = False
    endpoint = endpoint.strip().rstrip("/")
    if not endpoint:
        raise ObjectStorageConfigurationError(details={"field": "MINIO_ENDPOINT"})
    return endpoint, secure


def _object_key(
    *,
    prefix: str,
    tenant_id: str,
    document_id: str,
    version_id: str,
    filename: str,
) -> str:
    safe_filename = filename.replace("\\", "/").rsplit("/", maxsplit=1)[-1]
    parts = [part for part in (prefix, tenant_id, document_id, version_id, safe_filename) if part]
    return "/".join(parts)


def _object_key_matches_scope(
    *,
    prefix: str,
    tenant_id: str,
    document_id: str,
    version_id: str,
    object_key: str,
) -> bool:
    expected_parts = [part for part in (prefix, tenant_id, document_id, version_id) if part]
    expected_prefix = "/".join(expected_parts) + "/"
    return object_key.replace("\\", "/").startswith(expected_prefix)


def _import_dependency(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        raise ObjectStorageConfigurationError(details={"missing_dependency": name}) from exc

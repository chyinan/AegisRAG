from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import BinaryIO

from pydantic import BaseModel, ConfigDict, Field, SkipValidation, field_validator, model_validator


def _default_acl() -> dict[str, object]:
    return {"visibility": "tenant"}


class UploadDocumentCommand(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    document_id: str | None = None
    filename: str
    content_type: str | None = None
    source_type: str
    source_uri: str | None = None
    title: str | None = None
    acl: dict[str, object] = Field(default_factory=_default_acl)
    metadata: dict[str, object] = Field(default_factory=dict)
    stream: SkipValidation[BinaryIO]

    @field_validator("filename", "source_type")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("content_type", "source_uri", "title")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("document_id")
    @classmethod
    def _optional_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("acl", "metadata", mode="before")
    @classmethod
    def _mapping_field(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("value must be a mapping")
        return dict(value)


class UploadDocumentResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    version_id: str
    job_id: str
    status: str


class StoredObject(BaseModel):
    model_config = ConfigDict(frozen=True)

    bucket: str
    object_key: str
    etag: str | None = None
    byte_size: int
    checksum: str

    @field_validator("bucket", "object_key", "checksum")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class StoredDocumentContent(BaseModel):
    model_config = ConfigDict(frozen=True)

    bucket: str
    object_key: str
    content: bytes
    byte_size: int
    checksum: str

    @field_validator("bucket", "object_key", "checksum")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("byte_size")
    @classmethod
    def _byte_size_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("byte_size must not be negative")
        return value


class EnqueuedJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    queue_job_id: str | None
    queue_name: str

    @field_validator("queue_name")
    @classmethod
    def _queue_name_required(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("queue_name must not be blank")
        return normalized


class DocumentRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    created_by: str
    status: str
    source_type: str
    source_uri: str | None
    title: str | None
    acl: dict[str, object] = Field(default_factory=dict)
    checksum: str
    metadata: dict[str, object] = Field(default_factory=dict)
    deleted_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("id", "tenant_id", "created_by", "status", "source_type", "checksum")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized


class DocumentVersionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    document_id: str
    tenant_id: str
    created_by: str
    status: str
    source_type: str
    source_uri: str | None
    object_key: str
    filename: str
    content_type: str | None
    byte_size: int
    acl: dict[str, object] = Field(default_factory=dict)
    checksum: str
    metadata: dict[str, object] = Field(default_factory=dict)
    deleted_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator(
        "id",
        "document_id",
        "tenant_id",
        "created_by",
        "status",
        "source_type",
        "object_key",
        "filename",
        "checksum",
    )
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("byte_size")
    @classmethod
    def _byte_size_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("byte_size must not be negative")
        return value


class IngestionJobRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    created_by: str
    status: str
    document_id: str
    version_id: str
    queue_name: str
    queue_job_id: str | None = None
    attempt_count: int = 0
    error_code: str | None = None
    last_attempt_at: datetime | None = None
    next_retry_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator(
        "id",
        "tenant_id",
        "created_by",
        "status",
        "document_id",
        "version_id",
        "queue_name",
    )
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("attempt_count")
    @classmethod
    def _attempt_count_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("attempt_count must not be negative")
        return value


class EmbeddingJobRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    tenant_id: str
    created_by: str
    status: str
    document_id: str
    version_id: str
    provider: str
    model: str
    version: str | None = None
    dim: int | None = None
    chunk_count: int | None = None
    attempt_count: int = 0
    error_code: str | None = None
    last_attempt_at: datetime | None = None
    next_retry_at: datetime | None = None
    metadata: dict[str, object] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator(
        "id",
        "tenant_id",
        "created_by",
        "status",
        "document_id",
        "version_id",
        "provider",
        "model",
    )
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("attempt_count")
    @classmethod
    def _attempt_count_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("attempt_count must not be negative")
        return value

    @field_validator("dim")
    @classmethod
    def _dim_positive_when_present(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("dim must be greater than 0")
        return value

    @field_validator("chunk_count")
    @classmethod
    def _chunk_count_non_negative_when_present(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("chunk_count must not be negative")
        return value

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata_mapping(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be a mapping")
        return dict(value)


class ChunkRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str | None = None
    tenant_id: str
    document_id: str
    version_id: str
    chunk_id: str
    created_by: str
    status: str
    source_type: str
    source_uri: str | None = None
    title_path: list[str]
    content: str
    page_start: int | None = None
    page_end: int | None = None
    token_count: int
    acl: dict[str, object] = Field(default_factory=_default_acl)
    checksum: str
    section_ids: list[str]
    metadata: dict[str, object] = Field(default_factory=dict)
    deleted_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator(
        "tenant_id",
        "document_id",
        "version_id",
        "chunk_id",
        "created_by",
        "status",
        "source_type",
        "content",
        "checksum",
    )
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("source_uri")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("token_count")
    @classmethod
    def _token_count_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("token_count must be greater than 0")
        return value

    @field_validator("title_path", "section_ids")
    @classmethod
    def _non_empty_text_list(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        if not normalized:
            raise ValueError("value must contain at least one non-blank item")
        return normalized

    @field_validator("acl", mode="before")
    @classmethod
    def _acl_mapping(cls, value: object) -> dict[str, object]:
        if value is None:
            return _default_acl()
        if not isinstance(value, Mapping):
            raise ValueError("acl must be a mapping")
        normalized = dict(value)
        return normalized or _default_acl()

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata_mapping(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("metadata must be a mapping")
        return dict(value)

    @model_validator(mode="after")
    def _validate_page_range(self) -> ChunkRecord:
        if self.page_start is None and self.page_end is None:
            return self
        if self.page_start is None or self.page_end is None:
            raise ValueError("page_start and page_end must both be set or both be None")
        if self.page_start < 1 or self.page_end < 1:
            raise ValueError("page numbers must be 1-based")
        if self.page_end < self.page_start:
            raise ValueError("page_end must be greater than or equal to page_start")
        return self


class DocumentVersionStatusResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    version_id: str
    status: str
    chunk_count: int
    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_version: str | None = None
    embedding_dim: int | None = None
    vector_count: int | None = None
    index_status: str | None = None
    job_id: str | None = None
    attempt_count: int | None = None
    last_attempt_at: datetime | None = None
    next_retry_at: datetime | None = None
    deleted_at: datetime | None = None
    error_code: str | None = None
    error_summary: dict[str, object] | None = None
    request_id: str
    trace_id: str


class DocumentDeleteCommand(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    version_id: str | None = None

    @field_validator("document_id", "version_id")
    @classmethod
    def _normalize_identifier(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("identifier must not be blank")
        return normalized


class DocumentDeleteResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    version_id: str | None = None
    status: str
    deleted_versions: int
    deleted_chunks: int
    deleted_vectors: int
    request_id: str
    trace_id: str

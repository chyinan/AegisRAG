from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DistanceMetric = Literal["cosine", "l2"]


def _default_acl() -> dict[str, object]:
    return {"visibility": "tenant"}


class MetadataFilter(BaseModel):
    model_config = ConfigDict(frozen=True)

    key: str
    value: object

    @field_validator("key")
    @classmethod
    def _key_required(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("key must not be blank")
        return normalized


class AclFilter(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str
    roles: list[str] = Field(default_factory=list)
    department: str | None = None
    permissions: list[str] = Field(default_factory=list)

    @field_validator("user_id")
    @classmethod
    def _user_id_required(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("user_id must not be blank")
        return normalized

    @field_validator("roles", "permissions")
    @classmethod
    def _normalize_text_list(cls, value: list[str]) -> list[str]:
        return sorted({item.strip() for item in value if item.strip()})

    @field_validator("department")
    @classmethod
    def _optional_department(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class VectorRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str | None = None
    tenant_id: str
    document_id: str
    version_id: str
    chunk_id: str
    created_by: str
    status: str = "active"
    vector: list[float]
    embedding_provider: str
    embedding_model: str
    embedding_version: str | None = None
    embedding_dim: int
    source_type: str
    source_uri: str | None = None
    title_path: list[str]
    page_start: int | None = None
    page_end: int | None = None
    token_count: int
    acl: dict[str, object] = Field(default_factory=_default_acl)
    checksum: str
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
        "embedding_provider",
        "embedding_model",
        "source_type",
        "checksum",
    )
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("embedding_version", "source_uri")
    @classmethod
    def _optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("embedding_dim", "token_count")
    @classmethod
    def _positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("value must be greater than 0")
        return value

    @field_validator("vector")
    @classmethod
    def _vector_required(cls, value: list[float]) -> list[float]:
        if not value:
            raise ValueError("vector must not be empty")
        return value

    @field_validator("title_path")
    @classmethod
    def _title_path_required(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        if not normalized:
            raise ValueError("title_path must contain at least one non-blank item")
        return normalized

    @field_validator("acl", "metadata", mode="before")
    @classmethod
    def _mapping_field(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("value must be a mapping")
        return dict(value)

    @model_validator(mode="after")
    def _validate_vector_dimension_and_pages(self) -> VectorRecord:
        if len(self.vector) != self.embedding_dim:
            raise ValueError("vector length must match embedding_dim")
        if self.page_start is None and self.page_end is None:
            return self
        if self.page_start is None or self.page_end is None:
            raise ValueError("page_start and page_end must both be set or both be None")
        if self.page_start < 1 or self.page_end < 1:
            raise ValueError("page numbers must be 1-based")
        if self.page_end < self.page_start:
            raise ValueError("page_end must be greater than or equal to page_start")
        return self


class VectorSearchRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    query_vector: list[float]
    embedding_dim: int
    top_k: int
    score_threshold: float | None = None
    metadata_filters: list[MetadataFilter] = Field(default_factory=list)
    acl_filter: AclFilter
    include_deleted: bool = False
    distance_metric: DistanceMetric = "cosine"
    embedding_provider: str | None = None
    embedding_model: str | None = None
    embedding_version: str | None = None

    @field_validator("tenant_id")
    @classmethod
    def _tenant_required(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("tenant_id must not be blank")
        return normalized

    @field_validator("embedding_dim", "top_k")
    @classmethod
    def _positive_int(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("value must be greater than 0")
        return value

    @field_validator("query_vector")
    @classmethod
    def _query_vector_required(cls, value: list[float]) -> list[float]:
        if not value:
            raise ValueError("query_vector must not be empty")
        return value

    @model_validator(mode="after")
    def _validate_query_dimension(self) -> VectorSearchRequest:
        if len(self.query_vector) != self.embedding_dim:
            raise ValueError("query_vector length must match embedding_dim")
        return self


class VectorSearchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: str
    version_id: str
    chunk_id: str
    source: str | None = None
    source_type: str
    source_uri: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    title_path: list[str]
    score: float
    retrieval_method: str = "dense"
    tenant_id: str
    acl: dict[str, object]
    metadata: dict[str, object] = Field(default_factory=dict)


class VectorUpsertResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    upserted_count: int
    tenant_id: str
    document_id: str
    version_id: str
    embedding_provider: str
    embedding_model: str
    embedding_version: str | None = None
    embedding_dim: int


class VectorDeleteResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    deleted_count: int
    tenant_id: str
    document_id: str
    version_id: str | None = None

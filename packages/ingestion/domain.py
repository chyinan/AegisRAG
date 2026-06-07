from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePath
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def _default_acl() -> dict[str, object]:
    return {"visibility": "tenant"}


class RawDocumentRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    document_id: str
    version_id: str
    source_type: str
    source_uri: str | None = None
    filename: str
    object_key: str
    byte_size: int
    checksum: str
    acl: dict[str, object] = Field(default_factory=_default_acl)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator(
        "tenant_id",
        "document_id",
        "version_id",
        "source_type",
        "filename",
        "object_key",
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

    @field_validator("byte_size")
    @classmethod
    def _byte_size_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("byte_size must not be negative")
        return value

    @field_validator("acl", mode="before")
    @classmethod
    def _acl_field(cls, value: object) -> dict[str, object]:
        if value is None:
            return _default_acl()
        if not isinstance(value, Mapping):
            raise ValueError("value must be a mapping")
        return dict(value)

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata_field(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("value must be a mapping")
        return dict(value)


class ParseRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    document_id: str
    version_id: str
    source_type: str
    source_uri: str | None = None
    filename: str
    content: bytes
    acl: dict[str, object] = Field(default_factory=_default_acl)
    metadata: dict[str, object] = Field(default_factory=dict)
    checksum: str

    @field_validator(
        "tenant_id",
        "document_id",
        "version_id",
        "source_type",
        "filename",
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

    @field_validator("acl", mode="before")
    @classmethod
    def _acl_field(cls, value: object) -> dict[str, object]:
        if value is None:
            return _default_acl()
        if not isinstance(value, Mapping):
            raise ValueError("value must be a mapping")
        return dict(value)

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata_field(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("value must be a mapping")
        return dict(value)


class Section(BaseModel):
    model_config = ConfigDict(frozen=True)

    section_id: str
    tenant_id: str
    document_id: str
    version_id: str
    source_type: str
    source_uri: str | None = None
    title: str | None = None
    title_path: list[str]
    content: str
    page_start: int | None = None
    page_end: int | None = None
    acl: dict[str, object] = Field(default_factory=_default_acl)
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator(
        "section_id",
        "tenant_id",
        "document_id",
        "version_id",
        "source_type",
    )
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return value if value == normalized else normalized

    @field_validator("content")
    @classmethod
    def _content_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value

    @field_validator("title_path")
    @classmethod
    def _title_path_required(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        if not normalized:
            raise ValueError("title_path must not be empty")
        return normalized

    @field_validator("acl", mode="before")
    @classmethod
    def _acl_field(cls, value: object) -> dict[str, object]:
        if value is None:
            return _default_acl()
        if not isinstance(value, Mapping):
            raise ValueError("value must be a mapping")
        return dict(value)

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata_field(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("value must be a mapping")
        return dict(value)

    @model_validator(mode="after")
    def _page_range_valid(self) -> Self:
        _validate_page_range(self.page_start, self.page_end)
        return self


class ParsedDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    document_id: str
    version_id: str
    source_type: str
    source_uri: str | None = None
    sections: list[Section]
    acl: dict[str, object] = Field(default_factory=_default_acl)
    checksum: str
    metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("tenant_id", "document_id", "version_id", "source_type", "checksum")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("sections")
    @classmethod
    def _sections_required(cls, value: list[Section]) -> list[Section]:
        if not value:
            raise ValueError("sections must not be empty")
        section_ids = [section.section_id for section in value]
        if len(set(section_ids)) != len(section_ids):
            raise ValueError("section_id must be unique within a document")
        return value

    @field_validator("acl", mode="before")
    @classmethod
    def _acl_field(cls, value: object) -> dict[str, object]:
        if value is None:
            return _default_acl()
        if not isinstance(value, Mapping):
            raise ValueError("value must be a mapping")
        return dict(value)

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata_field(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("value must be a mapping")
        return dict(value)


class Chunk(BaseModel):
    model_config = ConfigDict(frozen=True)

    chunk_id: str
    tenant_id: str
    document_id: str
    version_id: str
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

    @field_validator(
        "chunk_id",
        "tenant_id",
        "document_id",
        "version_id",
        "source_type",
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

    @field_validator("content")
    @classmethod
    def _content_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value

    @field_validator("title_path")
    @classmethod
    def _title_path_required(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        if not normalized:
            raise ValueError("title_path must not be empty")
        return normalized

    @field_validator("token_count")
    @classmethod
    def _token_count_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("token_count must be positive")
        return value

    @field_validator("section_ids")
    @classmethod
    def _section_ids_required(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        if not normalized:
            raise ValueError("section_ids must not be empty")
        return normalized

    @field_validator("acl", mode="before")
    @classmethod
    def _acl_field(cls, value: object) -> dict[str, object]:
        if value is None:
            return _default_acl()
        if not isinstance(value, Mapping):
            raise ValueError("value must be a mapping")
        return dict(value)

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata_field(cls, value: object) -> dict[str, object]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("value must be a mapping")
        return dict(value)

    @model_validator(mode="after")
    def _page_range_valid(self) -> Self:
        _validate_page_range(self.page_start, self.page_end)
        return self


def _validate_page_range(page_start: int | None, page_end: int | None) -> None:
    values: tuple[Any, ...] = (page_start, page_end)
    for value in values:
        if value is not None and value < 1:
            raise ValueError("page values must be 1-based positive integers")
    if page_start is not None and page_end is not None and page_end < page_start:
        raise ValueError("page_end must be greater than or equal to page_start")


def safe_title_from_filename(filename: str) -> str:
    title = PurePath(filename.replace("\\", "/")).name.strip()
    return title or "Untitled"

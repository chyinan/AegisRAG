from __future__ import annotations

import re
from urllib.parse import ParseResult, urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

UNTITLED_SOURCE = "Untitled source"
SOURCE_UNAVAILABLE = "Source unavailable"
UNKNOWN_SOURCE_TYPE = "unknown"
_MAX_DISPLAY_CHARS = 160
_MAX_TITLE_PART_CHARS = 160
_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_URL_SCHEMES = frozenset({"http", "https"})
_INTERNAL_SCHEMES = frozenset({"file", "s3", "minio"})
_PROMPT_MARKERS = (
    "assistant:",
    "developer:",
    "ignore previous",
    "ignore system",
    "ignore the previous",
    "ignore the system",
    "prompt:",
    "system:",
    "tool:",
    "user:",
)
_SECRET_MARKERS = (
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "bearer ",
    "password",
    "secret",
    "token",
)


class SafeSourceMetadata(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_display_name: str
    source_type: str = UNKNOWN_SOURCE_TYPE
    document_id: str
    version_id: str
    chunk_id: str
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    title_path: tuple[str, ...] = ("Untitled",)
    source_ref: str | None = None

    @field_validator("source_display_name", "source_type", "document_id", "version_id", "chunk_id")
    @classmethod
    def _required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("value must not be blank")
        return normalized

    @field_validator("title_path")
    @classmethod
    def _title_path_required(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(part.strip() for part in value if part.strip())
        return normalized or ("Untitled",)

    @field_validator("source_ref")
    @classmethod
    def _optional_source_ref(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized or _is_unsafe_display_text(normalized):
            return None
        return _truncate(normalized, _MAX_DISPLAY_CHARS)

    @model_validator(mode="after")
    def _validate_page_range(self) -> SafeSourceMetadata:
        if self.page_start is None and self.page_end is None:
            return self
        if self.page_start is None or self.page_end is None:
            raise ValueError("page_start and page_end must both be set or both be None")
        if self.page_end < self.page_start:
            raise ValueError("page_end must be greater than or equal to page_start")
        return self


class SourceDisplayMetadata(SafeSourceMetadata):
    pass


def build_safe_source_metadata(
    *,
    source: str | None,
    source_uri: str | None,
    source_type: str | None,
    document_id: str,
    version_id: str,
    chunk_id: str,
    page_start: int | None = None,
    page_end: int | None = None,
    title_path: tuple[str, ...] | list[str] = (),
    source_ref: str | None = None,
) -> SafeSourceMetadata:
    normalized_source_type = _safe_source_type(source_type, source_uri)
    title_parts = tuple(
        safe_part
        for part in title_path
        if (safe_part := _safe_display_text(str(part), max_chars=_MAX_TITLE_PART_CHARS)) is not None
    )
    return SafeSourceMetadata(
        source_display_name=_source_display_name(source=source, source_uri=source_uri),
        source_type=normalized_source_type,
        document_id=document_id,
        version_id=version_id,
        chunk_id=chunk_id,
        page_start=page_start,
        page_end=page_end,
        title_path=title_parts or ("Untitled",),
        source_ref=source_ref,
    )


def _source_display_name(*, source: str | None, source_uri: str | None) -> str:
    safe_source = _safe_display_text(source, max_chars=_MAX_DISPLAY_CHARS)
    if safe_source is not None:
        return safe_source

    uri_display = _safe_uri_display(source_uri)
    if uri_display is not None:
        return uri_display
    if source is None or not source.strip():
        return UNTITLED_SOURCE
    return SOURCE_UNAVAILABLE


def _safe_source_type(source_type: str | None, source_uri: str | None) -> str:
    safe = _safe_display_text(source_type, max_chars=64)
    if safe is not None:
        return safe.lower().replace(" ", "_")
    parsed = _parse_uri(source_uri)
    if parsed is not None and parsed.scheme in _URL_SCHEMES:
        return "web"
    return UNKNOWN_SOURCE_TYPE


def _safe_uri_display(value: str | None) -> str | None:
    parsed = _parse_uri(value)
    if parsed is None:
        return None
    if parsed.scheme in _URL_SCHEMES and parsed.hostname:
        host = parsed.hostname.strip().lower()
        if not host or _is_unsafe_display_text(host):
            return None
        return _truncate(host, _MAX_DISPLAY_CHARS)
    return None


def _parse_uri(value: str | None) -> ParseResult | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    try:
        return urlparse(normalized)
    except ValueError:
        return None


def _safe_display_text(value: str | None, *, max_chars: int) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.strip().split())
    if not normalized or _is_unsafe_display_text(normalized):
        return None
    return _truncate(normalized, max_chars)


def _is_unsafe_display_text(value: str) -> bool:
    normalized = value.strip()
    lowered = normalized.lower()
    if any(marker in lowered for marker in _PROMPT_MARKERS):
        return True
    if any(marker in lowered for marker in _SECRET_MARKERS):
        return True
    if "://" in normalized:
        return True
    if _looks_like_local_absolute_path(normalized):
        return True
    if _looks_like_internal_uri(normalized):
        return True
    if _looks_like_object_key(normalized):
        return True
    return "\x00" in normalized or "\r" in normalized or "\n" in normalized or "\t" in normalized


def _looks_like_local_absolute_path(value: str) -> bool:
    return (
        value.startswith("/")
        or value.startswith("\\\\")
        or _WINDOWS_ABSOLUTE_PATH.match(value) is not None
    )


def _looks_like_internal_uri(value: str) -> bool:
    parsed = _parse_uri(value)
    if parsed is None:
        return False
    return parsed.scheme.lower() in _INTERNAL_SCHEMES


def _looks_like_object_key(value: str) -> bool:
    if "://" in value:
        return False
    if "\\" in value:
        return True
    parts = [part for part in value.split("/") if part]
    if len(parts) < 3:
        return False
    return any("." in part for part in parts[1:]) or any(
        part in {"raw", "objects"} for part in parts
    )


def _truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[: max_chars - 1].rstrip() + "..."

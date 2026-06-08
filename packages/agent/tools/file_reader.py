from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from packages.agent.dto import ToolDefinition, ToolRateLimit
from packages.common.context import AuthenticatedRequestContext
from packages.common.logging import REDACTED_VALUE, redact_sensitive_data

FILE_READER_PERMISSION = "agent:tool:file_reader"
FILE_ACCESS_DENIED = "FILE_ACCESS_DENIED"
FILE_NOT_READABLE = "FILE_NOT_READABLE"
FILE_TOO_LARGE = "FILE_TOO_LARGE"
FILE_UNSUPPORTED_TYPE = "FILE_UNSUPPORTED_TYPE"
FILE_CONTENT_REDACTED = "FILE_CONTENT_REDACTED"

_MAX_PATH_LENGTH = 512
_SENSITIVE_NAME_PARTS = (
    ".env",
    "access_token",
    "apikey",
    "api_key",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)


class FileReaderInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=_MAX_PATH_LENGTH)
    max_bytes: int | None = Field(default=None, ge=1)

    @field_validator("path")
    @classmethod
    def _path_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("path must not be blank")
        return normalized


class FileReaderOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["success", "error"]
    file_ref: str | None = None
    bytes_read: int = Field(default=0, ge=0)
    truncated: bool = False
    content_excerpt: str = ""
    error_code: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class FileReaderAllowlist:
    roots: tuple[Path, ...]

    @classmethod
    def from_roots(cls, roots: tuple[str | Path, ...]) -> FileReaderAllowlist:
        resolved: list[Path] = []
        for root in roots:
            path = Path(root).resolve(strict=True)
            if not path.is_dir():
                raise ValueError("allowlist root must be an existing directory")
            resolved.append(path)
        if not resolved:
            raise ValueError("at least one allowlist root is required")
        return cls(roots=tuple(resolved))

    def resolve(self, requested_path: str) -> ResolvedAllowedFile | None:
        candidate_path = Path(requested_path)
        if _is_unsafe_relative_path(candidate_path):
            return None

        for root in self.roots:
            try:
                resolved = (root / candidate_path).resolve(strict=True)
            except OSError:
                continue
            if _is_relative_to(resolved, root):
                return ResolvedAllowedFile(path=resolved, file_ref=candidate_path.as_posix())
        return None


@dataclass(frozen=True)
class ResolvedAllowedFile:
    path: Path
    file_ref: str


def build_file_reader_tool(
    *,
    allowlist_roots: tuple[str | Path, ...],
    max_file_bytes: int,
    max_return_bytes: int,
    timeout_seconds: float,
    rate_limit: ToolRateLimit,
) -> ToolDefinition:
    if max_file_bytes <= 0:
        raise ValueError("max_file_bytes must be positive")
    if max_return_bytes <= 0:
        raise ValueError("max_return_bytes must be positive")
    if max_return_bytes > max_file_bytes:
        raise ValueError("max_return_bytes must not exceed max_file_bytes")

    allowlist = FileReaderAllowlist.from_roots(allowlist_roots)

    async def handler(
        payload: FileReaderInput,
        context: AuthenticatedRequestContext,
    ) -> FileReaderOutput:
        _ = context
        resolved = allowlist.resolve(payload.path)
        if resolved is None:
            return _error(FILE_ACCESS_DENIED, "file_access_denied")

        path = resolved.path
        if path.is_dir():
            return _error(FILE_NOT_READABLE, "file_not_readable")
        if _has_sensitive_or_hidden_part(path, resolved.file_ref):
            return _error(FILE_ACCESS_DENIED, "file_access_denied")

        try:
            file_size = path.stat().st_size
        except OSError:
            return _error(FILE_NOT_READABLE, "file_not_readable")
        if file_size > max_file_bytes:
            return _error(FILE_TOO_LARGE, "file_too_large")

        try:
            data = path.read_bytes()
        except OSError:
            return _error(FILE_NOT_READABLE, "file_not_readable")
        if _looks_binary(data):
            return _error(FILE_UNSUPPORTED_TYPE, "file_unsupported_type")

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return _error(FILE_UNSUPPORTED_TYPE, "file_unsupported_type")

        effective_return_bytes = min(
            payload.max_bytes if payload.max_bytes is not None else max_return_bytes,
            max_return_bytes,
            max_file_bytes,
        )
        excerpt_bytes = text.encode("utf-8")[:effective_return_bytes]
        excerpt = excerpt_bytes.decode("utf-8", errors="ignore")
        redacted = redact_sensitive_data(excerpt)
        safe_excerpt = redacted if isinstance(redacted, str) else REDACTED_VALUE

        return FileReaderOutput(
            status="success",
            file_ref=resolved.file_ref,
            bytes_read=len(excerpt_bytes),
            truncated=len(data) > len(excerpt_bytes),
            content_excerpt=safe_excerpt,
        )

    return ToolDefinition(
        name="file_reader",
        description="Read bounded text excerpts from explicitly allowlisted local files.",
        input_schema=FileReaderInput,
        output_schema=FileReaderOutput,
        permission=FILE_READER_PERMISSION,
        timeout_seconds=timeout_seconds,
        rate_limit=rate_limit,
        handler=handler,
    )


def _is_unsafe_relative_path(path: Path) -> bool:
    if path.is_absolute():
        return True
    return any(part in {"", ".", ".."} for part in path.parts)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _has_sensitive_or_hidden_part(path: Path, file_ref: str) -> bool:
    parts = tuple(Path(file_ref).parts)
    if any(part.startswith(".") for part in parts):
        return True
    lowered_name = path.name.lower()
    compact_name = lowered_name.replace("-", "_").replace(" ", "_")
    return any(part in compact_name for part in _SENSITIVE_NAME_PARTS)


def _looks_binary(data: bytes) -> bool:
    return b"\x00" in data


def _error(error_code: str, message: str) -> FileReaderOutput:
    return FileReaderOutput(
        status="error",
        file_ref=None,
        bytes_read=0,
        truncated=False,
        content_excerpt="",
        error_code=error_code,
        message=message,
    )

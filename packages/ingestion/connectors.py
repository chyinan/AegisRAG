"""Secure, deterministic source connector ports and local-folder adapter."""

# pattern: Imperative Shell

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from packages.ingestion.security import validate_content_signature


@dataclass(frozen=True)
class ConnectorDocument:
    tenant_id: str
    external_id: str
    filename: str
    source_uri: str
    source_type: str
    payload: bytes
    checksum: str
    size_bytes: int
    relative_path: str


class FolderConnector:
    """Read supported documents below a configured root without symlink escape."""

    _SUPPORTED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".docx", ".txt", ".md", ".markdown"})

    def __init__(
        self,
        *,
        root: Path,
        tenant_id: str,
        connector_id: str = "folder",
        max_bytes: int = 25 * 1024 * 1024,
        cursor_secret: str = "development-folder-cursor",
    ) -> None:
        if root.is_symlink():
            raise ValueError("connector root must not be a symlink")
        resolved_root = root.expanduser().resolve()
        if not tenant_id.strip():
            raise ValueError("tenant_id must not be blank")
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        if not cursor_secret.strip():
            raise ValueError("cursor_secret must not be blank")
        if not connector_id.strip():
            raise ValueError("connector_id must not be blank")
        if re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", connector_id.strip()) is None:
            raise ValueError("connector_id must be a safe slug")
        if not resolved_root.is_dir():
            raise ValueError("connector root must be an existing directory")
        self._root = resolved_root
        self._tenant_id = tenant_id.strip()
        self._connector_id = connector_id.strip()
        self._max_bytes = max_bytes
        self._cursor_secret = cursor_secret.encode("utf-8")

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def iter_documents(self, *, start_after: str | None = None) -> Iterator[ConnectorDocument]:
        # Recursively scandir in path order.  Unlike os.walk this is streaming,
        # so a page does not materialize or re-read the entire tree.
        for relative_path, path in _iter_connector_paths(self._root):
            if start_after is not None and relative_path <= start_after:
                continue
            if (
                path.is_symlink()
                or _contains_symlink_component(path, self._root)
                or not path.is_file()
                or path.suffix.lower() not in self._SUPPORTED_EXTENSIONS
            ):
                continue
            file_descriptor: int | None = None
            try:
                resolved = path.resolve()
                resolved.relative_to(self._root)
                flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
                flags |= getattr(os, "O_NOFOLLOW", 0)
                file_descriptor = os.open(resolved, flags)
                size_bytes = os.fstat(file_descriptor).st_size
                if size_bytes > self._max_bytes:
                    os.close(file_descriptor)
                    file_descriptor = None
                    continue
                with os.fdopen(file_descriptor, "rb") as handle:
                    file_descriptor = None
                    payload = handle.read(self._max_bytes + 1)
                if len(payload) > self._max_bytes:
                    continue
            except (OSError, ValueError):
                if file_descriptor is not None:
                    os.close(file_descriptor)
                continue
            try:
                validate_content_signature(
                    source_type=_source_type(resolved.suffix),
                    payload=payload,
                    max_uncompressed_bytes=self._max_bytes * 4,
                )
            except ValueError:
                continue
            checksum = hashlib.sha256(payload).hexdigest()
            external_id = f"{self._tenant_id}:{self._connector_id}:{relative_path}"
            yield ConnectorDocument(
                tenant_id=self._tenant_id,
                external_id=external_id,
                filename=resolved.name,
                source_uri=f"folder://{self._connector_id}/{relative_path}",
                source_type=_source_type(resolved.suffix),
                payload=payload,
                checksum=checksum,
                size_bytes=size_bytes,
                relative_path=relative_path,
            )

    def cursor_token(self, relative_path: str) -> str:
        return _signed_cursor_token(relative_path, self._cursor_secret)

    def decode_cursor(self, token: str) -> str:
        return _decode_signed_cursor(token, self._cursor_secret)


def folder_cursor_token(relative_path: str) -> str:
    """Return a bounded opaque cursor that does not disclose filesystem paths."""
    return _signed_cursor_token(relative_path, b"development-folder-cursor")


def is_valid_folder_cursor(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_-]{1,479}\.[0-9a-f]{32}", value))


def _signed_cursor_token(relative_path: str, secret: bytes) -> str:
    payload = base64.urlsafe_b64encode(relative_path.encode("utf-8")).decode("ascii").rstrip("=")
    signature = hmac.new(secret, payload.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    token = f"{payload}.{signature}"
    if len(token) > 512:
        raise ValueError("relative path is too long for a bounded cursor")
    return token


def _decode_signed_cursor(token: str, secret: bytes) -> str:
    if not is_valid_folder_cursor(token):
        raise ValueError("invalid folder cursor")
    payload, signature = token.rsplit(".", 1)
    expected = hmac.new(secret, payload.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(signature, expected):
        raise ValueError("invalid folder cursor signature")
    padded = payload + "=" * (-len(payload) % 4)
    try:
        relative_path = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("invalid folder cursor payload") from exc
    path = Path(relative_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("invalid folder cursor path")
    return path.as_posix()


def _contains_symlink_component(path: Path, root: Path) -> bool:
    try:
        relative_parts = path.relative_to(root).parts
    except ValueError:
        return True
    current = root
    for part in relative_parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def _iter_connector_paths(
    root: Path,
    *,
    base_root: Path | None = None,
) -> Iterator[tuple[str, Path]]:
    base = base_root or root
    try:
        entries = list(os.scandir(root))
    except OSError:
        return
    ordered_entries = sorted(
        entries,
        key=lambda entry: entry.name + ("/" if entry.is_dir(follow_symlinks=False) else ""),
    )
    for entry in ordered_entries:
        path = Path(entry.path)
        relative_path = path.relative_to(base).as_posix()
        if entry.is_file(follow_symlinks=False):
            yield relative_path, path
        elif entry.is_dir(follow_symlinks=False):
            yield from _iter_connector_paths(path, base_root=base)


def _source_type(suffix: str) -> str:
    normalized = suffix.lower()
    if normalized == ".markdown":
        return "markdown"
    return normalized.removeprefix(".")

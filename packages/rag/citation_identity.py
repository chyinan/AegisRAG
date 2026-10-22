"""Canonical public identity shared by prompts, citations, and restored history.

The identifier is a label, not an authorization token. Source access must still be
checked using the document/version/chunk tuple and the current request identity.
"""

from __future__ import annotations

import hashlib
import json
from typing import Protocol


class CitationSourceIdentity(Protocol):
    @property
    def document_id(self) -> str: ...

    @property
    def version_id(self) -> str: ...

    @property
    def chunk_id(self) -> str: ...


def citation_id(source: CitationSourceIdentity) -> str:
    """Preserve the existing UTF-8 JSON tuple/SHA-256 marker contract."""
    encoded = json.dumps(
        (source.document_id, source.version_id, source.chunk_id),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
    return f"cite-{digest}"

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Final

from packages.ingestion.cleaner import canonicalize_content, stable_content_checksum
from packages.ingestion.domain import Chunk, ParsedDocument, Section
from packages.ingestion.exceptions import (
    EmptyChunkContentError,
    GenericDocumentChunkError,
    InvalidChunkConfigError,
)

TokenEstimator = Callable[[str], int]

_CHUNK_ID_NAMESPACE: Final = uuid.UUID("8a07fb65-7f79-4a7a-8a2a-2b9c4a4f6a24")
_TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]|[^\s]+", re.UNICODE)


@dataclass(frozen=True)
class FixedSizeChunkerConfig:
    min_tokens: int = 500
    max_tokens: int = 800
    overlap_ratio: float = 0.15

    def __post_init__(self) -> None:
        if isinstance(self.min_tokens, bool) or not isinstance(self.min_tokens, int):
            raise InvalidChunkConfigError(details={"reason": "min_tokens_must_be_integer"})
        if isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int):
            raise InvalidChunkConfigError(details={"reason": "max_tokens_must_be_integer"})
        if not isinstance(self.overlap_ratio, int | float) or not isfinite(
            self.overlap_ratio
        ):
            raise InvalidChunkConfigError(details={"reason": "overlap_ratio_must_be_finite"})
        if self.min_tokens <= 0:
            raise InvalidChunkConfigError(details={"reason": "min_tokens_must_be_positive"})
        if self.max_tokens < self.min_tokens:
            raise InvalidChunkConfigError(
                details={"reason": "max_tokens_must_be_at_least_min_tokens"}
            )
        if self.overlap_ratio < 0.10 or self.overlap_ratio > 0.20:
            raise InvalidChunkConfigError(details={"reason": "overlap_ratio_out_of_range"})
        if max(1, int(self.max_tokens * self.overlap_ratio)) >= self.max_tokens:
            raise InvalidChunkConfigError(
                details={"reason": "overlap_must_be_smaller_than_window"}
            )


@dataclass(frozen=True)
class _TokenRef:
    text: str
    section: Section
    section_content: str
    start: int
    end: int


class FixedSizeChunker:
    def __init__(
        self,
        config: FixedSizeChunkerConfig | None = None,
        *,
        token_estimator: TokenEstimator | None = None,
    ) -> None:
        self._config = config or FixedSizeChunkerConfig()
        self._token_estimator = token_estimator or estimate_tokens

    def split(self, document: ParsedDocument) -> list[Chunk]:
        tokens = self._collect_tokens(document)
        if not tokens:
            raise EmptyChunkContentError(
                details={
                    "document_id": document.document_id,
                    "version_id": document.version_id,
                    "reason": "empty_document_content",
                }
            )

        chunks: list[Chunk] = []
        start = 0
        chunk_index = 0
        overlap_tokens = max(1, int(self._config.max_tokens * self._config.overlap_ratio))

        while start < len(tokens):
            end = min(start + self._config.max_tokens, len(tokens))
            end = self._adjust_end_for_tail(tokens=tokens, start=start, end=end)
            window = tokens[start:end]
            chunk = self._build_chunk(
                document=document,
                token_window=window,
                chunk_index=chunk_index,
            )
            chunks.append(chunk)

            if end == len(tokens):
                break
            next_start = max(0, end - overlap_tokens)
            if next_start <= start:
                raise InvalidChunkConfigError(
                    details={"reason": "overlap_must_advance_window"}
                )
            start = next_start
            chunk_index += 1

        return chunks

    def _collect_tokens(self, document: ParsedDocument) -> list[_TokenRef]:
        tokens: list[_TokenRef] = []
        for section in document.sections:
            self._validate_section_boundary(document=document, section=section)
            content = canonicalize_content(section.content)
            if not content:
                raise EmptyChunkContentError(
                    details=_safe_details(document, section, "empty_section_content")
                )
            self._safe_estimate(content, document=document, section=section)
            tokens.extend(_iter_token_refs(content=content, section=section))
        return tokens

    def _adjust_end_for_tail(
        self,
        *,
        tokens: Sequence[_TokenRef],
        start: int,
        end: int,
    ) -> int:
        if end == len(tokens):
            return end
        overlap_tokens = max(1, int(self._config.max_tokens * self._config.overlap_ratio))
        next_chunk_length = len(tokens) - (end - overlap_tokens)
        if next_chunk_length >= self._config.min_tokens:
            return end

        deficit = self._config.min_tokens - next_chunk_length
        adjusted_end = max(start + self._config.min_tokens, end - deficit)
        if adjusted_end <= start or adjusted_end >= end:
            return end
        return adjusted_end

    def _build_chunk(
        self,
        *,
        document: ParsedDocument,
        token_window: Sequence[_TokenRef],
        chunk_index: int,
    ) -> Chunk:
        sections = _ordered_unique_sections(token_window)
        _validate_acl_consistency(document=document, sections=sections)

        content = _chunk_content(token_window)
        if not content:
            raise EmptyChunkContentError(
                details={
                    "document_id": document.document_id,
                    "version_id": document.version_id,
                    "reason": "empty_chunk_content",
                }
            )

        token_count = self._safe_estimate(content, document=document, section=sections[0])
        if token_count > self._config.max_tokens:
            raise GenericDocumentChunkError(
                details=_safe_details(document, sections[0], "chunk_token_budget_exceeded")
            )
        checksum = stable_content_checksum(content)
        section_ids = [section.section_id for section in sections]
        title_paths = [section.title_path for section in sections]
        page_start, page_end = _page_range(sections)
        overlap_token_count = 0 if chunk_index == 0 else int(
            self._config.max_tokens * self._config.overlap_ratio
        )

        return Chunk(
            chunk_id=_stable_chunk_id(
                tenant_id=document.tenant_id,
                document_id=document.document_id,
                version_id=document.version_id,
                section_ids=section_ids,
                chunk_index=chunk_index,
                checksum=checksum,
            ),
            tenant_id=document.tenant_id,
            document_id=document.document_id,
            version_id=document.version_id,
            source_type=document.source_type,
            source_uri=document.source_uri,
            title_path=_chunk_title_path(title_paths),
            content=content,
            page_start=page_start,
            page_end=page_end,
            token_count=token_count,
            acl=dict(sections[0].acl),
            checksum=checksum,
            section_ids=section_ids,
            metadata={
                "title_paths": title_paths,
                "source_section_count": len(section_ids),
                "overlap_ratio": self._config.overlap_ratio,
                "overlap_token_count": overlap_token_count,
                "chunk_index": chunk_index,
            },
        )

    def _safe_estimate(
        self,
        content: str,
        *,
        document: ParsedDocument,
        section: Section,
    ) -> int:
        try:
            estimated = self._token_estimator(content)
        except Exception as exc:
            raise GenericDocumentChunkError(
                details=_safe_details(document, section, "token_estimator_failed")
            ) from exc

        if estimated <= 0:
            estimated = estimate_tokens(content)
        if estimated <= 0:
            raise GenericDocumentChunkError(
                details=_safe_details(document, section, "token_estimator_non_positive")
            )
        return estimated

    def _validate_section_boundary(self, *, document: ParsedDocument, section: Section) -> None:
        if (
            section.tenant_id != document.tenant_id
            or section.document_id != document.document_id
            or section.version_id != document.version_id
            or section.source_type != document.source_type
            or section.source_uri != document.source_uri
        ):
            raise GenericDocumentChunkError(
                details=_safe_details(document, section, "section_boundary_mismatch")
            )


def estimate_tokens(content: str) -> int:
    canonical = canonicalize_content(content)
    if not canonical:
        return 0
    return len(_split_tokens(canonical))


def _split_tokens(content: str) -> list[str]:
    return _TOKEN_PATTERN.findall(content)


def _iter_token_refs(*, content: str, section: Section) -> list[_TokenRef]:
    return [
        _TokenRef(
            text=match.group(0),
            section=section,
            section_content=content,
            start=match.start(),
            end=match.end(),
        )
        for match in _TOKEN_PATTERN.finditer(content)
    ]


def _chunk_content(token_window: Sequence[_TokenRef]) -> str:
    parts: list[str] = []
    current_section_id: str | None = None
    current_content = ""
    current_start = 0
    current_end = 0

    for token in token_window:
        if token.section.section_id != current_section_id:
            if current_section_id is not None:
                parts.append(current_content[current_start:current_end].strip())
            current_section_id = token.section.section_id
            current_content = token.section_content
            current_start = token.start
        current_end = token.end

    if current_section_id is not None:
        parts.append(current_content[current_start:current_end].strip())

    return canonicalize_content("\n\n".join(part for part in parts if part))


def _ordered_unique_sections(tokens: Sequence[_TokenRef]) -> list[Section]:
    sections: list[Section] = []
    seen: set[str] = set()
    for token in tokens:
        section_id = token.section.section_id
        if section_id in seen:
            continue
        seen.add(section_id)
        sections.append(token.section)
    return sections


def _validate_acl_consistency(*, document: ParsedDocument, sections: Sequence[Section]) -> None:
    if not sections:
        return
    first_acl = sections[0].acl
    for section in sections[1:]:
        if section.acl != first_acl:
            raise GenericDocumentChunkError(
                details=_safe_details(document, section, "acl_mismatch")
            )


def _page_range(sections: Sequence[Section]) -> tuple[int | None, int | None]:
    page_starts = [section.page_start for section in sections if section.page_start is not None]
    page_ends = [section.page_end for section in sections if section.page_end is not None]
    if not page_starts and not page_ends:
        return None, None
    return (
        min(page_starts) if page_starts else None,
        max(page_ends) if page_ends else None,
    )


def _chunk_title_path(title_paths: Sequence[list[str]]) -> list[str]:
    if len(title_paths) == 1:
        return list(title_paths[0])

    common: list[str] = []
    for items in zip(*title_paths, strict=False):
        if all(item == items[0] for item in items):
            common.append(items[0])
        else:
            break
    return common or list(title_paths[0])


def _stable_chunk_id(
    *,
    tenant_id: str,
    document_id: str,
    version_id: str,
    section_ids: Sequence[str],
    chunk_index: int,
    checksum: str,
) -> str:
    name = "|".join(
        [
            tenant_id,
            document_id,
            version_id,
            ",".join(section_ids),
            str(chunk_index),
            checksum,
        ]
    )
    return str(uuid.uuid5(_CHUNK_ID_NAMESPACE, name))


def _safe_details(document: ParsedDocument, section: Section, reason: str) -> dict[str, object]:
    return {
        "document_id": document.document_id,
        "version_id": document.version_id,
        "section_id": section.section_id,
        "reason": reason,
    }

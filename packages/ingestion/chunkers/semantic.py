"""Semantic chunker that splits documents at natural topic boundaries.

Uses embedding similarity between consecutive sentences to detect topic
shifts.  When similarity falls below a configurable threshold the
algorithm starts a new chunk.  The fixed-size chunker remains the
default; enable semantic chunking by injecting a SemanticChunker
instance into the ingestion service.
"""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from packages.embeddings.dto import EmbeddingRequest
from packages.embeddings.ports import EmbeddingProvider
from packages.ingestion.cleaner import canonicalize_content, stable_content_checksum
from packages.ingestion.domain import Chunk, ParsedDocument, Section
from packages.ingestion.exceptions import (
    EmptyChunkContentError,
    GenericDocumentChunkError,
    InvalidChunkConfigError,
)

# ---------------------------------------------------------------------------
# Sentence splitting
# ---------------------------------------------------------------------------

# Splits on terminal punctuation followed by whitespace (Western style).
_SENTENCE_BOUNDARY_WS = re.compile(
    r"(?<=[.!?\u3002\uff01\uff1f])\s+(?=\S)", re.UNICODE
)

# Splits on CJK terminal punctuation even without whitespace (CJK style).
_SENTENCE_BOUNDARY_CJK = re.compile(
    r"(?<=[\u3002\uff01\uff1f])(?=[^\s])", re.UNICODE
)

# Paragraph breaks (double-newline) demarcate sentence groups.
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


def _split_sentences(text: str) -> list[str]:
    """Split *text* into sentences, treating paragraph breaks as boundaries."""
    paragraphs = _PARAGRAPH_BREAK.split(text)
    sentences: list[str] = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # Try Western-style (whitespace after punctuation).
        parts = [s.strip() for s in _SENTENCE_BOUNDARY_WS.split(para) if s.strip()]
        if len(parts) <= 1:
            # Try CJK-style (no whitespace).
            parts = [s.strip() for s in _SENTENCE_BOUNDARY_CJK.split(para) if s.strip()]
        if not parts:
            parts = [para]
        sentences.extend(parts)
    return sentences


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SemanticChunkerConfig:
    """Configuration for the semantic chunker.

    Attributes:
        min_chunk_sentences: Minimum sentences per chunk (guards against
            over-fragmentation).
        max_chunk_sentences: Hard ceiling on sentences per chunk – if
            reached the chunker emits a chunk regardless of similarity.
        semantic_threshold: Cosine-similarity threshold (0.0 – 1.0)
            below which a topic shift is declared.
        embedding_provider: Provider string forwarded to
            ``EmbeddingProvider`` (default ``"local"``).
        embedding_model: Model string forwarded to
            ``EmbeddingProvider`` (default ``"sentence-transformer"``).
        embedding_timeout_seconds: Timeout for embedding calls.
        embedding_retry_budget: Retries for embedding calls.
    """

    min_chunk_sentences: int = 3
    max_chunk_sentences: int = 50
    semantic_threshold: float = 0.65
    embedding_provider: str = "local"
    embedding_model: str = "sentence-transformer"
    embedding_timeout_seconds: float = 10.0
    embedding_retry_budget: int = 2

    def __post_init__(self) -> None:
        if self.min_chunk_sentences < 1:
            raise InvalidChunkConfigError(
                details={"reason": "min_chunk_sentences_must_be_positive"}
            )
        if self.max_chunk_sentences < self.min_chunk_sentences:
            raise InvalidChunkConfigError(
                details={"reason": "max_chunk_sentences_must_be_at_least_min"}
            )
        if not isinstance(self.semantic_threshold, float) or not math.isfinite(
            self.semantic_threshold
        ):
            raise InvalidChunkConfigError(
                details={"reason": "semantic_threshold_must_be_finite_float"}
            )
        if self.semantic_threshold < 0.0 or self.semantic_threshold > 1.0:
            raise InvalidChunkConfigError(
                details={"reason": "semantic_threshold_out_of_range"}
            )
        if self.embedding_timeout_seconds <= 0:
            raise InvalidChunkConfigError(
                details={"reason": "embedding_timeout_must_be_positive"}
            )
        if self.embedding_retry_budget < 0:
            raise InvalidChunkConfigError(
                details={"reason": "embedding_retry_budget_must_not_be_negative"}
            )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_CHUNK_ID_NAMESPACE: Final = uuid.UUID("9b37df86-8e4a-4f2c-9a6d-3c0d5b7e8f12")


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors of equal length."""
    if len(a) != len(b):
        raise ValueError("vectors must have equal length")
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _page_range(sections: Sequence[Section]) -> tuple[int | None, int | None]:
    page_starts = [s.page_start for s in sections if s.page_start is not None]
    page_ends = [s.page_end for s in sections if s.page_end is not None]
    if not page_starts and not page_ends:
        return None, None
    return (
        min(page_starts) if page_starts else None,
        max(page_ends) if page_ends else None,
    )


def _ordered_unique_sections(
    sentence_sections: Sequence[tuple[str, Section]],
) -> list[Section]:
    """Return sections in first-seen order (deduplicated)."""
    seen: set[str] = set()
    result: list[Section] = []
    for _, section in sentence_sections:
        if section.section_id in seen:
            continue
        seen.add(section.section_id)
        result.append(section)
    return result


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


def _safe_details(
    document: ParsedDocument, section: Section, reason: str
) -> dict[str, object]:
    return {
        "document_id": document.document_id,
        "version_id": document.version_id,
        "section_id": section.section_id,
        "reason": reason,
    }


def _validate_acl_consistency(
    *, document: ParsedDocument, sections: Sequence[Section]
) -> None:
    if not sections:
        return
    first_acl = sections[0].acl
    for section in sections[1:]:
        if section.acl != first_acl:
            raise GenericDocumentChunkError(
                details=_safe_details(document, section, "acl_mismatch")
            )


# ---------------------------------------------------------------------------
# Main Semantic Chunker
# ---------------------------------------------------------------------------


class SemanticChunker:
    """Chunker that splits documents at semantic topic boundaries.

    The chunker embeds every sentence, measures cosine similarity between
    consecutive sentences, and emits a new chunk whenever the similarity
    drops below ``semantic_threshold``.  Hard paragraph breaks are always
    respected as chunk boundaries.  The configuration also enforces a
    minimum and maximum sentence count per chunk to prevent over- or
    under-fragmentation.
    """

    def __init__(
        self,
        config: SemanticChunkerConfig | None = None,
        *,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._config = config or SemanticChunkerConfig()
        self._embedding_provider = embedding_provider

    async def split(self, document: ParsedDocument) -> list[Chunk]:
        # ---- collect sentences with their section context ----
        sentence_entries: list[tuple[str, Section]] = []
        for section in document.sections:
            self._validate_section_boundary(document=document, section=section)
            content = canonicalize_content(section.content)
            if not content:
                raise EmptyChunkContentError(
                    details=_safe_details(document, section, "empty_section_content")
                )
            sentences = _split_sentences(content)
            if not sentences:
                raise EmptyChunkContentError(
                    details=_safe_details(document, section, "empty_section_content")
                )
            sentence_entries.extend((s, section) for s in sentences)

        if not sentence_entries:
            raise EmptyChunkContentError(
                details={
                    "document_id": document.document_id,
                    "version_id": document.version_id,
                    "reason": "empty_document_content",
                }
            )

        # ---- embed all sentences in one batch ----
        embeddings = await self._embed_sentences(
            [s for s, _ in sentence_entries],
            rate_limit_key=document.tenant_id,
        )

        # ---- detect topic shifts ----
        boundaries = self._detect_boundaries(
            sentence_entries=sentence_entries,
            embeddings=embeddings,
        )

        # ---- build chunks ----
        chunks: list[Chunk] = []
        chunk_index = 0
        segment_start = 0

        for boundary in boundaries:
            segment = sentence_entries[segment_start:boundary]
            if segment:
                chunk = self._build_chunk(
                    document=document,
                    sentence_entries=segment,
                    chunk_index=chunk_index,
                )
                chunks.append(chunk)
                chunk_index += 1
            segment_start = boundary

        # tail
        segment = sentence_entries[segment_start:]
        if segment:
            chunk = self._build_chunk(
                document=document,
                sentence_entries=segment,
                chunk_index=chunk_index,
            )
            chunks.append(chunk)

        if not chunks:
            raise EmptyChunkContentError(
                details={
                    "document_id": document.document_id,
                    "version_id": document.version_id,
                    "reason": "empty_document_content",
                }
            )

        return chunks

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _embed_sentences(
        self,
        sentences: list[str],
        *,
        rate_limit_key: str,
    ) -> list[list[float]]:
        """Return an embedding vector for every sentence via the provider."""
        if not sentences:
            return []

        response = await self._embedding_provider.embed_texts(
            EmbeddingRequest(
                texts=sentences,
                provider=self._config.embedding_provider,
                model=self._config.embedding_model,
                timeout_seconds=self._config.embedding_timeout_seconds,
                retry_budget=self._config.embedding_retry_budget,
                rate_limit_key=rate_limit_key,
                metadata={"stage": "semantic_chunking"},
            )
        )

        # Order by index to match sentence order.
        vectors_by_index: dict[int, list[float]] = {
            v.index: v.vector for v in response.vectors
        }
        return [vectors_by_index[i] for i in range(len(sentences))]

    def _detect_boundaries(
        self,
        *,
        sentence_entries: list[tuple[str, Section]],
        embeddings: list[list[float]],
    ) -> list[int]:
        """Return sentence indices where a new chunk should start."""
        boundaries: list[int] = []
        current_len = 1  # sentences in the current segment

        for i in range(1, len(sentence_entries)):
            prev_text: str = sentence_entries[i - 1][0]
            prev_section: Section = sentence_entries[i - 1][1]
            curr_text: str = sentence_entries[i][0]
            curr_section: Section = sentence_entries[i][1]

            # Hard boundary: only section changes force a split.
            section_change = prev_section.section_id != curr_section.section_id

            # Soft boundary: cosine similarity below threshold.
            similarity = (
                _cosine_similarity(embeddings[i - 1], embeddings[i])
                if i < len(embeddings)
                else 1.0
            )

            soft_boundary = similarity < self._config.semantic_threshold

            if section_change:
                if current_len >= self._config.min_chunk_sentences:
                    boundaries.append(i)
                    current_len = 0
            elif soft_boundary and current_len >= self._config.min_chunk_sentences:
                boundaries.append(i)
                current_len = 0
            elif current_len >= self._config.max_chunk_sentences:
                # Force a boundary at the max chunk size.
                boundaries.append(i)
                current_len = 0

            current_len += 1

        return boundaries

    def _build_chunk(
        self,
        *,
        document: ParsedDocument,
        sentence_entries: list[tuple[str, Section]],
        chunk_index: int,
    ) -> Chunk:
        # Validate ACL consistency across ALL sections (without dedup).
        all_sections = [sec for _, sec in sentence_entries]
        _validate_acl_consistency(document=document, sections=all_sections)

        sections = _ordered_unique_sections(sentence_entries)

        content = "\n\n".join(text for text, _ in sentence_entries)
        content = canonicalize_content(content)
        if not content:
            raise EmptyChunkContentError(
                details={
                    "document_id": document.document_id,
                    "version_id": document.version_id,
                    "reason": "empty_chunk_content",
                }
            )

        checksum = stable_content_checksum(content)
        section_ids = [section.section_id for section in sections]
        title_paths = [section.title_path for section in sections]
        page_start, page_end = _page_range(sections)

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
            token_count=len(content.split()),
            acl=dict(sections[0].acl),
            checksum=checksum,
            section_ids=section_ids,
            metadata={
                "title_paths": title_paths,
                "source_section_count": len(section_ids),
                "chunker": "semantic",
                "sentence_count": len(sentence_entries),
                "semantic_threshold": self._config.semantic_threshold,
                "chunk_index": chunk_index,
            },
        )

    def _validate_section_boundary(
        self, *, document: ParsedDocument, section: Section
    ) -> None:
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

"""Integration tests for the Semantic Chunker.

These tests exercise the full semantic chunking pipeline: sentence
splitting, embedding-based similarity detection, boundary placement,
and metadata preservation.  A fake embedding provider returns
predictable vectors so tests are deterministic.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import cast

import pytest

from packages.embeddings.dto import EmbeddingRequest, EmbeddingResponse, EmbeddingVector
from packages.embeddings.ports import EmbeddingProvider
from packages.ingestion.chunkers.semantic import (
    SemanticChunker,
    SemanticChunkerConfig,
    _cosine_similarity,
    _split_sentences,
)
from packages.ingestion.domain import ParsedDocument, Section
from packages.ingestion.exceptions import (
    DOCUMENT_CHUNK_CONFIG_INVALID,
    DOCUMENT_CHUNK_EMPTY_CONTENT,
    DOCUMENT_CHUNK_FAILED,
    DocumentChunkError,
)

# ---------------------------------------------------------------------------
# Test helpers – mirror the patterns from test_fixed_size_chunker.py
# ---------------------------------------------------------------------------


def _section(
    section_id: str,
    content: str,
    *,
    source_type: str = "markdown",
    title_path: list[str] | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
    acl: dict[str, object] | None = None,
) -> Section:
    return Section(
        section_id=section_id,
        tenant_id="tenant-1",
        document_id="doc-1",
        version_id="ver-1",
        source_type=source_type,
        source_uri="kb://policy",
        title=f"Section {section_id}",
        title_path=title_path or ["Policy", "Scope"],
        content=content,
        page_start=page_start,
        page_end=page_end,
        acl=acl or {"visibility": "tenant", "groups": ["hr"]},
        metadata={"parser": "synthetic"},
    )


def _document(sections: list[Section], *, source_type: str = "markdown") -> ParsedDocument:
    return ParsedDocument(
        tenant_id="tenant-1",
        document_id="doc-1",
        version_id="ver-1",
        source_type=source_type,
        source_uri="kb://policy",
        sections=sections,
        acl={"visibility": "tenant", "groups": ["hr"]},
        checksum="raw-checksum-1",
        metadata={"stage": "deduped"},
    )


# ---------------------------------------------------------------------------
# Fake embedding provider that returns predictable vectors
# ---------------------------------------------------------------------------


@dataclass
class FakeEmbeddingProvider:
    """Returns vectors that encode positions so we can control similarity."""

    dim: int = 8
    _calls: list[EmbeddingRequest] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self._calls = []

    async def embed_texts(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self._calls.append(request)
        vectors: list[EmbeddingVector] = []
        for i, text in enumerate(request.texts):
            # Use a simple hash-like vector so different texts produce
            # different vectors.  Similar texts → similar vectors.
            seed = abs(hash(text)) % 1000
            vector = [
                math.sin(seed * 0.1 + j * 0.5) for j in range(self.dim)
            ]
            vectors.append(EmbeddingVector(index=i, vector=vector))
        return EmbeddingResponse(
            vectors=vectors,
            provider=request.provider,
            model=request.model,
            dim=self.dim,
            latency_ms=1.0,
        )


# ---------------------------------------------------------------------------
# Helper: create a provider that yields identical vectors for "same-topic"
# sentences and different vectors for "different-topic" sentences.
# ---------------------------------------------------------------------------


class _TopicAwareFakeProvider:
    """Each sentence prefixed with a "topic tag" like `<0>`, `<1>` yields a
    distinct pre-computed vector.  Sentences sharing the same tag will have
    cosine similarity ~1.0; different tags ~0.0.

    Tags must be a single digit 0-9.
    """

    def __init__(self, dim: int = 8, separator: str = " "):
        self.dim = dim
        self.separator = separator
        # Pre-compute orthogonal-ish vectors for 10 topics.
        self._topic_vectors: dict[int, list[float]] = {}
        for topic in range(10):
            vec = [0.0] * dim
            vec[topic % dim] = 1.0
            self._topic_vectors[topic] = vec

    async def embed_texts(self, request: EmbeddingRequest) -> EmbeddingResponse:
        vectors: list[EmbeddingVector] = []
        for i, text in enumerate(request.texts):
            topic = self._extract_topic(text)
            vector = list(self._topic_vectors.get(topic, [0.0] * self.dim))
            vectors.append(EmbeddingVector(index=i, vector=vector))
        return EmbeddingResponse(
            vectors=vectors,
            provider=request.provider,
            model=request.model,
            dim=self.dim,
            latency_ms=1.0,
        )

    def _extract_topic(self, text: str) -> int:
        """Extract topic tag <N> from the start of text, defaulting to 0."""
        if text.startswith("<") and ">" in text[:4]:
            end = text.index(">")
            tag = text[1:end]
            if tag.isdigit():
                return int(tag)
        return 0


def _topic_sentences(*topics: str) -> str:
    """Build a document string where each topic tag produces a paragraph.

    Example: _topic_sentences("<0>First paragraph.", "<1>Different topic.")
    """
    parts = []
    for t in topics:
        if "\n\n" in t:
            parts.append(t)
        else:
            parts.append(t + " Here is some more text about this topic.")
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Unit tests for sentence splitting and cosine similarity
# ---------------------------------------------------------------------------


class TestSentenceSplitting:
    def test_simple_period_splitting(self) -> None:
        text = "Sentence one. Sentence two. Sentence three."
        sentences = _split_sentences(text)
        assert len(sentences) == 3
        assert sentences[0].startswith("Sentence one")
        assert sentences[1].startswith("Sentence two")
        assert sentences[2].startswith("Sentence three")

    def test_paragraph_breaks_are_boundaries(self) -> None:
        text = "Para 1. Sentence A.\n\nPara 2. Sentence B."
        sentences = _split_sentences(text)
        assert len(sentences) >= 3  # at minimum Para1.S, A., Para2.S, B.

    def test_empty_text(self) -> None:
        assert _split_sentences("") == []
        assert _split_sentences("   \n\n   ") == []

    def test_exclamation_and_question_marks(self) -> None:
        text = "Hello! How are you? I'm fine."
        sentences = _split_sentences(text)
        assert len(sentences) == 3

    def test_chinese_punctuation(self) -> None:
        text = "第一句话。第二句话！第三句话？"
        sentences = _split_sentences(text)
        assert len(sentences) == 3


class TestCosineSimilarity:
    def test_identical_vectors(self) -> None:
        a = [1.0, 2.0, 3.0]
        b = [1.0, 2.0, 3.0]
        assert math.isclose(_cosine_similarity(a, b), 1.0, rel_tol=1e-6)

    def test_orthogonal_vectors(self) -> None:
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert _cosine_similarity(a, b) == 0.0

    def test_opposite_vectors(self) -> None:
        a = [1.0, 0.0]
        b = [-1.0, 0.0]
        assert math.isclose(_cosine_similarity(a, b), -1.0, rel_tol=1e-6)

    def test_zero_vector(self) -> None:
        assert _cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0
        assert _cosine_similarity([1.0, 2.0], [0.0, 0.0]) == 0.0

    def test_empty_vectors(self) -> None:
        assert _cosine_similarity([], []) == 0.0

    def test_unequal_length_raises(self) -> None:
        with pytest.raises(ValueError):
            _cosine_similarity([1.0], [1.0, 2.0])


# ---------------------------------------------------------------------------
# Configuration validation
# ---------------------------------------------------------------------------


class TestSemanticChunkerConfig:
    def test_defaults(self) -> None:
        config = SemanticChunkerConfig()
        assert config.min_chunk_sentences == 3
        assert config.max_chunk_sentences == 50
        assert config.semantic_threshold == pytest.approx(0.65)
        assert config.embedding_timeout_seconds == 10.0
        assert config.embedding_retry_budget == 2

    def test_rejects_negative_min_sentences(self) -> None:
        with pytest.raises(DocumentChunkError) as exc:
            SemanticChunkerConfig(min_chunk_sentences=0)
        assert exc.value.code == DOCUMENT_CHUNK_CONFIG_INVALID

    def test_rejects_max_less_than_min(self) -> None:
        with pytest.raises(DocumentChunkError) as exc:
            SemanticChunkerConfig(min_chunk_sentences=5, max_chunk_sentences=3)
        assert exc.value.code == DOCUMENT_CHUNK_CONFIG_INVALID

    @pytest.mark.parametrize("threshold", [-0.1, 1.1])
    def test_rejects_out_of_range_threshold(self, threshold: float) -> None:
        with pytest.raises(DocumentChunkError) as exc:
            SemanticChunkerConfig(semantic_threshold=threshold)
        assert exc.value.code == DOCUMENT_CHUNK_CONFIG_INVALID

    def test_rejects_non_finite_threshold(self) -> None:
        with pytest.raises(DocumentChunkError) as exc:
            SemanticChunkerConfig(semantic_threshold=float("nan"))
        assert exc.value.code == DOCUMENT_CHUNK_CONFIG_INVALID

    def test_rejects_zero_timeout(self) -> None:
        with pytest.raises(DocumentChunkError) as exc:
            SemanticChunkerConfig(embedding_timeout_seconds=0)
        assert exc.value.code == DOCUMENT_CHUNK_CONFIG_INVALID

    def test_rejects_negative_retry_budget(self) -> None:
        with pytest.raises(DocumentChunkError) as exc:
            SemanticChunkerConfig(embedding_retry_budget=-1)
        assert exc.value.code == DOCUMENT_CHUNK_CONFIG_INVALID


# ---------------------------------------------------------------------------
# Semantic chunker integration tests
# ---------------------------------------------------------------------------


class TestSemanticChunker:
    @pytest.mark.asyncio
    async def test_single_topic_produces_one_chunk(self) -> None:
        """A document where all sentences are similar should yield one chunk."""
        provider = _TopicAwareFakeProvider()
        chunker = SemanticChunker(
            SemanticChunkerConfig(semantic_threshold=0.5, min_chunk_sentences=2),
            embedding_provider=cast(EmbeddingProvider, provider),
        )
        doc = _document([
            _section("s1", _topic_sentences("<0>First.", "<0>Second.", "<0>Third."))
        ])

        chunks = await chunker.split(doc)
        assert len(chunks) == 1
        assert chunks[0].section_ids == ["s1"]
        assert chunks[0].metadata["chunker"] == "semantic"
        assert cast(int, chunks[0].metadata["sentence_count"]) >= 3

    @pytest.mark.asyncio
    async def test_topic_shift_splits_into_multiple_chunks(self) -> None:
        """Two distinct topics should produce at least two chunks."""
        provider = _TopicAwareFakeProvider()
        chunker = SemanticChunker(
            SemanticChunkerConfig(semantic_threshold=0.5, min_chunk_sentences=2),
            embedding_provider=cast(EmbeddingProvider, provider),
        )
        doc = _document([
            _section(
                "s1",
                _topic_sentences(
                    "<0>Topic A sentence one.",
                    "<0>Topic A sentence two.",
                    "<0>Topic A sentence three.",
                    "<1>Topic B sentence one.",
                    "<1>Topic B sentence two.",
                    "<1>Topic B sentence three.",
                ),
            )
        ])

        chunks = await chunker.split(doc)
        assert len(chunks) >= 2, f"Expected >=2 chunks, got {len(chunks)}"

    @pytest.mark.asyncio
    async def test_cross_section_preserves_lineage_and_pages(self) -> None:
        """Chunks spanning sections preserve page metadata and title paths."""
        provider = _TopicAwareFakeProvider()
        chunker = SemanticChunker(
            SemanticChunkerConfig(semantic_threshold=0.5, min_chunk_sentences=1),
            embedding_provider=cast(EmbeddingProvider, provider),
        )
        # Same section, but rich content covering multiple pages
        doc = _document(
            [
                _section(
                    "s1",
                    _topic_sentences(
                        "<0>Alpha section. <0>More alpha text.",
                        "<0>Beta continuation.",
                        "<0>Gamma final part.",
                    ),
                    source_type="pdf",
                    title_path=["Policy", "Scope"],
                    page_start=3,
                    page_end=5,
                ),
            ],
            source_type="pdf",
        )

        chunks = await chunker.split(doc)
        assert len(chunks) == 1  # same topic → one chunk

        merged = chunks[0]
        assert merged.tenant_id == "tenant-1"
        assert merged.document_id == "doc-1"
        assert merged.version_id == "ver-1"
        assert merged.source_type == "pdf"
        assert merged.source_uri == "kb://policy"
        assert merged.section_ids == ["s1"]
        assert merged.title_path == ["Policy", "Scope"]
        assert merged.page_start == 3
        assert merged.page_end == 5
        assert merged.acl == {"visibility": "tenant", "groups": ["hr"]}
        assert merged.metadata["title_paths"] == [["Policy", "Scope"]]
        assert merged.metadata["source_section_count"] == 1

    @pytest.mark.asyncio
    async def test_stable_chunk_ids(self) -> None:
        """Same document split twice yields identical chunk IDs."""
        provider = _TopicAwareFakeProvider()
        chunker = SemanticChunker(
            SemanticChunkerConfig(min_chunk_sentences=2),
            embedding_provider=cast(EmbeddingProvider, provider),
        )
        doc = _document([
            _section("s1", _topic_sentences("<0>First.", "<0>Second.", "<0>Third."))
        ])

        chunks1 = await chunker.split(doc)
        chunks2 = await chunker.split(doc)
        assert [c.chunk_id for c in chunks1] == [c.chunk_id for c in chunks2]

    @pytest.mark.asyncio
    async def test_documents_without_page_metadata_preserve_none(self) -> None:
        """Chunks with no page info should have None for page_start/end."""
        provider = _TopicAwareFakeProvider()
        chunker = SemanticChunker(
            SemanticChunkerConfig(min_chunk_sentences=1),
            embedding_provider=cast(EmbeddingProvider, provider),
        )
        doc = _document(
            [_section("s1", _topic_sentences("<0>Body."), source_type="docx")],
            source_type="docx",
        )

        chunks = await chunker.split(doc)
        assert all(c.page_start is None for c in chunks)
        assert all(c.page_end is None for c in chunks)

    @pytest.mark.asyncio
    async def test_different_section_acls_stay_separate(self) -> None:
        """Sections with different ACLs (and different IDs) produce separate chunks
        since section changes are hard boundaries in semantic chunking."""
        provider = _TopicAwareFakeProvider()
        chunker = SemanticChunker(
            SemanticChunkerConfig(min_chunk_sentences=1),
            embedding_provider=cast(EmbeddingProvider, provider),
        )
        doc = _document([
            _section(
                "s1",
                _topic_sentences("<0>Topic A."),
                acl={"visibility": "restricted", "groups": ["hr"]},
            ),
            _section(
                "s2",
                _topic_sentences("<0>Topic A continued."),
                acl={"visibility": "tenant"},
            ),
        ])

        chunks = await chunker.split(doc)
        # Sections with different IDs produce separate chunks.
        assert len(chunks) == 2
        assert chunks[0].acl == {"visibility": "restricted", "groups": ["hr"]}
        assert chunks[1].acl == {"visibility": "tenant"}

    @pytest.mark.asyncio
    async def test_section_source_mismatch_raises_error(self) -> None:
        """Section source type must match document source type."""
        provider = _TopicAwareFakeProvider()
        chunker = SemanticChunker(
            embedding_provider=cast(EmbeddingProvider, provider),
        )
        doc = _document([_section("s1", "Some text.", source_type="docx")])

        with pytest.raises(DocumentChunkError) as exc:
            await chunker.split(doc)
        assert exc.value.code == DOCUMENT_CHUNK_FAILED
        assert exc.value.details["reason"] == "section_boundary_mismatch"

    @pytest.mark.asyncio
    async def test_empty_document_raises_error(self) -> None:
        """A document with zero sentences should raise an error."""
        provider = _TopicAwareFakeProvider()
        chunker = SemanticChunker(
            embedding_provider=cast(EmbeddingProvider, provider),
        )
        doc = _document([_section("s1", "\u200b")])

        with pytest.raises(DocumentChunkError) as exc:
            await chunker.split(doc)
        assert exc.value.code == DOCUMENT_CHUNK_EMPTY_CONTENT

    @pytest.mark.asyncio
    async def test_max_chunk_sentences_forces_boundary(self) -> None:
        """When max_chunk_sentences is reached, a boundary is forced."""
        provider = _TopicAwareFakeProvider()
        chunker = SemanticChunker(
            SemanticChunkerConfig(
                min_chunk_sentences=1,
                max_chunk_sentences=2,
                semantic_threshold=0.99,  # very high → almost never splits
            ),
            embedding_provider=cast(EmbeddingProvider, provider),
        )
        # All same topic but max=2 forces split every 2 sentences
        doc = _document([
            _section(
                "s1",
                _topic_sentences(
                    "<0>S1.", "<0>S2.", "<0>S3.", "<0>S4.", "<0>S5."
                ),
            )
        ])

        chunks = await chunker.split(doc)
        assert len(chunks) >= 3  # 5 sentences / 2 per chunk → 3 chunks
        # Verify no chunk exceeds max
        for chunk in chunks:
            assert cast(int, chunk.metadata["sentence_count"]) <= 2

    @pytest.mark.asyncio
    async def test_chunk_checksum_is_set(self) -> None:
        """Every chunk must have a non-empty checksum."""
        provider = _TopicAwareFakeProvider()
        chunker = SemanticChunker(
            embedding_provider=cast(EmbeddingProvider, provider),
        )
        doc = _document([
            _section("s1", _topic_sentences("<0>First.", "<0>Second.", "<0>Third."))
        ])

        chunks = await chunker.split(doc)
        assert all(c.checksum for c in chunks)
        assert all(len(c.checksum) == 64 for c in chunks)  # sha256 hex

    @pytest.mark.asyncio
    async def test_token_count_positive(self) -> None:
        """Every chunk must have positive token count."""
        provider = _TopicAwareFakeProvider()
        chunker = SemanticChunker(
            embedding_provider=cast(EmbeddingProvider, provider),
        )
        doc = _document([
            _section("s1", _topic_sentences("<0>First.", "<0>Second."))
        ])

        chunks = await chunker.split(doc)
        assert all(c.token_count > 0 for c in chunks)

    @pytest.mark.asyncio
    async def test_semantic_threshold_low_produces_fewer_chunks(self) -> None:
        """A low threshold (more tolerant) should produce fewer chunks."""
        provider = _TopicAwareFakeProvider()

        high_threshold_chunker = SemanticChunker(
            SemanticChunkerConfig(
                semantic_threshold=0.99, min_chunk_sentences=1
            ),
            embedding_provider=cast(EmbeddingProvider, provider),
        )
        low_threshold_chunker = SemanticChunker(
            SemanticChunkerConfig(
                semantic_threshold=0.01, min_chunk_sentences=1
            ),
            embedding_provider=cast(EmbeddingProvider, provider),
        )

        doc = _document([
            _section(
                "s1",
                _topic_sentences(
                    "<0>A.", "<1>B.", "<2>C.", "<3>D.", "<4>E."
                ),
            )
        ])

        high_chunks = await high_threshold_chunker.split(doc)
        low_chunks = await low_threshold_chunker.split(doc)
        assert len(high_chunks) <= len(low_chunks), (
            f"High threshold (strict) should not produce more chunks than low: "
            f"{len(high_chunks)} vs {len(low_chunks)}"
        )

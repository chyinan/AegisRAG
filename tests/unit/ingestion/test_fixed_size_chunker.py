from __future__ import annotations

from typing import Any, cast

import pytest

from packages.ingestion.chunkers.fixed_size import (
    FixedSizeChunker,
    FixedSizeChunkerConfig,
    estimate_tokens,
)
from packages.ingestion.domain import Chunk, ParsedDocument, Section
from packages.ingestion.exceptions import (
    DOCUMENT_CHUNK_CONFIG_INVALID,
    DOCUMENT_CHUNK_EMPTY_CONTENT,
    DOCUMENT_CHUNK_FAILED,
    DocumentChunkError,
)
from packages.ingestion.ports import Chunker


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


def _words(count: int, prefix: str = "token") -> str:
    return " ".join(f"{prefix}{index}" for index in range(count))


def test_chunk_dto_requires_governance_lineage_and_positive_token_count() -> None:
    chunk = Chunk(
        chunk_id="chunk-1",
        tenant_id="tenant-1",
        document_id="doc-1",
        version_id="ver-1",
        source_type="markdown",
        source_uri="kb://policy",
        title_path=["Policy", "Scope"],
        content="Policy body",
        page_start=None,
        page_end=None,
        token_count=2,
        acl={"visibility": "tenant"},
        checksum="abc123",
        section_ids=["s1"],
        metadata={"title_paths": [["Policy", "Scope"]]},
    )

    assert chunk.section_ids == ["s1"]
    assert chunk.token_count == 2
    assert isinstance(Chunker, type)

    with pytest.raises(ValueError):
        Chunk(
            chunk_id="chunk-2",
            tenant_id="tenant-1",
            document_id="doc-1",
            version_id="ver-1",
            source_type="markdown",
            title_path=["Policy"],
            content="Policy body",
            token_count=0,
            checksum="abc123",
            section_ids=["s1"],
        )


def test_domain_defaults_explicit_null_acl_to_tenant_visibility() -> None:
    section = Section(
        section_id="s1",
        tenant_id="tenant-1",
        document_id="doc-1",
        version_id="ver-1",
        source_type="markdown",
        source_uri="kb://policy",
        title_path=["Policy"],
        content="Policy body",
        acl=cast(Any, None),
    )
    document = ParsedDocument(
        tenant_id="tenant-1",
        document_id="doc-1",
        version_id="ver-1",
        source_type="markdown",
        source_uri="kb://policy",
        sections=[section],
        acl=cast(Any, None),
        checksum="raw-checksum-1",
    )

    assert section.acl == {"visibility": "tenant"}
    assert document.acl == {"visibility": "tenant"}


def test_domain_rejects_duplicate_section_ids_and_invalid_page_ranges() -> None:
    with pytest.raises(ValueError):
        _document([_section("s1", "First"), _section("s1", "Second")])

    with pytest.raises(ValueError):
        _section("s2", "Body", page_start=0, page_end=1)

    with pytest.raises(ValueError):
        _section("s3", "Body", page_start=3, page_end=2)


def test_config_defaults_to_mvp_range_and_valid_overlap() -> None:
    config = FixedSizeChunkerConfig()

    assert config.min_tokens == 500
    assert config.max_tokens == 800
    assert config.overlap_ratio == pytest.approx(0.15)


@pytest.mark.parametrize("overlap_ratio", [0.09, 0.21])
def test_config_rejects_out_of_range_overlap(overlap_ratio: float) -> None:
    with pytest.raises(DocumentChunkError) as exc_info:
        FixedSizeChunkerConfig(overlap_ratio=overlap_ratio)

    assert exc_info.value.code == DOCUMENT_CHUNK_CONFIG_INVALID
    assert "content" not in exc_info.value.details


def test_config_rejects_window_that_cannot_advance_with_overlap() -> None:
    with pytest.raises(DocumentChunkError) as exc_info:
        FixedSizeChunkerConfig(min_tokens=1, max_tokens=1, overlap_ratio=0.10)

    assert exc_info.value.code == DOCUMENT_CHUNK_CONFIG_INVALID
    assert exc_info.value.details == {"reason": "overlap_must_be_smaller_than_window"}


def test_default_chunker_splits_long_document_into_target_sized_chunks_with_stable_ids() -> None:
    document = _document([_section("s1", _words(1_620))])

    chunker = FixedSizeChunker()
    chunks = chunker.split(document)
    repeat_chunks = chunker.split(document)

    assert len(chunks) == 3
    assert [chunk.chunk_id for chunk in chunks] == [chunk.chunk_id for chunk in repeat_chunks]
    assert all(chunk.token_count > 0 for chunk in chunks)
    assert all(500 <= chunk.token_count <= 800 for chunk in chunks)
    assert chunks[-1].token_count <= 800
    assert chunks[-1].content.strip()
    assert all(chunk.checksum for chunk in chunks)
    assert all(chunk.section_ids == ["s1"] for chunk in chunks)


@pytest.mark.parametrize(
    ("overlap_ratio", "expected_overlap"),
    [(0.10, 10), (0.15, 15), (0.20, 20)],
)
def test_overlap_ratio_repeats_expected_boundary_tokens(
    overlap_ratio: float,
    expected_overlap: int,
) -> None:
    document = _document([_section("s1", _words(240))])
    chunker = FixedSizeChunker(
        FixedSizeChunkerConfig(min_tokens=80, max_tokens=100, overlap_ratio=overlap_ratio)
    )

    chunks = chunker.split(document)

    first_words = chunks[0].content.split()
    second_words = chunks[1].content.split()
    assert second_words[:expected_overlap] == first_words[-expected_overlap:]
    assert chunks[1].metadata["overlap_token_count"] == expected_overlap


def test_cross_section_chunk_preserves_lineage_pages_title_summary_and_acl() -> None:
    document = _document(
        [
            _section(
                "s1",
                _words(260, "a"),
                source_type="pdf",
                title_path=["Policy", "Scope"],
                page_start=3,
                page_end=3,
            ),
            _section(
                "s2",
                _words(260, "b"),
                source_type="pdf",
                title_path=["Policy", "Eligibility"],
                page_start=4,
                page_end=5,
            ),
        ],
        source_type="pdf",
    )

    chunks = FixedSizeChunker().split(document)

    merged = chunks[0]
    assert merged.tenant_id == "tenant-1"
    assert merged.document_id == "doc-1"
    assert merged.version_id == "ver-1"
    assert merged.source_type == "pdf"
    assert merged.source_uri == "kb://policy"
    assert merged.section_ids == ["s1", "s2"]
    assert merged.title_path == ["Policy"]
    assert merged.page_start == 3
    assert merged.page_end == 5
    assert merged.acl == {"visibility": "tenant", "groups": ["hr"]}
    assert merged.metadata["title_paths"] == [
        ["Policy", "Scope"],
        ["Policy", "Eligibility"],
    ]
    assert merged.metadata["source_section_count"] == 2


def test_chunker_preserves_paragraph_boundaries_inside_chunk_content() -> None:
    document = _document([_section("s1", "First paragraph.\n\nSecond paragraph.")])
    chunker = FixedSizeChunker(
        FixedSizeChunkerConfig(min_tokens=1, max_tokens=20, overlap_ratio=0.10)
    )

    chunks = chunker.split(document)

    assert chunks[0].content == "First paragraph.\n\nSecond paragraph."


def test_documents_without_page_metadata_do_not_get_synthetic_pages() -> None:
    document = _document(
        [_section("s1", _words(120), source_type="docx", page_start=None, page_end=None)],
        source_type="docx",
    )

    chunks = FixedSizeChunker(
        FixedSizeChunkerConfig(min_tokens=50, max_tokens=80, overlap_ratio=0.10)
    ).split(document)

    assert all(chunk.page_start is None for chunk in chunks)
    assert all(chunk.page_end is None for chunk in chunks)


def test_acl_mismatch_is_not_merged_into_wider_chunk() -> None:
    document = _document(
        [
            _section("s1", _words(300), acl={"visibility": "restricted", "groups": ["hr"]}),
            _section("s2", _words(300), acl={"visibility": "tenant"}),
        ]
    )

    with pytest.raises(DocumentChunkError) as exc_info:
        FixedSizeChunker().split(document)

    assert exc_info.value.code == DOCUMENT_CHUNK_FAILED
    assert exc_info.value.details == {
        "document_id": "doc-1",
        "version_id": "ver-1",
        "section_id": "s2",
        "reason": "acl_mismatch",
    }


def test_section_source_mismatch_is_not_silently_relabelled() -> None:
    document = _document([_section("s1", _words(20), source_type="docx")])

    with pytest.raises(DocumentChunkError) as exc_info:
        FixedSizeChunker().split(document)

    assert exc_info.value.code == DOCUMENT_CHUNK_FAILED
    assert exc_info.value.details == {
        "document_id": "doc-1",
        "version_id": "ver-1",
        "section_id": "s1",
        "reason": "section_boundary_mismatch",
    }


def test_empty_cleaned_document_raises_stable_chunk_error() -> None:
    document = _document([_section("s1", "\u200b")])

    with pytest.raises(DocumentChunkError) as exc_info:
        FixedSizeChunker().split(document)

    assert exc_info.value.code == DOCUMENT_CHUNK_EMPTY_CONTENT
    assert exc_info.value.details == {
        "document_id": "doc-1",
        "version_id": "ver-1",
        "section_id": "s1",
        "reason": "empty_section_content",
    }


def test_token_estimator_failure_is_mapped_to_safe_domain_error() -> None:
    def failing_estimator(_: str) -> int:
        raise RuntimeError("tokenizer included unsafe content")

    document = _document([_section("s1", _words(20))])

    with pytest.raises(DocumentChunkError) as exc_info:
        FixedSizeChunker(token_estimator=failing_estimator).split(document)

    assert exc_info.value.code == DOCUMENT_CHUNK_FAILED
    assert exc_info.value.details == {
        "document_id": "doc-1",
        "version_id": "ver-1",
        "section_id": "s1",
        "reason": "token_estimator_failed",
    }
    assert "unsafe" not in repr(exc_info.value.details)


def test_token_estimator_non_positive_result_uses_safe_fallback_count() -> None:
    document = _document([_section("s1", "alpha beta gamma")])

    chunks = FixedSizeChunker(token_estimator=lambda _: 0).split(document)

    assert chunks[0].token_count == estimate_tokens("alpha beta gamma")
    assert chunks[0].token_count > 0


def test_token_estimator_budget_overrun_is_mapped_to_safe_domain_error() -> None:
    document = _document([_section("s1", _words(20))])

    with pytest.raises(DocumentChunkError) as exc_info:
        FixedSizeChunker(
            FixedSizeChunkerConfig(min_tokens=5, max_tokens=10, overlap_ratio=0.10),
            token_estimator=lambda _: 999,
        ).split(document)

    assert exc_info.value.code == DOCUMENT_CHUNK_FAILED
    assert exc_info.value.details == {
        "document_id": "doc-1",
        "version_id": "ver-1",
        "section_id": "s1",
        "reason": "chunk_token_budget_exceeded",
    }

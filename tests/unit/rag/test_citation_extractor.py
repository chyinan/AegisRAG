from __future__ import annotations

from packages.rag import (
    CitationExtractor,
    ContextPackingTrace,
    PackedCitationSource,
    PackedContext,
    PackedContextItem,
)


def test_extractor_returns_only_packed_context_sources_and_deduplicates() -> None:
    duplicate = _source(chunk_id="chunk-1", score=0.8)
    packed = _packed_context(
        (
            _item(
                chunk_ids=("chunk-1", "chunk-2"),
                citation_sources=(duplicate, duplicate, _source(chunk_id="chunk-2", score=0.9)),
            ),
        )
    )

    result = CitationExtractor().extract(
        answer="答案来自授权上下文。",
        packed_context=packed,
    )

    assert [citation.chunk_id for citation in result.citations] == ["chunk-2", "chunk-1"]
    assert all(citation.document_id == "doc-1" for citation in result.citations)
    assert result.trace.forged_reference_count == 0


def test_extractor_rejects_forged_references_instead_of_attaching_citations() -> None:
    result = CitationExtractor().extract(
        answer="答案来自 doc-forged chunk-forged cite-deadbeef。",
        packed_context=_packed_context((_item(),)),
    )

    assert result.citations == ()
    assert result.unsupported_claims[0].reason == "forged_or_unauthorized_reference"
    assert result.unsupported_claims[0].severity == "high"
    assert result.trace.forged_reference_count == 3


def test_extractor_treats_empty_citation_allowlist_as_no_allowed_sources() -> None:
    result = CitationExtractor().extract(
        answer="答案来自授权上下文。",
        packed_context=_packed_context((_item(),)),
        citation_source_ids=(),
    )

    assert result.citations == ()
    assert result.unsupported_claims[0].reason == "missing_authorized_citation_source"
    assert result.trace.allowed_source_count == 0


def test_extractor_preserves_missing_pages_without_inventing_values() -> None:
    packed = _packed_context(
        (
            _item(
                page_start=None,
                page_end=None,
                citation_sources=(_source(page_start=None, page_end=None),),
            ),
        )
    )

    result = CitationExtractor().extract(answer="有上下文支持。", packed_context=packed)

    assert result.citations[0].page_start is None
    assert result.citations[0].page_end is None


def test_extractor_returns_no_answer_without_citations() -> None:
    result = CitationExtractor().extract(
        answer="上下文不足，无法回答。",
        packed_context=_packed_context((_item(),)),
    )

    assert result.no_answer is True
    assert result.citations == ()
    assert result.unsupported_claims == ()


def test_extractor_reports_unsupported_when_sources_are_unavailable() -> None:
    packed = _packed_context(())

    result = CitationExtractor().extract(answer="这是无法绑定来源的结论。", packed_context=packed)

    assert result.citations == ()
    assert result.no_answer is False
    assert result.unsupported_claims[0].reason == "missing_authorized_citation_source"


def _packed_context(items: tuple[PackedContextItem, ...]) -> PackedContext:
    return PackedContext(
        items=items,
        total_tokens=sum(item.token_count for item in items),
        budget=1000,
        packing_trace=ContextPackingTrace(
            request_id="req-1",
            trace_id="trace-1",
            tenant_id="tenant-1",
            user_id="user-1",
            input_count=len(items),
            authorized_count=len(items),
            packed_count=len(items),
            dropped_count=0,
            total_tokens=sum(item.token_count for item in items),
            budget=1000,
        ),
    )


def _item(
    *,
    chunk_ids: tuple[str, ...] = ("chunk-1",),
    page_start: int | None = 1,
    page_end: int | None = 1,
    citation_sources: tuple[PackedCitationSource, ...] | None = None,
) -> PackedContextItem:
    sources = citation_sources or (
        _source(chunk_id=chunk_ids[0], page_start=page_start, page_end=page_end),
    )
    return PackedContextItem(
        content="授权正文",
        token_count=20,
        document_id="doc-1",
        version_id="v1",
        chunk_ids=chunk_ids,
        source="policy.md",
        source_uri="kb://policy.md",
        source_type="markdown",
        page_start=page_start,
        page_end=page_end,
        title_path=("Policy",),
        score=0.8,
        retrieval_method="hybrid",
        citation_sources=sources,
    )


def _source(
    *,
    chunk_id: str = "chunk-1",
    score: float = 0.8,
    page_start: int | None = 1,
    page_end: int | None = 1,
) -> PackedCitationSource:
    return PackedCitationSource(
        document_id="doc-1",
        version_id="v1",
        chunk_id=chunk_id,
        source="policy.md",
        source_uri="kb://policy.md",
        source_type="markdown",
        page_start=page_start,
        page_end=page_end,
        title_path=("Policy",),
        score=score,
        retrieval_method="hybrid",
        token_count=20,
        inclusion_reason="retrieval_candidate",
    )

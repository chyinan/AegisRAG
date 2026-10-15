from __future__ import annotations

import re

import pytest

from packages.rag.citation_extractor import CitationExtractor
from packages.rag.citation_identity import citation_id
from packages.rag.dto import Citation, PromptBuildRequest
from packages.rag.prompt_builder import PromptBuilder
from tests.unit.rag.test_citation_extractor import _item, _packed_context, _source


def test_citation_id_preserves_existing_marker_contract() -> None:
    source = _source()
    assert citation_id(source) == "cite-0c3d35bf88550bb2"
    assert Citation.from_source(source).model_dump(mode="json")["citation_id"] == citation_id(
        source
    )


@pytest.mark.parametrize("field", ["document_id", "version_id", "chunk_id"])
def test_citation_identity_includes_document_version_and_chunk(field: str) -> None:
    original = _source()
    modified = original.model_copy(update={field: f"{getattr(original, field)}-next"})
    assert citation_id(original) != citation_id(modified)
    assert Citation.from_source(modified).citation_id == citation_id(modified)


def test_citation_identity_is_derived_not_trusted_from_serialized_input() -> None:
    citation = Citation.from_source(_source())
    stored = {**citation.model_dump(), "citation_id": "cite-ffffffffffffffff"}
    restored = Citation.model_validate(stored)
    assert restored.citation_id == citation.citation_id
    assert restored.model_dump(mode="json")["citation_id"] == citation.citation_id
    assert "citation_id" in Citation.model_json_schema(mode="serialization")["required"]


def test_prompt_extractor_and_serialized_ids_agree_with_reverse_and_repeated_mentions() -> None:
    sources = (_source(chunk_id="chunk-high", score=0.9), _source(chunk_id="chunk-low", score=0.8))
    packed = _packed_context(
        (_item(chunk_ids=tuple(source.chunk_id for source in sources), citation_sources=sources),)
    )
    prompt = PromptBuilder().build(
        PromptBuildRequest(
            request_id="req-1",
            trace_id="trace-1",
            tenant_id="tenant-1",
            user_id="user-1",
            query="Which source supports each claim?",
            packed_context=packed,
        )
    )
    assert prompt.citation_source_ids == ("cite-47738d687d0fab02", "cite-56b3f2c255b9075f")
    context = next(message.content for message in prompt.messages if message.name == "context")
    assert set(re.findall(r'<citation_source id="([^"]+)"', context)) == set(
        prompt.citation_source_ids
    )
    high_id, low_id = prompt.citation_source_ids
    answer = f"B [{low_id}] then A [{high_id}] and B again [{low_id}]."
    result = CitationExtractor().extract(
        answer=answer, packed_context=packed, citation_source_ids=prompt.citation_source_ids
    )
    assert result.answer == answer
    assert result.unsupported_claims == ()
    assert [citation.chunk_id for citation in result.citations] == ["chunk-high", "chunk-low"]
    assert [citation.model_dump(mode="json")["citation_id"] for citation in result.citations] == [
        high_id,
        low_id,
    ]


def test_unmentioned_sources_do_not_change_the_cited_source_identity() -> None:
    high = _source(chunk_id="chunk-high", score=0.9)
    low = _source(chunk_id="chunk-low", score=0.8)
    packed = _packed_context(
        (_item(chunk_ids=(high.chunk_id, low.chunk_id), citation_sources=(high, low)),)
    )
    result = CitationExtractor().extract(
        answer=f"Only B [{citation_id(low)}].", packed_context=packed
    )
    assert result.citations[0].citation_id == citation_id(high)
    assert result.citations[1].citation_id == citation_id(low)


def test_unknown_marker_never_attaches_authorized_sources() -> None:
    result = CitationExtractor().extract(
        answer="Unsupported [cite-ffffffffffffffff].", packed_context=_packed_context((_item(),))
    )
    assert result.citations == ()
    assert result.trace.forged_reference_count == 1


def test_identity_encoding_is_unambiguous_for_unicode_and_delimiter_characters() -> None:
    first = _source().model_copy(update={"document_id": "文档|v1", "version_id": "v2"})
    second = _source().model_copy(update={"document_id": "文档", "version_id": "v1|v2"})
    assert citation_id(first) != citation_id(second)
    assert Citation.from_source(first).citation_id == citation_id(first)

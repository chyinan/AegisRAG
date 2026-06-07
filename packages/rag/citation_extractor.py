from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Sequence

from pydantic import BaseModel, ConfigDict

from packages.rag.dto import (
    Citation,
    CitationExtractionResult,
    CitationExtractionTrace,
    PackedCitationSource,
    PackedContext,
    UnsupportedClaim,
)

_REFERENCE_PATTERN = re.compile(
    r"\b(?:cite-[A-Za-z0-9_-]+|(?:doc|document|chunk|source)[-_]?[A-Za-z0-9._:-]+)\b",
    re.I,
)


class CitationExtractionConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    default_no_answer_text: str = "无法从给定上下文确认。"
    max_citations: int = 20


class CitationExtractor:
    def extract(
        self,
        *,
        answer: str,
        packed_context: PackedContext,
        citation_source_ids: Sequence[str] | None = None,
        config: CitationExtractionConfig | None = None,
    ) -> CitationExtractionResult:
        extraction_config = config or CitationExtractionConfig()
        no_answer = _is_no_answer(answer, extraction_config.default_no_answer_text)
        sources = _collect_sources(packed_context)
        if citation_source_ids is None:
            allowed_ids = {_citation_id(source) for source in sources}
        else:
            allowed_ids = set(citation_source_ids)
        allowed_sources = tuple(source for source in sources if _citation_id(source) in allowed_ids)
        forged_reference_count = _forged_reference_count(answer, allowed_sources)

        if no_answer:
            citations: tuple[Citation, ...] = ()
            unsupported_claims: tuple[UnsupportedClaim, ...] = ()
        elif forged_reference_count > 0:
            citations = ()
            unsupported_claims = (
                UnsupportedClaim(
                    reason="forged_or_unauthorized_reference",
                    summary=(
                        "Generated answer included source references that were not in the "
                        "authorized citation allowlist."
                    ),
                    severity="high",
                ),
            )
        else:
            citations = tuple(
                Citation.from_source(source)
                for source in sorted(
                    allowed_sources,
                    key=lambda source: (
                        -source.score,
                        source.document_id,
                        source.version_id,
                        source.chunk_id,
                    ),
                )[: extraction_config.max_citations]
            )
            unsupported_claims = _unsupported_claims(answer=answer, citations=citations)

        trace = packed_context.packing_trace
        extraction_trace = CitationExtractionTrace(
            request_id=trace.request_id,
            trace_id=trace.trace_id,
            tenant_id=trace.tenant_id,
            user_id=trace.user_id,
            input_source_count=len(sources),
            allowed_source_count=len(allowed_sources),
            citation_count=len(citations),
            unsupported_count=len(unsupported_claims),
            forged_reference_count=forged_reference_count,
            no_answer=no_answer,
            safe_counts={
                "context_item_count": len(packed_context.items),
                "input_source_count": len(sources),
                "allowed_source_count": len(allowed_sources),
                "citation_count": len(citations),
                "unsupported_count": len(unsupported_claims),
                "forged_reference_count": forged_reference_count,
            },
        )
        return CitationExtractionResult(
            answer=answer if answer.strip() else extraction_config.default_no_answer_text,
            citations=citations,
            unsupported_claims=unsupported_claims,
            no_answer=no_answer,
            trace=extraction_trace,
        )


def _collect_sources(packed_context: PackedContext) -> tuple[PackedCitationSource, ...]:
    seen: set[tuple[str, str, str]] = set()
    sources: list[PackedCitationSource] = []
    for item in packed_context.items:
        for source in item.citation_sources:
            identity = (source.document_id, source.version_id, source.chunk_id)
            if identity in seen:
                continue
            seen.add(identity)
            sources.append(source)
    return tuple(sources)


def _is_no_answer(answer: str, default_no_answer_text: str) -> bool:
    normalized = _normalize_answer(answer)
    no_answer_variants = {
        _normalize_answer(default_no_answer_text),
        _normalize_answer("无法根据上下文确认。"),
        _normalize_answer("上下文不足，无法回答。"),
        _normalize_answer("The provided context is insufficient to answer."),
        _normalize_answer("I cannot answer from the provided context."),
    }
    return not normalized or normalized in no_answer_variants


def _normalize_answer(value: str) -> str:
    return re.sub(r"[\s。.!！]+", "", value.strip()).lower()


def _unsupported_claims(
    *,
    answer: str,
    citations: Sequence[Citation],
) -> tuple[UnsupportedClaim, ...]:
    if citations:
        return ()
    if not answer.strip():
        return ()
    return (
        UnsupportedClaim(
            reason="missing_authorized_citation_source",
            summary="Generated answer could not be bound to an authorized citation source.",
        ),
    )


def _forged_reference_count(answer: str, allowed_sources: Iterable[PackedCitationSource]) -> int:
    allowed_tokens: set[str] = set()
    for source in allowed_sources:
        allowed_tokens.update(
            item
            for item in (
                source.document_id,
                source.version_id,
                source.chunk_id,
                source.source,
                source.source_uri,
            )
            if item
        )
    forged = []
    for match in _REFERENCE_PATTERN.findall(answer):
        if match not in allowed_tokens:
            forged.append(match)
    return len(forged)


def _citation_id(source: PackedCitationSource) -> str:
    encoded = json.dumps(
        (source.document_id, source.version_id, source.chunk_id),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
    return f"cite-{digest}"

"""Generate frontend SSE contract fixtures using the real backend serializers.

Run from the repository root. --check verifies committed fixtures without writes.
No provider, database, network, or application startup is involved.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TypedDict

from packages.rag.citation_extractor import CitationExtractor
from packages.rag.dto import (
    CitationExtractionResult,
    ContextPackingTrace,
    PackedCitationSource,
    PackedContext,
    PackedContextItem,
    PromptBuildRequest,
)
from packages.rag.prompt_builder import PromptBuilder
from packages.rag.streaming import (
    citation_event,
    final_event,
    format_sse_event,
    safe_error_event,
    token_event,
    tool_call_event,
    tool_result_event,
)

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "apps/web/src/test/fixtures/chat-stream.json"


class _StreamContext(TypedDict):
    request_id: str
    trace_id: str


class _RequestIdentity(_StreamContext):
    tenant_id: str
    user_id: str


def _source(chunk_id: str = "chunk-pilot", score: float = 0.95) -> PackedCitationSource:
    return PackedCitationSource(
        document_id="doc-pilot",
        version_id="version-pilot",
        chunk_id=chunk_id,
        source="Pilot handbook.md",
        source_type="markdown",
        title_path=("Remote work",),
        retrieval_method="hybrid",
        score=score,
        token_count=10,
        inclusion_reason="retrieval_candidate",
    )


def _cited_answer(
    sources: tuple[PackedCitationSource, ...], mentions: tuple[tuple[int, str], ...]
) -> CitationExtractionResult:
    """Use the production prompt IDs and extractor ordering, not frontend-made mocks."""
    identity: _RequestIdentity = {
        "request_id": "req-stream-fixture",
        "trace_id": "trace-stream-fixture",
        "tenant_id": "tenant-pilot",
        "user_id": "user-pilot",
    }
    packed = PackedContext(
        items=(
            PackedContextItem(
                document_id="doc-pilot",
                version_id="version-pilot",
                chunk_ids=tuple(source.chunk_id for source in sources),
                content="Synthetic fixture: remote work requires approval and is supported.",
                token_count=30,
                source_type="markdown",
                title_path=("Remote work",),
                retrieval_method="hybrid",
                score=0.95,
                citation_sources=sources,
            ),
        ),
        total_tokens=30,
        budget=1000,
        packing_trace=ContextPackingTrace(
            **identity,
            input_count=1,
            authorized_count=1,
            packed_count=1,
            dropped_count=0,
            total_tokens=30,
            budget=1000,
        ),
    )
    prompt = PromptBuilder().build(
        PromptBuildRequest(**identity, query="How does remote work operate?", packed_context=packed)
    )
    answer = " ".join(
        f"{sentence} [{prompt.citation_source_ids[index]}]." for index, sentence in mentions
    )
    return CitationExtractor().extract(
        answer=answer, packed_context=packed, citation_source_ids=prompt.citation_source_ids
    )


def stream_fixtures() -> dict[str, str]:
    context: _StreamContext = {
        "request_id": "req-stream-fixture",
        "trace_id": "trace-stream-fixture",
    }
    result = _cited_answer((_source(),), ((0, "Working remotely is supported"),))
    citation = result.citations[0]
    reversed_result = _cited_answer(
        (_source(), _source("chunk-approval", 0.8), _source("chunk-unused", 0.9)),
        ((1, "Approval is required"), (0, "Remote work is supported"), (1, "Ask for approval")),
    )
    tokens = "".join(
        format_sse_event(token_event(**context, index=index, delta=delta))
        for index, delta in enumerate(["Working ", "remotely 🌍 is supported."])
    )
    cited = format_sse_event(citation_event(**context, citation=citation))
    final = format_sse_event(
        final_event(
            **context,
            tenant_id="tenant-pilot",
            user_id="user-pilot",
            session_id="session-fixture",
            answer=result.answer,
            citations=result.citations,
            no_answer=False,
            unsupported_claims=(),
            metadata={},
        )
    )
    no_answer = format_sse_event(
        final_event(
            **context,
            tenant_id="tenant-pilot",
            user_id="user-pilot",
            session_id="session-fixture",
            answer="Cannot confirm from the authorized sources.",
            citations=(),
            no_answer=True,
            unsupported_claims=(),
            metadata={},
        )
    )
    failed_final = format_sse_event(
        final_event(
            **context,
            tenant_id="tenant-pilot",
            user_id="user-pilot",
            session_id="session-fixture",
            answer="Cannot confirm from the authorized sources.",
            citations=(),
            no_answer=True,
            unsupported_claims=(),
            metadata={"error_code": "RAG_QUERY_FAILED"},
            status="error",
        )
    )
    error = format_sse_event(
        safe_error_event(
            **context,
            code="RAG_QUERY_FAILED",
            message="Chat query could not be completed. Please try again.",
            details={"stage": "generation"},
        )
    )
    tools = format_sse_event(
        tool_call_event(
            **context,
            tool_call_id="tool-call-fixture",
            tool_name="rag_search",
            metadata={"agent_run_id": "run-fixture"},
        )
    ) + format_sse_event(
        tool_result_event(
            **context,
            tool_call_id="tool-call-fixture",
            tool_name="rag_search",
            status="success",
            metadata={"agent_run_id": "run-fixture", "latency_ms": 12},
        )
    )
    return {
        "success": tokens + cited + cited + final,
        "noAnswer": no_answer,
        "error": tokens + error + failed_final,
        "failedFinal": failed_final,
        "tokensOnly": tokens,
        "tools": tools + final,
        "reversedCitations": format_sse_event(
            final_event(
                **context,
                tenant_id="tenant-pilot",
                user_id="user-pilot",
                session_id="session-fixture",
                answer=reversed_result.answer,
                citations=reversed_result.citations,
                no_answer=False,
                unsupported_claims=(),
                metadata={},
            )
        ),
    }


def serialized_fixtures() -> str:
    return json.dumps(stream_fixtures(), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = serialized_fixtures()
    if args.check:
        if not FIXTURE_PATH.exists() or FIXTURE_PATH.read_text(encoding="utf-8") != expected:
            parser.exit(
                1, "Frontend stream fixtures differ from backend serialization. Regenerate them.\n"
            )
        return
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(expected, encoding="utf-8")


if __name__ == "__main__":
    main()

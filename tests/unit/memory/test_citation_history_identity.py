from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from packages.common.audit import InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.memory import ChatMemoryConfig, ChatMemoryService
from packages.memory.service import _safe_citation_summaries
from packages.rag import ChatApplicationService, PromptMemoryContext, QueryCommand, QueryResponse
from packages.rag.chat import _citations_from_message_metadata
from packages.rag.dto import Citation
from packages.rag.streaming import RagStreamEvent, final_event
from tests.unit.memory.test_chat_application_service import (
    FakeChatMemoryRepository,
    FakeRagQueryService,
    _context,
    _query_response,
)
from tests.unit.rag.test_citation_extractor import _source


class CitedQueryService(FakeRagQueryService):
    async def query(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        memory_context: PromptMemoryContext | None = None,
    ) -> QueryResponse:
        high = Citation.from_source(_source(chunk_id="chunk-high", score=0.9))
        low = Citation.from_source(_source(chunk_id="chunk-low", score=0.8))
        return _query_response(context=context, metadata={}).model_copy(
            update={
                "answer": f"B [{low.citation_id}] then A [{high.citation_id}].",
                "citations": (high, low),
            }
        )

    async def stream_query(
        self,
        *,
        context: AuthenticatedRequestContext,
        command: QueryCommand,
        memory_context: PromptMemoryContext | None = None,
    ) -> AsyncIterator[RagStreamEvent]:
        result = await self.query(context=context, command=command, memory_context=memory_context)
        yield final_event(
            request_id=context.request_id,
            trace_id=context.trace_id,
            tenant_id=context.auth.tenant_id,
            user_id=context.auth.user_id,
            answer=result.answer,
            citations=result.citations,
            no_answer=False,
            unsupported_claims=(),
            metadata={},
        )


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.asyncio
async def test_chat_persistence_reconstructs_identical_ids_without_a_history_migration(
    streaming: bool,
) -> None:
    repository = FakeChatMemoryRepository()
    memory = ChatMemoryService(repository=repository, config=ChatMemoryConfig())
    query = CitedQueryService()
    service = ChatApplicationService(
        memory_service=memory, rag_query_service=query, audit=InMemoryAuditPort()
    )
    if streaming:
        events = [
            event
            async for event in service.stream_chat(
                context=_context(), command=QueryCommand(query="Question"), session_id=None
            )
        ]
        assert events[-1].event == "final"
        assert events[-1].payload.model_dump()["status"] == "success"
    else:
        await service.chat(
            context=_context(), command=QueryCommand(query="Question"), session_id=None
        )
    stored = repository.created_messages[-1].metadata
    # Canonical IDs are recomputed; only safe public source metadata is stored.
    assert "citation_id" not in str(stored)
    history = await service.history(context=_context(), session_id="session-1")
    assistant = history.model_dump(mode="json")["messages"][-1]
    assert assistant["content"] == "B [cite-56b3f2c255b9075f] then A [cite-47738d687d0fab02]."
    assert [citation["citation_id"] for citation in assistant["citations"]] == [
        "cite-47738d687d0fab02",
        "cite-56b3f2c255b9075f",
    ]
    assert [citation["chunk_id"] for citation in assistant["citations"]] == [
        "chunk-high",
        "chunk-low",
    ]
    expected = await query.query(context=_context(), command=QueryCommand(query="Question"))
    assert assistant["citations"] == [
        citation.model_dump(mode="json") for citation in expected.citations
    ]


def test_legacy_history_with_mismatched_marker_metadata_reconstructs_from_source_identity() -> None:
    restored = _citations_from_message_metadata(
        {
            "citations": [
                {
                    "document_id": "doc-1",
                    "version_id": "v1",
                    "chunk_id": "chunk-1",
                    "citation_id": "cite-ffffffffffffffff",
                    "retrieval_method": "hybrid",
                    "score": 0.8,
                }
            ]
        }
    )
    assert restored[0].model_dump(mode="json")["citation_id"] == "cite-0c3d35bf88550bb2"


def test_legacy_sparse_citation_metadata_stays_sparse_and_drops_untrusted_ids() -> None:
    identity = {"document_id": "doc-1", "version_id": "v1", "chunk_id": "chunk-1"}
    stored = _safe_citation_summaries([{**identity, "citation_id": "cite-forged"}])
    assert stored == [identity]
    restored = _citations_from_message_metadata({"citations": stored})
    assert restored[0].source_type == "unknown"
    assert restored[0].citation_id == "cite-0c3d35bf88550bb2"


@pytest.mark.asyncio
async def test_memory_retains_only_sanitized_bounded_public_citation_fields() -> None:
    repository = FakeChatMemoryRepository()
    memory = ChatMemoryService(repository=repository)
    session = await memory.get_or_create_session(
        context=_context(), session_id=None, query="Question"
    )
    await memory.append_assistant_message(
        context=_context(),
        session_id=session.id,
        content="Synthetic response",
        citations_metadata={
            "citations": [
                {
                    "document_id": "doc-1",
                    "version_id": "v1",
                    "chunk_id": "chunk-1",
                    "source_display_name": "s3://private-bucket/private.pdf",
                    "source_type": "password=private-value",
                    "title_path": ["/home/private/secret.txt", "api_key=private-key", "Safe title"],
                    "page_start": True,
                    "page_end": 2,
                    "source_uri": "s3://private-bucket/private.pdf",
                    "source_ref": "private-reference",
                    "object_key": "private-object-key",
                    "provider_text": "private-provider-text",
                    "citation_id": "cite-forged",
                },
                {
                    "document_id": "doc-2",
                    "version_id": "v2",
                    "chunk_id": "chunk-2",
                    "source_display_name": "x" * 500,
                    "source_type": "markdown",
                    "title_path": ["y" * 500] * 100,
                    "page_start": 2,
                    "page_end": 4,
                },
            ],
        },
    )
    stored = repository.created_messages[-1].metadata["citations"]
    assert isinstance(stored, list)
    unsafe, bounded = stored
    assert unsafe["source_display_name"] == "Source unavailable"
    assert unsafe["source_type"] == "unknown"
    assert unsafe["title_path"] == ["Safe title"]
    assert "page_start" not in unsafe and "page_end" not in unsafe
    for forbidden in ("source_uri", "source_ref", "object_key", "provider_text", "citation_id"):
        assert forbidden not in unsafe
    assert "private" not in str(stored)
    # The shared source sanitizer includes a three-character truncation marker.
    assert len(bounded["source_display_name"]) <= 162
    assert 1 <= len(bounded["title_path"]) <= 16
    assert all(len(part) <= 162 for part in bounded["title_path"])
    assert (bounded["page_start"], bounded["page_end"]) == (2, 4)


def test_public_citation_title_depth_is_bounded_without_changing_safe_labels() -> None:
    stored = _safe_citation_summaries(
        [
            {
                "document_id": "doc",
                "version_id": "version",
                "chunk_id": "chunk",
                "title_path": [f"Safe heading {index}" for index in range(40)],
            }
        ]
    )
    assert stored[0]["title_path"] == [f"Safe heading {index}" for index in range(16)]

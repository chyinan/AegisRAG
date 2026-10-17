from __future__ import annotations

import hashlib

from packages.common.audit import InMemoryAuditPort
from packages.rag.dto import QueryCommand
from tests.unit.rag.test_query_service import FakeRetrievalService, _context, _service


async def test_capture_uses_the_same_packed_context_and_one_generation() -> None:
    audit = InMemoryAuditPort()
    service = _service(audit=audit)
    captured = await service.query_with_capture(
        context=_context(),
        command=QueryCommand(query="ordinary policy question"),
    )
    retrieval = service._retrieval_service
    assert isinstance(retrieval, FakeRetrievalService)
    assert len(retrieval.requests) == 1
    assert len(audit.events) == 1
    assert captured.contexts[0].content == "授权正文"
    assert captured.contexts[0].chunk_ids == ("chunk-1",)
    assert captured.contexts[0].sha256 == hashlib.sha256("授权正文".encode()).hexdigest()
    assert captured.prompt_sha256 is not None and len(captured.prompt_sha256) == 64
    assert captured.ragas_result() == (captured.response.answer, ("授权正文",))
    assert "授权正文" not in str(captured.response.model_dump())
    assert "授权正文" not in str(audit.events)


async def test_no_answer_capture_does_not_invoke_generation() -> None:
    service = _service(retrieval_candidates=())
    captured = await service.query_with_capture(
        context=_context(),
        command=QueryCommand(query="ordinary unanswered question"),
    )
    assert captured.response.no_answer
    assert captured.contexts == () and captured.prompt_sha256 is None

"""A final error payload must stay a failed audit even without an exception."""

from __future__ import annotations

from collections.abc import AsyncIterator

from packages.common.audit import AuditStatus, InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.rag.dto import QueryCommand
from packages.rag.service_token import (
    OpenAIChatCompletionRequest,
    OpenAIChatMessage,
    ServiceTokenChatAdapter,
)
from packages.rag.streaming import FinalEventPayload, RagStreamEvent
from tests.unit.rag.test_service_token_adapter import StubChatService, _context


async def test_final_error_payload_records_failed_audit_and_safe_error_code() -> None:
    closed: list[bool] = []

    class FinalErrorService(StubChatService):
        async def stream_chat(
            self,
            *,
            context: AuthenticatedRequestContext,
            command: QueryCommand,
            session_id: str | None,
        ) -> AsyncIterator[RagStreamEvent]:
            try:
                yield RagStreamEvent(
                    event="final",
                    payload=FinalEventPayload(
                        request_id=context.request_id,
                        trace_id=context.trace_id,
                        tenant_id=context.auth.tenant_id,
                        user_id=context.auth.user_id,
                        session_id="session-error",
                        answer="",
                        citations=(),
                        unsupported_claims=(),
                        no_answer=True,
                        status="error",
                        metadata={"error_code": "RAG_GENERATION_FAILED"},
                    ),
                )
            finally:
                closed.append(True)

    audit = InMemoryAuditPort()
    adapter = ServiceTokenChatAdapter(
        chat_service=FinalErrorService(),
        model_id="fake",
        owned_by="local",
        audit=audit,
    )
    frames = [
        frame
        async for frame in adapter.stream_chat_completion(
            context=_context(),
            request=OpenAIChatCompletionRequest(
                model="fake",
                messages=(OpenAIChatMessage(role="user", content="question"),),
                stream=True,
            ),
        )
    ]
    assert closed == [True]
    assert frames[-1] == "data: [DONE]\n\n"
    assert len(audit.events) == 1
    assert audit.events[0].status == AuditStatus.FAILURE
    assert audit.events[0].error_code == "RAG_GENERATION_FAILED"

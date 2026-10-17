"""Explicit close and real ASGI send/disconnect propagate across every wrapper."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from starlette.requests import ClientDisconnect
from starlette.responses import StreamingResponse

from apps.api.routes.chat import chat_stream
from apps.api.routes.query import query_stream
from apps.api.routes.service_token import chat_completions
from packages.common.audit import InMemoryAuditPort
from packages.llm.adapters.fake import FakeLLMProvider
from packages.llm.dto import GenerateChunk, GenerateRequest
from packages.memory import ChatMemoryService
from packages.rag import ChatApplicationService, ChatRequestBody, QueryRequestBody
from packages.rag.service_token import (
    OpenAIChatCompletionRequest,
    OpenAIChatMessage,
    ServiceTokenChatAdapter,
)
from tests.unit.memory.test_chat_application_service import FakeChatMemoryRepository, _context
from tests.unit.rag.test_query_service import _candidate, _chunk, _service


class PausedStream(FakeLLMProvider):
    def __init__(self) -> None:
        super().__init__(response_text="Policy answer.")
        self.started, self.closed = asyncio.Event(), asyncio.Event()

    async def stream(self, request: GenerateRequest) -> AsyncIterator[GenerateChunk]:
        self.started.set()
        try:
            async for chunk in super().stream(request):
                yield chunk
                await asyncio.Event().wait()
        finally:
            self.closed.set()


async def _response(
    surface: str,
) -> tuple[StreamingResponse, PausedStream, FakeChatMemoryRepository]:
    provider = PausedStream()
    query = _service(
        provider=provider,
        retrieval_candidates=(_candidate().model_copy(update={"tenant_id": "tenant-a"}),),
        chunk=_chunk(tenant_id="tenant-a"),
    )
    repository = FakeChatMemoryRepository()
    chat = ChatApplicationService(
        memory_service=ChatMemoryService(repository=repository), rag_query_service=query,
        audit=InMemoryAuditPort(),
    )
    if surface == "query":
        response = await query_stream(
            context=_context(), service=query, body=QueryRequestBody(query="Policy"),
        )
    elif surface == "chat":
        response = await chat_stream(
            context=_context(), service=chat, body=ChatRequestBody(query="Policy"),
        )
    else:
        response = await chat_completions(
            context=_context(), adapter=ServiceTokenChatAdapter(
                chat_service=chat, model_id="fake", owned_by="local", audit=InMemoryAuditPort(),
            ), body=OpenAIChatCompletionRequest(
                model="fake", messages=(OpenAIChatMessage(role="user", content="Policy"),),
                stream=True,
            ),
        )
    assert isinstance(response, StreamingResponse)
    return response, provider, repository


@pytest.mark.parametrize("surface", ["query", "chat", "service_token"])
async def test_outer_body_iterator_close_immediately_closes_provider(surface: str) -> None:
    response, provider, repository = await _response(surface)
    for _ in range(5):
        await anext(response.body_iterator)
        if provider.started.is_set():
            break
    assert provider.started.is_set() and not provider.closed.is_set()
    await response.body_iterator.aclose()
    assert provider.closed.is_set()
    assert [m.role for m in repository.created_messages] == ([] if surface == "query" else ["user"])


@pytest.mark.parametrize("surface", ["query", "chat", "service_token"])
@pytest.mark.parametrize("spec", ["2.3", "2.4"])
async def test_asgi_disconnect_closes_suspended_body_and_provider(surface: str, spec: str) -> None:
    response, provider, repository = await _response(surface)
    token_sent = asyncio.Event()

    async def receive() -> dict[str, str]:
        await token_sent.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.body" and provider.started.is_set():
            token_sent.set()
            if spec == "2.4":
                raise OSError("synthetic broken transport")
            await asyncio.sleep(0)

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": spec}}
    if spec == "2.4":
        with pytest.raises(ClientDisconnect):
            await asyncio.wait_for(response(scope, receive, send), 2)
    else:
        await asyncio.wait_for(response(scope, receive, send), 2)
    assert provider.started.is_set() and provider.closed.is_set()
    assert [m.role for m in repository.created_messages] == ([] if surface == "query" else ["user"])

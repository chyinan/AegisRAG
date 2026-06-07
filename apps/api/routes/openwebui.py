from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter
from starlette.responses import StreamingResponse

from apps.api.routes.query import RagQueryContextDep
from apps.api.service_dependencies import OpenWebUIChatAdapterDep
from packages.rag.openwebui import (
    OpenAIChatCompletionRequest,
    OpenAIChatCompletionResponse,
    OpenAIModelListResponse,
)

router = APIRouter(tags=["openwebui"])


@router.get("/v1/models", response_model=OpenAIModelListResponse)
async def models(
    context: RagQueryContextDep,
    adapter: OpenWebUIChatAdapterDep,
) -> OpenAIModelListResponse:
    _ = context
    return adapter.list_models()


@router.post("/v1/chat/completions", response_model=OpenAIChatCompletionResponse)
async def chat_completions(
    context: RagQueryContextDep,
    adapter: OpenWebUIChatAdapterDep,
    body: OpenAIChatCompletionRequest,
) -> OpenAIChatCompletionResponse | StreamingResponse:
    if not body.stream:
        return await adapter.chat_completion(context=context, request=body)

    async def event_frames() -> AsyncIterator[str]:
        async for frame in adapter.stream_chat_completion(context=context, request=body):
            yield frame

    return StreamingResponse(
        event_frames(),
        media_type="text/event-stream",
        headers={
            "X-Request-ID": context.request_id,
            "Cache-Control": "no-cache",
        },
    )

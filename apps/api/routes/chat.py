from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter
from starlette.responses import StreamingResponse

from apps.api.routes.query import RagQueryContextDep
from apps.api.service_dependencies import ChatApplicationServiceDep
from packages.common.envelope import ApiResponse, success_response
from packages.rag import ChatRequestBody, ChatResponse
from packages.rag.streaming import format_sse_event

router = APIRouter(tags=["rag"])


@router.post("/chat", response_model=ApiResponse[ChatResponse])
async def chat(
    context: RagQueryContextDep,
    service: ChatApplicationServiceDep,
    body: ChatRequestBody,
) -> ApiResponse[ChatResponse]:
    result = await service.chat(
        context=context,
        command=body.to_command(),
        session_id=body.session_id,
    )
    return success_response(request_id=context.request_id, data=result)


@router.post("/chat/stream")
async def chat_stream(
    context: RagQueryContextDep,
    service: ChatApplicationServiceDep,
    body: ChatRequestBody,
) -> StreamingResponse:
    async def event_frames() -> AsyncIterator[str]:
        async for event in service.stream_chat(
            context=context,
            command=body.to_command(),
            session_id=body.session_id,
        ):
            yield format_sse_event(event)

    return StreamingResponse(
        event_frames(),
        media_type="text/event-stream",
        headers={
            "X-Request-ID": context.request_id,
            "Cache-Control": "no-cache",
        },
    )

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Query
from starlette.responses import StreamingResponse

from apps.api.routes.query import RagQueryContextDep
from apps.api.service_dependencies import ChatApplicationServiceDep
from packages.common.envelope import ApiResponse, success_response
from packages.rag import ChatHistoryResponse, ChatRequestBody, ChatResponse
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


@router.get("/chat/history", response_model=ApiResponse[ChatHistoryResponse])
async def chat_history(
    context: RagQueryContextDep,
    service: ChatApplicationServiceDep,
    session_id: str = Query(min_length=1),
    limit: int = Query(default=50, ge=1, le=100),
) -> ApiResponse[ChatHistoryResponse]:
    result = await service.history(context=context, session_id=session_id, limit=limit)
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

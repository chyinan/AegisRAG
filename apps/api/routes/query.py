from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse

from apps.api.dependencies import AuthenticatedRequestContextDep
from apps.api.service_dependencies import RagQueryApplicationServiceDep
from packages.auth.policies import has_rag_query_permission
from packages.common.context import AuthenticatedRequestContext
from packages.common.envelope import ApiResponse, success_response
from packages.rag import RAG_QUERY_FORBIDDEN, QueryRequestBody, QueryResponse, RagQueryError
from packages.rag.streaming import format_sse_event

router = APIRouter(tags=["rag"])


def get_rag_query_context(context: AuthenticatedRequestContextDep) -> AuthenticatedRequestContext:
    if has_rag_query_permission(context.auth):
        return context
    raise RagQueryError(
        code=RAG_QUERY_FORBIDDEN,
        message="RAG query permission is required.",
        details={
            "request_id": context.request_id,
            "trace_id": context.trace_id,
            "tenant_id": context.auth.tenant_id,
            "user_id": context.auth.user_id,
            "required_permissions": ["document:read", "retrieval:query"],
            "error_code": RAG_QUERY_FORBIDDEN,
        },
        status_code=403,
    )


RagQueryContextDep = Annotated[AuthenticatedRequestContext, Depends(get_rag_query_context)]


@router.post("/query", response_model=ApiResponse[QueryResponse])
async def query(
    context: RagQueryContextDep,
    service: RagQueryApplicationServiceDep,
    body: QueryRequestBody,
) -> ApiResponse[QueryResponse]:
    result = await service.query(context=context, command=body.to_command())
    return success_response(request_id=context.request_id, data=result)


@router.post("/query/stream")
async def query_stream(
    context: RagQueryContextDep,
    service: RagQueryApplicationServiceDep,
    body: QueryRequestBody,
) -> StreamingResponse:
    async def event_frames() -> AsyncIterator[str]:
        async for event in service.stream_query(context=context, command=body.to_command()):
            yield format_sse_event(event)

    return StreamingResponse(
        event_frames(),
        media_type="text/event-stream",
        headers={
            "X-Request-ID": context.request_id,
            "Cache-Control": "no-cache",
        },
    )

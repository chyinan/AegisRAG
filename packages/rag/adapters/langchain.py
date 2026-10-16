"""Request-scoped Runnable export of the authoritative RAG query service."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any, TypeAlias

try:
    from langchain_core.runnables import Runnable, RunnableConfig
except ImportError:
    raise RuntimeError("AegisQueryRunnable requires the optional 'langchain' extra.") from None

from packages.common.context import AuthenticatedRequestContext
from packages.integrations.langchain_boundary import async_only, call_export, stream_export
from packages.rag.dto import QueryCommand, QueryResponse
from packages.rag.query import RagQueryApplicationService
from packages.rag.streaming import RagStreamEvent

QueryInput: TypeAlias = str | QueryCommand | Mapping[str, object]
QueryOutput: TypeAlias = QueryResponse | RagStreamEvent


class AegisQueryRunnable(Runnable[QueryInput, QueryOutput]):
    """Async query/citation and native event streaming without framework callbacks.

    ``ainvoke`` returns QueryResponse, while ``astream`` yields the native typed
    token/citation/error/final events. Streaming does not flatten away terminal
    errors or citation identity. Auth and policy are bound by trusted application
    code at construction, never read from a query or RunnableConfig.

    This object and its application service are request-scoped. Use async calls
    on their original event loop. An enclosing caller-owned chain can observe its
    own inputs/outputs, so it must independently disable unsafe tracing.
    """

    def __init__(
        self, *, service: RagQueryApplicationService, context: AuthenticatedRequestContext
    ) -> None:
        self._service = service
        self._context = context.model_copy(deep=True)
        self._gate = asyncio.Lock()

    @property
    def InputType(self) -> Any:
        return QueryCommand

    @property
    def OutputType(self) -> Any:
        return QueryResponse | RagStreamEvent

    def invoke(
        self, input: QueryInput, config: RunnableConfig | None = None, **kwargs: Any
    ) -> QueryResponse:
        async_only()

    async def ainvoke(
        self, input: QueryInput, config: RunnableConfig | None = None, **kwargs: Any
    ) -> QueryResponse:
        command = _command(input)
        async with self._gate:
            return await call_export(
                lambda: self._service.query(context=self._context, command=command),
                request_context=self._context,
            )

    def stream(
        self, input: QueryInput, config: RunnableConfig | None = None, **kwargs: Any
    ) -> Iterator[QueryOutput]:
        async_only()

    async def astream(
        self, input: QueryInput, config: RunnableConfig | None = None, **kwargs: Any
    ) -> AsyncIterator[RagStreamEvent]:
        command = _command(input)
        iterator = stream_export(
            lambda: self._service.stream_query(context=self._context, command=command),
            request_context=self._context,
        )
        # Do not run parallel queries on a request-scoped database session.
        async with self._gate:
            try:
                async for event in iterator:
                    yield event
            finally:
                await iterator.aclose()


def _command(value: QueryInput) -> QueryCommand:
    if isinstance(value, str):
        return QueryCommand(query=value)
    if isinstance(value, QueryCommand):
        # Revalidate nested mutable dictionaries rather than trusting model_copy.
        return QueryCommand.model_validate(value.model_dump())
    if not isinstance(value, Mapping) or set(value) - QueryCommand.model_fields.keys():
        raise ValueError("Query input must contain only QueryCommand fields.")
    return QueryCommand.model_validate(dict(value))

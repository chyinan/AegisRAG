"""Export authorized application retrieval as a real LangChain BaseRetriever."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import ConfigDict, PrivateAttr

try:
    from langchain_core.documents import Document
    from langchain_core.retrievers import BaseRetriever
    from langchain_core.runnables import RunnableConfig
except ImportError:
    raise RuntimeError("AegisRetriever requires the optional 'langchain' extra.") from None

from packages.common.context import AuthenticatedRequestContext
from packages.integrations.langchain_boundary import async_only, call_export
from packages.rag.citation_identity import citation_id
from packages.retrieval.application import (
    RetrieveApplicationService,
    RetrieveRequestBody,
)


class AegisRetriever(BaseRetriever):
    """Request-scoped, async-only retriever over the governed retrieval service.

    Construct with a trusted authenticated context, never model-supplied identity.
    The application's authorized excerpt (at most 1200 characters) becomes page
    content; this export does not bypass hydration to obtain full private chunks.
    Framework config, callbacks and kwargs cannot replace the bound identity or
    retrieval policy. Service audit/errors and cancellation remain authoritative.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    _service: RetrieveApplicationService = PrivateAttr()
    _context: AuthenticatedRequestContext = PrivateAttr()
    _options: dict[str, object] = PrivateAttr()
    _gate: asyncio.Lock = PrivateAttr(default_factory=asyncio.Lock)

    def __init__(
        self,
        *,
        service: RetrieveApplicationService,
        context: AuthenticatedRequestContext,
        top_k: int = 10,
        metadata_filter: dict[str, object] | None = None,
        score_threshold: float | None = None,
    ) -> None:
        super().__init__(name="AegisRetriever")
        options = RetrieveRequestBody(
            query="validation",
            top_k=top_k,
            metadata_filter=metadata_filter or {},
            score_threshold=score_threshold,
        )
        self._service = service
        self._context = context.model_copy(deep=True)
        self._options = options.model_dump(exclude={"query"})

    def invoke(
        self, input: str, config: RunnableConfig | None = None, **kwargs: Any
    ) -> list[Document]:
        async_only()

    async def ainvoke(
        self, input: str, config: RunnableConfig | None = None, **kwargs: Any
    ) -> list[Document]:
        # Deliberately bypass BaseRetriever's callback-dispatching invoke path.
        command = RetrieveRequestBody.model_validate({**self._options, "query": input}).to_command()

        async def retrieve() -> list[Document]:
            response = await self._service.retrieve(context=self._context, command=command)
            return [
                Document(
                    id=citation_id(candidate),
                    page_content=candidate.content_excerpt,
                    metadata={
                        **candidate.model_dump(mode="json", exclude={"metadata"}),
                        "citation_id": citation_id(candidate),
                        "content_scope": "authorized_excerpt",
                        "content_max_characters": 1200,
                        "retrieval_metadata": candidate.model_dump(mode="json")["metadata"],
                        "request_id": response.request_id,
                        "trace_id": response.trace_id,
                    },
                )
                for candidate in response.candidates
            ]

        # Framework abatch may schedule concurrent work against one DB session.
        async with self._gate:
            return await call_export(retrieve, request_context=self._context)

    def _get_relevant_documents(self, query: str, *, run_manager: Any = None) -> list[Document]:
        async_only()

    async def _aget_relevant_documents(
        self, query: str, *, run_manager: Any = None
    ) -> list[Document]:
        return await self.ainvoke(query)

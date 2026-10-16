"""Network-free contract composition using real packing/prompt/citation/query code.

Fixture lookup and an extractive fake provider deliberately do not measure dense,
BM25, reranking, or real LLM quality. Neither reads reference answers or terms.
"""

from __future__ import annotations

import html
import re
from collections.abc import AsyncIterator

from packages.auth.context import AuthContext
from packages.common.audit import InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.data.dto import ChunkRecord
from packages.eval.domain_dataset import DomainDataset, configuration_digest, sha256_bytes
from packages.eval.domain_runner import DomainApplicationBinding
from packages.llm.adapters.fake import FakeLLMProvider
from packages.llm.dto import GenerateChunk, GenerateRequest, GenerateResponse
from packages.rag.citation_extractor import CitationExtractor
from packages.rag.context_packer import ContextPacker
from packages.rag.dto import ContextPackingConfig, PromptBuilderConfig
from packages.rag.generation import RagGenerationService
from packages.rag.hydration import RetrievalCandidateHydrator
from packages.rag.prompt_builder import PromptBuilder
from packages.rag.query import RagQueryApplicationService
from packages.retrieval.dto import RetrievalCandidate, RetrievalRequest, RetrievalResult


def offline_configuration() -> dict[str, object]:
    """Include effective defaults so changed application contracts need a new profile."""
    return {
        "fixture_retrieval": "corpus-lookup-terms-v1",
        "fixture_provider": "packed-context-extractive-v1",
        "generation": {
            "provider": "fake",
            "model": "fake-llm",
            "retry_budget": 0,
            "timeout_seconds": 10.0,
        },
        "context_packing": ContextPackingConfig().model_dump(mode="json"),
        "prompt_builder": PromptBuilderConfig().model_dump(mode="json"),
        "top_k": 2,
    }


class ExtractiveFixtureProvider:
    """Read the actual escaped packed prompt, never the dataset's expectations."""

    def __init__(self) -> None:
        self.generation_count = 0

    def _provider(self, request: GenerateRequest) -> FakeLLMProvider:
        context_messages = [
            message.content for message in request.messages if message.name == "context"
        ]
        passages = [
            html.unescape(content).strip()
            for message in context_messages
            for content in re.findall(
                r"<untrusted_content>\s*(.*?)\s*</untrusted_content>", message, flags=re.DOTALL
            )
        ]
        return FakeLLMProvider(response_text="\n".join(passages))

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        self.generation_count += 1
        return await self._provider(request).generate(request)

    async def stream(self, request: GenerateRequest) -> AsyncIterator[GenerateChunk]:
        self.generation_count += 1
        async for chunk in self._provider(request).stream(request):
            yield chunk


class FixtureChunkRepository:
    def __init__(self, dataset: DomainDataset) -> None:
        self._chunks = {chunk.chunk_id: chunk for chunk in dataset.corpus}

    async def get_chunk(
        self,
        *,
        tenant_id: str,
        chunk_id: str,
        document_id: str | None = None,
        version_id: str | None = None,
    ) -> ChunkRecord | None:
        chunk = self._chunks.get(chunk_id)
        if chunk is None:
            return None
        return ChunkRecord(
            tenant_id=tenant_id,
            document_id=chunk.document_id,
            version_id=chunk.version_id,
            chunk_id=chunk.chunk_id,
            created_by="synthetic-fixture",
            status="active",
            source_type="markdown",
            source_uri=f"kb://synthetic/{chunk.document_id}.md",
            title_path=[chunk.title],
            content=chunk.content,
            token_count=len(chunk.content.split()),
            page_start=1,
            page_end=1,
            acl={"visibility": "tenant"},
            checksum=sha256_bytes(chunk.content.encode()),
            section_ids=[chunk.chunk_id],
        )


class FixtureLookupRetrieval:
    def __init__(self, dataset: DomainDataset) -> None:
        self._corpus = dataset.corpus
        self.request_count = 0

    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        auth: AuthContext | None,
    ) -> RetrievalResult:
        if auth is None:
            raise ValueError("Fixture requires authenticated context")
        self.request_count += 1
        candidates: list[RetrievalCandidate] = []
        query = request.query.casefold()
        for chunk in self._corpus:
            matches = sum(
                bool(re.search(r"\b" + re.escape(term.casefold()) + r"\b", query))
                for term in chunk.lookup_terms
            )
            if not matches:
                continue
            candidates.append(
                RetrievalCandidate(
                    document_id=chunk.document_id,
                    version_id=chunk.version_id,
                    chunk_id=chunk.chunk_id,
                    source=f"{chunk.document_id}.md",
                    source_type="markdown",
                    title_path=(chunk.title,),
                    score=min(0.95, 0.80 + 0.05 * matches),
                    retrieval_method="fixture_lookup",
                    tenant_id=auth.tenant_id,
                    acl={"visibility": "tenant"},
                    page_start=1,
                    page_end=1,
                )
            )
        candidates.sort(key=lambda candidate: (-candidate.score, candidate.chunk_id))
        return RetrievalResult(
            request_id=request.request_id,
            trace_id=request.trace_id,
            tenant_id=auth.tenant_id,
            user_id=auth.user_id,
            top_k=request.top_k,
            query_summary={"length": len(request.query)},
            candidates=tuple(candidates[: request.top_k]),
            latency_ms=0.0,
        )


def build_offline_binding(dataset: DomainDataset) -> DomainApplicationBinding:
    def context_factory(request_id: str) -> AuthenticatedRequestContext:
        return AuthenticatedRequestContext(
            request_id=request_id,
            trace_id=request_id,
            auth=AuthContext(
                tenant_id="synthetic-it-ops",
                user_id="synthetic-evaluator",
                roles=("knowledge_user",),
                permissions=("retrieval:query", "document:read"),
            ),
        )

    service = RagQueryApplicationService(
        retrieval_service=FixtureLookupRetrieval(dataset),
        hydrator=RetrievalCandidateHydrator(repository=FixtureChunkRepository(dataset)),
        context_packer=ContextPacker(),
        prompt_builder=PromptBuilder(),
        generation_service=RagGenerationService(
            provider=ExtractiveFixtureProvider(), retry_budget=0
        ),
        citation_extractor=CitationExtractor(),
        audit=InMemoryAuditPort(),
    )
    return DomainApplicationBinding(
        service=service,
        context_factory=context_factory,
        corpus_sha256=dataset.manifest.files["corpus.json"],
        configuration_sha256=configuration_digest(offline_configuration()),
        mode="offline_contract",
    )

"""Contract tests for the provider-neutral LLM reranker."""

import pytest

from packages.llm.dto import GenerateResponse, GenerationMetadata, TokenUsage
from packages.retrieval.dto import RetrievalCandidate, RetrievalFilterSet, RetrievalRequest
from packages.retrieval.rerank.adapters.llm_reranker import LLMReranker


class _FakeLLM:
    async def generate(self, request: object) -> GenerateResponse:
        usage = TokenUsage(input_tokens=1, output_tokens=1, total_tokens=2)
        metadata = GenerationMetadata(
            request_id="req-a",
            trace_id="trace-a",
            tenant_id="tenant-a",
            user_id="user-a",
            provider="fake",
            model="fake",
            usage=usage,
            latency_ms=1.0,
            finish_reason="stop",
        )
        return GenerateResponse(
            text="SCORE: 9\nSCORE: 1",
            provider="fake",
            model="fake",
            usage=usage,
            latency_ms=1.0,
            finish_reason="stop",
            request_id="req-a",
            trace_id="trace-a",
            tenant_id="tenant-a",
            user_id="user-a",
            metadata=metadata,
        )


@pytest.mark.asyncio
async def test_llm_reranker_consumes_generate_response_text() -> None:
    request = RetrievalRequest(query="policy", top_k=2, request_id="req-a", trace_id="trace-a")
    filters = RetrievalFilterSet(tenant_id="tenant-a", user_id="user-a")
    candidates = [
        RetrievalCandidate(
            document_id="doc-a",
            version_id="ver-a",
            chunk_id="chunk-a",
            source_type="txt",
            title_path=("Policy",),
            score=0.5,
            retrieval_method="hybrid",
            tenant_id="tenant-a",
            metadata={"chunk_text": "direct answer"},
        ),
        RetrievalCandidate(
            document_id="doc-b",
            version_id="ver-b",
            chunk_id="chunk-b",
            source_type="txt",
            title_path=("Other",),
            score=0.4,
            retrieval_method="hybrid",
            tenant_id="tenant-a",
            metadata={"chunk_text": "unrelated"},
        ),
    ]

    result = await LLMReranker(llm_provider=_FakeLLM(), max_retries=0).rerank(
        request=request,
        filters=filters,
        candidates=candidates,
    )

    assert [candidate.chunk_id for candidate in result.candidates] == ["chunk-a", "chunk-b"]

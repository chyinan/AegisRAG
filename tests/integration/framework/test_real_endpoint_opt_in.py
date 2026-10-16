"""Operator-only endpoint smoke contracts; skipped in normal tests and CI.

Set AEGISRAG_REAL_ENDPOINT_ALLOWED=1 only after approving the selected endpoint,
synthetic prompt transmission, and any provider cost. No saved app credentials
or implicit/default endpoint are used. See docs/FRAMEWORK_ADAPTERS.md.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import pytest

from apps.api.factories.common import create_embedding_provider, create_llm_provider
from packages.common.config import AppSettings
from packages.embeddings.dto import EmbeddingRequest
from packages.llm.dto import GenerateRequest, LLMMessage

pytestmark = pytest.mark.skipif(
    os.environ.get("AEGISRAG_REAL_ENDPOINT_ALLOWED") != "1",
    reason="Live provider validation requires explicit operator opt-in",
)


def _settings(adapter: str) -> AppSettings:
    names = ("BASE_URL", "API_KEY", "CHAT_MODEL", "EMBEDDING_MODEL", "EMBEDDING_DIM")
    values = {name: os.environ.get(f"AEGISRAG_REAL_{name}", "") for name in names}
    if any(not value for value in values.values()):
        pytest.fail("Opt-in requires all documented AEGISRAG_REAL_* settings", pytrace=False)
    url = urlsplit(values["BASE_URL"])
    if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
        pytest.fail("Use an explicit HTTP(S) base URL without embedded credentials", pytrace=False)
    if url.query or url.fragment:
        pytest.fail("Base URL must not contain query parameters or fragments", pytrace=False)
    try:
        dim = int(values["EMBEDDING_DIM"])
        if dim <= 0:
            raise ValueError
    except ValueError:
        pytest.fail("AEGISRAG_REAL_EMBEDDING_DIM must be a positive integer", pytrace=False)
    return AppSettings(
        LLM_ADAPTER=adapter,
        EMBEDDING_ADAPTER=adapter,
        LLM_PROVIDER="openai_compatible",
        LLM_MODEL=values["CHAT_MODEL"],
        LLM_BASE_URL=values["BASE_URL"],
        LLM_API_KEY=values["API_KEY"],
        EMBEDDING_PROVIDER="openai_compatible",
        EMBEDDING_MODEL=values["EMBEDDING_MODEL"],
        EMBEDDING_BASE_URL=values["BASE_URL"],
        EMBEDDING_API_KEY=values["API_KEY"],
        EMBEDDING_DIM=dim,
        VECTOR_INDEX_DIM=dim,
    )


@pytest.mark.parametrize("adapter", ("native", "langchain"))
async def test_explicit_endpoint_text_stream_and_embedding_contract(adapter: str) -> None:
    if adapter == "langchain":
        pytest.importorskip("langchain_openai")
    settings = _settings(adapter)
    provider = create_llm_provider(settings)
    request = GenerateRequest(
        messages=(LLMMessage(role="user", content="Reply with the word ready."),),
        provider=settings.llm_provider,
        model=settings.llm_model,
        timeout_seconds=30,
        retry_budget=0,
        request_id="endpoint-contract",
        trace_id="endpoint-contract",
        tenant_id="synthetic-validation",
        user_id="synthetic-validation",
        max_output_tokens=16,
        temperature=0,
        stream_options={"include_usage": os.environ.get("AEGISRAG_REAL_STREAM_USAGE") == "1"},
    )
    generated = await provider.generate(request)
    assert generated.provider == settings.llm_provider
    assert generated.text.strip()
    chunks = [chunk async for chunk in provider.stream(request)]
    assert chunks and chunks[-1].is_final
    assert sum(chunk.is_final for chunk in chunks) == 1
    assert chunks[-1].response is not None
    embeddings = create_embedding_provider(
        adapter=adapter,
        provider=settings.embedding_provider,
        model=settings.embedding_model,
        dim=settings.embedding_dim,
        base_url=settings.embedding_base_url,
        api_key=settings.embedding_api_key,
        version=settings.embedding_provider_version,
    )
    embedded = await embeddings.embed_texts(
        EmbeddingRequest(
            texts=["Synthetic endpoint validation."],
            chunk_ids=["synthetic-chunk"],
            provider=settings.embedding_provider,
            model=settings.embedding_model,
            timeout_seconds=30,
            retry_budget=0,
        )
    )
    assert len(embedded.vectors) == 1
    assert embedded.vectors[0].chunk_id == "synthetic-chunk"
    assert embedded.dim == settings.embedding_dim

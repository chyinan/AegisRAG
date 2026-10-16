"""Application contracts across all eight independent framework selections.

Only persistence is faked. Routes, application services, provider factories,
HTTP/SDK conversion, retrieval filters, hydration, citations and governed tool
execution run their production implementations. No model endpoint is contacted.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from functools import partial
from itertools import product
from typing import Any

import httpx
import pytest

from apps.api.factories import common
from apps.api.factories.agent import create_agent_runtime, create_agent_stepper
from apps.api.main import app
from apps.api.service_dependencies import (
    get_agent_run_application_service,
    get_chat_application_service,
    get_rag_query_application_service,
    get_retrieve_application_service,
)
from apps.worker.jobs import embedding_jobs
from packages.agent.dto import ToolRateLimit
from packages.agent.final_answer import StrictFinalAnswerValidator
from packages.agent.registry import ToolRegistry
from packages.agent.service import AgentRunApplicationService
from packages.agent.tools.rag_search import build_rag_search_tool
from packages.common.audit import InMemoryAuditPort
from packages.common.config import AppSettings
from packages.embeddings.service import EmbeddingJobService
from packages.memory import ChatMemoryService, ChatMessageRecord, ChatSessionRecord
from packages.rag import (
    ChatApplicationService,
    CitationExtractor,
    ContextPacker,
    PromptBuilder,
    QueryCommand,
    RagGenerationService,
    RagQueryApplicationService,
    RetrievalCandidateHydrator,
)
from packages.retrieval.application import RetrieveApplicationService
from packages.retrieval.dense import DenseRetriever, DenseRetrieverConfig
from packages.retrieval.service import RetrievalService
from packages.vectorstores.adapters.fake import FakeVectorStore
from packages.vectorstores.dto import VectorRecord
from tests.unit.agent.test_agent_run_service import FakeAgentRunRepository
from tests.unit.embeddings.test_embedding_service import FakeRepository
from tests.unit.memory.test_chat_application_service import FakeChatMemoryRepository
from tests.unit.rag.test_query_service import _chunk, _context
from tests.unit.retrieval.test_retrieve_application import FakeRetrievalLogPort

COMBINATIONS = [
    (*combination, "json")
    for combination in product(
        ("native", "langchain"), ("native", "langchain"), ("native", "langgraph")
    )
] + [
    ("langchain", embedding, backend, "native_tools")
    for embedding, backend in product(("native", "langchain"), ("native", "langgraph"))
]

HEADERS = {
    "X-Request-ID": "req-matrix",
    "X-Trace-ID": "trace-matrix",
    "X-User-ID": "user-1",
    "X-Tenant-ID": "tenant-1",
    "X-Roles": "knowledge_user",
    "X-Permissions": "document:read,retrieval:query,agent:run,agent:tool:rag_search",
}
CITATION = {"document_id": "doc-1", "version_id": "v1", "chunk_id": "chunk-1"}


class MemoryRepository(FakeChatMemoryRepository):
    async def create_session(self, record: Any) -> ChatSessionRecord:
        now = datetime.now(tz=UTC)
        session = ChatSessionRecord(
            **record.model_dump(),
            id="session-matrix",
            status="active",
            created_at=now,
            updated_at=now,
        )
        self.sessions[(record.tenant_id, record.user_id, session.id)] = session
        return session

    async def append_message(self, record: Any) -> ChatMessageRecord:
        now = datetime.now(tz=UTC)
        self.created_messages.append(record)
        message = ChatMessageRecord(
            **record.model_dump(),
            id=f"message-{len(self.created_messages)}",
            created_by=record.user_id,
            status="active",
            sequence_no=len(self.created_messages),
            created_at=now,
            updated_at=now,
        )
        self.persisted_messages.append(message)
        return message


class ChunkRepository:
    def __init__(self, chunks: list[Any]) -> None:
        self.chunks = {chunk.chunk_id: chunk for chunk in chunks}
        self.reads: list[dict[str, str]] = []

    async def get_chunk(self, **scope: str) -> Any:
        self.reads.append(scope)
        chunk = self.chunks.get(scope["chunk_id"])
        return chunk if chunk is not None and chunk.tenant_id == scope["tenant_id"] else None


class Endpoint:
    """Strict local OpenAI-shaped transport; unexpected paths fail immediately."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict[str, Any]]] = []
        self.failure: str | None = None
        self.block = False
        self.started = asyncio.Event()
        self.cancelled = False

    async def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "models.test"
        body = json.loads(request.content)
        path = request.url.path
        self.requests.append((path, body))
        assert path in {"/v1/embeddings", "/v1/chat/completions"}
        if self.failure == path:
            return httpx.Response(503, json={"error": {"message": "private-provider-error"}})
        if path == "/v1/embeddings":
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "model": "test-embedding",
                    "usage": {"total_tokens": 2},
                    "data": [
                        {"object": "embedding", "index": i, "embedding": [1.0, 0.0, 0.0]}
                        for i in reversed(range(len(body["input"])))
                    ],
                },
            )
        self.started.set()
        if self.block:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        text = "Evidence-backed answer."
        if "governed enterprise agent" in body["messages"][0]["content"]:
            user = body["messages"][-1]["content"]
            decision = (
                {
                    "action": "tool_call",
                    "tool_name": "rag_search",
                    "arguments": {"query": "policy", "metadata_filter": {"department": "HR"}},
                }
                if "<observations>[]</observations>" in user
                else {
                    "action": "final_answer",
                    "final_answer": text,
                    "final_citations": [
                        {**CITATION, "source": "Source unavailable", "page_start": 1, "page_end": 1}
                    ],
                }
            )
            text = json.dumps(decision)
        base = {"id": "completion-matrix", "created": 1, "model": "test-chat"}
        usage = {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11}
        if body.get("tools"):
            assert body["parallel_tool_calls"] is False
            assert body["tool_choice"] == "required"
            if decision["action"] == "tool_call":
                name, arguments = decision["tool_name"], decision["arguments"]
            else:
                name = "aegis_final_answer"
                arguments = {key: decision[key] for key in ("final_answer", "final_citations")}
            return httpx.Response(
                200,
                json={
                    **base,
                    "object": "chat.completion",
                    "usage": usage,
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call-matrix",
                                        "type": "function",
                                        "function": {
                                            "name": name,
                                            "arguments": json.dumps(arguments),
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                },
            )
        if body.get("stream"):
            chunks = [
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": text},
                            "finish_reason": None,
                        }
                    ]
                },
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                {"choices": [], "usage": usage},
            ]
            stream = "".join(
                "data: " + json.dumps({**base, "object": "chat.completion.chunk", **chunk}) + "\n\n"
                for chunk in chunks
            )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=stream + "data: [DONE]\n\n",
            )
        return httpx.Response(
            200,
            json={
                **base,
                "object": "chat.completion",
                "usage": usage,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": text},
                        "finish_reason": "stop",
                    }
                ],
            },
        )


@pytest.fixture(params=COMBINATIONS, ids=lambda item: "/".join(item))
async def stack(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Any]:
    llm_adapter, embedding_adapter, backend, decision_mode = request.param
    if "langchain" in (llm_adapter, embedding_adapter):
        pytest.importorskip("langchain_openai")
    if backend == "langgraph":
        pytest.importorskip("langgraph.graph")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    endpoint = Endpoint()
    async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint.handle)) as http:
        # Inject only transport/components. The factory and selected adapter are real.
        monkeypatch.setattr(
            common,
            "OpenAICompatibleChatProvider",
            partial(common.OpenAICompatibleChatProvider, client=http),
        )
        native_embedding = common.OpenAICompatibleEmbeddingProvider
        monkeypatch.setattr(
            common, "OpenAICompatibleEmbeddingProvider", partial(native_embedding, client=http)
        )
        monkeypatch.setattr(
            embedding_jobs,
            "OpenAICompatibleEmbeddingProvider",
            partial(native_embedding, client=http),
        )
        if "langchain" in (llm_adapter, embedding_adapter):
            import langchain_openai

            from packages.embeddings.adapters import langchain as embedding_module
            from packages.llm.adapters import langchain as llm_module

            with httpx.Client(
                transport=httpx.MockTransport(lambda _: pytest.fail("sync HTTP"))
            ) as sync:
                model = langchain_openai.ChatOpenAI(
                    model="test-chat",
                    base_url="https://models.test/v1",
                    api_key="test-key",
                    http_async_client=http,
                    http_client=sync,
                    max_retries=0,
                    use_responses_api=False,
                    stream_usage=True,
                    cache=False,
                )
                embeddings = langchain_openai.OpenAIEmbeddings(
                    model="test-embedding",
                    base_url="https://models.test/v1",
                    api_key="test-key",
                    http_async_client=http,
                    http_client=sync,
                    max_retries=0,
                    check_embedding_ctx_length=False,
                )
            monkeypatch.setattr(
                llm_module,
                "LangChainChatProvider",
                partial(llm_module.LangChainChatProvider, chat_model=model),
            )
            monkeypatch.setattr(
                embedding_module,
                "LangChainEmbeddingProvider",
                partial(embedding_module.LangChainEmbeddingProvider, embeddings=embeddings),
            )
        settings = AppSettings(
            LLM_ADAPTER=llm_adapter,
            EMBEDDING_ADAPTER=embedding_adapter,
            AGENT_ORCHESTRATION_BACKEND=backend,
            AGENT_DECISION_MODE=decision_mode,
            LLM_PROVIDER="openai_compatible",
            LLM_MODEL="test-chat",
            LLM_BASE_URL="https://models.test/v1",
            LLM_API_KEY="test-key",
            LLM_PROVIDER_VERSION="test-version",
            EMBEDDING_PROVIDER="openai_compatible",
            EMBEDDING_MODEL="test-embedding",
            EMBEDDING_BASE_URL="https://models.test/v1",
            EMBEDDING_API_KEY="test-key",
            EMBEDDING_PROVIDER_VERSION="test-version",
            EMBEDDING_DIM=3,
            VECTOR_INDEX_DIM=3,
        )
        llm = common.create_llm_provider(settings)
        embedding = common.create_embedding_provider(
            adapter=settings.embedding_adapter,
            provider=settings.embedding_provider,
            model=settings.embedding_model,
            dim=settings.embedding_dim,
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key,
            version=settings.embedding_provider_version,
        )
        assert type(llm).__name__ == (
            "LangChainChatProvider"
            if llm_adapter == "langchain"
            else "OpenAICompatibleChatProvider"
        )
        assert type(embedding).__name__ == (
            "LangChainEmbeddingProvider"
            if embedding_adapter == "langchain"
            else "OpenAICompatibleEmbeddingProvider"
        )
        audit = InMemoryAuditPort()
        store = FakeVectorStore(index_dim=3)
        chunks = [
            _chunk().model_copy(update={"metadata": {"department": "HR"}}),
            _chunk(
                chunk_id="chunk-private",
                acl={"visibility": "private", "allowed_user_ids": ["other"]},
            ).model_copy(update={"metadata": {"department": "HR"}}),
            _chunk(chunk_id="chunk-other-tenant", tenant_id="tenant-other").model_copy(
                update={"metadata": {"department": "HR"}}
            ),
            _chunk(chunk_id="chunk-other-department").model_copy(
                update={"metadata": {"department": "Finance"}}
            ),
        ]
        for chunk in chunks:
            await store.upsert(
                [
                    VectorRecord(
                        **chunk.model_dump(exclude={"content", "section_ids"}),
                        vector=[1.0, 0.0, 0.0],
                        embedding_provider="openai_compatible",
                        embedding_model="test-embedding",
                        embedding_version="test-version",
                        embedding_dim=3,
                    )
                ]
            )
        repository = ChunkRepository(chunks)
        hydrator = RetrievalCandidateHydrator(repository=repository)
        retrieval = RetrievalService(
            retriever=DenseRetriever(
                embedding_provider=embedding,
                vector_store=store,
                config=DenseRetrieverConfig(
                    embedding_provider="openai_compatible",
                    embedding_model="test-embedding",
                    embedding_version="test-version",
                    timeout_seconds=1,
                    retry_budget=0,
                ),
            )
        )
        retrieve = RetrieveApplicationService(
            retrieval_service=retrieval,
            retrieval_log=FakeRetrievalLogPort(),
            audit=audit,
            content_hydrator=hydrator,
        )
        query = RagQueryApplicationService(
            retrieval_service=retrieval,
            hydrator=hydrator,
            context_packer=ContextPacker(),
            prompt_builder=PromptBuilder(),
            generation_service=RagGenerationService(
                provider=llm,
                provider_name="openai_compatible",
                model="test-chat",
                timeout_seconds=1,
                retry_budget=0,
            ),
            citation_extractor=CitationExtractor(),
            audit=audit,
        )
        memory = MemoryRepository()
        chat = ChatApplicationService(
            memory_service=ChatMemoryService(repository=memory),
            rag_query_service=query,
            audit=audit,
        )
        registry = ToolRegistry(audit=audit)
        registry.register(
            build_rag_search_tool(
                retrieval_app=retrieve,
                timeout_seconds=1,
                rate_limit=ToolRateLimit(max_calls=100, window_seconds=60),
            )
        )
        stepper = create_agent_stepper(settings=settings, llm_provider=llm, registry=registry)
        runtime_factory = partial(
            create_agent_runtime,
            backend=backend,
            registry=registry,
            stepper=stepper,
            audit=audit,
            final_answer_validator=StrictFinalAnswerValidator(audit=audit),
        )
        agent_repository = FakeAgentRunRepository()
        agent = AgentRunApplicationService(
            repository=agent_repository,
            runtime_factory=lambda config, run_id: runtime_factory(
                config=config, agent_run_id=run_id
            ),
            stream_runtime_factory=lambda config, run_id, sink: runtime_factory(
                config=config, agent_run_id=run_id, event_sink=sink
            ),
            audit=audit,
            default_max_steps=4,
            default_max_tool_calls=2,
            default_timeout_seconds=3,
            repeated_action_threshold=2,
        )
        app.dependency_overrides.update(
            {
                get_retrieve_application_service: lambda: retrieve,
                get_rag_query_application_service: lambda: query,
                get_chat_application_service: lambda: chat,
                get_agent_run_application_service: lambda: agent,
            }
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://api.test"
            ) as api:
                yield {
                    "api": api,
                    "endpoint": endpoint,
                    "query": query,
                    "chat": chat,
                    "memory": memory,
                    "repository": repository,
                    "settings": settings,
                    "audit": audit,
                    "agent_repository": agent_repository,
                }
        finally:
            app.dependency_overrides.clear()


def _events(text: str) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in text.splitlines()
        if line.startswith("data: ")
    ]


async def test_retrieve_query_chat_agent_preserve_evidence_and_scope(stack: Any) -> None:
    api = stack["api"]
    payload = {"query": "policy", "metadata_filter": {"department": "HR"}}
    response = await api.post("/retrieve", headers=HEADERS, json=payload)
    assert response.status_code == 200, response.text
    candidates = response.json()["data"]["candidates"]
    assert [item["chunk_id"] for item in candidates] == ["chunk-1"]
    for path in ("/query", "/chat"):
        response = await api.post(path, headers=HEADERS, json=payload)
        assert response.status_code == 200, response.text
        result = response.json()["data"]
        assert result["no_answer"] is False
        assert [{key: item[key] for key in CITATION} for item in result["citations"]] == [CITATION]
        assert result["metadata"]["generation"]["provider"] == "openai_compatible"
    response = await api.post("/agent/run", headers=HEADERS, json={"input": "policy"})
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["status"] == "completed", result
    assert result["steps_used"] == 2 and result["tool_calls_used"] == 1
    assert [{key: item[key] for key in CITATION} for item in result["final_citations"]] == [
        CITATION
    ]
    assert all(item["chunk_id"] == "chunk-1" for item in stack["repository"].reads)
    assert stack["agent_repository"].updated.status == "completed"
    safe_audit = json.dumps(
        [event.model_dump(mode="json") for event in stack["audit"].events], ensure_ascii=False
    )
    assert "授权正文" not in safe_audit and "test-key" not in safe_audit


@pytest.mark.parametrize("path", ("/query/stream", "/chat/stream"))
async def test_stream_has_one_terminal_and_authorized_citations(stack: Any, path: str) -> None:
    response = await stack["api"].post(
        path, headers=HEADERS, json={"query": "policy", "metadata_filter": {"department": "HR"}}
    )
    assert response.status_code == 200, response.text
    names = [
        line.removeprefix("event: ")
        for line in response.text.splitlines()
        if line.startswith("event: ")
    ]
    assert names[-1] == "final" and names.count("final") == 1
    assert "token" in names and "citation" in names and "error" not in names
    final = _events(response.text)[-1]
    assert final["status"] == "success"
    assert [{key: item[key] for key in CITATION} for item in final["citations"]] == [CITATION]
    assert "source_uri" not in response.text and "授权正文" not in response.text
    if path == "/chat/stream":
        assert [message.role for message in stack["memory"].created_messages] == [
            "user",
            "assistant",
        ]


async def test_forbidden_scope_and_empty_evidence_do_not_generate(stack: Any) -> None:
    endpoint = stack["endpoint"]
    for path in ("/retrieve", "/query", "/chat"):
        response = await stack["api"].post(
            path,
            headers=HEADERS,
            json={"query": "policy", "metadata_filter": {"tenant_id": "attacker"}},
        )
        assert response.status_code in (403, 422), response.text
    assert endpoint.requests == []
    response = await stack["api"].post(
        "/query",
        headers=HEADERS,
        json={"query": "policy", "metadata_filter": {"department": "missing"}},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["no_answer"] is True
    assert response.json()["data"]["citations"] == []
    assert all(path == "/v1/embeddings" for path, _ in endpoint.requests)


async def test_provider_failure_stream_terminal_and_agent_budget(stack: Any) -> None:
    endpoint = stack["endpoint"]
    endpoint.failure = "/v1/chat/completions"
    response = await stack["api"].post(
        "/query/stream",
        headers=HEADERS,
        json={"query": "policy", "metadata_filter": {"department": "HR"}},
    )
    names = [
        line.removeprefix("event: ")
        for line in response.text.splitlines()
        if line.startswith("event: ")
    ]
    assert names[-2:] == ["error", "final"]
    assert names.count("final") == 1 and "token" not in names
    assert _events(response.text)[-1]["status"] == "error"
    assert "private-provider-error" not in response.text
    endpoint.failure = None
    response = await stack["api"].post(
        "/agent/run", headers=HEADERS, json={"input": "policy", "max_steps": 1}
    )
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["status"] == "stopped" and result["termination_reason"] == "MAX_STEPS_REACHED"
    assert result["steps_used"] == 1 and result["tool_calls_used"] == 1


async def test_cancelling_chat_does_not_persist_a_success(stack: Any) -> None:
    endpoint = stack["endpoint"]
    endpoint.block = True
    stream = stack["chat"].stream_chat(
        context=_context(),
        session_id=None,
        command=QueryCommand(query="policy", metadata_filter={"department": "HR"}),
    )
    observed = []

    async def consume() -> None:
        async for event in stream:
            observed.append(event)

    task = asyncio.create_task(consume())
    await asyncio.wait_for(endpoint.started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await stream.aclose()
    assert endpoint.cancelled
    assert all(event.event != "final" for event in observed)
    assert [message.role for message in stack["memory"].created_messages] == ["user"]


async def test_worker_uses_same_embedding_switch_and_keeps_provenance(stack: Any) -> None:
    settings = stack["settings"]
    provider = embedding_jobs._provider_from_settings(settings)
    repository = FakeRepository()
    repository.job = repository.job.model_copy(
        update={
            "provider": "openai_compatible",
            "model": "test-embedding",
            "version": "test-version",
            "dim": 3,
        }
    )
    store = FakeVectorStore(index_dim=3)
    service = EmbeddingJobService(
        repository=repository,
        provider=provider,
        audit=stack["audit"],
        vector_store=store,
        timeout_seconds=1,
        retry_budget=0,
    )
    payload = {
        "request_id": "req-worker",
        "trace_id": "trace-worker",
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "job_type": "document_embedding",
        "resource_id": "embed-job-1",
        "parameters": {"document_id": "doc-1", "version_id": "ver-1"},
    }
    from packages.data.queue.embedding import EMBEDDING_JOB_TYPE

    payload["job_type"] = EMBEDDING_JOB_TYPE
    result = await asyncio.to_thread(
        embedding_jobs.process_document_embedding, payload, embedding_service=service
    )
    assert result["status"] == "embedded" and result["chunk_count"] == 2 and result["dim"] == 3
    assert repository.version.status == "retrieval_ready"
    assert repository.job.provider == "openai_compatible"
    assert repository.job.model == "test-embedding" and repository.job.version == "test-version"
    assert repository.ready_calls == 1
    assert len(store._records) == 2
    assert {item.chunk_id for item in store._records.values()} == {"chunk-1", "chunk-2"}


async def test_agent_stream_emits_governed_progress_and_persisted_terminal(stack: Any) -> None:
    response = await stack["api"].post(
        "/agent/run/stream", headers=HEADERS, json={"input": "policy"}
    )
    assert response.status_code == 200, response.text
    events = _events(response.text)
    assert [event["event"] for event in events] == ["started", "tool_call", "tool_result", "final"]
    assert [event["sequence"] for event in events] == list(range(len(events)))
    assert all(event["request_id"] == "req-matrix" for event in events)
    assert all(event["trace_id"] == "trace-matrix" for event in events)
    assert all(event["agent_run_id"] == "run-1" for event in events)
    final = events[-1]["data"]
    assert final["status"] == "completed"
    assert [{key: item[key] for key in CITATION} for item in final["final_citations"]] == [CITATION]
    assert stack["agent_repository"].updated.status == "completed"
    assert "授权正文" not in response.text and "test-key" not in response.text
    assert "final_answer" not in json.dumps(events[:-1])


async def test_agent_tool_authorization_is_not_inferred_from_model_decision(stack: Any) -> None:
    headers = {**HEADERS, "X-Permissions": "agent:run,document:read,retrieval:query"}
    response = await stack["api"].post("/agent/run", headers=headers, json={"input": "policy"})
    assert response.status_code == 200, response.text
    result = response.json()["data"]
    assert result["status"] == "failed"
    assert result["final_answer"] is None
    assert result["tool_calls_used"] == 1
    assert all(path != "/v1/embeddings" for path, _ in stack["endpoint"].requests)
    assert stack["repository"].reads == []


async def test_embedding_error_is_structured_before_generation(stack: Any) -> None:
    stack["endpoint"].failure = "/v1/embeddings"
    response = await stack["api"].post("/retrieve", headers=HEADERS, json={"query": "policy"})
    assert response.status_code == 502, response.text
    assert response.json()["error"]["code"] == "RETRIEVAL_EMBEDDING_FAILED"
    assert "private-provider-error" not in response.text
    assert "test-key" not in response.text
    assert len(stack["endpoint"].requests) == 1
    assert stack["repository"].reads == []

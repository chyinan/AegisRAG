"""Offline, same-document query and workbench-chat journeys through real services.

Only external storage, queues, embedding, vectors, and generation use existing
fakes. No chunks, vectors, ready flags, retrieval results, or citations are seeded.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.api.factories.retrieval import create_retrieval_service
from apps.api.main import app
from apps.api.service_dependencies import (
    get_chat_application_service,
    get_document_lifecycle_service,
    get_document_upload_service,
    get_rag_query_application_service,
    get_retrieve_application_service,
    get_source_resolve_service,
)
from packages.auth.context import AuthContext
from packages.common.config import AppSettings
from packages.common.context import AuthenticatedRequestContext
from packages.common.tenant_context import tenant_scope
from packages.data.lifecycle import DocumentLifecycleService
from packages.data.queue.contracts import QueuePayload
from packages.data.queue.embedding import EMBEDDING_JOB_TYPE
from packages.data.queue.ingestion import INGESTION_JOB_TYPE
from packages.data.service import DocumentUploadService
from packages.data.storage.audit_repositories import SqlAlchemyAuditPort
from packages.data.storage.auxiliary_audit import SessionFactoryAuditPort
from packages.data.storage.models import ChunkModel, EmbeddingJobModel
from packages.data.storage.repositories import DocumentRepository
from packages.embeddings.adapters.fake import FakeEmbeddingProvider
from packages.embeddings.service import EmbeddingJobService
from packages.ingestion.service import IngestionParseService
from packages.llm.adapters.fake import FakeLLMProvider
from packages.llm.dto import GenerateChunk, GenerateRequest
from packages.memory import ChatMemoryService
from packages.memory.storage.models import ChatMessageModel, ChatSessionModel
from packages.memory.storage.repositories import ChatMemoryRepository
from packages.rag import (
    ChatApplicationService,
    ChatHistoryResponse,
    CitationExtractor,
    ContextPacker,
    PromptBuilder,
    RagGenerationService,
    RagQueryApplicationService,
    RetrievalCandidateHydrator,
)
from packages.rag.auxiliary_audit import BoundedBestEffortAuditPort
from packages.rag.citation_identity import citation_id
from packages.rag.source_resolver import SourceResolveService
from packages.rag.streaming import FinalEventPayload
from packages.retrieval.application import RetrieveApplicationService
from packages.retrieval.storage.repositories import RetrievalLogRepository
from packages.vectorstores.adapters.fake import FakeVectorStore
from tests.integration.storage.test_document_repositories import _run_migrations, _sqlite_async_url
from tests.unit.data.test_document_upload_service import FakeJobQueue, FakeObjectStorage
from tests.unit.ingestion.test_parse_service import FakeEmbeddingQueue, FakeStorage

DOCUMENT_TEXT = (
    "Synthetic policy: orbital equipment requires a cobalt inspection every seven days."
)
PERMISSIONS = ("document:upload", "document:manage", "document:read", "retrieval:query")
IDENTITY_FIELDS = ("document_id", "version_id", "chunk_id")


def _headers(stage: str) -> dict[str, str]:
    return {
        "X-Request-ID": f"req-journey-{stage}",
        "X-Trace-ID": "trace-journey",
        "X-User-ID": "user-1",
        "X-Tenant-ID": "tenant-1",
        "X-Roles": "knowledge_admin",
        "X-Permissions": ",".join(PERMISSIONS),
    }


def _queue_context(payload: QueuePayload) -> AuthenticatedRequestContext:
    assert (payload.tenant_id, payload.user_id) == ("tenant-1", "user-1")
    assert (payload.request_id, payload.trace_id) == ("req-journey-upload", "trace-journey")
    return AuthenticatedRequestContext(
        request_id=payload.request_id,
        trace_id=payload.trace_id,
        auth=AuthContext(
            tenant_id=payload.tenant_id,
            user_id=payload.user_id,
            roles=("knowledge_admin",),
            permissions=PERMISSIONS,
        ),
    )


def _queue_identifiers(payload: QueuePayload) -> dict[str, str]:
    assert set(payload.parameters) == {"document_id", "version_id"}
    assert all(isinstance(value, str) for value in payload.parameters.values())
    return {
        "job_id": payload.resource_id,
        "document_id": str(payload.parameters["document_id"]),
        "version_id": str(payload.parameters["version_id"]),
    }


def _identity(item: dict[str, Any]) -> tuple[str, ...]:
    return tuple(item[field] for field in IDENTITY_FIELDS)


def _require_hybrid_contributions(candidate: dict[str, Any]) -> None:
    provenance = candidate["metadata"]["retrieval_provenance"]
    contributions = {
        item["retrieval_method"]: item["contribution"] for item in provenance["sources"]
    }
    assert contributions.get("dense", 0) > 0, "dense branch did not contribute uploaded vectors"
    assert contributions.get("sparse", 0) > 0, "sparse branch did not contribute persisted text"
    assert set(provenance["retrieval_methods"]) == {"dense", "sparse"}


def _successful_stream_final(
    response: httpx.Response, *, request_stage: str, synthetic_answer: str,
    identity: tuple[str, ...],
) -> dict[str, Any]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = []
    for frame in response.text.strip().split("\n\n"):
        wire_event, wire_data = frame.splitlines()
        assert wire_data.startswith("data: ")
        event = json.loads(wire_data.removeprefix("data: "))
        assert wire_event == f"event: {event['event']}"
        events.append(event)
    names = [event["event"] for event in events]
    assert names[-1] == "final" and names.count("final") == 1
    assert "token" in names and "citation" in names and "error" not in names
    final = events[-1]
    FinalEventPayload.model_validate(final)
    assert final["status"] == "success" and final["no_answer"] is False
    assert final["unsupported_claims"] == []
    assert (final["tenant_id"], final["user_id"]) == ("tenant-1", "user-1")
    assert (final["request_id"], final["trace_id"]) == (
        f"req-journey-{request_stage}", "trace-journey",
    )
    assert final["answer"] == synthetic_answer
    assert "".join(event["delta"] for event in events if event["event"] == "token") == (
        synthetic_answer
    )
    assert [_identity(item) for item in final["citations"]] == [identity]
    assert [
        _identity(event["citation"]) for event in events if event["event"] == "citation"
    ] == [identity]
    return dict(final)


class RecordingFakeLLMProvider(FakeLLMProvider):
    """Observe the real request without replacing any generation behavior."""

    def __init__(self, *, response_text: str) -> None:
        super().__init__(response_text=response_text)
        self.requests: list[GenerateRequest] = []

    async def stream(self, request: GenerateRequest) -> AsyncIterator[GenerateChunk]:
        self.requests.append(request)
        async for chunk in super().stream(request):
            yield chunk


@pytest.fixture
def database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = _sqlite_async_url(tmp_path / "document-to-citation.db")
    _run_migrations(url, monkeypatch)
    return url


@pytest.mark.parametrize("stream_kind", ["query", "chat"], ids=["query", "workbench-chat"])
async def test_uploaded_document_reaches_hybrid_query_and_resolved_citation(
    database_url: str, monkeypatch: pytest.MonkeyPatch, stream_kind: str,
) -> None:
    # Use the existing test-only identity path, never an authentication override.
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ENABLE_DEV_AUTH_HEADERS", "true")
    settings = AppSettings(
        DATABASE_URL=database_url,
        EMBEDDING_PROVIDER="fake",
        EMBEDDING_MODEL="fake-embedding",
        EMBEDDING_DIM=8,
        VECTOR_INDEX_DIM=8,
        RERANK_PROVIDER="fake",
        HYBRID_DEGRADED_MODE=False,
        ADAPTIVE_ROUTING_ENABLED=False,
        QUERY_REWRITE_ENABLED=False,
    )
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    upload_storage = FakeObjectStorage()
    ingestion_queue = FakeJobQueue()
    embedding_queue = FakeEmbeddingQueue()
    embedding_provider = FakeEmbeddingProvider(dim=8)
    vector_store = FakeVectorStore(index_dim=8)
    llm: RecordingFakeLLMProvider | None = None
    auxiliary_audit = BoundedBestEffortAuditPort(
        owned_audit=SessionFactoryAuditPort(factory),
        timeout_seconds=settings.rag_auxiliary_io_timeout_seconds,
    )

    async def upload_service() -> AsyncIterator[DocumentUploadService]:
        async with factory() as session:
            yield DocumentUploadService(
                object_storage=upload_storage,
                repository=DocumentRepository(session),
                job_queue=ingestion_queue,
                audit=SqlAlchemyAuditPort(session),
            )

    async def lifecycle_service() -> AsyncIterator[DocumentLifecycleService]:
        async with factory() as session:
            yield DocumentLifecycleService(
                repository=DocumentRepository(session),
                vector_store=vector_store,
                audit=SqlAlchemyAuditPort(session, auto_commit=True),
            )

    async def retrieve_service() -> AsyncIterator[RetrieveApplicationService]:
        async with factory() as session:
            retrieval, trace, _ = create_retrieval_service(
                settings=settings, session=session,
                vector_store=vector_store, embedding_provider=embedding_provider,
            )
            yield RetrieveApplicationService(
                retrieval_service=retrieval,
                retrieval_log=RetrievalLogRepository(session),
                audit=SqlAlchemyAuditPort(session),
                pipeline_trace_provider=trace,
                content_hydrator=RetrievalCandidateHydrator(repository=DocumentRepository(session)),
            )

    def build_query_service(session: AsyncSession) -> RagQueryApplicationService:
        assert llm is not None
        retrieval, _, _ = create_retrieval_service(
            settings=settings, session=session,
            vector_store=vector_store, embedding_provider=embedding_provider,
        )
        return RagQueryApplicationService(
            retrieval_service=retrieval,
            hydrator=RetrievalCandidateHydrator(repository=DocumentRepository(session)),
            context_packer=ContextPacker(),
            prompt_builder=PromptBuilder(),
            generation_service=RagGenerationService(
                provider=llm, provider_name="fake", model="fake-llm",
            ),
            citation_extractor=CitationExtractor(),
            audit=SqlAlchemyAuditPort(session, auto_commit=True),
            stream_audit=auxiliary_audit,
        )

    async def query_service() -> AsyncIterator[RagQueryApplicationService]:
        async with factory() as session:
            yield build_query_service(session)

    async def chat_service() -> AsyncIterator[ChatApplicationService]:
        async with factory() as session:
            yield ChatApplicationService(
                memory_service=ChatMemoryService(repository=ChatMemoryRepository(session)),
                rag_query_service=build_query_service(session),
                audit=auxiliary_audit,
            )

    async def source_service() -> AsyncIterator[SourceResolveService]:
        async with factory() as session:
            yield SourceResolveService(
                repository=DocumentRepository(session),
                audit=SqlAlchemyAuditPort(session, auto_commit=True),
            )

    original_overrides = app.dependency_overrides.copy()
    app.dependency_overrides.update({
        get_chat_application_service: chat_service,
        get_document_upload_service: upload_service,
        get_document_lifecycle_service: lifecycle_service,
        get_retrieve_application_service: retrieve_service,
        get_rag_query_application_service: query_service,
        get_source_resolve_service: source_service,
    })
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://api.test",
        ) as client:
            response = await client.post(
                "/upload", headers=_headers("upload"),
                files={"file": ("synthetic-policy.txt", DOCUMENT_TEXT.encode(), "text/plain")},
                data={"source_type": "txt", "title": "Synthetic inspection policy"},
            )
            assert response.status_code == 200, response.text
            uploaded = response.json()["data"]
            assert uploaded["status"] == "uploaded"
            document_id, version_id = uploaded["document_id"], uploaded["version_id"]
            status_url = f"/documents/{document_id}/versions/{version_id}/status"
            response = await client.get(status_url, headers=_headers("uploaded-status"))
            assert response.status_code == 200, response.text
            assert response.json()["data"]["status"] == "uploaded"
            assert response.json()["data"]["chunk_count"] == 0
            async with factory() as session:
                assert await session.scalar(select(func.count()).select_from(ChunkModel)) == 0
                embedding_jobs = await session.scalar(
                    select(func.count()).select_from(EmbeddingJobModel)
                )
                assert embedding_jobs == 0
                assert await session.scalar(select(func.count()).select_from(ChatSessionModel)) == 0
                assert await session.scalar(select(func.count()).select_from(ChatMessageModel)) == 0
                uploaded_version = await DocumentRepository(session).get_version(
                    tenant_id="tenant-1", version_id=version_id,
                )
            assert uploaded_version is not None
            assert uploaded_version.document_id == document_id
            assert uploaded_version.status == "uploaded"
            assert vector_store._records == {}
            assert len(upload_storage.calls) == len(ingestion_queue.payloads) == 1
            captured = upload_storage.calls[0]
            assert captured["content"] == DOCUMENT_TEXT.encode()
            assert captured["checksum"] == hashlib.sha256(DOCUMENT_TEXT.encode()).hexdigest()
            assert captured["byte_size"] == len(DOCUMENT_TEXT.encode())
            assert (captured["document_id"], captured["version_id"]) == (document_id, version_id)
            assert uploaded_version.checksum == captured["checksum"]
            assert uploaded_version.byte_size == captured["byte_size"]

            parse_payload = QueuePayload.model_validate(ingestion_queue.payloads[0])
            assert parse_payload.job_type == INGESTION_JOB_TYPE
            assert _queue_identifiers(parse_payload) == {
                "job_id": uploaded["job_id"], "document_id": document_id, "version_id": version_id,
            }
            content = captured["content"]
            assert isinstance(content, bytes)
            storage = FakeStorage(
                content=content, checksum=str(captured["checksum"]),
                byte_size=int(str(captured["byte_size"])),
            )
            with tenant_scope(parse_payload.tenant_id):
                async with factory() as session:
                    parsed = await IngestionParseService(
                        repository=DocumentRepository(session), object_storage=storage,
                        audit=SqlAlchemyAuditPort(session), embedding_queue=embedding_queue,
                        embedding_provider="fake", embedding_model="fake-embedding",
                        embedding_version="fake-v1", embedding_dim=8,
                    ).parse_job(_queue_context(parse_payload), **_queue_identifiers(parse_payload))
            assert parsed.status == "chunked" and parsed.chunk_count == 1
            assert len(storage.calls) == len(embedding_queue.payloads) == 1
            assert storage.calls == [{
                "tenant_id": "tenant-1", "document_id": document_id,
                "version_id": version_id, "object_key": uploaded_version.object_key,
            }]
            response = await client.get(status_url, headers=_headers("chunked-status"))
            assert response.status_code == 200, response.text
            assert response.json()["data"]["status"] == "chunked"
            assert response.json()["data"]["chunk_count"] == 1

            embed_payload = embedding_queue.payloads[0]
            assert embed_payload.job_type == EMBEDDING_JOB_TYPE
            assert _queue_identifiers(embed_payload) == {
                "job_id": parsed.embedding_job_id,
                "document_id": document_id, "version_id": version_id,
            }
            with tenant_scope(embed_payload.tenant_id):
                async with factory() as session:
                    embedded = await EmbeddingJobService(
                        repository=DocumentRepository(session), provider=embedding_provider,
                        vector_store=vector_store, audit=SqlAlchemyAuditPort(session),
                    ).embed_job(_queue_context(embed_payload), **_queue_identifiers(embed_payload))
            assert embedded.status == "embedded" and embedded.chunk_count == 1
            response = await client.get(status_url, headers=_headers("ready-status"))
            assert response.status_code == 200, response.text
            ready = response.json()["data"]
            assert ready["status"] == "retrieval_ready"
            assert ready["chunk_count"] == ready["vector_count"] == 1
            assert ready["embedding_provider"] == "fake" and ready["embedding_dim"] == 8

            async with factory() as session:
                chunks = await DocumentRepository(session).list_chunks_for_version(
                    tenant_id="tenant-1", document_id=document_id, version_id=version_id,
                )
            assert len(chunks) == 1
            chunk = chunks[0]
            assert chunk.content == DOCUMENT_TEXT
            identity = (document_id, version_id, chunk.chunk_id)
            assert (chunk.document_id, chunk.version_id, chunk.chunk_id) == identity
            marker = citation_id(chunk)
            synthetic_answer = f"Synthetic fixture answer: inspect every seven days [{marker}]."
            llm = RecordingFakeLLMProvider(response_text=synthetic_answer)

            response = await client.post(
                "/retrieve", headers=_headers("retrieve"), json={"query": "cobalt inspection"},
            )
            assert response.status_code == 200, response.text
            candidates = response.json()["data"]["candidates"]
            assert [_identity(item) for item in candidates] == [identity]
            _require_hybrid_contributions(candidates[0])
            async with factory() as session:
                log = await RetrievalLogRepository(session).get_by_request_id(
                    tenant_id="tenant-1", request_id="req-journey-retrieve",
                )
            assert log is not None and log.status == "success"
            rrf = log.metadata["rrf"]
            assert isinstance(rrf, Mapping)
            assert rrf["input_counts"] == {"dense": 1, "sparse": 1}

            response = await client.post(
                f"/{stream_kind}/stream", headers=_headers(stream_kind),
                json={"query": "cobalt inspection"},
            )
            final = _successful_stream_final(
                response, request_stage=stream_kind,
                synthetic_answer=synthetic_answer, identity=identity,
            )
            assert len(llm.requests) == 1
            request = llm.requests[0]
            assert (request.tenant_id, request.user_id) == ("tenant-1", "user-1")
            prompt = "\n".join(message.content for message in request.messages)
            assert DOCUMENT_TEXT in prompt and marker in prompt

            citation_to_resolve = final["citations"][0]
            assert citation_to_resolve["citation_id"] == marker
            if stream_kind == "chat":
                session_id = final["session_id"]
                assert isinstance(session_id, str) and session_id
                assert final["metadata"]["session_id"] == session_id
                assert request.session_id == session_id
                assert final["metadata"]["memory"]["message_count"] == 0
                assert final["metadata"]["memory"]["used_count"] == 0
                # Read the actual committed records independently of the API request session.
                async with factory() as session:
                    memory = ChatMemoryRepository(session)
                    stored_session = await memory.get_active_session(
                        tenant_id="tenant-1", user_id="user-1", session_id=session_id,
                    )
                    stored_messages = await memory.list_recent_messages(
                        tenant_id="tenant-1", user_id="user-1", session_id=session_id, limit=50,
                    )
                assert stored_session is not None
                assert stored_session.status == "active" and stored_session.message_count == 2
                assert (stored_session.tenant_id, stored_session.user_id) == ("tenant-1", "user-1")
                assert stored_session.request_id == "req-journey-chat"
                assert [
                    (item.role, item.content, item.sequence_no) for item in stored_messages
                ] == [
                    ("user", "cobalt inspection", 1), ("assistant", synthetic_answer, 2),
                ]
                for item in stored_messages:
                    assert item.status == "active" and item.session_id == session_id
                    assert (item.tenant_id, item.user_id) == ("tenant-1", "user-1")
                    assert (item.request_id, item.trace_id) == ("req-journey-chat", "trace-journey")
                stored_citations = stored_messages[1].metadata["citations"]
                assert isinstance(stored_citations, list | tuple) and len(stored_citations) == 1
                assert isinstance(stored_citations[0], Mapping)
                assert tuple(stored_citations[0][field] for field in IDENTITY_FIELDS) == identity
                assert stored_messages[1].metadata["no_answer"] is False
                assert "error_code" not in stored_messages[1].metadata

                response = await client.get(
                    "/chat/history", headers=_headers("history"), params={"session_id": session_id},
                )
                assert response.status_code == 200, response.text
                assert response.json()["request_id"] == "req-journey-history"
                history = response.json()["data"]
                ChatHistoryResponse.model_validate(history)
                assert history["session_id"] == session_id
                history_messages = history["messages"]
                assert [
                    (item["role"], item["content"], item["sequence_no"])
                    for item in history_messages
                ] == [(item.role, item.content, item.sequence_no) for item in stored_messages]
                for item in history_messages:
                    assert (item["request_id"], item["trace_id"]) == (
                        "req-journey-chat", "trace-journey",
                    )
                    assert item["no_answer"] is False and item["error_code"] is None
                assert history_messages[0]["citations"] == []
                assert [_identity(item) for item in history_messages[1]["citations"]] == [identity]
                citation_to_resolve = history_messages[1]["citations"][0]
                assert citation_to_resolve["citation_id"] == marker

            response = await client.post(
                "/sources/resolve", headers=_headers("resolve"),
                json={field: citation_to_resolve[field] for field in IDENTITY_FIELDS},
            )
            assert response.status_code == 200, response.text
            source = response.json()["data"]
            assert _identity(source) == identity
            assert source["text_excerpt"] == chunk.content == DOCUMENT_TEXT
            assert source["excerpt_char_count"] == len(DOCUMENT_TEXT)

            if stream_kind == "chat":
                # This turn exists only to prove repository-backed prompt continuity.
                second_question = "Which cobalt inspection interval does that policy require?"
                response = await client.post(
                    "/chat/stream", headers=_headers("chat-second"),
                    json={"query": second_question, "session_id": session_id},
                )
                second_final = _successful_stream_final(
                    response, request_stage="chat-second",
                    synthetic_answer=synthetic_answer, identity=identity,
                )
                assert second_final["session_id"] == session_id
                assert second_final["metadata"]["session_id"] == session_id
                assert second_final["metadata"]["memory"]["message_count"] == 2
                assert second_final["metadata"]["memory"]["used_count"] == 2
                assert second_final["metadata"]["memory"]["dropped_count"] == 0
                assert len(llm.requests) == 2
                second_request = llm.requests[1]
                assert second_request.session_id == session_id
                assert (second_request.tenant_id, second_request.user_id) == ("tenant-1", "user-1")
                assert (second_request.request_id, second_request.trace_id) == (
                    "req-journey-chat-second", "trace-journey",
                )
                memory_prompts = [
                    message.content for message in second_request.messages
                    if message.name == "conversation_history"
                ]
                assert len(memory_prompts) == 1
                memory_prompt = memory_prompts[0]
                assert f'session_id="{session_id}"' in memory_prompt
                assert 'message_count="2"' in memory_prompt and 'used_count="2"' in memory_prompt
                assert 'role="user"' in memory_prompt and 'sequence_no="1"' in memory_prompt
                assert 'role="assistant"' in memory_prompt and 'sequence_no="2"' in memory_prompt
                for item in stored_messages:
                    assert item.content_summary in memory_prompt
                assert second_question not in memory_prompt
                assert DOCUMENT_TEXT not in memory_prompt
                second_prompt = "\n".join(message.content for message in second_request.messages)
                assert second_question in second_prompt
                assert DOCUMENT_TEXT in second_prompt and marker in second_prompt

    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(original_overrides)
        await engine.dispose()

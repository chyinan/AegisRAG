"""Actual SQLite pool lifecycle, with fake generation (not a capacity benchmark)."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import apps.api.factories.domain as factories
from packages.common.config import AppSettings
from packages.common.tenant_context import tenant_scope
from packages.data.storage.base import Base
from packages.data.storage.models import ChunkModel
from packages.data.storage.repositories import DocumentRepository
from packages.llm.adapters.fake import FakeLLMProvider
from packages.llm.dto import GenerateChunk, GenerateRequest, GenerateResponse
from packages.memory.storage.models import ChatMessageModel
from packages.rag.dto import QueryCommand
from packages.rag.exceptions import RagQueryError
from packages.rag.storage.session_scoped import SessionFactoryCandidateHydrator
from tests.integration.storage.test_document_repositories import _chunk, _document, _job, _version
from tests.unit.rag.test_query_service import _context


class PausedLLM(FakeLLMProvider):
    def __init__(self) -> None:
        super().__init__(response_text="Policy answer.")
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = asyncio.Event()

    async def generate(self, request: GenerateRequest) -> GenerateResponse:
        self.started.set()
        try:
            await self.release.wait()
            return await super().generate(request)
        finally:
            self.closed.set()

    async def stream(self, request: GenerateRequest) -> AsyncIterator[GenerateChunk]:
        self.started.set()
        try:
            async for chunk in super().stream(request):
                yield chunk
        finally:
            self.closed.set()


@pytest.mark.parametrize("chat", [False, True])
@pytest.mark.parametrize("cancel", [False, True])
async def test_generation_releases_all_read_connections_and_preserves_chat_atomicity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, chat: bool, cancel: bool,
) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'owned.db'}", pool_size=1, max_overflow=0,
        pool_timeout=0.2,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = PausedLLM()
    generator = None
    operation = None
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as seed:
            repo = DocumentRepository(seed)
            await repo.create_upload_records(document=_document(), version=_version(), job=_job())
            await repo.replace_chunks_for_version(
                tenant_id="tenant-1", document_id="doc-1", version_id="ver-1",
                chunks=[_chunk(acl={"visibility": "tenant"})],
            )
            await seed.commit()
        monkeypatch.setattr(factories, "create_session_factory", lambda _url: factory)
        monkeypatch.setattr(factories, "load_settings", lambda: AppSettings(
            VECTOR_STORE_TYPE="pgvector", RETRIEVAL_CACHE_ENABLED=False,
        ))
        monkeypatch.setattr(factories, "create_llm_provider", lambda *_a, **_kw: llm)
        generator = (
            factories.get_chat_application_service() if chat
            else factories.get_rag_query_application_service()
        )
        service = await anext(generator)
        kwargs = {"context": _context(), "command": QueryCommand(query="Policy")}
        operation = asyncio.create_task(
            service.chat(**kwargs, session_id=None) if chat else service.query(**kwargs)
        )
        await asyncio.wait_for(llm.started.wait(), 2)
        assert engine.pool.checkedout() == 0
        # With one physical pool slot, independent work must still get it now.
        async with factory() as observer:
            assert await observer.scalar(text("SELECT 1")) == 1
            messages = list((await observer.scalars(select(ChatMessageModel))).all())
            assert [m.role for m in messages] == (["user"] if chat else [])
        if cancel:
            operation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await operation
        else:
            llm.release.set()
            response = await asyncio.wait_for(operation, 2)
            assert response.tenant_id == "tenant-1" and response.user_id == "user-1"
        assert llm.closed.is_set()
        assert engine.pool.checkedout() == 0
        async with factory() as observer:
            messages = list((await observer.scalars(
                select(ChatMessageModel).order_by(ChatMessageModel.sequence_no),
            )).all())
            assert [m.role for m in messages] == (
                (["user"] if cancel else ["user", "assistant"]) if chat else []
            )
    finally:
        if operation is not None and not operation.done():
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
        if generator is not None:
            await generator.aclose()
        await engine.dispose()


async def test_hydration_closes_own_transaction_and_never_flushes_caller_pending_write(
    tmp_path: Path,
) -> None:
    from tests.unit.rag.test_query_service import _candidate

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'separate.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as caller:
            # New state is deliberately not flushed/committed. The hydrator
            # must not see it, flush it, or change this caller's transaction.
            pending = ChunkModel(
                id="pending", tenant_id="tenant-1", document_id="doc-1", version_id="v1",
                chunk_id="chunk-1", created_by="user-1", status="active", source_type="txt",
                title_path=["Policy"], content="uncommitted", token_count=1,
                acl={"visibility": "tenant"}, checksum="x", section_ids=[], metadata_={},
            )
            caller.add(pending)
            tx = caller.get_transaction()
            with pytest.raises(RagQueryError):
                await SessionFactoryCandidateHydrator(factory).hydrate(
                    candidates=(_candidate(),), auth=_context().auth,
                    request_id="r", trace_id="t",
                )
            assert pending in caller.new and caller.get_transaction() is tx
            async with factory() as observer:
                assert await observer.get(ChunkModel, "pending") is None
            with tenant_scope("different-tenant"):
                with pytest.raises(ValueError, match="tenant"):
                    await SessionFactoryCandidateHydrator(factory).hydrate(
                        candidates=(), auth=_context().auth, request_id="r", trace_id="t",
                    )
            await caller.rollback()
        assert engine.pool.checkedout() == 0
    finally:
        await engine.dispose()


@pytest.mark.parametrize("chat", [False, True])
async def test_sse_slow_reader_and_explicit_close_hold_no_read_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, chat: bool,
) -> None:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'stream.db'}", pool_size=1, max_overflow=0,
        pool_timeout=0.2,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    llm = PausedLLM()
    generator = None
    stream = None
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with factory() as seed:
            repo = DocumentRepository(seed)
            await repo.create_upload_records(document=_document(), version=_version(), job=_job())
            await repo.replace_chunks_for_version(
                tenant_id="tenant-1", document_id="doc-1", version_id="ver-1",
                chunks=[_chunk(acl={"visibility": "tenant"})],
            )
            await seed.commit()
        monkeypatch.setattr(factories, "create_session_factory", lambda _url: factory)
        monkeypatch.setattr(
            factories, "load_settings", lambda: AppSettings(VECTOR_STORE_TYPE="pgvector"),
        )
        monkeypatch.setattr(factories, "create_llm_provider", lambda *_a, **_kw: llm)
        generator = (
            factories.get_chat_application_service() if chat
            else factories.get_rag_query_application_service()
        )
        service = await anext(generator)
        kwargs = {"context": _context(), "command": QueryCommand(query="Policy")}
        stream = (
            service.stream_chat(**kwargs, session_id=None) if chat
            else service.stream_query(**kwargs)
        )
        events = []
        for _ in range(5):
            first = await asyncio.wait_for(anext(stream), 2)
            events.append(first.event)
            assert first.event not in {"error", "final"}
            if first.event == "token":
                break
        assert events == ["citation", "token"]
        assert llm.started.is_set() and engine.pool.checkedout() == 0
        # Pause the consumer while another independent operation uses the only slot.
        async with factory() as observer:
            assert await observer.scalar(text("SELECT 1")) == 1
        await stream.aclose()
        await asyncio.sleep(0)
        assert llm.closed.is_set()
        assert engine.pool.checkedout() == 0
        async with factory() as observer:
            messages = list((await observer.scalars(select(ChatMessageModel))).all())
            assert [m.role for m in messages] == (["user"] if chat else [])
    finally:
        if stream is not None:
            await stream.aclose()
        if generator is not None:
            await generator.aclose()
        await engine.dispose()

"""Keep cancellable Agent/RAG audits off mandatory request transactions."""

from __future__ import annotations

from typing import Literal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import apps.api.factories.domain as domain_factories
from packages.agent.final_answer import StrictFinalAnswerValidator
from packages.agent.runtime import AgentRunConfig, AgentRuntime
from packages.agent.service_token_bridge import ServiceTokenToolBridge
from packages.agent.storage.repositories import AgentRunRepository
from packages.agent.storage.session_scoped import (
    SessionFactoryEvidenceRevalidator,
    SessionFactoryToolCallRecorder,
)
from packages.common.config import AppSettings
from packages.data.storage.audit_repositories import SqlAlchemyAuditPort
from packages.data.storage.auxiliary_audit import SessionFactoryAuditPort
from packages.memory.storage.repositories import ChatMemoryRepository
from packages.rag.auxiliary_audit import BoundedBestEffortAuditPort
from packages.rag.chat import ChatApplicationService
from packages.rag.query import RagQueryApplicationService
from tests.framework_imports import require_langgraph


@pytest.fixture
def session_factory(monkeypatch: pytest.MonkeyPatch) -> async_sessionmaker[AsyncSession]:
    # Composition only: construction must not issue database or provider I/O.
    factory = async_sessionmaker(expire_on_commit=False)
    monkeypatch.setattr(domain_factories, "create_session_factory", lambda _url: factory)
    monkeypatch.setattr(domain_factories, "create_vector_store", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(domain_factories, "create_embedding_provider", lambda **_kwargs: object())
    monkeypatch.setattr(domain_factories, "create_retrieval_cache", lambda **_kwargs: None)
    monkeypatch.setattr(
        domain_factories, "create_retrieval_service",
        lambda **_kwargs: (object(), lambda: {}, None),
    )
    return factory


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_agent_service_and_runtime_share_owned_audit_not_mandatory_session(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    backend: Literal["native", "langgraph"],
) -> None:
    if backend == "langgraph":
        require_langgraph()
    settings = AppSettings(
        AGENT_ORCHESTRATION_BACKEND=backend,
        AGENT_AUXILIARY_IO_TIMEOUT_SECONDS=0.125,
    )
    monkeypatch.setattr(domain_factories, "load_settings", lambda: settings)
    generator = domain_factories.get_agent_run_application_service()
    try:
        service = await anext(generator)
        assert isinstance(service._audit, SessionFactoryAuditPort)
        assert service._audit._session_factory is session_factory
        assert service._auxiliary_io_timeout_seconds == 0.125
        assert isinstance(service._repository, AgentRunRepository)
        request_session = service._repository._session

        runtime = service._runtime_factory(_config(), "run-1")
        _assert_runtime_audit(runtime, service._audit, request_session)
        assert service._stream_runtime_factory is not None
        streaming_runtime = service._stream_runtime_factory(_config(), "stream-run", None)
        _assert_runtime_audit(streaming_runtime, service._audit, request_session)
    finally:
        await generator.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_durable_runtime_retains_owned_audit_composition(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    backend: Literal["native", "langgraph"],
) -> None:
    if backend == "langgraph":
        require_langgraph()
    settings = AppSettings(
        AGENT_DURABLE_ENABLED=True,
        AGENT_ORCHESTRATION_BACKEND=backend,
        AGENT_AUXILIARY_IO_TIMEOUT_SECONDS=0.125,
    )
    monkeypatch.setattr(domain_factories, "load_settings", lambda: settings)
    generator = domain_factories.get_durable_agent_service()
    try:
        service = await anext(generator)
        assert isinstance(service._audit, SessionFactoryAuditPort)
        assert service._audit._session_factory is session_factory
        assert service._store._sessions is session_factory
        runtime = service._runtime_factory(_config(), "run-1")
        recorder = runtime._registry._tool_call_recorder
        assert isinstance(recorder, SessionFactoryToolCallRecorder)
        assert recorder._session_factory is session_factory
        assert isinstance(service._evidence, SessionFactoryEvidenceRevalidator)
        _assert_runtime_audit(runtime, service._audit, None)
    finally:
        await generator.aclose()


@pytest.mark.asyncio
async def test_service_token_isolates_optional_audits_and_retains_required_query_audit(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings(
        AGENT_AUXILIARY_IO_TIMEOUT_SECONDS=0.125, RAG_AUXILIARY_IO_TIMEOUT_SECONDS=0.25,
    )
    monkeypatch.setattr(domain_factories, "load_settings", lambda: settings)
    generator = domain_factories.get_chat_adapter()
    try:
        adapter = await anext(generator)
        bridge = adapter._tool_bridge
        assert isinstance(bridge, ServiceTokenToolBridge)
        assert isinstance(bridge._audit, SessionFactoryAuditPort)
        assert bridge._audit._session_factory is session_factory
        assert bridge._registry._audit is bridge._audit
        assert bridge._registry._auxiliary_io_timeout_seconds == 0.125
        assert bridge._auxiliary_io_timeout_seconds == 0.125

        assert isinstance(bridge._agent_runs, AgentRunRepository)
        assert isinstance(bridge._tool_calls, SessionFactoryToolCallRecorder)
        request_session = bridge._agent_runs._session
        assert bridge._tool_calls._session_factory is session_factory
        recorder = bridge._registry._tool_call_recorder
        assert isinstance(recorder, SessionFactoryToolCallRecorder)
        assert recorder is bridge._tool_calls
        assert recorder._session_factory is session_factory

        chat_service = adapter._chat_service
        assert isinstance(chat_service, ChatApplicationService)
        query = chat_service._rag_query_service
        assert isinstance(query, RagQueryApplicationService)
        assert isinstance(query._audit, SqlAlchemyAuditPort)
        assert query._audit._session is request_session
        assert query._audit._auto_commit
        assert adapter._audit is chat_service._audit is query._stream_audit
        _assert_rag_owned_audit(adapter._audit, session_factory)
        memory_repository = chat_service._memory_service._repository
        assert isinstance(memory_repository, ChatMemoryRepository)
        assert memory_repository._session is request_session
    finally:
        await generator.aclose()


def _assert_runtime_audit(
    runtime: AgentRuntime, audit: SessionFactoryAuditPort, request_session: AsyncSession | None,
) -> None:
    assert runtime._audit is audit
    assert runtime._registry._audit is audit
    assert runtime._config.auxiliary_io_timeout_seconds == 0.125
    assert runtime._registry._auxiliary_io_timeout_seconds == 0.125
    validator = runtime._final_answer_validator
    assert isinstance(validator, StrictFinalAnswerValidator)
    assert validator._audit is audit
    assert validator._auxiliary_io_timeout_seconds == 0.125
    recorder = runtime._registry._tool_call_recorder
    assert isinstance(recorder, SessionFactoryToolCallRecorder)
    assert recorder._session_factory is audit._session_factory
    assert not hasattr(recorder, "_session")


def _config() -> AgentRunConfig:
    return AgentRunConfig(
        max_steps=2, max_tool_calls=1, timeout_seconds=5.0,
        repeated_action_threshold=2, auxiliary_io_timeout_seconds=0.75,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("factory_name", ["query", "chat"])
@pytest.mark.parametrize("llm_adapter", ["native", "langchain"])
@pytest.mark.parametrize("backend", ["native", "langgraph"])
async def test_rag_factories_split_only_optional_audits(
    session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    factory_name: str,
    llm_adapter: Literal["native", "langchain"],
    backend: Literal["native", "langgraph"],
) -> None:
    settings = AppSettings(
        LLM_ADAPTER=llm_adapter,
        LLM_PROVIDER="openai_compatible",
        LLM_BASE_URL="https://unused.invalid/v1",
        LLM_API_KEY="synthetic-test-only",
        AGENT_ORCHESTRATION_BACKEND=backend,
        RAG_AUXILIARY_IO_TIMEOUT_SECONDS=0.25,
    )
    monkeypatch.setattr(domain_factories, "load_settings", lambda: settings)
    monkeypatch.setattr(
        domain_factories, "create_llm_provider", lambda _settings, **_kwargs: object(),
    )
    generator = (
        domain_factories.get_rag_query_application_service()
        if factory_name == "query" else domain_factories.get_chat_application_service()
    )
    try:
        service = await anext(generator)
        if isinstance(service, ChatApplicationService):
            query = service._rag_query_service
            assert isinstance(query, RagQueryApplicationService)
            assert service._audit is query._stream_audit
            assert isinstance(query._audit, SqlAlchemyAuditPort)
            memory_repository = service._memory_service._repository
            assert isinstance(memory_repository, ChatMemoryRepository)
            assert query._audit._session is memory_repository._session
        else:
            query = service
        assert isinstance(query, RagQueryApplicationService)
        assert isinstance(query._audit, SqlAlchemyAuditPort)
        assert query._audit._auto_commit
        _assert_rag_owned_audit(query._stream_audit, session_factory)
        assert query._audit is not query._stream_audit
    finally:
        await generator.aclose()


def _assert_rag_owned_audit(
    audit: object, session_factory: async_sessionmaker[AsyncSession],
) -> None:
    assert isinstance(audit, BoundedBestEffortAuditPort)
    assert audit._timeout_seconds == 0.25
    assert isinstance(audit._owned_audit, SessionFactoryAuditPort)
    assert audit._owned_audit._session_factory is session_factory

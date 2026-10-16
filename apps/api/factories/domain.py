"""Domain service factories: document, RAG, chat, agent, ServiceToken.

Extracted from service_dependencies.py as part of DI decoupling (T1 finding).
Adds: CoT prompt enhancer, query rewriter (T2 Phase 1 P0).
"""
from __future__ import annotations

# pattern: Imperative Shell
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Protocol

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.factories.agent import create_agent_runtime, create_agent_stepper
from apps.api.factories.common import (
    create_embedding_provider,
    create_llm_provider,
    create_session_factory,
    create_vector_store,
)
from apps.api.factories.retrieval import (
    create_retrieval_cache,
    create_retrieval_service,
)
from packages.agent import AgentActionType, AgentRuntimeState, AgentStepDecision
from packages.agent.adapters.session_scoped import (
    SessionFactoryEvidenceRevalidator,
    SessionFactoryRetrievalApplication,
    SessionFactoryToolCallRecorder,
)
from packages.agent.dto import ToolCallRecorderPort, ToolRateLimit
from packages.agent.durable.dto import durable_error
from packages.agent.durable.service import DurableAgentService
from packages.agent.final_answer import StrictFinalAnswerValidator
from packages.agent.registry import ToolRegistry
from packages.agent.runtime import AgentEventSink, AgentRunConfig, AgentRuntime
from packages.agent.service import AgentRunApplicationService
from packages.agent.service_token_bridge import ServiceTokenToolBridge
from packages.agent.storage.durable import SqlAlchemyDurableStore
from packages.agent.storage.repositories import AgentRunRepository
from packages.agent.tools.calculator import build_calculator_tool
from packages.agent.tools.file_reader import build_file_reader_tool
from packages.agent.tools.rag_search import RetrievalApplication, build_rag_search_tool
from packages.common.audit import AuditPort
from packages.common.config import AppSettings, load_settings
from packages.data.adapters.minio_object_storage import MinioObjectStorage
from packages.data.lifecycle import DocumentLifecycleService
from packages.data.queue.adapters import RQEmbeddingJobQueue, RQIngestionJobQueue
from packages.data.service import DocumentUploadService, FolderImportService
from packages.data.storage.audit_repositories import SqlAlchemyAuditPort
from packages.data.storage.auxiliary_audit import SessionFactoryAuditPort
from packages.data.storage.repositories import DocumentRepository
from packages.ingestion.recovery import HandoffRecoveryService
from packages.memory import ChatMemoryService
from packages.memory.storage.repositories import ChatMemoryRepository
from packages.rag import (
    ChatApplicationService,
    CitationExtractor,
    ContextPacker,
    PromptBuilder,
    RagGenerationService,
    RagQueryApplicationService,
    RetrievalCandidateHydrator,
    ServiceTokenChatAdapter,
)
from packages.rag.auxiliary_audit import BoundedBestEffortAuditPort
from packages.rag.cot_prompt import CoTPromptEnhancer
from packages.retrieval.application import RetrieveApplicationService
from packages.retrieval.storage.repositories import RetrievalLogRepository

# ── Document Upload ──────────────────────────────────────────────

async def get_document_upload_service() -> AsyncIterator[DocumentUploadService]:
    settings = load_settings()
    session_factory = create_session_factory(settings.database_url)
    async with session_factory() as session:
        yield DocumentUploadService(
            object_storage=MinioObjectStorage.from_settings(settings),
            repository=DocumentRepository(session),
            job_queue=RQIngestionJobQueue.from_settings(settings),
            audit=SqlAlchemyAuditPort(session),
            max_upload_bytes=settings.upload_max_bytes,
            queue_name=settings.ingestion_queue_name,
        )


DocumentUploadServiceDep = Annotated[
    DocumentUploadService, Depends(get_document_upload_service)
]


def get_folder_import_service(
    upload_service: DocumentUploadServiceDep,
) -> FolderImportService:
    return FolderImportService(upload_service=upload_service)


FolderImportServiceDep = Annotated[
    FolderImportService, Depends(get_folder_import_service)
]


async def get_document_lifecycle_service() -> AsyncIterator[DocumentLifecycleService]:
    settings = load_settings()
    session_factory = create_session_factory(settings.database_url)
    async with session_factory() as session:
        yield DocumentLifecycleService(
            repository=DocumentRepository(session),
            vector_store=create_vector_store(
                settings.vector_store_type,
                settings.vector_index_dim,
                session,
                milvus_uri=settings.milvus_uri,
                milvus_token=settings.milvus_token,
                milvus_index_type=settings.milvus_index_type,
            ),
            audit=SqlAlchemyAuditPort(session),
        )


DocumentLifecycleServiceDep = Annotated[
    DocumentLifecycleService, Depends(get_document_lifecycle_service)
]


async def get_handoff_recovery_service() -> AsyncIterator[HandoffRecoveryService]:
    settings = load_settings()
    session_factory = create_session_factory(settings.database_url)
    async with session_factory() as session:
        yield HandoffRecoveryService(
            repository=DocumentRepository(session),
            queue=RQEmbeddingJobQueue.from_settings(settings),
            audit=SqlAlchemyAuditPort(session),
        )


HandoffRecoveryServiceDep = Annotated[
    HandoffRecoveryService, Depends(get_handoff_recovery_service)
]


# ── Retrieval ────────────────────────────────────────────────────

async def get_retrieve_application_service() -> AsyncIterator[RetrieveApplicationService]:
    settings = load_settings()
    session_factory = create_session_factory(settings.database_url)
    async with session_factory() as session:
        retrieval_service, pipeline_trace_provider, _cache = create_retrieval_service(
            settings=settings,
            session=session,
            vector_store=create_vector_store(
                settings.vector_store_type,
                settings.vector_index_dim,
                session,
                milvus_uri=settings.milvus_uri,
                milvus_token=settings.milvus_token,
                milvus_index_type=settings.milvus_index_type,
            ),
            embedding_provider=create_embedding_provider(
                adapter=settings.embedding_adapter,
                provider=settings.embedding_provider,
                model=settings.embedding_model,
                dim=settings.embedding_dim,
                base_url=settings.embedding_base_url,
                api_key=settings.embedding_api_key,
                version=settings.embedding_provider_version,
            ),
            retrieval_cache=create_retrieval_cache(redis_url=settings.redis_url),
        )
        yield RetrieveApplicationService(
            retrieval_service=retrieval_service,
            retrieval_log=RetrievalLogRepository(session),
            audit=SqlAlchemyAuditPort(session),
            pipeline_trace_provider=pipeline_trace_provider,
        )


RetrieveApplicationServiceDep = Annotated[
    RetrieveApplicationService, Depends(get_retrieve_application_service)
]


# ── RAG Query ────────────────────────────────────────────────────

async def get_rag_query_application_service() -> AsyncIterator[RagQueryApplicationService]:
    settings = load_settings()
    session_factory = create_session_factory(settings.database_url)
    auxiliary_audit = BoundedBestEffortAuditPort(
        owned_audit=SessionFactoryAuditPort(session_factory),
        timeout_seconds=settings.rag_auxiliary_io_timeout_seconds,
    )
    async with session_factory() as session:
        retrieval_service, _pipeline_trace, _cache = create_retrieval_service(
            settings=settings,
            session=session,
            vector_store=create_vector_store(
                settings.vector_store_type,
                settings.vector_index_dim,
                session,
                milvus_uri=settings.milvus_uri,
                milvus_token=settings.milvus_token,
                milvus_index_type=settings.milvus_index_type,
            ),
            embedding_provider=create_embedding_provider(
                adapter=settings.embedding_adapter,
                provider=settings.embedding_provider,
                model=settings.embedding_model,
                dim=settings.embedding_dim,
                base_url=settings.embedding_base_url,
                api_key=settings.embedding_api_key,
                version=settings.embedding_provider_version,
            ),
            retrieval_cache=create_retrieval_cache(redis_url=settings.redis_url),
        )
        llm_provider = create_llm_provider(settings, operation="rag.query")
        yield RagQueryApplicationService(
            retrieval_service=retrieval_service,
            hydrator=RetrievalCandidateHydrator(repository=DocumentRepository(session)),
            context_packer=ContextPacker(),
            prompt_builder=PromptBuilder(),
            generation_service=RagGenerationService(
                provider=llm_provider,
                provider_name=settings.llm_provider,
                model=settings.llm_model,
                timeout_seconds=settings.llm_timeout_seconds,
                retry_budget=settings.llm_retry_budget,
                temperature=settings.llm_temperature,
                max_output_tokens=settings.llm_max_output_tokens,
            ),
            citation_extractor=CitationExtractor(),
            cot_enhancer=CoTPromptEnhancer(
                enable_cot=settings.cot_enabled,
                enable_few_shot=settings.few_shot_enabled,
            ),
            audit=SqlAlchemyAuditPort(session, auto_commit=True),
            stream_audit=auxiliary_audit,
        )


RagQueryApplicationServiceDep = Annotated[
    RagQueryApplicationService, Depends(get_rag_query_application_service)
]


# ── Chat ─────────────────────────────────────────────────────────

async def get_chat_application_service() -> AsyncIterator[ChatApplicationService]:
    settings = load_settings()
    session_factory = create_session_factory(settings.database_url)
    auxiliary_audit = BoundedBestEffortAuditPort(
        owned_audit=SessionFactoryAuditPort(session_factory),
        timeout_seconds=settings.rag_auxiliary_io_timeout_seconds,
    )
    async with session_factory() as session:
        retrieval_service, _pipeline_trace, _cache = create_retrieval_service(
            settings=settings,
            session=session,
            vector_store=create_vector_store(
                settings.vector_store_type,
                settings.vector_index_dim,
                session,
                milvus_uri=settings.milvus_uri,
                milvus_token=settings.milvus_token,
                milvus_index_type=settings.milvus_index_type,
            ),
            embedding_provider=create_embedding_provider(
                adapter=settings.embedding_adapter,
                provider=settings.embedding_provider,
                model=settings.embedding_model,
                dim=settings.embedding_dim,
                base_url=settings.embedding_base_url,
                api_key=settings.embedding_api_key,
                version=settings.embedding_provider_version,
            ),
            retrieval_cache=create_retrieval_cache(redis_url=settings.redis_url),
        )
        llm_provider = create_llm_provider(settings, operation="chat")
        rag_query_service = RagQueryApplicationService(
            retrieval_service=retrieval_service,
            hydrator=RetrievalCandidateHydrator(repository=DocumentRepository(session)),
            context_packer=ContextPacker(),
            prompt_builder=PromptBuilder(),
            generation_service=RagGenerationService(
                provider=llm_provider,
                provider_name=settings.llm_provider,
                model=settings.llm_model,
                timeout_seconds=settings.llm_timeout_seconds,
                retry_budget=settings.llm_retry_budget,
                temperature=settings.llm_temperature,
                max_output_tokens=settings.llm_max_output_tokens,
            ),
            citation_extractor=CitationExtractor(),
            cot_enhancer=CoTPromptEnhancer(
                enable_cot=settings.cot_enabled,
                enable_few_shot=settings.few_shot_enabled,
            ),
            audit=SqlAlchemyAuditPort(session, auto_commit=True),
            stream_audit=auxiliary_audit,
        )
        yield ChatApplicationService(
            memory_service=ChatMemoryService(repository=ChatMemoryRepository(session)),
            rag_query_service=rag_query_service,
            audit=auxiliary_audit,
        )


ChatApplicationServiceDep = Annotated[
    ChatApplicationService, Depends(get_chat_application_service)
]


# ── ServiceToken Adapter ────────────────────────────────────────────

async def get_chat_adapter() -> AsyncIterator[ServiceTokenChatAdapter]:
    settings = load_settings()
    session_factory = create_session_factory(settings.database_url)
    auxiliary_audit = BoundedBestEffortAuditPort(
        owned_audit=SessionFactoryAuditPort(session_factory),
        timeout_seconds=settings.rag_auxiliary_io_timeout_seconds,
    )
    async with session_factory() as session:
        retrieval_service, _pipeline_trace, _cache = create_retrieval_service(
            settings=settings,
            session=session,
            vector_store=create_vector_store(
                settings.vector_store_type,
                settings.vector_index_dim,
                session,
                milvus_uri=settings.milvus_uri,
                milvus_token=settings.milvus_token,
                milvus_index_type=settings.milvus_index_type,
            ),
            embedding_provider=create_embedding_provider(
                adapter=settings.embedding_adapter,
                provider=settings.embedding_provider,
                model=settings.embedding_model,
                dim=settings.embedding_dim,
                base_url=settings.embedding_base_url,
                api_key=settings.embedding_api_key,
                version=settings.embedding_provider_version,
            ),
            retrieval_cache=create_retrieval_cache(redis_url=settings.redis_url),
        )
        llm_provider = create_llm_provider(settings, operation="service_token.chat")
        rag_query_service = RagQueryApplicationService(
            retrieval_service=retrieval_service,
            hydrator=RetrievalCandidateHydrator(repository=DocumentRepository(session)),
            context_packer=ContextPacker(),
            prompt_builder=PromptBuilder(),
            generation_service=RagGenerationService(
                provider=llm_provider,
                provider_name=settings.llm_provider,
                model=settings.llm_model,
                timeout_seconds=settings.llm_timeout_seconds,
                retry_budget=settings.llm_retry_budget,
                temperature=settings.llm_temperature,
                max_output_tokens=settings.llm_max_output_tokens,
            ),
            citation_extractor=CitationExtractor(),
            cot_enhancer=CoTPromptEnhancer(
                enable_cot=settings.cot_enabled,
                enable_few_shot=settings.few_shot_enabled,
            ),
            audit=SqlAlchemyAuditPort(session, auto_commit=True),
            stream_audit=auxiliary_audit,
        )
        chat_service = ChatApplicationService(
            memory_service=ChatMemoryService(repository=ChatMemoryRepository(session)),
            rag_query_service=rag_query_service,
            audit=auxiliary_audit,
        )
        agent_audit = SessionFactoryAuditPort(session_factory)
        tool_call_repository = SessionFactoryToolCallRecorder(session_factory)
        agent_run_repository = AgentRunRepository(session)
        registry = _build_agent_tool_registry(
            settings=settings,
            audit=agent_audit,
            tool_call_repository=tool_call_repository,
            retrieve_application_service=_owned_agent_retrieval(
                settings=settings, session_factory=session_factory,
            ),
        )
        yield ServiceTokenChatAdapter(
            chat_service=chat_service,
            tool_bridge=ServiceTokenToolBridge(
                registry=registry,
                agent_runs=agent_run_repository,
                tool_calls=tool_call_repository,
                audit=agent_audit,
                auxiliary_io_timeout_seconds=settings.agent_auxiliary_io_timeout_seconds,
            ),
            model_id=settings.llm_model,
            owned_by=settings.llm_provider,
            audit=auxiliary_audit,
        )


ServiceTokenChatAdapterDep = Annotated[
    ServiceTokenChatAdapter, Depends(get_chat_adapter)
]


# ── Agent ────────────────────────────────────────────────────────

async def get_agent_run_application_service() -> AsyncIterator[AgentRunApplicationService]:
    settings = load_settings()
    session_factory = create_session_factory(settings.database_url)
    async with session_factory() as session:
        audit, build_runtime = _agent_runtime_components(
            settings=settings, session_factory=session_factory,
        )
        yield AgentRunApplicationService(
            repository=AgentRunRepository(session),
            runtime_factory=build_runtime,
            stream_runtime_factory=build_runtime,
            audit=audit,
            default_max_steps=settings.agent_default_max_steps,
            default_max_tool_calls=settings.agent_default_max_tool_calls,
            default_timeout_seconds=settings.agent_default_timeout_seconds,
            repeated_action_threshold=settings.agent_repeated_action_threshold,
            auxiliary_io_timeout_seconds=settings.agent_auxiliary_io_timeout_seconds,
            persistence_phase_timeout_seconds=settings.agent_persistence_phase_timeout_seconds,
        )


AgentRunApplicationServiceDep = Annotated[
    AgentRunApplicationService, Depends(get_agent_run_application_service)
]


async def get_durable_agent_service() -> AsyncIterator[DurableAgentService]:
    # Import lazily to keep dependency registration acyclic.
    from apps.api.dependencies import revalidate_authenticated_context

    settings = load_settings()
    if not settings.agent_durable_enabled:
        raise durable_error("DURABLE_AGENT_DISABLED", status_code=503)
    session_factory = create_session_factory(settings.database_url)
    audit, build_runtime = _agent_runtime_components(
        settings=settings, session_factory=session_factory,
    )
    yield DurableAgentService(
        store=SqlAlchemyDurableStore(session_factory),
        runtime_factory=build_runtime,
        evidence_revalidator=SessionFactoryEvidenceRevalidator(session_factory),
        context_revalidator=revalidate_authenticated_context,
        audit=audit,
        backend=settings.agent_orchestration_backend,
        background_enabled=settings.agent_durable_background_enabled,
        default_max_steps=settings.agent_default_max_steps,
        default_max_tool_calls=settings.agent_default_max_tool_calls,
        default_timeout_seconds=settings.agent_default_timeout_seconds,
        repeated_action_threshold=settings.agent_repeated_action_threshold,
        retention_seconds=settings.agent_durable_retention_seconds,
        lease_seconds=settings.agent_durable_lease_seconds,
    )

DurableAgentServiceDep = Annotated[DurableAgentService, Depends(get_durable_agent_service)]


class _ConfiguredRuntimeFactory(Protocol):
    def __call__(
        self, config: AgentRunConfig, agent_run_id: str,
        event_sink: AgentEventSink | None = None,
    ) -> AgentRuntime: ...


def _agent_runtime_components(
    *, settings: AppSettings,
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[AuditPort, _ConfiguredRuntimeFactory]:
    audit = SessionFactoryAuditPort(session_factory)
    registry = _build_agent_tool_registry(
        settings=settings,
        audit=audit,
        tool_call_repository=SessionFactoryToolCallRecorder(session_factory),
        retrieve_application_service=_owned_agent_retrieval(
            settings=settings, session_factory=session_factory,
        ),
    )
    def build_runtime(
        config: AgentRunConfig,
        agent_run_id: str,
        event_sink: AgentEventSink | None = None,
    ) -> AgentRuntime:
        return create_agent_runtime(
            backend=settings.agent_orchestration_backend,
            registry=registry,
            stepper=(
                _DeterministicAgentStepper()
                if settings.llm_provider == "fake"
                else create_agent_stepper(
                    settings=settings,
                    llm_provider=create_llm_provider(settings, operation="agent"),
                    registry=registry,
                )
            ),
            audit=audit,
            config=config.model_copy(update={
                "auxiliary_io_timeout_seconds": settings.agent_auxiliary_io_timeout_seconds,
            }),
            agent_run_id=agent_run_id,
            final_answer_validator=StrictFinalAnswerValidator(
                audit=audit,
                auxiliary_io_timeout_seconds=settings.agent_auxiliary_io_timeout_seconds,
            ),
            event_sink=event_sink,
        )

    return audit, build_runtime


def _owned_agent_retrieval(
    *, settings: AppSettings, session_factory: async_sessionmaker[AsyncSession],
) -> SessionFactoryRetrievalApplication:
    def build(session: AsyncSession) -> RetrieveApplicationService:
        retrieval_service, pipeline_trace, _cache = create_retrieval_service(
            settings=settings,
            session=session,
            vector_store=create_vector_store(
                settings.vector_store_type,
                settings.vector_index_dim,
                session,
                milvus_uri=settings.milvus_uri,
                milvus_token=settings.milvus_token,
                milvus_index_type=settings.milvus_index_type,
            ),
            embedding_provider=create_embedding_provider(
                adapter=settings.embedding_adapter,
                provider=settings.embedding_provider,
                model=settings.embedding_model,
                dim=settings.embedding_dim,
                base_url=settings.embedding_base_url,
                api_key=settings.embedding_api_key,
                version=settings.embedding_provider_version,
            ),
            retrieval_cache=create_retrieval_cache(redis_url=settings.redis_url),
        )
        return RetrieveApplicationService(
            retrieval_service=retrieval_service,
            retrieval_log=RetrievalLogRepository(session),
            audit=SqlAlchemyAuditPort(session),
            pipeline_trace_provider=pipeline_trace,
            content_hydrator=RetrievalCandidateHydrator(repository=DocumentRepository(session)),
        )

    return SessionFactoryRetrievalApplication(session_factory=session_factory, build_service=build)


# ── Helpers ──────────────────────────────────────────────────────

def _build_agent_tool_registry(
    *,
    settings: AppSettings,
    audit: SqlAlchemyAuditPort | AuditPort,
    tool_call_repository: ToolCallRecorderPort,
    retrieve_application_service: RetrievalApplication,
) -> ToolRegistry:
    registry = ToolRegistry(
        audit=audit, tool_call_recorder=tool_call_repository,
        auxiliary_io_timeout_seconds=settings.agent_auxiliary_io_timeout_seconds,
    )
    rate_limit = ToolRateLimit(
        max_calls=settings.tool_default_rate_limit_max_calls,
        window_seconds=settings.tool_default_rate_limit_window_seconds,
    )
    registry.register(
        build_rag_search_tool(
            retrieval_app=retrieve_application_service,
            timeout_seconds=settings.tool_default_timeout_seconds,
            rate_limit=rate_limit,
        )
    )
    registry.register(
        build_calculator_tool(
            timeout_seconds=settings.tool_default_timeout_seconds,
            rate_limit=rate_limit,
        )
    )
    registry.register(
        build_file_reader_tool(
            allowlist_roots=_file_reader_allowlist_roots(
                settings.file_reader_allowlist_roots
            ),
            max_file_bytes=settings.file_reader_max_file_bytes,
            max_return_bytes=settings.file_reader_max_return_bytes,
            timeout_seconds=settings.tool_default_timeout_seconds,
            rate_limit=rate_limit,
        )
    )
    if settings.support_ticket_enabled:
        from apps.api.factories.support_tickets import create_support_ticket_service
        from packages.agent.tools.support_ticket import build_support_ticket_tool

        registry.register(build_support_ticket_tool(
            service=create_support_ticket_service(
                settings, create_session_factory(settings.database_url),
            ),
            timeout_seconds=settings.tool_default_timeout_seconds,
            rate_limit=rate_limit,
        ))
    return registry


def _file_reader_allowlist_roots(value: str) -> tuple[Path, ...]:
    roots = tuple(
        (
            Path(item.strip())
            if Path(item.strip()).is_absolute()
            else Path.cwd() / item.strip()
        )
        for item in value.split(",")
        if item.strip()
    )
    if not roots:
        return (Path.cwd() / "docs",)
    return roots


class _DeterministicAgentStepper:
    async def next_step(self, state: AgentRuntimeState) -> AgentStepDecision:
        _ = state
        return AgentStepDecision(
            action=AgentActionType.FINAL_ANSWER,
            final_answer="Agent run accepted for governed execution.",
        )

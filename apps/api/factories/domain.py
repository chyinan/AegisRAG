"""Domain service factories: document, RAG, chat, agent, OpenWebUI.

Extracted from service_dependencies.py as part of DI decoupling (T1 finding).
Adds: CoT prompt enhancer, query rewriter (T2 Phase 1 P0).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated

from fastapi import Depends

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
from packages.agent import AgentActionType, AgentRuntime, AgentRuntimeState, AgentStepDecision
from packages.agent.dto import ToolCallRecorderPort, ToolRateLimit
from packages.agent.final_answer import StrictFinalAnswerValidator
from packages.agent.openwebui_bridge import OpenWebUIToolBridge
from packages.agent.registry import ToolRegistry
from packages.agent.service import AgentRunApplicationService
from packages.agent.storage.repositories import AgentRunRepository, ToolCallRepository
from packages.agent.tools.calculator import build_calculator_tool
from packages.agent.tools.file_reader import build_file_reader_tool
from packages.agent.tools.rag_search import build_rag_search_tool
from packages.common.audit import AuditPort
from packages.common.config import AppSettings, load_settings
from packages.data.adapters.minio_object_storage import MinioObjectStorage
from packages.data.lifecycle import DocumentLifecycleService
from packages.data.queue.adapters import RQIngestionJobQueue
from packages.data.service import DocumentUploadService
from packages.data.storage.audit_repositories import SqlAlchemyAuditPort
from packages.data.storage.repositories import DocumentRepository
from packages.memory import ChatMemoryService
from packages.memory.storage.repositories import ChatMemoryRepository
from packages.rag import (
    ChatApplicationService,
    CitationExtractor,
    ContextPacker,
    OpenWebUIChatAdapter,
    PromptBuilder,
    RagGenerationService,
    RagQueryApplicationService,
    RetrievalCandidateHydrator,
)
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
                provider=settings.embedding_provider,
                model=settings.embedding_model,
                dim=settings.embedding_dim,
                base_url=settings.embedding_base_url,
                api_key=settings.embedding_api_key,
                version=settings.embedding_provider_version,
            ),
            retrieval_cache=create_retrieval_cache(redis_url=settings.redis_url),
        )
        llm_provider = create_llm_provider(settings)
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
        )


RagQueryApplicationServiceDep = Annotated[
    RagQueryApplicationService, Depends(get_rag_query_application_service)
]


# ── Chat ─────────────────────────────────────────────────────────

async def get_chat_application_service() -> AsyncIterator[ChatApplicationService]:
    settings = load_settings()
    session_factory = create_session_factory(settings.database_url)
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
                provider=settings.embedding_provider,
                model=settings.embedding_model,
                dim=settings.embedding_dim,
                base_url=settings.embedding_base_url,
                api_key=settings.embedding_api_key,
                version=settings.embedding_provider_version,
            ),
            retrieval_cache=create_retrieval_cache(redis_url=settings.redis_url),
        )
        llm_provider = create_llm_provider(settings)
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
        )
        yield ChatApplicationService(
            memory_service=ChatMemoryService(repository=ChatMemoryRepository(session)),
            rag_query_service=rag_query_service,
            audit=SqlAlchemyAuditPort(session, auto_commit=True),
        )


ChatApplicationServiceDep = Annotated[
    ChatApplicationService, Depends(get_chat_application_service)
]


# ── OpenWebUI Adapter ────────────────────────────────────────────

async def get_openwebui_chat_adapter() -> AsyncIterator[OpenWebUIChatAdapter]:
    settings = load_settings()
    session_factory = create_session_factory(settings.database_url)
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
                provider=settings.embedding_provider,
                model=settings.embedding_model,
                dim=settings.embedding_dim,
                base_url=settings.embedding_base_url,
                api_key=settings.embedding_api_key,
                version=settings.embedding_provider_version,
            ),
            retrieval_cache=create_retrieval_cache(redis_url=settings.redis_url),
        )
        retrieve_application_service = RetrieveApplicationService(
            retrieval_service=retrieval_service,
            retrieval_log=RetrievalLogRepository(session),
            audit=SqlAlchemyAuditPort(session),
            pipeline_trace_provider=lambda: {},
        )
        llm_provider = create_llm_provider(settings)
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
        )
        chat_service = ChatApplicationService(
            memory_service=ChatMemoryService(repository=ChatMemoryRepository(session)),
            rag_query_service=rag_query_service,
            audit=SqlAlchemyAuditPort(session, auto_commit=True),
        )
        audit = SqlAlchemyAuditPort(session, auto_commit=True)
        tool_call_repository = ToolCallRepository(session)
        agent_run_repository = AgentRunRepository(session)
        registry = _build_agent_tool_registry(
            settings=settings,
            audit=audit,
            tool_call_repository=tool_call_repository,
            retrieve_application_service=retrieve_application_service,
        )
        yield OpenWebUIChatAdapter(
            chat_service=chat_service,
            tool_bridge=OpenWebUIToolBridge(
                registry=registry,
                agent_runs=agent_run_repository,
                tool_calls=tool_call_repository,
                audit=audit,
            ),
            model_id=settings.llm_model,
            owned_by=settings.llm_provider,
            audit=audit,
        )


OpenWebUIChatAdapterDep = Annotated[
    OpenWebUIChatAdapter, Depends(get_openwebui_chat_adapter)
]


# ── Agent ────────────────────────────────────────────────────────

async def get_agent_run_application_service() -> AsyncIterator[AgentRunApplicationService]:
    settings = load_settings()
    session_factory = create_session_factory(settings.database_url)
    async with session_factory() as session:
        audit = SqlAlchemyAuditPort(session, auto_commit=True)
        tool_call_repository = ToolCallRepository(session)
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
                provider=settings.embedding_provider,
                model=settings.embedding_model,
                dim=settings.embedding_dim,
                base_url=settings.embedding_base_url,
                api_key=settings.embedding_api_key,
                version=settings.embedding_provider_version,
            ),
            retrieval_cache=create_retrieval_cache(redis_url=settings.redis_url),
        )
        retrieve_application_service = RetrieveApplicationService(
            retrieval_service=retrieval_service,
            retrieval_log=RetrievalLogRepository(session),
            audit=SqlAlchemyAuditPort(session),
            pipeline_trace_provider=pipeline_trace,
        )
        registry = _build_agent_tool_registry(
            settings=settings,
            audit=audit,
            tool_call_repository=tool_call_repository,
            retrieve_application_service=retrieve_application_service,
        )
        yield AgentRunApplicationService(
            repository=AgentRunRepository(session),
            runtime_factory=lambda config, agent_run_id: AgentRuntime(
                registry=registry,
                stepper=_DeterministicAgentStepper(),
                audit=audit,
                config=config,
                agent_run_id=agent_run_id,
                final_answer_validator=StrictFinalAnswerValidator(audit=audit),
            ),
            audit=audit,
            default_max_steps=settings.agent_default_max_steps,
            default_max_tool_calls=settings.agent_default_max_tool_calls,
            default_timeout_seconds=settings.agent_default_timeout_seconds,
            repeated_action_threshold=settings.agent_repeated_action_threshold,
        )


AgentRunApplicationServiceDep = Annotated[
    AgentRunApplicationService, Depends(get_agent_run_application_service)
]


# ── Helpers ──────────────────────────────────────────────────────

def _build_agent_tool_registry(
    *,
    settings: AppSettings,
    audit: SqlAlchemyAuditPort | AuditPort,
    tool_call_repository: ToolCallRecorderPort,
    retrieve_application_service: RetrieveApplicationService,
) -> ToolRegistry:
    registry = ToolRegistry(audit=audit, tool_call_recorder=tool_call_repository)
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

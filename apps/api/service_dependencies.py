from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
from packages.audit import AuditExplorerService
from packages.common.audit import AuditPort
from packages.common.config import AppSettings, load_settings
from packages.data.adapters.minio_object_storage import MinioObjectStorage
from packages.data.lifecycle import DocumentLifecycleService
from packages.data.queue.adapters import RQIngestionJobQueue
from packages.data.service import DocumentUploadService
from packages.data.storage.audit_repositories import AuditLogRepository, SqlAlchemyAuditPort
from packages.data.storage.exceptions import StorageConfigurationError
from packages.data.storage.repositories import DocumentRepository
from packages.data.storage.review_repositories import ReviewItemRepository
from packages.data.storage.session import create_async_db_engine, create_session_factory
from packages.diagnostics import DiagnosticsService
from packages.embeddings.adapters.fake import FakeEmbeddingProvider
from packages.eval import EvalEvidenceService
from packages.llm.adapters.fake import FakeLLMProvider
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
    SourceResolveService,
)
from packages.retrieval.application import RetrieveApplicationService
from packages.retrieval.dense import DenseRetriever, DenseRetrieverConfig
from packages.retrieval.rerank import FakeReranker, RerankConfig, RerankingRetriever
from packages.retrieval.rrf import HybridMergeConfig, HybridRetriever, RRFMerger
from packages.retrieval.service import RetrievalService
from packages.retrieval.sparse import PostgresSparseRetriever, SparseRetrieverConfig
from packages.retrieval.storage.repositories import RetrievalLogRepository
from packages.review import ReviewQueueService
from packages.vectorstores.adapters.fake import FakeVectorStore
from packages.vectorstores.adapters.pgvector import PgVectorStore
from packages.vectorstores.dto import DistanceMetric
from packages.vectorstores.ports import VectorStore


async def get_document_upload_service() -> AsyncIterator[DocumentUploadService]:
    settings = load_settings()
    session_factory = _session_factory(settings.database_url)
    async with session_factory() as session:
        yield DocumentUploadService(
            object_storage=MinioObjectStorage.from_settings(settings),
            repository=DocumentRepository(session),
            job_queue=RQIngestionJobQueue.from_settings(settings),
            audit=SqlAlchemyAuditPort(session),
            max_upload_bytes=settings.upload_max_bytes,
            queue_name=settings.ingestion_queue_name,
        )


DocumentUploadServiceDep = Annotated[DocumentUploadService, Depends(get_document_upload_service)]


async def get_document_lifecycle_service() -> AsyncIterator[DocumentLifecycleService]:
    settings = load_settings()
    session_factory = _session_factory(settings.database_url)
    async with session_factory() as session:
        yield DocumentLifecycleService(
            repository=DocumentRepository(session),
            vector_store=_vector_store_from_settings(
                settings.vector_store_type,
                settings.vector_index_dim,
                session,
            ),
            audit=SqlAlchemyAuditPort(session),
        )


DocumentLifecycleServiceDep = Annotated[
    DocumentLifecycleService,
    Depends(get_document_lifecycle_service),
]


async def get_retrieve_application_service() -> AsyncIterator[RetrieveApplicationService]:
    settings = load_settings()
    session_factory = _session_factory(settings.database_url)
    async with session_factory() as session:
        retrieval_service, pipeline_trace_provider = _retrieval_service_from_settings(
            settings=settings,
            session=session,
        )
        yield RetrieveApplicationService(
            retrieval_service=retrieval_service,
            retrieval_log=RetrievalLogRepository(session),
            audit=SqlAlchemyAuditPort(session),
            pipeline_trace_provider=pipeline_trace_provider,
        )


RetrieveApplicationServiceDep = Annotated[
    RetrieveApplicationService,
    Depends(get_retrieve_application_service),
]


async def get_rag_query_application_service() -> AsyncIterator[RagQueryApplicationService]:
    settings = load_settings()
    session_factory = _session_factory(settings.database_url)
    async with session_factory() as session:
        retrieval_service, _pipeline_trace_provider = _retrieval_service_from_settings(
            settings=settings,
            session=session,
        )
        llm_provider = _llm_provider_from_settings(
            provider=settings.llm_provider,
            model=settings.llm_model,
            response_text=settings.llm_fake_response_text,
        )
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
            ),
            citation_extractor=CitationExtractor(),
            audit=SqlAlchemyAuditPort(session, auto_commit=True),
        )


RagQueryApplicationServiceDep = Annotated[
    RagQueryApplicationService,
    Depends(get_rag_query_application_service),
]


async def get_chat_application_service() -> AsyncIterator[ChatApplicationService]:
    settings = load_settings()
    session_factory = _session_factory(settings.database_url)
    async with session_factory() as session:
        retrieval_service, _pipeline_trace_provider = _retrieval_service_from_settings(
            settings=settings,
            session=session,
        )
        llm_provider = _llm_provider_from_settings(
            provider=settings.llm_provider,
            model=settings.llm_model,
            response_text=settings.llm_fake_response_text,
        )
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
            ),
            citation_extractor=CitationExtractor(),
            audit=SqlAlchemyAuditPort(session, auto_commit=True),
        )
        yield ChatApplicationService(
            memory_service=ChatMemoryService(repository=ChatMemoryRepository(session)),
            rag_query_service=rag_query_service,
            audit=SqlAlchemyAuditPort(session, auto_commit=True),
        )


ChatApplicationServiceDep = Annotated[
    ChatApplicationService,
    Depends(get_chat_application_service),
]


async def get_openwebui_chat_adapter() -> AsyncIterator[OpenWebUIChatAdapter]:
    settings = load_settings()
    session_factory = _session_factory(settings.database_url)
    async with session_factory() as session:
        retrieval_service, _pipeline_trace_provider = _retrieval_service_from_settings(
            settings=settings,
            session=session,
        )
        retrieve_application_service = RetrieveApplicationService(
            retrieval_service=retrieval_service,
            retrieval_log=RetrievalLogRepository(session),
            audit=SqlAlchemyAuditPort(session),
            pipeline_trace_provider=lambda: {},
        )
        llm_provider = _llm_provider_from_settings(
            provider=settings.llm_provider,
            model=settings.llm_model,
            response_text=settings.llm_fake_response_text,
        )
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
            ),
            citation_extractor=CitationExtractor(),
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
        registry = build_agent_tool_registry(
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
    OpenWebUIChatAdapter,
    Depends(get_openwebui_chat_adapter),
]


async def get_agent_run_application_service() -> AsyncIterator[AgentRunApplicationService]:
    settings = load_settings()
    session_factory = _session_factory(settings.database_url)
    async with session_factory() as session:
        audit = SqlAlchemyAuditPort(session, auto_commit=True)
        tool_call_repository = ToolCallRepository(session)
        retrieval_service, pipeline_trace_provider = _retrieval_service_from_settings(
            settings=settings,
            session=session,
        )
        retrieve_application_service = RetrieveApplicationService(
            retrieval_service=retrieval_service,
            retrieval_log=RetrievalLogRepository(session),
            audit=SqlAlchemyAuditPort(session),
            pipeline_trace_provider=pipeline_trace_provider,
        )
        registry = build_agent_tool_registry(
            settings=settings,
            audit=audit,
            tool_call_repository=tool_call_repository,
            retrieve_application_service=retrieve_application_service,
        )
        yield AgentRunApplicationService(
            repository=AgentRunRepository(session),
            runtime_factory=lambda config, agent_run_id: AgentRuntime(
                registry=registry,
                stepper=DeterministicAgentStepper(),
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
    AgentRunApplicationService,
    Depends(get_agent_run_application_service),
]


async def get_source_resolve_service() -> AsyncIterator[SourceResolveService]:
    settings = load_settings()
    session_factory = _session_factory(settings.database_url)
    async with session_factory() as session:
        yield SourceResolveService(
            repository=DocumentRepository(session),
            citation_metadata_repository=DocumentRepository(session),
            audit=SqlAlchemyAuditPort(session, auto_commit=True),
        )


SourceResolveServiceDep = Annotated[
    SourceResolveService,
    Depends(get_source_resolve_service),
]


async def get_diagnostics_service() -> AsyncIterator[DiagnosticsService]:
    settings = load_settings()
    session_factory = _session_factory(settings.database_url)
    async with session_factory() as session:
        yield DiagnosticsService(
            retrieval_logs=RetrievalLogRepository(session),
            audit_logs=AuditLogRepository(session),
        )


DiagnosticsServiceDep = Annotated[
    DiagnosticsService,
    Depends(get_diagnostics_service),
]


async def get_eval_evidence_service() -> AsyncIterator[EvalEvidenceService]:
    settings = load_settings()
    session_factory = _session_factory(settings.database_url)
    async with session_factory() as session:
        yield EvalEvidenceService(
            report_dir=settings.eval_report_dir,
            audit=SqlAlchemyAuditPort(session, auto_commit=True),
        )


EvalEvidenceServiceDep = Annotated[
    EvalEvidenceService,
    Depends(get_eval_evidence_service),
]


async def get_audit_explorer_service() -> AsyncIterator[AuditExplorerService]:
    settings = load_settings()
    session_factory = _session_factory(settings.database_url)
    async with session_factory() as session:
        yield AuditExplorerService(
            audit_logs=AuditLogRepository(session),
            tool_calls=ToolCallRepository(session),
            audit=SqlAlchemyAuditPort(session, auto_commit=True),
        )


AuditExplorerServiceDep = Annotated[
    AuditExplorerService,
    Depends(get_audit_explorer_service),
]


async def get_review_queue_service() -> AsyncIterator[ReviewQueueService]:
    settings = load_settings()
    session_factory = _session_factory(settings.database_url)
    async with session_factory() as session:
        yield ReviewQueueService(
            repository=ReviewItemRepository(session),
            audit=SqlAlchemyAuditPort(session, auto_commit=True),
        )


ReviewQueueServiceDep = Annotated[
    ReviewQueueService,
    Depends(get_review_queue_service),
]


@lru_cache(maxsize=8)
def _session_factory(database_url: str | None) -> async_sessionmaker[AsyncSession]:
    engine = create_async_db_engine(database_url)
    return create_session_factory(engine)


def _vector_store_from_settings(
    vector_store_type: str,
    vector_index_dim: int,
    session: AsyncSession,
) -> VectorStore:
    if vector_store_type == "fake":
        return FakeVectorStore(index_dim=vector_index_dim)
    if vector_store_type == "pgvector":
        return PgVectorStore(session, index_dim=vector_index_dim)
    raise ValueError("Unsupported VECTOR_STORE_TYPE. Supported values are 'fake' and 'pgvector'.")


def _embedding_provider_from_settings(
    *,
    provider: str,
    model: str,
    dim: int,
) -> FakeEmbeddingProvider:
    if provider == "fake":
        return FakeEmbeddingProvider(dim=dim, provider=provider, model=model, version="fake-v1")
    raise StorageConfigurationError(
        details={
            "provider": provider,
            "supported_embedding_providers": ["fake"],
        }
    )


def _llm_provider_from_settings(
    *,
    provider: str,
    model: str,
    response_text: str,
) -> FakeLLMProvider:
    if provider == "fake":
        return FakeLLMProvider(
            provider=provider,
            model=model,
            version="fake-v1",
            response_text=response_text,
        )
    raise StorageConfigurationError(
        details={
            "provider": provider,
            "supported_llm_providers": ["fake"],
        }
    )


def _retrieval_service_from_settings(
    *,
    settings: AppSettings,
    session: AsyncSession,
) -> tuple[RetrievalService, Callable[[], Mapping[str, object]]]:
    vector_store = _vector_store_from_settings(
        settings.vector_store_type,
        settings.vector_index_dim,
        session,
    )
    embedding_provider = _embedding_provider_from_settings(
        provider=settings.embedding_provider,
        model=settings.embedding_model,
        dim=settings.embedding_dim,
    )
    dense_retriever = DenseRetriever(
        embedding_provider=embedding_provider,
        vector_store=vector_store,
        config=DenseRetrieverConfig(
            embedding_provider=settings.embedding_provider,
            embedding_model=settings.embedding_model,
            embedding_version="fake-v1" if settings.embedding_provider == "fake" else None,
            timeout_seconds=settings.embedding_timeout_seconds,
            retry_budget=settings.embedding_retry_budget,
            distance_metric=_distance_metric_from_settings(settings.vector_distance_metric),
        ),
    )
    sparse_retriever = PostgresSparseRetriever(
        session=session,
        config=SparseRetrieverConfig(),
    )
    merger = RRFMerger(config=HybridMergeConfig())
    hybrid_retriever = HybridRetriever(
        dense_retriever=dense_retriever,
        sparse_retriever=sparse_retriever,
        merger=merger,
        config=HybridMergeConfig(),
    )
    reranking_retriever = RerankingRetriever(
        upstream_retriever=hybrid_retriever,
        reranker=FakeReranker(),
        config=RerankConfig(),
    )
    return (
        RetrievalService(retriever=reranking_retriever),
        lambda: _retrieval_pipeline_trace(
            merger=merger,
            reranking_retriever=reranking_retriever,
        ),
    )


def _distance_metric_from_settings(value: str) -> DistanceMetric:
    if value == "cosine":
        return "cosine"
    if value == "l2":
        return "l2"
    raise StorageConfigurationError(
        details={
            "distance_metric": value,
            "supported_distance_metrics": ["cosine", "l2"],
        }
    )


def _retrieval_pipeline_trace(
    *,
    merger: RRFMerger,
    reranking_retriever: RerankingRetriever,
) -> dict[str, object]:
    rrf_trace = merger.last_trace
    rerank_trace = reranking_retriever.last_trace
    rrf: dict[str, object] = {}
    if rrf_trace is not None:
        rrf = {
            "input_counts": dict(rrf_trace.input_counts),
            "deduped_count": rrf_trace.deduped_count,
            "filtered_count": rrf_trace.filtered_count,
            "threshold": rrf_trace.threshold,
            "rank_constant": rrf_trace.rank_constant,
            "weights": dict(rrf_trace.weights),
        }
    rerank: dict[str, object] = {"status": "not_available", "candidate_count": 0}
    if rerank_trace is not None:
        if rerank_trace.error_code:
            status = "failed"
        elif rerank_trace.degraded:
            status = "degraded"
        else:
            status = "success"
        rerank = {
            "status": status,
            "provider": rerank_trace.provider,
            "model": rerank_trace.model,
            "latency_ms": rerank_trace.latency_ms,
            "input_count": rerank_trace.input_count,
            "output_count": rerank_trace.output_count,
            "candidate_count": rerank_trace.output_count,
            "safe_counts": dict(rerank_trace.safe_counts),
            "error_code": rerank_trace.error_code,
        }
    return {"rrf": rrf, "rerank": rerank}


def build_agent_tool_registry(
    *,
    settings: AppSettings,
    audit: SqlAlchemyAuditPort | AuditPort,
    tool_call_repository: ToolCallRecorderPort,
    retrieve_application_service: RetrieveApplicationService,
) -> ToolRegistry:
    registry = ToolRegistry(
        audit=audit,
        tool_call_recorder=tool_call_repository,
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
            allowlist_roots=_file_reader_allowlist_roots(settings.file_reader_allowlist_roots),
            max_file_bytes=settings.file_reader_max_file_bytes,
            max_return_bytes=settings.file_reader_max_return_bytes,
            timeout_seconds=settings.tool_default_timeout_seconds,
            rate_limit=rate_limit,
        )
    )
    return registry


def _file_reader_allowlist_roots(value: str) -> tuple[Path, ...]:
    roots = tuple(
        (Path(item.strip()) if Path(item.strip()).is_absolute() else Path.cwd() / item.strip())
        for item in value.split(",")
        if item.strip()
    )
    if not roots:
        return (Path.cwd() / "docs",)
    return roots


class DeterministicAgentStepper:
    async def next_step(self, state: AgentRuntimeState) -> AgentStepDecision:
        _ = state
        return AgentStepDecision(
            action=AgentActionType.FINAL_ANSWER,
            final_answer="Agent run accepted for governed execution.",
        )

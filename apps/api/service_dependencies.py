"""Backward-compatible re-exports from factories package.

DI decoupling (T1 finding): the monolithic ~600-line file has been split into:
  apps/api/factories/common.py   — DB session, vector store, providers, circuit breakers
  apps/api/factories/retrieval.py — retrieval pipeline, caching, real reranker
  apps/api/factories/domain.py   — document, RAG, chat, OpenWebUI, agent
  apps/api/factories/services.py — source resolve, diagnostics, eval, audit, review
"""

from apps.api.factories import (
    # Agent
    AgentRunApplicationServiceDep,
    AuditExplorerServiceDep,
    ChatApplicationServiceDep,
    DiagnosticsServiceDep,
    DocumentLifecycleServiceDep,
    # Document
    DocumentUploadServiceDep,
    EvalEvidenceServiceDep,
    OpenWebUIChatAdapterDep,
    # RAG
    RagQueryApplicationServiceDep,
    # Retrieval
    RetrieveApplicationServiceDep,
    ReviewQueueServiceDep,
    # Services
    SourceResolveServiceDep,
    get_agent_run_application_service,
    get_audit_explorer_service,
    get_chat_application_service,
    get_diagnostics_service,
    get_document_lifecycle_service,
    # Factory functions
    get_document_upload_service,
    get_eval_evidence_service,
    get_openwebui_chat_adapter,
    get_rag_query_application_service,
    get_retrieve_application_service,
    get_review_queue_service,
    get_source_resolve_service,
    create_adaptive_retrieval_service,
)

__all__ = [
    "DocumentUploadServiceDep",
    "DocumentLifecycleServiceDep",
    "RetrieveApplicationServiceDep",
    "RagQueryApplicationServiceDep",
    "ChatApplicationServiceDep",
    "OpenWebUIChatAdapterDep",
    "AgentRunApplicationServiceDep",
    "SourceResolveServiceDep",
    "DiagnosticsServiceDep",
    "EvalEvidenceServiceDep",
    "AuditExplorerServiceDep",
    "ReviewQueueServiceDep",
    "get_document_upload_service",
    "get_document_lifecycle_service",
    "get_retrieve_application_service",
    "get_rag_query_application_service",
    "get_chat_application_service",
    "get_openwebui_chat_adapter",
    "get_agent_run_application_service",
    "get_source_resolve_service",
    "get_diagnostics_service",
    "get_eval_evidence_service",
    "get_audit_explorer_service",
    "get_review_queue_service",
    "create_adaptive_retrieval_service",
]

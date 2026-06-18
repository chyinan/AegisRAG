"""Factory modules for FastAPI dependency injection.

Split from the monolithic service_dependencies.py (~600 lines)
to improve maintainability and boundary clarity (T1 finding).

Modules:
  common    — DB session, vector store, embedding/LLM providers, circuit breakers
  retrieval — retrieval pipeline (dense, sparse, hybrid, rerank, cache)
  domain    — document upload, lifecycle, rag query, chat, openwebui, agent
  services  — source resolve, diagnostics, eval, audit, review
"""

from apps.api.factories.common import (
    CircuitBreakerRegistry,
    create_circuit_breaker,
    create_embedding_provider,
    create_llm_provider,
    create_session_factory,
    create_vector_store,
)
from apps.api.factories.domain import (
    AgentRunApplicationServiceDep,
    ChatApplicationServiceDep,
    DocumentLifecycleServiceDep,
    DocumentUploadServiceDep,
    OpenWebUIChatAdapterDep,
    RagQueryApplicationServiceDep,
    RetrieveApplicationServiceDep,
    get_agent_run_application_service,
    get_chat_application_service,
    get_document_lifecycle_service,
    get_document_upload_service,
    get_openwebui_chat_adapter,
    get_rag_query_application_service,
    get_retrieve_application_service,
)
from apps.api.factories.retrieval import (
    RetrievalCacheRegistry,
    create_retrieval_cache,
    create_retrieval_service,
)
from apps.api.factories.services import (
    AuditExplorerServiceDep,
    DiagnosticsServiceDep,
    EvalEvidenceServiceDep,
    ReviewQueueServiceDep,
    SourceResolveServiceDep,
    get_audit_explorer_service,
    get_diagnostics_service,
    get_eval_evidence_service,
    get_review_queue_service,
    get_source_resolve_service,
)

__all__ = [
    "create_session_factory",
    "create_vector_store",
    "create_embedding_provider",
    "create_llm_provider",
    "create_circuit_breaker",
    "CircuitBreakerRegistry",
    "create_retrieval_service",
    "create_retrieval_cache",
    "RetrievalCacheRegistry",
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
]

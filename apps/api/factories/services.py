"""Ancillary service factories: source resolve, diagnostics, eval, audit, review.

Extracted from service_dependencies.py as part of DI decoupling (T1 finding).
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends

from apps.api.factories.common import create_session_factory
from packages.agent.storage.repositories import ToolCallRepository
from packages.audit import AuditExplorerService
from packages.common.config import load_settings
from packages.data.storage.audit_repositories import AuditLogRepository, SqlAlchemyAuditPort
from packages.data.storage.repositories import DocumentRepository
from packages.data.storage.review_repositories import ReviewItemRepository
from packages.diagnostics import DiagnosticsService
from packages.eval import EvalEvidenceService
from packages.rag import SourceResolveService
from packages.retrieval.storage.repositories import RetrievalLogRepository
from packages.review import ReviewQueueService


async def get_source_resolve_service() -> AsyncIterator[SourceResolveService]:
    settings = load_settings()
    session_factory = create_session_factory(settings.database_url)
    async with session_factory() as session:
        yield SourceResolveService(
            repository=DocumentRepository(session),
            citation_metadata_repository=DocumentRepository(session),
            audit=SqlAlchemyAuditPort(session, auto_commit=True),
        )


SourceResolveServiceDep = Annotated[
    SourceResolveService, Depends(get_source_resolve_service)
]


async def get_diagnostics_service() -> AsyncIterator[DiagnosticsService]:
    settings = load_settings()
    session_factory = create_session_factory(settings.database_url)
    async with session_factory() as session:
        yield DiagnosticsService(
            retrieval_logs=RetrievalLogRepository(session),
            audit_logs=AuditLogRepository(session),
        )


DiagnosticsServiceDep = Annotated[
    DiagnosticsService, Depends(get_diagnostics_service)
]


async def get_eval_evidence_service() -> AsyncIterator[EvalEvidenceService]:
    settings = load_settings()
    session_factory = create_session_factory(settings.database_url)
    async with session_factory() as session:
        yield EvalEvidenceService(
            report_dir=settings.eval_report_dir,
            audit=SqlAlchemyAuditPort(session, auto_commit=True),
        )


EvalEvidenceServiceDep = Annotated[
    EvalEvidenceService, Depends(get_eval_evidence_service)
]


async def get_audit_explorer_service() -> AsyncIterator[AuditExplorerService]:
    settings = load_settings()
    session_factory = create_session_factory(settings.database_url)
    async with session_factory() as session:
        yield AuditExplorerService(
            audit_logs=AuditLogRepository(session),
            tool_calls=ToolCallRepository(session),
            audit=SqlAlchemyAuditPort(session, auto_commit=True),
        )


AuditExplorerServiceDep = Annotated[
    AuditExplorerService, Depends(get_audit_explorer_service)
]


async def get_review_queue_service() -> AsyncIterator[ReviewQueueService]:
    settings = load_settings()
    session_factory = create_session_factory(settings.database_url)
    async with session_factory() as session:
        yield ReviewQueueService(
            repository=ReviewItemRepository(session),
            audit=SqlAlchemyAuditPort(session, auto_commit=True),
        )


ReviewQueueServiceDep = Annotated[
    ReviewQueueService, Depends(get_review_queue_service)
]

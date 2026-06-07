from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

import pytest

from packages.auth.context import AuthContext
from packages.common.audit import InMemoryAuditPort
from packages.common.context import AuthenticatedRequestContext
from packages.data.storage.exceptions import StorageError
from packages.retrieval.application import RetrieveApplicationService, RetrieveCommand
from packages.retrieval.dto import (
    RetrievalCandidate,
    RetrievalLogCreate,
    RetrievalLogRecord,
    RetrievalRequest,
    RetrievalResult,
)
from packages.retrieval.exceptions import RETRIEVAL_BACKEND_FAILED, RetrievalError
from packages.retrieval.ports import RetrievalLogPort
from packages.retrieval.service import RetrievalService


class FakeRetrievalService:
    def __init__(
        self,
        *,
        result: RetrievalResult | None = None,
        error: RetrievalError | None = None,
    ) -> None:
        self.calls: list[tuple[RetrievalRequest, AuthContext | None]] = []
        self._result = result
        self._error = error

    async def retrieve(
        self,
        *,
        request: RetrievalRequest,
        auth: AuthContext | None,
    ) -> RetrievalResult:
        self.calls.append((request, auth))
        if self._error is not None:
            raise self._error
        if self._result is None:
            raise AssertionError("result is required")
        return self._result


class FakeRetrievalLogPort:
    def __init__(self) -> None:
        self.created: list[RetrievalLogCreate] = []
        self.records: list[RetrievalLogRecord] = []
        self.commits = 0
        self.rollbacks = 0

    async def create(self, record: RetrievalLogCreate) -> RetrievalLogRecord:
        self.created.append(record)
        data = record.model_dump(exclude={"created_at"})
        stored = RetrievalLogRecord(
            **data,
            id=f"log-{len(self.created)}",
            created_at=record.created_at or datetime.now(tz=UTC),
            updated_at=record.created_at or datetime.now(tz=UTC),
        )
        self.records.append(stored)
        return stored

    async def get_by_request_id(
        self,
        *,
        tenant_id: str,
        request_id: str,
    ) -> RetrievalLogRecord | None:
        records = await self.list_by_request_id(tenant_id=tenant_id, request_id=request_id)
        return records[0] if records else None

    async def list_by_request_id(
        self,
        *,
        tenant_id: str,
        request_id: str,
    ) -> list[RetrievalLogRecord]:
        return [
            record
            for record in self.records
            if record.tenant_id == tenant_id and record.request_id == request_id
        ]

    async def list_by_created_at(
        self,
        *,
        tenant_id: str,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        limit: int = 100,
    ) -> list[RetrievalLogRecord]:
        records = [record for record in self.records if record.tenant_id == tenant_id]
        if created_from is not None:
            records = [record for record in records if record.created_at >= created_from]
        if created_to is not None:
            records = [record for record in records if record.created_at <= created_to]
        return records[:limit]

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class FailingRetrievalLogPort(FakeRetrievalLogPort):
    async def create(self, record: RetrievalLogCreate) -> RetrievalLogRecord:
        self.created.append(record)
        raise StorageError(code="RETRIEVAL_LOG_STORAGE_WRITE_FAILED", message="write failed")


class SteppingCounter:
    def __init__(self, values: list[float]) -> None:
        self._values = values

    def __call__(self) -> float:
        if not self._values:
            raise AssertionError("counter exhausted")
        return self._values.pop(0)


def _context() -> AuthenticatedRequestContext:
    return AuthenticatedRequestContext(
        request_id="req-1",
        trace_id="trace-1",
        session_id=None,
        auth=AuthContext(
            tenant_id="tenant-1",
            user_id="user-1",
            roles=("knowledge_user",),
            permissions=("document:read",),
        ),
    )


def _candidate(chunk_id: str, score: float) -> RetrievalCandidate:
    return RetrievalCandidate(
        document_id="doc-1",
        version_id="ver-1",
        chunk_id=chunk_id,
        source="kb://policy.md",
        source_type="markdown",
        source_uri="kb://policy.md",
        page_start=1,
        page_end=1,
        title_path=("Policy",),
        score=score,
        retrieval_method="hybrid",
        tenant_id="tenant-1",
        acl={"visibility": "tenant", "token": "secret-token"},
        metadata={
            "department": "HR",
            "content": "chunk body must not leak",
            "absolute_path": r"C:\secret\policy.md",
            "retrieval_provenance": {
                "retrieval_methods": ("dense", "sparse"),
                "sources": (
                    {
                        "retrieval_method": "dense",
                        "rank": 1,
                        "score": 0.7,
                        "weight": 1.0,
                        "contribution": 0.1,
                        "sql": "select * from chunks",
                        "snippet": "chunk body must not leak through source",
                    },
                    {
                        "retrieval_method": "sparse",
                        "rank": 2,
                        "score": 0.6,
                        "weight": 1.0,
                        "contribution": 0.08,
                    },
                ),
                "raw_rrf_score": 0.18,
                "normalized_fusion_score": 0.9,
                "fusion_reason": "dense_sparse_overlap",
                "query": "raw query",
            },
            "rerank_provenance": {
                "provider": "fake",
                "model": "fake-reranker-v1",
                "status": "success",
                "input_rank": 2,
                "output_rank": 1,
                "pre_score": 0.9,
                "rerank_score": score,
                "score_source": "rerank",
                "latency_ms": 1.2,
                "provider_raw_response": "password=secret",
            },
        },
    )


@pytest.mark.asyncio
async def test_retrieve_application_success_logs_safe_summary_and_audit() -> None:
    context = _context()
    result = RetrievalResult(
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-1",
        user_id="user-1",
        top_k=2,
        query_summary={"length": 18},
        latency_ms=12.5,
        candidates=(_candidate("chunk-2", 0.82), _candidate("chunk-1", 0.74)),
    )
    service = FakeRetrievalService(result=result)
    log = FakeRetrievalLogPort()
    audit = InMemoryAuditPort()
    app_service = RetrieveApplicationService(
        retrieval_service=cast(RetrievalService, service),
        retrieval_log=cast(RetrievalLogPort, log),
        audit=audit,
        clock=lambda: datetime(2026, 6, 7, tzinfo=UTC),
    )

    response = await app_service.retrieve(
        context=context,
        command=RetrieveCommand(query="leave policy secret", top_k=2),
    )

    assert response.request_id == "req-1"
    assert response.candidates[0].metadata["department"] == "HR"
    assert "content" not in response.candidates[0].metadata
    assert "absolute_path" not in response.candidates[0].metadata
    assert response.candidates[0].acl == {"visibility": "tenant"}
    assert len(service.calls) == 1
    request, auth = service.calls[0]
    assert request.request_id == "req-1"
    assert request.trace_id == "trace-1"
    assert request.top_k == 2
    assert auth == context.auth

    assert len(log.created) == 1
    record = log.created[0]
    assert record.status == "success"
    assert record.result_count == 2
    assert record.rerank_score == 0.82
    assert record.query_summary == {"length": 18}
    assert record.metadata["dense_top_k"] == 2
    assert record.metadata["sparse_top_k"] == 2
    rrf = record.metadata["rrf"]
    rerank = record.metadata["rerank"]
    assert isinstance(rrf, Mapping)
    assert isinstance(rerank, Mapping)
    assert rrf["deduped_count"] == 2
    assert rerank["status"] == "success"
    assert record.metadata["candidate_ids"] == [
        {"document_id": "doc-1", "version_id": "ver-1", "chunk_id": "chunk-2"},
        {"document_id": "doc-1", "version_id": "ver-1", "chunk_id": "chunk-1"},
    ]
    assert "chunk body" not in str(record.metadata)
    assert "snippet" not in str(response.candidates[0].metadata)
    assert "snippet" not in str(record.metadata)
    assert "select *" not in str(record.metadata)
    assert r"C:\secret" not in str(record.metadata)
    assert "password=secret" not in str(record.metadata)
    assert log.commits == 1
    assert audit.events[0].status == "success"
    assert audit.events[0].metadata["result_count"] == 2


@pytest.mark.asyncio
async def test_retrieve_application_failure_logs_safe_error_and_reraises() -> None:
    context = _context()
    error = RetrievalError(
        code=RETRIEVAL_BACKEND_FAILED,
        message="Retrieval backend failed.",
        details={
            "request_id": "req-1",
            "trace_id": "trace-1",
            "query": "full query should be redacted",
            "sql": "select * from chunks",
            "provider_raw_response": "password=secret",
            "absolute_path": r"C:\secret\provider.log",
        },
        status_code=502,
    )
    service = FakeRetrievalService(error=error)
    log = FakeRetrievalLogPort()
    audit = InMemoryAuditPort()
    app_service = RetrieveApplicationService(
        retrieval_service=cast(RetrievalService, service),
        retrieval_log=cast(RetrievalLogPort, log),
        audit=audit,
        clock=lambda: datetime(2026, 6, 7, tzinfo=UTC),
        perf_counter=SteppingCounter([10.0, 10.25]),
    )

    with pytest.raises(RetrievalError) as exc_info:
        await app_service.retrieve(
            context=context,
            command=RetrieveCommand(query="full query should be redacted", top_k=5),
        )

    assert exc_info.value.code == RETRIEVAL_BACKEND_FAILED
    assert len(log.created) == 1
    record = log.created[0]
    assert record.status == "failure"
    assert record.error_code == RETRIEVAL_BACKEND_FAILED
    assert record.latency_ms == 250.0
    assert record.top_k == 5
    assert record.result_count == 0
    assert record.query_summary == {"length": 29, "term_count": 5}
    assert "full query" not in str(record.metadata)
    assert "select *" not in str(record.metadata)
    assert "password=secret" not in str(record.metadata)
    assert r"C:\secret" not in str(record.metadata)
    assert log.commits == 1
    assert audit.events[0].status == "failure"
    assert audit.events[0].error_code == RETRIEVAL_BACKEND_FAILED


@pytest.mark.asyncio
async def test_retrieve_application_failure_log_error_preserves_original_retrieval_error() -> None:
    context = _context()
    error = RetrievalError(
        code=RETRIEVAL_BACKEND_FAILED,
        message="Retrieval backend failed.",
        details={"request_id": "req-1", "trace_id": "trace-1"},
        status_code=502,
    )
    service = FakeRetrievalService(error=error)
    log = FailingRetrievalLogPort()
    audit = InMemoryAuditPort()
    app_service = RetrieveApplicationService(
        retrieval_service=cast(RetrievalService, service),
        retrieval_log=cast(RetrievalLogPort, log),
        audit=audit,
        perf_counter=SteppingCounter([10.0, 10.25]),
    )

    with pytest.raises(RetrievalError) as exc_info:
        await app_service.retrieve(
            context=context,
            command=RetrieveCommand(query="policy", top_k=5),
        )

    assert exc_info.value.code == RETRIEVAL_BACKEND_FAILED
    assert log.rollbacks == 1
    assert audit.events == []


@pytest.mark.asyncio
async def test_retrieve_application_logs_pipeline_trace_when_available() -> None:
    context = _context()
    result = RetrievalResult(
        request_id="req-1",
        trace_id="trace-1",
        tenant_id="tenant-1",
        user_id="user-1",
        top_k=2,
        query_summary={"length": 18},
        latency_ms=12.5,
        candidates=(_candidate("chunk-2", 0.82),),
    )
    service = FakeRetrievalService(result=result)
    log = FakeRetrievalLogPort()
    audit = InMemoryAuditPort()
    app_service = RetrieveApplicationService(
        retrieval_service=cast(RetrievalService, service),
        retrieval_log=cast(RetrievalLogPort, log),
        audit=audit,
        pipeline_trace_provider=lambda: {
            "rrf": {
                "input_counts": {"dense": 8, "sparse": 6},
                "deduped_count": 9,
                "filtered_count": 2,
                "threshold": 0.2,
            },
            "rerank": {
                "status": "degraded",
                "provider": "fake",
                "model": "fake-reranker-v1",
                "latency_ms": 3.0,
                "input_count": 9,
                "output_count": 2,
                "candidate_count": 2,
            },
        },
    )

    await app_service.retrieve(
        context=context,
        command=RetrieveCommand(query="leave policy secret", top_k=2),
    )

    record = log.created[0]
    assert record.metadata["dense_top_k"] == 8
    assert record.metadata["sparse_top_k"] == 6
    assert record.metadata["rrf"] == {
        "input_counts": {"dense": 8, "sparse": 6},
        "deduped_count": 9,
        "filtered_count": 2,
        "threshold": 0.2,
    }
    assert record.metadata["rerank"] == {
        "status": "degraded",
        "provider": "fake",
        "model": "fake-reranker-v1",
        "latency_ms": 3.0,
        "input_count": 9,
        "output_count": 2,
        "candidate_count": 2,
    }

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.auth.context import AuthContext
from packages.common.logging import REDACTED_VALUE
from packages.data.storage.base import Base
from packages.data.storage.models import ChunkModel
from packages.retrieval.dto import RetrievalRequest
from packages.retrieval.exceptions import (
    RETRIEVAL_SPARSE_SEARCH_FAILED,
    RetrievalError,
)
from packages.retrieval.filters import build_retrieval_filter_set
from packages.retrieval.service import RetrievalService
from packages.retrieval.sparse import (
    PostgresSparseRetriever,
    SparseChunkRecord,
    SparseRetrieverConfig,
    parse_sparse_query_terms,
)


@pytest.mark.asyncio
async def test_sparse_retriever_recalls_exact_keywords_and_maps_citation_safe_candidates() -> None:
    backend = RecordingSparseBackend(_records())
    retriever = PostgresSparseRetriever(backend=backend, config=SparseRetrieverConfig())
    request = RetrievalRequest(
        query="制度编号 HR-2026-01 ERR-42 张三 ZX-900",
        top_k=10,
        request_id="req-1",
        trace_id="trace-1",
    )

    candidates = await retriever.retrieve(
        request=request,
        filters=build_retrieval_filter_set(auth=_auth(), request=request),
    )

    assert backend.last_payload is not None
    assert backend.last_payload["tenant_id"] == "tenant-a"
    assert backend.last_payload["user_id"] == "user-1"
    assert backend.last_payload["include_deleted"] is False
    assert [candidate.chunk_id for candidate in candidates] == [
        "clause",
        "error-code",
        "person",
        "product",
    ]
    assert {candidate.retrieval_method for candidate in candidates} == {"sparse"}
    assert candidates[0].document_id == "doc-clause"
    assert candidates[0].version_id == "ver-clause"
    assert candidates[0].source == "kb://clause.md"
    assert candidates[0].source_type == "markdown"
    assert candidates[0].source_uri == "kb://clause.md"
    assert candidates[0].page_start == 1
    assert candidates[0].page_end == 1
    assert candidates[0].title_path == ("Policy", "clause")
    assert candidates[0].tenant_id == "tenant-a"
    assert candidates[0].acl == {"visibility": "tenant", "allowed_roles": ["hr"]}
    assert candidates[0].metadata == {"department": "people", "source_type": "markdown"}
    assert "content" not in str([candidate.model_dump() for candidate in candidates]).lower()
    assert "tsvector" not in str([candidate.model_dump() for candidate in candidates]).lower()


@pytest.mark.asyncio
async def test_sparse_retriever_filters_before_candidates_are_returned() -> None:
    backend = RecordingSparseBackend(
        [
            *_records(),
            _record(chunk_id="wrong-tenant", tenant_id="tenant-b", content="ERR-42"),
            _record(chunk_id="wrong-metadata", department="finance", content="ERR-42"),
            _record(chunk_id="deleted", content="ERR-42", deleted_at=datetime.now(tz=UTC)),
            _record(chunk_id="inactive", content="ERR-42", status="draft"),
            _record(chunk_id="private", content="ERR-42", acl={"visibility": "private"}),
            _record(
                chunk_id="denied",
                content="ERR-42",
                acl={"visibility": "tenant", "denied_users": ["user-1"]},
            ),
            _record(
                chunk_id="role-allowed",
                content="ERR-42",
                acl={"visibility": "private", "allowed_roles": ["hr"]},
                rank=0.85,
            ),
            _record(
                chunk_id="department-allowed",
                content="ERR-42",
                acl={"visibility": "private", "allowed_departments": ["people"]},
                rank=0.8,
            ),
            _record(
                chunk_id="permission-allowed",
                content="ERR-42",
                acl={"visibility": "private", "allowed_permissions": ["document:read"]},
                rank=0.75,
            ),
        ]
    )
    retriever = PostgresSparseRetriever(backend=backend, config=SparseRetrieverConfig())
    request = RetrievalRequest(
        query="ERR-42",
        top_k=2,
        score_threshold=0.2,
        metadata_filter={"department": "people"},
        request_id="req-1",
        trace_id="trace-1",
    )

    candidates = await retriever.retrieve(
        request=request,
        filters=build_retrieval_filter_set(auth=_auth(), request=request),
    )

    assert [candidate.chunk_id for candidate in candidates] == ["error-code", "role-allowed"]
    assert all(candidate.tenant_id == "tenant-a" for candidate in candidates)
    assert all(candidate.metadata["department"] == "people" for candidate in candidates)


@pytest.mark.asyncio
async def test_sparse_retriever_returns_empty_for_symbol_only_query() -> None:
    retriever = PostgresSparseRetriever(
        backend=RecordingSparseBackend(_records()),
        config=SparseRetrieverConfig(),
    )
    request = RetrievalRequest(query="!!! --- ***", request_id="req-1", trace_id="trace-1")

    candidates = await retriever.retrieve(
        request=request,
        filters=build_retrieval_filter_set(auth=_auth(), request=request),
    )

    assert candidates == []


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("中文制度 HR-2026-01 / ERR-42", ("中文制度", "hr-2026-01", "err-42")),
        ("ZX-900, 张三; section_12", ("zx-900", "张三", "section_12")),
        ("!!!", ()),
    ],
)
def test_parse_sparse_query_terms_handles_chinese_mixed_text_and_symbols(
    query: str,
    expected: tuple[str, ...],
) -> None:
    assert parse_sparse_query_terms(query, max_terms=8) == expected


@pytest.mark.asyncio
async def test_sparse_retriever_maps_backend_failure_to_safe_error() -> None:
    retriever = PostgresSparseRetriever(
        backend=FailingSparseBackend(),
        config=SparseRetrieverConfig(language_config="simple"),
    )
    request = RetrievalRequest(
        query="secret full query text",
        top_k=3,
        request_id="req-1",
        trace_id="trace-1",
    )

    with pytest.raises(RetrievalError) as exc_info:
        await retriever.retrieve(
            request=request,
            filters=build_retrieval_filter_set(auth=_auth(), request=request),
        )

    assert exc_info.value.code == RETRIEVAL_SPARSE_SEARCH_FAILED
    assert exc_info.value.details == {
        "request_id": "req-1",
        "trace_id": "trace-1",
        "tenant_id": "tenant-a",
        "user_id": "user-1",
        "top_k": 3,
        "retrieval_method": "sparse",
        "backend_kind": "postgres",
        "language_config": "simple",
        "error_code": RETRIEVAL_SPARSE_SEARCH_FAILED,
    }
    details = str(exc_info.value.details)
    assert "secret full query text" not in details
    assert "raw sql" not in details.lower()
    assert "chunk content" not in details.lower()
    assert "password" not in details.lower()
    assert "C:\\" not in details


@pytest.mark.asyncio
async def test_sparse_retriever_maps_backend_timeout_to_safe_error() -> None:
    retriever = PostgresSparseRetriever(
        backend=SlowSparseBackend(),
        config=SparseRetrieverConfig(timeout_seconds=0.01),
    )
    request = RetrievalRequest(query="ERR-42", request_id="req-1", trace_id="trace-1")

    with pytest.raises(RetrievalError) as exc_info:
        await retriever.retrieve(
            request=request,
            filters=build_retrieval_filter_set(auth=_auth(), request=request),
        )

    assert exc_info.value.code == RETRIEVAL_SPARSE_SEARCH_FAILED
    assert exc_info.value.details["error_code"] == RETRIEVAL_SPARSE_SEARCH_FAILED
    assert "ERR-42" not in str(exc_info.value.details)


@pytest.mark.asyncio
async def test_retrieval_service_accepts_sparse_retriever_and_keeps_guard() -> None:
    service = RetrievalService(
        retriever=PostgresSparseRetriever(
            backend=RecordingSparseBackend(
                [
                    *_records(),
                    _record(chunk_id="low-score", content="ERR-42", rank=0.01),
                    _record(chunk_id="wrong-tenant", tenant_id="tenant-b", content="ERR-42"),
                ]
            ),
            config=SparseRetrieverConfig(),
        )
    )

    result = await service.retrieve(
        request=RetrievalRequest(
            query="ERR-42",
            top_k=1,
            score_threshold=0.5,
            metadata_filter={"department": "people"},
            request_id="req-1",
            trace_id="trace-1",
        ),
        auth=_auth(),
    )

    assert [candidate.chunk_id for candidate in result.candidates] == ["error-code"]
    assert result.candidates[0].retrieval_method == "sparse"


@pytest.mark.asyncio
async def test_sqlite_fallback_filters_before_top_k_and_before_loading_content(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "sparse-fallback.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path.as_posix()}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        async with session_factory() as session:
            session.add_all(
                [
                    _chunk_model(
                        id_="private-id",
                        chunk_id="private-first",
                        content="ERR-42 private content",
                        acl={"visibility": "private"},
                    ),
                    _chunk_model(
                        id_="wrong-metadata-id",
                        chunk_id="wrong-metadata",
                        content="ERR-42 finance content",
                        metadata={"department": "finance", "source_type": "markdown"},
                    ),
                    _chunk_model(
                        id_="allowed-id",
                        chunk_id="allowed",
                        content="ERR-42 allowed content",
                    ),
                ]
            )
            await session.commit()

            service = RetrievalService(
                retriever=PostgresSparseRetriever(
                    session=session,
                    config=SparseRetrieverConfig(),
                )
            )
            result = await service.retrieve(
                request=RetrievalRequest(
                    query="ERR-42",
                    top_k=1,
                    metadata_filter={"department": "people"},
                    request_id="req-1",
                    trace_id="trace-1",
                ),
                auth=_auth(),
            )

        assert [candidate.chunk_id for candidate in result.candidates] == ["allowed"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sparse_retriever_maps_candidate_validation_failure_to_safe_error() -> None:
    retriever = PostgresSparseRetriever(
        backend=RecordingSparseBackend(
            [_record(chunk_id="bad-title", content="ERR-42", title_path=[])]
        ),
        config=SparseRetrieverConfig(),
    )
    request = RetrievalRequest(query="ERR-42", request_id="req-1", trace_id="trace-1")

    with pytest.raises(RetrievalError) as exc_info:
        await retriever.retrieve(
            request=request,
            filters=build_retrieval_filter_set(auth=_auth(), request=request),
        )

    assert exc_info.value.code == RETRIEVAL_SPARSE_SEARCH_FAILED
    assert exc_info.value.details["error_code"] == RETRIEVAL_SPARSE_SEARCH_FAILED
    assert "ERR-42" not in str(exc_info.value.details)


@pytest.mark.asyncio
async def test_candidate_metadata_redaction_uses_shared_sensitive_keys() -> None:
    retriever = PostgresSparseRetriever(
        backend=RecordingSparseBackend(
            [
                _record(
                    chunk_id="sensitive-metadata",
                    content="ERR-42",
                    metadata={
                        "department": "people",
                        "prompt": "leak prompt",
                        "body": "leak body",
                        "full_query": "leak query",
                        "safe": "ok",
                    },
                )
            ]
        ),
        config=SparseRetrieverConfig(),
    )
    request = RetrievalRequest(query="ERR-42", request_id="req-1", trace_id="trace-1")

    candidates = await retriever.retrieve(
        request=request,
        filters=build_retrieval_filter_set(auth=_auth(), request=request),
    )

    assert candidates[0].metadata["prompt"] == REDACTED_VALUE
    assert candidates[0].metadata["body"] == REDACTED_VALUE
    assert candidates[0].metadata["full_query"] == REDACTED_VALUE
    assert candidates[0].metadata["safe"] == "ok"


def test_postgres_sql_uses_bind_params_websearch_and_capped_query_terms() -> None:
    retriever = PostgresSparseRetriever(
        backend=RecordingSparseBackend(_records()),
        config=SparseRetrieverConfig(
            language_config="simple",
            max_query_terms=2,
            max_query_term_length=4,
        ),
    )
    request = RetrievalRequest(
        query="ERR-424242 product-code third-token",
        metadata_filter={"department": "people"},
        request_id="req-1",
        trace_id="trace-1",
    )
    filters = build_retrieval_filter_set(auth=_auth(), request=request)

    statement, params = retriever.build_postgres_statement(request=request, filters=filters)

    sql = str(statement)
    assert "websearch_to_tsquery(:language_config, :query)" in sql
    assert "ts_rank_cd" in sql
    assert "chunks" in sql
    assert "ERR-424242" not in sql
    assert params["query"] == "err prod"
    assert params["tenant_id"] == "tenant-a"
    assert params["metadata_filter_0"] == '{"department": "people"}'
    assert params["top_k"] == 10
    assert params["denied_user"] == "user-1"
    assert "acl ->> 'denied_users' = :denied_user" in sql
    assert "acl ->> 'allowed_roles' = :acl_allowed_roles_0" in sql
    assert params["acl_allowed_roles_0"] == "hr"


def test_sparse_config_rejects_invalid_runtime_settings() -> None:
    with pytest.raises(ValueError, match="max_query_terms"):
        SparseRetrieverConfig(max_query_terms=0)
    with pytest.raises(ValueError, match="timeout_seconds"):
        SparseRetrieverConfig(timeout_seconds=float("inf"))


def test_parse_sparse_query_terms_limits_term_count_and_length() -> None:
    assert parse_sparse_query_terms(
        "ERR-424242 product-code third-token",
        max_terms=2,
        max_term_length=4,
    ) == ("err", "prod")


class RecordingSparseBackend:
    backend_kind = "postgres"

    def __init__(self, records: list[SparseChunkRecord]) -> None:
        self._records = records
        self.last_payload: dict[str, object] | None = None

    async def search(
        self,
        *,
        request: RetrievalRequest,
        filters_payload: Mapping[str, object],
        query_terms: tuple[str, ...],
        config: SparseRetrieverConfig,
    ) -> list[SparseChunkRecord]:
        self.last_payload = dict(filters_payload)
        return self._records


class FailingSparseBackend:
    backend_kind = "postgres"

    async def search(
        self,
        *,
        request: RetrievalRequest,
        filters_payload: Mapping[str, object],
        query_terms: tuple[str, ...],
        config: SparseRetrieverConfig,
    ) -> list[SparseChunkRecord]:
        raise RuntimeError("raw SQL password at C:\\secret\\sparse.sql with chunk content")


class SlowSparseBackend:
    backend_kind = "postgres"

    async def search(
        self,
        *,
        request: RetrievalRequest,
        filters_payload: Mapping[str, object],
        query_terms: tuple[str, ...],
        config: SparseRetrieverConfig,
    ) -> list[SparseChunkRecord]:
        await asyncio.sleep(1.0)
        return []


def _auth() -> AuthContext:
    return AuthContext(
        user_id="user-1",
        tenant_id="tenant-a",
        roles=("hr",),
        department="people",
        permissions=("document:read",),
    )


def _records() -> list[SparseChunkRecord]:
    return [
        _record(chunk_id="clause", content="制度编号 HR-2026-01 适用于员工手册。", rank=0.95),
        _record(chunk_id="error-code", content="ERR-42 表示索引任务失败。", rank=0.9),
        _record(chunk_id="person", content="张三 是该制度的审批人。", rank=0.85),
        _record(chunk_id="product", content="产品型号 ZX-900 支持离线部署。", rank=0.8),
    ]


def _record(
    *,
    chunk_id: str,
    content: str,
    rank: float = 0.9,
    tenant_id: str = "tenant-a",
    department: str = "people",
    status: str = "active",
    deleted_at: datetime | None = None,
    acl: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
    title_path: list[str] | None = None,
) -> SparseChunkRecord:
    return SparseChunkRecord(
        tenant_id=tenant_id,
        document_id=f"doc-{chunk_id}",
        version_id=f"ver-{chunk_id}",
        chunk_id=chunk_id,
        status=status,
        content=content,
        source_type="markdown",
        source_uri=f"kb://{chunk_id}.md",
        title_path=title_path if title_path is not None else ["Policy", chunk_id],
        page_start=1,
        page_end=1,
        acl=acl or {"visibility": "tenant", "allowed_roles": ["hr"]},
        metadata=metadata or {"department": department, "source_type": "markdown"},
        deleted_at=deleted_at,
        rank=rank,
    )


def _chunk_model(
    *,
    id_: str,
    chunk_id: str,
    content: str,
    acl: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
) -> ChunkModel:
    return ChunkModel(
        id=id_,
        tenant_id="tenant-a",
        document_id=f"doc-{chunk_id}",
        version_id=f"ver-{chunk_id}",
        chunk_id=chunk_id,
        created_by="user-1",
        status="active",
        source_type="markdown",
        source_uri=f"kb://{chunk_id}.md",
        title_path=["Policy", chunk_id],
        content=content,
        page_start=1,
        page_end=1,
        token_count=10,
        acl=acl or {"visibility": "tenant", "allowed_roles": ["hr"]},
        checksum=f"checksum-{chunk_id}",
        section_ids=["section-1"],
        metadata_=metadata or {"department": "people", "source_type": "markdown"},
    )

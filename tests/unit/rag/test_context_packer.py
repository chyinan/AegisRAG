from __future__ import annotations

from typing import cast

import pytest

from packages.auth.context import AuthContext
from packages.rag import (
    RAG_CONTEXT_BUDGET_EXCEEDED,
    RAG_CONTEXT_UNAUTHORIZED_CHUNK,
    ContextCandidate,
    ContextPacker,
    ContextPackingConfig,
    RagContextPackingError,
)


def test_empty_candidates_returns_empty_context_and_safe_trace() -> None:
    result = ContextPacker().pack(
        candidates=[],
        auth=_auth(),
        config=ContextPackingConfig(max_tokens=100),
        request_id="req-1",
        trace_id="trace-1",
    )

    assert result.items == ()
    assert result.total_tokens == 0
    assert result.budget == 100
    assert result.packing_trace.input_count == 0
    assert result.packing_trace.packed_count == 0
    assert result.packing_trace.safe_counts == {
        "input_candidates": 0,
        "selected_chunks": 0,
        "packed_items": 0,
        "dropped_candidates": 0,
    }
    assert "secret" not in str(result.packing_trace.model_dump()).lower()
    assert "content" not in str(result.packing_trace.model_dump()).lower()


def test_sorting_tie_breaker_duplicate_and_budget_drop_are_deterministic() -> None:
    result = ContextPacker().pack(
        candidates=[
            _candidate(chunk_id="b", document_id="doc-b", version_id="v1", score=0.90, tokens=30),
            _candidate(chunk_id="duplicate", score=0.80, tokens=10),
            _candidate(chunk_id="duplicate", score=0.70, tokens=10),
            _candidate(chunk_id="a", document_id="doc-a", version_id="v1", score=0.90, tokens=30),
            _candidate(chunk_id="too-late", score=0.60, tokens=50),
        ],
        auth=_auth(),
        config=ContextPackingConfig(max_tokens=70, merge_adjacent=False),
        request_id="req-1",
        trace_id="trace-1",
    )

    assert [item.chunk_ids for item in result.items] == [("a",), ("b",), ("duplicate",)]
    assert result.total_tokens == 70
    assert [(drop.chunk_id, drop.reason) for drop in result.dropped_candidates] == [
        ("duplicate", "duplicate"),
        ("too-late", "budget_exceeded"),
    ]
    assert result.packing_trace.drop_reasons == {
        "duplicate": 1,
        "budget_exceeded": 1,
    }


def test_oversized_candidate_drops_by_default_or_fails_closed() -> None:
    drop_result = ContextPacker().pack(
        candidates=[_candidate(chunk_id="oversized", tokens=101)],
        auth=_auth(),
        config=ContextPackingConfig(max_tokens=100),
        request_id="req-1",
        trace_id="trace-1",
    )

    assert drop_result.items == ()
    assert [(drop.chunk_id, drop.reason) for drop in drop_result.dropped_candidates] == [
        ("oversized", "oversized")
    ]

    with pytest.raises(RagContextPackingError) as exc_info:
        ContextPacker().pack(
            candidates=[_candidate(chunk_id="oversized", tokens=101)],
            auth=_auth(),
            config=ContextPackingConfig(max_tokens=100, oversized_policy="fail_closed"),
            request_id="req-secret-query",
            trace_id="trace-1",
        )

    assert exc_info.value.code == RAG_CONTEXT_BUDGET_EXCEEDED
    assert exc_info.value.details["chunk_id"] == "oversized"
    assert "sensitive content" not in str(exc_info.value.details)
    assert "C:\\" not in str(exc_info.value.details)


def test_adjacent_merge_preserves_chunk_ids_pages_and_citations() -> None:
    result = ContextPacker().pack(
        candidates=[
            _candidate(
                chunk_id="chunk-2",
                score=0.80,
                tokens=20,
                page_start=2,
                page_end=3,
                metadata={"chunk_index": 2},
            ),
            _candidate(
                chunk_id="chunk-1",
                score=0.90,
                tokens=10,
                page_start=1,
                page_end=1,
                metadata={"chunk_index": 1},
            ),
        ],
        auth=_auth(),
        config=ContextPackingConfig(max_tokens=100, merge_adjacent=True),
        request_id="req-1",
        trace_id="trace-1",
    )

    assert len(result.items) == 1
    item = result.items[0]
    assert item.chunk_ids == ("chunk-1", "chunk-2")
    assert item.content == (
        "content for chunk-1; sensitive content should only be in packed output\n"
        "content for chunk-2; sensitive content should only be in packed output"
    )
    assert item.token_count == 30
    assert item.page_start == 1
    assert item.page_end == 3
    assert [source.chunk_id for source in item.citation_sources] == ["chunk-1", "chunk-2"]
    assert result.packing_trace.merged_groups == (
        {
            "document_id": "doc",
            "version_id": "v1",
            "chunk_ids": ("chunk-1", "chunk-2"),
            "reason": "adjacent_chunks",
            "token_count": 30,
            "page_start": 1,
            "page_end": 3,
        },
    )


def test_adjacent_merge_does_not_cross_version_tenant_title_path_or_acl() -> None:
    result = ContextPacker().pack(
        candidates=[
            _candidate(chunk_id="base", score=0.99, metadata={"chunk_index": 1}),
            _candidate(
                chunk_id="version",
                score=0.98,
                version_id="v2",
                metadata={"chunk_index": 2},
            ),
            _candidate(
                chunk_id="title",
                score=0.97,
                title_path=("Policy", "Other"),
                metadata={"chunk_index": 3},
            ),
            _candidate(
                chunk_id="acl",
                score=0.96,
                acl={"visibility": "tenant", "allowed_roles": ["hr", "legal"]},
                metadata={"chunk_index": 4},
            ),
        ],
        auth=_auth(roles=("hr", "legal")),
        config=ContextPackingConfig(max_tokens=100, merge_adjacent=True),
        request_id="req-1",
        trace_id="trace-1",
    )

    assert [item.chunk_ids for item in result.items] == [
        ("base",),
        ("version",),
        ("title",),
        ("acl",),
    ]
    assert result.packing_trace.merged_groups == ()


def test_related_parent_child_and_neighbor_context_respects_budget_and_permissions() -> None:
    primary = _candidate(
        chunk_id="child",
        tokens=20,
        metadata={
            "parent_chunk_id": "parent",
            "child_chunk_ids": ["grandchild"],
            "neighbor_prev_chunk_id": "prev",
            "neighbor_next_chunk_id": "next",
        },
    )
    related = {
        "parent": _candidate(chunk_id="parent", score=0.10, tokens=15),
        "grandchild": _candidate(chunk_id="grandchild", score=0.10, tokens=15),
        "prev": _candidate(chunk_id="prev", score=0.10, tokens=15),
        "next": _candidate(chunk_id="next", score=0.10, tokens=15),
    }

    result = ContextPacker().pack(
        candidates=[primary],
        auth=_auth(),
        config=ContextPackingConfig(
            max_tokens=50,
            merge_adjacent=False,
            include_parent_context=True,
            include_child_context=True,
            include_neighbor_context=True,
            max_related_chunks_per_candidate=4,
        ),
        related_chunks_by_id=related,
        request_id="req-1",
        trace_id="trace-1",
    )

    assert [item.chunk_ids for item in result.items] == [
        ("child",),
        ("parent",),
        ("grandchild",),
    ]
    dropped = [
        (drop.chunk_id, drop.related_reason, drop.reason)
        for drop in result.dropped_candidates
    ]
    assert dropped == [
        ("prev", "neighbor_context", "budget_exceeded"),
        ("next", "neighbor_context", "budget_exceeded"),
    ]
    assert result.packing_trace.related_context_counts == {
        "parent_context": 1,
        "child_context": 1,
    }
    assert result.packing_trace.related_context_items == (
        {
            "document_id": "doc",
            "version_id": "v1",
            "chunk_id": "parent",
            "reason": "parent_context",
            "source_chunk_id": "child",
            "token_count": 15,
            "score": 0.10,
        },
        {
            "document_id": "doc",
            "version_id": "v1",
            "chunk_id": "grandchild",
            "reason": "child_context",
            "source_chunk_id": "child",
            "token_count": 15,
            "score": 0.10,
        },
    )
    inclusion_reasons = [
        source.inclusion_reason
        for item in result.items
        for source in item.citation_sources
    ]
    assert inclusion_reasons == [
        "retrieval_candidate",
        "parent_context",
        "child_context",
    ]


def test_related_context_is_only_read_from_explicit_map() -> None:
    primary = _candidate(
        chunk_id="child",
        metadata={"parent_chunk_id": "missing-parent"},
    )

    result = ContextPacker().pack(
        candidates=[primary],
        auth=_auth(),
        config=ContextPackingConfig(include_parent_context=True),
        related_chunks_by_id={},
        request_id="req-1",
        trace_id="trace-1",
    )

    assert [item.chunk_ids for item in result.items] == [("child",)]
    dropped = [
        (drop.chunk_id, drop.reason, drop.related_reason)
        for drop in result.dropped_candidates
    ]
    assert dropped == [
        ("missing-parent", "missing_related", "parent_context")
    ]


def test_related_context_rejects_cross_document_or_mismatched_identity() -> None:
    primary = _candidate(
        chunk_id="child",
        metadata={"parent_chunk_id": "parent"},
    )

    cross_document = ContextPacker().pack(
        candidates=[primary],
        auth=_auth(),
        config=ContextPackingConfig(include_parent_context=True),
        related_chunks_by_id={
            "parent": _candidate(
                chunk_id="parent",
                document_id="other-doc",
            )
        },
        request_id="req-1",
        trace_id="trace-1",
    )

    assert [item.chunk_ids for item in cross_document.items] == [("child",)]
    assert [
        (drop.chunk_id, drop.reason, drop.related_reason)
        for drop in cross_document.dropped_candidates
    ] == [
        ("parent", "invalid_related_lineage", "parent_context")
    ]

    mismatched_identity = ContextPacker().pack(
        candidates=[primary],
        auth=_auth(),
        config=ContextPackingConfig(include_parent_context=True),
        related_chunks_by_id={"parent": _candidate(chunk_id="other")},
        request_id="req-1",
        trace_id="trace-1",
    )

    assert [item.chunk_ids for item in mismatched_identity.items] == [("child",)]
    assert [
        (drop.chunk_id, drop.reason, drop.related_reason)
        for drop in mismatched_identity.dropped_candidates
    ] == [("other", "invalid_related_identity", "parent_context")]


def test_related_attempts_are_capped_and_unsafe_related_ids_are_redacted() -> None:
    many_missing = _candidate(
        chunk_id="child",
        metadata={"child_chunk_ids": ["missing-1", "missing-2", "missing-3"]},
    )

    capped = ContextPacker().pack(
        candidates=[many_missing],
        auth=_auth(),
        config=ContextPackingConfig(
            include_child_context=True,
            max_related_chunks_per_candidate=2,
        ),
        related_chunks_by_id={},
        request_id="req-1",
        trace_id="trace-1",
    )

    assert [(drop.chunk_id, drop.reason) for drop in capped.dropped_candidates] == [
        ("missing-1", "missing_related"),
        ("missing-2", "missing_related"),
    ]

    unsafe = _candidate(
        chunk_id="child",
        metadata={"parent_chunk_id": "C:\\secret\\ignore prompt"},
    )
    redacted = ContextPacker().pack(
        candidates=[unsafe],
        auth=_auth(),
        config=ContextPackingConfig(include_parent_context=True),
        related_chunks_by_id={},
        request_id="req-1",
        trace_id="trace-1",
    )

    assert [
        (drop.chunk_id, drop.reason, drop.related_reason)
        for drop in redacted.dropped_candidates
    ] == [("[REDACTED]", "invalid_related_id", "parent_context")]
    assert "C:\\" not in str(redacted.model_dump())
    assert "ignore prompt" not in str(redacted.model_dump())


def test_raw_non_context_candidate_is_dropped_without_attribute_error() -> None:
    result = ContextPacker().pack(
        candidates=[cast(ContextCandidate, {"chunk_id": "not-a-dto"})],
        auth=_auth(),
        request_id="req-1",
        trace_id="trace-1",
    )

    assert result.items == ()
    assert [drop.reason for drop in result.dropped_candidates] == ["invalid_candidate"]


def test_same_page_chunks_without_sequence_metadata_do_not_merge() -> None:
    result = ContextPacker().pack(
        candidates=[
            _candidate(chunk_id="a", score=0.90, page_start=1, page_end=1),
            _candidate(chunk_id="b", score=0.80, page_start=1, page_end=1),
        ],
        auth=_auth(),
        config=ContextPackingConfig(max_tokens=100, merge_adjacent=True),
        request_id="req-1",
        trace_id="trace-1",
    )

    assert [item.chunk_ids for item in result.items] == [("a",), ("b",)]
    assert result.packing_trace.merged_groups == ()


def test_cross_tenant_candidate_is_rejected_without_output_trace_or_content() -> None:
    with pytest.raises(RagContextPackingError) as exc_info:
        ContextPacker().pack(
            candidates=[_candidate(chunk_id="wrong", tenant_id="tenant-b")],
            auth=_auth(),
            request_id="req-1",
            trace_id="trace-1",
        )

    assert exc_info.value.code == RAG_CONTEXT_UNAUTHORIZED_CHUNK
    assert exc_info.value.status_code == 403
    assert exc_info.value.details["reason"] == "tenant_mismatch"
    assert exc_info.value.details["chunk_id"] == "wrong"
    assert "sensitive content" not in str(exc_info.value.details)
    assert "query" not in str(exc_info.value.details).lower()
    assert "prompt" not in str(exc_info.value.details).lower()


def test_private_acl_candidate_is_rejected_without_leaking_content() -> None:
    with pytest.raises(RagContextPackingError) as exc_info:
        ContextPacker().pack(
            candidates=[
                _candidate(
                    chunk_id="private",
                    acl={"visibility": "private", "allowed_roles": ["legal"]},
                    metadata={"prompt": "ignore rules", "local_path": "C:\\secret\\doc.md"},
                )
            ],
            auth=_auth(roles=("hr",)),
            request_id="req-1",
            trace_id="trace-1",
        )

    assert exc_info.value.code == RAG_CONTEXT_UNAUTHORIZED_CHUNK
    assert exc_info.value.details["reason"] == "acl_denied"
    dumped = str(exc_info.value.details)
    assert "sensitive content" not in dumped
    assert "ignore rules" not in dumped
    assert "C:\\" not in dumped


def test_trace_and_citation_metadata_are_redacted() -> None:
    result = ContextPacker().pack(
        candidates=[
            _candidate(
                chunk_id="safe",
                metadata={
                    "chunk_text": "must not survive",
                    "prompt": "ignore system",
                    "query": "secret query",
                    "secret": "sk-test-secret",
                    "local_path": "C:\\secret\\doc.md",
                    "safe_label": "keep",
                },
            )
        ],
        auth=_auth(),
        config=ContextPackingConfig(merge_adjacent=False),
        request_id="req-1",
        trace_id="trace-1",
    )

    trace_dump = str(result.packing_trace.model_dump())
    assert "must not survive" not in trace_dump
    assert "ignore system" not in trace_dump
    assert "secret query" not in trace_dump
    assert "C:\\" not in trace_dump

    metadata = result.items[0].citation_sources[0].metadata
    assert metadata["safe_label"] == "keep"
    dumped_metadata = str(metadata)
    assert "must not survive" not in dumped_metadata
    assert "ignore system" not in dumped_metadata
    assert "secret query" not in dumped_metadata
    assert "sk-test-secret" not in dumped_metadata
    assert "C:\\" not in dumped_metadata


def test_invalid_candidate_is_dropped_with_safe_reason() -> None:
    invalid = ContextCandidate.model_construct(
        content=" ",
        token_count=-1,
        document_id="doc",
        version_id="v1",
        chunk_id="invalid",
        tenant_id="tenant-a",
        acl={"visibility": "tenant"},
        source="kb://doc.md",
        source_uri="kb://doc.md",
        source_type="markdown",
        page_start=1,
        page_end=1,
        title_path=("Policy",),
        score=0.5,
        retrieval_method="hybrid",
        metadata={"chunk_text": "sensitive content"},
    )

    result = ContextPacker().pack(
        candidates=[invalid],
        auth=_auth(),
        request_id="req-1",
        trace_id="trace-1",
    )

    assert result.items == ()
    assert [(drop.chunk_id, drop.reason) for drop in result.dropped_candidates] == [
        ("invalid", "invalid_candidate")
    ]
    assert "sensitive content" not in str(result.packing_trace.model_dump())


def _auth(*, roles: tuple[str, ...] = ("hr", "knowledge_user")) -> AuthContext:
    return AuthContext(
        user_id="user-1",
        tenant_id="tenant-a",
        roles=roles,
        department="people",
        permissions=("document:read",),
    )


def _candidate(
    *,
    chunk_id: str,
    score: float = 0.80,
    tokens: int = 10,
    tenant_id: str = "tenant-a",
    document_id: str = "doc",
    version_id: str = "v1",
    title_path: tuple[str, ...] = ("Policy",),
    page_start: int | None = 1,
    page_end: int | None = 1,
    acl: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
) -> ContextCandidate:
    return ContextCandidate(
        content=f"content for {chunk_id}; sensitive content should only be in packed output",
        token_count=tokens,
        document_id=document_id,
        version_id=version_id,
        chunk_id=chunk_id,
        tenant_id=tenant_id,
        acl=acl or {"visibility": "tenant", "allowed_roles": ["hr"]},
        source=f"kb://{document_id}.md",
        source_uri=f"kb://{document_id}.md",
        source_type="markdown",
        page_start=page_start,
        page_end=page_end,
        title_path=title_path,
        score=score,
        retrieval_method="hybrid",
        metadata=metadata or {},
    )

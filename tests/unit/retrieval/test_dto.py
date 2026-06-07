from __future__ import annotations

from math import nan

import pytest

from packages.retrieval.dto import MAX_RETRIEVAL_TOP_K, RetrievalCandidate, RetrievalRequest
from packages.retrieval.exceptions import RETRIEVAL_INVALID_REQUEST, RetrievalError


def test_retrieval_request_requires_structured_fields() -> None:
    request = RetrievalRequest(
        query="  policy renewal  ",
        top_k=5,
        metadata_filter={"department": "hr"},
        score_threshold=0.25,
        request_id="req-1",
        trace_id="trace-1",
    )

    assert request.query == "policy renewal"
    assert request.top_k == 5
    assert request.metadata_filter == {"department": "hr"}
    assert request.score_threshold == 0.25
    assert request.request_id == "req-1"
    assert request.trace_id == "trace-1"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("query", " "),
        ("top_k", 0),
        ("top_k", -1),
        ("top_k", True),
        ("top_k", MAX_RETRIEVAL_TOP_K + 1),
        ("score_threshold", -0.1),
        ("score_threshold", 1.1),
        ("score_threshold", nan),
        ("request_id", " "),
        ("trace_id", " "),
    ],
)
def test_retrieval_request_rejects_invalid_boundary_values(field: str, value: object) -> None:
    payload: dict[str, object] = {
        "query": "policy",
        "top_k": 10,
        "metadata_filter": {},
        "score_threshold": None,
        "request_id": "req-1",
        "trace_id": "trace-1",
    }
    payload[field] = value

    with pytest.raises(RetrievalError) as exc_info:
        RetrievalRequest.model_validate(payload)

    assert exc_info.value.code == RETRIEVAL_INVALID_REQUEST


@pytest.mark.parametrize(
    "metadata_filter",
    [
        "department = 'hr'",
        ["department", "hr"],
        {"": "hr"},
        {"department": {"$ne": "hr"}},
        {"department": object()},
        {"$where": "tenant_id == 'tenant-a'"},
        {"department": ["hr", "legal"]},
        {"department": {nan}},
    ],
)
def test_retrieval_request_rejects_non_structured_metadata_filter(
    metadata_filter: object,
) -> None:
    with pytest.raises(RetrievalError) as exc_info:
        RetrievalRequest.model_validate(
            {
                "query": "policy",
                "top_k": 10,
                "metadata_filter": metadata_filter,
                "request_id": "req-1",
                "trace_id": "trace-1",
            }
        )

    assert exc_info.value.code == RETRIEVAL_INVALID_REQUEST


def test_retrieval_request_metadata_filter_has_no_shared_mutable_default() -> None:
    first = RetrievalRequest(query="policy", request_id="req-1", trace_id="trace-1")
    second = RetrievalRequest(query="policy", request_id="req-2", trace_id="trace-2")

    assert first.metadata_filter == {}
    assert second.metadata_filter == {}
    assert first.metadata_filter is not second.metadata_filter


def test_invalid_retrieval_request_error_details_are_safe() -> None:
    with pytest.raises(RetrievalError) as exc_info:
        RetrievalRequest(
            query=" ",
            top_k=5000,
            request_id=" req-1 ",
            trace_id=" trace-1 ",
        )

    assert exc_info.value.code == RETRIEVAL_INVALID_REQUEST
    assert exc_info.value.details == {
        "error_code": RETRIEVAL_INVALID_REQUEST,
        "error_count": 2,
        "request_id": "req-1",
        "trace_id": "trace-1",
        "top_k": 5000,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"score": nan},
        {"page_start": 0, "page_end": 1},
        {"page_start": 2, "page_end": 1},
        {"page_start": 1, "page_end": None},
    ],
)
def test_retrieval_candidate_rejects_invalid_score_and_page_metadata(
    payload: dict[str, object],
) -> None:
    data: dict[str, object] = {
        "document_id": "doc-1",
        "version_id": "ver-1",
        "chunk_id": "chunk-1",
        "source_type": "markdown",
        "page_start": 1,
        "page_end": 1,
        "title_path": ("Policy",),
        "score": 0.5,
        "retrieval_method": "dense",
        "tenant_id": "tenant-a",
    }
    data.update(payload)

    with pytest.raises(ValueError):
        RetrievalCandidate.model_validate(data)

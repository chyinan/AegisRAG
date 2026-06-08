from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from packages.rag.dto import Citation
from packages.rag.streaming import (
    CitationEventPayload,
    ErrorEventPayload,
    RagStreamEvent,
    TokenEventPayload,
    ToolCallEventPayload,
    ToolResultEventPayload,
    format_sse_event,
    safe_error_event,
)


def test_format_sse_event_uses_event_name_and_json_payload() -> None:
    event = RagStreamEvent(
        event="token",
        payload=TokenEventPayload(
            request_id="req-1",
            trace_id="trace-1",
            index=0,
            delta="基于",
        ),
    )

    frame = format_sse_event(event)

    assert frame.startswith("event: token\n")
    assert frame.endswith("\n\n")
    payload = json.loads(frame.split("data: ", maxsplit=1)[1])
    assert payload == {
        "request_id": "req-1",
        "trace_id": "trace-1",
        "event": "token",
        "index": 0,
        "delta": "基于",
    }


def test_stream_event_rejects_unknown_event_type_and_mismatched_payload() -> None:
    with pytest.raises(ValidationError):
        RagStreamEvent.model_validate(
            {
                "event": "unknown",
                "payload": TokenEventPayload(
                    request_id="req-1",
                    trace_id="trace-1",
                    index=0,
                    delta="token",
                ).model_dump(),
            }
        )

    with pytest.raises(ValidationError):
        RagStreamEvent(
            event="final",
            payload=TokenEventPayload(
                request_id="req-1",
                trace_id="trace-1",
                index=0,
                delta="token",
            ),
        )


def test_payloads_reject_empty_request_id() -> None:
    with pytest.raises(ValidationError):
        TokenEventPayload(request_id=" ", trace_id="trace-1", index=0, delta="token")


def test_citation_event_contains_complete_source_metadata() -> None:
    citation = Citation(
        document_id="doc-1",
        version_id="v1",
        chunk_id="chunk-1",
        source_display_name="policy.md",
        source_type="markdown",
        page_start=1,
        page_end=2,
        title_path=("Policy", "Leave"),
        retrieval_method="hybrid",
        score=0.91,
    )
    event = RagStreamEvent(
        event="citation",
        payload=CitationEventPayload(
            request_id="req-1",
            trace_id="trace-1",
            citation=citation,
        ),
    )

    data = json.loads(format_sse_event(event).split("data: ", maxsplit=1)[1])

    assert data["event"] == "citation"
    assert data["request_id"] == "req-1"
    assert data["citation"] == {
        "document_id": "doc-1",
        "version_id": "v1",
        "chunk_id": "chunk-1",
        "source_display_name": "policy.md",
        "source_ref": None,
        "source_type": "markdown",
        "page_start": 1,
        "page_end": 2,
        "title_path": ["Policy", "Leave"],
        "retrieval_method": "hybrid",
        "score": 0.91,
    }


def test_citation_direct_construction_sanitizes_unsafe_source_display_name() -> None:
    citation = Citation(
        document_id="doc-1",
        version_id="v1",
        chunk_id="chunk-1",
        source_display_name="tenant-bucket/policy.pdf",
        source_type="markdown",
        title_path=("Policy",),
        retrieval_method="hybrid",
        score=0.91,
    )

    payload = citation.model_dump(mode="json")

    assert payload["source_display_name"] == "Source unavailable"
    assert "tenant-bucket/policy.pdf" not in str(payload)


def test_safe_error_event_redacts_sensitive_details() -> None:
    event = safe_error_event(
        request_id="req-1",
        trace_id="trace-1",
        code="LLM_STREAM_FAILED",
        message="LLM stream failed.",
        details={
            "stage": "generation_stream",
            "query": "secret query",
            "prompt": "secret prompt",
            "content": "secret content",
            "provider": "fake",
            "output_tokens": 3,
        },
        terminal=True,
    )

    assert isinstance(event.payload, ErrorEventPayload)
    data = event.payload.model_dump(mode="json")
    assert data["details"] == {
        "stage": "generation_stream",
        "query": "[REDACTED]",
        "prompt": "[REDACTED]",
        "content": "[REDACTED]",
        "provider": "fake",
        "output_tokens": 3,
    }
    assert data["terminal"] is True


def test_safe_error_event_redacts_raw_source_locators_and_token_urls() -> None:
    event = safe_error_event(
        request_id="req-1",
        trace_id="trace-1",
        code="SOURCE_ERROR",
        message="Source error.",
        details={
            "source_uri": "minio://tenant-bucket/raw/internal/policy.pdf",
            "object_key": "tenant-bucket/raw/internal/policy.pdf",
            "url": "https://example.test/private.pdf?token=secret",
            "stage": "source_resolution",
        },
    )

    data = event.payload.model_dump(mode="json")

    assert data["details"]["source_uri"] == "[REDACTED]"
    assert data["details"]["object_key"] == "[REDACTED]"
    assert data["details"]["url"] == "[REDACTED]"
    assert data["details"]["stage"] == "source_resolution"
    assert "tenant-bucket" not in str(data)
    assert "token=secret" not in str(data)


def test_reserved_tool_events_have_safe_payload_contracts() -> None:
    event = RagStreamEvent(
        event="tool_call",
        payload=ToolCallEventPayload(
            request_id="req-1",
            trace_id="trace-1",
            tool_call_id="call-1",
            tool_name="rag_search",
            metadata={"query": "secret query", "result_count": 1},
        ),
    )
    result = RagStreamEvent(
        event="tool_result",
        payload=ToolResultEventPayload(
            request_id="req-1",
            trace_id="trace-1",
            tool_call_id="call-1",
            tool_name="rag_search",
            status="success",
            metadata={"content": "secret content", "result_count": 1},
        ),
    )

    call_data = json.loads(format_sse_event(event).split("data: ", maxsplit=1)[1])
    result_data = json.loads(format_sse_event(result).split("data: ", maxsplit=1)[1])

    assert call_data["event"] == "tool_call"
    assert call_data["metadata"]["query"] == "[REDACTED]"
    assert result_data["event"] == "tool_result"
    assert result_data["metadata"]["content"] == "[REDACTED]"

from packages.common.context import RequestContext
from packages.common.logging import (
    REDACTED_VALUE,
    REQUEST_COMPLETED_EVENT,
    build_request_log_event,
    redact_sensitive_data,
)


def test_redact_sensitive_data_handles_case_insensitive_keys_recursively() -> None:
    payload = {
        "Authorization": "Bearer token",
        "access_token": "access",
        "Api-Key": "key",
        "nested": {
            "TOKEN": "token",
            "secret": "secret",
            "Password": "password",
            "credential": "credential",
            "safe": "metadata",
        },
    }

    assert redact_sensitive_data(payload) == {
        "Authorization": REDACTED_VALUE,
        "access_token": REDACTED_VALUE,
        "Api-Key": REDACTED_VALUE,
        "nested": {
            "TOKEN": REDACTED_VALUE,
            "secret": REDACTED_VALUE,
            "Password": REDACTED_VALUE,
            "credential": REDACTED_VALUE,
            "safe": "metadata",
        },
    }


def test_redact_sensitive_data_blocks_content_keys_and_secret_like_values() -> None:
    payload = {
        "prompt": "Use this full prompt",
        "document_content": "full enterprise document",
        "tool_args": {"filename": r"D:\secret\contract.pdf"},
        "file_path": r"D:\secret\contract.pdf",
        "note": "Bearer secret-token",
        "message": "api key sk-testsecret123",
        "safe": "metadata",
    }

    assert redact_sensitive_data(payload) == {
        "prompt": REDACTED_VALUE,
        "document_content": REDACTED_VALUE,
        "tool_args": REDACTED_VALUE,
        "file_path": REDACTED_VALUE,
        "note": REDACTED_VALUE,
        "message": REDACTED_VALUE,
        "safe": "metadata",
    }


def test_redact_sensitive_data_preserves_safe_token_usage_observability_fields() -> None:
    payload = {
        "token_usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
        },
        "access_token": "secret-token",
    }

    assert redact_sensitive_data(payload) == {
        "token_usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
        },
        "access_token": REDACTED_VALUE,
    }


def test_redact_sensitive_data_blocks_chat_rag_content_and_local_paths() -> None:
    payload = {
        "answer": "full assistant answer",
        "assistant_message": "full assistant message",
        "user_message": "full user message",
        "chunk_content": "full chunk content",
        "provider_raw_payload": "raw provider payload",
        "note": r"D:\Programs\RAG-Local-System\.env",
        "safe_count": 3,
    }

    assert redact_sensitive_data(payload) == {
        "answer": REDACTED_VALUE,
        "assistant_message": REDACTED_VALUE,
        "user_message": REDACTED_VALUE,
        "chunk_content": REDACTED_VALUE,
        "provider_raw_payload": REDACTED_VALUE,
        "note": REDACTED_VALUE,
        "safe_count": 3,
    }


def test_request_log_event_uses_fixed_snake_case_fields_and_no_body_fields() -> None:
    context = RequestContext(
        request_id="req-123",
        trace_id="trace-123",
        session_id="session-123",
    )

    event = build_request_log_event(
        context=context,
        tenant_id="tenant-abc",
        user_id="user-123",
        method="GET",
        path="/health",
        status_code=200,
        latency_ms=4.23456,
        error_code=None,
        role_count=2,
        permission_count=3,
    )

    assert event == {
        "event": REQUEST_COMPLETED_EVENT,
        "request_id": "req-123",
        "trace_id": "trace-123",
        "tenant_id": "tenant-abc",
        "user_id": "user-123",
        "session_id": "session-123",
        "method": "GET",
        "path": "/health",
        "status_code": 200,
        "latency_ms": 4.235,
        "error_code": None,
        "role_count": 2,
        "permission_count": 3,
    }
    forbidden_fields = {"body", "request_body", "response_body", "prompt", "document_content"}
    assert forbidden_fields.isdisjoint(event)

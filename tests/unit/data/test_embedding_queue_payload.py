import pytest

from packages.auth.context import AuthContext
from packages.common.context import AuthenticatedRequestContext
from packages.data.queue.contracts import QueuePayload
from packages.data.queue.embedding import (
    EMBEDDING_JOB_TYPE,
    build_embedding_queue_payload,
)


def test_build_embedding_queue_payload_contains_safe_ids_only() -> None:
    context = AuthenticatedRequestContext(
        request_id="req-1",
        trace_id="trace-1",
        auth=AuthContext(user_id="user-1", tenant_id="tenant-1"),
    )

    payload = build_embedding_queue_payload(
        context=context,
        job_id="job-1",
        document_id="doc-1",
        version_id="ver-1",
    )

    assert payload.model_dump(mode="json") == {
        "request_id": "req-1",
        "trace_id": "trace-1",
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "job_type": EMBEDDING_JOB_TYPE,
        "resource_id": "job-1",
        "parameters": {"document_id": "doc-1", "version_id": "ver-1"},
    }
    dumped = repr(payload.model_dump(mode="json"))
    assert "content" not in dumped
    assert "prompt" not in dumped
    assert "api_key" not in dumped
    assert "C:\\" not in dumped


def test_embedding_queue_payload_rejects_sensitive_or_absolute_path_parameters() -> None:
    with pytest.raises(ValueError):
        QueuePayload(
            request_id="req-1",
            trace_id="trace-1",
            tenant_id="tenant-1",
            user_id="user-1",
            job_type=EMBEDDING_JOB_TYPE,
            resource_id="job-1",
            parameters={"document_id": "doc-1", "version_id": "ver-1", "prompt": "secret"},
        )

    with pytest.raises(ValueError):
        QueuePayload(
            request_id="req-1",
            trace_id="trace-1",
            tenant_id="tenant-1",
            user_id="user-1",
            job_type=EMBEDDING_JOB_TYPE,
            resource_id="job-1",
            parameters={"document_id": "doc-1", "version_id": "C:\\secret\\file.txt"},
        )

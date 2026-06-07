from apps.worker.jobs.ingestion_jobs import process_document_ingestion
from packages.auth.context import AuthContext
from packages.common.context import AuthenticatedRequestContext
from packages.data.queue.ingestion import build_ingestion_queue_payload


def test_build_ingestion_queue_payload_contains_ids_only() -> None:
    context = AuthenticatedRequestContext(
        request_id="req-1",
        trace_id="trace-1",
        auth=AuthContext(user_id="user-1", tenant_id="tenant-1"),
    )

    payload = build_ingestion_queue_payload(
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
        "job_type": "ingestion.process_document",
        "resource_id": "job-1",
        "parameters": {"document_id": "doc-1", "version_id": "ver-1"},
    }
    dumped = repr(payload.model_dump(mode="json"))
    assert "content" not in dumped
    assert "prompt" not in dumped
    assert "token" not in dumped


def test_process_document_ingestion_validates_id_only_queue_payload() -> None:
    class Result:
        status = "parsed"
        document_id = "doc-1"
        version_id = "ver-1"
        job_id = "job-1"
        section_count = 1

    class FakeParseService:
        async def parse_job(
            self,
            context: AuthenticatedRequestContext,
            *,
            job_id: str,
            document_id: str,
            version_id: str,
        ) -> Result:
            assert context.trace_id == "trace-1"
            return Result()

    result = process_document_ingestion(
        {
            "request_id": "req-1",
            "trace_id": "trace-1",
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "job_type": "ingestion.process_document",
            "resource_id": "job-1",
            "parameters": {"document_id": "doc-1", "version_id": "ver-1"},
        },
        parse_service=FakeParseService(),
    )

    assert result == {
        "status": "parsed",
        "job_type": "ingestion.process_document",
        "resource_id": "job-1",
        "document_id": "doc-1",
        "version_id": "ver-1",
        "section_count": 1,
    }


def test_process_document_ingestion_delegates_to_parse_service_after_payload_validation() -> None:
    class Result:
        status = "parsed"
        document_id = "doc-1"
        version_id = "ver-1"
        job_id = "job-1"
        section_count = 2

    class FakeParseService:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def parse_job(
            self,
            context: AuthenticatedRequestContext,
            *,
            job_id: str,
            document_id: str,
            version_id: str,
        ) -> Result:
            self.calls.append(
                {
                    "context": context,
                    "job_id": job_id,
                    "document_id": document_id,
                    "version_id": version_id,
                }
            )
            return Result()

    service = FakeParseService()

    result = process_document_ingestion(
        {
            "request_id": "req-1",
            "trace_id": "trace-1",
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "job_type": "ingestion.process_document",
            "resource_id": "job-1",
            "parameters": {"document_id": "doc-1", "version_id": "ver-1"},
        },
        parse_service=service,
    )

    assert result == {
        "status": "parsed",
        "job_type": "ingestion.process_document",
        "resource_id": "job-1",
        "document_id": "doc-1",
        "version_id": "ver-1",
        "section_count": 2,
    }
    assert service.calls[0]["job_id"] == "job-1"


def test_process_document_ingestion_rejects_sensitive_or_missing_id_payload() -> None:
    try:
        process_document_ingestion(
            {
                "request_id": "req-1",
                "trace_id": "trace-1",
                "tenant_id": "tenant-1",
                "user_id": "user-1",
                "job_type": "ingestion.process_document",
                "resource_id": "job-1",
                "parameters": {
                    "document_id": "doc-1",
                    "version_id": "ver-1",
                    "document_content": "secret",
                },
            }
        )
    except ValueError as exc:
        assert "payload" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError("expected invalid payload to be rejected")

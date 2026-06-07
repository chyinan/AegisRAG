from pathlib import Path

import pytest
from pydantic import ValidationError
from redis import Redis

from packages.data.queue import rq_worker
from packages.data.queue.contracts import QueuePayload
from packages.data.queue.rq_worker import WorkerSettings


def _valid_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "request_id": "req-1",
        "trace_id": "trace-1",
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "job_type": "ingestion.parse",
        "resource_id": "document-1",
        "parameters": {"version_id": "version-1", "retry": 1, "dry_run": False},
    }
    payload.update(overrides)
    return payload


def test_queue_payload_accepts_json_serializable_summary() -> None:
    payload = QueuePayload.model_validate(_valid_payload())

    assert payload.request_id == "req-1"
    assert payload.trace_id == "trace-1"
    assert payload.parameters["version_id"] == "version-1"


@pytest.mark.parametrize(
    "parameters",
    [
        {"file": object()},
        {"raw_bytes": b"abc"},
        {"path": Path("local.txt")},
        {"access_token": "secret-token"},
        {"api_token": "secret-token"},
        {"auth_token": "secret-token"},
        {"bearer_token": "secret-token"},
        {"minio_secret": "secret"},
        {"minio_secret_key": "secret"},
        {"token": "secret-token"},
        {"prompt": "summarize the full document"},
        {"safe": "sk-testsecret123"},
        {"score": float("nan")},
        {"score": float("inf")},
        {"source_path": r"C:\Users\alice\secret.pdf"},
        {"source_path": "/home/alice/secret.pdf"},
    ],
)
def test_queue_payload_rejects_non_json_or_sensitive_payload(parameters: object) -> None:
    with pytest.raises(ValidationError):
        QueuePayload.model_validate(_valid_payload(parameters=parameters))


def test_worker_settings_reads_distinct_queue_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://redis:6379/0")
    monkeypatch.setenv("WORKER_QUEUE_NAME", "ingestion")
    ingestion = WorkerSettings.from_environment()

    monkeypatch.setenv("WORKER_QUEUE_NAME", "embedding")
    embedding = WorkerSettings.from_environment()

    assert ingestion.queue_name == "ingestion"
    assert embedding.queue_name == "embedding"
    assert ingestion.queue_name != embedding.queue_name


def test_worker_settings_requires_redis_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(ValueError, match="REDIS_URL"):
        WorkerSettings.from_environment()


def test_queue_payload_rejects_sensitive_or_path_like_top_level_identifiers() -> None:
    with pytest.raises(ValidationError):
        QueuePayload.model_validate(_valid_payload(resource_id=r"C:\Users\alice\secret.pdf"))

    with pytest.raises(ValidationError):
        QueuePayload.model_validate(_valid_payload(request_id="Bearer secret-token"))


def test_worker_redis_connection_uses_socket_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_from_url(url: str, **kwargs: object) -> object:
        captured["url"] = url
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(Redis, "from_url", fake_from_url)

    rq_worker._redis_connection(
        WorkerSettings(
            redis_url="redis://redis:6379/0",
            queue_name="ingestion",
            redis_timeout_seconds=3.5,
        )
    )

    assert captured == {
        "url": "redis://redis:6379/0",
        "socket_connect_timeout": 3.5,
        "socket_timeout": 3.5,
    }

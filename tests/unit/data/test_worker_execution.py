"""Worker API contracts, without pretending mocks run a Redis scheduler."""

from unittest.mock import Mock

import pytest

from packages.data.queue import rq_worker
from packages.data.queue.rq_worker import WorkerSettings


@pytest.mark.parametrize("burst", [True, False])
def test_worker_enables_delayed_retry_scheduler(
    monkeypatch: pytest.MonkeyPatch,
    burst: bool,
) -> None:
    settings = WorkerSettings(
        redis_url="redis://localhost:6379/0", queue_name="custom", burst=burst
    )
    worker = Mock()
    worker.work.return_value = True
    factory = Mock(return_value=worker)
    monkeypatch.setattr(rq_worker, "create_worker", factory)

    assert rq_worker.run_worker(settings) is True
    factory.assert_called_once_with(settings)
    worker.work.assert_called_once_with(burst=burst, with_scheduler=True)


def test_worker_reads_configured_consumer_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("WORKER_QUEUE_NAME", " tenant-embedding ")
    monkeypatch.setenv("WORKER_BURST", "yes")
    settings = WorkerSettings.from_environment()
    assert settings.queue_name == "tenant-embedding"
    assert settings.burst is True

from unittest.mock import Mock

import pytest
from redis import Redis
from rq import Retry
from rq.job import Job

from apps.worker.jobs import retry_policy
from packages.common.retry import QueueRetryPolicy


@pytest.mark.parametrize("remaining,expected", [(3, 5), (2, 30), (1, 120), (0, 0), (None, 0)])
def test_current_policy_matches_actual_rq_retry_interval(monkeypatch, remaining, expected) -> None:
    job = Job(connection=Redis(host="127.0.0.1", port=1))
    job.retries_left = remaining
    job.retry_intervals = Retry(max=3, interval=[5, 30, 120]).intervals
    monkeypatch.setattr(retry_policy, "get_current_job", lambda: job)
    policy = retry_policy.current_retry_policy()
    assert policy == QueueRetryPolicy(remaining or 0, expected)
    assert policy.can_retry is bool(remaining)


def test_direct_service_invocation_has_no_queue_policy(monkeypatch) -> None:
    monkeypatch.setattr(retry_policy, "get_current_job", Mock(return_value=None))
    assert retry_policy.current_retry_policy() is None


def test_queue_deadline_matches_rq_whole_second_granularity() -> None:
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 10, 14, 17, 0, 0, 999999, tzinfo=UTC)
    due = QueueRetryPolicy(3, 5).next_retry_at(now)
    assert due == datetime(2026, 10, 14, 17, 0, 5, tzinfo=UTC)
    assert due.timestamp() == int((now + timedelta(seconds=5)).timestamp())
    assert QueueRetryPolicy(0, 0).next_retry_at(now) is None


@pytest.mark.parametrize("remaining,delay", [(-1, 0), (0, -1), (-1, -1)])
def test_queue_retry_policy_rejects_negative_state(remaining: int, delay: int) -> None:
    with pytest.raises(ValueError, match="Retry state must be nonnegative"):
        QueueRetryPolicy(remaining, delay)

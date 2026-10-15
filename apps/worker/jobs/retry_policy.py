"""Translate the current RQ attempt into domain retry policy."""

from rq import get_current_job

from packages.common.retry import QueueRetryPolicy


def current_retry_policy() -> QueueRetryPolicy | None:
    job = get_current_job()
    if job is None:
        return None
    remaining = job.retries_left or 0
    return QueueRetryPolicy(
        remaining_retries=remaining,
        delay_seconds=job.get_retry_interval() if remaining > 0 else 0,
    )

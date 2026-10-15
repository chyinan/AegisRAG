"""Executor-provided retry state, independent of any queue implementation."""

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class QueueRetryPolicy:
    remaining_retries: int
    delay_seconds: int

    def __post_init__(self) -> None:
        if self.remaining_retries < 0 or self.delay_seconds < 0:
            raise ValueError("Retry state must be nonnegative.")

    @property
    def can_retry(self) -> bool:
        return self.remaining_retries > 0

    def next_retry_at(self, now: datetime) -> datetime | None:
        if not self.can_retry:
            return None
        # RQ stores scheduled timestamps at whole-second precision. A stricter
        # microsecond DB deadline could reject its correctly scheduled retry.
        return (now + timedelta(seconds=self.delay_seconds)).replace(microsecond=0)

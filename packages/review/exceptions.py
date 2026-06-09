from __future__ import annotations

from collections.abc import Mapping

from packages.common.errors import DomainError

REVIEW_QUEUE_FORBIDDEN = "REVIEW_QUEUE_FORBIDDEN"
REVIEW_QUEUE_INVALID_ITEM = "REVIEW_QUEUE_INVALID_ITEM"
REVIEW_QUEUE_INVALID_STATUS_TRANSITION = "REVIEW_QUEUE_INVALID_STATUS_TRANSITION"
REVIEW_QUEUE_NOT_FOUND = "REVIEW_QUEUE_NOT_FOUND"
REVIEW_QUEUE_STORAGE_READ_FAILED = "REVIEW_QUEUE_STORAGE_READ_FAILED"
REVIEW_QUEUE_STORAGE_WRITE_FAILED = "REVIEW_QUEUE_STORAGE_WRITE_FAILED"
REVIEW_QUEUE_EVAL_CANDIDATE_FAILED = "REVIEW_QUEUE_EVAL_CANDIDATE_FAILED"


class ReviewQueueError(DomainError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        details: Mapping[str, object] | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(
            code=code,
            message=message,
            details=details,
            status_code=status_code,
        )

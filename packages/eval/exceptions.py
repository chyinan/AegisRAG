from __future__ import annotations

from collections.abc import Mapping

from packages.common.errors import DomainError

EVAL_EVIDENCE_FORBIDDEN = "EVAL_EVIDENCE_FORBIDDEN"
EVAL_EVIDENCE_INVALID_REPORT_FILENAME = "EVAL_EVIDENCE_INVALID_REPORT_FILENAME"
EVAL_EVIDENCE_NOT_FOUND = "EVAL_EVIDENCE_NOT_FOUND"
EVAL_EVIDENCE_PARSE_FAILED = "EVAL_EVIDENCE_PARSE_FAILED"
EVAL_EVIDENCE_STORAGE_READ_FAILED = "EVAL_EVIDENCE_STORAGE_READ_FAILED"


class EvalEvidenceError(DomainError):
    def __init__(
        self,
        *,
        code: str,
        message: str = "Eval evidence operation failed.",
        details: Mapping[str, object] | None = None,
        status_code: int = 400,
    ) -> None:
        super().__init__(code=code, message=message, details=details, status_code=status_code)

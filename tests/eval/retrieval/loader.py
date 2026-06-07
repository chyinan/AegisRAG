from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from tests.eval.retrieval.dto import (
    SAFE_FIXTURE_ID_PATTERN,
    RetrievalEvalCase,
    RetrievalEvalCorpusRecord,
)


class RetrievalEvalDatasetError(ValueError):
    def __init__(self, *, code: str, details: dict[str, object] | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(self.__str__())

    def __str__(self) -> str:
        safe_details = json.dumps(self.details, ensure_ascii=False, sort_keys=True)
        return f"{self.code}: {safe_details}"


def load_retrieval_eval_cases(
    path: Path,
    *,
    min_case_count: int = 20,
) -> tuple[RetrievalEvalCase, ...]:
    payload = _load_payload(path)
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise RetrievalEvalDatasetError(
            code="invalid_top_level",
            details={"field": "cases", "file": path.name},
        )
    if len(raw_cases) < min_case_count:
        raise RetrievalEvalDatasetError(
            code="case_count_below_minimum",
            details={
                "case_count": len(raw_cases),
                "min_case_count": min_case_count,
                "file": path.name,
            },
        )

    cases: list[RetrievalEvalCase] = []
    seen: set[str] = set()
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, dict):
            raise RetrievalEvalDatasetError(
                code="invalid_case",
                details={"index": index, "field": "case", "file": path.name},
            )
        try:
            case = RetrievalEvalCase.model_validate(raw_case)
        except ValidationError as exc:
            raise RetrievalEvalDatasetError(
                code="invalid_case",
                details=_safe_validation_details(
                    raw_case=raw_case,
                    index=index,
                    exc=exc,
                    file=path.name,
                ),
            ) from exc
        if case.case_id in seen:
            raise RetrievalEvalDatasetError(
                code="duplicate_case_id",
                details={"case_id": case.case_id, "file": path.name},
            )
        seen.add(case.case_id)
        cases.append(case)
    return tuple(cases)


def load_retrieval_eval_corpus(
    path: Path,
    *,
    case_ids: Iterable[str] | None = None,
) -> tuple[RetrievalEvalCorpusRecord, ...]:
    payload = _load_payload(path)
    raw_corpus = payload.get("corpus", [])
    if not isinstance(raw_corpus, list):
        raise RetrievalEvalDatasetError(
            code="invalid_top_level",
            details={"field": "corpus", "file": path.name},
        )
    known_case_ids = set(case_ids) if case_ids is not None else _case_ids_from_payload(payload)

    records: list[RetrievalEvalCorpusRecord] = []
    seen: set[str] = set()
    for index, raw_record in enumerate(raw_corpus):
        if not isinstance(raw_record, dict):
            raise RetrievalEvalDatasetError(
                code="invalid_corpus_record",
                details={"index": index, "field": "corpus", "file": path.name},
            )
        try:
            record = RetrievalEvalCorpusRecord.model_validate(raw_record)
        except ValidationError as exc:
            raise RetrievalEvalDatasetError(
                code="invalid_corpus_record",
                details=_safe_validation_details(
                    raw_case=raw_record,
                    index=index,
                    exc=exc,
                    file=path.name,
                ),
            ) from exc
        unknown_case_ids = sorted(
            case_id for case_id in record.relevant_case_ids if case_id not in known_case_ids
        )
        if unknown_case_ids:
            raise RetrievalEvalDatasetError(
                code="unknown_relevant_case_id",
                details={
                    "index": index,
                    "unknown_count": len(unknown_case_ids),
                    "file": path.name,
                },
            )
        record_key = (
            f"{record.tenant_id}:{record.document_id}:"
            f"{record.version_id}:{record.chunk_id}"
        )
        if record_key in seen:
            raise RetrievalEvalDatasetError(
                code="duplicate_corpus_record",
                details={"chunk_id": record.chunk_id, "file": path.name},
            )
        seen.add(record_key)
        records.append(record)
    return tuple(records)


def _load_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RetrievalEvalDatasetError(code="file_not_found", details={"file": path.name})
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RetrievalEvalDatasetError(code="read_failed", details={"file": path.name}) from exc
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise RetrievalEvalDatasetError(
            code="invalid_json",
            details={"file": path.name, "line": exc.lineno, "column": exc.colno},
        ) from exc
    if not isinstance(payload, dict):
        raise RetrievalEvalDatasetError(code="invalid_top_level", details={"file": path.name})
    return payload


def _case_ids_from_payload(payload: dict[str, Any]) -> set[str]:
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        return set()
    case_ids: set[str] = set()
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            continue
        case_id = raw_case.get("case_id")
        if isinstance(case_id, str) and case_id.strip():
            case_ids.add(case_id.strip())
    return case_ids


def _safe_validation_details(
    *,
    raw_case: dict[str, object],
    index: int,
    exc: ValidationError,
    file: str,
) -> dict[str, object]:
    first_error = exc.errors(include_url=False, include_context=False)[0]
    loc = first_error.get("loc", ())
    field = ".".join(str(item) for item in loc) if isinstance(loc, tuple) else str(loc)
    details: dict[str, object] = {
        "index": index,
        "field": field,
        "error_count": exc.error_count(),
        "file": file,
    }
    case_id = raw_case.get("case_id")
    if isinstance(case_id, str) and SAFE_FIXTURE_ID_PATTERN.fullmatch(case_id.strip()):
        details["case_id"] = case_id.strip()
    return details

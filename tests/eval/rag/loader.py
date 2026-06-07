from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from tests.eval.rag.dto import (
    FORBIDDEN_TEXT_MARKERS,
    SAFE_FIXTURE_ID_PATTERN,
    RagEvalCase,
    RagEvalCorpusRecord,
    RagEvalDataset,
)


class RagEvalDatasetError(ValueError):
    def __init__(self, *, code: str, details: dict[str, object] | None = None) -> None:
        self.code = code
        self.details = details or {}
        super().__init__(self.__str__())

    def __str__(self) -> str:
        safe_details = json.dumps(self.details, ensure_ascii=False, sort_keys=True)
        return f"{self.code}: {safe_details}"


def load_rag_eval_dataset(path: Path, *, min_case_count: int = 20) -> RagEvalDataset:
    payload = _load_payload(path)
    _validate_top_level(payload, path)

    raw_version = payload["dataset_version"]
    if not isinstance(raw_version, str) or not SAFE_FIXTURE_ID_PATTERN.fullmatch(raw_version):
        raise RagEvalDatasetError(
            code="invalid_dataset",
            details={"field": "dataset_version", "file": path.name},
        )

    raw_cases = payload["cases"]
    raw_corpus = payload["corpus"]
    cases: list[RagEvalCase] = []
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, dict):
            raise RagEvalDatasetError(
                code="invalid_case",
                details={"index": index, "field": "case", "file": path.name},
            )
        try:
            cases.append(RagEvalCase.model_validate(raw_case))
        except ValidationError as exc:
            raise RagEvalDatasetError(
                code="invalid_case",
                details=_safe_validation_details(
                    raw_item=raw_case,
                    index=index,
                    exc=exc,
                    file=path.name,
                ),
            ) from exc

    corpus: list[RagEvalCorpusRecord] = []
    for index, raw_record in enumerate(raw_corpus):
        if not isinstance(raw_record, dict):
            raise RagEvalDatasetError(
                code="invalid_corpus_record",
                details={"index": index, "field": "corpus", "file": path.name},
            )
        try:
            corpus.append(RagEvalCorpusRecord.model_validate(raw_record))
        except ValidationError as exc:
            raise RagEvalDatasetError(
                code="invalid_corpus_record",
                details=_safe_validation_details(
                    raw_item=raw_record,
                    index=index,
                    exc=exc,
                    file=path.name,
                ),
            ) from exc

    try:
        dataset = RagEvalDataset(
            dataset_version=raw_version,
            cases=tuple(cases),
            corpus=tuple(corpus),
        )
    except ValidationError as exc:
        raise RagEvalDatasetError(
            code="invalid_dataset",
            details=_safe_validation_details(raw_item=payload, index=None, exc=exc, file=path.name),
        ) from exc

    if len(dataset.cases) < min_case_count:
        raise RagEvalDatasetError(
            code="case_count_below_minimum",
            details={
                "case_count": len(dataset.cases),
                "min_case_count": min_case_count,
                "file": path.name,
            },
        )

    seen_cases: set[str] = set()
    for case in dataset.cases:
        if case.case_id in seen_cases:
            raise RagEvalDatasetError(
                code="duplicate_case_id",
                details={"case_id": case.case_id, "file": path.name},
            )
        seen_cases.add(case.case_id)

    seen_corpus: set[str] = set()
    for index, record in enumerate(dataset.corpus):
        unknown_case_ids = sorted(
            case_id for case_id in record.relevant_case_ids if case_id not in seen_cases
        )
        if unknown_case_ids:
            raise RagEvalDatasetError(
                code="unknown_relevant_case_id",
                details={"index": index, "unknown_count": len(unknown_case_ids), "file": path.name},
            )
        record_key = (
            f"{record.tenant_id}:{record.document_id}:"
            f"{record.version_id}:{record.chunk_id}"
        )
        if record_key in seen_corpus:
            raise RagEvalDatasetError(
                code="duplicate_corpus_record",
                details={"chunk_id": record.chunk_id, "file": path.name},
            )
        seen_corpus.add(record_key)

    _validate_case_corpus_contracts(dataset)

    return dataset


def _load_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RagEvalDatasetError(code="file_not_found", details={"file": path.name})
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RagEvalDatasetError(code="read_failed", details={"file": path.name}) from exc
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise RagEvalDatasetError(
            code="invalid_json",
            details={"file": path.name, "line": exc.lineno, "column": exc.colno},
        ) from exc
    if not isinstance(payload, dict):
        raise RagEvalDatasetError(code="invalid_top_level", details={"file": path.name})
    return payload


def _validate_top_level(payload: dict[str, Any], path: Path) -> None:
    for field in ("dataset_version", "cases", "corpus"):
        if field not in payload:
            raise RagEvalDatasetError(
                code="invalid_top_level",
                details={"field": field, "file": path.name},
            )
    if not isinstance(payload["cases"], list):
        raise RagEvalDatasetError(
            code="invalid_top_level",
            details={"field": "cases", "file": path.name},
        )
    if not isinstance(payload["corpus"], list):
        raise RagEvalDatasetError(
            code="invalid_top_level",
            details={"field": "corpus", "file": path.name},
        )


def _safe_validation_details(
    *,
    raw_item: dict[str, object],
    index: int | None,
    exc: ValidationError,
    file: str,
) -> dict[str, object]:
    first_error = exc.errors(include_url=False, include_context=False)[0]
    loc = first_error.get("loc", ())
    loc_items = tuple(str(item) for item in loc) if isinstance(loc, tuple) else (str(loc),)
    field = ".".join(item for item in loc_items if not item.isdigit()) or "model"
    field = _safe_detail_text(field, fallback="unsafe_field")
    details: dict[str, object] = {
        "field": field,
        "error_count": exc.error_count(),
        "file": file,
    }
    if index is not None:
        details["index"] = index

    raw_case_id = _find_case_id(raw_item)
    if isinstance(raw_case_id, str) and _is_safe_detail_id(raw_case_id):
        details["case_id"] = raw_case_id.strip()
    return details


def _find_case_id(raw_item: object) -> object:
    if isinstance(raw_item, dict):
        case_id = raw_item.get("case_id")
        if case_id is not None:
            return case_id
        raw_cases = raw_item.get("cases")
        if isinstance(raw_cases, list) and raw_cases:
            first_case = raw_cases[0]
            if isinstance(first_case, dict):
                return first_case.get("case_id")
    return None


def _validate_case_corpus_contracts(dataset: RagEvalDataset) -> None:
    relevant_records: dict[str, list[RagEvalCorpusRecord]] = {
        case.case_id: [] for case in dataset.cases
    }
    for record in dataset.corpus:
        for case_id in record.relevant_case_ids:
            relevant_records[case_id].append(record)

    for case in dataset.cases:
        records = relevant_records[case.case_id]
        cross_tenant_count = sum(1 for record in records if record.tenant_id != case.tenant_id)
        if cross_tenant_count:
            raise RagEvalDatasetError(
                code="cross_tenant_relevant_corpus",
                details={
                    "case_id": case.case_id,
                    "record_count": cross_tenant_count,
                    "file": "rag_smoke.json",
                },
            )

        same_tenant_records = [record for record in records if record.tenant_id == case.tenant_id]
        if case.expected_no_answer and same_tenant_records:
            raise RagEvalDatasetError(
                code="no_answer_case_has_relevant_corpus",
                details={
                    "case_id": case.case_id,
                    "record_count": len(same_tenant_records),
                    "file": "rag_smoke.json",
                },
            )

        authorized_records = [
            record for record in same_tenant_records if _record_authorized_for_case(record, case)
        ]
        if case.attack_type == "acl_isolation":
            unauthorized_count = len(same_tenant_records) - len(authorized_records)
            if not authorized_records or unauthorized_count == 0:
                raise RagEvalDatasetError(
                    code="invalid_acl_isolation_fixture",
                    details={
                        "case_id": case.case_id,
                        "authorized_count": len(authorized_records),
                        "unauthorized_count": unauthorized_count,
                        "file": "rag_smoke.json",
                    },
                )

        _validate_expected_ids_against_corpus(case, authorized_records)


def _validate_expected_ids_against_corpus(
    case: RagEvalCase,
    records: list[RagEvalCorpusRecord],
) -> None:
    document_ids = {record.document_id for record in records}
    chunk_ids = {record.chunk_id for record in records}
    citation_records = {
        (record.document_id, record.version_id, record.chunk_id): record for record in records
    }

    missing_documents = sorted(
        document_id for document_id in case.expected_documents if document_id not in document_ids
    )
    if missing_documents:
        raise RagEvalDatasetError(
            code="expected_document_missing_from_corpus",
            details={
                "case_id": case.case_id,
                "missing_count": len(missing_documents),
                "file": "rag_smoke.json",
            },
        )

    missing_chunks = sorted(
        chunk_id for chunk_id in case.expected_chunks if chunk_id not in chunk_ids
    )
    if missing_chunks:
        raise RagEvalDatasetError(
            code="expected_chunk_missing_from_corpus",
            details={
                "case_id": case.case_id,
                "missing_count": len(missing_chunks),
                "file": "rag_smoke.json",
            },
        )

    for citation in case.expected_citations:
        record = citation_records.get(
            (citation.document_id, citation.version_id, citation.chunk_id)
        )
        if record is None:
            raise RagEvalDatasetError(
                code="expected_citation_missing_from_corpus",
                details={"case_id": case.case_id, "file": "rag_smoke.json"},
            )
        if (
            citation.page_start is not None
            and (citation.page_start != record.page_start or citation.page_end != record.page_end)
        ):
            raise RagEvalDatasetError(
                code="expected_citation_page_mismatch",
                details={"case_id": case.case_id, "file": "rag_smoke.json"},
            )


def _record_authorized_for_case(record: RagEvalCorpusRecord, case: RagEvalCase) -> bool:
    if record.acl.visibility == "tenant":
        return True
    if case.user_id in record.acl.allowed_users:
        return True
    if case.department and case.department in record.acl.allowed_departments:
        return True
    return bool(set(case.roles) & set(record.acl.allowed_roles))


def _is_safe_detail_id(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip()
    return bool(SAFE_FIXTURE_ID_PATTERN.fullmatch(normalized)) and not _has_forbidden_marker(
        normalized
    )


def _safe_detail_text(value: str, *, fallback: str) -> str:
    if _has_forbidden_marker(value):
        return fallback
    return value


def _has_forbidden_marker(value: str) -> bool:
    lowered = value.lower()
    return any(marker in lowered for marker in FORBIDDEN_TEXT_MARKERS)

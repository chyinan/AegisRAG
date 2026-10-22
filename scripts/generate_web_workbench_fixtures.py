"""Serialize synthetic browser fixtures through backend DTOs, without external I/O.

These are HTTP contract mocks, not proof of PostgreSQL/Redis/MinIO or model operation.
Run `python -m scripts.generate_web_workbench_fixtures [--check]` from the repo root.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from apps.api.routes.auth import LoginResponseData, LogoutResponseData
from packages.common.envelope import ApiError, error_response, success_response
from packages.data.dto import (
    DocumentReviewListItem,
    DocumentReviewListResult,
    DocumentVersionStatusResult,
    UploadDocumentResult,
)
from packages.rag.source_resolver import SourceResolveResponse

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "apps/web/src/test/fixtures/backend-workbench.json"
)
IDENTIFIERS = {"document_id": "doc-pilot", "version_id": "version-pilot"}
CONTEXT = {"request_id": "req-workbench-fixture", "trace_id": "trace-workbench-fixture"}


def envelope(data: BaseModel) -> dict[str, object]:
    return success_response(request_id=CONTEXT["request_id"], data=data).model_dump(mode="json")


def workbench_fixtures() -> dict[str, object]:
    status = DocumentVersionStatusResult(
        **IDENTIFIERS,
        **CONTEXT,
        status="uploaded",
        chunk_count=0,
        job_id="job-fixture",
        attempt_count=0,
    )
    row = DocumentReviewListItem(
        **IDENTIFIERS,
        **CONTEXT,
        source_display_name="Pilot handbook.md",
        source_type="markdown",
        status="uploaded",
        created_by="user-pilot",
        chunk_count=0,
    )
    result: dict[str, object] = {
        "login": envelope(
            LoginResponseData(
                access_token="synthetic-browser-access-token",
                refresh_token="synthetic-browser-refresh-token",
                expires_in=3600,
                user_id="user-pilot",
                display_name="Pilot Admin",
                tenant_id="tenant-pilot",
                roles=["admin"],
                permissions=[
                    "document:read",
                    "document:upload",
                    "document:manage",
                    "retrieval:query",
                    "admin:settings",
                    "diagnostics:read",
                ],
            )
        ),
        "logout": envelope(LogoutResponseData(revoked=True)),
        "upload": envelope(
            UploadDocumentResult(**IDENTIFIERS, job_id="job-fixture", status="uploaded")
        ),
        "documents": envelope(DocumentReviewListResult(items=[row], limit=25, **CONTEXT)),
        "emptyDocuments": envelope(DocumentReviewListResult(items=[], limit=25, **CONTEXT)),
        "queued": envelope(status),
        "indexing": envelope(
            status.model_copy(
                update={
                    "status": "indexing",
                    "chunk_count": 1,
                    "index_status": "indexing",
                    "attempt_count": 1,
                }
            )
        ),
        "ready": envelope(
            status.model_copy(
                update={
                    "status": "retrieval_ready",
                    "chunk_count": 1,
                    "vector_count": 1,
                    "index_status": "ready",
                    "attempt_count": 1,
                }
            )
        ),
        "retrying": envelope(
            status.model_copy(
                update={
                    "status": "failed_retryable",
                    "attempt_count": 1,
                    "next_retry_at": datetime(2026, 10, 14, 17, 0, tzinfo=UTC),
                    "error_code": "EMBEDDING_PROVIDER_UNAVAILABLE",
                    "error_summary": {"error_code": "EMBEDDING_PROVIDER_UNAVAILABLE"},
                }
            )
        ),
        "failed": envelope(
            status.model_copy(
                update={
                    "status": "failed_terminal",
                    "attempt_count": 4,
                    "error_code": "EMBEDDING_PROVIDER_UNAVAILABLE",
                    "error_summary": {"error_code": "EMBEDDING_PROVIDER_UNAVAILABLE"},
                }
            )
        ),
        "source": envelope(
            SourceResolveResponse(
                **IDENTIFIERS,
                **CONTEXT,
                chunk_id="chunk-pilot",
                source_display_name="Pilot handbook.md",
                source_type="markdown",
                title_path=("Remote work",),
                text_excerpt="Working remotely is supported with manager approval.",
                excerpt_char_count=len("Working remotely is supported with manager approval."),
                token_count=10,
                retrieval_method="hybrid",
                score=0.95,
            )
        ),
        "sourceDenied": error_response(
            request_id=CONTEXT["request_id"],
            error=ApiError(
                code="SOURCE_ACCESS_DENIED",
                message="Source reference cannot be resolved.",
            ),
        ).model_dump(mode="json"),
        "loginDenied": error_response(
            request_id=CONTEXT["request_id"],
            error=ApiError(
                code="AUTH_INVALID_CREDENTIALS",
                message="Invalid username or password.",
            ),
        ).model_dump(mode="json"),
    }
    return result


def serialized_fixtures() -> str:
    return json.dumps(workbench_fixtures(), indent=2, ensure_ascii=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = serialized_fixtures()
    if args.check:
        if not FIXTURE_PATH.exists() or FIXTURE_PATH.read_text(encoding="utf-8") != expected:
            parser.exit(1, "Workbench fixtures differ from backend DTOs. Regenerate them.\n")
        return
    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(expected, encoding="utf-8")


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field

from packages.data.demo_seed import (
    FORBIDDEN_TEXT_MARKERS,
    DemoCase,
    DemoCitation,
    DemoManifest,
    _is_forbidden_report_key,
    _looks_like_path_or_token_url,
)


class WalkthroughHttpResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status_code: int
    headers: dict[str, str] = Field(default_factory=dict)
    text: str = ""
    json_body: object | None = None


class WalkthroughHttpTransport(Protocol):
    async def post_json(
        self,
        path: str,
        *,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_seconds: float,
    ) -> WalkthroughHttpResponse: ...


class HttpxWalkthroughTransport:
    def __init__(self, *, api_base_url: str) -> None:
        self._api_base_url = api_base_url.rstrip("/")

    async def post_json(
        self,
        path: str,
        *,
        headers: dict[str, str],
        payload: dict[str, object],
        timeout_seconds: float,
    ) -> WalkthroughHttpResponse:
        timeout = httpx.Timeout(timeout_seconds)
        async with httpx.AsyncClient(base_url=self._api_base_url, timeout=timeout) as client:
            response = await client.post(path, headers=headers, json=payload)
        try:
            json_body: object | None = response.json()
        except json.JSONDecodeError:
            json_body = None
        return WalkthroughHttpResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            text=response.text,
            json_body=json_body,
        )


class WalkthroughCaseReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: str
    case_type: str
    status: str
    request_id: str
    trace_id: str
    session_id: str | None = None
    latency_ms: float
    citation_count: int
    result_count: int = 0
    no_answer: bool = False
    prompt_injection_safe: bool = True
    source_resolve_checked: bool = False
    failure_stage: str | None = None
    safe_summary: str
    next_steps: tuple[str, ...] = ()


class WalkthroughSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_count: int
    passed_count: int
    failed_count: int
    average_latency_ms: float


class WalkthroughReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    generated_at: str
    report_type: str = "enterprise_rag_walkthrough"
    summary: WalkthroughSummary
    cases: tuple[WalkthroughCaseReport, ...]


class DemoWalkthroughRunner:
    def __init__(
        self,
        *,
        manifest: DemoManifest,
        http: WalkthroughHttpTransport | None = None,
        api_base_url: str = "http://127.0.0.1:8000",
        report_dir: Path | None = None,
        report_path: Path | None = None,
        bearer_tokens_by_user: Mapping[str, str] | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._manifest = manifest
        self._http = http or HttpxWalkthroughTransport(api_base_url=api_base_url)
        self._report_dir = report_dir
        self._report_path = report_path
        self._bearer_tokens_by_user = {
            user_id: token.strip()
            for user_id, token in dict(bearer_tokens_by_user or {}).items()
            if token.strip()
        }
        self._timeout_seconds = timeout_seconds
        self._users = {user.user_id: user for user in manifest.users}

    async def run(self, *, case_selector: Sequence[str] | None = None) -> WalkthroughReport:
        selected = self._selected_cases(case_selector)
        results = [await self._run_case(case) for case in selected]
        report = _build_report(tuple(results))
        if self._report_dir is not None or self._report_path is not None:
            _write_walkthrough_report(
                report,
                report_dir=self._report_dir,
                report_path=self._report_path,
            )
        return report

    def _selected_cases(self, case_selector: Sequence[str] | None) -> tuple[DemoCase, ...]:
        if case_selector is None:
            return self._manifest.cases
        selected_ids = set(case_selector)
        selected = tuple(case for case in self._manifest.cases if case.case_id in selected_ids)
        found_ids = {case.case_id for case in selected}
        missing = sorted(selected_ids - found_ids)
        if missing:
            raise ValueError("unknown demo case selector")
        return selected

    async def _run_case(self, case: DemoCase) -> WalkthroughCaseReport:
        started = time.perf_counter()
        request_id = f"req-{case.case_id}"
        trace_id = f"trace-{case.case_id}"
        try:
            chat_response = await self._http.post_json(
                "/v1/chat/completions",
                headers=self._headers(case, request_id=request_id, trace_id=trace_id),
                payload={
                    "model": "configured-rag-model",
                    "messages": [{"role": "user", "content": case.query}],
                    "stream": False,
                    "metadata_filter": {"demo_namespace": self._manifest.namespace},
                },
                timeout_seconds=self._timeout_seconds,
            )
            if chat_response.status_code >= 400 or not isinstance(chat_response.json_body, Mapping):
                return self._failure(case, request_id, trace_id, started, "chat")
            body = chat_response.json_body
            citations = _citations(body.get("citations"))
            no_answer = body.get("no_answer") is True
            answer = _answer_text(body)
            if _contains_unsafe_response_data(body) or _contains_unsafe_response_data(
                chat_response.text
            ):
                return self._failure(case, request_id, trace_id, started, "response_safety")
            failure_stage = self._case_failure_stage(
                case,
                citations=citations,
                no_answer=no_answer,
                answer=answer,
            )
            source_resolve_checked = False
            if failure_stage is None and case.case_type == "source_resolve":
                source_resolve_checked = await self._resolve_expected_source(
                    case=case,
                    citations=citations,
                    request_id=request_id,
                    trace_id=trace_id,
                )
                if not source_resolve_checked:
                    failure_stage = "source_resolve"
            if failure_stage is None and case.case_type == "acl_isolation":
                denied = await self._forbidden_sources_are_denied(
                    case=case,
                    request_id=request_id,
                    trace_id=trace_id,
                )
                if not denied:
                    failure_stage = "permission"
            latency_ms = max((time.perf_counter() - started) * 1000, 0.0)
            return WalkthroughCaseReport(
                case_id=case.case_id,
                case_type=case.case_type,
                status="passed" if failure_stage is None else "failed",
                request_id=str(body.get("request_id") or request_id),
                trace_id=str(body.get("trace_id") or trace_id),
                session_id=_optional_str(body.get("session_id")),
                latency_ms=latency_ms,
                citation_count=len(citations),
                result_count=_result_count(body),
                no_answer=no_answer,
                prompt_injection_safe=(
                    case.case_type != "prompt_injection" or failure_stage is None
                ),
                source_resolve_checked=source_resolve_checked,
                failure_stage=failure_stage,
                safe_summary=_safe_summary(case, failure_stage),
                next_steps=() if failure_stage is None else _next_steps(failure_stage),
            )
        except httpx.TimeoutException:
            return self._failure(case, request_id, trace_id, started, "timeout")
        except Exception:
            return self._failure(case, request_id, trace_id, started, "runner")

    async def _resolve_expected_source(
        self,
        *,
        case: DemoCase,
        citations: tuple[Mapping[str, object], ...],
        request_id: str,
        trace_id: str,
    ) -> bool:
        expected = {_citation_key(citation) for citation in case.expected_citations}
        citation = next(
            (
                item
                for item in citations
                if _citation_mapping_key(item) in expected
            ),
            None,
        )
        if citation is None:
            return False
        payload = {
            "document_id": citation.get("document_id"),
            "version_id": citation.get("version_id"),
            "chunk_id": citation.get("chunk_id"),
            "page_start": citation.get("page_start"),
            "page_end": citation.get("page_end"),
            "request_id": request_id,
        }
        payload = {key: value for key, value in payload.items() if value is not None}
        response = await self._http.post_json(
            "/sources/resolve",
            headers=self._headers(case, request_id=f"{request_id}-source", trace_id=trace_id),
            payload=payload,
            timeout_seconds=self._timeout_seconds,
        )
        if response.status_code >= 400 or not isinstance(response.json_body, Mapping):
            return False
        data = response.json_body.get("data")
        if not isinstance(data, Mapping):
            return False
        return _source_resolve_matches(
            citation=citation,
            data=data,
            request_id=f"{request_id}-source",
            trace_id=trace_id,
        )

    async def _forbidden_sources_are_denied(
        self,
        *,
        case: DemoCase,
        request_id: str,
        trace_id: str,
    ) -> bool:
        for citation in case.forbidden_citations:
            response = await self._http.post_json(
                "/sources/resolve",
                headers=self._headers(
                    case,
                    request_id=f"{request_id}-forbidden-source",
                    trace_id=trace_id,
                ),
                payload={
                    "document_id": citation.document_id,
                    "version_id": citation.version_id,
                    "chunk_id": citation.chunk_id,
                    "request_id": request_id,
                },
                timeout_seconds=self._timeout_seconds,
            )
            if response.status_code < 400:
                return False
            if _contains_unsafe_response_data(response.json_body) or _contains_unsafe_response_data(
                response.text
            ):
                return False
        return True

    def _case_failure_stage(
        self,
        case: DemoCase,
        *,
        citations: tuple[Mapping[str, object], ...],
        no_answer: bool,
        answer: str,
    ) -> str | None:
        if _contains_case_forbidden_terms(case, answer):
            return "response_safety"
        if case.case_type == "no_answer":
            return None if no_answer and not citations else "no_answer"
        if case.case_type == "acl_isolation":
            forbidden = {_citation_key(citation) for citation in case.forbidden_citations}
            actual = {_citation_mapping_key(citation) for citation in citations}
            return None if not (forbidden & actual) else "permission"
        if case.case_type == "prompt_injection":
            if citations and _expected_citations_present(case, citations):
                return None
            return "citation"
        if not citations:
            return "citation"
        return None if _expected_citations_present(case, citations) else "citation"

    def _headers(self, case: DemoCase, *, request_id: str, trace_id: str) -> dict[str, str]:
        user = self._users[case.user_id]
        token = self._bearer_tokens_by_user.get(user.user_id)
        if token is not None:
            return {
                "X-Request-ID": request_id,
                "X-Trace-ID": trace_id,
                "Authorization": f"Bearer {token}",
            }
        return {
            "X-Request-ID": request_id,
            "X-Trace-ID": trace_id,
            "X-User-ID": user.user_id,
            "X-Tenant-ID": user.tenant_id,
            "X-Roles": ",".join(user.roles),
            "X-Permissions": ",".join(user.permissions),
        }

    def _failure(
        self,
        case: DemoCase,
        request_id: str,
        trace_id: str,
        started: float,
        failure_stage: str,
    ) -> WalkthroughCaseReport:
        return WalkthroughCaseReport(
            case_id=case.case_id,
            case_type=case.case_type,
            status="failed",
            request_id=request_id,
            trace_id=trace_id,
            latency_ms=max((time.perf_counter() - started) * 1000, 0.0),
            citation_count=0,
            failure_stage=failure_stage,
            safe_summary=_safe_summary(case, failure_stage),
            next_steps=_next_steps(failure_stage),
        )


def _build_report(results: tuple[WalkthroughCaseReport, ...]) -> WalkthroughReport:
    passed = sum(1 for result in results if result.status == "passed")
    return WalkthroughReport(
        generated_at=datetime.now(tz=UTC).isoformat(),
        summary=WalkthroughSummary(
            case_count=len(results),
            passed_count=passed,
            failed_count=len(results) - passed,
            average_latency_ms=(
                sum(result.latency_ms for result in results) / len(results) if results else 0.0
            ),
        ),
        cases=results,
    )


def _write_walkthrough_report(
    report: WalkthroughReport,
    *,
    report_dir: Path | None,
    report_path: Path | None,
) -> Path:
    if report_path is None:
        if report_dir is None:
            raise ValueError("report_dir or report_path is required")
        report_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%SZ")
        path = report_dir / f"enterprise-rag-walkthrough-{stamp}.json"
    else:
        path = report_path
        path.parent.mkdir(parents=True, exist_ok=True)
    payload = _sanitize_report(report.model_dump(mode="json"))
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path


def _citations(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _answer_text(body: Mapping[str, object]) -> str:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, Mapping):
        return ""
    message = first.get("message")
    if not isinstance(message, Mapping):
        return ""
    content = message.get("content")
    return content if isinstance(content, str) else ""


def _contains_case_forbidden_terms(case: DemoCase, answer: str) -> bool:
    normalized = answer.casefold()
    return any(term.casefold() in normalized for term in case.must_not_include_terms)


def _expected_citations_present(
    case: DemoCase,
    citations: tuple[Mapping[str, object], ...],
) -> bool:
    expected = {_citation_key(citation) for citation in case.expected_citations}
    actual = {_citation_mapping_key(citation) for citation in citations}
    return bool(expected) and expected <= actual


def _citation_key(citation: DemoCitation) -> str:
    return f"{citation.document_id}:{citation.version_id}:{citation.chunk_id}"


def _citation_mapping_key(citation: Mapping[str, object]) -> str:
    return f"{citation.get('document_id')}:{citation.get('version_id')}:{citation.get('chunk_id')}"


def _result_count(body: Mapping[str, object]) -> int:
    metadata = body.get("metadata")
    if not isinstance(metadata, Mapping):
        return 0
    retrieval = metadata.get("retrieval")
    if not isinstance(retrieval, Mapping):
        return 0
    value = retrieval.get("result_count")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def _contains_unsafe_source_resolve_data(data: Mapping[str, object]) -> bool:
    if any(key in data for key in ("source_uri", "object_key", "prompt", "chunk_content")):
        return True
    metadata = data.get("metadata")
    return _contains_unsafe_response_data(metadata)


def _source_resolve_matches(
    *,
    citation: Mapping[str, object],
    data: Mapping[str, object],
    request_id: str,
    trace_id: str,
) -> bool:
    if _contains_unsafe_source_resolve_data(data):
        return False
    for field in ("document_id", "version_id", "chunk_id"):
        if data.get(field) != citation.get(field):
            return False
    if data.get("request_id") != request_id or data.get("trace_id") != trace_id:
        return False
    if not isinstance(data.get("text_excerpt"), str) or not data.get("text_excerpt"):
        return False
    if not isinstance(data.get("source_display_name"), str) or not data.get(
        "source_display_name"
    ):
        return False
    if not isinstance(data.get("retrieval_method"), str) or not data.get(
        "retrieval_method"
    ):
        return False
    score = data.get("score")
    return isinstance(score, int | float) and not isinstance(score, bool)


def _contains_unsafe_response_data(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered_key = str(key).lower()
            if lowered_key not in {
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
            } and _is_forbidden_report_key(lowered_key):
                return True
            if _contains_unsafe_response_data(item):
                return True
        return False
    if isinstance(value, list | tuple):
        return any(_contains_unsafe_response_data(item) for item in value)
    if isinstance(value, str):
        lowered = value.lower()
        return any(marker in lowered for marker in FORBIDDEN_TEXT_MARKERS) or (
            _looks_like_path_or_token_url(value)
        )
    return False


def _sanitize_report(value: object) -> object:
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            lowered_key = str(key).lower()
            if _is_forbidden_report_key(lowered_key):
                continue
            result[str(key)] = _sanitize_report(item)
        return result
    if isinstance(value, list | tuple):
        return [_sanitize_report(item) for item in value]
    if isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in FORBIDDEN_TEXT_MARKERS):
            return "[redacted]"
        if _looks_like_path_or_token_url(value):
            return "[redacted]"
        return value
    return value


def _safe_summary(case: DemoCase, failure_stage: str | None) -> str:
    if failure_stage is None:
        return f"{case.case_type} case passed with synthetic-safe metadata."
    return f"{case.case_type} case failed at {failure_stage}."


def _next_steps(failure_stage: str) -> tuple[str, ...]:
    commands = {
        "chat": (
            ".venv\\Scripts\\python.exe -m pytest "
            "tests/integration/api/test_service_token_routes.py -q"
        ),
        "source_resolve": (
            ".venv\\Scripts\\python.exe -m pytest "
            "tests/integration/api/test_sources_routes.py -q"
        ),
        "no_answer": (
            ".venv\\Scripts\\python.exe -m pytest "
            "tests/unit/eval/test_rag_eval_runner.py -q"
        ),
        "permission": (
            ".venv\\Scripts\\python.exe -m pytest "
            "tests/unit/retrieval/test_filters.py -q"
        ),
    }
    return (commands.get(failure_stage, ".venv\\Scripts\\python.exe -m pytest tests/eval -q"),)


def _optional_str(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None

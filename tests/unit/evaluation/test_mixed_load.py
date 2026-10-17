"""Synthetic HTTPX contracts only: no sockets, models or performance claims."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from evaluation import mixed_load as load
from packages.auth.context import AuthContext
from packages.common.context import AuthenticatedRequestContext
from packages.memory import ChatMemoryService
from tests.unit.memory.test_service import FakeChatMemoryRepository

TOKENS = {"LOAD_A": "synthetic-a-token", "LOAD_B": "synthetic-b-token"}


@dataclass
class Clock:
    now: float = 0
    sleeps: list[float] = field(default_factory=list)

    def __call__(self) -> float:
        return self.now

    async def sleep(self, duration: float) -> None:
        self.sleeps.append(duration)
        self.now += duration
        await asyncio.sleep(0)


def config_data(**updates: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "target_url": "https://target.test/api/",
        "target_revision": "fixture-revision",
        "identities": [
            {"label": "a", "tenant_id": "tenant-a", "user_id": "user-a", "token_env": "LOAD_A"}
        ],
        "cases": [
            {
                "name": "short-a",
                "identity": "a",
                "endpoint": "/query/stream",
                "payload": {"query": "private synthetic prompt", "max_output_tokens": 64},
                "output_profile": "short",
            }
        ],
        "stages": [
            {
                "name": "warm",
                "users": 1,
                "duration_s": 1.0,
                "cache_state": "warm",
                "max_requests_per_user": 1,
            }
        ],
    }
    data.update(updates)
    return data


def context(request: httpx.Request, **updates: Any) -> dict[str, Any]:
    identity = "b" if request.headers["Authorization"] == "Bearer synthetic-b-token" else "a"
    data = {
        "request_id": request.headers["X-Request-ID"],
        "trace_id": request.headers["X-Trace-ID"],
        "tenant_id": f"tenant-{identity}",
        "user_id": f"user-{identity}",
    }
    data.update(updates)
    return data


def frame(request: httpx.Request, event: str, **updates: Any) -> bytes:
    data = context(request, event=event, **updates)
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


def final(request: httpx.Request, **updates: Any) -> bytes:
    data = {"status": "success", "answer": "safe answer", "citations": []}
    data.update(updates)
    return frame(request, "final", **data)


def stream_response(body: bytes) -> httpx.Response:
    return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})


def valid_stream(request: httpx.Request) -> httpx.Response:
    return stream_response(frame(request, "token", index=0, delta="safe") + final(request))


async def measure(
    handler: Callable[[httpx.Request], httpx.Response]
    | Callable[[httpx.Request], Coroutine[None, None, httpx.Response]],
    *,
    data: dict[str, Any] | None = None,
    clock: Clock | None = None,
) -> dict[str, Any]:
    clock = clock or Clock()
    return await load.run_mixed_load(
        load.MixedLoadConfig.model_validate(data or config_data()),
        transport=httpx.MockTransport(handler),
        environ=TOKENS,
        clock=clock,
        sleep=clock.sleep,
    )


@pytest.mark.parametrize(
    "target",
    [
        "http://name:password@target.test",
        "ftp://target.test",
        "/relative",
        "https://target.test?",
        "https://target.test#",
        "https://target.test?token=private",
    ],
)
def test_unsafe_or_ambiguous_target_is_rejected(target: str) -> None:
    with pytest.raises(ValidationError):
        load.MixedLoadConfig.model_validate(config_data(target_url=target))


async def test_real_load_is_refused_before_client_or_secret_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden_client(**kwargs: Any) -> None:
        raise AssertionError("no client may be constructed")

    monkeypatch.setattr(httpx, "AsyncClient", forbidden_client)
    with pytest.raises(ValueError, match="explicit"):
        await load.run_mixed_load(load.MixedLoadConfig.model_validate(config_data()), environ={})


async def test_cli_requires_opt_in_before_reading_config(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as error:
        await load.main(["--config", str(tmp_path / "absent.json")])
    assert error.value.code == 2
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "change",
    [
        {"behavior": "disconnect"},
        {"behavior": "slow_reader"},
        {"slow_read_delay_s": 0.1},
        {"disconnect_after_token_events": 1},
        {"endpoint": "/unknown"},
        {"payload": {"query": "q", "max_output_tokens": True}},
        {"payload": {"query": "q", "max_output_tokens": 64, "session_id": "shared"}},
        {"identity": "missing"},
        {"weight": 0},
    ],
)
def test_invalid_case_contracts_fail_before_traffic(change: dict[str, Any]) -> None:
    data = config_data()
    data["cases"][0].update(change)
    with pytest.raises(ValidationError):
        load.MixedLoadConfig.model_validate(data)


@pytest.mark.parametrize("field", ["duration_s", "users"])
@pytest.mark.parametrize("value", [0, -1, True, float("inf"), float("nan")])
def test_invalid_stage_values_are_rejected(field: str, value: Any) -> None:
    data = config_data()
    data["stages"][0][field] = value
    with pytest.raises(ValidationError):
        load.MixedLoadConfig.model_validate(data)


async def test_distinct_identity_tokens_requests_and_prefix_are_used_without_dev_headers() -> None:
    data = config_data()
    data["identities"].append(
        {"label": "b", "tenant_id": "tenant-b", "user_id": "user-b", "token_env": "LOAD_B"}
    )
    data["cases"].append(
        {
            "name": "long-b",
            "identity": "b",
            "endpoint": "/query/stream",
            "payload": {"query": "other private prompt", "max_output_tokens": 1024},
            "output_profile": "long",
        }
    )
    data["stages"][0]["users"] = 2
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return valid_stream(request)

    report = await measure(handler, data=data)
    assert report["measurement_source"] == "mock_transport_contract_only"
    assert report["target_url"] == "https://target.test/api"
    assert len(requests) == 2
    for index, request in enumerate(requests):
        assert str(request.url) == "https://target.test/api/query/stream"
        token_key = "LOAD_" + "AB"[index]
        assert request.headers["Authorization"] == f"Bearer {TOKENS[token_key]}"
        assert "X-Tenant-ID" not in request.headers
        assert "X-User-ID" not in request.headers
        assert json.loads(request.content) == data["cases"][index]["payload"]
        assert request.extensions["timeout"] == {
            key: 120 for key in ("connect", "read", "write", "pool")
        }
    serialized = json.dumps(report)
    for secret in (
        *TOKENS.values(),
        "private prompt",
        "private synthetic prompt",
        "tenant-a",
        "user-a",
    ):
        assert secret not in serialized
    assert report["stages"][0]["summary"]["successful"] == 2


@pytest.mark.parametrize("environ", [{}, {"LOAD_A": " "}])
async def test_missing_or_invalid_token_is_rejected_without_requests(
    environ: dict[str, str],
) -> None:
    with pytest.raises(ValueError, match="bearer token"):
        await load.run_mixed_load(
            load.MixedLoadConfig.model_validate(config_data()),
            environ=environ,
            transport=httpx.MockTransport(lambda request: pytest.fail("must not send")),
        )


async def test_reusing_one_token_for_two_identities_is_rejected() -> None:
    data = config_data()
    data["identities"].append(
        {"label": "b", "tenant_id": "tenant-b", "user_id": "user-b", "token_env": "LOAD_B"}
    )
    with pytest.raises(ValueError, match="distinct"):
        await load.run_mixed_load(
            load.MixedLoadConfig.model_validate(data),
            environ={"LOAD_A": "same", "LOAD_B": "same"},
            transport=httpx.MockTransport(lambda request: pytest.fail("must not send")),
        )


@pytest.mark.parametrize(
    ("body_factory", "error"),
    [
        (lambda request: b"", "missing_final"),
        (lambda request: frame(request, "token", delta="x"), "missing_final"),
        (lambda request: final(request).rstrip(b"\n"), "truncated_sse"),
        (lambda request: b"event: final\ndata: not json\n\n", "invalid_sse"),
        (
            lambda request: frame(request, "error", code="PRIVATE_PROVIDER_ERROR") + final(request),
            "stream_error",
        ),
        (lambda request: final(request, status="error"), "stream_final_error"),
        (lambda request: final(request, tenant_id="other-tenant"), "identity_mismatch"),
        (lambda request: final(request, user_id="other-user"), "identity_mismatch"),
        (lambda request: final(request, request_id="other-request"), "context_mismatch"),
        (lambda request: final(request, trace_id="other-trace"), "context_mismatch"),
        (
            lambda request: final(request) + frame(request, "token", delta="late"),
            "event_after_final",
        ),
        (lambda request: final(request) + final(request), "event_after_final"),
        (lambda request: frame(request, "unknown"), "unexpected_event"),
    ],
)
async def test_2xx_stream_is_not_success_without_valid_terminal_protocol(
    body_factory: Callable[[httpx.Request], bytes],
    error: str,
) -> None:
    report = await measure(lambda request: stream_response(body_factory(request)))
    summary = report["stages"][0]["summary"]
    assert summary["successful"] == 0
    assert summary["failed"] == summary["attempts"] == 1
    assert summary["http_status_counts"] == {"200": 1}
    assert summary["error_counts"] == {error: 1}
    assert summary["completion_latency_successful_only_ms"]["sample_count"] == 0
    assert report["exit_code"] == 1
    assert "PRIVATE_PROVIDER_ERROR" not in json.dumps(report)


async def test_content_type_is_validated_for_sse() -> None:
    report = await measure(lambda request: httpx.Response(200, content=final(request)))
    assert report["stages"][0]["summary"]["error_counts"] == {"invalid_content_type": 1}


class TimedStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes], clock: Clock) -> None:
        self.chunks = chunks
        self.clock = clock
        self.closed = False
        self.yielded = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.clock.now += 0.25
            self.yielded += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


async def test_sse_fragmentation_comments_bom_crlf_ttft_and_complete_latency() -> None:
    clock = Clock()
    streams: list[TimedStream] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            b"\xef\xbb\xbf: keepalive\r\n\r\n"
            + frame(request, "token", delta="")
            + frame(request, "token", delta="first")
            + final(request)
        )
        # Split inside a JSON token and across UTF-8/newline boundaries.
        parts = [body[:1], body[1:10], body[10 : len(body) // 2], body[len(body) // 2 :]]
        stream = TimedStream(parts, clock)
        streams.append(stream)
        return httpx.Response(
            200, stream=stream, headers={"content-type": "text/event-stream; charset=utf-8"}
        )

    report = await measure(handler, clock=clock)
    summary = report["stages"][0]["summary"]
    assert summary["successful"] == 1
    assert summary["nonempty_token_events"] == 1
    assert summary["ttft_successful_only_ms"]["sample_count"] == 1
    assert 0 < summary["ttft_successful_only_ms"]["p95_ms"] <= 1000
    assert summary["completion_latency_successful_only_ms"]["p95_ms"] == 1000
    assert streams[0].closed


async def test_final_without_token_has_no_fabricated_ttft() -> None:
    report = await measure(lambda request: stream_response(final(request)))
    summary = report["stages"][0]["summary"]
    assert summary["successful"] == 1
    assert summary["ttft_successful_only_ms"]["sample_count"] == 0
    assert summary["ttft_successful_only_ms"]["p95_ms"] is None


async def test_slow_reader_delay_is_in_client_completion_latency() -> None:
    data = config_data()
    data["cases"][0].update(behavior="slow_reader", slow_read_delay_s=0.5)
    clock = Clock()
    report = await measure(valid_stream, data=data, clock=clock)
    summary = report["stages"][0]["summary"]
    assert clock.sleeps == [0.5, 0.5]
    assert summary["completion_latency_successful_only_ms"]["p95_ms"] == 1000
    assert summary["ttft_successful_only_ms"]["p95_ms"] == 0


async def test_planned_disconnect_closes_stream_and_is_never_successful_throughput() -> None:
    data = config_data()
    data["cases"][0].update(behavior="disconnect", disconnect_after_token_events=1)
    clock = Clock()
    streams: list[TimedStream] = []

    def handler(request: httpx.Request) -> httpx.Response:
        stream = TimedStream([frame(request, "token", delta="first"), final(request)], clock)
        streams.append(stream)
        return httpx.Response(200, stream=stream, headers={"content-type": "text/event-stream"})

    report = await measure(handler, data=data, clock=clock)
    summary = report["stages"][0]["summary"]
    assert summary["planned_disconnects"] == 1
    assert summary["successful"] == summary["failed"] == summary["successful_per_s"] == 0
    assert report["status"] == "completed"
    assert streams[0].closed and streams[0].yielded == 1
    assert len(report["stages"][0]["planned_disconnect_request_ids"]) == 1


async def test_disconnect_threshold_not_reached_is_failure() -> None:
    data = config_data()
    data["cases"][0].update(behavior="disconnect", disconnect_after_token_events=2)
    report = await measure(valid_stream, data=data)
    assert report["stages"][0]["summary"]["error_counts"] == {"disconnect_threshold_not_reached": 1}


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
async def test_http_failures_are_not_retried_or_redirected(status: int) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, headers={"location": "https://other.test/private"})

    report = await measure(handler)
    summary = report["stages"][0]["summary"]
    assert len(requests) == 1
    assert summary["error_counts"] == {"http_status": 1}
    assert summary["retries"] == 0 and report["retry_policy"] == "none"


@pytest.mark.parametrize(
    ("exception", "error"),
    [
        (httpx.ReadTimeout("private provider detail"), "httpx_timeout"),
        (httpx.ReadError("private provider detail"), "transport_error"),
        (TimeoutError("private provider detail"), "request_deadline"),
        (RuntimeError("private provider detail"), "unexpected_error"),
    ],
)
async def test_safe_error_classification(exception: Exception, error: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exception

    report = await measure(handler)
    assert report["stages"][0]["summary"]["error_counts"] == {error: 1}
    assert "private provider detail" not in json.dumps(report)


@pytest.mark.parametrize("endpoint", ["/retrieve", "/query", "/chat"])
async def test_json_contract_and_per_worker_chat_sessions(endpoint: str) -> None:
    data = config_data()
    case = data["cases"][0]
    case["endpoint"] = endpoint
    if endpoint == "/retrieve":
        case["output_profile"] = "retrieval"
        case["payload"] = {"query": "retrieve query", "top_k": 10}
    data["stages"][0]["users"] = 2
    sessions: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        if endpoint == "/chat":
            sessions.append(payload.get("session_id"))
        result = context(
            request, answer="answer", citations=[], candidates=[], session_id="server-session"
        )
        return httpx.Response(
            200, json={"request_id": request.headers["X-Request-ID"], "data": result, "error": None}
        )

    report = await measure(handler, data=data)
    assert report["stages"][0]["summary"]["successful"] == 2
    if endpoint == "/chat":
        assert sessions == [None, None]


@pytest.mark.parametrize("endpoint", ["/chat", "/chat/stream"])
async def test_real_memory_contract_creates_then_reuses_sessions_per_worker_and_identity(
    endpoint: str,
) -> None:
    data = config_data()
    data["identities"].append(
        {"label": "b", "tenant_id": "tenant-b", "user_id": "user-b", "token_env": "LOAD_B"}
    )
    data["cases"][0]["endpoint"] = endpoint
    data["cases"].append({**data["cases"][0], "name": "short-b", "identity": "b"})
    data["stages"][0].update(users=2, duration_s=100.0, max_requests_per_user=4)
    repository = FakeChatMemoryRepository()
    repository.sessions.clear()
    memory = ChatMemoryService(repository=repository)
    calls: dict[tuple[str, str], list[tuple[str | None, str]]] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        expected = context(request)
        auth = AuthContext(
            tenant_id=expected["tenant_id"],
            user_id=expected["user_id"],
            roles=("knowledge_user",),
            permissions=("document:read", "retrieval:query"),
        )
        request_context = AuthenticatedRequestContext(
            request_id=expected["request_id"],
            trace_id=expected["trace_id"],
            auth=auth,
        )
        # This is the real service contract: a supplied unknown ID fails rather
        # than creating a session. Only the repository is an in-memory fake.
        session = await memory.get_or_create_session(
            context=request_context,
            session_id=body.get("session_id"),
            query=body["query"],
        )
        worker = expected["request_id"].split("-")[-2]
        key = (worker, auth.tenant_id)
        calls.setdefault(key, []).append((body.get("session_id"), session.id))
        if endpoint.endswith("/stream"):
            return stream_response(final(request, session_id=session.id))
        return httpx.Response(
            200,
            json={
                "request_id": expected["request_id"],
                "error": None,
                "data": context(request, answer="answer", citations=[], session_id=session.id),
            },
        )

    report = await measure(handler, data=data)
    assert report["stages"][0]["summary"]["successful"] == 8
    assert len(calls) == len(repository.created_sessions) == 4
    returned_ids = set()
    for invocations in calls.values():
        assert len(invocations) == 2
        assert invocations[0][0] is None
        first_id = invocations[0][1]
        assert invocations[1] == (first_id, first_id)
        returned_ids.add(first_id)
    assert len(returned_ids) == 4
    assert all(session_id not in json.dumps(report) for session_id in returned_ids)


@pytest.mark.parametrize("endpoint", ["/chat", "/chat/stream"])
async def test_chat_rejects_changed_session_on_reuse(endpoint: str) -> None:
    data = config_data()
    data["cases"][0]["endpoint"] = endpoint
    data["stages"][0]["max_requests_per_user"] = 2
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        session_id = "server-first" if calls == 1 else "server-wrong"
        if endpoint.endswith("/stream"):
            return stream_response(final(request, session_id=session_id))
        return httpx.Response(
            200,
            json={
                "request_id": request.headers["X-Request-ID"],
                "error": None,
                "data": context(request, answer="answer", citations=[], session_id=session_id),
            },
        )

    report = await measure(handler, data=data)
    assert report["stages"][0]["summary"]["successful"] == 1
    assert report["stages"][0]["summary"]["error_counts"] == {"session_mismatch": 1}


async def test_invalid_utf8_is_not_silently_replaced_with_success() -> None:
    report = await measure(
        lambda request: stream_response(
            final(request, answer="corrupt-marker").replace(b"corrupt-marker", b"\xff"),
        )
    )
    assert report["stages"][0]["summary"]["error_counts"] == {"invalid_sse": 1}


@pytest.mark.parametrize("body", [b"", b"{}", b'{"error":{"code":"PRIVATE"}}', b"[]"])
async def test_2xx_json_needs_valid_envelope(body: bytes) -> None:
    data = config_data()
    data["cases"][0]["endpoint"] = "/query"
    report = await measure(lambda request: httpx.Response(200, content=body), data=data)
    assert report["stages"][0]["summary"]["successful"] == 0
    assert report["exit_code"] == 1


async def test_retrieve_rejects_cross_tenant_candidate() -> None:
    data = config_data()
    data["cases"][0].update(endpoint="/retrieve", output_profile="retrieval")

    def handler(request: httpx.Request) -> httpx.Response:
        result = context(request, candidates=[{"tenant_id": "foreign"}])
        return httpx.Response(
            200, json={"request_id": request.headers["X-Request-ID"], "data": result, "error": None}
        )

    report = await measure(handler, data=data)
    assert report["stages"][0]["summary"]["error_counts"] == {"identity_mismatch": 1}


async def test_stages_drain_in_order_and_external_ingestion_is_only_a_marker() -> None:
    data = config_data(
        stages=[
            {
                "name": "cold",
                "users": 1,
                "duration_s": 0.1,
                "cache_state": "cold",
                "ingestion_state": "external_active",
                "external_ingestion_ref": "operator-job-42",
                "max_requests_per_user": 1,
            },
            {
                "name": "warm",
                "users": 2,
                "duration_s": 0.1,
                "cache_state": "warm",
                "max_requests_per_user": 1,
            },
        ]
    )
    clock = Clock()

    def handler(request: httpx.Request) -> httpx.Response:
        clock.now += 0.2
        return valid_stream(request)

    report = await measure(handler, data=data, clock=clock)
    assert [stage["name"] for stage in report["stages"]] == ["cold", "warm"]
    assert report["stages"][0]["drain_s"] == pytest.approx(0.1)
    assert report["stages"][0]["external_ingestion_ref"] == "operator-job-42"
    assert report["stages"][0]["cache_state_source"] == "operator_declaration_only"
    assert "not_collected" in report["server_metrics"]


def test_external_ingestion_requires_reference() -> None:
    data = config_data()
    data["stages"][0]["ingestion_state"] = "external_active"
    with pytest.raises(ValidationError):
        load.MixedLoadConfig.model_validate(data)


async def test_response_limit_prevents_success() -> None:
    report = await measure(valid_stream, data=config_data(max_response_bytes=10))
    assert report["stages"][0]["summary"]["error_counts"] == {"response_limit": 1}


async def test_cancellation_propagates_and_closes_stream() -> None:
    closed = asyncio.Event()

    class CancelledStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            if not closed.is_set():
                raise asyncio.CancelledError
            yield b""  # pragma: no cover

        async def aclose(self) -> None:
            closed.set()

    with pytest.raises(asyncio.CancelledError):
        await measure(
            lambda request: httpx.Response(
                200,
                stream=CancelledStream(),
                headers={"content-type": "text/event-stream"},
            )
        )
    assert closed.is_set()


def test_nearest_rank_percentiles_and_empty_population() -> None:
    result = load._distribution([4, 1, 3, 2])
    assert result["p50_ms"] == 2
    assert result["p95_ms"] == result["p99_ms"] == 4
    assert load._distribution([])["p95_ms"] is None


async def test_report_files_are_unique_and_each_run_is_independent(tmp_path: Path) -> None:
    first = await measure(lambda request: httpx.Response(500))
    second = await measure(valid_stream)
    assert first["stages"][0]["summary"]["failed"] == 1
    assert second["stages"][0]["summary"]["failed"] == 0
    first_path = load.save_report(first, tmp_path)
    second_path = load.save_report(second, tmp_path)
    assert first_path != second_path
    assert json.loads(first_path.read_text())["exit_code"] == 1
    assert json.loads(second_path.read_text())["exit_code"] == 0

"""Mock HTTP contracts for the operator probe; these never contact a deployment."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from scripts import deployment_acceptance as acceptance
from scripts.deployment_acceptance import (
    DeploymentProbe,
    ProbeConfig,
    ProbeError,
    argument_parser,
    read_password,
    validate_base_url,
)

JsonObject = dict[str, Any]
PASSWORD = "test-password-never-log"
TOKEN = "test-bearer-never-log"
PRIVATE = "private-body-never-log"


def envelope(data: JsonObject) -> JsonObject:
    return {"request_id": "request-synthetic", "data": data, "error": None, "metadata": {}}


def denied(code: str = "SOURCE_ACCESS_DENIED") -> JsonObject:
    return {
        "request_id": "request-synthetic",
        "data": None,
        "error": {"code": code, "message": PRIVATE},
    }


def frame(event: str, data: JsonObject) -> str:
    return f"event: {event}\ndata: {json.dumps({'event': event, **data})}\n\n"


class SplitStream(httpx.AsyncByteStream):
    def __init__(self, content: bytes, *, delay: float = 0.0) -> None:
        self.content = content
        self.delay = delay

    async def __aiter__(self):  # type: ignore[no-untyped-def]
        # Split UTF-8 and CRLF boundaries, not just logical SSE frames.
        for position in range(0, len(self.content), 7):
            if self.delay:
                await asyncio.sleep(self.delay)
            yield self.content[position : position + 7]


class SyntheticDeployment:
    """Stateful mock with normal local-login and backend-shaped JSON/SSE contracts."""

    def __init__(self, probe: DeploymentProbe) -> None:
        self.probe = probe
        self.requests: list[httpx.Request] = []
        self.deleted = False
        self.statuses = ["uploaded", "indexing", "retrieval_ready"]
        self.status_reads = 0
        self.stream_mode = "success"
        self.overrides: dict[str, Callable[[httpx.Request], httpx.Response]] = {}

    @property
    def identity(self) -> JsonObject:
        return {"tenant_id": "tenant-test", "user_id": "user-test"}

    @property
    def version(self) -> JsonObject:
        return {
            "document_id": self.probe.report.document_id,
            "version_id": self.probe.report.version_id,
        }

    @property
    def citation(self) -> JsonObject:
        from packages.rag.dto import Citation

        # The citation_id is derived by the actual API DTO serializer, not this test.
        return Citation(
            **self.version,
            chunk_id="chunk-test",
            source_display_name="acceptance.txt",
            source_type="txt",
            title_path=("Acceptance",),
            retrieval_method="hybrid",
            score=0.9,
        ).model_dump(mode="json")

    def stream(self) -> bytes:
        identity = {"request_id": "request-stream", "trace_id": "trace-stream", **self.identity}
        citation = self.citation
        answer = f"The exact code is {self.probe._answer_code} [{citation['citation_id']}]."
        final = {
            **identity,
            "status": "success",
            "answer": answer,
            "no_answer": False,
            "citations": [citation],
            "unsupported_claims": [],
            "metadata": {},
        }
        tokens = frame("token", {**identity, "index": 0, "delta": "The code 🔎 "})
        cited = frame("citation", {**identity, "citation": citation})
        if self.stream_mode == "no_answer":
            final["no_answer"] = True
        elif self.stream_mode == "error_final":
            final["status"] = "error"
        elif self.stream_mode == "missing_fact":
            final["answer"] = f"Fake LLM response. [{citation['citation_id']}]"
        elif self.stream_mode == "missing_marker":
            final["answer"] = self.probe._answer_code
        elif self.stream_mode == "forged_marker":
            final["answer"] = answer + " [cite-0000000000000000]"
        elif self.stream_mode == "foreign_identity":
            final["tenant_id"] = "other-tenant"
        elif self.stream_mode == "unsupported":
            final["unsupported_claims"] = [{"reason": "forged", "summary": PRIVATE}]
        elif self.stream_mode == "forged_citation":
            citation["citation_id"] = "cite-0000000000000000"
        elif self.stream_mode == "wrong_version":
            citation["version_id"] = "other-version"
        elif self.stream_mode == "unknown_chunk":
            citation["chunk_id"] = "other-chunk"
        elif self.stream_mode == "empty_citations":
            final["citations"] = []
        final_frame = frame("final", final)
        result = tokens + cited + final_frame
        if self.stream_mode == "only_tokens":
            result = tokens
        elif self.stream_mode == "only_final":
            result = final_frame
        elif self.stream_mode == "no_citation_event":
            result = tokens + final_frame
        elif self.stream_mode == "truncated":
            result = result[:-2]
        elif self.stream_mode == "after_final":
            result += frame("error", {**identity, "code": "bad", "message": PRIVATE})
        elif self.stream_mode == "error_event":
            result = tokens + frame("error", {**identity, "code": "bad", "message": PRIVATE})
        elif self.stream_mode == "bad_event_type":
            result = 'event: token\ndata: {"event":"final"}\n\n'
        elif self.stream_mode == "bad_json":
            result = "event: token\ndata: {bad json}\n\n"
        elif self.stream_mode == "crlf":
            result = ": keepalive\r\n\r\n" + result.replace("\n", "\r\n")
        if self.stream_mode == "bad_utf8":
            return b"\xff\xfe"
        return result.encode()

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path in self.overrides:
            return self.overrides[path](request)
        assert not any(header.startswith("x-dev-") for header in request.headers)
        if path not in {"/health", "/ready", "/auth/login"}:
            assert request.headers["authorization"] == "Bearer " + TOKEN
        if path == "/health":
            return httpx.Response(200, json=envelope({"status": "ok", "service": "api"}))
        if path == "/ready":
            return httpx.Response(200, json=envelope({"ready": True, "dependencies": []}))
        if path == "/auth/login":
            assert json.loads(request.content) == {
                "tenant_id": "tenant-test",
                "username": "operator",
                "password": PASSWORD,
            }
            return httpx.Response(
                200,
                json=envelope(
                    {
                        **self.identity,
                        "access_token": TOKEN,
                        "refresh_token": "unused-refresh-secret",
                        "token_type": "bearer",
                        "expires_in": 3600,
                        "permissions": sorted(acceptance.REQUIRED_PERMISSIONS),
                        "roles": ["admin"],
                    }
                ),
            )
        if path == "/auth/logout":
            return httpx.Response(200, json=envelope({"revoked": True}))
        if path == "/upload":
            assert request.method == "POST"
            body = request.content.decode()
            assert '"visibility": "private"' in body
            assert '"allowed_users": ["user-test"]' in body
            assert self.probe.report.run_id in body
            assert self.probe.report.document_id in body
            assert self.probe.report.version_id in body
            return httpx.Response(
                200,
                json=envelope(
                    {
                        **self.version,
                        "job_id": "job-test",
                        "status": "uploaded",
                    }
                ),
            )
        if path.endswith("/status"):
            status = self.statuses[min(self.status_reads, len(self.statuses) - 1)]
            self.status_reads += 1
            return httpx.Response(
                200,
                json=envelope(
                    {
                        **self.version,
                        "status": status,
                        "chunk_count": 1,
                        "vector_count": 1,
                        "index_status": "ready",
                    }
                ),
            )
        if path == "/retrieve":
            data = json.loads(request.content)
            assert data["metadata_filter"] == {"acceptance_probe_id": self.probe.report.run_id}
            assert "max_output_tokens" not in data
            return httpx.Response(
                200,
                json=envelope(
                    {
                        **self.identity,
                        "candidates": []
                        if self.deleted
                        else [{**self.citation, "tenant_id": "tenant-test"}],
                    }
                ),
            )
        if path == "/query/stream":
            assert json.loads(request.content)["metadata_filter"] == {
                "acceptance_probe_id": self.probe.report.run_id,
            }
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream; charset=utf-8"},
                stream=SplitStream(self.stream()),
            )
        if path == "/sources/resolve":
            assert json.loads(request.content) == {**self.version, "chunk_id": "chunk-test"}
            return (
                httpx.Response(404, json=denied())
                if self.deleted
                else httpx.Response(
                    200,
                    json=envelope(
                        {
                            **self.version,
                            "chunk_id": "chunk-test",
                            "text_excerpt": self.probe._content,
                        }
                    ),
                )
            )
        if request.method == "DELETE":
            assert path == f"/documents/{self.probe.report.document_id}"
            self.deleted = True
            return httpx.Response(
                200,
                json=envelope(
                    {
                        "document_id": self.probe.report.document_id,
                        "version_id": None,
                        "status": "deleted",
                        "deleted_versions": 1,
                        "deleted_chunks": 1,
                        "deleted_vectors": 1,
                    }
                ),
            )
        raise AssertionError(f"Unexpected route: {request.method} {path}")


@pytest.fixture
def deployment() -> SyntheticDeployment:
    return SyntheticDeployment(
        DeploymentProbe(
            ProbeConfig(
                base_url="http://127.0.0.1:8000",
                tenant_id="tenant-test",
                username="operator",
                password=SecretStr(PASSWORD),
                request_timeout=0.1,
                indexing_timeout=0.08,
                stream_timeout=0.1,
                poll_interval=0.001,
            )
        )
    )


async def run(deployment: SyntheticDeployment) -> acceptance.ProbeReport:
    async with httpx.AsyncClient(transport=httpx.MockTransport(deployment.handle)) as client:
        return await deployment.probe.run(client)


async def test_complete_mock_http_workflow(deployment: SyntheticDeployment) -> None:
    report = await run(deployment)
    assert report.outcome == "passed"
    assert report.error_code is None
    assert report.cleanup == "deleted"
    assert report.logout == "revoked"
    assert not report.upload_outcome_unknown
    assert deployment.status_reads == 3
    assert report.checks_passed == [
        "health",
        "local_login",
        "synthetic_private_upload",
        "exact_version_retrieval_ready",
        "scoped_retrieval",
        "streamed_known_answer_and_citations",
        "authorized_exact_source",
        "deleted_source_and_retrieval_hidden",
    ]
    paths = [request.url.path for request in deployment.requests]
    assert paths.count("/upload") == 1
    assert paths.count("/query/stream") == 1
    assert paths.count("/sources/resolve") == 2
    assert paths[-1] == "/auth/logout"
    assert len([request for request in deployment.requests if request.method == "DELETE"]) == 1
    for forbidden in (PASSWORD, TOKEN, PRIVATE, deployment.probe._answer_code):
        assert forbidden not in json.dumps(asdict(report))
        assert forbidden not in repr(deployment.probe)
        assert forbidden not in repr(deployment.probe.config)


async def test_retryable_state_can_become_ready(deployment: SyntheticDeployment) -> None:
    deployment.statuses = ["failed_retryable", "retrieval_ready"]
    assert (await run(deployment)).outcome == "passed"


@pytest.mark.parametrize(
    "state,code",
    [
        ("failed_terminal", "PROBE_INDEXING_FAILED"),
        ("deleted", "PROBE_INDEXING_FAILED"),
        ("mystery", "PROBE_STATUS_UNKNOWN"),
        ("uploaded", "PROBE_INDEXING_TIMEOUT"),
    ],
)
async def test_indexing_failure_cleans_up(
    deployment: SyntheticDeployment,
    state: str,
    code: str,
) -> None:
    deployment.statuses = [state]
    report = await run(deployment)
    assert report.outcome == "failed"
    assert report.error_code == code
    assert report.stage == "indexing"
    assert report.cleanup == "deleted"
    assert report.logout == "revoked"
    assert not any(request.url.path == "/query/stream" for request in deployment.requests)


async def test_ready_requires_exact_version(deployment: SyntheticDeployment) -> None:
    deployment.overrides[deployment.probe._status_path()] = lambda _: httpx.Response(
        200,
        json=envelope({**deployment.version, "version_id": "wrong", "status": "retrieval_ready"}),
    )
    report = await run(deployment)
    assert report.error_code == "PROBE_VERSION_MISMATCH"
    assert report.cleanup == "deleted"


async def test_ready_requires_searchable_counts(deployment: SyntheticDeployment) -> None:
    deployment.overrides[deployment.probe._status_path()] = lambda _: httpx.Response(
        200,
        json=envelope(
            {
                **deployment.version,
                "status": "retrieval_ready",
                "chunk_count": 0,
                "vector_count": 0,
                "index_status": "ready",
            }
        ),
    )
    assert (await run(deployment)).error_code == "PROBE_READY_INVALID"


@pytest.mark.parametrize(
    "mode,code",
    [
        ("no_answer", "PROBE_ANSWER_FAILED"),
        ("error_final", "PROBE_ANSWER_FAILED"),
        ("missing_fact", "PROBE_KNOWN_ANSWER_MISSING"),
        ("missing_marker", "PROBE_CITATION_BINDING_INVALID"),
        ("forged_marker", "PROBE_CITATION_BINDING_INVALID"),
        ("foreign_identity", "PROBE_IDENTITY_MISMATCH"),
        ("unsupported", "PROBE_ANSWER_UNSUPPORTED"),
        ("forged_citation", "PROBE_CITATION_INVALID"),
        ("wrong_version", "PROBE_VERSION_MISMATCH"),
        ("unknown_chunk", "PROBE_CITATION_UNKNOWN"),
        ("empty_citations", "PROBE_CITATION_MISSING"),
        ("only_tokens", "PROBE_STREAM_INCOMPLETE"),
        ("only_final", "PROBE_STREAM_INCOMPLETE"),
        ("no_citation_event", "PROBE_CITATION_BINDING_INVALID"),
        ("truncated", "PROBE_SSE_TRUNCATED"),
        ("after_final", "PROBE_SSE_AFTER_FINAL"),
        ("error_event", "PROBE_SSE_ERROR"),
        ("bad_event_type", "PROBE_SSE_EVENT_INVALID"),
        ("bad_json", "PROBE_JSON_INVALID"),
        ("bad_utf8", "PROBE_SSE_ENCODING_INVALID"),
    ],
)
async def test_stream_fails_closed_and_cleans_up(
    deployment: SyntheticDeployment,
    mode: str,
    code: str,
) -> None:
    deployment.stream_mode = mode
    report = await run(deployment)
    assert report.error_code == code
    assert report.stage == "stream"
    assert report.cleanup == "deleted"
    assert report.logout == "revoked"


async def test_fragmented_crlf_and_utf8_are_supported(deployment: SyntheticDeployment) -> None:
    deployment.stream_mode = "crlf"
    assert (await run(deployment)).outcome == "passed"


async def test_stream_total_deadline_covers_trickle_data(deployment: SyntheticDeployment) -> None:
    deployment.overrides["/query/stream"] = lambda _: httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        stream=SplitStream(deployment.stream(), delay=0.02),
    )
    assert (await run(deployment)).error_code == "PROBE_STREAM_TIMEOUT"


async def test_response_size_bound(deployment: SyntheticDeployment) -> None:
    deployment.overrides["/ready"] = lambda _: httpx.Response(
        200,
        content=b"x" * (acceptance.MAX_RESPONSE_BYTES + 1),
    )
    report = await run(deployment)
    assert report.error_code == "PROBE_RESPONSE_TOO_LARGE"
    assert report.cleanup == "not_needed"


async def test_stream_size_bound(deployment: SyntheticDeployment) -> None:
    deployment.overrides["/query/stream"] = lambda _: httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=b"x" * (acceptance.MAX_RESPONSE_BYTES + 1),
    )
    assert (await run(deployment)).error_code == "PROBE_RESPONSE_TOO_LARGE"


@pytest.mark.parametrize(
    "status,code",
    [
        (200, "SOURCE_ACCESS_DENIED"),
        (401, "AUTH_TOKEN_EXPIRED"),
        (403, "RAG_QUERY_FORBIDDEN"),
        (404, "NOT_FOUND"),
        (503, "SOURCE_ACCESS_DENIED"),
    ],
)
async def test_deleted_source_must_return_specific_denial(
    deployment: SyntheticDeployment,
    status: int,
    code: str,
) -> None:
    deployment.overrides["/sources/resolve"] = lambda _: (
        httpx.Response(
            status,
            json=denied(code),
        )
        if deployment.deleted
        else httpx.Response(
            200,
            json=envelope(
                {
                    **deployment.version,
                    "chunk_id": "chunk-test",
                    "text_excerpt": deployment.probe._content,
                }
            ),
        )
    )
    assert (await run(deployment)).error_code == "PROBE_DELETED_SOURCE_NOT_DENIED"


async def test_deleted_source_cannot_include_data(deployment: SyntheticDeployment) -> None:
    deployment.overrides["/sources/resolve"] = lambda _: (
        httpx.Response(
            404,
            json={**denied(), "data": {"text_excerpt": PRIVATE}},
        )
        if deployment.deleted
        else httpx.Response(
            200,
            json=envelope(
                {
                    **deployment.version,
                    "chunk_id": "chunk-test",
                    "text_excerpt": deployment.probe._content,
                }
            ),
        )
    )
    assert (await run(deployment)).error_code == "PROBE_DELETED_SOURCE_NOT_DENIED"


async def test_deleted_retrieval_must_be_empty(deployment: SyntheticDeployment) -> None:
    deployment.overrides["/retrieve"] = lambda _: httpx.Response(
        200,
        json=envelope(
            {
                **deployment.identity,
                "candidates": [{**deployment.citation, "tenant_id": "tenant-test"}],
            }
        ),
    )
    assert (await run(deployment)).error_code == "PROBE_DELETED_RETRIEVAL_VISIBLE"


async def test_source_must_contain_exact_uploaded_fact(deployment: SyntheticDeployment) -> None:
    deployment.overrides["/sources/resolve"] = lambda _: httpx.Response(
        200,
        json=envelope(
            {
                **deployment.version,
                "chunk_id": "chunk-test",
                "text_excerpt": PRIVATE,
            }
        ),
    )
    report = await run(deployment)
    assert report.error_code == "PROBE_SOURCE_CONTENT_MISMATCH"
    assert report.cleanup == "deleted"


async def test_scope_escape_is_never_accepted(deployment: SyntheticDeployment) -> None:
    deployment.overrides["/retrieve"] = lambda _: httpx.Response(
        200,
        json=envelope(
            {
                **deployment.identity,
                "candidates": [
                    {
                        **deployment.citation,
                        "document_id": "private-doc",
                        "tenant_id": "tenant-test",
                    }
                ],
            }
        ),
    )
    report = await run(deployment)
    assert report.error_code == "PROBE_VERSION_MISMATCH"
    assert report.cleanup == "deleted"


async def test_cleanup_failure_preserves_original_error_and_ids(
    deployment: SyntheticDeployment,
) -> None:
    deployment.statuses = ["failed_terminal"]
    deployment.overrides[f"/documents/{deployment.probe.report.document_id}"] = lambda _: (
        httpx.Response(503, json=denied("STORAGE_PRIVATE_FAILURE"))
    )
    report = await run(deployment)
    assert report.outcome == "failed"
    assert report.error_code == "PROBE_INDEXING_FAILED"
    assert report.cleanup_error_code == "PROBE_DELETE_FAILED"
    assert report.cleanup == "failed"
    assert report.document_id and report.version_id
    assert report.logout == "revoked"
    assert PRIVATE not in json.dumps(asdict(report))


async def test_logout_failure_makes_otherwise_successful_probe_fail(
    deployment: SyntheticDeployment,
) -> None:
    deployment.overrides["/auth/logout"] = lambda _: httpx.Response(
        200,
        json=envelope(
            {
                "revoked": False,
            }
        ),
    )
    report = await run(deployment)
    assert report.outcome == "failed"
    assert report.error_code == "PROBE_LOGOUT_NOT_REVOKED"
    assert report.logout == "failed"
    assert report.cleanup == "deleted"


async def test_wrong_login_tenant_does_not_upload(deployment: SyntheticDeployment) -> None:
    deployment.overrides["/auth/login"] = lambda _: httpx.Response(
        200,
        json=envelope(
            {
                "access_token": TOKEN,
                "user_id": "user-test",
                "tenant_id": "other-tenant",
            }
        ),
    )
    report = await run(deployment)
    assert report.error_code == "PROBE_IDENTITY_MISMATCH"
    assert report.cleanup == "not_needed"
    assert report.logout == "revoked"
    assert not any(request.url.path == "/upload" for request in deployment.requests)


async def test_ambiguous_upload_is_not_retried(deployment: SyntheticDeployment) -> None:
    def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(PRIVATE + PASSWORD + TOKEN)

    deployment.overrides["/upload"] = timeout
    report = await run(deployment)
    assert report.error_code == "PROBE_REQUEST_TIMEOUT"
    assert report.upload_outcome_unknown
    assert report.cleanup == "unconfirmed"
    assert len([r for r in deployment.requests if r.url.path == "/upload"]) == 1
    assert PASSWORD not in json.dumps(asdict(report))


async def test_uncertain_upload_plus_missing_document_remains_unconfirmed(
    deployment: SyntheticDeployment,
) -> None:
    deployment.overrides["/upload"] = lambda _: httpx.Response(500, json=denied("FAILED"))
    deployment.overrides[f"/documents/{deployment.probe.report.document_id}"] = lambda _: (
        httpx.Response(404, json=denied("DOCUMENT_NOT_FOUND"))
    )
    report = await run(deployment)
    assert report.outcome == "failed"
    assert report.cleanup == "unconfirmed"
    assert report.upload_outcome_unknown


async def test_redirect_never_receives_password_or_bearer(deployment: SyntheticDeployment) -> None:
    deployment.overrides["/auth/login"] = lambda _: httpx.Response(
        307,
        headers={"location": "https://untrusted.example/collect"},
        content=b"{}",
    )
    report = await run(deployment)
    assert report.error_code == "PROBE_REDIRECT_REFUSED"
    assert all(request.url.host == "127.0.0.1" for request in deployment.requests)
    assert report.cleanup == "not_needed"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000",
        "http://localhost:8000/",
        "http://[::1]:8000",
        "https://private-api.example:8443",
    ],
)
def test_safe_urls(url: str) -> None:
    validate_base_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.org",
        "http://192.168.0.1:8000",
        "ftp://localhost",
        "https://user:secret@example.org",
        "https://example.org/?token=secret",
        "https://example.org/#secret",
        "https://example.org/path",
        "http://localhost:0",
        "http://localhost:bad",
        "https://example.org\n",
        "not a url",
    ],
)
def test_unsafe_urls_are_rejected_without_echo(url: str) -> None:
    with pytest.raises(ProbeError) as error:
        validate_base_url(url)
    assert url not in str(error.value)


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan"), 3601])
def test_invalid_timeout_rejected(value: float) -> None:
    with pytest.raises(ProbeError, match="PROBE_TIMEOUT_INVALID"):
        ProbeConfig(
            base_url="http://localhost",
            tenant_id="test",
            username="test",
            password=SecretStr(PASSWORD),
            request_timeout=value,
        )


def test_password_file_preserves_spaces_and_redacts_repr(tmp_path: Path) -> None:
    path = tmp_path / "secret"
    path.write_text("  spaced-password  \r\n")
    path.chmod(0o600)
    password = read_password(path)
    assert password.get_secret_value() == "  spaced-password  "
    assert "spaced-password" not in repr(password)


@pytest.mark.parametrize("content", ["", "first\nsecond\n", "pw\x00", "x" * 1025, "pw\r"])
def test_password_file_invalid_values(tmp_path: Path, content: str) -> None:
    path = tmp_path / "secret"
    path.write_text(content)
    path.chmod(0o600)
    with pytest.raises(ProbeError):
        read_password(path)


def test_password_file_requires_private_permissions_and_no_symlink(tmp_path: Path) -> None:
    path = tmp_path / "secret"
    path.write_text(PASSWORD)
    path.chmod(0o644)
    with pytest.raises(ProbeError, match="PROBE_PASSWORD_FILE_UNSAFE"):
        read_password(path)
    path.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(ProbeError):
        read_password(link)


def test_password_file_does_not_read_fifo(tmp_path: Path) -> None:
    import os

    path = tmp_path / "fifo"
    os.mkfifo(path, mode=0o600)
    with pytest.raises(ProbeError, match="PROBE_PASSWORD_FILE_UNSAFE"):
        read_password(path)


def test_password_cli_is_not_accepted_or_echoed(capsys: pytest.CaptureFixture[str]) -> None:
    args = [
        "--base-url",
        "http://localhost",
        "--tenant-id",
        "test",
        "--username",
        "test",
        "--confirm-isolated-target",
        "--password",
        PASSWORD,
    ]
    with pytest.raises(SystemExit) as error:
        argument_parser().parse_args(args)
    assert error.value.code == 2
    output = capsys.readouterr()
    assert PASSWORD not in output.err + output.out
    assert "PROBE_ARGUMENTS_INVALID" in output.err


def test_target_confirmation_is_required() -> None:
    with pytest.raises(SystemExit) as error:
        argument_parser().parse_args(
            ["--base-url", "http://localhost", "--tenant-id", "test", "--username", "test"]
        )
    assert error.value.code == 2


def test_cli_summary_and_exit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "secret"
    path.write_text(PASSWORD)
    path.chmod(0o600)

    async def mocked_live(probe: DeploymentProbe) -> None:
        await run(SyntheticDeployment(probe))

    monkeypatch.setattr(acceptance, "_run_live", mocked_live)
    assert (
        acceptance.main(
            [
                "--base-url",
                "http://localhost",
                "--tenant-id",
                "tenant-test",
                "--username",
                "operator",
                "--password-file",
                str(path),
                "--confirm-isolated-target",
                "--poll-interval",
                "0.001",
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    assert json.loads(output.out)["outcome"] == "passed"
    assert not output.err
    assert PASSWORD not in output.out and TOKEN not in output.out


def test_real_backend_stream_serializers_are_accepted(deployment: SyntheticDeployment) -> None:
    """Guard the mock against changing canonical backend event/DTO serialization."""
    from packages.rag.dto import Citation
    from packages.rag.streaming import (
        citation_event,
        final_event,
        format_sse_event,
        token_event,
    )

    citation = Citation.model_validate(deployment.citation)
    context = {"request_id": "real-serializer-request", "trace_id": "real-serializer-trace"}
    wire = "".join(
        format_sse_event(event)
        for event in (
            token_event(**context, index=0, delta="Acceptance code "),
            citation_event(**context, citation=citation),
            final_event(
                request_id=context["request_id"],
                trace_id=context["trace_id"],
                tenant_id="tenant-test",
                user_id="user-test",
                answer=f"{deployment.probe._answer_code} [{citation.citation_id}]",
                citations=(citation,),
                no_answer=False,
                unsupported_claims=(),
                metadata={},
            ),
        )
    )
    deployment.overrides["/query/stream"] = lambda _: httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=wire,
    )
    assert asyncio.run(run(deployment)).outcome == "passed"


async def test_cancellation_attempts_owned_cleanup_and_logout(
    deployment: SyntheticDeployment,
) -> None:
    async def cancelled_stream() -> None:
        raise asyncio.CancelledError

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == deployment.probe._status_path():
            await cancelled_stream()
        return deployment.handle(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(asyncio.CancelledError):
            await deployment.probe.run(client)
    assert deployment.probe.report.error_code == "PROBE_INTERRUPTED"
    assert deployment.probe.report.cleanup == "deleted"
    assert deployment.probe.report.logout == "revoked"


async def test_normal_request_has_total_deadline(deployment: SyntheticDeployment) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/ready":
            await asyncio.sleep(1)
        return deployment.handle(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        report = await deployment.probe.run(client)
    assert report.error_code == "PROBE_REQUEST_TIMEOUT"
    assert report.cleanup == "not_needed"


async def test_login_permissions_required_before_any_upload(
    deployment: SyntheticDeployment,
) -> None:
    deployment.overrides["/auth/login"] = lambda _: httpx.Response(
        200,
        json=envelope(
            {
                **deployment.identity,
                "access_token": TOKEN,
                "token_type": "bearer",
                "expires_in": 3600,
                "permissions": ["document:upload"],
            }
        ),
    )
    report = await run(deployment)
    assert report.error_code == "PROBE_LOGIN_INVALID"
    assert report.cleanup == "not_needed"
    assert report.logout == "revoked"
    assert not any(request.url.path == "/upload" for request in deployment.requests)


async def test_plain_json_is_not_an_accepted_stream(deployment: SyntheticDeployment) -> None:
    deployment.overrides["/query/stream"] = lambda _: httpx.Response(200, json=envelope({}))
    assert (await run(deployment)).error_code == "PROBE_SSE_CONTENT_TYPE_INVALID"


async def test_empty_retrieval_is_not_a_ready_answer(deployment: SyntheticDeployment) -> None:
    deployment.overrides["/retrieve"] = lambda _: httpx.Response(
        200,
        json=envelope(
            {
                **deployment.identity,
                "candidates": [],
            }
        ),
    )
    assert (await run(deployment)).error_code == "PROBE_RETRIEVAL_EMPTY"


async def test_delete_contract_cannot_redirect_cleanup_to_another_document(
    deployment: SyntheticDeployment,
) -> None:
    document_path = f"/documents/{deployment.probe.report.document_id}"
    deployment.overrides[document_path] = lambda _: httpx.Response(
        200,
        json=envelope(
            {
                "document_id": "existing-private-document",
                "version_id": None,
                "status": "deleted",
            }
        ),
    )
    report = await run(deployment)
    assert report.error_code == "PROBE_DELETE_INVALID"
    assert report.cleanup == "failed"
    assert all(
        request.url.path == document_path
        for request in deployment.requests
        if request.method == "DELETE"
    )


def test_missing_password_file_does_not_fall_back_to_stdin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(ProbeError, match="PROBE_PASSWORD_FILE_REQUIRED"):
        read_password(None)
    with pytest.raises(ProbeError, match="PROBE_PASSWORD_FILE_UNREADABLE"):
        read_password(tmp_path / "missing")


def test_unsafe_password_prompt_fallback_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    import getpass
    import warnings

    def unsafe_prompt(_: str) -> str:
        warnings.warn("Password will echo", getpass.GetPassWarning, stacklevel=2)
        raise AssertionError("Must fail before the echoing prompt is used")

    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(getpass, "getpass", unsafe_prompt)
    with pytest.raises(ProbeError, match="PROBE_PASSWORD_PROMPT_UNSAFE"):
        read_password(None)


def test_cli_invalid_url_never_reads_password(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def unexpected_read(_: Path | None) -> SecretStr:
        raise AssertionError("Do not read a password before validating the destination")

    monkeypatch.setattr(acceptance, "read_password", unexpected_read)
    result = acceptance.main(
        [
            "--base-url",
            "https://operator:secret@example.test",
            "--tenant-id",
            "tenant-test",
            "--username",
            "operator",
            "--confirm-isolated-target",
        ]
    )
    assert result == 1
    output = capsys.readouterr().out
    assert json.loads(output)["error_code"] == "PROBE_URL_INVALID"
    assert "secret" not in output


async def test_embedding_completed_state_can_advance_to_ready(
    deployment: SyntheticDeployment,
) -> None:
    deployment.statuses = ["embedded", "indexing", "retrieval_ready"]
    assert (await run(deployment)).outcome == "passed"

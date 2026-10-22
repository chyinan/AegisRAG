"""Opt-in HTTP acceptance of an isolated, synthetic AegisRAG deployment.

This is an operator CLI, not an application dependency. It never starts a stack,
creates users, changes providers, or treats a mocked run as deployment evidence.
See docs/operations/DEPLOYMENT_ACCEPTANCE.md before using a live endpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import codecs
import getpass
import hashlib
import ipaddress
import json
import math
import os
import re
import stat
import sys
import warnings
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import SecretStr

MAX_RESPONSE_BYTES = 1024 * 1024
PENDING_STATUSES = frozenset(
    {
        "uploaded",
        "queued",
        "parsing",
        "parsed",
        "chunking",
        "chunked",
        "embedding",
        "embedded",
        "indexing",
        "failed_retryable",
    }
)
REQUIRED_PERMISSIONS = frozenset({"document:manage", "document:read", "retrieval:query"})
JsonObject = dict[str, Any]


class ProbeError(Exception):
    """Only locally defined codes are safe to emit; never include response bodies."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ProbeConfig:
    base_url: str
    tenant_id: str
    username: str
    password: SecretStr = field(repr=False)
    request_timeout: float = 15.0
    indexing_timeout: float = 180.0
    stream_timeout: float = 60.0
    poll_interval: float = 2.0

    def __post_init__(self) -> None:
        validate_base_url(self.base_url)
        if not self.tenant_id.strip() or len(self.tenant_id) > 36:
            raise ProbeError("PROBE_IDENTITY_INVALID")
        if not self.username.strip() or len(self.username) > 255:
            raise ProbeError("PROBE_IDENTITY_INVALID")
        for value in (
            self.request_timeout,
            self.indexing_timeout,
            self.stream_timeout,
            self.poll_interval,
        ):
            if not math.isfinite(value) or not 0 < value <= 3600:
                raise ProbeError("PROBE_TIMEOUT_INVALID")
        if self.poll_interval > self.indexing_timeout:
            raise ProbeError("PROBE_TIMEOUT_INVALID")


@dataclass
class ProbeReport:
    schema_version: int = 1
    outcome: str = "failed"
    stage: str = "configuration"
    error_code: str | None = None
    run_id: str = field(default_factory=lambda: str(uuid4()))
    # IDs are generated locally and supplied to upload, never copied from error bodies.
    document_id: str = field(default_factory=lambda: str(uuid4()))
    version_id: str = field(default_factory=lambda: str(uuid4()))
    checks_passed: list[str] = field(default_factory=list)
    cleanup: str = "not_needed"
    cleanup_error_code: str | None = None
    logout: str = "not_needed"
    logout_error_code: str | None = None
    upload_outcome_unknown: bool = False

    def fail(self, code: str) -> None:
        self.outcome = "failed"
        if self.error_code is None:
            self.error_code = code


def validate_base_url(value: str) -> None:
    try:
        url = urlsplit(value)
        port = url.port
        host = url.hostname
    except ValueError:
        raise ProbeError("PROBE_URL_INVALID") from None
    if (
        url.scheme not in {"http", "https"}
        or not host
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or url.path not in {"", "/"}
        or (port is not None and port == 0)
        or any(char.isspace() or ord(char) < 32 for char in value)
    ):
        raise ProbeError("PROBE_URL_INVALID")
    if url.scheme == "http":
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
        if not loopback:
            raise ProbeError("PROBE_HTTPS_REQUIRED")


def read_password(path: Path | None) -> SecretStr:
    if path is None:
        if not sys.stdin.isatty():
            raise ProbeError("PROBE_PASSWORD_FILE_REQUIRED")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                value = getpass.getpass("Existing test-account password: ")
        except getpass.GetPassWarning:
            raise ProbeError("PROBE_PASSWORD_PROMPT_UNSAFE") from None
    else:
        # Do not follow symlinks, read a pipe/device, or accept group/world-readable secrets.
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open(path, flags)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode):
                    raise ProbeError("PROBE_PASSWORD_FILE_UNSAFE")
                if os.name == "posix" and info.st_mode & 0o077:
                    raise ProbeError("PROBE_PASSWORD_FILE_UNSAFE")
                raw = stream.read(1025)
            if len(raw) > 1024:
                raise ProbeError("PROBE_PASSWORD_INVALID")
            value = raw.decode("utf-8")
        except (OSError, UnicodeError):
            raise ProbeError("PROBE_PASSWORD_FILE_UNREADABLE") from None
        if value.endswith("\r\n"):
            value = value[:-2]
        elif value.endswith("\n"):
            value = value[:-1]
    if not value or len(value) > 255 or any(char in value for char in ("\r", "\n", "\x00")):
        raise ProbeError("PROBE_PASSWORD_INVALID")
    return SecretStr(value)


def _object(value: Any) -> JsonObject:
    if not isinstance(value, dict):
        raise ProbeError("PROBE_CONTRACT_INVALID")
    return value


def _text(data: JsonObject, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ProbeError("PROBE_CONTRACT_INVALID")
    return value


def _json(raw: bytes | str) -> JsonObject:
    try:
        return _object(json.loads(raw))
    except (ValueError, UnicodeError):
        raise ProbeError("PROBE_JSON_INVALID") from None


def _envelope(data: JsonObject) -> JsonObject:
    if data.get("error") is not None:
        raise ProbeError("PROBE_API_ERROR")
    _text(data, "request_id")
    return _object(data.get("data"))


def _canonical_citation(document_id: str, version_id: str, chunk_id: str) -> str:
    identity = json.dumps(
        (document_id, version_id, chunk_id), ensure_ascii=False, separators=(",", ":")
    )
    return "cite-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


class DeploymentProbe:
    def __init__(self, config: ProbeConfig, *, report: ProbeReport | None = None) -> None:
        self.config = config
        self.report = report or ProbeReport()
        self._token: SecretStr | None = None
        self._user_id: str | None = None
        self._upload_attempted = False
        self._deleted = False
        self._answer_code = "COBALT-" + uuid4().hex
        self._content = (
            f"Synthetic deployment acceptance probe {self.report.run_id}.\n"
            f"The exact acceptance code is {self._answer_code}.\n"
        )
        self._query: JsonObject = {
            "query": "What is the exact acceptance code? Give it verbatim and cite the source.",
            "top_k": 5,
            "metadata_filter": {"acceptance_probe_id": self.report.run_id},
            "max_output_tokens": 256,
        }

    def _headers(self) -> dict[str, str]:
        headers = {"X-Request-ID": str(uuid4())}
        if self._token is not None:
            headers["Authorization"] = "Bearer " + self._token.get_secret_value()
        return headers

    async def _request(
        self, client: httpx.AsyncClient, method: str, path: str, **kwargs: Any
    ) -> tuple[int, JsonObject]:
        # Both elapsed-time and response-size bounds apply, including slow trickle responses.
        try:
            async with asyncio.timeout(self.config.request_timeout):
                async with client.stream(
                    method,
                    self.config.base_url.rstrip("/") + path,
                    headers=self._headers(),
                    timeout=self.config.request_timeout,
                    follow_redirects=False,
                    **kwargs,
                ) as response:
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > MAX_RESPONSE_BYTES:
                            raise ProbeError("PROBE_RESPONSE_TOO_LARGE")
                    status = response.status_code
                    if 300 <= status < 400:
                        raise ProbeError("PROBE_REDIRECT_REFUSED")
                    return status, _json(bytes(chunks))
        except TimeoutError:
            raise ProbeError("PROBE_REQUEST_TIMEOUT") from None
        except httpx.TimeoutException:
            raise ProbeError("PROBE_REQUEST_TIMEOUT") from None
        except httpx.HTTPError:
            raise ProbeError("PROBE_HTTP_FAILED") from None

    async def _data(
        self, client: httpx.AsyncClient, method: str, path: str, **kwargs: Any
    ) -> JsonObject:
        status, body = await self._request(client, method, path, **kwargs)
        if status != 200:
            raise ProbeError("PROBE_HTTP_STATUS_UNEXPECTED")
        return _envelope(body)

    def _same_version(self, data: JsonObject) -> None:
        if (
            data.get("document_id") != self.report.document_id
            or data.get("version_id") != self.report.version_id
        ):
            raise ProbeError("PROBE_VERSION_MISMATCH")

    def _same_identity(self, data: JsonObject) -> None:
        if data.get("tenant_id") != self.config.tenant_id or data.get("user_id") != self._user_id:
            raise ProbeError("PROBE_IDENTITY_MISMATCH")

    async def _login(self, client: httpx.AsyncClient) -> None:
        data = await self._data(
            client,
            "POST",
            "/auth/login",
            json={
                "tenant_id": self.config.tenant_id,
                "username": self.config.username,
                "password": self.config.password.get_secret_value(),
            },
        )
        token = _text(data, "access_token")
        if len(token) > 16384 or any(char.isspace() or ord(char) < 32 for char in token):
            raise ProbeError("PROBE_LOGIN_INVALID")
        self._token = SecretStr(token)
        self._user_id = _text(data, "user_id")
        self._same_identity(data)
        permissions = data.get("permissions")
        if (
            data.get("token_type") != "bearer"
            or type(data.get("expires_in")) is not int
            or data["expires_in"] <= 0
            or not isinstance(permissions, list)
            or not all(isinstance(value, str) for value in permissions)
            or not REQUIRED_PERMISSIONS.issubset(permissions)
        ):
            raise ProbeError("PROBE_LOGIN_INVALID")

    async def _upload(self, client: httpx.AsyncClient) -> None:
        self._upload_attempted = True
        self.report.upload_outcome_unknown = True
        data = await self._data(
            client,
            "POST",
            "/upload",
            data={
                "document_id": self.report.document_id,
                "version_id": self.report.version_id,
                "source_type": "txt",
                "title": "Synthetic deployment acceptance",
                "acl": json.dumps({"visibility": "private", "allowed_users": [self._user_id]}),
                "metadata": json.dumps({"acceptance_probe_id": self.report.run_id}),
            },
            files={"file": (f"acceptance-{self.report.run_id}.txt", self._content, "text/plain")},
        )
        self._same_version(data)
        _text(data, "job_id")
        if data.get("status") != "uploaded":
            raise ProbeError("PROBE_UPLOAD_INVALID")
        self.report.upload_outcome_unknown = False

    def _status_path(self) -> str:
        return f"/documents/{self.report.document_id}/versions/{self.report.version_id}/status"

    async def _wait_ready(self, client: httpx.AsyncClient) -> None:
        try:
            async with asyncio.timeout(self.config.indexing_timeout):
                while True:
                    data = await self._data(client, "GET", self._status_path())
                    self._same_version(data)
                    status = data.get("status")
                    if status == "retrieval_ready":
                        if (
                            type(data.get("chunk_count")) is not int
                            or data["chunk_count"] <= 0
                            or type(data.get("vector_count")) is not int
                            or data["vector_count"] <= 0
                            or data.get("index_status") != "ready"
                        ):
                            raise ProbeError("PROBE_READY_INVALID")
                        return
                    if status in {"failed_terminal", "deleted"}:
                        raise ProbeError("PROBE_INDEXING_FAILED")
                    if not isinstance(status, str) or status not in PENDING_STATUSES:
                        raise ProbeError("PROBE_STATUS_UNKNOWN")
                    await asyncio.sleep(self.config.poll_interval)
        except TimeoutError:
            raise ProbeError("PROBE_INDEXING_TIMEOUT") from None

    async def _retrieve(self, client: httpx.AsyncClient, *, deleted: bool = False) -> set[str]:
        body = {key: value for key, value in self._query.items() if key != "max_output_tokens"}
        data = await self._data(client, "POST", "/retrieve", json=body)
        self._same_identity(data)
        candidates = data.get("candidates")
        if not isinstance(candidates, list):
            raise ProbeError("PROBE_RETRIEVAL_INVALID")
        if deleted:
            if candidates:
                raise ProbeError("PROBE_DELETED_RETRIEVAL_VISIBLE")
            return set()
        if not candidates:
            raise ProbeError("PROBE_RETRIEVAL_EMPTY")
        chunk_ids = set()
        for value in candidates:
            candidate = _object(value)
            self._same_version(candidate)
            if candidate.get("tenant_id") != self.config.tenant_id:
                raise ProbeError("PROBE_IDENTITY_MISMATCH")
            chunk_ids.add(_text(candidate, "chunk_id"))
        return chunk_ids

    def _citation(self, value: Any, chunk_ids: set[str]) -> JsonObject:
        citation = _object(value)
        self._same_version(citation)
        chunk_id = _text(citation, "chunk_id")
        if chunk_id not in chunk_ids:
            raise ProbeError("PROBE_CITATION_UNKNOWN")
        expected = _canonical_citation(self.report.document_id, self.report.version_id, chunk_id)
        if citation.get("citation_id") != expected:
            raise ProbeError("PROBE_CITATION_INVALID")
        return citation

    async def _stream(self, client: httpx.AsyncClient, chunk_ids: set[str]) -> JsonObject:
        final: JsonObject | None = None
        saw_delta = False
        cited_ids: set[str] = set()
        event_name = ""
        data_lines: list[str] = []
        buffer = ""
        byte_count = 0
        decoder = codecs.getincrementaldecoder("utf-8")()

        def consume(line: str) -> None:
            nonlocal event_name, data_lines, final, saw_delta
            if line == "":
                if not data_lines:
                    event_name = ""
                    return
                if final is not None:
                    raise ProbeError("PROBE_SSE_AFTER_FINAL")
                payload = _json("\n".join(data_lines))
                if payload.get("event") != event_name:
                    raise ProbeError("PROBE_SSE_EVENT_INVALID")
                if event_name == "error":
                    raise ProbeError("PROBE_SSE_ERROR")
                if event_name == "token":
                    delta = payload.get("delta")
                    if not isinstance(delta, str):
                        raise ProbeError("PROBE_SSE_TOKEN_INVALID")
                    saw_delta = saw_delta or bool(delta.strip())
                elif event_name == "citation":
                    citation = self._citation(payload.get("citation"), chunk_ids)
                    cited_ids.add(citation["citation_id"])
                elif event_name == "final":
                    final = payload
                else:
                    raise ProbeError("PROBE_SSE_EVENT_INVALID")
                event_name, data_lines = "", []
            elif line.startswith(":"):
                return
            elif line.startswith("event:"):
                event_name = line[6:].lstrip(" ")
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip(" "))
            elif line.startswith(("id:", "retry:")):
                return
            else:
                raise ProbeError("PROBE_SSE_FRAME_INVALID")

        try:
            async with asyncio.timeout(self.config.stream_timeout):
                async with client.stream(
                    "POST",
                    self.config.base_url.rstrip("/") + "/query/stream",
                    headers=self._headers(),
                    json=self._query,
                    timeout=self.config.request_timeout,
                    follow_redirects=False,
                ) as response:
                    if response.status_code != 200:
                        raise ProbeError("PROBE_HTTP_STATUS_UNEXPECTED")
                    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
                    if media_type != "text/event-stream":
                        raise ProbeError("PROBE_SSE_CONTENT_TYPE_INVALID")
                    async for chunk in response.aiter_bytes():
                        byte_count += len(chunk)
                        if byte_count > MAX_RESPONSE_BYTES:
                            raise ProbeError("PROBE_RESPONSE_TOO_LARGE")
                        buffer += decoder.decode(chunk)
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            consume(line.removesuffix("\r"))
                    buffer += decoder.decode(b"", final=True)
                    if buffer or data_lines or event_name:
                        raise ProbeError("PROBE_SSE_TRUNCATED")
        except TimeoutError:
            raise ProbeError("PROBE_STREAM_TIMEOUT") from None
        except httpx.TimeoutException:
            raise ProbeError("PROBE_STREAM_TIMEOUT") from None
        except httpx.HTTPError:
            raise ProbeError("PROBE_HTTP_FAILED") from None
        except UnicodeError:
            raise ProbeError("PROBE_SSE_ENCODING_INVALID") from None
        if not saw_delta or final is None:
            raise ProbeError("PROBE_STREAM_INCOMPLETE")
        self._same_identity(final)
        if final.get("status") != "success" or final.get("no_answer") is not False:
            raise ProbeError("PROBE_ANSWER_FAILED")
        if final.get("unsupported_claims") != []:
            raise ProbeError("PROBE_ANSWER_UNSUPPORTED")
        answer = _text(final, "answer")
        if self._answer_code not in answer:
            raise ProbeError("PROBE_KNOWN_ANSWER_MISSING")
        citations = final.get("citations")
        if not isinstance(citations, list) or not citations:
            raise ProbeError("PROBE_CITATION_MISSING")
        verified = [self._citation(citation, chunk_ids) for citation in citations]
        final_ids = {citation["citation_id"] for citation in verified}
        mentioned = set(re.findall(r"\[(cite-[0-9a-f]{16})\]", answer))
        if not mentioned or mentioned != final_ids or not final_ids.issubset(cited_ids):
            raise ProbeError("PROBE_CITATION_BINDING_INVALID")
        return verified[0]

    async def _source(
        self, client: httpx.AsyncClient, citation: JsonObject, *, deleted: bool = False
    ) -> None:
        body = {key: citation[key] for key in ("document_id", "version_id", "chunk_id")}
        status, envelope = await self._request(client, "POST", "/sources/resolve", json=body)
        if deleted:
            # Generic 401/403, arbitrary 404, and infrastructure failures cannot prove deletion.
            error = envelope.get("error")
            if (
                status not in {403, 404}
                or envelope.get("data") is not None
                or not isinstance(error, dict)
                or error.get("code") != "SOURCE_ACCESS_DENIED"
            ):
                raise ProbeError("PROBE_DELETED_SOURCE_NOT_DENIED")
            return
        if status != 200:
            raise ProbeError("PROBE_HTTP_STATUS_UNEXPECTED")
        data = _envelope(envelope)
        self._same_version(data)
        if data.get("chunk_id") != citation["chunk_id"]:
            raise ProbeError("PROBE_CITATION_INVALID")
        excerpt = _text(data, "text_excerpt")
        if self._answer_code not in excerpt or self.report.run_id not in excerpt:
            raise ProbeError("PROBE_SOURCE_CONTENT_MISMATCH")

    async def _delete(self, client: httpx.AsyncClient, *, cleanup: bool = False) -> None:
        status, envelope = await self._request(
            client, "DELETE", f"/documents/{self.report.document_id}"
        )
        if cleanup and status == 404:
            error = envelope.get("error")
            if (
                envelope.get("data") is None
                and isinstance(error, dict)
                and error.get("code") == "DOCUMENT_NOT_FOUND"
            ):
                self.report.cleanup = (
                    "unconfirmed" if self.report.upload_outcome_unknown else "absent"
                )
                return
        if status != 200:
            raise ProbeError("PROBE_DELETE_FAILED")
        data = _envelope(envelope)
        if (
            data.get("document_id") != self.report.document_id
            or data.get("version_id") is not None
            or data.get("status") != "deleted"
        ):
            raise ProbeError("PROBE_DELETE_INVALID")
        self._deleted = True
        self.report.cleanup = "unconfirmed" if self.report.upload_outcome_unknown else "deleted"

    async def run(self, client: httpx.AsyncClient) -> ProbeReport:
        try:
            self.report.stage = "health"
            health = await self._data(client, "GET", "/health")
            ready = await self._data(client, "GET", "/ready")
            if health.get("status") != "ok" or ready.get("ready") is not True:
                raise ProbeError("PROBE_NOT_READY")
            self.report.checks_passed.append("health")
            self.report.stage = "login"
            await self._login(client)
            self.report.checks_passed.append("local_login")
            self.report.stage = "upload"
            await self._upload(client)
            self.report.checks_passed.append("synthetic_private_upload")
            self.report.stage = "indexing"
            await self._wait_ready(client)
            self.report.checks_passed.append("exact_version_retrieval_ready")
            self.report.stage = "retrieval"
            chunks = await self._retrieve(client)
            self.report.checks_passed.append("scoped_retrieval")
            self.report.stage = "stream"
            citation = await self._stream(client, chunks)
            self.report.checks_passed.append("streamed_known_answer_and_citations")
            self.report.stage = "source"
            await self._source(client, citation)
            self.report.checks_passed.append("authorized_exact_source")
            self.report.stage = "delete"
            await self._delete(client)
            self.report.stage = "deletion_visibility"
            await self._source(client, citation, deleted=True)
            await self._retrieve(client, deleted=True)
            self.report.checks_passed.append("deleted_source_and_retrieval_hidden")
        except ProbeError as exc:
            self.report.fail(exc.code)
        except asyncio.CancelledError:
            self.report.fail("PROBE_INTERRUPTED")
            raise
        except Exception:
            # CLI boundary deliberately suppresses raw exceptions containing credentials/HTTP data.
            self.report.fail("PROBE_UNEXPECTED_ERROR")
        finally:
            if self._upload_attempted and not self._deleted:
                try:
                    await self._delete(client, cleanup=True)
                except Exception as exc:
                    self.report.cleanup = "failed"
                    code = exc.code if isinstance(exc, ProbeError) else "PROBE_CLEANUP_FAILED"
                    self.report.cleanup_error_code = code
                    self.report.fail(code)
            if self._token is not None:
                if self.report.error_code is None:
                    self.report.stage = "logout"
                try:
                    data = await self._data(client, "POST", "/auth/logout")
                    if data.get("revoked") is not True:
                        raise ProbeError("PROBE_LOGOUT_NOT_REVOKED")
                    self.report.logout = "revoked"
                except Exception as exc:
                    self.report.logout = "failed"
                    code = exc.code if isinstance(exc, ProbeError) else "PROBE_LOGOUT_FAILED"
                    self.report.logout_error_code = code
                    self.report.fail(code)
                finally:
                    self._token = None
        if self.report.error_code is None:
            self.report.outcome = "passed"
            self.report.stage = "complete"
        return self.report


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        # argparse's usual error may echo a mistakenly supplied --password secret.
        self.exit(2, "PROBE_ARGUMENTS_INVALID; use --help (no password arguments are accepted).\n")


def argument_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--base-url", required=True, help="Direct API origin; HTTPS or loopback HTTP"
    )
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument(
        "--username", required=True, help="Existing dedicated synthetic-test account"
    )
    parser.add_argument("--password-file", type=Path, help="Private UTF-8 file, one password line")
    parser.add_argument("--request-timeout", type=float, default=15.0)
    parser.add_argument("--indexing-timeout", type=float, default=180.0)
    parser.add_argument("--stream-timeout", type=float, default=60.0)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument(
        "--confirm-isolated-target",
        action="store_true",
        required=True,
        help="Confirm synthetic-only target and approved configured model/embedding endpoints",
    )
    return parser


async def _run_live(probe: DeploymentProbe) -> None:
    async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
        await probe.run(client)


def main(argv: list[str] | None = None) -> int:
    args = argument_parser().parse_args(argv)
    report = ProbeReport()
    try:
        # Validate destination before reading the credential. Never accept a credentialed URL.
        validate_base_url(args.base_url)
        config = ProbeConfig(
            base_url=args.base_url,
            tenant_id=args.tenant_id,
            username=args.username,
            password=read_password(args.password_file),
            request_timeout=args.request_timeout,
            indexing_timeout=args.indexing_timeout,
            stream_timeout=args.stream_timeout,
            poll_interval=args.poll_interval,
        )
        asyncio.run(_run_live(DeploymentProbe(config, report=report)))
    except ProbeError as exc:
        report.fail(exc.code)
    except (KeyboardInterrupt, EOFError):
        report.fail("PROBE_INTERRUPTED")
    except Exception:
        report.fail("PROBE_UNEXPECTED_ERROR")
    sys.stdout.write(json.dumps(asdict(report), sort_keys=True) + "\n")
    if report.error_code == "PROBE_INTERRUPTED":
        return 130
    return 0 if report.outcome == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

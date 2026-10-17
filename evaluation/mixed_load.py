#!/usr/bin/env python3
"""Opt-in target-environment measurements; see CONCURRENCY_ACCEPTANCE.md.

No live request is made without --allow-real-load. MockTransport runs verify
measurement contracts only and are explicitly labelled synthetic in reports.
"""

from __future__ import annotations

import argparse
import asyncio
import codecs
import json
import logging
import math
import os
import re
import time
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

LOGGER = logging.getLogger("evaluation.mixed_load")
PERCENTILE_METHOD = "nearest_rank_ceil_n_p_over_100"
Endpoint = Literal["/retrieve", "/query", "/query/stream", "/chat", "/chat/stream"]
Outcome = Literal["success", "failure", "planned_disconnect"]


class StrictConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False, frozen=True)


class Identity(StrictConfig):
    label: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,48}$")
    tenant_id: str = Field(min_length=1, max_length=128)
    user_id: str = Field(min_length=1, max_length=128)
    token_env: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")


class RequestCase(StrictConfig):
    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,48}$")
    identity: str
    endpoint: Endpoint
    payload: dict[str, Any]
    output_profile: Literal["retrieval", "short", "long"]
    weight: int = Field(default=1, ge=1, le=1000)
    behavior: Literal["normal", "slow_reader", "disconnect"] = "normal"
    slow_read_delay_s: float = Field(default=0, ge=0)
    disconnect_after_token_events: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if not isinstance(self.payload.get("query"), str) or not self.payload["query"].strip():
            raise ValueError("every case needs a nonblank query")
        if "session_id" in self.payload:
            raise ValueError("chat session_id is learned from the server per worker and identity")
        if self.endpoint == "/retrieve":
            if self.output_profile != "retrieval":
                raise ValueError("retrieve requires retrieval output_profile")
        else:
            limit = self.payload.get("max_output_tokens")
            if (
                self.output_profile == "retrieval"
                or isinstance(limit, bool)
                or not isinstance(limit, int)
                or not 1 <= limit <= 4096
            ):
                raise ValueError(
                    "generation requires short/long profile and max_output_tokens 1..4096"
                )
        streaming = self.endpoint.endswith("/stream")
        if self.behavior != "normal" and not streaming:
            raise ValueError("slow reading and disconnects require a streaming endpoint")
        if (self.behavior == "slow_reader") != (self.slow_read_delay_s > 0):
            raise ValueError("only slow_reader requires a positive slow_read_delay_s")
        if (self.behavior == "disconnect") != (self.disconnect_after_token_events is not None):
            raise ValueError("only disconnect requires disconnect_after_token_events")
        return self


class Stage(StrictConfig):
    name: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,48}$")
    users: int = Field(gt=0)
    duration_s: float = Field(gt=0)
    cache_state: Literal["unknown", "cold", "warm"] = "unknown"
    ingestion_state: Literal["idle", "external_active"] = "idle"
    external_ingestion_ref: str | None = None
    max_requests_per_user: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_ingestion(self) -> Self:
        if self.ingestion_state == "external_active" and not (
            self.external_ingestion_ref and self.external_ingestion_ref.strip()
        ):
            raise ValueError("external_active requires an external_ingestion_ref")
        return self


class MixedLoadConfig(StrictConfig):
    target_url: str
    target_revision: str = Field(min_length=1)
    identities: list[Identity] = Field(min_length=1)
    cases: list[RequestCase] = Field(min_length=1)
    stages: list[Stage] = Field(min_length=1)
    request_timeout_s: float = Field(default=120, gt=0)
    think_time_s: float = Field(default=0.1, ge=0)
    max_response_bytes: int = Field(default=16 * 1024 * 1024, gt=0)
    max_sse_event_bytes: int = Field(default=1024 * 1024, gt=0)

    @field_validator("target_url")
    @classmethod
    def validate_target(cls, value: str) -> str:
        try:
            url = httpx.URL(value)
        except (httpx.InvalidURL, TypeError, ValueError):
            raise ValueError("target_url must be an absolute HTTP(S) URL") from None
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.userinfo
            or "?" in value
            or "#" in value
        ):
            raise ValueError("target_url forbids credentials, query and fragment")
        return str(url).rstrip("/")

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        for values in (
            [identity.label for identity in self.identities],
            [identity.token_env for identity in self.identities],
            [(identity.tenant_id, identity.user_id) for identity in self.identities],
            [case.name for case in self.cases],
            [stage.name for stage in self.stages],
        ):
            if len(set(values)) != len(values):
                raise ValueError(
                    "identity labels, token sources, identities, cases and stages must be unique"
                )
        labels = {identity.label for identity in self.identities}
        if any(case.identity not in labels for case in self.cases):
            raise ValueError("case references an unknown identity")
        if any(
            not value.strip()
            for identity in self.identities
            for value in (
                identity.tenant_id,
                identity.user_id,
            )
        ):
            raise ValueError("identity values must not be blank")
        return self


class MeasurementError(Exception):
    """A safe fixed category, never provider text or credentials."""


@dataclass(frozen=True)
class Observation:
    request_id: str
    case: str
    outcome: Outcome
    error: str | None
    status_code: int | None
    elapsed_ms: float
    ttft_ms: float | None
    token_events: int
    answer_chars: int | None
    session_id: str | None = None


def _distribution(samples: list[float]) -> dict[str, Any]:
    ordered = sorted(samples)

    def percentile(p: int) -> float | None:
        return ordered[max(0, math.ceil(len(ordered) * p / 100) - 1)] if ordered else None

    return {
        "sample_count": len(ordered),
        "p50_ms": percentile(50),
        "p95_ms": percentile(95),
        "p99_ms": percentile(99),
        "avg_ms": sum(ordered) / len(ordered) if ordered else None,
        "min_ms": ordered[0] if ordered else None,
        "max_ms": ordered[-1] if ordered else None,
    }


def _summary(observations: list[Observation], elapsed_s: float) -> dict[str, Any]:
    successful = [item for item in observations if item.outcome == "success"]
    failed = [item for item in observations if item.outcome == "failure"]
    planned = sum(item.outcome == "planned_disconnect" for item in observations)
    return {
        "attempts": len(observations),
        "successful": len(successful),
        "failed": len(failed),
        "planned_disconnects": planned,
        "retries": 0,
        "attempts_per_s": len(observations) / elapsed_s if elapsed_s > 0 else 0,
        "successful_per_s": len(successful) / elapsed_s if elapsed_s > 0 else 0,
        "unexpected_failure_fraction": len(failed) / len(observations) if observations else None,
        "http_status_counts": dict(
            sorted(
                Counter(
                    str(item.status_code) for item in observations if item.status_code is not None
                ).items()
            )
        ),
        "error_counts": dict(sorted(Counter(item.error for item in failed).items())),
        "terminal_latency_all_attempts_ms": _distribution(
            [item.elapsed_ms for item in observations]
        ),
        "completion_latency_successful_only_ms": _distribution(
            [item.elapsed_ms for item in successful]
        ),
        "ttft_observed_all_outcomes_ms": _distribution(
            [item.ttft_ms for item in observations if item.ttft_ms is not None]
        ),
        "ttft_successful_only_ms": _distribution(
            [item.ttft_ms for item in successful if item.ttft_ms is not None]
        ),
        "nonempty_token_events": sum(item.token_events for item in observations),
        "successful_answer_chars": [
            item.answer_chars for item in successful if item.answer_chars is not None
        ],
    }


def _check_context(
    payload: Mapping[str, Any],
    identity: Identity,
    request_id: str,
    trace_id: str,
    *,
    final: bool,
) -> None:
    if payload.get("request_id") != request_id or payload.get("trace_id") != trace_id:
        raise MeasurementError("context_mismatch")
    if final and (
        payload.get("tenant_id") != identity.tenant_id or payload.get("user_id") != identity.user_id
    ):
        raise MeasurementError("identity_mismatch")


def _chat_session(
    payload: Mapping[str, Any],
    case: RequestCase,
    expected: str | None,
) -> str | None:
    if not case.endpoint.startswith("/chat"):
        return None
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise MeasurementError("invalid_result")
    if expected is not None and session_id != expected:
        raise MeasurementError("session_mismatch")
    return session_id


async def _sse_events(
    response: httpx.Response,
    config: MixedLoadConfig,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    name = "message"
    data: list[str] = []
    size = 0
    first_line = True
    async for line in _utf8_lines(response, config):
        if first_line:
            line = line.removeprefix("\ufeff")
            first_line = False
        added = len(line.encode("utf-8")) + 1
        size += added
        if size > config.max_sse_event_bytes:
            raise MeasurementError("response_limit")
        if not line:
            if data:
                try:
                    payload = json.loads("\n".join(data))
                except (ValueError, UnicodeError):
                    raise MeasurementError("invalid_sse") from None
                if not isinstance(payload, dict) or payload.get("event") != name:
                    raise MeasurementError("invalid_sse")
                yield name, payload
            name, data, size = "message", [], 0
        elif not line.startswith(":"):
            field, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if field == "event":
                name = value
            elif field == "data":
                data.append(value)
    if data or name != "message":
        raise MeasurementError("truncated_sse")


async def _utf8_lines(
    response: httpx.Response,
    config: MixedLoadConfig,
) -> AsyncIterator[str]:
    """Strict incremental UTF-8; bound decoded body and unfinished SSE lines."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
    pending = ""
    total = 0
    try:
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > config.max_response_bytes:
                raise MeasurementError("response_limit")
            pending += decoder.decode(chunk)
            while match := re.search(r"\r\n|\r|\n", pending):
                # A trailing CR may be the first half of CRLF in the next chunk.
                if match.group() == "\r" and match.end() == len(pending):
                    break
                line, pending = pending[: match.start()], pending[match.end() :]
                if len(line.encode("utf-8")) > config.max_sse_event_bytes:
                    raise MeasurementError("response_limit")
                yield line
            if len(pending.encode("utf-8")) > config.max_sse_event_bytes:
                raise MeasurementError("response_limit")
        pending += decoder.decode(b"", final=True)
    except UnicodeError:
        raise MeasurementError("invalid_sse") from None
    if pending:
        yield pending[:-1] if pending.endswith("\r") else pending


async def _json_result(
    response: httpx.Response,
    config: MixedLoadConfig,
    case: RequestCase,
    identity: Identity,
    request_id: str,
    trace_id: str,
    session_id: str | None,
) -> tuple[int | None, str | None]:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        body.extend(chunk)
        if len(body) > config.max_response_bytes:
            raise MeasurementError("response_limit")
    try:
        envelope = json.loads(body)
    except (ValueError, UnicodeError):
        raise MeasurementError("invalid_json") from None
    if not isinstance(envelope, dict) or envelope.get("error") is not None:
        raise MeasurementError("invalid_envelope")
    if envelope.get("request_id") != request_id or not isinstance(envelope.get("data"), dict):
        raise MeasurementError("invalid_envelope")
    data = envelope["data"]
    _check_context(data, identity, request_id, trace_id, final=True)
    if case.endpoint == "/retrieve":
        candidates = data.get("candidates")
        if not isinstance(candidates, list) or any(
            not isinstance(item, dict) for item in candidates
        ):
            raise MeasurementError("invalid_result")
        if any(item.get("tenant_id") != identity.tenant_id for item in candidates):
            raise MeasurementError("identity_mismatch")
        return None, None
    if not isinstance(data.get("answer"), str) or not isinstance(data.get("citations"), list):
        raise MeasurementError("invalid_result")
    return len(data["answer"]), _chat_session(data, case, session_id)


async def _request(
    client: httpx.AsyncClient,
    config: MixedLoadConfig,
    case: RequestCase,
    identity: Identity,
    token: str,
    request_id: str,
    trace_id: str,
    session_id: str | None,
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
) -> Observation:
    started = clock()
    status_code = None
    ttft = None
    tokens = 0
    answer_chars = None
    returned_session_id = None
    error = None
    outcome: Outcome = "success"
    payload = dict(case.payload)
    if case.endpoint.startswith("/chat") and session_id is not None:
        payload["session_id"] = session_id
    try:
        async with asyncio.timeout(config.request_timeout_s):
            async with client.stream(
                "POST",
                config.target_url + case.endpoint,
                json=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Request-ID": request_id,
                    "X-Trace-ID": trace_id,
                    "Accept": "text/event-stream"
                    if case.endpoint.endswith("/stream")
                    else "application/json",
                },
                follow_redirects=False,
                timeout=config.request_timeout_s,
            ) as response:
                status_code = response.status_code
                if not 200 <= status_code < 300:
                    raise MeasurementError("http_status")
                if not case.endpoint.endswith("/stream"):
                    answer_chars, returned_session_id = await _json_result(
                        response,
                        config,
                        case,
                        identity,
                        request_id,
                        trace_id,
                        session_id,
                    )
                else:
                    if response.headers.get("content-type", "").split(";")[0].strip().lower() != (
                        "text/event-stream"
                    ):
                        raise MeasurementError("invalid_content_type")
                    final_seen = False
                    async for name, event in _sse_events(response, config):
                        if final_seen:
                            raise MeasurementError("event_after_final")
                        _check_context(event, identity, request_id, trace_id, final=name == "final")
                        if name == "error":
                            raise MeasurementError("stream_error")
                        if name == "token":
                            if not isinstance(event.get("delta"), str):
                                raise MeasurementError("invalid_sse")
                            if event["delta"]:
                                tokens += 1
                                if ttft is None:
                                    ttft = (clock() - started) * 1000
                                if case.behavior == "disconnect" and tokens >= (
                                    case.disconnect_after_token_events or 1
                                ):
                                    outcome = "planned_disconnect"
                                    break
                        elif name == "final":
                            if event.get("status") != "success":
                                raise MeasurementError("stream_final_error")
                            if not isinstance(event.get("answer"), str) or not isinstance(
                                event.get("citations"),
                                list,
                            ):
                                raise MeasurementError("invalid_result")
                            final_seen = True
                            answer_chars = len(event["answer"])
                            returned_session_id = _chat_session(event, case, session_id)
                        elif name not in {"citation", "tool_call", "tool_result"}:
                            raise MeasurementError("unexpected_event")
                        if case.behavior == "slow_reader":
                            await sleep(case.slow_read_delay_s)
                    if outcome != "planned_disconnect":
                        if not final_seen:
                            raise MeasurementError("missing_final")
                        if case.behavior == "disconnect":
                            raise MeasurementError("disconnect_threshold_not_reached")
    except MeasurementError as exc:
        error = str(exc)
    except TimeoutError:
        error = "request_deadline"
    except httpx.TimeoutException:
        error = "httpx_timeout"
    except httpx.HTTPError:
        error = "transport_error"
    except Exception:
        # Count unexpected terminal failures without leaking exception text.
        # Cancellation is a BaseException and must propagate.
        error = "unexpected_error"
    if error is not None:
        outcome = "failure"
    return Observation(
        request_id,
        case.name,
        outcome,
        error,
        status_code,
        (clock() - started) * 1000,
        ttft,
        tokens,
        answer_chars,
        returned_session_id if outcome == "success" else None,
    )


async def _stage(
    config: MixedLoadConfig,
    stage: Stage,
    stage_index: int,
    run_id: str,
    client: httpx.AsyncClient,
    tokens: Mapping[str, str],
    clock: Callable[[], float],
    sleep: Callable[[float], Awaitable[None]],
) -> dict[str, Any]:
    identities = {identity.label: identity for identity in config.identities}
    plan = [case for case in config.cases for _ in range(case.weight)]
    observations: list[Observation] = []
    started_utc = datetime.now(UTC).isoformat()
    started = clock()
    deadline = started + stage.duration_s

    async def worker(index: int) -> None:
        sequence = 0
        sessions: dict[str, str] = {}
        while clock() < deadline:
            if stage.max_requests_per_user is not None and sequence >= stage.max_requests_per_user:
                break
            case = plan[(index + sequence) % len(plan)]
            request_id = f"ml-{run_id[:12]}-{stage_index}-{index}-{sequence}"
            observation = await _request(
                client,
                config,
                case,
                identities[case.identity],
                tokens[case.identity],
                request_id,
                uuid4().hex,
                sessions.get(case.identity),
                clock,
                sleep,
            )
            observations.append(observation)
            if observation.session_id is not None:
                sessions[case.identity] = observation.session_id
            sequence += 1
            if clock() < deadline and (
                stage.max_requests_per_user is None or sequence < stage.max_requests_per_user
            ):
                await sleep(min(config.think_time_s, max(0, deadline - clock())))

    tasks: list[asyncio.Task[None]] = []
    try:
        for index in range(stage.users):
            pending = worker(index)
            try:
                task = asyncio.create_task(pending)
            except BaseException:
                pending.close()
                raise
            tasks.append(task)
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    elapsed = max(0, clock() - started)
    summary = _summary(observations, elapsed)
    status = "completed"
    if not observations:
        status = "empty"
    elif summary["failed"]:
        status = "failed" if summary["failed"] == len(observations) else "partial"
    return {
        "name": stage.name,
        "users": stage.users,
        "status": status,
        "started_utc": started_utc,
        "ended_utc": datetime.now(UTC).isoformat(),
        "dispatch_window_s": stage.duration_s,
        "actual_elapsed_s": elapsed,
        "drain_s": max(0, elapsed - stage.duration_s),
        "cache_state": stage.cache_state,
        "cache_state_source": "operator_declaration_only",
        "ingestion_state": stage.ingestion_state,
        "external_ingestion_ref": stage.external_ingestion_ref,
        "max_requests_per_user": stage.max_requests_per_user,
        "summary": summary,
        "cases": {
            case.name: _summary([item for item in observations if item.case == case.name], elapsed)
            for case in config.cases
        },
        "failure_examples": [
            {
                "request_id": item.request_id,
                "case": item.case,
                "error": item.error,
                "http_status": item.status_code,
            }
            for item in observations
            if item.outcome == "failure"
        ][:20],
        "planned_disconnect_request_ids": [
            item.request_id for item in observations if item.outcome == "planned_disconnect"
        ][:20],
    }


async def run_mixed_load(
    config: MixedLoadConfig,
    *,
    allow_real_load: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
    environ: Mapping[str, str] | None = None,
    clock: Callable[[], float] = time.perf_counter,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, Any]:
    """Only a real MockTransport bypasses explicit live-load opt-in."""
    mocked = isinstance(transport, httpx.MockTransport)
    if not mocked and not allow_real_load:
        raise ValueError("real load requires explicit allow_real_load=True")
    environment = os.environ if environ is None else environ
    tokens = {
        identity.label: environment.get(identity.token_env, "") for identity in config.identities
    }
    if any(not token or re.search(r"\s", token) for token in tokens.values()):
        raise ValueError(
            "each identity requires its own nonblank bearer token environment variable"
        )
    if len(set(tokens.values())) != len(tokens):
        raise ValueError("identities must use distinct bearer tokens")
    max_users = max(stage.users for stage in config.stages)
    started = clock()
    run_id = uuid4().hex
    async with httpx.AsyncClient(
        transport=transport,
        timeout=config.request_timeout_s,
        follow_redirects=False,
        trust_env=False,
        limits=httpx.Limits(max_connections=max_users, max_keepalive_connections=max_users),
    ) as client:
        stages = [
            await _stage(config, stage, index, run_id, client, tokens, clock, sleep)
            for index, stage in enumerate(config.stages)
        ]
    completed = all(stage["status"] == "completed" for stage in stages)
    return {
        "schema_version": 1,
        "run_id": run_id,
        "status": "completed" if completed else "incomplete",
        "exit_code": 0 if completed else 1,
        "measurement_source": "mock_transport_contract_only" if mocked else "opted_in_target_http",
        "target_url": config.target_url,
        "target_revision": config.target_revision,
        "actual_elapsed_s": max(0, clock() - started),
        "percentile_method": PERCENTILE_METHOD,
        "retry_policy": "none",
        "auth_mode": "distinct_bearer_per_identity",
        "identity_labels": [identity.label for identity in config.identities],
        "request_timeout_s": config.request_timeout_s,
        "think_time_s": config.think_time_s,
        "cases": [
            {
                "name": case.name,
                "identity": case.identity,
                "endpoint": case.endpoint,
                "output_profile": case.output_profile,
                "weight": case.weight,
                "behavior": case.behavior,
                "slow_read_delay_s": case.slow_read_delay_s,
                "disconnect_after_token_events": case.disconnect_after_token_events,
                "max_output_tokens": case.payload.get("max_output_tokens"),
            }
            for case in config.cases
        ],
        "server_metrics": "not_collected; correlate external telemetry with stage UTC windows",
        "stages": stages,
    }


def save_report(report: Mapping[str, Any], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S_%fZ")
    path = directory / f"mixed_load_{stamp}_{uuid4().hex}.json"
    with path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")
    return path


async def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--allow-real-load", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path("evaluation/reports"))
    args = parser.parse_args(argv)
    if not args.allow_real_load:
        parser.error(
            "refusing real load without --allow-real-load; requests can invoke paid providers"
        )
    try:
        config = MixedLoadConfig.model_validate_json(args.config.read_text(encoding="utf-8"))
    except (OSError, ValueError, ValidationError):
        parser.error("configuration could not be read or validated; no request was started")
    try:
        report = await run_mixed_load(config, allow_real_load=True)
    except ValueError:
        parser.error("bearer token environment configuration is missing or invalid")
    try:
        path = save_report(report, args.output_dir)
    except (OSError, ValueError):
        LOGGER.error("measurement report could not be saved")
        return 1
    LOGGER.info("measurement status=%s report=%s", report["status"], path)
    return int(report["exit_code"])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(asyncio.run(main()))

"""Offline CLI destination/statistics regressions; no endpoint is contacted."""
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from evaluation import benchmark_pipeline as benchmark


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, milliseconds: float) -> None:
        self.now += milliseconds / 1000


def prepare(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    handler: Callable[[httpx.Request, Clock], httpx.Response],
    *,
    query_count: int = 1,
) -> tuple[list[httpx.Request], Clock]:
    requests: list[httpx.Request] = []
    clock = Clock()
    client_type = httpx.AsyncClient

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request, clock)

    def client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs['transport'] = httpx.MockTransport(respond)
        return client_type(*args, **kwargs)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(benchmark.httpx, 'AsyncClient', client)
    monkeypatch.setattr(benchmark.time, 'perf_counter', clock)
    monkeypatch.setattr(benchmark.sys if hasattr(benchmark, 'sys') else __import__('sys'), 'argv', [
        'benchmark_pipeline.py', '--rounds', '1', '--queries', str(query_count),
        '--api-url', 'https://benchmark.example.invalid',
    ])
    return requests, clock


def report(tmp_path: Path) -> dict:
    files = list((tmp_path / 'evaluation/reports').glob('benchmark_*.json'))
    assert len(files) == 1
    return json.loads(files[0].read_text())


def successful(request: httpx.Request, clock: Clock) -> httpx.Response:
    if request.url.path == '/retrieve':
        clock.advance(10)
        return httpx.Response(200, json={'data': {'candidates': []}})
    clock.advance(20)
    return httpx.Response(200, json={'data': {'answer': 'Synthetic answer'}})


@pytest.mark.asyncio
async def test_cli_sends_both_requests_to_selected_origin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    requests, _ = prepare(monkeypatch, tmp_path, successful)
    await benchmark.main()
    assert [str(item.url) for item in requests] == [
        'https://benchmark.example.invalid/retrieve',
        'https://benchmark.example.invalid/query',
    ]


@pytest.mark.asyncio
async def test_query_mean_uses_its_own_success_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    queries = 0

    def respond(request: httpx.Request, clock: Clock) -> httpx.Response:
        nonlocal queries
        if request.url.path == '/query':
            queries += 1
            if queries == 2:
                clock.advance(80)
                return httpx.Response(503, json={'message': 'synthetic failure'})
        return successful(request, clock)

    prepare(monkeypatch, tmp_path, respond, query_count=2)
    await benchmark.main()
    result = report(tmp_path)
    assert result['retrieve']['avg'] == 10.0
    assert result['query']['avg'] == 20.0


@pytest.mark.asyncio
async def test_all_failed_attempts_still_write_an_explicit_failure_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    def respond(request: httpx.Request, clock: Clock) -> httpx.Response:
        clock.advance(5)
        return httpx.Response(503, json={'message': 'synthetic failure'})

    prepare(monkeypatch, tmp_path, respond)
    exit_code = await benchmark.main()
    result = report(tmp_path)
    assert exit_code == 1
    assert result['status'] == 'incomplete'
    assert result['retrieve']['avg'] is None
    assert result['query']['avg'] is None


@pytest.mark.asyncio
async def test_partial_failure_is_not_a_successful_cli_exit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    query_calls = 0

    def respond(request: httpx.Request, clock: Clock) -> httpx.Response:
        nonlocal query_calls
        if request.url.path == '/query':
            query_calls += 1
            if query_calls == 2:
                clock.advance(5)
                return httpx.Response(503, json={'message': 'synthetic failure'})
        return successful(request, clock)

    prepare(monkeypatch, tmp_path, respond, query_count=2)
    assert await benchmark.main() == 1
    assert report(tmp_path)['status'] == 'incomplete'

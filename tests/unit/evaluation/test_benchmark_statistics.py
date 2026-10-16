"""Deterministic sequential benchmark accounting; synthetic HTTP transport only."""
from __future__ import annotations

import asyncio
import math

import httpx
import pytest

from evaluation import benchmark_pipeline as benchmark
from tests.unit.evaluation.test_benchmark_pipeline import Clock, successful


@pytest.mark.parametrize('latencies,expected', [
    ([], {'p50': None, 'p95': None, 'p99': None, 'avg': None, 'min': None, 'max': None}),
    ([5.0], {'p50': 5.0, 'p95': 5.0, 'p99': 5.0, 'avg': 5.0, 'min': 5.0, 'max': 5.0}),
    ([20.0, 10.0], {'p50': 10.0, 'p95': 20.0, 'p99': 20.0, 'avg': 15.0, 'min': 10.0, 'max': 20.0}),
])
def test_endpoint_percentiles_use_documented_nearest_rank(latencies, expected) -> None:
    samples = benchmark.EndpointSamples(planned=len(latencies), attempts=len(latencies))
    samples.latencies_ms.extend(latencies)
    result = samples.summary()
    assert {key: result[key] for key in expected} == expected


@pytest.mark.parametrize('changes', [
    {'rounds': 0}, {'rounds': -1}, {'rounds': 101}, {'rounds': True},
    {'queries': 0}, {'queries': 11}, {'queries': True},
    {'request_timeout_seconds': 0}, {'request_timeout_seconds': math.nan},
    {'request_timeout_seconds': math.inf}, {'request_timeout_seconds': 121},
])
def test_invalid_run_dimensions_fail_before_io(changes) -> None:
    with pytest.raises(ValueError):
        benchmark.BenchmarkConfig(**changes)


@pytest.mark.asyncio
async def test_wall_clock_rate_includes_failed_requests_and_uses_complete_pairs() -> None:
    clock = Clock()
    query_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal query_calls
        if request.url.path == '/query':
            query_calls += 1
            if query_calls == 2:
                clock.advance(80)
                return httpx.Response(503)
        return successful(request, clock)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await benchmark.run_benchmark(
            client, benchmark.BenchmarkConfig(rounds=1, queries=2), clock=clock,
        )
    assert result['status'] == 'incomplete'
    assert result['elapsed_seconds'] == 0.12
    assert result['completed_pairs'] == 1
    assert result['observed_sequential_pairs_per_second'] == 8.333333
    assert 'throughput_qps' not in result
    assert result['retrieve']['successes'] == 2
    assert result['query']['attempts'] == 2
    assert result['query']['successes'] == 1
    assert result['query']['failures'] == 1
    assert result['query']['avg'] == 20.0
    assert result['query']['errors'] == {'http_status_503': 1}


@pytest.mark.asyncio
async def test_failed_retrieval_preserves_existing_skip_policy_without_query_retry() -> None:
    paths = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await benchmark.run_benchmark(
            client, benchmark.BenchmarkConfig(rounds=1, queries=2), clock=Clock(),
        )
    assert paths == ['/retrieve', '/retrieve']
    assert result['retrieve']['attempts'] == result['retrieve']['failures'] == 2
    assert result['query']['attempts'] == result['query']['successes'] == 0
    assert result['query']['skipped'] == 2
    assert result['query']['avg'] is None
    assert result['observed_sequential_pairs_per_second'] is None


@pytest.mark.asyncio
async def test_request_timeout_cancels_owned_attempt_and_records_failure(monkeypatch) -> None:
    entered, cancelled = asyncio.Event(), asyncio.Event()
    contexts = []
    original_timeout = asyncio.timeout

    def timeout(delay):
        context = original_timeout(delay)
        contexts.append(context)
        return context

    async def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        raise AssertionError('unreachable')

    monkeypatch.setattr(benchmark.asyncio, 'timeout', timeout)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        task = asyncio.create_task(benchmark.run_benchmark(
            client, benchmark.BenchmarkConfig(rounds=1, queries=1, request_timeout_seconds=5),
        ))
        await asyncio.wait_for(entered.wait(), 5)
        contexts[0].reschedule(asyncio.get_running_loop().time())
        result = await task
    assert cancelled.is_set()
    assert result['retrieve']['errors'] == {'timeout': 1}
    assert result['query']['skipped'] == 1
    assert result['status'] == 'incomplete'


@pytest.mark.asyncio
async def test_consumer_cancellation_propagates_without_a_success_report() -> None:
    entered, cancelled = asyncio.Event(), asyncio.Event()

    async def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
        raise AssertionError('unreachable')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        task = asyncio.create_task(benchmark.run_benchmark(
            client, benchmark.BenchmarkConfig(rounds=1, queries=1),
        ))
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert cancelled.is_set()


@pytest.mark.parametrize('data', [None, {'data': []}, {'data': {'candidates': 'invalid'}}])
@pytest.mark.asyncio
async def test_invalid_response_is_counted_as_contract_failure(data) -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=data),
    )) as client:
        result = await benchmark.run_benchmark(
            client, benchmark.BenchmarkConfig(rounds=1, queries=1),
        )
    assert result['retrieve']['errors'] == {'invalid_response': 1}
    assert result['query']['skipped'] == 1
    assert result['status'] == 'incomplete'

import asyncio

import pytest

from packages.common.config import AppSettings
from packages.common.context import RequestContext
from packages.data.readiness import DependencyProbe, ProbeResult, collect_readiness


class _FakeProbe:
    def __init__(self, result: ProbeResult) -> None:
        self._result = result

    async def check(self) -> ProbeResult:
        return self._result


class _BoomProbe:
    name = "database"

    async def check(self) -> ProbeResult:
        raise RuntimeError("boom")


class _SlowProbe:
    name = "redis"

    async def check(self) -> ProbeResult:
        await asyncio.sleep(1)
        return ProbeResult(
            name="redis",
            configured=True,
            ok=True,
            latency_ms=1.0,
        )


def _probe(result: ProbeResult) -> DependencyProbe:
    return _FakeProbe(result)


@pytest.mark.asyncio
async def test_collect_readiness_treats_unconfigured_dependencies_as_non_blocking() -> None:
    readiness = await collect_readiness(
        probes=[
            _probe(
                ProbeResult(
                    name="database",
                    configured=False,
                    ok=False,
                    latency_ms=None,
                    error_code=None,
                )
            ),
        ],
    )

    assert readiness.ready is True
    assert readiness.dependencies[0].status == "not_configured"
    assert readiness.dependencies[0].required is False
    assert readiness.dependencies[0].blocking is False


@pytest.mark.asyncio
async def test_collect_readiness_marks_configured_failures_as_blocking() -> None:
    readiness = await collect_readiness(
        probes=[
            _probe(
                ProbeResult(
                    name="redis",
                    configured=True,
                    ok=False,
                    latency_ms=12.3,
                    error_code="redis_ping_failed",
                )
            ),
        ],
    )

    assert readiness.ready is False
    assert readiness.dependencies[0].status == "unavailable"
    assert readiness.dependencies[0].required is True
    assert readiness.dependencies[0].blocking is True
    assert readiness.dependencies[0].details == {
        "configured": True,
        "latency_ms": 12.3,
        "error_code": "redis_ping_failed",
    }


@pytest.mark.asyncio
async def test_collect_readiness_logs_safe_dependency_summary(
    capsys: pytest.CaptureFixture,
) -> None:
    import structlog
    structlog.reset_defaults()
    context = RequestContext(
        request_id="req-ready",
        trace_id="trace-ready",
        session_id=None,
    )

    await collect_readiness(
        probes=[
            _probe(
                ProbeResult(
                    name="minio",
                    configured=True,
                    ok=False,
                    latency_ms=5.0,
                    error_code="minio_http_503",
                )
            ),
        ],
        context=context,
    )

    captured = capsys.readouterr().out
    assert "api.readiness.checked" in captured
    assert "req-ready" in captured
    assert "minio_http_503" in captured
    assert "http://minio:9000" not in captured
    assert "secret" not in captured.lower()


@pytest.mark.asyncio
async def test_default_probe_collection_does_not_block_when_dependencies_are_unconfigured() -> None:
    settings = AppSettings(
        DATABASE_URL=None,
        REDIS_URL=None,
        MINIO_ENDPOINT=None,
        MINIO_ACCESS_KEY=None,
        MINIO_SECRET_KEY=None,
        MINIO_BUCKET=None,
    )

    readiness = await collect_readiness(settings=settings)

    assert readiness.ready is True
    assert {dependency.name for dependency in readiness.dependencies} == {
        "database",
        "redis",
        "minio",
        "vector_store",
    }
    assert all(dependency.status == "not_configured" for dependency in readiness.dependencies)


@pytest.mark.asyncio
async def test_collect_readiness_converts_probe_exceptions_to_blocking_status() -> None:
    readiness = await collect_readiness(probes=[_BoomProbe()])

    assert readiness.ready is False
    assert readiness.dependencies[0].name == "database"
    assert readiness.dependencies[0].status == "unavailable"
    assert readiness.dependencies[0].details["error_code"] == "database_probe_failed"


@pytest.mark.asyncio
async def test_collect_readiness_times_out_slow_probes() -> None:
    readiness = await collect_readiness(
        settings=AppSettings(READINESS_TIMEOUT_SECONDS=0.001),
        probes=[_SlowProbe()],
    )

    assert readiness.ready is False
    assert readiness.dependencies[0].name == "redis"
    assert readiness.dependencies[0].status == "unavailable"
    assert readiness.dependencies[0].details["error_code"] == "redis_probe_timeout"

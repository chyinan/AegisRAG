from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import Literal, Protocol

import httpx
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from packages.common.config import AppSettings, load_settings
from packages.common.context import RequestContext
from packages.common.health import DependencyStatus, ReadinessData
from packages.common.logging import (
    READINESS_CHECKED_EVENT,
    get_readiness_logger,
    log_structured_event,
)


@dataclass(frozen=True)
class ProbeResult:
    name: str
    configured: bool
    ok: bool
    latency_ms: float | None
    error_code: str | None = None


class DependencyProbe(Protocol):
    async def check(self) -> ProbeResult: ...


class DatabaseProbe:
    name = "database"

    def __init__(self, database_url: str | None, timeout_seconds: float) -> None:
        self._database_url = database_url
        self._timeout_seconds = timeout_seconds

    async def check(self) -> ProbeResult:
        if not self._database_url:
            return ProbeResult(
                name="database",
                configured=False,
                ok=False,
                latency_ms=None,
            )

        started_at = perf_counter()
        engine = create_async_engine(
            self._database_url,
            pool_pre_ping=True,
            connect_args={"timeout": self._timeout_seconds},
        )
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            return ProbeResult(
                name="database",
                configured=True,
                ok=False,
                latency_ms=_elapsed_ms(started_at),
                error_code="database_ping_failed",
            )
        finally:
            await engine.dispose()

        return ProbeResult(
            name="database",
            configured=True,
            ok=True,
            latency_ms=_elapsed_ms(started_at),
        )


class RedisProbe:
    name = "redis"

    def __init__(self, redis_url: str | None, timeout_seconds: float) -> None:
        self._redis_url = redis_url
        self._timeout_seconds = timeout_seconds

    async def check(self) -> ProbeResult:
        if not self._redis_url:
            return ProbeResult(
                name="redis",
                configured=False,
                ok=False,
                latency_ms=None,
            )

        started_at = perf_counter()
        client: Redis = Redis.from_url(
            self._redis_url,
            socket_connect_timeout=self._timeout_seconds,
            socket_timeout=self._timeout_seconds,
        )
        try:
            await client.ping()
        except Exception:
            return ProbeResult(
                name="redis",
                configured=True,
                ok=False,
                latency_ms=_elapsed_ms(started_at),
                error_code="redis_ping_failed",
            )
        finally:
            await client.aclose()

        return ProbeResult(
            name="redis",
            configured=True,
            ok=True,
            latency_ms=_elapsed_ms(started_at),
        )


class MinioProbe:
    name = "minio"

    def __init__(self, minio_endpoint: str | None, timeout_seconds: float) -> None:
        self._minio_endpoint = minio_endpoint
        self._timeout_seconds = timeout_seconds

    async def check(self) -> ProbeResult:
        if not self._minio_endpoint:
            return ProbeResult(
                name="minio",
                configured=False,
                ok=False,
                latency_ms=None,
            )

        started_at = perf_counter()
        health_url = f"{self._minio_endpoint.rstrip('/')}/minio/health/ready"
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.get(health_url)
        except Exception:
            return ProbeResult(
                name="minio",
                configured=True,
                ok=False,
                latency_ms=_elapsed_ms(started_at),
                error_code="minio_ping_failed",
            )

        if response.status_code < 200 or response.status_code >= 300:
            return ProbeResult(
                name="minio",
                configured=True,
                ok=False,
                latency_ms=_elapsed_ms(started_at),
                error_code=f"minio_http_{response.status_code}",
            )

        return ProbeResult(
            name="minio",
            configured=True,
            ok=True,
            latency_ms=_elapsed_ms(started_at),
        )


class VectorStoreProbe:
    name = "vector_store"

    async def check(self) -> ProbeResult:
        return ProbeResult(
            name="vector_store",
            configured=False,
            ok=False,
            latency_ms=None,
        )


async def collect_readiness(
    *,
    settings: AppSettings | None = None,
    probes: list[DependencyProbe] | None = None,
    context: RequestContext | None = None,
) -> ReadinessData:
    resolved_settings = settings or load_settings()
    resolved_probes = probes or _default_probes(resolved_settings)
    results = await asyncio.gather(
        *[
            _check_probe_safely(
                probe,
                timeout_seconds=resolved_settings.readiness_timeout_seconds,
            )
            for probe in resolved_probes
        ]
    )
    dependencies = [_dependency_status(result) for result in results]
    readiness = ReadinessData(
        ready=not any(dependency.blocking for dependency in dependencies),
        dependencies=dependencies,
    )
    if context is not None:
        _log_readiness(context=context, dependencies=dependencies)
    return readiness


async def _check_probe_safely(
    probe: DependencyProbe,
    *,
    timeout_seconds: float,
) -> ProbeResult:
    name = _probe_name(probe)
    try:
        return await asyncio.wait_for(probe.check(), timeout=timeout_seconds)
    except TimeoutError:
        return ProbeResult(
            name=name,
            configured=True,
            ok=False,
            latency_ms=None,
            error_code=f"{name}_probe_timeout",
        )
    except Exception:
        return ProbeResult(
            name=name,
            configured=True,
            ok=False,
            latency_ms=None,
            error_code=f"{name}_probe_failed",
        )


def _default_probes(settings: AppSettings) -> list[DependencyProbe]:
    return [
        DatabaseProbe(settings.database_url, settings.readiness_timeout_seconds),
        RedisProbe(settings.redis_url, settings.readiness_timeout_seconds),
        MinioProbe(settings.minio_endpoint, settings.readiness_timeout_seconds),
        VectorStoreProbe(),
    ]


def _dependency_status(result: ProbeResult) -> DependencyStatus:
    if not result.configured:
        return DependencyStatus(
            name=result.name,
            status="not_configured",
            required=False,
            blocking=False,
            message=f"{result.name} dependency is not configured.",
            details={"configured": False},
        )

    status: Literal["ok", "unavailable"] = "ok" if result.ok else "unavailable"
    details: dict[str, object] = {
        "configured": True,
        "latency_ms": result.latency_ms,
    }
    if result.error_code is not None:
        details["error_code"] = result.error_code

    return DependencyStatus(
        name=result.name,
        status=status,
        required=True,
        blocking=not result.ok,
        message=(
            f"{result.name} dependency is available."
            if result.ok
            else f"{result.name} dependency is unavailable."
        ),
        details=details,
    )


def _log_readiness(
    *,
    context: RequestContext,
    dependencies: list[DependencyStatus],
) -> None:
    dependency_summary = [
        {
            "name": dependency.name,
            "status": dependency.status,
            "configured": dependency.details.get("configured", False),
            "latency_ms": dependency.details.get("latency_ms"),
            "error_code": dependency.details.get("error_code"),
        }
        for dependency in dependencies
    ]
    log_structured_event(
        get_readiness_logger(),
        {
            "event": READINESS_CHECKED_EVENT,
            "request_id": context.request_id,
            "trace_id": context.trace_id,
            "session_id": context.session_id,
            "ready": not any(dependency.blocking for dependency in dependencies),
            "dependencies": dependency_summary,
        },
    )


def _probe_name(probe: DependencyProbe) -> str:
    explicit = getattr(probe, "name", None)
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    class_name = probe.__class__.__name__
    if class_name.endswith("Probe"):
        class_name = class_name[: -len("Probe")]
    return class_name.strip("_").lower() or "dependency"


def _elapsed_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 3)

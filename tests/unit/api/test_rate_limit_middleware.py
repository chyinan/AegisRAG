"""Rate-limit backend selection and fail-closed middleware tests."""

import pytest
from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import PlainTextResponse

from apps.api.rate_limit_middleware import RateLimitMiddleware, _build_limiter
from packages.common.rate_limit import (
    RateLimitBackendUnavailableError,
    RateLimitConfig,
)
from packages.data.adapters.redis_rate_limit import RedisRateLimiter


def test_production_requires_redis_for_rate_limiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(RuntimeError, match="REDIS_URL"):
        _build_limiter(config=None, redis_url=None, timeout_seconds=1.0)


def test_unknown_environment_requires_redis_for_rate_limiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("APP_ENV", raising=False)

    with pytest.raises(RuntimeError, match="REDIS_URL"):
        _build_limiter(config=None, redis_url=None, timeout_seconds=1.0)


def test_production_selects_redis_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")

    limiter = _build_limiter(
        config=RateLimitConfig(max_requests=10),
        redis_url="redis://localhost:6379/0",
        timeout_seconds=1.0,
    )

    assert isinstance(limiter, RedisRateLimiter)


@pytest.mark.asyncio
async def test_middleware_returns_503_when_redis_rate_limiter_is_unavailable() -> None:
    class BrokenLimiter:
        async def is_allowed(self, key: str, cost: int = 1) -> bool:
            del key, cost
            raise RateLimitBackendUnavailableError

        async def remaining(self, key: str) -> float:
            del key
            return 0

        async def aclose(self) -> None:
            return None

    middleware = RateLimitMiddleware(FastAPI(), limiter=BrokenLimiter())  # type: ignore[arg-type]
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/secure",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "client": ("127.0.0.1", 1),
            "server": ("test", 80),
            "root_path": "",
        }
    )

    async def call_next(_: Request) -> PlainTextResponse:
        raise AssertionError("backend failure must stop request processing")

    response = await middleware.dispatch(request, call_next)

    assert response.status_code == 503

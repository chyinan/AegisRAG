"""Distributed Redis rate limiter contract tests."""

import pytest

from packages.common.rate_limit import (
    RateLimitBackendUnavailableError,
    RateLimitConfig,
)
from packages.data.adapters.redis_rate_limit import RedisRateLimiter


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def eval(
        self,
        script: str,
        numkeys: int,
        key: str,
        cost: int,
        ttl: int,
    ) -> int:
        assert script
        assert numkeys == 1
        current = self.values.get(key, 0) + int(cost)
        self.values[key] = current
        self.ttls.setdefault(key, int(ttl))
        return current

    async def get(self, key: str) -> str | None:
        value = self.values.get(key)
        return None if value is None else str(value)

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_redis_rate_limiter_is_atomic_and_reports_remaining() -> None:
    client = _FakeRedis()
    limiter = RedisRateLimiter(
        "redis://unused",
        config=RateLimitConfig(max_requests=2, burst_multiplier=1, window_seconds=30),
        client=client,  # type: ignore[arg-type]
    )

    assert await limiter.is_allowed("tenant-a:user-a") is True
    assert await limiter.is_allowed("tenant-a:user-a") is True
    assert await limiter.is_allowed("tenant-a:user-a") is False
    assert await limiter.remaining("tenant-a:user-a") == 0
    assert list(client.ttls.values()) == [30]


@pytest.mark.asyncio
async def test_redis_rate_limiter_fails_closed_when_backend_is_unavailable() -> None:
    class _BrokenRedis:
        async def eval(self, *args: object) -> int:
            raise TimeoutError("redis unavailable")

        async def get(self, key: str) -> None:
            del key
            raise TimeoutError("redis unavailable")

        async def aclose(self) -> None:
            return None

    limiter = RedisRateLimiter(
        "redis://unused",
        client=_BrokenRedis(),  # type: ignore[arg-type]
    )

    with pytest.raises(RateLimitBackendUnavailableError):
        await limiter.is_allowed("tenant-a:user-a")

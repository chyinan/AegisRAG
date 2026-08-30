"""Redis-backed distributed rate limiter adapter."""

# pattern: Imperative Shell

from __future__ import annotations

import asyncio
import math
from typing import Protocol, cast

from redis.asyncio import Redis
from redis.exceptions import RedisError

from packages.common.rate_limit import (
    RateLimitBackendUnavailableError,
    RateLimitConfig,
    RateLimiter,
    _hash_key,
)


class RedisRateLimiter(RateLimiter):
    """Atomic fixed-window limiter for horizontally scaled API instances."""

    _INCREMENT_SCRIPT = """
local current = redis.call('INCRBY', KEYS[1], ARGV[1])
if current == tonumber(ARGV[1]) then
  redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return current
"""

    def __init__(
        self,
        redis_url: str,
        *,
        config: RateLimitConfig | None = None,
        timeout_seconds: float = 1.0,
        client: object | None = None,
    ) -> None:
        if not redis_url.strip():
            raise ValueError("redis_url must not be blank")
        if timeout_seconds <= 0 or not math.isfinite(timeout_seconds):
            raise ValueError("timeout_seconds must be finite and positive")
        self._config = config or RateLimitConfig()
        self._timeout_seconds = timeout_seconds
        self._limit = self._config.max_requests * self._config.burst_multiplier
        self._ttl_seconds = max(1, math.ceil(self._config.window_seconds))
        self._client = cast(
            _AsyncRedisRateClient,
            client
            if client is not None
            else Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=timeout_seconds,
                socket_timeout=timeout_seconds,
            ),
        )

    async def is_allowed(self, key: str, cost: int = 1) -> bool:
        if cost < 1:
            raise ValueError("cost must be positive")
        try:
            current = cast(
                int,
                await asyncio.wait_for(
                    self._client.eval(
                        self._INCREMENT_SCRIPT,
                        1,
                        self._key(key),
                        cost,
                        self._ttl_seconds,
                    ),
                    timeout=self._timeout_seconds,
                ),
            )
            return current <= self._limit
        except (RedisError, OSError, TimeoutError, ValueError) as exc:
            raise RateLimitBackendUnavailableError from exc

    async def remaining(self, key: str) -> float:
        try:
            value = cast(
                str | None,
                await asyncio.wait_for(
                    self._client.get(self._key(key)),
                    timeout=self._timeout_seconds,
                ),
            )
            current = 0 if value is None else int(value)
            return float(max(0, self._limit - current))
        except (RedisError, OSError, TimeoutError, ValueError) as exc:
            raise RateLimitBackendUnavailableError from exc

    async def aclose(self) -> None:
        await self._client.aclose()

    def _key(self, key: str) -> str:
        return f"{self._config.key_prefix}:{_hash_key(key)}"


class _AsyncRedisRateClient(Protocol):
    async def eval(
        self,
        script: str,
        numkeys: int,
        key: str,
        cost: int,
        ttl: int,
    ) -> int: ...

    async def get(self, key: str) -> str | None: ...

    async def aclose(self) -> None: ...

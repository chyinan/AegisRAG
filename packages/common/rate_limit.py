from __future__ import annotations

import asyncio
import hashlib
import math
from collections.abc import Callable
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RateLimitConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    max_requests: int = Field(default=100, ge=1)
    window_seconds: float = Field(default=60.0, gt=0.0)
    burst_multiplier: int = Field(default=2, ge=1)
    key_prefix: str = Field(default="rl")
    bucket_ttl_seconds: float = Field(default=600.0, gt=0.0)

    @field_validator("window_seconds")
    @classmethod
    def _window_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("window_seconds must be finite")
        return value


class InMemoryRateLimiter:
    """Token-bucket rate limiter with sliding window (in-memory).

    For production, swap with Redis-backed implementation.

    Buckets are evicted after ``bucket_ttl_seconds`` of inactivity to prevent
    unbounded memory growth from stale client keys (W1 fix).
    """

    def __init__(self, config: RateLimitConfig | None = None) -> None:
        self._config = config or RateLimitConfig()
        self._buckets: dict[str, _TokenBucket] = {}
        self._lock = asyncio.Lock()
        self._last_purge: float = perf_counter()
        self._purge_interval: float = max(60.0, self._config.bucket_ttl_seconds / 10.0)

    async def is_allowed(self, key: str, cost: int = 1) -> bool:
        bucket_key = f"{self._config.key_prefix}:{_hash_key(key)}"
        async with self._lock:
            now = perf_counter()
            bucket = self._buckets.get(bucket_key)
            if bucket is None or (now - bucket.last_refill) > self._config.window_seconds:
                bucket = _TokenBucket(
                    tokens=self._config.max_requests * self._config.burst_multiplier,
                    last_refill=now,
                    max_tokens=self._config.max_requests * self._config.burst_multiplier,
                    refill_rate=self._config.max_requests / self._config.window_seconds,
                )
                self._buckets[bucket_key] = bucket
            else:
                elapsed = now - bucket.last_refill
                refill = elapsed * bucket.refill_rate
                bucket.tokens = min(bucket.max_tokens, bucket.tokens + refill)
                bucket.last_refill = now

            bucket.last_access = now

            if bucket.tokens >= cost:
                bucket.tokens -= cost
                result = True
            else:
                result = False

            self._purge_stale(now)
            return result

    async def remaining(self, key: str) -> float:
        """Return approximate remaining tokens for *key*.

        Acquires the internal lock so callers see a consistent snapshot
        (W2 fix).
        """
        bucket_key = f"{self._config.key_prefix}:{_hash_key(key)}"
        async with self._lock:
            bucket = self._buckets.get(bucket_key)
            if bucket is None:
                return float(self._config.max_requests)
            return max(0.0, bucket.tokens)

    def _purge_stale(self, now: float) -> None:
        """Evict buckets that have been idle longer than *bucket_ttl_seconds*.

        Runs at most once per *purge_interval* to amortize the cost across
        many ``is_allowed`` calls (W1 fix).
        """
        if now - self._last_purge < self._purge_interval:
            return
        self._last_purge = now
        cutoff = now - self._config.bucket_ttl_seconds
        stale = [
            k for k, b in self._buckets.items() if b.last_access < cutoff
        ]
        for k in stale:
            del self._buckets[k]


class _TokenBucket:
    __slots__ = ("tokens", "last_refill", "max_tokens", "refill_rate", "last_access")

    def __init__(
        self,
        tokens: float,
        last_refill: float,
        max_tokens: float,
        refill_rate: float,
    ) -> None:
        self.tokens = tokens
        self.last_refill = last_refill
        self.max_tokens = max_tokens
        self.refill_rate = refill_rate
        self.last_access: float = last_refill


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]

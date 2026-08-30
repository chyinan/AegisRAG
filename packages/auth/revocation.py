"""Distributed token revocation ports and Redis implementation."""

# pattern: Imperative Shell

from __future__ import annotations

import asyncio
import hashlib
import math
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, cast

from redis.asyncio import Redis
from redis.exceptions import RedisError


class TokenRevocationStore(Protocol):
    async def revoke(self, jti: str, expires_at: datetime) -> None:
        """Mark a token identifier as revoked until its JWT expiry."""

    async def revoke_once(self, jti: str, expires_at: datetime) -> bool:
        """Atomically revoke a token, returning False if already revoked."""

    async def is_revoked(self, jti: str) -> bool:
        """Return whether a token identifier is currently revoked."""

    async def revoke_family(self, session_id: str, expires_at: datetime) -> None:
        """Revoke every token in a session family until the family expires."""

    async def is_family_revoked(self, session_id: str) -> bool:
        """Return whether the session family is currently revoked."""

    async def aclose(self) -> None:
        """Release underlying resources."""


class TokenRevocationUnavailableError(RuntimeError):
    """Raised when the authoritative revocation backend cannot be reached."""


class InMemoryTokenRevocationStore:
    """Deterministic test double; never use as a production default."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self._revoked: dict[str, datetime] = {}
        self._families: dict[str, datetime] = {}
        self._lock = asyncio.Lock()

    async def revoke(self, jti: str, expires_at: datetime) -> None:
        normalized = _validate_jti(jti)
        async with self._lock:
            self._purge_expired()
            if expires_at > self._clock():
                self._revoked[normalized] = expires_at

    async def revoke_once(self, jti: str, expires_at: datetime) -> bool:
        normalized = _validate_jti(jti)
        async with self._lock:
            self._purge_expired()
            if normalized in self._revoked:
                return False
            if expires_at <= self._clock():
                return False
            self._revoked[normalized] = expires_at
            return True

    async def is_revoked(self, jti: str) -> bool:
        normalized = _validate_jti(jti)
        async with self._lock:
            self._purge_expired()
            return normalized in self._revoked

    async def revoke_family(self, session_id: str, expires_at: datetime) -> None:
        normalized = _validate_jti(session_id)
        async with self._lock:
            self._purge_expired()
            if expires_at > self._clock():
                current = self._families.get(normalized)
                if current is None or expires_at > current:
                    self._families[normalized] = expires_at

    async def is_family_revoked(self, session_id: str) -> bool:
        normalized = _validate_jti(session_id)
        async with self._lock:
            self._purge_expired()
            return normalized in self._families

    async def aclose(self) -> None:
        return None

    def _purge_expired(self) -> None:
        now = self._clock()
        for jti, expires_at in tuple(self._revoked.items()):
            if expires_at <= now:
                del self._revoked[jti]
        for session_id, expires_at in tuple(self._families.items()):
            if expires_at <= now:
                del self._families[session_id]


class RedisTokenRevocationStore:
    """Redis-backed revocation store with fail-closed error semantics."""

    _SET_MAX_EXPIRY_SCRIPT = """
local current = redis.call('GET', KEYS[1])
local expiry = tonumber(ARGV[1])
if current == false or tonumber(current) < expiry then
  redis.call('SET', KEYS[1], ARGV[1], 'EXAT', expiry)
end
return 1
"""

    def __init__(
        self,
        redis_url: str,
        *,
        key_prefix: str = "aegisrag:auth:revoked",
        timeout_seconds: float = 1.0,
        client: object | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not redis_url.strip():
            raise ValueError("redis_url must not be blank")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._key_prefix = key_prefix.strip().rstrip(":")
        self._timeout_seconds = timeout_seconds
        self._clock = clock or (lambda: datetime.now(tz=UTC))
        self._client = cast(
            _AsyncRedisClient,
            client
            if client is not None
            else Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=timeout_seconds,
                socket_timeout=timeout_seconds,
            ),
        )

    async def revoke(self, jti: str, expires_at: datetime) -> None:
        normalized = _validate_jti(jti)
        ttl_seconds = max(1, math.ceil((expires_at - self._clock()).total_seconds()))
        try:
            await asyncio.wait_for(
                self._client.set(self._key(normalized), "1", ex=ttl_seconds),
                timeout=self._timeout_seconds,
            )
        except (RedisError, OSError, TimeoutError) as exc:
            raise TokenRevocationUnavailableError from exc

    async def revoke_once(self, jti: str, expires_at: datetime) -> bool:
        normalized = _validate_jti(jti)
        ttl_seconds = max(1, math.ceil((expires_at - self._clock()).total_seconds()))
        try:
            result = await asyncio.wait_for(
                self._client.set(
                    self._key(normalized),
                    "1",
                    ex=ttl_seconds,
                    nx=True,
                ),
                timeout=self._timeout_seconds,
            )
            return bool(result)
        except (RedisError, OSError, TimeoutError) as exc:
            raise TokenRevocationUnavailableError from exc

    async def is_revoked(self, jti: str) -> bool:
        normalized = _validate_jti(jti)
        try:
            result = await asyncio.wait_for(
                self._client.exists(self._key(normalized)),
                timeout=self._timeout_seconds,
            )
            return bool(result)
        except (RedisError, OSError, TimeoutError) as exc:
            raise TokenRevocationUnavailableError from exc

    async def revoke_family(self, session_id: str, expires_at: datetime) -> None:
        normalized = _validate_jti(session_id)
        expiry_epoch = max(1, math.ceil(expires_at.timestamp()))
        try:
            await asyncio.wait_for(
                self._client.eval(
                    self._SET_MAX_EXPIRY_SCRIPT,
                    1,
                    self._family_key(normalized),
                    expiry_epoch,
                ),
                timeout=self._timeout_seconds,
            )
        except (RedisError, OSError, TimeoutError) as exc:
            raise TokenRevocationUnavailableError from exc

    async def is_family_revoked(self, session_id: str) -> bool:
        normalized = _validate_jti(session_id)
        try:
            result = await asyncio.wait_for(
                self._client.exists(self._family_key(normalized)),
                timeout=self._timeout_seconds,
            )
            return bool(result)
        except (RedisError, OSError, TimeoutError) as exc:
            raise TokenRevocationUnavailableError from exc

    async def aclose(self) -> None:
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()

    def _key(self, jti: str) -> str:
        digest = hashlib.sha256(jti.encode("utf-8")).hexdigest()
        return f"{self._key_prefix}:{digest}"

    def _family_key(self, session_id: str) -> str:
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        return f"{self._key_prefix}:family:{digest}"


class _AsyncRedisClient(Protocol):
    async def set(self, key: str, value: str, *, ex: int, nx: bool | None = None) -> object: ...

    async def exists(self, key: str) -> int: ...

    async def eval(self, script: str, numkeys: int, key: str, expiry_epoch: int) -> object: ...

    async def aclose(self) -> None: ...


def _validate_jti(jti: str) -> str:
    normalized = jti.strip()
    if not normalized:
        raise ValueError("jti must not be blank")
    return normalized

"""Token revocation contract tests."""

from datetime import UTC, datetime, timedelta

import pytest

from packages.auth.context import AuthContext
from packages.auth.revocation import (
    InMemoryTokenRevocationStore,
    RedisTokenRevocationStore,
    TokenRevocationUnavailableError,
)


@pytest.mark.asyncio
async def test_in_memory_revocation_expires_tokens() -> None:
    now = datetime.now(tz=UTC)
    store = InMemoryTokenRevocationStore(clock=lambda: now)

    await store.revoke("jti-a", now + timedelta(seconds=30))
    assert await store.is_revoked("jti-a") is True
    await store.revoke_family("session-a", now + timedelta(seconds=30))
    assert await store.is_family_revoked("session-a") is True

    expired = InMemoryTokenRevocationStore(clock=lambda: now + timedelta(seconds=31))
    await expired.revoke("jti-a", now + timedelta(seconds=1))
    assert await expired.is_revoked("jti-a") is False


@pytest.mark.asyncio
async def test_redis_revocation_uses_hashed_key_and_ttl() -> None:
    class FakeRedis:
        def __init__(self) -> None:
            self.values: dict[str, str] = {}
            self.ttls: dict[str, int] = {}

        async def set(
            self,
            key: str,
            value: str,
            *,
            ex: int,
            nx: bool | None = None,
        ) -> bool:
            if nx and key in self.values:
                return False
            self.values[key] = value
            self.ttls[key] = ex
            return True

        async def exists(self, key: str) -> int:
            return int(key in self.values)

        async def eval(
            self,
            script: str,
            numkeys: int,
            key: str,
            expiry_epoch: int,
        ) -> int:
            assert script
            assert numkeys == 1
            current = int(self.values.get(key, "0"))
            if current < expiry_epoch:
                self.values[key] = str(expiry_epoch)
            return 1

        async def aclose(self) -> None:
            return None

    client = FakeRedis()
    store = RedisTokenRevocationStore(
        "redis://unused",
        client=client,  # type: ignore[arg-type]
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    expires_at = datetime(2026, 1, 1, 0, 0, 42, tzinfo=UTC)

    await store.revoke("sensitive-jti", expires_at)
    assert len(client.values) == 1
    key = next(iter(client.values))
    assert "sensitive-jti" not in key
    assert client.ttls[key] == 42
    assert await store.is_revoked("sensitive-jti") is True
    assert await store.revoke_once("sensitive-jti", expires_at) is False
    assert await store.revoke_once("another-jti", expires_at) is True
    await store.revoke_family("session-a", expires_at)
    assert await store.is_family_revoked("session-a") is True


@pytest.mark.asyncio
async def test_redis_revocation_fails_closed_when_redis_is_unavailable() -> None:
    class BrokenRedis:
        async def exists(self, key: str) -> int:
            del key
            raise TimeoutError("redis unavailable")

        async def set(
            self,
            key: str,
            value: str,
            *,
            ex: int,
            nx: bool | None = None,
        ) -> bool:
            del key, value, ex, nx
            raise TimeoutError("redis unavailable")

        async def aclose(self) -> None:
            return None

    store = RedisTokenRevocationStore(
        "redis://unused",
        client=BrokenRedis(),  # type: ignore[arg-type]
    )

    with pytest.raises(TokenRevocationUnavailableError):
        await store.is_revoked("jti-a")


def test_auth_context_does_not_serialize_or_repr_token_metadata() -> None:
    context = AuthContext(
        user_id="user-a",
        tenant_id="tenant-a",
        jti="secret-jti",
        session_id="secret-session",
    )

    assert "secret-jti" not in repr(context)
    assert "secret-session" not in repr(context)
    assert "jti" not in context.model_dump()
    assert "session_id" not in context.model_dump()

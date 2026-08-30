"""Request/task-scoped tenant context used by database RLS hooks."""

# pattern: Imperative Shell

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

_tenant_id: ContextVar[str | None] = ContextVar("aegisrag_tenant_id", default=None)


def current_tenant_id() -> str | None:
    return _tenant_id.get()


def set_tenant_id(tenant_id: str) -> Token[str | None]:
    normalized = tenant_id.strip()
    if not normalized:
        raise ValueError("tenant_id must not be blank")
    return _tenant_id.set(normalized)


def reset_tenant_id(token: Token[str | None]) -> None:
    _tenant_id.reset(token)


@contextmanager
def tenant_scope(tenant_id: str) -> Iterator[None]:
    token = set_tenant_id(tenant_id)
    try:
        yield
    finally:
        reset_tenant_id(token)

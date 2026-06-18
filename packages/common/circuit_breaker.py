from __future__ import annotations

import math
from collections.abc import Callable
from enum import Enum
from time import perf_counter
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

from packages.common.logging import get_request_logger

T = TypeVar("T")
_logger = get_request_logger()


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    failure_threshold: int = Field(default=5, ge=1)
    success_threshold: int = Field(default=2, ge=1)
    timeout_seconds: float = Field(default=30.0, gt=0.0)
    half_open_max_calls: int = Field(default=1, ge=1)

    @field_validator("timeout_seconds")
    @classmethod
    def _timeout_finite(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("timeout_seconds must be finite")
        return value


class CircuitBreaker:
    def __init__(self, *, name: str, config: CircuitBreakerConfig | None = None) -> None:
        self._name = name
        self._config = config or CircuitBreakerConfig()
        self._state: CircuitState = CircuitState.CLOSED
        self._failure_count: int = 0
        self._success_count: int = 0
        self._last_failure_time: float = 0.0
        self._opened_at: float = 0.0

    @property
    def state(self) -> CircuitState:
        return self._state

    async def call(self, fn: Callable[..., T], *args: object, **kwargs: object) -> T:
        self._transition_if_ready()
        if self._state == CircuitState.OPEN:
            raise CircuitOpenError(
                name=self._name,
                opened_at=self._opened_at,
                timeout_seconds=self._config.timeout_seconds,
            )
        try:
            result = await fn(*args, **kwargs)
        except Exception:
            self._on_failure()
            raise
        self._on_success()
        return result

    def _transition_if_ready(self) -> None:
        if self._state != CircuitState.OPEN:
            return
        elapsed = perf_counter() - self._opened_at
        if elapsed >= self._config.timeout_seconds:
            self._state = CircuitState.HALF_OPEN
            self._success_count = 0

    def _on_failure(self) -> None:
        now = perf_counter()
        self._last_failure_time = now
        self._failure_count += 1
        if self._state == CircuitState.CLOSED and self._failure_count >= self._config.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = now
            self._success_count = 0
        elif self._state == CircuitState.HALF_OPEN and self._failure_count >= 1:
            self._state = CircuitState.OPEN
            self._opened_at = now
            self._success_count = 0

    def _on_success(self) -> None:
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= self._config.success_threshold:
                self._state = CircuitState.CLOSED
                self._failure_count = 0


class CircuitOpenError(Exception):
    def __init__(self, *, name: str, opened_at: float, timeout_seconds: float) -> None:
        self.name = name
        self.opened_at = opened_at
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"Circuit '{name}' is OPEN. Retry after {timeout_seconds - (perf_counter() - opened_at):.1f}s."
        )

from __future__ import annotations

import os

from fastapi import status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from packages.common.envelope import ApiError, error_response
from packages.common.rate_limit import (
    InMemoryRateLimiter,
    RateLimitBackendUnavailableError,
    RateLimitConfig,
    RateLimiter,
)
from packages.data.adapters.redis_rate_limit import RedisRateLimiter


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP rate limiting middleware with per-path override support.

    Supports stricter limits for sensitive endpoints like login.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: RateLimiter | None = None,
        config: RateLimitConfig | None = None,
        redis_url: str | None = None,
        backend_timeout_seconds: float = 1.0,
        exempt_paths: frozenset[str] = frozenset({"/health", "/sidecar"}),
        path_limits: dict[str, RateLimitConfig] | None = None,
        path_limiters: dict[str, RateLimiter] | None = None,
    ) -> None:
        super().__init__(app)
        self._limiter = limiter or _build_limiter(
            config=config,
            redis_url=redis_url,
            timeout_seconds=backend_timeout_seconds,
        )
        self._exempt_paths = exempt_paths
        self._path_limits: dict[str, RateLimiter] = {}
        if path_limiters:
            self._path_limits.update(path_limiters)
        if path_limits:
            for path, path_config in path_limits.items():
                self._path_limits[path] = _build_limiter(
                    config=path_config,
                    redis_url=redis_url,
                    timeout_seconds=backend_timeout_seconds,
                )

    async def aclose(self) -> None:
        """Close Redis pools owned by this middleware instance."""
        limiters = (self._limiter, *self._path_limits.values())
        seen: set[int] = set()
        for limiter in limiters:
            if id(limiter) in seen:
                continue
            seen.add(id(limiter))
            await limiter.aclose()

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path
        if path in self._exempt_paths or path.startswith("/sidecar"):
            return await call_next(request)

        client_ip = self._client_ip(request)

        # Check per-path limit first (stricter), then general limit
        path_limiter = self._path_limits.get(path)
        try:
            if path_limiter is not None:
                if not await path_limiter.is_allowed(client_ip):
                    return self._rate_limited_response(request)

            if not await self._limiter.is_allowed(client_ip):
                return self._rate_limited_response(request)
        except RateLimitBackendUnavailableError:
            return self._backend_unavailable_response(request)

        response = await call_next(request)
        try:
            remaining = int(await self._limiter.remaining(client_ip))
            if path_limiter is not None:
                path_remaining = int(await path_limiter.remaining(client_ip))
                remaining = min(remaining, path_remaining)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
        except RateLimitBackendUnavailableError:
            if "X-RateLimit-Remaining" in response.headers:
                del response.headers["X-RateLimit-Remaining"]
        return response

    def _client_ip(self, request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip.strip()
        client = request.client
        if client:
            return client.host
        return "unknown"

    def _rate_limited_response(self, request: Request) -> JSONResponse:
        context = getattr(request.state, "request_context", None)
        request_id = getattr(context, "request_id", "unknown")
        envelope = error_response(
            request_id=request_id,
            error=ApiError(
                code="RATE_LIMITED",
                message="Too many requests. Please try again later.",
                details={"retry_after_seconds": 60},
            ),
        )
        response = JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=envelope.model_dump(mode="json"),
        )
        response.headers["Retry-After"] = "60"
        response.headers["X-Request-ID"] = request_id
        return response

    def _backend_unavailable_response(self, request: Request) -> JSONResponse:
        context = getattr(request.state, "request_context", None)
        request_id = getattr(context, "request_id", "unknown")
        envelope = error_response(
            request_id=request_id,
            error=ApiError(
                code="RATE_LIMIT_BACKEND_UNAVAILABLE",
                message="Rate limiting is temporarily unavailable.",
                details={"retry_after_seconds": 5},
            ),
        )
        response = JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=envelope.model_dump(mode="json"),
        )
        response.headers["Retry-After"] = "5"
        response.headers["X-Request-ID"] = request_id
        return response


def _build_limiter(
    *,
    config: RateLimitConfig | None,
    redis_url: str | None,
    timeout_seconds: float,
) -> RateLimiter:
    if redis_url is not None and redis_url.strip():
        return RedisRateLimiter(
            redis_url,
            config=config,
            timeout_seconds=timeout_seconds,
        )
    app_env = os.getenv("APP_ENV", "").strip().lower()
    if app_env in {"local", "dev", "development", "test", "testing"}:
        return InMemoryRateLimiter(config=config)
    raise RuntimeError("REDIS_URL is required for rate limiting outside local/test environments")

from __future__ import annotations

from fastapi import status
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse

from packages.common.envelope import ApiError, error_response
from packages.common.rate_limit import InMemoryRateLimiter, RateLimitConfig


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP rate limiting middleware with per-path override support.

    Supports stricter limits for sensitive endpoints like login.
    """

    def __init__(
        self,
        app,
        *,
        limiter: InMemoryRateLimiter | None = None,
        config: RateLimitConfig | None = None,
        exempt_paths: frozenset[str] = frozenset({"/health", "/sidecar"}),
        path_limits: dict[str, RateLimitConfig] | None = None,
    ) -> None:
        super().__init__(app)
        self._limiter = limiter or InMemoryRateLimiter(config=config)
        self._exempt_paths = exempt_paths
        self._path_limits: dict[str, InMemoryRateLimiter] = {}
        if path_limits:
            for path, path_config in path_limits.items():
                self._path_limits[path] = InMemoryRateLimiter(config=path_config)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint):
        path = request.url.path
        if path in self._exempt_paths or path.startswith("/sidecar"):
            return await call_next(request)

        client_ip = self._client_ip(request)

        # Check per-path limit first (stricter), then general limit
        path_limiter = self._path_limits.get(path)
        if path_limiter is not None:
            if not await path_limiter.is_allowed(client_ip):
                return self._rate_limited_response(request)

        if not await self._limiter.is_allowed(client_ip):
            return self._rate_limited_response(request)

        response = await call_next(request)
        remaining = int(await self._limiter.remaining(client_ip))
        if path_limiter is not None:
            path_remaining = int(await path_limiter.remaining(client_ip))
            remaining = min(remaining, path_remaining)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
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

from __future__ import annotations

from time import perf_counter

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from apps.api.dependencies import request_context_from_headers
from packages.auth.context import AuthContext
from packages.common.context import AuthMethod
from packages.common.logging import (
    bind_request_context,
    build_request_log_event,
    clear_log_context,
    get_request_logger,
    log_structured_event,
)

_AUTH_METHODS: frozenset[AuthMethod] = frozenset(
    {"jwt_bearer", "service_token", "dev_headers"}
)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        started_at = perf_counter()
        context = request_context_from_headers(request.headers)
        request.state.request_context = context
        bind_request_context(context)

        status_code = 500
        error_code: str | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            state_error_code = getattr(request.state, "error_code", None)
            if isinstance(state_error_code, str):
                error_code = state_error_code
            elif status_code >= 500:
                error_code = "INTERNAL_ERROR"
            response.headers["X-Request-ID"] = context.request_id
            return response
        except Exception:
            error_code = "INTERNAL_ERROR"
            raise
        finally:
            latency_ms = (perf_counter() - started_at) * 1000
            auth_context = _auth_context_from_state(request)
            event = build_request_log_event(
                context=context,
                tenant_id=auth_context.tenant_id if auth_context is not None else None,
                user_id=auth_context.user_id if auth_context is not None else None,
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                latency_ms=latency_ms,
                error_code=error_code,
                role_count=len(auth_context.roles) if auth_context is not None else None,
                permission_count=(
                    len(auth_context.permissions) if auth_context is not None else None
                ),
                auth_method=_auth_method_from_state(request),
            )
            log_structured_event(get_request_logger(), event)
            clear_log_context()


def _auth_context_from_state(request: Request) -> AuthContext | None:
    candidate = getattr(request.state, "auth_context", None)
    if isinstance(candidate, AuthContext):
        return candidate
    return None


def _auth_method_from_state(request: Request) -> AuthMethod | None:
    candidate: object = getattr(request.state, "auth_method", None)
    if candidate in _AUTH_METHODS:
        return candidate
    return None


# ---------------------------------------------------------------------------
# API Versioning — Deprecation middleware for legacy (unversioned) endpoints
# ---------------------------------------------------------------------------

_DEPRECATION_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/v1/",
    "/metrics",
    "/sidecar/",
    "/sidecar",
    "/health",
    "/ready",
)


class DeprecationMiddleware(BaseHTTPMiddleware):
    """Adds deprecation headers to requests hitting legacy (unversioned) paths.

    All API endpoints have been moved under ``/v1/``.  Requests that reach the
    same endpoints through their old, unprefixed URLs receive:

      * ``X-API-Deprecated: true``
      * ``X-API-Version: v1``

    Infrastructure endpoints (``/health``, ``/ready``, ``/metrics``,
    ``/sidecar``, and anything under ``/v1/``) are exempt.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        response = await call_next(request)
        if request.url.path.startswith(_DEPRECATION_EXEMPT_PREFIXES):
            return response
        response.headers["X-API-Deprecated"] = "true"
        response.headers["X-API-Version"] = "v1"
        return response

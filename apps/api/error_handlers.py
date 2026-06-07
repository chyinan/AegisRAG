from collections.abc import Mapping
from http import HTTPStatus

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from apps.api.dependencies import request_context_from_headers
from packages.auth.exceptions import (
    AuthContextError,
    AuthContextInvalidError,
    AuthContextRequiredError,
)
from packages.common.context import RequestContext
from packages.common.envelope import ApiError, error_response
from packages.common.errors import DomainError


def register_auth_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AuthContextRequiredError, auth_context_required_handler)
    app.add_exception_handler(AuthContextInvalidError, auth_context_invalid_handler)


def register_error_handlers(app: FastAPI) -> None:
    register_auth_error_handlers(app)
    app.add_exception_handler(DomainError, domain_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_exception_handler(Exception, unexpected_exception_handler)


async def auth_context_required_handler(request: Request, exc: Exception) -> JSONResponse:
    return _auth_error_response(
        request=request,
        exc=_as_auth_error(exc),
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


async def auth_context_invalid_handler(request: Request, exc: Exception) -> JSONResponse:
    return _auth_error_response(
        request=request,
        exc=_as_auth_error(exc),
        status_code=status.HTTP_401_UNAUTHORIZED,
    )


async def domain_error_handler(request: Request, exc: Exception) -> JSONResponse:
    domain_error = exc if isinstance(exc, DomainError) else _as_auth_error(exc)
    return _domain_error_response(
        request=request,
        exc=domain_error,
        status_code=domain_error.status_code,
    )


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    http_error = exc if isinstance(exc, StarletteHTTPException) else None
    status_code = (
        http_error.status_code if http_error is not None else status.HTTP_500_INTERNAL_SERVER_ERROR
    )
    code = _http_error_code(status_code)
    request.state.error_code = code
    return _error_json_response(
        request=request,
        status_code=status_code,
        error=ApiError(
            code=code,
            message=_http_error_message(status_code),
            details={"status_code": status_code},
        ),
        headers=http_error.headers if http_error is not None else None,
    )


async def request_validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    validation_error = exc if isinstance(exc, RequestValidationError) else None
    request.state.error_code = "REQUEST_VALIDATION_ERROR"
    return _error_json_response(
        request=request,
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        error=ApiError(
            code="REQUEST_VALIDATION_ERROR",
            message="Request validation failed.",
            details=_validation_error_details(validation_error),
        ),
    )


async def unexpected_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request.state.error_code = "INTERNAL_ERROR"
    return _error_json_response(
        request=request,
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        error=ApiError(
            code="INTERNAL_ERROR",
            message="Internal server error.",
            details={},
        ),
    )


def _auth_error_response(request: Request, exc: AuthContextError, status_code: int) -> JSONResponse:
    return _domain_error_response(request=request, exc=exc, status_code=status_code)


def _domain_error_response(request: Request, exc: DomainError, status_code: int) -> JSONResponse:
    request.state.error_code = exc.code
    return _error_json_response(
        request=request,
        status_code=status_code,
        error=ApiError(
            code=exc.code,
            message=exc.message,
            details=exc.details,
        ),
    )


def _error_json_response(
    *,
    request: Request,
    status_code: int,
    error: ApiError,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    context = _request_context(request)
    response = error_response(
        request_id=context.request_id,
        error=error,
    )
    response_headers = dict(headers or {})
    response_headers["X-Request-ID"] = context.request_id
    return JSONResponse(
        status_code=status_code,
        content=response.model_dump(mode="json"),
        headers=response_headers,
    )


def _request_context(request: Request) -> RequestContext:
    candidate = getattr(request.state, "request_context", None)
    if isinstance(candidate, RequestContext):
        return candidate
    context = request_context_from_headers(request.headers)
    request.state.request_context = context
    return context


def _as_auth_error(exc: Exception) -> AuthContextError:
    if isinstance(exc, AuthContextError):
        return exc
    return AuthContextInvalidError(details={"reason": "auth_context_error"})


def _http_error_code(status_code: int) -> str:
    return {
        status.HTTP_400_BAD_REQUEST: "BAD_REQUEST",
        status.HTTP_401_UNAUTHORIZED: "UNAUTHORIZED",
        status.HTTP_403_FORBIDDEN: "FORBIDDEN",
        status.HTTP_404_NOT_FOUND: "NOT_FOUND",
        status.HTTP_405_METHOD_NOT_ALLOWED: "METHOD_NOT_ALLOWED",
        status.HTTP_409_CONFLICT: "CONFLICT",
        status.HTTP_413_CONTENT_TOO_LARGE: "REQUEST_TOO_LARGE",
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: "UNSUPPORTED_MEDIA_TYPE",
        status.HTTP_429_TOO_MANY_REQUESTS: "RATE_LIMITED",
    }.get(status_code, "HTTP_ERROR")


def _http_error_message(status_code: int) -> str:
    try:
        phrase = HTTPStatus(status_code).phrase
    except ValueError:
        phrase = "HTTP error"
    return f"{phrase}."


def _validation_error_details(exc: RequestValidationError | None) -> dict[str, object]:
    if exc is None:
        return {"errors": []}
    errors: list[dict[str, object]] = []
    for error in exc.errors():
        errors.append(
            {
                "loc": list(error.get("loc", ())),
                "msg": str(error.get("msg", "Invalid request.")),
                "type": str(error.get("type", "value_error")),
            }
        )
    return {"errors": errors}

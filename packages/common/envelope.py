from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ApiError(BaseModel):
    code: str
    message: str
    details: dict[str, object] = Field(default_factory=dict)


class ResponseMetadata(BaseModel):
    latency_ms: float | None = None


class ApiResponse(BaseModel, Generic[T]):
    request_id: str
    data: T | None = None
    error: ApiError | None = None
    metadata: ResponseMetadata = Field(default_factory=ResponseMetadata)


def success_response(
    *,
    request_id: str,
    data: T,
    metadata: ResponseMetadata | None = None,
) -> ApiResponse[T]:
    return ApiResponse[T](
        request_id=request_id,
        data=data,
        error=None,
        metadata=metadata or ResponseMetadata(),
    )


def error_response(
    *,
    request_id: str,
    error: ApiError,
    metadata: ResponseMetadata | None = None,
) -> ApiResponse[None]:
    return ApiResponse[None](
        request_id=request_id,
        data=None,
        error=error,
        metadata=metadata or ResponseMetadata(),
    )

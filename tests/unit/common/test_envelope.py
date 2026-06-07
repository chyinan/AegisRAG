from pydantic import BaseModel

from packages.common.envelope import (
    ApiError,
    ApiResponse,
    ResponseMetadata,
    error_response,
    success_response,
)


class SampleData(BaseModel):
    value: str


def test_success_response_uses_explicit_request_id_and_data() -> None:
    data = SampleData(value="ok")

    response = success_response(request_id="req-1", data=data)

    assert isinstance(response, ApiResponse)
    assert response.request_id == "req-1"
    assert response.data == data
    assert response.error is None
    assert response.metadata.latency_ms is None
    assert response.model_dump() == {
        "request_id": "req-1",
        "data": {"value": "ok"},
        "error": None,
        "metadata": {"latency_ms": None},
    }


def test_error_response_uses_structured_error_and_no_data() -> None:
    error = ApiError(
        code="AUTH_CONTEXT_REQUIRED",
        message="Authentication context is required.",
        details={"field": "tenant_id"},
    )

    response = error_response(
        request_id="req-2",
        error=error,
        metadata=ResponseMetadata(latency_ms=12.5),
    )

    assert response.request_id == "req-2"
    assert response.data is None
    assert response.error == error
    assert response.metadata.latency_ms == 12.5
    assert response.model_dump() == {
        "request_id": "req-2",
        "data": None,
        "error": {
            "code": "AUTH_CONTEXT_REQUIRED",
            "message": "Authentication context is required.",
            "details": {"field": "tenant_id"},
        },
        "metadata": {"latency_ms": 12.5},
    }


def test_response_metadata_defaults_are_stable() -> None:
    assert ResponseMetadata().model_dump() == {"latency_ms": None}

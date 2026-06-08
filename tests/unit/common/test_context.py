import pytest
from pydantic import ValidationError

from packages.auth.context import AuthContext
from packages.common.context import AuthenticatedRequestContext, RequestContext


def test_request_context_serializes_required_trace_fields() -> None:
    context = RequestContext(
        request_id="req-123",
        trace_id="trace-123",
        session_id="session-123",
    )

    assert context.model_dump() == {
        "request_id": "req-123",
        "trace_id": "trace-123",
        "session_id": "session-123",
    }


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("request_id", ""),
        ("request_id", "  "),
        ("trace_id", ""),
        ("trace_id", "  "),
    ],
)
def test_request_context_rejects_blank_required_identifiers(
    field_name: str,
    value: str,
) -> None:
    payload = {
        "request_id": "req-123",
        "trace_id": "trace-123",
        "session_id": None,
        field_name: value,
    }

    with pytest.raises(ValidationError):
        RequestContext.model_validate(payload)


def test_authenticated_request_context_requires_auth_context() -> None:
    auth = AuthContext(user_id="user-123", tenant_id="tenant-abc")
    context = AuthenticatedRequestContext(
        request_id="req-123",
        trace_id="trace-123",
        session_id=None,
        auth=auth,
        auth_method="jwt_bearer",
    )

    assert context.auth == auth
    assert context.auth_method == "jwt_bearer"
    assert context.model_dump() == {
        "request_id": "req-123",
        "trace_id": "trace-123",
        "session_id": None,
        "auth_method": "jwt_bearer",
        "auth": {
            "user_id": "user-123",
            "tenant_id": "tenant-abc",
            "roles": (),
            "department": None,
            "permissions": (),
        },
    }


def test_authenticated_request_context_rejects_unknown_auth_method() -> None:
    with pytest.raises(ValidationError):
        AuthenticatedRequestContext.model_validate(
            {
                "request_id": "req-123",
                "trace_id": "trace-123",
                "auth_method": "bearer local-openwebui-service-token",
                "auth": {"user_id": "user-123", "tenant_id": "tenant-abc"},
            }
        )


def test_authenticated_request_context_rejects_missing_auth() -> None:
    with pytest.raises(ValidationError):
        AuthenticatedRequestContext.model_validate(
            {
                "request_id": "req-123",
                "trace_id": "trace-123",
                "session_id": None,
            }
        )

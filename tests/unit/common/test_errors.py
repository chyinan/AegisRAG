from packages.auth.exceptions import AuthContextInvalidError, AuthContextRequiredError
from packages.common.errors import DomainError


def test_domain_error_keeps_stable_public_fields() -> None:
    error = DomainError(
        code="DOCUMENT_NOT_FOUND",
        message="Document was not found.",
        details={"document_id": "doc-123"},
    )

    assert error.code == "DOCUMENT_NOT_FOUND"
    assert error.message == "Document was not found."
    assert error.details == {"document_id": "doc-123"}
    assert str(error) == "DOCUMENT_NOT_FOUND: Document was not found."


def test_domain_error_defaults_details_to_empty_dict() -> None:
    error = DomainError(code="VALIDATION_FAILED", message="Validation failed.")

    assert error.details == {}


def test_domain_error_redacts_sensitive_details_recursively() -> None:
    error = DomainError(
        code="AUTH_FAILED",
        message="Authentication failed.",
        details={
            "Authorization": "Bearer secret-token",
            "nested": {"api_key": "secret-key", "safe": "metadata"},
            "items": [{"password": "secret-password"}],
            "prompt": "full prompt text",
            "reason": "Bearer secret-token",
        },
    )

    assert error.details == {
        "Authorization": "[REDACTED]",
        "nested": {"api_key": "[REDACTED]", "safe": "metadata"},
        "items": [{"password": "[REDACTED]"}],
        "prompt": "[REDACTED]",
        "reason": "[REDACTED]",
    }


def test_auth_context_errors_remain_domain_errors_with_existing_contract() -> None:
    required = AuthContextRequiredError(details={"missing": ["auth_context"]})
    invalid = AuthContextInvalidError(details={"reason": "jwt_decode_failed"})

    assert isinstance(required, DomainError)
    assert required.code == "AUTH_CONTEXT_REQUIRED"
    assert required.message == "Authentication context is required."
    assert required.details == {"missing": ["auth_context"]}
    assert isinstance(invalid, DomainError)
    assert invalid.code == "AUTH_CONTEXT_INVALID"
    assert invalid.message == "Authentication context is invalid."
    assert invalid.details == {"reason": "jwt_decode_failed"}

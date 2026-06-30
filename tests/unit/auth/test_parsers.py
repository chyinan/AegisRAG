from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest
from jwt import encode

from packages.auth.context import AuthContext
from packages.auth.exceptions import AuthContextInvalidError, AuthContextRequiredError
from packages.auth.parsers import (
    JwtAuthSettings,
    ServiceTokenSettings,
    decode_jwt_token,
    parse_auth_fixture,
    parse_dev_auth_headers,
    parse_jwt_claims,
    parse_service_token,
)

TEST_JWT_SECRET = "test-secret-with-at-least-32-bytes"
TEST_OPENWEBUI_SERVICE_TOKEN = "local-service_token-service-token"


def _future_exp() -> datetime:
    return datetime.now(tz=UTC) + timedelta(minutes=5)


def test_dev_header_parser_normalizes_comma_separated_roles_and_permissions() -> None:
    auth = parse_dev_auth_headers(
        {
            "X-User-ID": " user-123 ",
            "X-Tenant-ID": " tenant-abc ",
            "X-Roles": " admin, knowledge_manager, ,",
            "X-Department": " HR ",
            "X-Permissions": " document:read, retrieval:query, ",
        }
    )

    assert auth == AuthContext(
        user_id="user-123",
        tenant_id="tenant-abc",
        roles=("admin", "knowledge_manager"),
        department="HR",
        permissions=("document:read", "retrieval:query"),
    )


def test_jwt_claims_parser_supports_sub_or_user_id_and_list_permissions() -> None:
    auth = parse_jwt_claims(
        {
            "sub": "user-123",
            "tenant_id": "tenant-abc",
            "roles": ["admin", "knowledge_manager"],
            "department": "HR",
            "permissions": ["document:read", "retrieval:query"],
            "type": "access",
        }
    )

    assert auth == AuthContext(
        user_id="user-123",
        tenant_id="tenant-abc",
        roles=("admin", "knowledge_manager"),
        department="HR",
        permissions=("document:read", "retrieval:query"),
    )


def test_service_token_parser_matches_sha256_hash_with_default_permissions() -> None:
    auth = parse_service_token(
        TEST_OPENWEBUI_SERVICE_TOKEN,
        ServiceTokenSettings.from_records(
            [
                {
                    "token_sha256": sha256(
                        TEST_OPENWEBUI_SERVICE_TOKEN.encode("utf-8")
                    ).hexdigest(),
                    "user_id": "service_token-service",
                    "tenant_id": "tenant-abc",
                    "roles": ["service_token"],
                    "department": "platform",
                }
            ]
        ),
    )

    assert auth == AuthContext(
        user_id="service_token-service",
        tenant_id="tenant-abc",
        roles=("service_token",),
        department="platform",
        permissions=("document:read", "retrieval:query"),
    )


def test_service_token_parser_uses_explicit_permissions_when_configured() -> None:
    auth = parse_service_token(
        TEST_OPENWEBUI_SERVICE_TOKEN,
        ServiceTokenSettings.from_records(
            [
                {
                    "token_sha256": sha256(
                        TEST_OPENWEBUI_SERVICE_TOKEN.encode("utf-8")
                    ).hexdigest(),
                    "user_id": "service_token-service",
                    "tenant_id": "tenant-abc",
                    "permissions": ["document:read", "retrieval:query", "document:manage"],
                }
            ]
        ),
    )

    assert auth.permissions == ("document:read", "retrieval:query", "document:manage")


def test_service_token_parser_fails_closed_without_config() -> None:
    with pytest.raises(AuthContextInvalidError) as exc_info:
        parse_service_token(
            TEST_OPENWEBUI_SERVICE_TOKEN,
            ServiceTokenSettings.from_records([]),
        )

    assert exc_info.value.details == {"reason": "service_token_not_configured"}
    assert TEST_OPENWEBUI_SERVICE_TOKEN not in str(exc_info.value.details)


def test_service_token_parser_rejects_unknown_token_without_leaking_it() -> None:
    with pytest.raises(AuthContextInvalidError) as exc_info:
        parse_service_token(
            "unknown-service-token",
            ServiceTokenSettings.from_records(
                [
                    {
                        "token_sha256": sha256(
                            TEST_OPENWEBUI_SERVICE_TOKEN.encode("utf-8")
                        ).hexdigest(),
                        "user_id": "service_token-service",
                        "tenant_id": "tenant-abc",
                    }
                ]
            ),
        )

    assert exc_info.value.details == {"reason": "service_token_unknown"}
    assert "unknown-service-token" not in str(exc_info.value.details)


def test_service_token_settings_rejects_duplicate_hashes() -> None:
    token_hash = sha256(TEST_OPENWEBUI_SERVICE_TOKEN.encode("utf-8")).hexdigest()

    with pytest.raises(AuthContextInvalidError) as exc_info:
        ServiceTokenSettings.from_records(
            [
                {
                    "token_sha256": token_hash,
                    "user_id": "service_token-service-a",
                    "tenant_id": "tenant-a",
                },
                {
                    "token_sha256": token_hash,
                    "user_id": "service_token-service-b",
                    "tenant_id": "tenant-b",
                },
            ]
        )

    assert exc_info.value.details == {"reason": "service_token_config_invalid"}


def test_service_token_settings_reports_malformed_records_safely() -> None:
    with pytest.raises(AuthContextInvalidError) as exc_info:
        ServiceTokenSettings.from_records(
            [
                {
                    "token_sha256": sha256(
                        TEST_OPENWEBUI_SERVICE_TOKEN.encode("utf-8")
                    ).hexdigest(),
                    "tenant_id": "tenant-abc",
                }
            ]
        )

    assert exc_info.value.details == {"reason": "service_token_config_invalid"}
    assert "user_id" not in str(exc_info.value.details)


def test_jwt_claims_parser_supports_scope_string_as_permissions() -> None:
    auth = parse_jwt_claims(
        {
            "user_id": "user-123",
            "tenant_id": "tenant-abc",
            "roles": "viewer",
            "scope": "document:read retrieval:query",
            "type": "access",
        }
    )

    assert auth.permissions == ("document:read", "retrieval:query")
    assert auth.roles == ("viewer",)


def test_jwt_claims_parser_does_not_fallback_to_scope_when_permissions_claim_is_present() -> None:
    auth = parse_jwt_claims(
        {
            "user_id": "user-123",
            "tenant_id": "tenant-abc",
            "permissions": [],
            "scope": "document:read retrieval:query",
            "type": "access",
        }
    )

    assert auth.permissions == ()


def test_jwt_claims_parser_rejects_conflicting_subject_claims() -> None:
    with pytest.raises(AuthContextInvalidError) as exc_info:
        parse_jwt_claims(
            {
                "sub": "user-from-sub",
                "user_id": "user-from-user-id",
                "tenant_id": "tenant-abc",
                "type": "access",
            }
        )

    assert exc_info.value.details == {"reason": "user_id_claim_conflict"}


def test_fixture_parser_and_jwt_claims_parser_return_same_auth_context() -> None:
    fixture = {
        "user_id": "user-123",
        "tenant_id": "tenant-abc",
        "roles": ["admin", "knowledge_manager"],
        "department": "HR",
        "permissions": ["document:read", "retrieval:query"],
    }

    claims = {**fixture, "type": "access"}
    assert parse_auth_fixture(fixture) == parse_jwt_claims(claims)


@pytest.mark.parametrize(
    "payload",
    [
        {"X-Tenant-ID": "tenant-abc"},
        {"X-User-ID": "user-123"},
        {"X-User-ID": "", "X-Tenant-ID": "tenant-abc"},
        {"X-User-ID": "user-123", "X-Tenant-ID": ""},
    ],
)
def test_dev_header_parser_raises_required_error_for_missing_required_context(
    payload: dict[str, str],
) -> None:
    with pytest.raises(AuthContextRequiredError) as exc_info:
        parse_dev_auth_headers(payload)

    assert exc_info.value.code == "AUTH_CONTEXT_REQUIRED"
    assert "missing" in exc_info.value.details


def test_jwt_claims_parser_raises_required_error_for_missing_required_claims() -> None:
    with pytest.raises(AuthContextRequiredError):
        parse_jwt_claims({"sub": "user-123", "type": "access"})


def test_decode_jwt_token_rejects_unconfigured_secret() -> None:
    token = encode(
        {"sub": "user-123", "tenant_id": "tenant-abc", "type": "access", "exp": _future_exp()},
        TEST_JWT_SECRET,
        "HS256",
    )

    with pytest.raises(AuthContextInvalidError):
        decode_jwt_token(token, JwtAuthSettings(secret=None))


def test_decode_jwt_token_requires_expiration() -> None:
    token = encode(
        {"sub": "user-123", "tenant_id": "tenant-abc", "type": "access"},
        TEST_JWT_SECRET,
        "HS256",
    )

    with pytest.raises(AuthContextInvalidError) as exc_info:
        decode_jwt_token(token, JwtAuthSettings(secret=TEST_JWT_SECRET))

    assert exc_info.value.details == {"reason": "jwt_decode_failed"}


def test_decode_jwt_token_verifies_token_and_returns_auth_context() -> None:
    token = encode(
        {
            "sub": "user-123",
            "tenant_id": "tenant-abc",
            "roles": ["admin"],
            "permissions": ["document:read"],
            "type": "access",
            "iss": "local-test",
            "aud": "local-api",
            "exp": _future_exp(),
        },
        TEST_JWT_SECRET,
        "HS256",
    )

    auth = decode_jwt_token(
        token,
        JwtAuthSettings(
            secret=TEST_JWT_SECRET,
            algorithm="HS256",
            issuer="local-test",
            audience="local-api",
        ),
    )

    assert auth == AuthContext(
        user_id="user-123",
        tenant_id="tenant-abc",
        roles=("admin",),
        department=None,
        permissions=("document:read",),
    )

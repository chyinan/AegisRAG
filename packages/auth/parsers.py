import os
from collections.abc import Mapping
from dataclasses import dataclass

import jwt
from jwt import InvalidTokenError
from jwt.types import Options
from pydantic import ValidationError

from packages.auth.context import AuthContext
from packages.auth.exceptions import AuthContextInvalidError, AuthContextRequiredError

DEV_USER_HEADER = "X-User-ID"
DEV_TENANT_HEADER = "X-Tenant-ID"
DEV_ROLES_HEADER = "X-Roles"
DEV_DEPARTMENT_HEADER = "X-Department"
DEV_PERMISSIONS_HEADER = "X-Permissions"


@dataclass(frozen=True)
class JwtAuthSettings:
    secret: str | None
    algorithm: str = "HS256"
    issuer: str | None = None
    audience: str | None = None

    @classmethod
    def from_environment(cls) -> "JwtAuthSettings":
        return cls(
            secret=os.getenv("JWT_SECRET"),
            algorithm=os.getenv("JWT_ALGORITHM", "HS256"),
            issuer=os.getenv("JWT_ISSUER"),
            audience=os.getenv("JWT_AUDIENCE"),
        )


def parse_dev_auth_headers(headers: Mapping[str, str | None]) -> AuthContext:
    normalized = {key.lower(): value for key, value in headers.items()}
    user_id = _required_value(normalized.get(DEV_USER_HEADER.lower()), "user_id")
    tenant_id = _required_value(normalized.get(DEV_TENANT_HEADER.lower()), "tenant_id")
    return _build_auth_context(
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "roles": _split_csv(normalized.get(DEV_ROLES_HEADER.lower())),
            "department": normalized.get(DEV_DEPARTMENT_HEADER.lower()),
            "permissions": _split_csv(normalized.get(DEV_PERMISSIONS_HEADER.lower())),
        }
    )


def parse_jwt_claims(claims: Mapping[str, object]) -> AuthContext:
    user_id = _user_id_claim(claims)
    tenant_id = _required_value(claims.get("tenant_id"), "tenant_id")
    if "permissions" in claims:
        permissions = _normalize_claim_sequence(claims.get("permissions"))
    else:
        permissions = _normalize_scope(claims.get("scope"))

    return _build_auth_context(
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "roles": _normalize_claim_sequence(claims.get("roles")),
            "department": claims.get("department"),
            "permissions": permissions,
        }
    )


def parse_auth_fixture(payload: Mapping[str, object]) -> AuthContext:
    user_id = _required_value(payload.get("user_id"), "user_id")
    tenant_id = _required_value(payload.get("tenant_id"), "tenant_id")
    return _build_auth_context(
        {
            "user_id": user_id,
            "tenant_id": tenant_id,
            "roles": _normalize_claim_sequence(payload.get("roles")),
            "department": payload.get("department"),
            "permissions": _normalize_claim_sequence(payload.get("permissions")),
        }
    )


def decode_jwt_token(token: str, settings: JwtAuthSettings) -> AuthContext:
    if not settings.secret:
        raise AuthContextInvalidError(details={"reason": "jwt_secret_not_configured"})

    options: Options = {"require": ["exp"]}
    if not settings.audience:
        options["verify_aud"] = False

    try:
        claims = jwt.decode(
            token,
            settings.secret,
            algorithms=[settings.algorithm],
            issuer=settings.issuer,
            audience=settings.audience,
            options=options,
        )
    except InvalidTokenError as exc:
        raise AuthContextInvalidError(details={"reason": "jwt_decode_failed"}) from exc

    if not isinstance(claims, dict):
        raise AuthContextInvalidError(details={"reason": "jwt_claims_invalid"})
    return parse_jwt_claims(claims)


def _build_auth_context(payload: Mapping[str, object]) -> AuthContext:
    try:
        return AuthContext.model_validate(dict(payload))
    except ValidationError as exc:
        required_fields = {
            str(error["loc"][0])
            for error in exc.errors()
            if error["loc"] and error["loc"][0] in {"user_id", "tenant_id"}
        }
        if required_fields:
            raise AuthContextRequiredError(details={"missing": sorted(required_fields)}) from exc
        raise AuthContextInvalidError(details={"reason": "auth_context_validation_failed"}) from exc


def _required_value(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AuthContextRequiredError(details={"missing": [field_name]})
    return value.strip()


def _user_id_claim(claims: Mapping[str, object]) -> str:
    subject = _optional_string_claim(claims.get("sub"))
    user_id = _optional_string_claim(claims.get("user_id"))
    if subject and user_id and subject != user_id:
        raise AuthContextInvalidError(details={"reason": "user_id_claim_conflict"})
    return _required_value(subject or user_id, "user_id")


def _optional_string_claim(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _split_csv(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


def _normalize_claim_sequence(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return _split_csv(value)
    if isinstance(value, list | tuple | set):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _normalize_scope(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        return ()
    return tuple(item.strip() for item in value.split() if item.strip())

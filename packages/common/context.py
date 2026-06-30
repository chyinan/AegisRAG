from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from packages.auth.context import AuthContext

AuthMethod = Literal["jwt_bearer", "service_token", "dev_headers"]


class RequestContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    request_id: str
    trace_id: str
    session_id: str | None = None

    @field_validator("request_id", "trace_id")
    @classmethod
    def _required_identifier_must_not_be_blank(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("identifier must not be blank")
        return normalized

    @field_validator("session_id")
    @classmethod
    def _normalize_session_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class AuthenticatedRequestContext(RequestContext):
    auth_method: AuthMethod | None = None
    auth: AuthContext

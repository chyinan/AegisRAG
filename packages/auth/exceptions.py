from collections.abc import Mapping

from packages.common.errors import DomainError


class AuthContextError(DomainError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(code=code, message=message, details=details)


class AuthContextRequiredError(AuthContextError):
    def __init__(self, details: dict[str, object] | None = None) -> None:
        super().__init__(
            code="AUTH_CONTEXT_REQUIRED",
            message="Authentication context is required.",
            details=details,
        )


class AuthContextInvalidError(AuthContextError):
    def __init__(self, details: dict[str, object] | None = None) -> None:
        super().__init__(
            code="AUTH_CONTEXT_INVALID",
            message="Authentication context is invalid.",
            details=details,
        )

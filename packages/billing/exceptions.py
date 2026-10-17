from packages.common.errors import DomainError


class BillingError(DomainError):
    def __init__(self, code: str, message: str, *, status_code: int = 503) -> None:
        super().__init__(code=code, message=message, status_code=status_code)

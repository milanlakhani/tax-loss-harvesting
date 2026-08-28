from __future__ import annotations

from app.domain.enums import ParseErrorCode


class DomainError(Exception):
    def __init__(self, message: str, code: str = "DOMAIN_ERROR") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class IdempotencyConflictError(DomainError):
    def __init__(self, message: str = "Idempotency key already used with different parameters") -> None:
        super().__init__(message, code="IDEMPOTENCY_CONFLICT")


class ActiveAnalysisExistsError(DomainError):
    def __init__(self, message: str = "An active analysis already exists for this portfolio and period") -> None:
        super().__init__(message, code="ACTIVE_ANALYSIS_EXISTS")


class ParseError(DomainError):
    def __init__(self, message: str, code: ParseErrorCode) -> None:
        super().__init__(message, code=code.value)
        self.parse_code = code


class ProviderError(DomainError):
    def __init__(self, message: str, provider: str) -> None:
        super().__init__(message, code="PROVIDER_ERROR")
        self.provider = provider


class InvalidStateTransitionError(DomainError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="INVALID_STATE_TRANSITION")


class PaperExecutionError(DomainError):
    def __init__(self, message: str, code: str = "PAPER_EXECUTION_ERROR") -> None:
        super().__init__(message, code=code)


class SessionAccessError(DomainError):
    def __init__(self, message: str = "Session not found") -> None:
        super().__init__(message, code="SESSION_NOT_FOUND")

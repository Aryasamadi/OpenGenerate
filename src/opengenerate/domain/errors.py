"""Explicit domain and operational error taxonomy.

`DomainError` subclasses describe business-rule violations (never retried).
`OperationError` subclasses classify external/infrastructure failures so that callers can decide
between retry, give up, or treat the outcome as *unknown* (e.g. a send whose delivery is ambiguous).
"""

from __future__ import annotations


class DomainError(Exception):
    code = "domain_error"


class ValidationFailed(DomainError):
    code = "validation_failed"


class AccessDenied(DomainError):
    """Raised for both 'forbidden' and 'not yours' so object existence is never leaked."""

    code = "access_denied"


class NotFound(DomainError):
    code = "not_found"


class Conflict(DomainError):
    code = "conflict"


class QuotaExceeded(DomainError):
    code = "quota_exceeded"


class InsufficientFunds(DomainError):
    code = "insufficient_funds"


class CurrencyNotSpendable(DomainError):
    code = "currency_not_spendable"


class IdempotencyConflict(DomainError):
    """The same idempotency key was reused with different parameters."""

    code = "idempotency_conflict"


class OperationError(Exception):
    """Base for classified external failures."""


class TransientError(OperationError):
    """Definitely did not take effect (or is safe to repeat); retry with backoff."""

    def __init__(self, message: str = "", *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class PermanentError(OperationError):
    """Will never succeed if repeated unchanged; do not retry."""


class UnknownOutcomeError(OperationError):
    """The request may or may not have taken effect. Never blindly repeat non-idempotent work."""

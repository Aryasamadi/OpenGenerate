"""Money primitives: integer minor units only, explicit rounding, closed currency set."""

from __future__ import annotations

from enum import StrEnum

from opengenerate.domain.errors import ValidationFailed

BPS_DENOMINATOR = 10_000


class Currency(StrEnum):
    TMN = "TMN"  # toman, exponent 0
    USD = "USD"  # cents, exponent 2
    LEGACY = "LEGACY"  # frozen balance of unknown currency; never spendable

    @property
    def spendable(self) -> bool:
        return self is not Currency.LEGACY

    @property
    def exponent(self) -> int:
        return 2 if self is Currency.USD else 0


class LedgerReason(StrEnum):
    PAYMENT_DEBIT = "payment_debit"
    PAYMENT_SURPLUS = "payment_surplus"
    REFERRAL_REWARD = "referral_reward"
    REFUND_CREDIT = "refund_credit"
    REFUND_CLAWBACK = "refund_clawback"
    ADMIN_ADJUSTMENT = "admin_adjustment"
    MIGRATION = "migration"
    LEGACY_RECONCILE = "legacy_reconcile"


LEGACY_ONLY_REASONS = frozenset({LedgerReason.MIGRATION, LedgerReason.LEGACY_RECONCILE})


def mul_bps(amount: int, bps: int) -> int:
    """Return round-half-up(amount * bps / 10_000) for non-negative integers."""
    if amount < 0:
        raise ValidationFailed("amount must be non-negative")
    if not 0 <= bps <= BPS_DENOMINATOR:
        raise ValidationFailed("bps must be within 0..10000")
    return (amount * bps + BPS_DENOMINATOR // 2) // BPS_DENOMINATOR


def apply_percent_discount(amount: int, percent: int) -> tuple[int, int]:
    """Return (discount, payable) for an integer percentage 0..100."""
    if not 0 <= percent <= 100:
        raise ValidationFailed("percent must be within 0..100")
    discount = mul_bps(amount, percent * 100)
    return discount, amount - discount

"""Pure referral attribution, reward calculation, currency-specific cap, and clawback rules."""

from __future__ import annotations

from dataclasses import dataclass

from opengenerate.domain.errors import ValidationFailed
from opengenerate.domain.money import Currency, mul_bps

DEFAULT_REFERRAL_PERCENT = 20
MAX_REFERRAL_PERCENT = 50


@dataclass(frozen=True, slots=True)
class ReferralEconomics:
    percent: int = DEFAULT_REFERRAL_PERCENT
    cap_tmn: int = 0  # 0 means unlimited
    cap_usd: int = 0  # 0 means unlimited (in cents)

    def __post_init__(self) -> None:
        if not 0 <= self.percent <= MAX_REFERRAL_PERCENT:
            raise ValidationFailed(f"referral percent must be within 0..{MAX_REFERRAL_PERCENT}")
        if self.cap_tmn < 0 or self.cap_usd < 0:
            raise ValidationFailed("referral caps must be non-negative (0 = unlimited)")

    def cap_for(self, currency: Currency) -> int:
        if currency is Currency.TMN:
            return self.cap_tmn
        if currency is Currency.USD:
            return self.cap_usd
        return 0


def compute_referral_reward(
    *,
    eligible_paid_amount: int,
    currency: Currency,
    economics: ReferralEconomics,
    already_earned_in_currency: int,
) -> int:
    """Compute the referral reward in `currency`, respecting the per-currency cap."""
    if eligible_paid_amount <= 0 or economics.percent <= 0 or not currency.spendable:
        return 0
    raw = mul_bps(eligible_paid_amount, economics.percent * 100)
    if raw <= 0:
        return 0
    cap = economics.cap_for(currency)
    if cap <= 0:
        return raw
    remaining = max(0, cap - max(0, already_earned_in_currency))
    return min(raw, remaining)


def compute_referral_clawback(reward_granted: int, referrer_wallet_balance: int) -> tuple[int, int]:
    """Return `(clawback_amount, shortfall)` ensuring referrer wallet never goes negative."""
    if reward_granted <= 0:
        return 0, 0
    available = max(0, referrer_wallet_balance)
    clawback = min(reward_granted, available)
    return clawback, reward_granted - clawback
